// Play's "Get set up to play" strip: the four steps a new player has to
// clear, projected from readings the page already performs. It adds no
// bridge call and no timer of its own — the page runs Steam Play, Vulkan
// layers, displays and controllers once on open, and the same readings feed
// the sections below. A step is only ever "done" because a reading says so.

import type { SteamPlayStatus, VulkanLayers } from './rowynMvp';
import type { Reading } from './rowynMvpModel';

export type SetupState = 'done' | 'todo' | 'pending' | 'unknown';

export interface SetupStep {
  key: 'steam' | 'steamPlay' | 'layers' | 'controllers';
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
  return [steamStep(input.steam), steamPlayStep(input), layersStep(input), controllersStep(input)];
}
