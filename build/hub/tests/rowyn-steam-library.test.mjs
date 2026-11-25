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
const mvpBundle = join(temp, 'mvp.mjs');
await build({
  entryPoints: ['src/services/rowynMvpModel.ts'],
  outfile: mvpBundle, bundle: true, format: 'esm', platform: 'node',
  loader: { '.json': 'json' },
});
const mvp = await import(pathToFileURL(mvpBundle).href);
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

const state = (status) => ({ label: status, note: '', anticheat: '', checked: '2026-05-18', status });
const entry = (app_id, name, cover = null, verdict = null) => ({ game: game(app_id, name, cover), verdict });

const LIBRARY = [
  entry('620', 'Portal 2', null, state('native')),
  entry('999', 'Pokémon Violet', 'data:image/jpeg;base64,AAAA', state('proton')),
  entry('220', 'Half-Life 2', null, state('tweaks')),
  entry('570', 'Dota 2', null, null),
  entry('730', 'Counter-Strike 2', null, state('blocked')),
];

const names = (entries) => entries.map((item) => item.game.name);

test('entries are sorted once and resolve each verdict from the app id alone', () => {
  const asked = [];
  const entries = m.steamLibraryEntries(
    [game('620', 'portal 2'), game('570', 'Dota 2')],
    (appId) => { asked.push(appId); return state('proton'); },
  );
  assert.deepEqual(names(entries), ['Dota 2', 'portal 2']);
  assert.deepEqual(asked, ['570', '620']);
  assert.equal(entries[0].verdict.status, 'proton');
});

test('search folds case and accents, reads punctuation as spaces, and matches app ids', () => {
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, 'POKÉMON', 'all')), ['Pokémon Violet']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, 'pokemon', 'all')), ['Pokémon Violet']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, 'half life', 'all')), ['Half-Life 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '  portal  ', 'all')), ['Portal 2']);
  // An app-id prefix is deliberate: 22 is not a title match for 220 only.
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '22', 'all')), ['Half-Life 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '57', 'all')), ['Dota 2']);
  assert.equal(m.filterSteamLibrary(LIBRARY, '', 'all').length, LIBRARY.length);
  assert.equal(m.filterSteamLibrary(LIBRARY, 'zzz', 'all').length, 0);
});

test('the curated-state filter and the search compose', () => {
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '', 'native')), ['Portal 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '', 'proton')), ['Pokémon Violet']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '', 'tweaks')), ['Half-Life 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '', 'blocked')), ['Counter-Strike 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '', 'unreviewed')), ['Dota 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, '2', 'unreviewed')), ['Dota 2']);
  assert.deepEqual(names(m.filterSteamLibrary(LIBRARY, 'portal', 'blocked')), []);
});

test('coverage counts what the local reads actually produced', () => {
  assert.deepEqual(m.steamLibraryCoverage(LIBRARY), { total: 5, withVerdict: 4, covered: 1 });
  assert.deepEqual(m.steamLibraryCoverage([]), { total: 0, withVerdict: 0, covered: 0 });
});

test('the cover budget caps rendered covers without mutating the scan', () => {
  const many = Array.from({ length: 65 }, (_, index) => entry(String(index + 1), `Game ${index + 1}`, 'data:image/jpeg;base64,AAAA'));
  const plan = m.steamLibraryCoverPlan(many);
  assert.equal(m.STEAM_COVER_BUDGET, 60);
  assert.equal(plan.entries.length, 65);
  assert.equal(plan.covers, 60);
  assert.equal(plan.budgeted, 5);
  assert.equal(plan.entries.filter((item) => item.game.cover).length, 60);
  // The scan result keeps every cover; only the plan drops the extras.
  assert.equal(many.filter((item) => item.game.cover).length, 65);
  assert.deepEqual(m.steamLibraryCoverPlan(many, 2).entries.map((item) => Boolean(item.game.cover)).slice(0, 3), [true, true, false]);
  // A filtered list starts its own budget, so a later title's cover comes back.
  const filtered = m.filterSteamLibrary(many, 'Game 65', 'all');
  assert.ok(m.steamLibraryCoverPlan(filtered).entries[0].game.cover);
});

test('the coverage note reports counts, filtering and the budget, and stays local', () => {
  const coverage = { total: 65, withVerdict: 2, covered: 65 };
  const note = m.steamLibraryCoverageNote(coverage, 65, 5);
  assert.match(note, /65 installed title/);
  assert.match(note, /2 carry a curated note/);
  assert.match(note, /65 have Steam’s cached cover art/);
  assert.match(note, /5 more cover\(s\)/);
  assert.match(note, /60-cover budget/);
  assert.match(note, /no network request was made/i);
  assert.doesNotMatch(note, /\bfps\b|frame rate|latency/i);

  assert.doesNotMatch(m.steamLibraryCoverageNote(coverage, 65, 0), /budget/);
  assert.match(m.steamLibraryCoverageNote(coverage, 2, 0), /Showing 2 under this search and filter/);
  assert.equal(m.steamLibraryCoverageNote({ total: 0, withVerdict: 0, covered: 0 }, 0, 0), '');
});

const FRESH_NOW = Date.parse('2026-08-01T00:00:00Z');

test('one dated provenance note carries the caveats the cards used to repeat', () => {
  const note = m.steamLibraryProvenance('2026-05-18', 40, FRESH_NOW);
  assert.match(note, /last checked 2026-05-18/);
  assert.match(note, /cover 40 titles/);
  assert.match(note, /Titles without a note show no verdict/);
  assert.match(note, /no network request/);
  assert.match(note, /not guarantees/);
  assert.match(note, /not Rowyn game validation/);
  // The date and the update caveat appear once, in the section note.
  assert.equal(note.split('2026-05-18').length - 1, 1);
  assert.equal(note.split('updates can change').length - 1, 1);

  // Without a dataset, no date is invented and no verdict is claimed.
  const none = m.steamLibraryProvenance(null, 0);
  assert.match(none, /no title carries a verdict/i);
  assert.doesNotMatch(none, /last checked/);
});

test('a note older than six months says so in the same dated note', () => {
  const stale = m.steamLibraryProvenance('2026-05-18', 30, Date.parse('2026-11-23T00:00:00Z'));
  assert.match(stale, /more than six months ago/);
  assert.match(stale, /treat them as stale/);
  assert.match(stale, /ProtonDB/);
  // Still one dated note: one date and one update caveat, not a second note.
  assert.equal(stale.split('2026-05-18').length - 1, 1);
  assert.equal(stale.split('updates can change').length - 1, 1);
  assert.doesNotMatch(stale, /last checked 2026-05-18; they are guidance/);

  // Six months is the line: day 183 is dated, day 184 is stale.
  assert.equal(m.COMPAT_NOTE_STALE_DAYS, 183);
  assert.equal(m.compatNoteAgeDays('2026-05-18', Date.parse('2026-11-17T12:00:00Z')), 183);
  assert.equal(m.compatNoteAgeDays('2026-05-18', Date.parse('2026-11-18T00:00:00Z')), 184);
  assert.doesNotMatch(
    m.steamLibraryProvenance('2026-05-18', 30, Date.parse('2026-11-17T12:00:00Z')),
    /stale/,
  );
  // A date ahead of the clock or an unreadable date makes no stale claim.
  assert.equal(m.compatNoteAgeDays('2026-12-31', Date.parse('2026-11-23T00:00:00Z')), 0);
  assert.equal(m.compatNoteAgeDays('not-a-date', Date.now()), null);
  assert.doesNotMatch(m.steamLibraryProvenance('not-a-date', 30, Date.now()), /stale/);
});

test('the shipped dataset is flagged when its note would be stale', () => {
  const reviewedOn = mvp.compatReviewedOn();
  assert.ok(reviewedOn, 'the shipped dataset must carry a check date');
  const age = m.compatNoteAgeDays(reviewedOn, Date.now());
  assert.ok(age !== null && age >= 0);
  const note = m.steamLibraryProvenance(reviewedOn, mvp.compatVerdictCount());
  if (age > m.COMPAT_NOTE_STALE_DAYS) {
    console.warn(
      `FLAG: curated compatibility notes are ${age} days old (checked ${reviewedOn}); refresh compat_games.json.`,
    );
    assert.match(note, /more than six months ago/, 'a stale dataset must be called stale on the page');
  } else {
    assert.doesNotMatch(note, /stale/);
  }
});

test('the model projects, it does not fetch or measure', () => {
  for (const name of Object.keys(m)) {
    assert.ok(['function', 'string', 'number'].includes(typeof m[name]), name);
  }
});

test('the scan status names what the folder list said', () => {
  const scan = (over) => ({
    available: true,
    warnings: 0,
    games: [game('570', 'Dota 2')],
    libraries: { scanned: 2, unavailable: 0, list_read: true },
    ...over,
  });
  const clean = m.steamScanStatus(scan({}));
  assert.match(clean, /Detected 1 installed title\(s\)/);
  assert.match(clean, /2 library folder\(s\) Steam lists/);
  assert.doesNotMatch(clean, /may be missing/);
  assert.doesNotMatch(clean, /Compatibility was tested/);

  // An unmounted card is named, not folded into the warning count.
  const missingFolder = m.steamScanStatus(scan({ libraries: { scanned: 2, unavailable: 1, list_read: true } }));
  assert.match(missingFolder, /1 folder\(s\) Steam lists could not be read/);
  assert.match(missingFolder, /unmounted drive/);

  // The hedge survives only when the list itself could not be read.
  const unlisted = m.steamScanStatus(scan({ libraries: { scanned: 1, unavailable: 0, list_read: false } }));
  assert.match(unlisted, /libraryfolders\.vdf could not be read/);
  assert.match(unlisted, /may be missing/);

  assert.match(m.steamScanStatus({ available: false, warnings: 0, games: [] }), /not detected/);
});
