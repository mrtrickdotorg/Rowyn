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

// The real component and its real transitions, bundled; only the bridge is a
// stand-in, so nothing here reaches Tauri or a window.
await build({
  stdin: {
    resolveDir: root,
    loader: 'tsx',
    contents: `
      import React from 'react';
      import { renderToStaticMarkup } from 'react-dom/server';
      import { QuarantineDialog } from './src/components/BootQuarantineClear';
      import * as flow from './src/services/rowynQuarantineModel';
      export { flow };
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

  const done = await flow.finishQuarantineClear(DIGEST, clear);
  assert.deepEqual(calls, [[DIGEST, true]]);
  assert.deepEqual(done, { open: false, busy: false, message: CLEARED, cleared: true });
});

test('a refusal keeps the dialog open with the bridge message and no refresh', async () => {
  const clear = async () => {
    throw new Error(`${DIGEST} is not quarantined now, so nothing was changed.`);
  };
  const refused = await flow.finishQuarantineClear(DIGEST, clear);
  assert.equal(refused.open, true);
  assert.equal(refused.cleared, false);
  assert.equal(refused.busy, false);
  assert.match(refused.message, /is not quarantined now/);
});

test('the component clears through the bridge and refreshes only on success', async () => {
  const source = await readFile(resolve(root, 'src/components/BootQuarantineClear.tsx'), 'utf8');
  assert.match(source, /finishQuarantineClear\(digest, clearBootQuarantine\)/);
  assert.match(source, /if \(next\.cleared\) onCleared\(\);/);
  assert.match(source, /setState\(beginQuarantineClear\)/);
  assert.match(source, /onCancel=\{\(\) => setState\(cancelQuarantineDialog\)\}/);
  assert.match(source, /onClick=\{\(\) => setState\(openQuarantineDialog\)\}/);
});
