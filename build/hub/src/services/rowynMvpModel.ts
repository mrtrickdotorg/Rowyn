import type { InstalledFlatpaksResult } from './liveData';
export interface Reading { name: string; state: string; detail: string }

/**
 * The states `rowyn_diagnostics.rs` actually emits (its `READING_STATES`).
 *
 * `ok` means the check ran and found nothing to review; `attention` means it
 * ran and found something worth reading; `error` means it ran and broke;
 * `unavailable` means it could not run at all. Nothing here is called
 * "available" — the page used to test for that string, so a healthy reading
 * fell through to the partial/warning branch and the "Needs attention" count
 * included every check.
 */
export type ReadingState = 'ok' | 'attention' | 'error' | 'unavailable';
export const READING_STATES: readonly ReadingState[] = ['ok', 'attention', 'error', 'unavailable'];

export type ReadingTone = 'is-ok' | 'is-partial' | 'is-missing';

/** Card tone for one reading. Unknown strings fail toward review, not success. */
export function readingTone(state: string): ReadingTone {
  if (state === 'ok') return 'is-ok';
  return state === 'attention' ? 'is-partial' : 'is-missing';
}

/** True only for a check that ran and reported something to look at.
 * `unavailable` is its own visible label; it is not a finding. */
export function readingNeedsAttention(state: string): boolean {
  return state !== 'ok' && state !== 'unavailable';
}

export function readingStateLabel(state: string): string {
  switch (state) {
    case 'ok': return 'ok';
    case 'attention': return 'needs attention';
    case 'error': return 'check failed';
    case 'unavailable': return 'unavailable';
    default: return `unknown state (${state || 'not reported'})`;
  }
}
export interface SteamGame { app_id: string; name: string; cover: string | null }
export interface SteamScan { available: boolean; warnings: number; games: SteamGame[] }

/**
 * Curated compatibility verdicts from the shipped compat_games.json
 * dataset (kept from the port for this surface). A verdict is attributed
 * by Steam app id only — never by title — so a remake, demo or re-release
 * that shares a curated title cannot borrow another release's note. An
 * entry with no app id and an uncurated app id both get no verdict; the
 * page keeps showing the ProtonDB link instead of guessing.
 */
import compatData from '../data/compat_games.json';

export interface CompatVerdict { label: string; note: string; anticheat: string; checked: string; status: string }

const COMPAT_LABELS: Record<string, string> = {
  native: 'Works natively on Linux',
  proton: 'Works with Proton',
  tweaks: 'Works with tweaks',
  blocked: 'Does not run — anti-cheat or broken',
};

interface CompatEntry { name: string; status: string; note: string; anticheat: string; checked: string; source_url?: string }

const compatEntries = compatData.games as CompatEntry[];
const compatByAppId = new Map<string, CompatEntry>();
for (const game of compatEntries) {
  const match = (game.source_url ?? '').match(/\/app\/(\d+)/);
  if (match) compatByAppId.set(match[1], game);
}

/**
 * The dataset's newest check date and its size. The Play page states these
 * once per section; a card never repeats a date or a "not tested" caveat.
 */
export function compatReviewedOn(): string | null {
  const dates = compatEntries
    .map((game) => game.checked)
    .filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value))
    .sort();
  return dates.length ? dates[dates.length - 1] : null;
}
export function compatVerdictCount(): number {
  // Only entries with an app id can be attributed to a scanned title; the
  // section note states the coverage the page can actually show.
  return compatByAppId.size;
}
export function compatVerdict(appId: string): CompatVerdict | null {
  const game = compatByAppId.get(appId);
  if (!game) return null;
  return {
    label: COMPAT_LABELS[game.status] ?? `Curated status: ${game.status}`,
    note: game.note,
    anticheat: game.anticheat,
    checked: game.checked,
    status: game.status,
  };
}
export interface DiagnosticsSnapshot { collected_at: number; readings: Reading[] }
export const LAUNCHERS = [
  { id: 'com.valvesoftware.Steam', name: 'Steam', description: 'Steam library and Proton. Enable Steam Play per title when needed.' },
  { id: 'com.heroicgameslauncher.hgl', name: 'Heroic', description: 'Epic, GOG and Amazon libraries; sign in inside the launcher.' },
  { id: 'net.lutris.Lutris', name: 'Lutris', description: 'Community-managed game installers; review scripts before using them.' },
  { id: 'com.usebottles.bottles', name: 'Bottles', description: 'Isolated Wine environments. Not every Windows game is supported.' },
] as const;
export function requireLauncher(id: string) {
  const launcher = LAUNCHERS.find((entry) => entry.id === id);
  if (!launcher) throw new Error('Unsupported launcher.');
  return launcher;
}
export function launcherState(id: string, scan: InstalledFlatpaksResult | null): 'installed' | 'not installed' | 'unavailable' {
  requireLauncher(id);
  if (scan?.apps.some((app) => app.id === id)) return 'installed';
  return !scan || scan.failedScopes.length ? 'unavailable' : 'not installed';
}
export async function installLauncher(id: string, ops: { install: (id: string) => Promise<string>; wait: (job: string, limit: number) => Promise<string> }, onStarted: (job: string) => void = () => {}): Promise<string> {
  requireLauncher(id);
  const job = await ops.install(id);
  const pending = ops.wait(job, 1800); // registers the shared cancellation slot synchronously
  onStarted(job);
  return pending;
}
export function createPlayStatusOwner(write: (message: string) => void) {
  let generation = 0;
  const message = (error: unknown) => error instanceof Error ? error.message : String(error);
  return {
    async run(pending: string, action: () => Promise<string>) {
      const ticket = ++generation; write(pending);
      try { const result = await action(); if (ticket === generation) write(result); }
      catch (error) { if (ticket === generation) write(`Failed: ${message(error)}`); }
    },
    async cancel(action: () => Promise<string>) {
      const ticket = ++generation; write('Cancelling install…');
      try { const result = await action(); if (ticket === generation) write(result); }
      catch (error) { if (ticket === generation) write(`Cancel failed: ${message(error)}`); }
    },
  };
}
export function launchOptions(boundary: 'flatpak' | 'host', gaming: boolean, mimalloc: boolean, mangohud = false, vkbasalt = false, gamemode = false): string {
  // MangoHud and vkBasalt are environment switches, not wrapper binaries:
  // MANGOHUD=1 / ENABLE_VKBASALT=1 activate the Vulkan layers wherever the
  // layers themselves are installed — inside the Flatpak (the Freedesktop
  // VulkanLayer extensions for the app's runtime branch) or on a
  // host-native install (the distribution's packages in a
  // Distrobox/terminal context). The env assignments must precede any
  // wrapper path on the host boundary.
  const env = [mangohud ? 'MANGOHUD=1' : '', vkbasalt ? 'ENABLE_VKBASALT=1' : ''].filter(Boolean).join(' ');
  // GameMode wraps the sandboxed command with the client that ships inside
  // Steam's own Flatpak — the image's /usr/games/gamemoderun is not reachable
  // there. On the host boundary rowyn-game-launch already runs gamemoderun,
  // so the flag is only meaningful here.
  if (boundary === 'flatpak') return [env, gamemode ? 'gamemoderun' : '', '%command%'].filter(Boolean).join(' ');
  return [env, gaming ? '/usr/bin/rowyn-game-launch' : '', mimalloc ? '/usr/bin/rowyn-mimalloc-run' : '', '%command%'].filter(Boolean).join(' ');
}
export function steamCompatibilityLinks(appId: string) {
  if (!/^[1-9][0-9]{0,9}$/.test(appId)) throw new Error('invalid Steam app id');
  return {
    protondb: `https://www.protondb.com/app/${appId}`,
    steamStore: `https://store.steampowered.com/app/${appId}`,
  };
}
/** The native export refuses a report over 128 KB (bytes); stay under it. */
export const REPORT_BYTE_LIMIT = 120_000;
/** One probe may report 12 KB of command output; keep every section shareable. */
export const REPORT_DETAIL_LIMIT = 4_000;

function reportBytes(text: string): number {
  return typeof TextEncoder === 'function' ? new TextEncoder().encode(text).length : text.length;
}

function boundedDetail(detail: string): string {
  if (detail.length <= REPORT_DETAIL_LIMIT) return detail;
  return `${detail.slice(0, REPORT_DETAIL_LIMIT)}\n[detail truncated: ${detail.length - REPORT_DETAIL_LIMIT} character(s) omitted; the Diagnostics page shows the full reading]`;
}

/**
 * The text the user copies, previews and saves.
 *
 * Every reading is included. The old version stopped at sixteen, which
 * silently dropped Wi-Fi, audio, display and controller results — the checks
 * a report is usually filed about. When the byte budget cannot hold them all,
 * the omission is stated in the report itself.
 */
export function formatReport(snapshot: DiagnosticsSnapshot): string {
  const header = [
    'Rowyn Diagnostics — read-only',
    `Collected: ${new Date(snapshot.collected_at * 1000).toISOString()}`,
    'Scrubbed locally; redaction is best effort. Review before sharing. Nothing uploaded.',
    '',
  ].join('\n');
  const blocks: string[] = [];
  const omitted: string[] = [];
  let used = reportBytes(header);
  for (const item of snapshot.readings) {
    const block = `${item.name} [${item.state}]: ${boundedDetail(item.detail)}`;
    const size = reportBytes(block) + 2;
    if (used + size > REPORT_BYTE_LIMIT) {
      omitted.push(item.name);
      continue;
    }
    blocks.push(block);
    used += size;
  }
  const trailer = omitted.length
    ? `\n[report truncated: ${omitted.length} reading(s) omitted to stay under the export limit: ${omitted.join(', ')}]`
    : '';
  return [header, ...blocks].join('\n\n') + trailer;
}
