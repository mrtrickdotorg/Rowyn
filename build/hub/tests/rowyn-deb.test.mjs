import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-deb-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynDebModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

const report = (overrides = {}) => ({
  package: 'discord',
  version: '0.0.124',
  architecture: 'amd64',
  maintainer: 'Discord Inc. <linux@discord.com>',
  description: 'All-in-one voice and text chat',
  installed_size_kib: 189345,
  file_size_bytes: 95000000,
  sha256: '00',
  signature: 'none',
  verdict: 'user-app',
  reasons: [],
  notes: [],
  desktop_files: ['/usr/share/applications/discord.desktop'],
  binaries: [],
  flatpak_hint: { label: 'Discord', id: 'com.discordapp.Discord' },
  ...overrides,
});

test('operations are a fixed set and removals carry the package name', () => {
  for (const op of ['create', 'install', 'delete', 'remove:google-chrome-stable']) {
    assert.equal(m.isDebOperation(op), true, op);
  }
  for (const op of ['', 'remove:', 'remove:Bad Name', 'remove:-rf', 'shell', 'install:x']) {
    assert.equal(m.isDebOperation(op), false, op);
  }
});

test('confirmations are exact and create needs none', () => {
  assert.equal(m.installConfirmation(report()), 'install:discord');
  assert.equal(m.deleteConfirmation(), 'rowyn-deb');
  const app = { package: 'code', version: '1.0', source: 'code.deb', exported_apps: ['code'], exported_bins: [], installed_at: 1 };
  assert.equal(m.removalOperation(app), 'remove:code');
  assert.equal(m.removalConfirmation(app), 'code');
  assert.equal(m.needsConfirmation('create'), false);
  assert.equal(m.needsConfirmation('install'), true);
  assert.equal(m.needsConfirmation('delete'), true);
  assert.equal(m.needsConfirmation('remove:code'), true);
});

test('state copy is honest about the one-time download and the container boundary', () => {
  const absent = m.describeState({ state: 'absent', name: 'rowyn-deb', image: '', status: 'absent', detail: '' });
  assert.match(absent, /one-time/);
  assert.match(absent, /downloads an Ubuntu environment/);
  const managed = m.describeState({ state: 'managed', name: 'rowyn-deb', image: '', status: 'running', detail: '' });
  assert.match(managed, /not in the system/);
  assert.match(managed, /across Rowyn updates/);
  const collision = m.describeState({ state: 'collision', name: 'rowyn-deb', image: '', status: 'unknown', detail: '' });
  assert.match(collision, /No changes were made/);
});

test('signature copy states unsigned files plainly', () => {
  assert.match(m.signatureText(report()), /NOT signed/);
  assert.match(m.signatureText(report()), /vendor's own website/);
  assert.match(m.signatureText(report({ signature: 'embedded' })), /embedded signature/);
  // rowyn-deb-inspect's vocabulary is exactly embedded|none; anything
  // else must fall through to the plain unsigned wording, never claim
  // a signature the inspector did not verify.
  assert.match(m.signatureText(report({ signature: 'anything-else' })), /NOT signed/);
});

test('job controller polls to a terminal state and reports it once', async () => {
  const seen = [];
  let polls = 0;
  const ctl = m.createJobController({
    start: async () => ({ id: 'j1', status: 'running', detail: 'started' }),
    job: async () => { polls++; return { id: 'j1', status: 'succeeded', detail: 'Installed discord 0.0.124' }; },
    cancel: async () => ({ id: 'j1', status: 'cancelled', detail: 'Cancelled' }),
  }, {
    onJob: (job) => seen.push(job),
    onError: (error) => { throw error; },
  }, 1);
  await ctl.start('install', 'install:discord', '/home/u/Downloads/discord.deb');
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 20));
  assert.equal(polls, 1);
  assert.equal(seen.at(-1).status, 'succeeded');
  assert.equal(seen.at(-1).detail, 'Installed discord 0.0.124');
  ctl.dispose();
});

test('job controller refuses overlapping starts and cancel reaches the backend', async () => {
  let starts = 0, cancels = 0;
  const ctl = m.createJobController({
    start: async () => { starts++; return { id: 'j2', status: 'running', detail: 'started' }; },
    job: async () => ({ id: 'j2', status: 'running', detail: 'working' }),
    cancel: async () => { cancels++; return { id: 'j2', status: 'running', detail: 'Cancellation requested.' }; },
  }, { onJob: () => {}, onError: () => {} }, 5);
  await ctl.start('create');
  await ctl.start('create');
  assert.equal(starts, 1);
  await ctl.cancel();
  assert.equal(cancels, 1);
  ctl.dispose();
});

test('card copy is Flatpak-first, names the container, and never uses window.confirm', async () => {
  const card = await readFile(resolve(root, 'src/components/RowynDebCard.tsx'), 'utf8');
  assert.match(card, /Flatpak\) come first/);
  assert.match(card, /rowyn-deb/);
  assert.match(card, /app container/);
  assert.match(card, /refused, with the reason/);
  assert.match(card, /role="alertdialog"/);
  assert.match(card, /App container \(\.deb\)/);
  assert.doesNotMatch(card, /window\.confirm|confirmUserAction/);
});

test('service invokes only the five registered rowyn_deb commands', async () => {
  const service = await readFile(resolve(root, 'src/services/rowynDeb.ts'), 'utf8');
  for (const command of ['rowyn_deb_state', 'rowyn_deb_inspect', 'rowyn_deb_start', 'rowyn_deb_job', 'rowyn_deb_cancel']) {
    assert.match(service, new RegExp(command));
  }
  const backend = await readFile(resolve(root, 'src-tauri/src/commands/rowyn_deb.rs'), 'utf8');
  for (const command of ['rowyn_deb_state', 'rowyn_deb_inspect', 'rowyn_deb_start', 'rowyn_deb_job', 'rowyn_deb_cancel']) {
    assert.match(backend, new RegExp(`fn ${command}`));
  }
  // The backend re-runs the inspector before installing and refuses
  // system verdicts; it never trusts the staged file.
  assert.match(backend, /run_inspect\(&file\)\?/);
  assert.match(backend, /verdict != "user-app"/);
  assert.match(backend, /install:\{\}/);
});
