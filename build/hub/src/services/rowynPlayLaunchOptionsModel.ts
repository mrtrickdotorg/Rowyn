// A saved per-title profile is a choice, not a fact: nothing on the page has
// yet been applied to Steam, which keeps its own copy of each title's launch
// options in the account's localconfig.vdf. This projection compares the
// command the page would build with that recorded copy, so a card can say
// whether Steam will run it — and name the drift instead of showing the saved
// profile as though it were in effect. Pure: no reads, no writes.

export type LaunchOptionsState = 'matches' | 'differs' | 'none' | 'unknown';

export interface LaunchOptionsReconciliation {
  state: LaunchOptionsState;
  /** A word for the card: In sync / Drift / Not applied / Not checked. */
  label: string;
  /** One sentence saying what Steam will run, without repeating the text. */
  detail: string;
  /** Steam's own recorded text, when there is one to show. */
  recorded: string | null;
}

/** Steam stores whatever was pasted; spacing is not a difference a player
 * made, so runs of whitespace collapse before comparing. */
export function normalizeLaunchOptions(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

export interface LaunchOptionsInput {
  /** The command the page built for this title, gating already applied. */
  assembled: string;
  /** Steam's recorded LaunchOptions for the title; null/undefined for none. */
  recorded: string | null | undefined;
  /** Whether the per-account profile could be read at all. */
  read: boolean;
  /** A profile with nothing set builds `%command%`, which means "no options",
   * so drift is phrased differently from one that was deliberately edited. */
  defaultProfile: boolean;
}

export function reconcileLaunchOptions({
  assembled,
  recorded,
  read,
  defaultProfile,
}: LaunchOptionsInput): LaunchOptionsReconciliation {
  if (!read) {
    return {
      state: 'unknown',
      label: 'Not checked',
      detail:
        'Steam’s per-account profile could not be read, so whether this command is applied is unknown — which is not the same as not applied.',
      recorded: null,
    };
  }
  if (recorded == null || normalizeLaunchOptions(recorded) === '') {
    return {
      state: 'none',
      label: 'Not applied',
      detail:
        'Steam has no launch options recorded for this title, so the command above is not applied yet. Copy it into Steam → Properties → Launch Options.',
      recorded: null,
    };
  }
  if (normalizeLaunchOptions(recorded) === normalizeLaunchOptions(assembled)) {
    return {
      state: 'matches',
      label: 'In sync',
      detail: 'Steam has this command recorded for this title, so it is what Steam will run.',
      recorded: null,
    };
  }
  return {
    state: 'differs',
    label: 'Drift',
    detail: defaultProfile
      ? 'Steam has launch options recorded that this profile does not include, so Steam will run its own text instead of the command above.'
      : 'Steam will run the text it has recorded instead of the command above. Copy the command above into Steam → Properties → Launch Options to replace it.',
    recorded,
  };
}
