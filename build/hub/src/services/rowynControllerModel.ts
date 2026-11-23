// B5: the controller section opens with three player lines — a pad is present,
// its kernel driver is reported, and this session can write /dev/uinput — and
// keeps the helper's technical rows behind a details toggle. The helper's own
// hint text is dropped in Rust; only the curated per-family table reaches here.
// Nothing in this model presses a button or claims input was tested.

import type { ControllerHint, ControllerReport } from './rowynMvp';
import type { Reading } from './rowynMvpModel';

export type ControllerLineState = 'ok' | 'warn' | 'unknown';

export interface ControllerLine {
  key: 'pad' | 'driver' | 'uinput';
  label: string;
  state: ControllerLineState;
  detail: string;
}

/** What the three lines do and do not establish. */
export const CONTROLLER_READ_NOTE =
  'These three lines report what the shipped controller check read: a controller-like device, the kernel driver for a detected pad, and whether this session can write /dev/uinput for Steam Input and remappers. No button, stick, trigger or rumble test runs, and nothing here changes permissions.';

const PAD_COUNT = /^(\d+) controller-like device\(s\): (.+)$/;
const DRIVER_PREFIX = 'Driver for ';

function reading(readings: Reading[], name: string): Reading | undefined {
  return readings.find((item) => item.name.toLowerCase() === name);
}

function padLine(readings: Reading[]): ControllerLine {
  const item = reading(readings, 'input devices');
  if (!item) {
    return { key: 'pad', label: 'Controller', state: 'unknown', detail: 'The helper returned no input-device row.' };
  }
  if (item.state !== 'ok') {
    return { key: 'pad', label: 'Controller', state: 'warn', detail: 'No controller-like input device was found.' };
  }
  const counted = item.detail.match(PAD_COUNT);
  return {
    key: 'pad',
    label: 'Controller',
    state: 'ok',
    detail: counted ? `${counted[1]} controller-like device(s): ${counted[2]}.` : item.detail,
  };
}

function driverLine(readings: Reading[]): ControllerLine {
  const drivers = readings.filter((item) => item.name.startsWith(DRIVER_PREFIX));
  if (!drivers.length) {
    return {
      key: 'driver',
      label: 'Driver',
      state: 'unknown',
      detail: 'No USB controller driver row was reported; Bluetooth pads do not appear there.',
    };
  }
  const labels = (items: Reading[]) => items.map((item) => item.name.slice(DRIVER_PREFIX.length)).join(', ');
  const failed = drivers.filter((item) => item.state === 'fail');
  if (failed.length) {
    return {
      key: 'driver',
      label: 'Driver',
      state: 'warn',
      detail: `Not reported for ${labels(failed)} (${failed[0].detail}).`,
    };
  }
  return { key: 'driver', label: 'Driver', state: 'ok', detail: `Loaded for ${labels(drivers)}.` };
}

function uinputLine(readings: Reading[]): ControllerLine {
  const item = reading(readings, 'uinput');
  if (!item) {
    return { key: 'uinput', label: 'Steam Input access', state: 'unknown', detail: 'The helper returned no /dev/uinput row.' };
  }
  if (item.state === 'ok') {
    return { key: 'uinput', label: 'Steam Input access', state: 'ok', detail: 'This session can write /dev/uinput.' };
  }
  const missing = /missing/.test(item.detail);
  return {
    key: 'uinput',
    label: 'Steam Input access',
    state: 'warn',
    detail: missing
      ? '/dev/uinput is missing; Steam Input and remappers need it.'
      : 'This session cannot read and write /dev/uinput; Steam Input and remappers need it.',
  };
}

/** The three player lines, in the order the review describes them. */
export function controllerSummary(findings: Reading[] | null): ControllerLine[] {
  if (!findings) return [];
  return [padLine(findings), driverLine(findings), uinputLine(findings)];
}

/** Curated advice for the families this scan saw; never the helper's text. */
export function controllerAdvice(report: ControllerReport | null): ControllerHint[] {
  return report?.hints ?? [];
}
