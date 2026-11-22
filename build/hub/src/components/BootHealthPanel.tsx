import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { BootQuarantineClear } from './BootQuarantineClear';
import { rowynBootHealthSnapshot } from '../services/rowynBootHealth';
import type { BootHealthSnapshot } from '../services/rowynBootHealthModel';
import { reportStatusSource } from '../services/rowynStatusSources';
import '../styles/rowynMvp.css';

/**
 * The boot-health record. This panel only reads it; staging, rollback and
 * restarts stay elsewhere. The single write — clearing one quarantine after an
 * explicit confirmation — lives in BootQuarantineClear.
 */
export function BootHealthPanel({ compact = false }: { compact?: boolean }) {
  const [snapshot, setSnapshot] = useState<BootHealthSnapshot | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const mounted = useRef(false);
  const generation = useRef(0);

  async function read() {
    const token = ++generation.current;
    setBusy(true);
    setError('');
    try {
      const next = await rowynBootHealthSnapshot();
      if (!mounted.current || token !== generation.current) return;
      setSnapshot(next);
    } catch (cause) {
      if (!mounted.current || token !== generation.current) return;
      setSnapshot(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (mounted.current && token === generation.current) setBusy(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void read();
    return () => { mounted.current = false; generation.current++; };
  }, []);

  // Absence of the record is not a failure, but it does mean this host has no
  // boot-health source to show.
  useEffect(() => {
    reportStatusSource('bootHealth', snapshot?.available === true);
  }, [snapshot]);

  const state = error ? 'unavailable' : snapshot?.state ?? 'pending';
  const label = error ? 'Boot health unavailable' : snapshot?.label ?? 'Reading boot-health record…';
  const guidance = error
    ? `${error} Diagnostics still reports the raw record.`
    : snapshot?.guidance ?? 'Reading the record written by the image.';
  return <section
    aria-label="Boot health"
    data-state={state}
    className={`glass dashboard-card rowyn-boot-health${compact ? ' is-compact' : ''}`}
  >
    <strong>{label}</strong>
    <p className="card-copy">{guidance}</p>
    {snapshot?.lastBoot.at && (
      <p className="card-copy">
        Last boot recorded {snapshot.lastBoot.at}
        {snapshot.lastBoot.healthy === true ? ' and passed its checks' : ''}
        {snapshot.lastBoot.healthy === false ? ' and failed its checks' : ''}
      </p>
    )}
    {!!snapshot?.quarantined.length && (
      <ul>
        {snapshot.quarantined.map((item) => (
          <li key={item.digest}>
            <code className="break-all">{item.digest}</code> — {item.reasonLabel}
            {item.at ? ` (${item.at})` : ''}
            <BootQuarantineClear
              digest={item.digest}
              reasonLabel={item.reasonLabel}
              onCleared={() => void read()}
            />
          </li>
        ))}
      </ul>
    )}
    {!!snapshot?.failures.length && (
      <ul>
        {snapshot.failures.map((item) => (
          <li key={item.digest}><code className="break-all">{item.digest}</code> — {item.count} failed boot(s)</li>
        ))}
      </ul>
    )}
    {!!snapshot?.rollbacks.length && (
      <ul>
        {snapshot.rollbacks.map((item) => (
          <li key={item.digest}>
            <code className="break-all">{item.digest}</code> — {item.attempts} rollback attempt(s)
            {item.lastError ? `; last error: ${item.lastError}` : ''}
          </li>
        ))}
      </ul>
    )}
    <div className="home-actions">
      <button className="action-button" type="button" disabled={busy} onClick={() => void read()}>
        {busy ? 'Reading…' : 'Refresh boot health'}
      </button>
      <Link to="/diagnostics?section=Diagnostics">Open Diagnostics</Link>
    </div>
    <p className="card-copy">
      Read-only apart from clearing a quarantine, which asks for confirmation first. Clearing only
      lets that image be staged again; it never downloads or restarts anything.
    </p>
  </section>;
}
