// A7: MangoHud reads a different config depending on how the game was
// launched. Flathub Steam reads it inside its sandbox at
// ~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf; a
// host-native MangoHud reads ~/.config/MangoHud/MangoHud.conf. This model
// picks the path for the launcher boundary the page already asks about, words
// the read-only result, and carries the starting config as copyable text.
// Rowyn never creates, edits or backs up either file, and nothing here
// measures frame rate or claims the overlay is running.

import type { MangohudConfig } from './rowynMvp';

/** The same boundary the launch-options builder uses. */
export type MangohudBoundary = 'flatpak' | 'host';

export interface MangohudPathState {
  /** `~/…` when under home; the absolute path when XDG_CONFIG_HOME points
   * outside home. No absolute home path is printed. */
  path: string;
  exists: boolean;
  readable: boolean;
  settings: number;
  boundary: MangohudBoundary;
}

/** Which file applies, and what the read established about it. */
export function mangohudConfigState(
  status: MangohudConfig,
  boundary: MangohudBoundary = 'flatpak',
): MangohudPathState {
  return boundary === 'host'
    ? {
      path: status.native_path,
      exists: status.native_exists,
      readable: status.native_readable,
      settings: status.native_settings,
      boundary,
    }
    : {
      path: `~/${status.path}`,
      exists: status.exists,
      readable: status.readable,
      settings: status.settings,
      boundary,
    };
}

export function mangohudConfigPath(
  status: MangohudConfig,
  boundary: MangohudBoundary = 'flatpak',
): string {
  return mangohudConfigState(status, boundary).path;
}

/** What the read establishes about the file this boundary actually reads. */
export function mangohudConfigLabel(
  status: MangohudConfig | null,
  boundary: MangohudBoundary = 'flatpak',
): string {
  if (!status) return 'MangoHud settings: not read yet.';
  const state = mangohudConfigState(status, boundary);
  const where = state.boundary === 'host' ? 'host-native MangoHud' : 'Flathub Steam';
  if (state.boundary === 'flatpak' && !status.steam_detected) {
    return `MangoHud settings: Steam was not detected in the common native or Flatpak paths, so there is no Flathub config to read. Flathub Steam would read ${state.path}.`;
  }
  const skipped = status.warnings
    ? ` ${status.warnings} item(s) could not be read.`
    : '';
  if (state.exists && state.readable) {
    return `MangoHud settings for ${where}: ${state.settings} setting(s) in ${state.path}.${skipped}`;
  }
  if (state.exists) {
    return `MangoHud settings for ${where}: ${state.path} exists but could not be read.${skipped}`;
  }
  // The other boundary's file is not an alternative for this one; name it so a
  // player who followed the wrong guide knows why their settings do nothing.
  const other = state.boundary === 'host'
    ? (status.exists ? ` Flathub Steam’s config at ~/${status.path} is not read by host-native MangoHud.` : '')
    : (status.native_exists ? ` A file at ${status.native_path} is ignored by Flathub Steam.` : '');
  return `MangoHud settings for ${where}: no config yet — ${where} reads ${state.path} and uses MangoHud’s defaults until one exists.${other}${skipped}`;
}

/** Read-only promise shown with the recommended config. */
export const MANGOHUD_READ_NOTE =
  'Rowyn reads both candidate paths and these files only: it never creates, edits or backs up MangoHud’s configuration, and the overlay still needs MANGOHUD=1 in the game’s launch options.';

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
