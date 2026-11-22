/**
 * Strict projection of /var/lib/rowyn/boot-health.json.
 *
 * The record is written by the image's boot-health helper (root, mode 0644),
 * so the Hub can read it unprivileged but must not trust its shape: every
 * field is validated, lists are bounded, and anything unrecognised degrades to
 * "unavailable" rather than being displayed as a healthy system.
 */
export const BOOT_HEALTH_SCHEMA = 1;
export const MAX_ROWS = 50;

const DIGEST = /^sha256:[0-9a-f]{64}$/;

export type BootHealthState = 'available' | 'pending' | 'attention' | 'unavailable';

export interface BootHealthFailure { digest: string; count: number }
export interface BootHealthQuarantine { digest: string; reason: string; reasonLabel: string; at: string | null }
export interface BootHealthRollback { digest: string; attempts: number; lastError: string | null }
export interface BootHealthSnapshot {
  state: BootHealthState;
  available: boolean;
  knownGood: number;
  failures: BootHealthFailure[];
  quarantined: BootHealthQuarantine[];
  rollbacks: BootHealthRollback[];
  lastBoot: { digest: string | null; healthy: boolean | null; at: string | null };
  label: string;
  guidance: string;
}

const REASON_LABELS: Record<string, string> = {
  'unhealthy-boot': 'Failed its boot checks',
  downgrade: 'Older than the booted image',
  manual: 'Quarantined by an administrator',
};

const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const digests = (value: unknown): string[] => {
  const entries = record(value);
  if (!entries) return [];
  return Object.keys(entries).filter((key) => DIGEST.test(key)).sort();
};

function unavailable(reason: string): BootHealthSnapshot {
  return {
    state: 'unavailable',
    available: false,
    knownGood: 0,
    failures: [],
    quarantined: [],
    rollbacks: [],
    lastBoot: { digest: null, healthy: null, at: null },
    label: 'Boot health unavailable',
    guidance: reason,
  };
}

function project(snapshot: Omit<BootHealthSnapshot, 'state' | 'label' | 'guidance' | 'available'>): BootHealthSnapshot {
  const base = { ...snapshot, available: true };
  if (snapshot.quarantined.length) {
    return {
      ...base,
      state: 'attention',
      label: `${snapshot.quarantined.length} deployment(s) quarantined`,
      guidance:
        'A staged image failed its boot checks and was moved back. Known-good images are never re-judged; clear one here, with confirmation, to let that image be staged again.',
    };
  }
  if (snapshot.failures.length) {
    return {
      ...base,
      state: 'attention',
      label: 'Boot checks failing',
      guidance:
        'An image has failed a boot check. Three failed boots quarantine it and roll the machine back to the previous deployment.',
    };
  }
  if (snapshot.knownGood === 0) {
    return {
      ...base,
      state: 'pending',
      label: 'Not judged yet',
      guidance:
        'No deployment has been confirmed healthy yet. The check runs shortly after each boot and records the result here.',
    };
  }
  return {
    ...base,
    state: 'available',
    label: `${snapshot.knownGood} verified deployment(s)`,
    guidance:
      'These images reached a working desktop and are never judged again. This is a first-boot gate, not hardware certification.',
  };
}

export function parseBootHealth(raw: unknown): BootHealthSnapshot {
  if (typeof raw !== 'string' || !raw.trim()) {
    return unavailable('No boot-health record has been written on this system yet.');
  }
  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    return unavailable('The boot-health record is not valid JSON; run Diagnostics for the raw reading.');
  }
  const root = record(document);
  if (!root) return unavailable('The boot-health record is not an object.');
  if (root.schema_version !== BOOT_HEALTH_SCHEMA) {
    return unavailable(`The boot-health record uses an unknown schema (${String(root.schema_version)}).`);
  }

  const knownGood = digests(root.known_good).length;

  const failures: BootHealthFailure[] = Object.entries(record(root.failures) ?? {})
    .filter(([digest, count]) => DIGEST.test(digest) && typeof count === 'number' && count > 0)
    .map(([digest, count]) => ({ digest, count: Math.min(count as number, 99) }))
    .sort((a, b) => b.count - a.count || a.digest.localeCompare(b.digest))
    .slice(0, MAX_ROWS);

  const quarantined: BootHealthQuarantine[] = Object.entries(record(root.quarantined) ?? {})
    .filter(([digest]) => DIGEST.test(digest))
    .map(([digest, entry]) => {
      const item = record(entry) ?? {};
      const reason = typeof item.reason === 'string' ? item.reason : 'manual';
      return {
        digest,
        reason,
        reasonLabel: REASON_LABELS[reason] ?? 'Quarantined',
        at: typeof item.quarantined_at === 'string' ? item.quarantined_at : null,
      };
    })
    .sort((a, b) => a.digest.localeCompare(b.digest))
    .slice(0, MAX_ROWS);

  const rollbacks: BootHealthRollback[] = Object.entries(record(root.rollback) ?? {})
    .filter(([digest]) => DIGEST.test(digest))
    .map(([digest, entry]) => {
      const item = record(entry) ?? {};
      return {
        digest,
        attempts: typeof item.attempts === 'number' ? Math.max(0, Math.min(item.attempts, 99)) : 0,
        lastError: typeof item.last_error === 'string' ? item.last_error : null,
      };
    })
    .sort((a, b) => b.attempts - a.attempts || a.digest.localeCompare(b.digest))
    .slice(0, MAX_ROWS);

  const boot = record(root.last_boot) ?? {};
  const lastBoot = {
    digest: typeof boot.digest === 'string' && DIGEST.test(boot.digest) ? boot.digest : null,
    healthy: typeof boot.healthy === 'boolean' ? boot.healthy : null,
    at: typeof boot.recorded_at === 'string' ? boot.recorded_at : null,
  };

  return project({ knownGood, failures, quarantined, rollbacks, lastBoot });
}
