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

/** Six months. Past this, the curated notes read as stale rather than merely
 * dated, because an authoritative-looking old matrix is worse than a link. */
export const COMPAT_NOTE_STALE_DAYS = 183;

/** Whole days between a `YYYY-MM-DD` check date and `now`, or null when the
 * date is missing or unparseable. A date ahead of the clock is not stale. */
export function compatNoteAgeDays(reviewedOn: string | null, now = Date.now()): number | null {
  if (!reviewedOn || !/^\d{4}-\d{2}-\d{2}$/.test(reviewedOn)) return null;
  const checked = Date.parse(`${reviewedOn}T00:00:00Z`);
  if (!Number.isFinite(checked)) return null;
  const days = Math.floor((now - checked) / 86_400_000);
  return days < 0 ? 0 : days;
}

const CHIPS: Record<string, string> = {
  native: 'Native',
  proton: 'Proton',
  tweaks: 'Tweaks',
  blocked: 'Blocked',
};

/** One word for the curated state, from the verdict the card already has.
 * The card resolves the app-id verdict once and feeds both the chip and the
 * disclosure; this projection never looks a verdict up a second time. */
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
export function steamLibraryProvenance(
  reviewedOn: string | null,
  verdictCount: number,
  now = Date.now(),
): string {
  const ageDays = compatNoteAgeDays(reviewedOn, now);
  const stale = ageDays !== null && ageDays > COMPAT_NOTE_STALE_DAYS;
  const verdicts = verdictCount && reviewedOn
    ? `Curated compatibility notes cover ${verdictCount} titles and were last checked ${reviewedOn}${stale
      ? ', more than six months ago — treat them as stale and check ProtonDB for current reports'
      : ''}; they are guidance, not Rowyn game validation, and game, launcher or anti-cheat updates can change them. Titles without a note show no verdict.`
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
