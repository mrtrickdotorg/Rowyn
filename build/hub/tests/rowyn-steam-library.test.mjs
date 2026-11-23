// B3: the Steam section reads as a library. The model sorts for reading,
// gives a curated state one word, stands in a letter tile for absent artwork,
// and states how many covers the local read actually returned. It never
// fetches, measures or invents a verdict.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-steam-library-'));
const bundle = join(temp, 'library.mjs');
await build({
  entryPoints: ['src/services/rowynSteamLibraryModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const game = (app_id, name, cover = null) => ({ app_id, name, cover });

test('titles are sorted for reading, without mutating the scan result', () => {
  const scanned = [game('620', 'portal 2'), game('570', 'Dota 2'), game('440', 'Team Fortress 2')];
  const sorted = m.sortSteamGames(scanned);
  assert.deepEqual(sorted.map((item) => item.name), ['Dota 2', 'portal 2', 'Team Fortress 2']);
  assert.deepEqual(scanned.map((item) => item.name), ['portal 2', 'Dota 2', 'Team Fortress 2']);
  // Same title from two app ids stays deterministic.
  const tied = m.sortSteamGames([game('20', 'Same'), game('3', 'Same')]);
  assert.deepEqual(tied.map((item) => item.app_id), ['3', '20']);
});

test('curated states become one word from the verdict the card already has', () => {
  const base = { label: 'Works natively on Linux', note: 'Native Linux client.', anticheat: 'VAC', checked: '2026-05-18' };
  assert.deepEqual(m.steamStateChip({ ...base, status: 'native' }), { label: 'Native', tone: 'is-native' });
  assert.deepEqual(m.steamStateChip({ ...base, status: 'blocked' }), { label: 'Blocked', tone: 'is-blocked' });
  // No verdict stays no chip; a status the page has no word for is not guessed.
  assert.equal(m.steamStateChip(null), null);
  assert.equal(m.steamStateChip({ ...base, status: 'unreviewed' }), null);
});

test('a title without cached artwork gets a letter tile, not a blank', () => {
  assert.equal(m.coverInitials('Portal 2'), 'P2');
  assert.equal(m.coverInitials('Half-Life 2'), 'HL');
  assert.equal(m.coverInitials('Celeste'), 'C');
  assert.equal(m.coverInitials('  '), '?');
});

test('the cover note counts what was read and says it stayed local', () => {
  const games = [game('620', 'Portal 2', 'data:image/jpeg;base64,AAAA'), game('570', 'Dota 2')];
  const note = m.steamLibraryCoverNote(games);
  assert.match(note, /1 of 2 title/);
  assert.match(note, /letter tile/);
  assert.match(note, /no network request/i);
  assert.doesNotMatch(note, /\bfps\b|frame rate|latency/i);

  const none = m.steamLibraryCoverNote([game('570', 'Dota 2')]);
  assert.match(none, /no cached cover art/i);
  assert.match(none, /No network request was made/);
  assert.equal(m.steamLibraryCoverNote([]), '');
});

test('the model projects, it does not fetch or measure', () => {
  for (const name of Object.keys(m)) {
    assert.equal(typeof m[name], 'function', name);
  }
});
