// A6: Steam's own Shader Pre-Caching setting, shown as Steam recorded it.
// The flag lives in Steam's global config.vdf; this model only words it. It
// makes no measurement claim and never turns "not recorded" into on or off.

import type { ShaderCacheStatus } from './rowynMvp';

/** The one line of guidance: what pre-caching does for first-play stutter. */
export const SHADER_PRE_CACHING_TIP =
  'Enable Shader Pre-Caching in Steam → Settings → Downloads: Steam then downloads shaders compiled for this GPU ahead of time, which is what keeps a game’s first minutes from stuttering while shaders compile.';

/** What the read establishes, including what Steam did not record. */
export function shaderPreCachingLabel(status: ShaderCacheStatus | null): string {
  if (!status) return 'Shader Pre-Caching: reading Steam’s configuration…';
  if (!status.steam_detected) {
    return 'Shader Pre-Caching: not available — Steam was not detected in the common native or Flatpak paths.';
  }
  if (!status.config_read) {
    return 'Shader Pre-Caching: not read — Steam’s config.vdf was not found or could not be read; start Steam once and check again.';
  }
  const skipped = status.warnings
    ? ` ${status.warnings} configuration item(s) were skipped or unreadable.`
    : '';
  if (status.pre_caching === true) return `Shader Pre-Caching: on (Steam’s own setting).${skipped}`;
  if (status.pre_caching === false) {
    return `Shader Pre-Caching: off (Steam’s own setting).${skipped}`;
  }
  return `Shader Pre-Caching: not recorded in Steam’s configuration, so Steam’s default (on) applies.${skipped}`;
}
