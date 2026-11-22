import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-boot-health-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynBootHealthModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

const GOOD = `sha256:${'a'.repeat(64)}`;
const BAD = `sha256:${'b'.repeat(64)}`;

const record = (overrides = {}) => JSON.stringify({
  schema_version: 1,
  known_good: {},
  failures: {},
  quarantined: {},
  rollback: {},
  last_boot: {},
  ...overrides,
});

test('a known-good record reports verified deployments, not health claims', () => {
  const view = m.parseBootHealth(record({
    known_good: { [GOOD]: { recorded_at: '2026-11-01T00:00:00Z', version: '0.2.0' } },
    last_boot: { recorded_at: '2026-11-01T00:00:00Z', digest: GOOD, healthy: true },
  }));
  assert.equal(view.state, 'available');
  assert.equal(view.knownGood, 1);
  assert.equal(view.lastBoot.healthy, true);
  assert.match(view.guidance, /first-boot gate, not hardware certification/);
});

test('failures are counted per digest, bounded and sorted by severity', () => {
  const view = m.parseBootHealth(record({ failures: { [GOOD]: 1, [BAD]: 2 } }));
  assert.equal(view.state, 'attention');
  assert.deepEqual(view.failures, [{ digest: BAD, count: 2 }, { digest: GOOD, count: 1 }]);
  assert.match(view.guidance, /Three failed boots quarantine it/);
});

test('quarantine explains the reason and stays read-only guidance', () => {
  const view = m.parseBootHealth(record({
    quarantined: { [BAD]: { quarantined_at: '2026-11-02T10:00:00Z', reason: 'downgrade' } },
  }));
  assert.equal(view.state, 'attention');
  assert.equal(view.quarantined[0].reasonLabel, 'Older than the booted image');
  assert.equal(view.quarantined[0].at, '2026-11-02T10:00:00Z');
  assert.match(view.guidance, /clear one here, with confirmation/);
  assert.doesNotMatch(view.guidance, /terminal-only/);
});

test('an empty record is pending rather than healthy or broken', () => {
  const view = m.parseBootHealth(record());
  assert.equal(view.state, 'pending');
  assert.equal(view.knownGood, 0);
  assert.match(view.label, /Not judged yet/);
});

test('unusable records degrade to unavailable instead of looking healthy', () => {
  for (const raw of [null, '', '   ', '{', '[]', record({ schema_version: 2 }), JSON.stringify({ schema_version: 1 })]) {
    const view = m.parseBootHealth(raw);
    assert.ok(['unavailable', 'pending'].includes(view.state), String(raw));
  }
  assert.equal(m.parseBootHealth('{').state, 'unavailable');
  assert.equal(m.parseBootHealth(record({ schema_version: 2 })).state, 'unavailable');
});

test('invalid digests and unknown reasons never reach the UI as trusted data', () => {
  const view = m.parseBootHealth(record({
    known_good: { 'not-a-digest': {} },
    failures: { 'not-a-digest': 3, [GOOD]: 0 },
    quarantined: { [GOOD]: { reason: 'because' } },
    rollback: { [BAD]: { attempts: 99, last_error: 'bootc rollback exited 1' } },
  }));
  assert.equal(view.knownGood, 0);
  assert.deepEqual(view.failures, []);
  assert.equal(view.quarantined[0].reasonLabel, 'Quarantined');
  assert.equal(view.rollbacks[0].attempts, 99);
  assert.equal(view.rollbacks[0].lastError, 'bootc rollback exited 1');
});
