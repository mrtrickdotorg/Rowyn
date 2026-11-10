// Rowyn Play per-game profiles: the launch choices a player picked for
// one title, remembered per user so relaunching the Hub (or Steam) does
// not reset them. The Play page's top-level builder remains the
// stateless default profile; a saved per-game profile overrides it for
// that title. Storage follows the setup checklist's pattern — an
// injectable Storage so tests drive the model without a DOM, and a
// save that reports failure instead of pretending persistence worked.

import { launchOptions } from './rowynMvpModel';

export interface GameProfile {
  mangohud: boolean;
  vkbasalt: boolean;
  /** Prefix the launch with gamescope. On the Flatpak boundary the
   * gamescope Vulkan layer carries the session (installed from the Play
   * page's layer card); on the host boundary this emits the gamescope
   * wrapper prefix for a Distrobox/native gamescope install. */
  gamescope: boolean;
  gaming: boolean;
  mimalloc: boolean;
  /** Free-form extra launch options, appended verbatim. */
  extra: string;
}

export const EMPTY_PROFILE: GameProfile = {
  mangohud: false,
  vkbasalt: false,
  gamescope: false,
  gaming: false,
  mimalloc: false,
  extra: '',
};

const STORAGE_KEY = 'rowyn.play.profiles.v1';

export type ProfileStorage = Pick<Storage, 'getItem' | 'setItem'>;

function localStore(): ProfileStorage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function sanitizeProfile(value: unknown): GameProfile {
  if (!value || typeof value !== 'object') return { ...EMPTY_PROFILE };
  const raw = value as Record<string, unknown>;
  return {
    mangohud: raw.mangohud === true,
    vkbasalt: raw.vkbasalt === true,
    gamescope: raw.gamescope === true,
    gaming: raw.gaming === true,
    mimalloc: raw.mimalloc === true,
    extra: typeof raw.extra === 'string' ? raw.extra : '',
  };
}

export function loadProfiles(storage: ProfileStorage | null = localStore()): Record<string, GameProfile> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const profiles: Record<string, GameProfile> = {};
    for (const [appId, value] of Object.entries(parsed as Record<string, unknown>)) {
      profiles[appId] = sanitizeProfile(value);
    }
    return profiles;
  } catch {
    return {};
  }
}

/** Returns null on success, or a plain-language persistence warning. */
export function saveProfiles(
  profiles: Record<string, GameProfile>,
  storage: ProfileStorage | null = localStore(),
): string | null {
  if (!storage) return 'Profiles cannot be saved in this session; they will reset when the Hub closes.';
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(profiles));
    return null;
  } catch {
    return 'Profiles could not be saved (storage is full or blocked); they will reset when the Hub closes.';
  }
}

export function profileFor(profiles: Record<string, GameProfile>, appId: string): GameProfile {
  return profiles[appId] ?? { ...EMPTY_PROFILE };
}

export function isDefaultProfile(profile: GameProfile): boolean {
  return !profile.mangohud && !profile.vkbasalt && !profile.gamescope
    && !profile.gaming && !profile.mimalloc && profile.extra.trim() === '';
}

/**
 * Assemble the full Steam launch-options string for one title.
 * Ordering contract: environment switches first (MANGOHUD=1,
 * ENABLE_VKBASALT=1 — gamescope inherits its environment, the
 * documented pattern), then the gamescope prefix on the host boundary,
 * then Rowyn's wrapper binaries, then %command%, then the player's
 * verbatim extras. On the Flatpak boundary the gamescope flag changes
 * nothing in the string: the layer, matched to the game's runtime
 * branch on the Play page, is what carries it.
 */
export function assembleProfileOptions(boundary: 'flatpak' | 'host', profile: GameProfile): string {
  const base = launchOptions(boundary, profile.gaming, profile.mimalloc, profile.mangohud, profile.vkbasalt);
  const withScope = boundary === 'host' && profile.gamescope ? insertGamescope(base, profile) : base;
  const extra = profile.extra.trim();
  return extra ? `${withScope} ${extra}` : withScope;
}

function insertGamescope(options: string, profile: GameProfile): string {
  // launchOptions(host) shape: [env…] [wrapper…] %command%. gamescope
  // wraps the wrappers too, so it goes immediately before the first
  // wrapper path, or before %command% when no wrapper is set. The env
  // assignments stay in front of it (gamescope passes its environment
  // through to the game).
  const marker = profile.gaming
    ? ' /usr/bin/rowyn-game-launch'
    : profile.mimalloc
      ? ' /usr/bin/rowyn-mimalloc-run'
      : ' %command%';
  if (options.startsWith('%command%')) return `gamescope -- ${options}`;
  return options.replace(marker, ` gamescope --${marker}`);
}
