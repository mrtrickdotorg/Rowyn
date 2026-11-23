// B1: the Play page's "Get set up to play" strip is projected from readings
// the page already performs. Pure model: every state — including "not read
// yet" and "the read failed" — is asserted here, so the strip can never call
// a step done from a missing reading.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-setup-'));
const bundle = join(temp, 'setup.mjs');
await build({
  entryPoints: ['src/services/rowynPlaySetupModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const READY = {
  steam: 'installed',
  steamPlay: { steam_present: true, mapping_present: true, steam_running: false, detail: '' },
  steamPlayFailed: false,
  layers: {
    steam_runtime_branch: '24.08',
    mangohud_branches: ['24.08'],
    gamescope_branches: ['24.08'],
    vkbasalt_branches: ['24.08'],
    detail: '',
  },
  layersFailed: false,
  controllers: [{ name: 'Input devices', state: 'ok', detail: 'one pad' }],
  controllersFailed: false,
};

const states = (steps) => steps.map((step) => step.state);

test('all four steps are done when every reading says ready', () => {
  const steps = m.playSetupSteps(READY);
  assert.deepEqual(steps.map((step) => step.key), ['steam', 'steamPlay', 'layers', 'controllers']);
  assert.deepEqual(states(steps), ['done', 'done', 'done', 'done']);
});

test('nothing is claimed before the reads land', () => {
  const steps = m.playSetupSteps({
    steam: 'pending',
    steamPlay: null, steamPlayFailed: false,
    layers: null, layersFailed: false,
    controllers: null, controllersFailed: false,
  });
  assert.deepEqual(states(steps), ['pending', 'pending', 'pending', 'pending']);
  for (const step of steps) assert.doesNotMatch(step.hint, /could not/, step.key);
});

test('a failed read is unknown, never done and never a task', () => {
  const steps = m.playSetupSteps({
    steam: 'unavailable',
    steamPlay: null, steamPlayFailed: true,
    layers: null, layersFailed: true,
    controllers: null, controllersFailed: true,
  });
  assert.deepEqual(states(steps), ['unknown', 'unknown', 'unknown', 'unknown']);
  // Unknown states say how to retry instead of inventing a fix.
  assert.match(steps[1].hint, /Check Steam Play/);
  assert.match(steps[2].hint, /Check Vulkan layers/);
  assert.match(steps[3].hint, /Rescan controllers/);
});

test('a successful re-read wins over an earlier failure', () => {
  const steps = m.playSetupSteps({ ...READY, layersFailed: true, controllersFailed: true });
  assert.deepEqual(states(steps), ['done', 'done', 'done', 'done']);
});

test('Steam Play names the blocker: a running Steam or a missing mapping', () => {
  const unmapped = { ...READY, steamPlay: { ...READY.steamPlay, mapping_present: false } };
  assert.equal(states(m.playSetupSteps(unmapped))[1], 'todo');
  assert.match(m.playSetupSteps(unmapped)[1].hint, /Enable Steam Play for all titles below/);
  const running = { ...READY, steamPlay: { ...unmapped.steamPlay, steam_running: true } };
  assert.match(m.playSetupSteps(running)[1].hint, /Quit Steam/);
});

test('layers name the runtime branch and exactly what is missing', () => {
  const partial = {
    ...READY,
    layers: { ...READY.layers, gamescope_branches: [], vkbasalt_branches: ['23.08'] },
  };
  const step = m.playSetupSteps(partial)[2];
  assert.equal(step.state, 'todo');
  assert.match(step.hint, /24\.08/);
  assert.match(step.hint, /gamescope, vkBasalt/);
  assert.doesNotMatch(step.hint, /MangoHud/);
  // A layer installed for another branch is not coverage for this one.
  assert.equal(m.playSetupSteps({ ...READY, layers: { ...READY.layers, steam_runtime_branch: null } })[2].state, 'unknown');
});

test('a missing controller is a task, not a failure', () => {
  const none = { ...READY, controllers: [{ name: 'Input devices', state: 'warn', detail: 'none' }] };
  const step = m.playSetupSteps(none)[3];
  assert.equal(step.state, 'todo');
  // The label marks it optional; the hint says how to clear it.
  assert.match(step.label, /optional/);
  assert.match(step.hint, /pair it over Bluetooth/);
});
