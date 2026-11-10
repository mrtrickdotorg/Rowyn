import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-mvp-tests-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynMvpModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

test('report preview contains explicit unavailable/error states and no hidden fields', () => {
  const report = m.formatReport({ collected_at: 0, readings: [
    { name: 'Image', state: 'unavailable', detail: 'permission denied', hidden: 'do not export' },
    { name: 'GPU', state: 'error', detail: 'check failed' },
  ] });
  assert.match(report, /Image \[unavailable\]: permission denied/);
  assert.match(report, /GPU \[error\]: check failed/);
  assert.doesNotMatch(report, /do not export/);
  assert.match(report, /Review before sharing/);
});

test('report keeps every reading and states any truncation instead of stopping at sixteen', () => {
  const readings = Array.from({ length: 24 }, (_, index) => ({ name: `Check ${index + 1}`, state: 'ok', detail: 'value' }));
  const report = m.formatReport({ collected_at: 0, readings });
  assert.match(report, /Check 1 \[ok\]/);
  assert.match(report, /Check 24 \[ok\]/, 'the last reading must not be dropped silently');
  assert.doesNotMatch(report, /report truncated/);

  // One oversized probe is shortened in place, not allowed to displace others.
  const long = m.formatReport({ collected_at: 0, readings: [{ name: 'Displays', state: 'ok', detail: 'y'.repeat(200_000) }] });
  assert.match(long, /Displays \[ok\]/);
  assert.match(long, /detail truncated/);

  // Enough large readings to cross the native export limit: every omission is named.
  const many = Array.from({ length: 40 }, (_, index) => ({ name: `Big ${index + 1}`, state: 'ok', detail: 'd'.repeat(6_000) }));
  const bounded = m.formatReport({ collected_at: 0, readings: many });
  assert.ok(new TextEncoder().encode(bounded).length <= m.REPORT_BYTE_LIMIT, 'export must fit the native byte limit');
  assert.match(bounded, /report truncated: \d+ reading\(s\) omitted/);
});

test('the diagnostics page classifies readings with the bridge vocabulary, not "available"', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynDiagnostics.tsx'), 'utf8');
  assert.doesNotMatch(page, /'available'/);
  assert.match(page, /readingNeedsAttention\(/);
  assert.match(page, /readingTone\(/);
  assert.match(page, /readingStateLabel\(/);
  assert.deepEqual([...m.READING_STATES], ['ok', 'attention', 'error', 'unavailable']);
  assert.equal(m.readingNeedsAttention('ok'), false);
  assert.equal(m.readingNeedsAttention('attention'), true);
  assert.equal(m.readingNeedsAttention('error'), true);
  assert.equal(m.readingNeedsAttention('unavailable'), false, 'unavailable is its own label, not a finding');
  assert.equal(m.readingTone('ok'), 'is-ok');
  assert.equal(m.readingTone('attention'), 'is-partial');
  assert.equal(m.readingTone('unavailable'), 'is-missing');
  assert.equal(m.readingStateLabel('attention'), 'needs attention');
});

test('launcher selection only accepts the four curated ids', () => {
  assert.equal(m.LAUNCHERS.length, 4);
  for (const launcher of m.LAUNCHERS) assert.equal(m.requireLauncher(launcher.id).id, launcher.id);
  for (const id of ['', '--system', 'org.example.App', 'com.valvesoftware.Steam;id'])
    assert.throws(() => m.requireLauncher(id), /Unsupported launcher/);
});

test('unknown installed scopes stay unknown, while either confirmed scope is installed', () => {
  const id = m.LAUNCHERS[0].id;
  assert.equal(m.launcherState(id, null), 'unavailable');
  assert.equal(m.launcherState(id, { apps: [], failedScopes: ['system'] }), 'unavailable');
  assert.equal(m.launcherState(id, { apps: [], failedScopes: [] }), 'not installed');
  assert.equal(m.launcherState(id, { apps: [{ id, scope: 'system' }], failedScopes: ['user'] }), 'installed');
});

test('curated install preserves terminal success, failure and cancellation', async () => {
  const id = m.LAUNCHERS[0].id;
  const calls = [];
  const ops = { install: async (value) => { calls.push(value); return 'flatpak-123'; }, wait: async (job, limit) => { calls.push([job, limit]); return 'Cancelled.'; } };
  const started = [];
  assert.equal(await m.installLauncher(id, ops, (job) => started.push(job)), 'Cancelled.');
  assert.deepEqual(started, ['flatpak-123'], 'UI must receive the running job before waiting so Cancel becomes visible');
  assert.deepEqual(calls, [id, ['flatpak-123', 1800]]);
  ops.wait = async () => 'Installed successfully.';
  assert.equal(await m.installLauncher(id, ops), 'Installed successfully.');
  ops.wait = async () => { throw new Error('remote unavailable'); };
  await assert.rejects(m.installLauncher(id, ops), /remote unavailable/);
  await assert.rejects(m.installLauncher('org.unknown.App', ops), /Unsupported launcher/);
});

test('cancel owns the status even when the primary finishes late; failed cancel is visible', async () => {
  const messages = [];
  const owner = m.createPlayStatusOwner((message) => messages.push(message));
  let finish;
  const pending = owner.run('Installing…', () => new Promise((resolve) => { finish = resolve; }));
  await owner.cancel(async () => 'Cancelled.');
  finish('Installed successfully.');
  await pending;
  assert.equal(messages.at(-1), 'Cancelled.');
  await owner.run('Opening…', async () => { throw new Error('not installed'); });
  assert.equal(messages.at(-1), 'Failed: not installed');
  await owner.cancel(async () => { throw new Error('transport unavailable'); });
  assert.equal(messages.at(-1), 'Cancel failed: transport unavailable');
  await owner.run('Opening…', async () => 'Launch requested.');
  assert.equal(messages.at(-1), 'Launch requested.');
});

test('host-only options never pretend to work inside Flatpak Steam', () => {
  assert.equal(m.launchOptions('flatpak', true, true), '%command%');
  assert.equal(m.launchOptions('host', true, false), '/usr/bin/rowyn-game-launch %command%');
  assert.equal(m.launchOptions('host', false, true), '/usr/bin/rowyn-mimalloc-run %command%');
  assert.match(m.launchOptions('host', true, true), /rowyn-game-launch \/usr\/bin\/rowyn-mimalloc-run %command%/);
});

test('MangoHud is an env switch on both boundaries, ordered before wrappers', () => {
  assert.equal(m.launchOptions('flatpak', true, true, true), 'MANGOHUD=1 %command%');
  assert.equal(m.launchOptions('flatpak', false, false, false), '%command%');
  assert.equal(m.launchOptions('host', true, false, true), 'MANGOHUD=1 /usr/bin/rowyn-game-launch %command%');
  assert.equal(m.launchOptions('host', false, false, true), 'MANGOHUD=1 %command%');
});

test('vkBasalt is an env switch on both boundaries and composes with MangoHud', () => {
  assert.equal(m.launchOptions('flatpak', false, false, false, true), 'ENABLE_VKBASALT=1 %command%');
  assert.equal(m.launchOptions('flatpak', false, false, true, true), 'MANGOHUD=1 ENABLE_VKBASALT=1 %command%');
  assert.equal(m.launchOptions('host', true, false, false, true), 'ENABLE_VKBASALT=1 /usr/bin/rowyn-game-launch %command%');
  assert.equal(m.launchOptions('host', false, false, false, false), '%command%');
});

test('curated compat verdicts resolve by app id, then by title, else null', () => {
  const cs2 = m.compatVerdict('730', 'Counter-Strike 2');
  assert.ok(cs2);
  assert.match(cs2.label, /natively/);
  // A title match survives edition punctuation differences.
  const byName = m.compatVerdict('999999', 'Counter-Strike 2');
  assert.ok(byName);
  assert.equal(byName.label, cs2.label);
  // Unknown titles get no invented verdict.
  assert.equal(m.compatVerdict('999999', 'Definitely Not A Curated Game'), null);
});

test('Steam compatibility links accept only numeric app IDs and clearly point to community data', () => {
  assert.deepEqual(m.steamCompatibilityLinks('620'), {
    protondb: 'https://www.protondb.com/app/620',
    steamStore: 'https://store.steampowered.com/app/620',
  });
  for (const id of ['', '0', '000620', '../620', '620?x=1', '１２３', '12345678901'])
    assert.throws(() => m.steamCompatibilityLinks(id), /invalid Steam app id/);
});

test('native pages wire explicit reads, report actions, install and cancel without timer loops', async () => {
  const diagnostics = await readFile(resolve(root, 'src/pages/RowynDiagnostics.tsx'), 'utf8');
  const play = await readFile(resolve(root, 'src/pages/RowynPlay.tsx'), 'utf8');
  for (const text of [diagnostics, play]) assert.doesNotMatch(text, /setInterval|setTimeout/);
  assert.match(diagnostics, /copyText\(report\)/);
  assert.match(diagnostics, /useSectionFollow\(SECTIONS\)/);
  assert.match(diagnostics, /exportRowynReport\(report\)/);
  assert.match(play, /waitInstallJob/);
  assert.match(play, /cancelInstall/);
  assert.match(play, /rowynControllerScan/);
  assert.match(play, /rowynSteamGamesScan/);
  assert.match(play, /Scan Steam games \(local only\)/);
  assert.match(play, /not Rowyn-validated/);
  assert.match(play, /ExternalLink/);
  assert.equal([...play.matchAll(/id="hub-section-compatibility"/g)].length, 1);
  assert.equal([...play.matchAll(/Compatibility &amp; anti-cheat/g)].length, 1);
  assert.match(play, /useSectionFollow\(SECTIONS\)/);
  assert.match(play, /uninstallFlatpak\(launcher\.id\)/);
  assert.doesNotMatch(play, /target=["']_blank/);
});
