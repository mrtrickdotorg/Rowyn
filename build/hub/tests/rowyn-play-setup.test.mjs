// B1/A5–A7: the Play page's "Get set up to play" strip is projected from
// readings the page already performs. Pure model: every state — including
// "not read yet" and "the read failed" — is asserted here, so the strip can
// never call a step done from a missing reading, and no step is a new probe.

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

const KEYS = ['steam', 'steamPlay', 'layers', 'shader', 'recent', 'mangohud', 'controllers'];

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
  shaderCache: { steam_detected: true, config_read: true, pre_caching: true, warnings: 0 },
  shaderFailed: false,
  recentPlay: {
    steam_detected: true, profile_read: true, warnings: 0, unnamed: 0,
    games: [{ app_id: '730', name: 'Counter-Strike 2', last_played: 1_700_000_000, playtime_minutes: 10 }],
  },
  recentFailed: false,
  mangohudConfig: {
    steam_detected: true, flatpak_steam: true,
    path: '.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf',
    exists: true, readable: true, settings: 4,
    native_path: '.config/MangoHud/MangoHud.conf', native_exists: false, warnings: 0,
  },
  mangohudFailed: false,
};

const states = (steps) => steps.map((step) => step.state);

test('all seven steps are done when every reading says ready', () => {
  const steps = m.playSetupSteps(READY);
  assert.deepEqual(steps.map((step) => step.key), KEYS);
  assert.deepEqual(states(steps), ['done', 'done', 'done', 'done', 'done', 'done', 'done']);
  assert.match(steps[3].hint, /setting is on/);
  assert.match(steps[4].hint, /1 recently played/);
  assert.match(steps[5].hint, /4 setting\(s\)/);
});

test('nothing is claimed before the reads land', () => {
  const steps = m.playSetupSteps({
    steam: 'pending',
    steamPlay: null, steamPlayFailed: false,
    layers: null, layersFailed: false,
    controllers: null, controllersFailed: false,
    shaderCache: null, shaderFailed: false,
    recentPlay: null, recentFailed: false,
    mangohudConfig: null, mangohudFailed: false,
  });
  assert.deepEqual(states(steps), Array(7).fill('pending'));
  for (const step of steps) assert.doesNotMatch(step.hint, /could not/, step.key);
});

test('a failed read is unknown, never done and never a task', () => {
  const steps = m.playSetupSteps({
    steam: 'unavailable',
    steamPlay: null, steamPlayFailed: true,
    layers: null, layersFailed: true,
    controllers: null, controllersFailed: true,
    shaderCache: null, shaderFailed: true,
    recentPlay: null, recentFailed: true,
    mangohudConfig: null, mangohudFailed: true,
  });
  assert.deepEqual(states(steps), Array(7).fill('unknown'));
  // Unknown states say how to retry instead of inventing a fix.
  assert.match(steps[1].hint, /Check Steam Play/);
  assert.match(steps[2].hint, /Check Vulkan layers/);
  assert.match(steps[3].hint, /Check shader pre-caching/);
  assert.match(steps[4].hint, /Refresh recently played/);
  assert.match(steps[5].hint, /Check MangoHud settings/);
  assert.match(steps[6].hint, /Rescan controllers/);
});

test('a successful re-read wins over an earlier failure', () => {
  const steps = m.playSetupSteps({
    ...READY, layersFailed: true, controllersFailed: true,
    shaderFailed: true, recentFailed: true, mangohudFailed: true,
  });
  assert.deepEqual(states(steps), Array(7).fill('done'));
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

test('shader pre-caching is reported as Steam recorded it, off is a task', () => {
  const off = m.playSetupSteps({ ...READY, shaderCache: { ...READY.shaderCache, pre_caching: false } })[3];
  assert.equal(off.state, 'todo');
  assert.match(off.label, /Enable Shader Pre-Caching/);
  assert.match(off.hint, /Steam → Settings → Downloads/);
  assert.match(off.hint, /Rowyn does not change it/);
  const unrecorded = m.playSetupSteps({ ...READY, shaderCache: { ...READY.shaderCache, pre_caching: null } })[3];
  assert.equal(unrecorded.state, 'done');
  assert.match(unrecorded.label, /default/);
  assert.match(unrecorded.hint, /default \(on\)/);
  const unreadable = m.playSetupSteps({ ...READY, shaderCache: { ...READY.shaderCache, config_read: false } })[3];
  assert.equal(unreadable.state, 'unknown');
  assert.match(unreadable.hint, /config\.vdf/);
});

test('recent play is done only when Steam’s profile was actually read', () => {
  const unread = m.playSetupSteps({ ...READY, recentPlay: { ...READY.recentPlay, profile_read: false } })[4];
  assert.equal(unread.state, 'unknown');
  assert.match(unread.hint, /start Steam once/);
  const empty = m.playSetupSteps({ ...READY, recentPlay: { ...READY.recentPlay, games: [] } })[4];
  assert.equal(empty.state, 'done');
  assert.match(empty.hint, /no recently played title/);
  const missing = m.playSetupSteps({ ...READY, recentPlay: { ...READY.recentPlay, steam_detected: false } })[4];
  assert.equal(missing.state, 'unknown');
  assert.match(missing.hint, /Steam was not detected/);
});

test('MangoHud names its path; a missing config is optional, not a fault', () => {
  const absent = m.playSetupSteps({
    ...READY,
    mangohudConfig: { ...READY.mangohudConfig, exists: false, readable: false, settings: 0 },
  })[5];
  assert.equal(absent.state, 'todo');
  assert.match(absent.label, /optional/);
  assert.match(absent.hint, /MangoHud uses its defaults/);
  assert.match(absent.hint, /recommended config/);
  const unreadable = m.playSetupSteps({
    ...READY,
    mangohudConfig: { ...READY.mangohudConfig, readable: false, settings: 0 },
  })[5];
  assert.equal(unreadable.state, 'unknown');
  assert.match(unreadable.hint, /could not be read/);
  const noSteam = m.playSetupSteps({
    ...READY,
    mangohudConfig: { ...READY.mangohudConfig, steam_detected: false },
  })[5];
  assert.equal(noSteam.state, 'unknown');
});

test('a missing controller is a task, not a failure', () => {
  const none = { ...READY, controllers: [{ name: 'Input devices', state: 'warn', detail: 'none' }] };
  const step = m.playSetupSteps(none)[6];
  assert.equal(step.state, 'todo');
  // The label marks it optional; the hint says how to clear it.
  assert.match(step.label, /optional/);
  assert.match(step.hint, /pair it over Bluetooth/);
});
