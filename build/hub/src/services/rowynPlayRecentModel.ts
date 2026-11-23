// Play's "Recently played" list: Steam's own per-account play history, shown
// as Steam wrote it (`LastPlayed` and its playtime counter). This is a pure
// projection of the native read — no timer, no network, no derived score and
// no claim that Rowyn measured frame rate, latency or session length. A title
// Steam has no local name for (not installed here) is counted, never labelled
// with a made-up name.

import type { RecentPlay } from './rowynMvp';

const MINUTES_PER_HOUR = 60;
const SECONDS_PER_DAY = 86_400;

/** Steam's playtime counter is minutes; a missing counter stays missing. */
export function formatPlaytime(minutes: number | null): string {
  if (minutes === null || !Number.isFinite(minutes) || minutes < 0) return 'playtime not recorded';
  if (minutes < MINUTES_PER_HOUR) return `${Math.round(minutes)} min played`;
  // One decimal place (dropped when it is .0) keeps half-hours like 30.5
  // instead of rounding them into a different number of hours.
  return `${Math.round((minutes / MINUTES_PER_HOUR) * 10) / 10} h played`;
}

/** Relative wording only: the page makes no claim beyond Steam's timestamp. */
export function formatLastPlayed(epochSeconds: number, now: number = Date.now()): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return 'last played not recorded';
  const days = Math.floor((now / 1000 - epochSeconds) / SECONDS_PER_DAY);
  if (days <= 0) return 'last played today';
  if (days === 1) return 'last played yesterday';
  if (days < 30) return `last played ${days} days ago`;
  if (days < 365) {
    const months = Math.floor(days / 30);
    return `last played ${months} month${months === 1 ? '' : 's'} ago`;
  }
  const years = Math.floor(days / 365);
  return `last played ${years} year${years === 1 ? '' : 's'} ago`;
}

/** What the read actually establishes, including what it could not. */
export function recentPlayStatus(play: RecentPlay): string {
  if (!play.steam_detected) {
    return 'Steam was not detected in the common native or Flatpak paths, so there is no local profile to read.';
  }
  if (!play.profile_read) {
    return 'Steam’s local profile could not be read. Start Steam once and refresh; a custom Steam location may not be scanned.';
  }
  const skipped = play.warnings ? ` ${play.warnings} profile item(s) were skipped or unreadable.` : '';
  const unnamed = play.unnamed
    ? ` ${play.unnamed} recently played title(s) are not installed here, so Steam’s local metadata does not name them.`
    : '';
  if (!play.games.length) {
    return `Steam’s local profile records no played title with a local name yet.${skipped}${unnamed}`;
  }
  return `Read ${play.games.length} recently played title(s) from Steam’s local profile; Rowyn measured nothing here.${skipped}${unnamed}`;
}
