// B8: mount the real Play page without the native window. A small React hook
// runtime, a stub for the Tauri invoke bridge and the page's own models are
// bundled together; this covers the on-open reads, the setup strip, the Steam
// scan, search/filter and the cover budget through the rendered tree.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const REACT_RUNTIME = `
let current = null;
export const Fragment = Symbol('rowyn.fragment');
export function useState(initial) {
  const runtime = current;
  const index = runtime.index++;
  if (!(index in runtime.hooks)) {
    runtime.hooks[index] = typeof initial === 'function' ? initial() : initial;
  }
  return [runtime.hooks[index], (next) => {
    runtime.hooks[index] = typeof next === 'function' ? next(runtime.hooks[index]) : next;
    runtime.dirty = true;
  }];
}
export function useRef(initial) {
  const index = current.index++;
  if (!(index in current.hooks)) current.hooks[index] = { current: initial };
  return current.hooks[index];
}
export function useEffect(effect, deps) {
  const index = current.index++;
  const previous = current.effects[index];
  const changed = !previous || !deps || !previous.deps || previous.deps.length !== deps.length
    || deps.some((value, key) => !Object.is(value, previous.deps[key]));
  if (changed) current.pending.push({ index, effect, deps });
}
export function createMount(component) {
  const runtime = { index: 0, hooks: [], effects: [], pending: [], dirty: false };
  let tree = null;
  function render() {
    runtime.index = 0;
    const previous = current;
    current = runtime;
    try {
      tree = component();
    } finally {
      current = previous;
    }
    for (const item of runtime.pending.splice(0)) {
      const prior = runtime.effects[item.index];
      if (prior?.cleanup) prior.cleanup();
      const cleanup = item.effect();
      runtime.effects[item.index] = {
        deps: item.deps,
        cleanup: typeof cleanup === 'function' ? cleanup : null,
      };
    }
  }
  async function settle(limit = 300) {
    for (let step = 0; step < limit; step++) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      if (runtime.dirty) {
        runtime.dirty = false;
        render();
        continue;
      }
      if (runtime.pending.length) {
        render();
        continue;
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
      if (!runtime.dirty && !runtime.pending.length) return tree;
    }
    throw new Error('Play page did not settle');
  }
  render();
  return {
    get tree() { return tree; },
    settle,
    unmount() { for (const entry of runtime.effects) entry?.cleanup?.(); },
  };
}
`;

const JSX_RUNTIME = `
export const Fragment = Symbol('rowyn.fragment');
export function jsx(type, props) { return { type, props: props ?? {} }; }
export const jsxs = jsx;
export const jsxDEV = jsx;
`;

const VIRTUAL = {
  'rowyn:react': REACT_RUNTIME,
  'react': REACT_RUNTIME,
  'react/jsx-runtime': JSX_RUNTIME,
  'react/jsx-dev-runtime': JSX_RUNTIME,
  '@tauri-apps/api/core': `
export function invoke(command, args) {
  return globalThis.__ROWYN_INVOKE__(command, args);
}
`,
  'rowyn:liveData': `
export async function fetchInstalledFlatpaks() { return globalThis.__ROWYN_LAUNCHERS__; }
export async function installFlatpak() { throw new Error('install is not stubbed'); }
export async function uninstallFlatpak() { throw new Error('remove is not stubbed'); }
export async function waitInstallJob() { throw new Error('wait is not stubbed'); }
export function cancelInstall() { throw new Error('cancel is not stubbed'); }
export function invalidateSharedReads() {}
export function getInFlightJob() { return null; }
`,
  'rowyn:rowynSettings': `
export async function openRowynSettings() { return 'opened'; }
`,
  'rowyn:useSectionFollow': 'export function useSectionFollow() {}',
  'rowyn:ExternalLink': `
import { jsx } from 'react/jsx-runtime';
export function ExternalLink({ href, children }) { return jsx('a', { href, children }); }
`,
};

const temp = mkdtempSync(join(tmpdir(), 'rowyn-play-mount-'));
const bundle = join(temp, 'play.mjs');
await build({
  entryPoints: ['tests/play-mount-entry.mjs'],
  outfile: bundle,
  bundle: true,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  loader: { '.css': 'empty' },
  plugins: [{
    name: 'play-mount-stubs',
    setup(buildApi) {
      const aliases = [
        [/^react$/, 'rowyn:react'],
        [/^react\/jsx-runtime$/, 'react/jsx-runtime'],
        [/^react\/jsx-dev-runtime$/, 'react/jsx-dev-runtime'],
        [/^@tauri-apps\/api\/core$/, '@tauri-apps/api/core'],
        [/\/liveData$/, 'rowyn:liveData'],
        [/\/rowynSettings$/, 'rowyn:rowynSettings'],
        [/\/useSectionFollow$/, 'rowyn:useSectionFollow'],
        [/\/ExternalLink$/, 'rowyn:ExternalLink'],
      ];
      buildApi.onResolve({ filter: /.*/ }, (args) => {
        if (args.path === 'react') return { path: 'rowyn:react', namespace: 'rowyn-virtual' };
        if (args.path.startsWith('rowyn:') || args.path === 'react/jsx-runtime'
          || args.path === 'react/jsx-dev-runtime' || args.path === '@tauri-apps/api/core') {
          return { path: args.path, namespace: 'rowyn-virtual' };
        }
        for (const [pattern, replacement] of aliases) {
          if (pattern.test(args.path)) return { path: replacement, namespace: 'rowyn-virtual' };
        }
        return null;
      });
      buildApi.onLoad({ filter: /.*/, namespace: 'rowyn-virtual' }, (args) => {
        const contents = VIRTUAL[args.path];
        return contents ? { contents, loader: 'js', resolveDir: process.cwd() } : null;
      });
    },
  }],
});

globalThis.window = {
  __TAURI_INTERNALS__: {},
  location: { hash: '', search: '' },
  addEventListener() {},
  removeEventListener() {},
};
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => { storage.set(key, String(value)); },
  removeItem: (key) => { storage.delete(key); },
};
const m = await import(pathToFileURL(bundle).href);
test.after(() => {
  rmSync(temp, { recursive: true, force: true });
  delete globalThis.window;
  delete globalThis.localStorage;
  delete globalThis.__ROWYN_INVOKE__;
  delete globalThis.__ROWYN_LAUNCHERS__;
});

const LAUNCHERS = {
  apps: [
    { id: 'com.valvesoftware.Steam', name: 'Steam' },
    { id: 'com.heroicgameslauncher.hgl', name: 'Heroic' },
    { id: 'net.lutris.Lutris', name: 'Lutris' },
    { id: 'com.usebottles.bottles', name: 'Bottles' },
  ],
  failedScopes: [],
};

const games = [
  { app_id: '1245620', name: 'Elden Ring', cover: null },
  { app_id: '730', name: 'Counter-Strike 2', cover: null },
  { app_id: '999999', name: 'Unlisted Demo', cover: null },
];

function bridge(overrides = {}) {
  const base = {
    rowyn_gamescope_status: { available: true, detail: 'gamescope is available to this session.' },
    rowyn_gamemode_status: {
      daemon_installed: true, daemon_active: false, daemon_scope: 'user',
      sandbox_client: false, available: false, detail: 'Neither this Steam Flatpak nor the runtime it uses ships gamemoderun.',
    },
    rowyn_steam_play_status: {
      steam_present: true, steam_running: false, mapping_present: true,
      detail: 'Steam Play is enabled for all titles.',
    },
    rowyn_vulkan_layers_scan: {
      steam_runtime_branch: '24.08',
      mangohud_branches: ['24.08'], gamescope_branches: ['24.08'], vkbasalt_branches: ['24.08'],
      detail: 'Vulkan layers read for runtime 24.08.',
    },
    rowyn_display_modes_scan: {
      outputs: [{ name: 'DP-1', connected: true, hdr: 'supported', vrr: 'supported' }],
      detail: '1 output read.',
    },
    rowyn_controller_scan: {
      findings: [{ name: 'Input devices', state: 'ok', detail: 'one pad' }],
      hints: [],
    },
    rowyn_steam_recent_play: {
      steam_detected: true, profile_read: true, warnings: 0, unnamed: 0, games: [],
    },
    rowyn_steam_shader_cache: {
      steam_detected: true, config_read: true, pre_caching: true, warnings: 0,
    },
    rowyn_mangohud_config: {
      steam_detected: true, flatpak_steam: true,
      path: '.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf',
      exists: true, readable: true, settings: 3,
      native_path: '~/.config/MangoHud/MangoHud.conf',
      native_exists: false, native_readable: false, native_settings: 0, warnings: 0,
    },
    rowyn_steam_games_scan: { available: true, warnings: 0, games },
  };
  return { ...base, ...overrides };
}

function mountWith(overrides = {}) {
  const calls = [];
  globalThis.__ROWYN_LAUNCHERS__ = LAUNCHERS;
  const answers = bridge();
  for (const [command, answer] of Object.entries(overrides)) {
    if (answer === null) delete answers[command];
    else answers[command] = answer;
  }
  globalThis.__ROWYN_INVOKE__ = (command, args) => {
    calls.push({ command, args });
    if (command in answers) return Promise.resolve(structuredClone(answers[command]));
    return Promise.reject(new Error(`unstubbed bridge call: ${command}`));
  };
  return { mount: m.mountPlay(), calls };
}

function visit(node, onNode) {
  if (node === null || node === undefined || typeof node === 'boolean') return;
  if (Array.isArray(node)) {
    for (const child of node) visit(child, onNode);
    return;
  }
  if (typeof node !== 'object') return;
  if (typeof node.type === 'function') {
    visit(node.type(node.props ?? {}), onNode);
    return;
  }
  onNode(node);
  visit(node.props?.children, onNode);
}

function textOf(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  if (typeof node !== 'object') return '';
  if (typeof node.type === 'function') return textOf(node.type(node.props ?? {}));
  return textOf(node.props?.children);
}

function all(node, predicate) {
  const found = [];
  visit(node, (item) => { if (predicate(item)) found.push(item); });
  return found;
}

function byText(tree, needle, tag) {
  return all(tree, (item) => (!tag || item.type === tag) && textOf(item).includes(needle))[0];
}

test('the page mounts with stubbed reads and renders the setup strip', async () => {
  const { mount, calls } = mountWith();
  await mount.settle();
  const text = textOf(mount.tree);
  for (const needle of ['Steam installed', 'Steam Play enabled', 'Overlays installed',
    'Shader pre-caching on', 'Recent play read', 'MangoHud settings read', 'Controller detected']) {
    assert.ok(text.includes(needle), needle);
  }
  // The on-open pass is the page's own reads, each exactly once; the game scan
  // stays explicit.
  for (const command of ['rowyn_steam_play_status', 'rowyn_vulkan_layers_scan',
    'rowyn_display_modes_scan', 'rowyn_controller_scan', 'rowyn_steam_recent_play',
    'rowyn_steam_shader_cache', 'rowyn_mangohud_config']) {
    assert.equal(calls.filter((call) => call.command === command).length, 1, command);
  }
  assert.equal(calls.filter((call) => call.command === 'rowyn_steam_games_scan').length, 0);
  assert.equal(all(mount.tree, (node) => node.type === 'img').length, 0);
  mount.unmount();
});

test('scanning renders the library, and search and state filter narrow it', async () => {
  const { mount, calls } = mountWith();
  await mount.settle();
  const scan = byText(mount.tree, 'Scan Steam games', 'button');
  assert.ok(scan, 'the explicit scan button is rendered');
  await scan.props.onClick();
  await mount.settle();

  let text = textOf(mount.tree);
  assert.match(text, /3 installed title\(s\): 2 carry a curated note/);
  for (const title of ['Elden Ring', 'Counter-Strike 2', 'Unlisted Demo']) assert.ok(text.includes(title), title);
  assert.match(text, /\bNative\b/);
  assert.match(text, /\bProton\b/);
  // Unlisted Demo carries no curated note and gets no chip.
  assert.doesNotMatch(text, /Curated status: unreviewed/);
  assert.equal(calls.filter((call) => call.command === 'rowyn_steam_games_scan').length, 1);

  const search = all(mount.tree, (node) => node.type === 'input'
    && node.props.placeholder === 'Title or Steam App ID')[0];
  assert.ok(search);
  search.props.onChange({ target: { value: 'elden' } });
  await mount.settle();
  text = textOf(mount.tree);
  assert.ok(text.includes('Elden Ring'));
  assert.ok(!text.includes('Counter-Strike 2'));
  assert.match(text, /Showing 1 under this search and filter/);

  search.props.onChange({ target: { value: '' } });
  await mount.settle();
  const filter = all(mount.tree, (node) => node.type === 'select')[0];
  assert.ok(filter);
  filter.props.onChange({ target: { value: 'proton' } });
  await mount.settle();
  text = textOf(mount.tree);
  assert.ok(text.includes('Elden Ring'));
  assert.ok(!text.includes('Counter-Strike 2'));
  assert.ok(!text.includes('Unlisted Demo'));

  filter.props.onChange({ target: { value: 'unreviewed' } });
  await mount.settle();
  text = textOf(mount.tree);
  assert.ok(text.includes('Unlisted Demo'));
  assert.ok(!text.includes('Counter-Strike 2'));
  mount.unmount();
});

test('a large scan renders only the cover budget and says so', async () => {
  const many = Array.from({ length: 65 }, (_, index) => ({
    app_id: String(1000 + index),
    name: `Game ${index + 1}`,
    cover: 'data:image/jpeg;base64,AAAA',
  }));
  const { mount } = mountWith({ rowyn_steam_games_scan: { available: true, warnings: 0, games: many } });
  await mount.settle();
  await byText(mount.tree, 'Scan Steam games', 'button').props.onClick();
  await mount.settle();
  assert.equal(all(mount.tree, (node) => node.type === 'img').length, 60);
  const text = textOf(mount.tree);
  assert.match(text, /65 installed title\(s\)/);
  assert.match(text, /5 more cover\(s\) stay a letter tile/);
  assert.match(text, /60-cover budget/);
  mount.unmount();
});

test('a failed read renders an unknown step instead of claiming readiness', async () => {
  const { mount } = mountWith({ rowyn_mangohud_config: null });
  // A null override removes the answer, so the invoke stub rejects it.
  await mount.settle();
  const text = textOf(mount.tree);
  assert.ok(text.includes('Check MangoHud settings'));
  assert.match(text, /could not run/);
  assert.doesNotMatch(text, /\d+ setting\(s\) in/);
  mount.unmount();
});

test('per-game GameMode is offered only where the sandbox client can reach the game', async () => {
  const mixed = [
    { app_id: '1245620', name: 'Elden Ring', cover: null, container: true },
    { app_id: '620', name: 'Portal 2', cover: null, container: false },
  ];
  const { mount } = mountWith({
    rowyn_gamemode_status: {
      daemon_installed: true, daemon_active: false, daemon_scope: 'user',
      sandbox_client: true, available: true,
      detail: 'GameMode activates with this option.',
    },
    rowyn_steam_games_scan: { available: true, warnings: 0, games: mixed },
  });
  await mount.settle();
  const scan = byText(mount.tree, 'Scan Steam games', 'button');
  await scan.props.onClick();
  await mount.settle();
  const text = textOf(mount.tree);
  // The native title gets the checkbox; the Proton title gets the reason.
  assert.match(text, /GameMode \(gamemoderun %command%\): the client ships in Steam's runtime/);
  assert.match(text, /in-game activation has not been tested on Rowyn yet/);
  assert.match(text, /No per-game GameMode here: Steam starts this title through Proton/);
  assert.match(text, /cannot load the client at all/);
  mount.unmount();
});
