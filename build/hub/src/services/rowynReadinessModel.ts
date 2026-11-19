export type ReadinessState = 'ready' | 'needs-setup' | 'attention' | 'unavailable';
export type ReadinessId = 'printing' | 'logging' | 'updates' | 'backups' | 'development';
export interface UpdateAttempt {
  startedAt: number; finishedAt: number | null; outcome: string; reasonCode: string;
  lastSuccessAt: number | null; lastStagedAt: number | null;
}
export interface ReadinessCheck { id: ReadinessId; title: string; state: ReadinessState; detail: string; updateAttempt?: UpdateAttempt }
export interface ReadinessSnapshot { checkedAt: number; checks: ReadinessCheck[] }

const titles: Record<ReadinessId, string> = {
  printing: 'Printing', logging: 'System logging', updates: 'Updates',
  backups: 'Backups', development: 'Development tools',
};
const states: ReadinessState[] = ['ready', 'needs-setup', 'attention', 'unavailable'];

type ReadinessAction = { label: string; route: '/diagnostics' | '/updates' | '/backups' | '/development' }
  | { label: string; setting: 'printers' };
export const readinessTargets: Record<ReadinessId, ReadinessAction> = {
  printing: { label: 'Open Print settings', setting: 'printers' },
  logging: { label: 'Open Diagnostics', route: '/diagnostics' },
  updates: { label: 'Open Updates', route: '/updates' },
  backups: { label: 'Open Backups', route: '/backups' },
  development: { label: 'Open Development', route: '/development' },
};

function validateAttempt(value: unknown, clock: number): UpdateAttempt {
  const invalid = () => new Error('Readiness update history is invalid.');
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  const record = value as Record<string, unknown>;
  const pairs: Record<string, string[]> = {
    running: ['attempt_started'], current: ['no_update'], staged: ['update_staged', 'metered_cap'],
    deferred: ['battery', 'metered', 'upgrade_locked'], failed: ['runtime_auth_io', 'upgrade_failed', 'unknown_failure'],
    disabled: ['policy_disabled'], invalid_policy: ['invalid_policy'], interrupted: ['cancelled'],
  };
  const fields = ['startedAt', 'finishedAt', 'outcome', 'reasonCode', 'lastSuccessAt', 'lastStagedAt'];
  if (Object.keys(record).length !== fields.length || fields.some((key) => !Object.prototype.hasOwnProperty.call(record, key))
      || typeof record.outcome !== 'string' || !Object.prototype.hasOwnProperty.call(pairs, record.outcome)
      || typeof record.reasonCode !== 'string' || !pairs[record.outcome].includes(record.reasonCode)) throw invalid();
  for (const key of ['startedAt', 'finishedAt', 'lastSuccessAt', 'lastStagedAt']) {
    const time = record[key];
    if (time !== null && (!Number.isSafeInteger(time) || (time as number) < 0 || (time as number) > clock || (time as number) > 253402300799)) throw invalid();
  }
  const attempt = record as unknown as UpdateAttempt;
  const success = attempt.outcome === 'current' || attempt.outcome === 'staged';
  if (attempt.startedAt === null || (attempt.outcome === 'running') !== (attempt.finishedAt === null)
      || (attempt.finishedAt !== null && attempt.finishedAt < attempt.startedAt)
      || (attempt.lastSuccessAt !== null && attempt.lastSuccessAt > (attempt.finishedAt ?? attempt.startedAt))
      || (success && attempt.lastSuccessAt !== attempt.finishedAt)
      || (attempt.outcome === 'staged' && attempt.lastStagedAt !== attempt.finishedAt)
      || (attempt.lastStagedAt !== null && (attempt.lastSuccessAt === null || attempt.lastStagedAt > attempt.lastSuccessAt))) throw invalid();
  return { ...attempt };
}

export function validateReadiness(value: unknown): ReadinessSnapshot {
  const invalid = () => new Error('Readiness response is invalid.');
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  const root = value as Record<string, unknown>;
  if (!Number.isSafeInteger(root.checkedAt) || (root.checkedAt as number) <= 0
      || (root.checkedAt as number) > Math.floor(Date.now() / 1000) + 60
      || !Array.isArray(root.checks) || root.checks.length !== Object.keys(titles).length) throw invalid();
  const seen = new Set<string>();
  const checks = root.checks.map((value): ReadinessCheck => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
    const check = value as Record<string, unknown>;
    if (typeof check.id !== 'string' || !Object.prototype.hasOwnProperty.call(titles, check.id)
        || seen.has(check.id) || check.title !== titles[check.id as ReadinessId]
        || typeof check.state !== 'string' || !states.includes(check.state as ReadinessState)
        || typeof check.detail !== 'string' || check.detail.length === 0 || check.detail.length > 240
        || /[\u0000-\u001f\u007f]/.test(check.detail)) throw invalid();
    if (Object.prototype.hasOwnProperty.call(check, 'updateAttempt') && check.id !== 'updates') throw invalid();
    const updateAttempt = Object.prototype.hasOwnProperty.call(check, 'updateAttempt') ? validateAttempt(check.updateAttempt, root.checkedAt as number) : undefined;
    seen.add(check.id);
    return { id: check.id as ReadinessId, title: titles[check.id as ReadinessId],
      state: check.state as ReadinessState, detail: check.detail, ...(updateAttempt ? { updateAttempt } : {}) };
  });
  return { checkedAt: root.checkedAt as number, checks };
}
