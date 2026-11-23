// B3: the Steam section leads with the library. These are pure projections of
// the explicit local scan: alphabetical order for a library, one-word curated
// states, a letter tile when Steam has cached no artwork, and a count of how
// many covers the local read actually returned. Nothing here measures, fetches
// or infers a verdict.

import type { CompatVerdict, SteamGame } from './rowynMvpModel';

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

/** One word for the curated state, from the verdict the card already has.
 * The card resolves `compatVerdict` once and feeds both the chip and the
 * disclosure; this projection never looks it up a second time. */
export function steamStateChip(verdict: CompatVerdict | null): SteamStateChip | null {
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

/** B4: one dated provenance note for the whole Steam library section.
 * It names what the local reads cover, the curated dataset's date and size,
 * and that a title without a note carries no verdict — so no card has to
 * repeat a date or a "not tested" line. */
export function steamLibraryProvenance(reviewedOn: string | null, verdictCount: number): string {
  const verdicts = verdictCount && reviewedOn
    ? `Curated compatibility notes cover ${verdictCount} titles and were last checked ${reviewedOn}; they are guidance, not Rowyn game validation, and game, launcher or anti-cheat updates can change them. Titles without a note show no verdict.`
    : 'No curated compatibility notes ship with this build, so no title carries a verdict.';
  return `Local reads only: shader pre-caching, play history, installed titles and cover art come from Steam’s own files, with no network request, no launch and no game-file access. ${verdicts} ProtonDB links are community reports, not guarantees.`;
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
