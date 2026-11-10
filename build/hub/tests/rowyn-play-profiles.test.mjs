// Pass 2: per-game Play profiles — persistence and launch assembly.
// Bundles the model (and its launchOptions dependency) with esbuild,
// like the other frontend suites.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-profiles-'));
const bundle = join(temp, 'profiles.mjs');
await build({
  entryPoints: ['src/services/rowynPlayProfilesModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

function fakeStorage() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    map,
  };
}

const FULL = { mangohud: true, vkbasalt: true, gamescope: false, gaming: true, mimalloc: true, extra: '' };

test('profiles persist per title and survive a reload', () => {
  const storage = fakeStorage();
  const profiles = { '730': { ...FULL }, '1245620': { ...m.EMPTY_PROFILE, vkbasalt: true } };
  assert.equal(m.saveProfiles(profiles, storage), null);
  const loaded = m.loadProfiles(storage);
  assert.deepEqual(loaded['730'], FULL);
  assert.equal(loaded['1245620'].vkbasalt, true);
  assert.equal(loaded['1245620'].mangohud, false);
  // A title with no saved profile gets the default (stateless) profile.
  assert.deepEqual(m.profileFor(loaded, '999999'), m.EMPTY_PROFILE);
  assert.equal(m.isDefaultProfile(m.profileFor(loaded, '999999')), true);
  assert.equal(m.isDefaultProfile(loaded['730']), false);
});

test('corrupt or partial storage never breaks the page', () => {
  const storage = fakeStorage();
  storage.map.set('rowyn.play.profiles.v1', '{not json');
  assert.deepEqual(m.loadProfiles(storage), {});
  storage.map.set('rowyn.play.profiles.v1', JSON.stringify({ '730': { mangohud: 'yes', extra: 42 } }));
  const loaded = m.loadProfiles(storage);
  assert.equal(loaded['730'].mangohud, false);
  assert.equal(loaded['730'].extra, '');
  assert.deepEqual(m.loadProfiles(null), {});
});

test('a failed save reports a warning instead of pretending', () => {
  const broken = { getItem: () => null, setItem: () => { throw new Error('full'); } };
  assert.match(m.saveProfiles({}, broken), /could not be saved/);
  assert.match(m.saveProfiles({}, null), /cannot be saved/);
});

test('the default profile assembles to the stateless strings', () => {
  assert.equal(m.assembleProfileOptions('flatpak', m.EMPTY_PROFILE), '%command%');
  assert.equal(m.assembleProfileOptions('host', m.EMPTY_PROFILE), '%command%');
});

test('assembly composes env switches, wrappers and extras in order', () => {
  assert.equal(
    m.assembleProfileOptions('flatpak', { ...FULL, extra: '-high' }),
    'MANGOHUD=1 ENABLE_VKBASALT=1 %command% -high');
  assert.equal(
    m.assembleProfileOptions('host', FULL),
    'MANGOHUD=1 ENABLE_VKBASALT=1 /usr/bin/rowyn-game-launch /usr/bin/rowyn-mimalloc-run %command%');
});

test('gamescope prefixes the host boundary after the env switches only', () => {
  assert.equal(
    m.assembleProfileOptions('host', { ...m.EMPTY_PROFILE, gamescope: true }),
    'gamescope -- %command%');
  assert.equal(
    m.assembleProfileOptions('host', { ...m.EMPTY_PROFILE, gamescope: true, mangohud: true, gaming: true }),
    'MANGOHUD=1 gamescope -- /usr/bin/rowyn-game-launch %command%');
  // Flatpak: the layer carries gamescope; the option string is unchanged.
  assert.equal(
    m.assembleProfileOptions('flatpak', { ...m.EMPTY_PROFILE, gamescope: true, mangohud: true }),
    'MANGOHUD=1 %command%');
});
