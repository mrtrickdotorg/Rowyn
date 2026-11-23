// Play's "Get set up to play" strip: the steps a new player has to clear,
// projected from readings the page already performs. It adds no bridge call
// and no timer of its own — the page runs Steam Play, Vulkan layers, displays
// and controllers, and reads shader pre-caching, recent play and the MangoHud
// config once on open; the same readings feed the sections below. A step is
// only ever "done" because a reading says so.

import type { MangohudConfig, RecentPlay, ShaderCacheStatus, SteamPlayStatus, VulkanLayers } from './rowynMvp';
import { mangohudConfigState, type MangohudBoundary } from './rowynPlayMangohudModel';
import type { Reading } from './rowynMvpModel';

export type SetupState = 'done' | 'todo' | 'pending' | 'unknown';

export interface SetupStep {
  key: 'steam' | 'steamPlay' | 'layers' | 'shader' | 'recent' | 'mangohud' | 'controllers';
  label: string;
  hint: string;
  state: SetupState;
}

/** Markers are decorative; the hint carries the meaning for screen readers. */
export const SETUP_MARKERS: Record<SetupState, string> = {
  done: '✓',
  todo: '•',
  pending: '…',
  unknown: '?',
};

export interface PlaySetupInput {
  /** Steam's launcher state; `pending` until the launcher scan reports. */
  steam: 'installed' | 'not installed' | 'unavailable' | 'pending';
  steamPlay: SteamPlayStatus | null;
  steamPlayFailed: boolean;
  layers: VulkanLayers | null;
  layersFailed: boolean;
  controllers: Reading[] | null;
  controllersFailed: boolean;
  shaderCache: ShaderCacheStatus | null;
  shaderFailed: boolean;
  recentPlay: RecentPlay | null;
  recentFailed: boolean;
  mangohudConfig: MangohudConfig | null;
  mangohudFailed: boolean;
  /** The launcher boundary the MangoHud section is set to. */
  mangohudBoundary: MangohudBoundary;
}

const LAYERS = [
  ['MangoHud', 'mangohud_branches'],
  ['gamescope', 'gamescope_branches'],
  ['vkBasalt', 'vkbasalt_branches'],
] as const;

/** Only reached when no reading exists: a failure to read stays visible as
 * unknown rather than turning into advice the page cannot support. */
function unresolved(failed: boolean): SetupStep['state'] {
  return failed ? 'unknown' : 'pending';
}

function steamStep(steam: PlaySetupInput['steam']): SetupStep {
  switch (steam) {
    case 'installed':
      return {
        key: 'steam',
        label: 'Steam installed',
        hint: 'Steam is installed for this account.',
        state: 'done',
      };
    case 'not installed':
      return {
        key: 'steam',
        label: 'Install Steam',
        hint: 'Install Steam below — this page is set up around it.',
        state: 'todo',
      };
    case 'unavailable':
      return {
        key: 'steam',
        label: 'Install Steam',
        hint: 'The launcher scan could not read every scope; use Refresh launchers.',
        state: 'unknown',
      };
    default:
      return {
        key: 'steam',
        label: 'Install Steam',
        hint: 'Reading installed launchers…',
        state: 'pending',
      };
  }
}

function steamPlayStep(input: PlaySetupInput): SetupStep {
  const { steamPlay } = input;
  if (!steamPlay) {
    return {
      key: 'steamPlay',
      label: 'Enable Steam Play',
      hint: input.steamPlayFailed
        ? 'Steam Play state could not be read; use Check Steam Play.'
        : 'Reading Steam’s configuration…',
      state: unresolved(input.steamPlayFailed),
    };
  }
  if (!steamPlay.steam_present) {
    return {
      key: 'steamPlay',
      label: 'Enable Steam Play',
      hint: 'Steam was not detected, so Steam Play cannot be checked yet.',
      state: 'unknown',
    };
  }
  if (steamPlay.mapping_present) {
    return {
      key: 'steamPlay',
      label: 'Steam Play enabled',
      hint: 'Steam Play is on for all titles (Proton Experimental).',
      state: 'done',
    };
  }
  return {
    key: 'steamPlay',
    label: 'Enable Steam Play for all titles',
    hint: steamPlay.steam_running
      ? 'Quit Steam, then use Enable Steam Play for all titles below.'
      : 'Use Enable Steam Play for all titles below so Windows games are playable.',
    state: 'todo',
  };
}

function layersStep(input: PlaySetupInput): SetupStep {
  const { layers } = input;
  if (!layers) {
    return {
      key: 'layers',
      label: 'Install the overlay layers',
      hint: input.layersFailed
        ? 'The Flatpak layer list could not be read; use Check Vulkan layers.'
        : 'Reading installed Flatpak layers…',
      state: unresolved(input.layersFailed),
    };
  }
  const branch = layers.steam_runtime_branch;
  if (!branch) {
    return {
      key: 'layers',
      label: 'Install the overlay layers',
      hint: 'Steam’s runtime branch is unknown, so layer coverage cannot be checked.',
      state: 'unknown',
    };
  }
  const missing = LAYERS.filter(([, field]) => !layers[field].includes(branch)).map(([name]) => name);
  if (missing.length === 0) {
    return {
      key: 'layers',
      label: 'Overlays installed',
      hint: `MangoHud, gamescope and vkBasalt are installed for runtime ${branch}.`,
      state: 'done',
    };
  }
  return {
    key: 'layers',
    label: 'Install the overlay layers',
    hint: `Missing for runtime ${branch}: ${missing.join(', ')}. Install them under Compatibility & anti-cheat.`,
    state: 'todo',
  };
}

function shaderStep(input: PlaySetupInput): SetupStep {
  const { shaderCache } = input;
  if (!shaderCache) {
    return {
      key: 'shader',
      label: 'Check shader pre-caching',
      hint: input.shaderFailed
        ? 'Steam’s shader setting could not be read; use Check shader pre-caching.'
        : 'Reading Steam’s shader setting…',
      state: unresolved(input.shaderFailed),
    };
  }
  if (!shaderCache.steam_detected) {
    return {
      key: 'shader',
      label: 'Check shader pre-caching',
      hint: 'Steam was not detected, so its shader setting cannot be read.',
      state: 'unknown',
    };
  }
  if (!shaderCache.config_read) {
    return {
      key: 'shader',
      label: 'Check shader pre-caching',
      hint: 'Steam’s config.vdf could not be read; start Steam once and check again.',
      state: 'unknown',
    };
  }
  if (shaderCache.pre_caching === false) {
    // A real choice, but the one setting that decides whether a first launch
    // stutters, so the strip names where to change it rather than assuming.
    return {
      key: 'shader',
      label: 'Enable Shader Pre-Caching',
      hint: 'Steam’s own setting is off. Turn it on in Steam → Settings → Downloads so shaders compile ahead of time; Rowyn does not change it.',
      state: 'todo',
    };
  }
  return {
    key: 'shader',
    label: shaderCache.pre_caching === true ? 'Shader pre-caching on' : 'Shader pre-caching at default',
    hint: shaderCache.pre_caching === true
      ? 'Steam’s own setting is on.'
      : 'Steam has not recorded the setting, so its default (on) applies.',
    state: 'done',
  };
}

function recentStep(input: PlaySetupInput): SetupStep {
  const { recentPlay } = input;
  if (!recentPlay) {
    return {
      key: 'recent',
      label: 'Read recent play',
      hint: input.recentFailed
        ? 'Steam’s play history could not be read; use Refresh recently played.'
        : 'Reading Steam’s play history…',
      state: unresolved(input.recentFailed),
    };
  }
  if (!recentPlay.steam_detected) {
    return {
      key: 'recent',
      label: 'Read recent play',
      hint: 'Steam was not detected, so there is no local play history to read.',
      state: 'unknown',
    };
  }
  if (!recentPlay.profile_read) {
    return {
      key: 'recent',
      label: 'Read recent play',
      hint: 'Steam’s local profile could not be read; start Steam once and refresh.',
      state: 'unknown',
    };
  }
  return {
    key: 'recent',
    label: 'Recent play read',
    hint: recentPlay.games.length
      ? `Steam records ${recentPlay.games.length} recently played title(s); Rowyn measured nothing here.`
      : 'Steam records no recently played title with a local name yet.',
    state: 'done',
  };
}

function mangohudStep(input: PlaySetupInput): SetupStep {
  const { mangohudConfig } = input;
  if (!mangohudConfig) {
    return {
      key: 'mangohud',
      label: 'Check MangoHud settings',
      hint: input.mangohudFailed
        ? 'The MangoHud settings read could not run; use Check MangoHud settings.'
        : 'Reading MangoHud’s configuration…',
      state: unresolved(input.mangohudFailed),
    };
  }
  if (!mangohudConfig.steam_detected) {
    return {
      key: 'mangohud',
      label: 'Check MangoHud settings',
      hint: 'Steam was not detected, so there is no MangoHud config to read.',
      state: 'unknown',
    };
  }
  const state = mangohudConfigState(mangohudConfig, input.mangohudBoundary);
  if (state.exists && state.readable) {
    return {
      key: 'mangohud',
      label: 'MangoHud settings read',
      hint: `${state.settings} setting(s) in ${state.path}; the overlay still needs MANGOHUD=1 in the launch options.`,
      state: 'done',
    };
  }
  if (state.exists) {
    return {
      key: 'mangohud',
      label: 'Check MangoHud settings',
      hint: `${state.path} exists but could not be read; use Check MangoHud settings.`,
      state: 'unknown',
    };
  }
  return {
    key: 'mangohud',
    label: 'Set up MangoHud (optional)',
    hint: `No config yet at ${state.path}; MangoHud uses its defaults. Copy the recommended config under Compatibility if you want the overlay.`,
    state: 'todo',
  };
}

function controllersStep(input: PlaySetupInput): SetupStep {
  const { controllers } = input;
  if (!controllers) {
    return {
      key: 'controllers',
      label: 'Pair a controller (optional)',
      hint: input.controllersFailed
        ? 'The controller check could not run; use Rescan controllers.'
        : 'Reading input devices…',
      state: unresolved(input.controllersFailed),
    };
  }
  const detected = controllers.some((item) => item.name === 'Input devices' && item.state === 'ok');
  return detected
    ? {
      key: 'controllers',
      label: 'Controller detected',
      hint: 'Steam Input and remappers can see it.',
      state: 'done',
    }
    : {
      key: 'controllers',
      label: 'Pair a controller (optional)',
      hint: 'No controller detected — plug one in or pair it over Bluetooth, then rescan.',
      state: 'todo',
    };
}

export function playSetupSteps(input: PlaySetupInput): SetupStep[] {
  return [
    steamStep(input.steam),
    steamPlayStep(input),
    layersStep(input),
    shaderStep(input),
    recentStep(input),
    mangohudStep(input),
    controllersStep(input),
  ];
}
