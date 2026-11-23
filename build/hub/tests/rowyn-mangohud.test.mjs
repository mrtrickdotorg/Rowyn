// A7: Flathub Steam reads MangoHud's config inside its sandbox. The model has
// to name that path, keep a missing config distinct from an empty one, flag a
// host-path config Flathub Steam ignores, and make no write or measurement
// claim. Rowyn offers the starting config as text only.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-mangohud-'));
const bundle = join(temp, 'mangohud.mjs');
await build({
  entryPoints: ['src/services/rowynPlayMangohudModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const base = {
  steam_detected: true,
  flatpak_steam: true,
  path: '.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf',
  exists: true,
  readable: true,
  settings: 3,
  native_path: '.config/MangoHud/MangoHud.conf',
  native_exists: false,
  warnings: 0,
};

test('the sandbox path is the one named, home-relative', () => {
  assert.equal(
    m.mangohudConfigPath(base),
    '~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf',
  );
  // No absolute home directory is ever printed.
  assert.doesNotMatch(m.mangohudConfigLabel(base), /\/(home|users)\//);
  assert.match(m.mangohudConfigLabel(base), /3 setting\(s\)/);
});

test('missing, unreadable and unread stay distinct', () => {
  assert.match(m.mangohudConfigLabel(null), /not read yet/);
  assert.match(m.mangohudConfigLabel({ ...base, steam_detected: false }), /not detected/);

  const unreadable = m.mangohudConfigLabel({ ...base, readable: false });
  assert.match(unreadable, /exists but could not be read/);
  assert.doesNotMatch(unreadable, /\b0 setting/);

  const absent = m.mangohudConfigLabel({ ...base, exists: false, readable: false, settings: 0 });
  assert.match(absent, /no config yet/);
  assert.match(absent, /defaults/);
  assert.match(absent, /MangoHud\.conf/);

  // A host-path config is named as ignored rather than counted as present.
  const hostOnly = m.mangohudConfigLabel({ ...base, exists: false, readable: false, settings: 0, native_exists: true });
  assert.match(hostOnly, /ignored by Flathub Steam/);
  assert.match(hostOnly, /~\/\.config\/MangoHud\/MangoHud\.conf/);

  // Skipped reads stay visible.
  assert.match(m.mangohudConfigLabel({ ...base, warnings: 2 }), /2 item\(s\)/);
  assert.match(
    m.mangohudConfigLabel({ ...base, exists: false, warnings: 1 }),
    /1 item\(s\)/,
  );
});

test('the recommended config is text only and the note promises no writes', () => {
  assert.match(m.MANGOHUD_READ_NOTE, /never creates, edits or backs up/);
  assert.match(m.MANGOHUD_READ_NOTE, /MANGOHUD=1/);
  assert.doesNotMatch(m.MANGOHUD_READ_NOTE, /will write|we write|Rowyn creates/i);
  assert.match(m.MANGOHUD_RECOMMENDED, /^#/);
  assert.match(m.MANGOHUD_RECOMMENDED, /gpu_stats/);
  assert.match(m.MANGOHUD_RECOMMENDED, /toggle_hud=/);
  // Advice, not a measurement claim.
  assert.doesNotMatch(m.MANGOHUD_RECOMMENDED, /\bfps (is|was|measured)\b|\d+ ?fps/i);
});

test('the model formats, it does not read or write', () => {
  for (const name of Object.keys(m)) {
    assert.ok(['function', 'string'].includes(typeof m[name]), name);
  }
});
