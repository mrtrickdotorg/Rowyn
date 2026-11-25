// The per-title card must say whether Steam will run the command the page
// built, not just show the saved profile. Bundles the model with esbuild,
// like the other frontend suites.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-launch-options-'));
const bundle = join(temp, 'launch-options.mjs');
await build({
  entryPoints: ['src/services/rowynPlayLaunchOptionsModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

test('an identical command recorded by Steam reads as in sync', () => {
  const result = m.reconcileLaunchOptions({
    assembled: 'MANGOHUD=1 %command%',
    recorded: 'MANGOHUD=1  %command%',
    read: true,
    defaultProfile: false,
  });
  assert.equal(result.state, 'matches');
  assert.equal(result.label, 'In sync');
  assert.equal(result.recorded, null);
  // Spacing Steam rewrote is not drift the player caused.
  assert.equal(m.normalizeLaunchOptions('  a   b  '), 'a b');
});

test('different text is flagged as drift and Steam’s own text is shown', () => {
  const result = m.reconcileLaunchOptions({
    assembled: 'MANGOHUD=1 %command%',
    recorded: 'gamemoderun %command% -novid',
    read: true,
    defaultProfile: false,
  });
  assert.equal(result.state, 'differs');
  assert.equal(result.label, 'Drift');
  assert.equal(result.recorded, 'gamemoderun %command% -novid');
  assert.match(result.detail, /Steam will run the text it has recorded instead/);
  assert.doesNotMatch(result.detail, /is applied/);
});

test('a default profile does not claim drift it did not build', () => {
  const result = m.reconcileLaunchOptions({
    assembled: '%command%',
    recorded: 'DXVK_HUD=1 %command%',
    read: true,
    defaultProfile: true,
  });
  assert.equal(result.state, 'differs');
  assert.match(result.detail, /this profile does not include/);
});

test('no recorded options is "not applied", and an unread profile is unknown', () => {
  const none = m.reconcileLaunchOptions({
    assembled: '%command%', recorded: null, read: true, defaultProfile: true,
  });
  assert.equal(none.state, 'none');
  assert.equal(none.label, 'Not applied');
  assert.match(none.detail, /not applied yet/);
  // Steam writes an empty value for cleared options: still none recorded.
  assert.equal(
    m.reconcileLaunchOptions({ assembled: '%command%', recorded: '   ', read: true, defaultProfile: true }).state,
    'none',
  );

  const unknown = m.reconcileLaunchOptions({
    assembled: '%command%', recorded: null, read: false, defaultProfile: true,
  });
  assert.equal(unknown.state, 'unknown');
  assert.match(unknown.detail, /not the same as not applied/);
  // An unread profile never turns a saved profile into an "in sync" claim.
  assert.equal(
    m.reconcileLaunchOptions({ assembled: '%command%', recorded: '%command%', read: false, defaultProfile: true }).state,
    'unknown',
  );
});
