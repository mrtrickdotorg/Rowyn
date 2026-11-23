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

test('the summary is three player lines in a fixed order', () => {
  const lines = m.controllerSummary(HEALTHY);
  assert.deepEqual(lines.map((line) => line.key), ['pad', 'driver', 'uinput']);
  assert.deepEqual(lines.map((line) => line.state), ['ok', 'ok', 'ok']);
  assert.match(lines[0].detail, /DualSense Wireless Controller/);
  assert.match(lines[1].detail, /Loaded for DualSense/);
  assert.match(lines[2].detail, /can write \/dev\/uinput/);
  assert.equal(m.controllerSummary(null).length, 0);
});

test('a missing pad or driver is a warning, not a success', () => {
  const noPad = m.controllerSummary([
    reading('Input devices', 'warn', 'no controller-like input device found (4 input devices, 0 event nodes, 0 js nodes)'),
  ]);
  assert.equal(noPad[0].state, 'warn');
  assert.match(noPad[0].detail, /No controller-like input device/);

  const badDriver = m.controllerSummary([
    reading('Input devices', 'ok', '1 controller-like device(s): Xbox One controller'),
    reading('Driver for Xbox One controller', 'fail', 'xpad is not loaded or built in'),
  ]);
  assert.equal(badDriver[1].state, 'warn');
  assert.match(badDriver[1].detail, /Not reported for Xbox One controller \(xpad is not loaded or built in\)/);
});

test('an absent driver row says why instead of guessing', () => {
  const lines = m.controllerSummary([reading('Input devices', 'ok', '1 controller-like device(s): DualSense')]);
  assert.equal(lines[1].state, 'unknown');
  assert.match(lines[1].detail, /Bluetooth pads do not appear there/);
  assert.equal(m.controllerSummary([])[0].state, 'unknown');
});

test('uinput access distinguishes missing from not writable', () => {
  const missing = m.controllerSummary([reading('uinput', 'warn', '/dev/uinput is missing')]);
  assert.equal(missing[2].state, 'warn');
  assert.match(missing[2].detail, /is missing/);

  const denied = m.controllerSummary([
    reading('uinput', 'warn', '/dev/uinput exists but your user cannot read and write it'),
  ]);
  assert.equal(denied[2].state, 'warn');
  assert.match(denied[2].detail, /cannot read and write/);
  assert.doesNotMatch(denied[2].detail, /is missing/);
});

test('advice is the curated table only, and the note claims no input test', () => {
  const hints = [{ family: 'playstation', label: 'PlayStation pads', hint: 'Curated advice.' }];
  assert.deepEqual(m.controllerAdvice({ findings: HEALTHY, hints }), hints);
  assert.deepEqual(m.controllerAdvice(null), []);
  assert.match(m.CONTROLLER_READ_NOTE, /No button, stick, trigger or rumble test runs/);
  assert.doesNotMatch(m.CONTROLLER_READ_NOTE, /input (test|testing) (passed|works)/i);
});
