import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-update-status-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynUpdateStatusModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const { projectUpdateBanner } = await import(pathToFileURL(out).href);
const fixture = (status = {}, extra = {}) => ({ snapshot: null, pending: null, health: null, status: { staged: false, rollback: false, check_state: 'idle', detail: '', ...status }, ...extra });

test('unavailable and failed reads never imply healthy/current', () => {
  assert.equal(projectUpdateBanner(null).state, 'unavailable');
  assert.equal(projectUpdateBanner(null, true).state, 'failed');
  assert.notEqual(projectUpdateBanner(null).state, 'current');
});

test('staged state takes precedence over stale available state', () => {
  const projected = projectUpdateBanner(fixture({ staged: true, check_state: 'available', remote_digest: 'sha256:remote' }));
  assert.equal(projected.state, 'restartready');
  assert.match(projected.guidance, /restart/i);
});

test('zero-count pending summaries and cached digests never imply an available system update', () => {
  assert.equal(projectUpdateBanner(fixture({check_state:'current',remote_digest:'sha256:'+'a'.repeat(64)}, {pending:{bootc:'current',flatpak:'0',firmware:'0'}})).state, 'current');
  assert.equal(projectUpdateBanner(fixture({check_state:'idle'}, {pending:{bootc:'unknown',flatpak:'0',firmware:'unknown'}})).state, 'unavailable');
  assert.equal(projectUpdateBanner(fixture({check_state:'idle'}, {pending:{bootc:'staged',flatpak:'0',firmware:'0'}})).state, 'restartready');
});

test('a failed read is unavailable, not a policy block', () => {
  for (const status of [
    { check_state: 'error', blocked_reason: 'Could not read bootc status.' },
    { check_state: 'blocked', blocked_reason: 'Could not read update status.' },
    { check_state: 'failed', detail: 'bootc is not installed.' },
  ]) {
    const projected = projectUpdateBanner(fixture(status));
    assert.equal(projected.state, 'failed', JSON.stringify(status));
    assert.equal(projected.label, 'Update status unavailable');
    assert.doesNotMatch(projected.label, /blocked/i);
  }
  // A real safety hold still reads as blocked, with its own reason.
  const held = projectUpdateBanner(fixture({ check_state: 'blocked', blocked_reason: 'Safety hold' }));
  assert.equal(held.state, 'blocked');
  assert.equal(held.label, 'Update blocked');
});

test('busy, blocked, failed, available and current statuses project explicitly', () => {
  assert.equal(projectUpdateBanner(fixture({ check_state: 'busy' })).state, 'staging');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'blocked', blocked_reason: 'Safety hold' })).state, 'blocked');
  assert.match(projectUpdateBanner(fixture({ check_state: 'blocked', blocked_reason: 'Safety hold' })).guidance, /Safety hold/);
  assert.equal(projectUpdateBanner(fixture({ check_state: 'error' })).state, 'failed');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'error' })).label, 'Update status unavailable');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'available' })).state, 'available');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'uptodate' })).state, 'current');
});

test('up-to-date guidance carries the real last-check time from the backend', () => {
  const projected = projectUpdateBanner(fixture({ check_state: 'uptodate', detail: 'Last checked 2025-11-13 00:05 UTC.' }));
  assert.equal(projected.state, 'current');
  assert.equal(projected.label, 'System is up to date');
  assert.equal(projected.guidance, 'Last checked 2025-11-13 00:05 UTC.');
  // Without a backend detail the generic sentence remains.
  assert.equal(projectUpdateBanner(fixture({ check_state: 'uptodate' })).guidance, 'No system update is currently available.');
});

test('deferred and disabled checks are honest states, never current or failed', () => {
  const deferred = projectUpdateBanner(fixture({ check_state: 'deferred', detail: 'The automatic update check at 2025-11-13 00:00 UTC was deferred: this device was on battery power. The last completed check was at 2025-11-12 23:51 UTC.' }));
  assert.equal(deferred.state, 'deferred');
  assert.equal(deferred.label, 'Update check deferred');
  assert.match(deferred.guidance, /battery power/);
  const disabled = projectUpdateBanner(fixture({ check_state: 'disabled', detail: 'Automatic update checks are disabled by policy. Open Updates to check manually.' }));
  assert.equal(disabled.state, 'disabled');
  assert.equal(disabled.label, 'Automatic checks off');
  assert.match(disabled.guidance, /disabled by policy/);
  assert.notEqual(deferred.state, 'current');
  assert.notEqual(disabled.state, 'failed');
});
