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

export type SteamStateFilter = 'all' | 'native' | 'proton' | 'tweaks' | 'blocked' | 'unreviewed';

export interface SteamLibraryEntry {
  game: SteamGame;
  /** Resolved once, so the chip, the filter and the disclosure agree. */
  verdict: CompatVerdict | null;
}

/** Sort once for reading, then attach the app-id verdict each card shows. The
 * resolver takes the app id only: a scanned title can never borrow a note. */
export function steamLibraryEntries(
  games: SteamGame[],
  verdictFor: (appId: string) => CompatVerdict | null,
): SteamLibraryEntry[] {
  return sortSteamGames(games).map((game) => ({ game, verdict: verdictFor(game.app_id) }));
}

/** Case- and accent-folded, with punctuation read as a space, so "pokemon"
 * finds "Pokémon" and "half life" finds "Half-Life". */
function fold(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

/** A title match on the name, or a prefix match on the app id. Local text
 * only; nothing is sent anywhere to answer it. */
export function steamLibraryMatches(entry: SteamLibraryEntry, query: string): boolean {
  const needle = fold(query.trim());
  if (!needle) return true;
  return fold(entry.game.name).includes(needle) || entry.game.app_id.startsWith(needle);
}

/** Search and curated-state filter, preserving the library's order. */
export function filterSteamLibrary(
  entries: SteamLibraryEntry[],
  query: string,
  state: SteamStateFilter,
): SteamLibraryEntry[] {
  return entries.filter((entry) => {
    if (!steamLibraryMatches(entry, query)) return false;
    if (state === 'all') return true;
    if (state === 'unreviewed') return entry.verdict === null;
    return entry.verdict?.status === state;
  });
}

/** How many of the scanned titles the page can actually show something for. */
export interface SteamLibraryCoverage {
  total: number;
  withVerdict: number;
  covered: number;
}

export function steamLibraryCoverage(entries: SteamLibraryEntry[]): SteamLibraryCoverage {
  return {
    total: entries.length,
    withVerdict: entries.filter((entry) => entry.verdict !== null).length,
    covered: entries.filter((entry) => Boolean(entry.game.cover)).length,
  };
}

/** A large library would otherwise put every base64 cover in the DOM at once.
 * The first covers in reading order render; the rest fall back to the letter
 * tile they already have, and a search or filter brings a cover back in. */
export const STEAM_COVER_BUDGET = 60;

export interface SteamCoverPlan {
  entries: SteamLibraryEntry[];
  budgeted: number;
  covers: number;
}

export function steamLibraryCoverPlan(
  entries: SteamLibraryEntry[],
  limit: number = STEAM_COVER_BUDGET,
): SteamCoverPlan {
  let covers = 0;
  let budgeted = 0;
  const planned = entries.map((entry) => {
    if (!entry.game.cover) return entry;
    if (covers < limit) {
      covers += 1;
      return entry;
    }
    budgeted += 1;
    return { ...entry, game: { ...entry.game, cover: null } };
  });
  return { entries: planned, budgeted, covers };
}

/** One line for the whole library: what the reads cover, how many are on
 * screen, and what the cover budget left as a letter tile. */
export function steamLibraryCoverageNote(
  coverage: SteamLibraryCoverage,
  shown: number,
  budgeted: number,
): string {
  if (!coverage.total) return '';
  const counts = `${coverage.total} installed title(s): ${coverage.withVerdict} carry a curated note, ${coverage.covered} have Steam’s cached cover art.`;
  const filtered = shown === coverage.total ? '' : ` Showing ${shown} under this search and filter.`;
  const budget = budgeted
    ? ` ${budgeted} more cover(s) stay a letter tile under the ${STEAM_COVER_BUDGET}-cover budget; search or filter to bring one in.`
    : '';
  return `${counts}${filtered}${budget} Covers come from Steam’s local appcache/librarycache; no network request was made.`;
}
