// B6: three helpers ship with Rowyn but were undiscoverable. This model names
// them, words the tune state without inventing a measurement, and holds the
// copyable wrapper examples. It executes and fetches nothing.

import type { GamingHelpers } from './rowynMvp';

export const GAMING_HELPERS_NOTE =
  'rowyn-game-launch starts a command in the Rowyn gaming slice (and under GameMode when it is installed); rowyn-mimalloc-run preloads the mimalloc allocator for one command; rowyn-tune switches the opt-in latency sysctl profile. All three ship in the image — nothing is installed from this page, and none of them is a benchmark.';

export const TUNE_ADMIN_NOTE =
  'Switching the profile writes or removes /etc/sysctl.d/90-rowyn-gaming.conf and asks for your administrator password through the same askpass helper the VPN and firewall actions use.';

export const TUNE_OFF_NOTE =
  'The drop-in is gone, so defaults apply from the next boot; the running kernel keeps the tuned values until then.';

export const HOST_ONLY_NOTE =
  'Both wrappers live on the host, so a game launched by Flatpak Steam cannot run them; use these examples only on the host-native boundary.';

export interface HelperExample {
  key: string;
  label: string;
  command: string;
}

export const HELPER_EXAMPLES: readonly HelperExample[] = [
  {
    key: 'slice',
    label: 'Steam → Properties → Launch Options, host-native launcher:',
    command: '/usr/bin/rowyn-game-launch %command%',
  },
  {
    key: 'mimalloc',
    label: 'One game with the allocator, host-native launcher:',
    command: '/usr/bin/rowyn-mimalloc-run %command%',
  },
];

export function gamingTuneLabel(helpers: GamingHelpers | null): string {
  if (!helpers) return 'Helper state not read yet.';
  if (!helpers.installed) return 'Some Rowyn gaming helpers are missing from this install.';
  return helpers.tune === 'gaming'
    ? 'rowyn-tune: gaming profile on.'
    : 'rowyn-tune: default profile (no drop-in).';
}

export function tuneIsOn(helpers: GamingHelpers | null): boolean {
  return helpers?.tune === 'gaming';
}
