import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { chmod, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { after, test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

/**
 * The quarantine clear, end to end, without an image and without a machine:
 * the image's real helper rewrites a record on disk, and the record it leaves
 * behind is what the Hub's own parser and panel read cycle display.
 *
 * Only two things are stood in for. The helper's root gate is neutralized in a
 * copy, exactly as build/tests/test_boot_health.py does, because an unprivileged
 * runner cannot pass it; RootGateTests covers the gate itself. And the bridge
 * is bypassed: the panel's load reads the file the helper wrote, which is the
 * same string rowyn_boot_health_snapshot hands it.
 */

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');

/** Walks up from the ported tests directory to the checkout that owns shared/. */
function findHelper(start) {
  for (let directory = start; ; directory = dirname(directory)) {
    const candidate = resolve(directory, 'shared/rowyn-boot-health');
    if (existsSync(candidate)) return candidate;
    if (dirname(directory) === directory) return null;
  }
}

const HELPER = findHelper(root);

const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-quarantine-e2e-'));
const outfile = resolve(directory, 'ui.cjs');
after(() => rm(directory, { recursive: true, force: true }));

// The real parser and the real read cycle, bundled; nothing here reaches Tauri.
await build({
  stdin: {
    resolveDir: root,
    loader: 'tsx',
    contents: `
      import { createBootHealthPanel } from './src/services/rowynBootHealthPanel';
      import { parseBootHealth } from './src/services/rowynBootHealthModel';
      export { createBootHealthPanel, parseBootHealth };
    `,
  },
  outfile,
  bundle: true,
  platform: 'node',
  format: 'cjs',
  logLevel: 'silent',
});
const loaded = await import(pathToFileURL(outfile).href);
const ui = loaded.default ?? loaded;

const GOOD = 'sha256:' + 'a'.repeat(64);
const BAD = 'sha256:' + 'b'.repeat(64);

// One known-good deployment, and one that failed its boot checks twice, was
// quarantined, and was rolled back once: every entry the clear must prune.
const FIXTURE = {
  schema_version: 1,
  known_good: { [GOOD]: { recorded_at: '2026-11-01T00:00:00Z', version: '0.2.0' } },
  failures: { [BAD]: 2 },
  quarantined: {
    [BAD]: { reason: 'unhealthy-boot', quarantined_at: '2026-11-02T09:00:00Z' },
  },
  rollback: {
    [BAD]: {
      attempts: 1,
      last_error: 'bootc rollback exited 1',
      attempted_at: '2026-11-02T09:00:01Z',
    },
  },
  last_boot: {
    recorded_at: '2026-11-02T09:05:00Z',
    digest: GOOD,
    healthy: true,
  },
  updated_at: '2026-11-02T09:00:01Z',
};

/** A fresh state directory holding the fixture and a copy of the real helper. */
async function sandbox() {
  const state = await mkdtemp(resolve(directory, 'state-'));
  const helper = resolve(state, 'rowyn-boot-health');
  const text = await readFile(HELPER, 'utf8');
  assert.match(text, /os\.geteuid\(\) != 0/,
    'the helper must still gate on euid for this copy to be what runs');
  await writeFile(helper, text.replace('os.geteuid() != 0', 'False'), 'utf8');
  await chmod(helper, 0o755);
  const record = resolve(state, 'boot-health.json');
  await writeFile(record, JSON.stringify(FIXTURE, null, 2) + '\n', 'utf8');
  return { state, helper, record };
}

/** Runs the helper the way the installed one runs, with the digest on stdin. */
function runHelper(helper, digits, { stdin = '' } = {}) {
  return new Promise((settle, fail) => {
    const child = spawn(process.env.ROWYN_TEST_PYTHON || 'python3', [helper, ...digits], {
      // Only the state directory matters for a clear; nothing here boots.
      env: { ...process.env, ROWYN_BOOT_HEALTH_DIR: dirname(helper) },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    child.on('error', fail);
    child.on('close', (code) => settle({ code, stdout, stderr }));
    child.stdin.end(stdin);
  });
}

/** The panel exactly as the component builds it, loading the file on disk. */
function panelOver(record) {
  const views = [];
  const panel = ui.createBootHealthPanel(
    async () => ui.parseBootHealth(await readFile(record, 'utf8')),
    (view) => views.push(view),
  );
  return { panel, views };
}

async function until(predicate, label) {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    if (predicate()) return;
    await new Promise((settle) => setTimeout(settle, 1));
  }
  assert.fail(`timed out waiting for ${label}`);
}

const digestCount = (text, digest) => text.split(digest).length - 1;

test('the helper clears the digest from the record and the panel then shows it gone', async (t) => {
  if (!HELPER) return t.skip('run the port inside the checkout to reach shared/rowyn-boot-health');
  const { helper, record } = await sandbox();
  const { panel } = panelOver(record);

  // The fixture is what the panel shows before anyone clears anything.
  await panel.read();
  const before = panel.view();
  assert.equal(before.error, '');
  assert.equal(before.busy, false);
  assert.equal(before.snapshot.state, 'attention');
  assert.equal(before.snapshot.label, '1 deployment(s) quarantined');
  assert.deepEqual(before.snapshot.quarantined.map((entry) => entry.digest), [BAD]);
  assert.equal(before.snapshot.quarantined[0].reasonLabel, 'Failed its boot checks');
  assert.deepEqual(before.snapshot.failures, [{ digest: BAD, count: 2 }]);
  assert.equal(before.snapshot.rollbacks[0].digest, BAD);
  assert.equal(before.snapshot.rollbacks[0].attempts, 1);

  // The literal argv the sudoers grant allows, with the digest on stdin.
  const cleared = await runHelper(helper, ['clear-quarantine', '-'], { stdin: BAD + '\n' });
  assert.equal(cleared.code, 0, cleared.stdout + cleared.stderr);
  assert.match(cleared.stdout, new RegExp(`cleared quarantine for ${BAD}`));

  // On disk: the digest is gone from every map, and from the raw bytes.
  const raw = await readFile(record, 'utf8');
  const saved = JSON.parse(raw);
  assert.deepEqual(saved.quarantined, {});
  assert.deepEqual(saved.failures, {});
  assert.deepEqual(saved.rollback, {});
  assert.ok(saved.known_good[GOOD], 'the known-good deployment is kept');
  assert.equal(saved.last_boot.digest, GOOD, 'the last-boot line is not part of the clear');
  assert.notEqual(saved.updated_at, FIXTURE.updated_at, 'a clear writes the record');
  assert.equal(digestCount(raw, BAD), 0, 'the cleared digest must not survive anywhere in the record');

  // The re-read a successful clear asks for, over the file the helper wrote.
  await panel.onCleared();
  await until(() => !panel.view().busy, 'the re-read to land');
  const after = panel.view();
  assert.equal(after.error, '');
  assert.equal(after.snapshot.state, 'available');
  assert.equal(after.snapshot.label, '1 verified deployment(s)');
  assert.deepEqual(after.snapshot.quarantined, []);
  assert.deepEqual(after.snapshot.failures, []);
  assert.deepEqual(after.snapshot.rollbacks, []);
  assert.equal(after.snapshot.knownGood, 1);
});

test('a second clear is refused and leaves the record the panel just read', async (t) => {
  if (!HELPER) return t.skip('run the port inside the checkout to reach shared/rowyn-boot-health');
  const { helper, record } = await sandbox();
  const first = await runHelper(helper, ['clear-quarantine', '-'], { stdin: BAD + '\n' });
  assert.equal(first.code, 0, first.stdout + first.stderr);
  const settled = await readFile(record, 'utf8');

  const { panel } = panelOver(record);
  await panel.read();
  assert.equal(panel.view().snapshot.state, 'available');

  const again = await runHelper(helper, ['clear-quarantine', '-'], { stdin: BAD + '\n' });
  assert.equal(again.code, 1, again.stdout + again.stderr);
  assert.match(again.stderr, new RegExp(`${BAD} is not quarantined`));
  assert.equal(await readFile(record, 'utf8'), settled, 'a refusal must not touch the record');

  await panel.onCleared();
  await until(() => !panel.view().busy, 'the re-read to land');
  assert.equal(panel.view().snapshot.state, 'available');
  assert.deepEqual(panel.view().snapshot.quarantined, []);
});
