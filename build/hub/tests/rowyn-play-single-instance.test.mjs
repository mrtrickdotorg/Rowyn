// Regression: launching Rowyn Play while the Hub is open is a
// single-instance activation. The shell forwards the second launch's
// `--page Play` to the existing window as a `navigate` event; the webview
// listener resolves it to /play. Both halves run headless here: the frontend
// against a stubbed Tauri event bridge, and the shell wiring by reading the
// generated main.rs (the plugin itself needs a live GTK/DBus session).

import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-single-instance-'));
const bundle = join(temp, 'deepLink.mjs');

await build({
  entryPoints: ['src/deepLink.ts'],
  outfile: bundle,
  bundle: true,
  format: 'esm',
  platform: 'node',
  plugins: [{
    name: 'deeplink-stubs',
    setup(buildApi) {
      const virtual = {
        '@tauri-apps/api/core': `
export async function invoke(command, args) {
  globalThis.__DEEPLINK_INVOKES__.push({ command, args });
  return globalThis.__DEEPLINK_PENDING__ ?? null;
}
`,
        '@tauri-apps/api/event': `
export async function listen(event, handler) {
  globalThis.__DEEPLINK_LISTENERS__.push({ event, handler });
  return () => {};
}
`,
        'rowyn:tauriEnv': 'export function inTauriShell() { return true; }',
        'rowyn:acceptance': `
export async function recordHubAcceptance(event, detail) {
  globalThis.__DEEPLINK_ACCEPTANCE__.push({ event, detail });
}
`,
      };
      buildApi.onResolve({ filter: /.*/ }, (args) => {
        if (args.path === '@tauri-apps/api/core' || args.path === '@tauri-apps/api/event') {
          return { path: args.path, namespace: 'rowyn-virtual' };
        }
        if (/\/tauriEnv$/.test(args.path)) return { path: 'rowyn:tauriEnv', namespace: 'rowyn-virtual' };
        if (/\/services\/acceptance$/.test(args.path)) return { path: 'rowyn:acceptance', namespace: 'rowyn-virtual' };
        return null;
      });
      buildApi.onLoad({ filter: /.*/, namespace: 'rowyn-virtual' }, (args) => {
        const contents = virtual[args.path];
        return contents ? { contents, loader: 'js' } : null;
      });
    },
  }],
});

globalThis.window = { location: { hash: '' }, open: () => { throw new Error('activation must not open a window'); } };
const m = await import(pathToFileURL(bundle).href);
test.after(() => {
  rmSync(temp, { recursive: true, force: true });
  delete globalThis.window;
  delete globalThis.__DEEPLINK_INVOKES__;
  delete globalThis.__DEEPLINK_LISTENERS__;
  delete globalThis.__DEEPLINK_ACCEPTANCE__;
  delete globalThis.__DEEPLINK_PENDING__;
});

function reset(pending = null) {
  globalThis.window.location.hash = '';
  globalThis.__DEEPLINK_INVOKES__ = [];
  globalThis.__DEEPLINK_LISTENERS__ = [];
  globalThis.__DEEPLINK_ACCEPTANCE__ = [];
  globalThis.__DEEPLINK_PENDING__ = pending;
}

test('a second-instance Play activation navigates the open window to /play', async () => {
  reset();
  await m.installDeepLinkHandling();
  assert.equal(m.routeForPage('Play'), '/play');
  const listener = globalThis.__DEEPLINK_LISTENERS__.find((entry) => entry.event === 'navigate');
  assert.ok(listener, 'the webview listens for navigate');
  // Registration must happen before the initial-page pull, or a fast second
  // launch in the gap would be lost.
  assert.deepEqual(globalThis.__DEEPLINK_INVOKES__.map((call) => call.command), ['take_pending_page']);

  listener.handler({ payload: 'Play' });
  assert.equal(globalThis.window.location.hash, '/play');
  const last = globalThis.__DEEPLINK_ACCEPTANCE__.at(-1);
  assert.equal(last.event, 'deep-link');
  assert.deepEqual(JSON.parse(last.detail), { page: 'Play', route: '/play', source: 'single-instance' });
});

test('an initial --page still resolves once, through the pending-page pull', async () => {
  reset('Work');
  await m.installDeepLinkHandling();
  assert.equal(globalThis.window.location.hash, '/work');
  assert.equal(globalThis.__DEEPLINK_ACCEPTANCE__.length, 1);
  assert.deepEqual(JSON.parse(globalThis.__DEEPLINK_ACCEPTANCE__[0].detail),
    { page: 'Work', route: '/work', source: 'initial' });
});

test('the shell forwards a second launch instead of constructing a window', async () => {
  const main = readFileSync('src-tauri/src/main.rs', 'utf8');
  assert.match(main, /\.plugin\(tauri_plugin_single_instance::init/);
  const block = main.split('.plugin(tauri_plugin_single_instance::init', 2)[1]
    .split('.manage(PendingPage', 1)[0];
  assert.match(block, /extract_page_arg\(&argv\)/);
  assert.match(block, /get_webview_window\("main"\)/);
  assert.match(block, /window\.emit\("navigate", page\)/);
  assert.doesNotMatch(block, /WebviewWindowBuilder|build\(\)\?/,
    'the single-instance callback must focus the existing window, not build another');
});
