import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-quarantine-clear-'));
const outfile = resolve(directory, 'ui.cjs');
after(() => rm(directory, { recursive: true, force: true }));

// The real components, the real read cycle and the real transitions, bundled;
// only the bridge is a stand-in, so nothing here reaches Tauri or a window.
await build({
  stdin: {
    resolveDir: root,
    loader: 'tsx',
    contents: `
      import React from 'react';
      import { renderToStaticMarkup } from 'react-dom/server';
      import { QuarantineDialog } from './src/components/BootQuarantineClear';
      import * as flow from './src/services/rowynQuarantineModel';
      import { createBootHealthPanel } from './src/services/rowynBootHealthPanel';
      import { parseBootHealth } from './src/services/rowynBootHealthModel';
      export { flow, createBootHealthPanel, parseBootHealth };
      export function dialog(props) {
        return renderToStaticMarkup(<QuarantineDialog {...props} />);
      }
    `,
  },
  outfile,
  bundle: true,
  platform: 'node',
  format: 'cjs',
  jsx: 'automatic',
  logLevel: 'silent',
});
const loaded = await import(pathToFileURL(outfile).href);
const ui = loaded.default ?? loaded;
const flow = ui.flow;

const DIGEST = 'sha256:' + '1'.repeat(64);
const REASON = 'Older than the booted image';
const CLEARED = `Quarantine cleared for ${DIGEST}.`;

// The same record the image writes, through the real parser: one quarantined
// deployment, then the same record after the clear.
const QUARANTINED = JSON.stringify({
  schema_version: 1,
  known_good: { [DIGEST]: {} },
  quarantined: { [DIGEST]: { reason: 'downgrade', quarantined_at: '2026-11-01T10:00:00Z' } },
});
const STAGED_AGAIN = JSON.stringify({ schema_version: 1, known_good: { [DIGEST]: {} } });

/**
 * A panel whose bridge answers with the given records in order and counts them.
 * `gate(index)` may return a promise that must settle before that read answers,
 * so a test can hold one read in flight.
 */
function panelGated(records, { failing = false, gate = () => null } = {}) {
  const calls = { reads: 0 };
  const views = [];
  const panel = ui.createBootHealthPanel(async () => {
    const index = calls.reads++;
    if (failing) throw new Error('The boot-health record could not be read.');
    const held = gate(index);
    if (held) await held;
    return ui.parseBootHealth(records[Math.min(index, records.length - 1)]);
  }, (view) => views.push(view));
  return { panel, views, reads: () => calls.reads };
}

function panelAnswering(records, failing = false) {
  return panelGated(records, { failing });
}

/** Polls a settlement the panel reaches on its own schedule, never a clock. */
async function until(predicate, label) {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    if (predicate()) return;
    await new Promise((settle) => setTimeout(settle, 1));
  }
  assert.fail(`timed out waiting for ${label}`);
}

test('the dialog names the digest, the reason, and what clearing does', () => {
  const html = ui.dialog({
    digest: DIGEST,
    reasonLabel: REASON,
    busy: false,
    onConfirm() {},
    onCancel() {},
  });
  assert.match(html, /role="alertdialog"/);
  assert.match(html, /aria-modal="false"/);
  assert.ok(html.includes(DIGEST), 'the digest must be shown before confirming');
  assert.ok(html.includes(REASON), 'the reason must be shown before confirming');
  assert.ok(html.includes('Clear the quarantine for this deployment?'));
  assert.ok(html.includes('Nothing is downloaded, installed, or restarted'));
  assert.ok(html.includes('>Clear quarantine<'));
  assert.ok(html.includes('>Cancel<'));
});

test('a busy dialog shows progress and cannot be confirmed twice', () => {
  const html = ui.dialog({ digest: DIGEST, busy: true, onConfirm() {}, onCancel() {} });
  assert.ok(html.includes('Clearing…'));
  assert.doesNotMatch(html, />Clear quarantine</);
  assert.match(html, /<button[^>]*disabled[^>]*>Cancel</);
});

test('opening shows the dialog and leaves the bridge alone', () => {
  const opened = flow.openQuarantineDialog(flow.QUARANTINE_DIALOG_CLOSED);
  assert.deepEqual(opened, { open: true, busy: false, message: '', cleared: false });
  // A pure state transition: it takes nothing but the state, so no bridge can
  // be reached from this path.
  assert.equal(flow.openQuarantineDialog.length, 1);
});

test('cancel closes the dialog without calling the bridge', () => {
  const opened = flow.openQuarantineDialog(flow.QUARANTINE_DIALOG_CLOSED);
  const cancelled = flow.cancelQuarantineDialog(opened);
  // A plain return value: this path has no bridge to call.
  assert.deepEqual(cancelled, { open: false, busy: false, message: '', cleared: false });
  assert.equal(flow.cancelQuarantineDialog.length, 1, 'cancel takes nothing but the state');
});

test('cancel is a no-op while a clear is running', () => {
  const running = flow.beginQuarantineClear(
    flow.openQuarantineDialog(flow.QUARANTINE_DIALOG_CLOSED));
  assert.equal(flow.cancelQuarantineDialog(running), running);
});

test('confirming sends the digest once, marked confirmed, and asks for a refresh', async () => {
  const calls = [];
  const clear = async (digest, confirmed) => {
    calls.push([digest, confirmed]);
    return CLEARED;
  };

  const running = flow.beginQuarantineClear(
    flow.openQuarantineDialog(flow.QUARANTINE_DIALOG_CLOSED));
  assert.equal(running.busy, true);

  const refreshed = [];
  const done = await flow.finishQuarantineClear(DIGEST, clear, () => { refreshed.push(true); });
  assert.deepEqual(calls, [[DIGEST, true]]);
  assert.deepEqual(done, { open: false, busy: false, message: CLEARED, cleared: true });
  assert.deepEqual(refreshed, [true], 'a successful clear asks for exactly one refresh');
});

test('a refusal keeps the dialog open with the bridge message and no refresh', async () => {
  const clear = async () => {
    throw new Error(`${DIGEST} is not quarantined now, so nothing was changed.`);
  };
  const refreshed = [];
  const refused = await flow.finishQuarantineClear(DIGEST, clear, () => { refreshed.push(true); });
  assert.equal(refused.open, true);
  assert.equal(refused.cleared, false);
  assert.equal(refused.busy, false);
  assert.match(refused.message, /is not quarantined now/);
  assert.deepEqual(refreshed, [], 'a refusal must not ask for a refresh');
});

test('the panel re-reads the record after a successful clear', async () => {
  const { panel, views, reads } = panelAnswering([QUARANTINED, STAGED_AGAIN]);
  await panel.read();
  assert.equal(reads(), 1);
  assert.equal(panel.view().snapshot.quarantined.length, 1);
  assert.match(panel.view().snapshot.label, /1 deployment\(s\) quarantined/);

  const outcome = await flow.finishQuarantineClear(DIGEST, async () => CLEARED, panel.onCleared);
  assert.equal(outcome.cleared, true);
  assert.equal(reads(), 2, 'the clear must make the panel read the record again');
  await until(() => !panel.view().busy, 'the re-read to land');
  assert.equal(panel.view().busy, false);
  assert.equal(panel.view().error, '');
  assert.equal(panel.view().snapshot.quarantined.length, 0);
  assert.match(panel.view().snapshot.label, /1 verified deployment\(s\)/);
  assert.equal(views.at(-1).snapshot.quarantined.length, 0,
    'the view published for the render is the re-read one');
});

test('a slow re-read cannot hold the dialog open', async () => {
  let release;
  const held = new Promise((settle) => { release = settle; });
  // The first read answers at once; the re-read after the clear is held open.
  const { panel, reads } = panelGated([QUARANTINED, STAGED_AGAIN], {
    gate: (index) => (index === 1 ? held : null),
  });
  await panel.read();
  assert.equal(panel.view().snapshot.quarantined.length, 1);

  // The bridge answers immediately; nothing releases the re-read it starts.
  const outcome = await flow.finishQuarantineClear(DIGEST, async () => CLEARED, panel.onCleared);
  assert.deepEqual(outcome, { open: false, busy: false, message: CLEARED, cleared: true },
    'the dialog closes on the bridge answer, not on the re-read');
  assert.equal(reads(), 2, 'the re-read still starts');
  assert.equal(panel.view().busy, true, 'the panel is still reading after the dialog is gone');
  assert.equal(panel.view().snapshot.quarantined.length, 1,
    'the previous record stays until the read lands');

  release();
  await until(() => !panel.view().busy, 'the held re-read to land');
  assert.equal(panel.view().error, '');
  assert.equal(panel.view().snapshot.quarantined.length, 0,
    'the record published whenever the read lands is the re-read one');
});

test('a re-read that fails does not become the dialog\'s failure', async () => {
  const failing = async () => { throw new Error('the record could not be read'); };
  const threw = () => { throw new Error('the record could not be read'); };
  for (const onCleared of [failing, threw]) {
    const done = await flow.finishQuarantineClear(DIGEST, async () => CLEARED, onCleared);
    assert.deepEqual(done, { open: false, busy: false, message: CLEARED, cleared: true });
  }
});

test('a refusal leaves the panel on the record it last read', async () => {
  const { panel, reads } = panelAnswering([QUARANTINED, STAGED_AGAIN]);
  await panel.read();
  const before = panel.view().snapshot;

  const outcome = await flow.finishQuarantineClear(
    DIGEST,
    async () => { throw new Error(`${DIGEST} is not quarantined now, so nothing was changed.`); },
    panel.onCleared);
  assert.equal(outcome.cleared, false);
  assert.equal(reads(), 1, 'a refusal must not re-read the record');
  assert.equal(panel.view().snapshot, before, 'the shown record is untouched');
  assert.equal(panel.view().snapshot.quarantined.length, 1);
});

test('a re-read that fails is reported as unavailable, not as a cleared record', async () => {
  const { panel } = panelAnswering([QUARANTINED], true);
  await panel.read();
  assert.equal(panel.view().snapshot, null);
  assert.match(panel.view().error, /could not be read/);
  assert.equal(panel.view().busy, false);
});

test('a disposed panel publishes nothing, so unmount cannot refresh a gone page', async () => {
  const { panel, views } = panelAnswering([QUARANTINED, STAGED_AGAIN]);
  await panel.read();
  const published = views.length;
  panel.dispose();
  await panel.onCleared();
  assert.equal(views.length, published);
  assert.equal(panel.view().snapshot.quarantined.length, 1);
});

test('the component clears through the bridge and refreshes only on success', async () => {
  const control = await readFile(resolve(root, 'src/components/BootQuarantineClear.tsx'), 'utf8');
  assert.match(control, /finishQuarantineClear\(digest, clearBootQuarantine, onCleared\)/);
  assert.doesNotMatch(control, /if \(next\.cleared\)/);
  assert.match(control, /setState\(beginQuarantineClear\)/);
  assert.match(control, /onCancel=\{\(\) => setState\(cancelQuarantineDialog\)\}/);
  assert.match(control, /onClick=\{\(\) => setState\(openQuarantineDialog\)\}/);

  // The panel renders from the read cycle tested above and hands that cycle's
  // re-read to the clear control, so the covered code is the code that runs.
  const panel = await readFile(resolve(root, 'src/components/BootHealthPanel.tsx'), 'utf8');
  assert.match(panel, /createBootHealthPanel\(rowynBootHealthSnapshot, setView\)/);
  assert.match(panel, /onCleared=\{panel\.onCleared\}/);
  assert.doesNotMatch(panel, /invoke\(/);
});
