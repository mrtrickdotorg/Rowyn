// B6: the helpers block names the three shipped commands, reports the
// rowyn-tune state, and offers copyable wrapper examples. The model must not
// claim a measurement or hide the host-only boundary.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-helpers-'));
const bundle = join(temp, 'helpers.mjs');
await build({
  entryPoints: ['src/services/rowynPlayHelpersModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const installed = { tune: 'default', installed: true, detail: '' };

test('the block names all three helpers and what each does', () => {
  for (const name of ['rowyn-game-launch', 'rowyn-mimalloc-run', 'rowyn-tune']) {
    assert.match(m.GAMING_HELPERS_NOTE, new RegExp(name));
  }
  assert.match(m.GAMING_HELPERS_NOTE, /ship in the image/);
  assert.match(m.GAMING_HELPERS_NOTE, /nothing is installed/i);
});

test('the tune state reads on, off, unread and missing without guessing', () => {
  assert.equal(m.gamingTuneLabel(null), 'Helper state not read yet.');
  assert.match(m.gamingTuneLabel({ ...installed, tune: 'gaming' }), /gaming profile on/);
  assert.match(m.gamingTuneLabel(installed), /default profile/);
  assert.match(m.gamingTuneLabel({ ...installed, installed: false }), /missing/);
  assert.equal(m.tuneIsOn(installed), false);
  assert.equal(m.tuneIsOn({ ...installed, tune: 'gaming' }), true);
  assert.equal(m.tuneIsOn(null), false);
});

test('the toggle copy states the admin path and the reboot boundary', () => {
  assert.match(m.TUNE_ADMIN_NOTE, /administrator password/);
  assert.match(m.TUNE_ADMIN_NOTE, /askpass/);
  assert.match(m.TUNE_ADMIN_NOTE, /\/etc\/sysctl\.d\/90-rowyn-gaming\.conf/);
  assert.match(m.TUNE_OFF_NOTE, /next boot/);
  assert.match(m.TUNE_OFF_NOTE, /keeps the tuned values/);
});

test('both wrapper examples are host paths with %command% and a host-only note', () => {
  assert.deepEqual(
    m.HELPER_EXAMPLES.map((example) => example.command),
    ['/usr/bin/rowyn-game-launch %command%', '/usr/bin/rowyn-mimalloc-run %command%'],
  );
  for (const example of m.HELPER_EXAMPLES) {
    assert.match(example.label, /host-native/);
    assert.match(example.command, /^\/usr\/bin\//);
  }
  assert.match(m.HOST_ONLY_NOTE, /Flatpak Steam/);
  assert.match(m.HOST_ONLY_NOTE, /cannot run them/);
});

test('the block is text only: no fetch, timer or measurement claim', () => {
  for (const name of Object.keys(m)) {
    assert.ok(['function', 'string', 'object', 'boolean'].includes(typeof m[name]), name);
  }
  const text = JSON.stringify(m);
  for (const forbidden of ['fetch(', 'setInterval', 'setTimeout', 'measured', 'benchmark result']) {
    assert.ok(!text.includes(forbidden), forbidden);
  }
});
