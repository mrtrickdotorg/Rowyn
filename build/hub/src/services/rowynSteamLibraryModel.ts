// B3: the Steam section leads with the library. These are pure projections of
// the explicit local scan: alphabetical order for a library, one-word curated
// states, a letter tile when Steam has cached no artwork, and a count of how
// many covers the local read actually returned. Nothing here measures, fetches
// or infers a verdict.

import { compatVerdict, type SteamGame } from './rowynMvpModel';

export interface SteamStateChip {
  label: string;
  tone: string;
}

const CHIPS: Record<string, string> = {
  native: 'Native',
  proton: 'Proton',
  tweaks: 'Tweaks',
  blocked: 'Blocked',
};

/** One word for the curated state, or nothing when there is no verdict. */
export function steamStateChip(appId: string, name: string): SteamStateChip | null {
  const verdict = compatVerdict(appId, name);
  if (!verdict) return null;
  const label = CHIPS[verdict.status];
  return label ? { label, tone: `is-${verdict.status}` } : null;
}

/** Alphabetical, as a library is read; ties fall back to the app id. */
export function sortSteamGames(games: SteamGame[]): SteamGame[] {
  return [...games].sort(
    (a, b) =>
      a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }) ||
      a.app_id.localeCompare(b.app_id, 'en', { numeric: true }),
  );
}

/** The letter tile standing in for absent artwork: up to two initials. */
export function coverInitials(name: string): string {
  const words = name
    .replace(/[™®©]/g, '')
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean)
    .slice(0, 2);
  const initials = words.map((word) => Array.from(word)[0] ?? '').join('').toUpperCase();
  return initials || '?';
}

/** What the cover read established, and what it did not. */
export function steamLibraryCoverNote(games: SteamGame[]): string {
  if (!games.length) return '';
  const covered = games.filter((game) => Boolean(game.cover)).length;
  if (!covered) {
    return 'Steam has no cached cover art for these titles on this machine, so each shows a letter tile instead. No network request was made.';
  }
  return `${covered} of ${games.length} title(s) have Steam’s cached cover art; the rest show a letter tile. Covers come from Steam’s local appcache/librarycache and make no network request.`;
}
