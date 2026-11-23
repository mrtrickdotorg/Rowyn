// A7: Flathub Steam reads MangoHud's configuration inside its sandbox, at
// ~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf — not the
// ~/.config/MangoHud path most guides name. This model words the read-only
// result and carries the starting config the page offers as copyable text.
// Rowyn never creates, edits or backs up the file, and nothing here measures
// frame rate or claims the overlay is running.

import type { MangohudConfig } from './rowynMvp';

/** Home-relative paths come from the backend; only the `~/` is added here. */
export function mangohudConfigPath(status: MangohudConfig): string {
  return `~/${status.path}`;
}

/** What the read establishes about the file Flathub Steam actually reads. */
export function mangohudConfigLabel(status: MangohudConfig | null): string {
  if (!status) return 'MangoHud settings: not read yet.';
  if (!status.steam_detected) {
    return `MangoHud settings: Steam was not detected in the common native or Flatpak paths, so there is no config to read. Flathub Steam would read ~/${status.path}.`;
  }
  const skipped = status.warnings
    ? ` ${status.warnings} item(s) could not be read.`
    : '';
  if (status.exists && status.readable) {
    return `MangoHud settings: ${status.settings} setting(s) in ~/${status.path}.${skipped}`;
  }
  if (status.exists) {
    return `MangoHud settings: ~/${status.path} exists but could not be read.${skipped}`;
  }
  const ignored = status.native_exists
    ? ` A file at ~/${status.native_path} is ignored by Flathub Steam.`
    : '';
  return `MangoHud settings: no config yet — Flathub Steam reads ~/${status.path} and uses MangoHud’s defaults until one exists.${ignored}${skipped}`;
}

/** Read-only promise shown with the recommended config. */
export const MANGOHUD_READ_NOTE =
  'Rowyn reads this path and this file only: it never creates, edits or backs up MangoHud’s configuration, and the overlay still needs MANGOHUD=1 in the game’s launch options.';

/** A small starting point; saving it is the player's action, not Rowyn's. */
export const MANGOHUD_RECOMMENDED = `# A starting point for MangoHud. Save it as MangoHud.conf at the path above,
# then edit it freely — every key here is optional.
gpu_stats
cpu_stats
frame_timing
fps_limit=0
position=top-left
font_size=24
background_alpha=0.4
toggle_hud=Shift_R+F12
`;
