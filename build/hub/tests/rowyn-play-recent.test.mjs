// A5: "Recently played" shows Steam's own `LastPlayed` and playtime counter.
// The model must never turn a missing value into zero, must never make a
// measurement claim, and must say plainly when Steam could not be read.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-recent-'));
const bundle = join(temp, 'recent.mjs');
await build({
  entryPoints: ['src/services/rowynPlayRecentModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const NOW = 1_762_500_000 * 1000;

test('a missing playtime is not zero minutes', () => {
  assert.equal(m.formatPlaytime(null), 'playtime not recorded');
  assert.equal(m.formatPlaytime(0), '0 min played');
  assert.equal(m.formatPlaytime(45), '45 min played');
  assert.equal(m.formatPlaytime(90), '1.5 h played');
  assert.equal(m.formatPlaytime(1830), '30.5 h played');
  assert.equal(m.formatPlaytime(1200), '20 h played');
});

test('last played is relative wording only, and unknown stays unknown', () => {
  const day = 86_400;
  assert.equal(m.formatLastPlayed(0), 'last played not recorded');
  assert.equal(m.formatLastPlayed(Number.NaN), 'last played not recorded');
  assert.equal(m.formatLastPlayed(NOW / 1000, NOW), 'last played today');
  assert.equal(m.formatLastPlayed(NOW / 1000 - day, NOW), 'last played yesterday');
  assert.equal(m.formatLastPlayed(NOW / 1000 - day * 5, NOW), 'last played 5 days ago');
  assert.equal(m.formatLastPlayed(NOW / 1000 - day * 60, NOW), 'last played 2 months ago');
  assert.equal(m.formatLastPlayed(NOW / 1000 - day * 800, NOW), 'last played 2 years ago');
  // A future timestamp (clock skew) is still "today", never a negative age.
  assert.equal(m.formatLastPlayed(NOW / 1000 + day, NOW), 'last played today');
});

test('the status says what was read, and what could not be', () => {
  const base = { steam_detected: true, profile_read: true, warnings: 0, unnamed: 0, games: [] };
  assert.match(m.recentPlayStatus({ ...base, steam_detected: false }), /Steam was not detected/);
  assert.match(m.recentPlayStatus({ ...base, profile_read: false }), /could not be read/);
  assert.match(m.recentPlayStatus(base), /no played title with a local name yet/);

  const games = [{ app_id: '620', name: 'Portal 2', last_played: 1_762_300_000, playtime_minutes: 1830 }];
  const read = m.recentPlayStatus({ ...base, games });
  assert.match(read, /Read 1 recently played title/);
  assert.match(read, /Rowyn measured nothing here/);
  assert.doesNotMatch(read, /fps|frame rate|latency/i);

  // Unnamed history and skipped profiles are stated, not dropped.
  const partial = m.recentPlayStatus({ ...base, games, warnings: 2, unnamed: 3 });
  assert.match(partial, /2 profile item/);
  assert.match(partial, /3 recently played title/);
  assert.match(partial, /not installed here/);
});

test('the model formats, it does not read or measure', () => {
  for (const name of Object.keys(m)) {
    assert.equal(typeof m[name], 'function', name);
  }
});
