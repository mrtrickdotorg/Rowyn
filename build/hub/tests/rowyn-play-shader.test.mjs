// A6: Shader Pre-Caching is Steam's own setting, read from its global
// config.vdf. The model must word what Steam recorded, keep "not recorded"
// distinct from on and off, and make no measurement claim.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-shader-'));
const bundle = join(temp, 'shader.mjs');
await build({
  entryPoints: ['src/services/rowynPlayShaderModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

test('the flag is reported as Steam wrote it, never guessed', () => {
  const base = { steam_detected: true, config_read: true, pre_caching: true, warnings: 0 };
  assert.match(m.shaderPreCachingLabel(base), /^Shader Pre-Caching: on/);
  assert.match(m.shaderPreCachingLabel({ ...base, pre_caching: false }), /^Shader Pre-Caching: off/);
  // An absent flag is not on and not off: Steam's default is named instead.
  const unset = m.shaderPreCachingLabel({ ...base, pre_caching: null });
  assert.match(unset, /not recorded/);
  assert.match(unset, /default \(on\) applies/);
  assert.match(m.shaderPreCachingLabel({ ...base, steam_detected: false }), /not detected/);
  assert.match(m.shaderPreCachingLabel({ ...base, config_read: false }), /not read/);
  assert.match(m.shaderPreCachingLabel(null), /reading Steam/);
  // Skipped configuration stays visible rather than reading as a clean result.
  assert.match(m.shaderPreCachingLabel({ ...base, warnings: 2 }), /2 configuration item/);
});

test('the guidance is one line, actionable, and makes no measurement claim', () => {
  assert.equal(m.SHADER_PRE_CACHING_TIP.split('\n').length, 1);
  assert.match(m.SHADER_PRE_CACHING_TIP, /Settings → Downloads/);
  assert.match(m.SHADER_PRE_CACHING_TIP, /stutter/);
  assert.doesNotMatch(m.SHADER_PRE_CACHING_TIP, /\bfps\b|frame rate|latency/i);
});

test('the model formats, it does not read or measure', () => {
  for (const name of Object.keys(m)) {
    assert.ok(['function', 'string'].includes(typeof m[name]), name);
  }
});
