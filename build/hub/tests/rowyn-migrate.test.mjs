import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-migrate-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynMigrateModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

test('the offered categories are exactly the fixed backend set', () => {
  assert.deepEqual(m.MIGRATE_CATEGORIES.map((category) => category.key),
    ['documents', 'pictures', 'desktop', 'downloads', 'bookmarks', 'wallpaper']);
});

test('category summaries report copied, skipped and failed honestly', () => {
  assert.equal(
    m.categorySummary({ category: 'documents', copied_files: 3, copied_bytes: 2048, skipped: 1, failed: 0, reasons: [] }),
    'Documents: 3 file(s) copied (2.0 KB), 1 skipped');
  assert.equal(
    m.categorySummary({ category: 'wallpaper', copied_files: 0, copied_bytes: 0, skipped: 1, failed: 2, reasons: [] }),
    'Wallpaper: 0 file(s) copied (0 B), 1 skipped, 2 failed');
  assert.equal(m.formatBytes(20 * 1024 * 1024), '20.0 MB');
});
