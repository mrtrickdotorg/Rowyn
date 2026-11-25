// B5: the controller section opens with three player lines (pad, driver,
// /dev/uinput access) and only curated per-family advice. These tests pin the
// wording that matters: nothing claims input was tested, and unknown readings
// stay unknown rather than turning into a confident "works".

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const temp = mkdtempSync(join(tmpdir(), 'rowyn-controller-'));
const bundle = join(temp, 'controller.mjs');
await build({
  entryPoints: ['src/services/rowynControllerModel.ts'],
  outfile: bundle, bundle: true, format: 'esm', platform: 'node',
});
const m = await import(pathToFileURL(bundle).href);
test.after(() => rmSync(temp, { recursive: true, force: true }));

const reading = (name, state, detail) => ({ name, state, detail });

const HEALTHY = [
  reading('Input devices', 'ok', '1 controller-like device(s): DualSense Wireless Controller'),
  reading('USB controllers', 'ok', 'DualSense (054c:0ce6)'),
  reading('Driver for DualSense', 'ok', 'hid_playstation available'),
  reading('uinput', 'ok', '/dev/uinput is present and usable by your user'),
];

const sandbox = (overrides = {}) => ({
  checked: true,
  installed: true,
  devices: ['all'],
  input_granted: true,
  host_input_nodes: true,
  host_readable: true,
  reachable: true,
  detail: 'Flatpak Steam is granted all; /dev/input is visible inside its sandbox.',
  ...overrides,
});

const report = (findings, steamSandbox = sandbox()) => ({ findings, hints: [], steam_sandbox: steamSandbox });

test('the summary is four player lines in a fixed order', () => {
  const lines = m.controllerSummary(report(HEALTHY));
  assert.deepEqual(lines.map((line) => line.key), ['pad', 'driver', 'uinput', 'sandbox']);
  assert.deepEqual(lines.map((line) => line.state), ['ok', 'ok', 'ok', 'ok']);
  assert.match(lines[0].detail, /DualSense Wireless Controller/);
  assert.match(lines[1].detail, /Loaded for DualSense/);
  assert.match(lines[2].detail, /can write \/dev\/uinput/);
  assert.match(lines[3].detail, /visible inside its sandbox/);
  assert.equal(m.controllerSummary(null).length, 0);
});

test('a pad the launcher sandbox cannot reach is not reported as ready', () => {
  const blocked = m.controllerSummary(report(HEALTHY, sandbox({
    devices: ['dri'], input_granted: false, reachable: false,
    detail: 'Flatpak Steam is granted only dri; /dev/input is hidden inside its sandbox, so a controller cannot reach it there.',
  })));
  // The pad exists, but the card must not call it ready for the launcher.
  assert.equal(blocked[0].state, 'warn');
  assert.match(blocked[0].detail, /Flatpak Steam cannot reach it/);
  assert.equal(blocked[3].state, 'warn');
  assert.match(blocked[3].detail, /dev\/input is hidden/);
});

test('a permission read that did not run stays unknown, never granted', () => {
  const unchecked = m.controllerSummary(report(HEALTHY, sandbox({ checked: false, reachable: false })));
  assert.equal(unchecked[3].state, 'unknown');
  assert.match(unchecked[3].detail, /could not be read/);
  // Unknown is not a warning about the pad itself.
  assert.equal(unchecked[0].state, 'ok');

  const absent = m.controllerSummary(report(HEALTHY, sandbox({ installed: false, reachable: false })));
  assert.equal(absent[3].state, 'ok');
  assert.match(absent[3].detail, /not installed/);

  const missing = m.controllerSummary({ findings: HEALTHY, hints: [] });
  assert.equal(missing[3].state, 'unknown');
  assert.match(missing[3].detail, /did not report/);
});

test('a missing pad or driver is a warning, not a success', () => {
  const noPad = m.controllerSummary(report([
    reading('Input devices', 'warn', 'no controller-like input device found (4 input devices, 0 event nodes, 0 js nodes)'),
  ]));
  assert.equal(noPad[0].state, 'warn');
  assert.match(noPad[0].detail, /No controller-like input device/);

  const badDriver = m.controllerSummary(report([
    reading('Input devices', 'ok', '1 controller-like device(s): Xbox One controller'),
    reading('Driver for Xbox One controller', 'fail', 'xpad is not loaded or built in'),
  ]));
  assert.equal(badDriver[1].state, 'warn');
  assert.match(badDriver[1].detail, /Not reported for Xbox One controller \(xpad is not loaded or built in\)/);
});

test('an absent driver row says why instead of guessing', () => {
  const lines = m.controllerSummary(report([reading('Input devices', 'ok', '1 controller-like device(s): DualSense')]));
  assert.equal(lines[1].state, 'unknown');
  assert.match(lines[1].detail, /Bluetooth pads do not appear there/);
  assert.equal(m.controllerSummary(report([]))[0].state, 'unknown');
});

test('uinput access distinguishes missing from not writable', () => {
  const missing = m.controllerSummary(report([reading('uinput', 'warn', '/dev/uinput is missing')]));
  assert.equal(missing[2].state, 'warn');
  assert.match(missing[2].detail, /is missing/);

  const denied = m.controllerSummary(report([
    reading('uinput', 'warn', '/dev/uinput exists but your user cannot read and write it'),
  ]));
  assert.equal(denied[2].state, 'warn');
  assert.match(denied[2].detail, /cannot read and write/);
  assert.doesNotMatch(denied[2].detail, /is missing/);
});

test('advice is the curated table only, and the note claims no input test', () => {
  const hints = [{ family: 'playstation', label: 'PlayStation pads', hint: 'Curated advice.' }];
  assert.deepEqual(m.controllerAdvice({ findings: HEALTHY, hints }), hints);
  assert.deepEqual(m.controllerAdvice(null), []);
  assert.match(m.CONTROLLER_READ_NOTE, /No button, stick, trigger or rumble test runs/);
  assert.match(m.CONTROLLER_READ_NOTE, /sandbox is granted the input devices/);
  assert.doesNotMatch(m.CONTROLLER_READ_NOTE, /input (test|testing) (passed|works)/i);
});
