import { useEffect, useRef, useState } from 'react';
import type { BackupSnapshot } from '../services/rowynBackupModel';
import { backupFreshness, backupStatus, backupSummary } from '../services/rowynBackupModel';
import { fetchRowynBackupsSnapshot, openRowynSettings } from '../services/rowynSettings';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = { Backups: 'hub-section-backups' };

export function Backups() {
  useSectionFollow(SECTIONS);
  const [snapshot, setSnapshot] = useState<BackupSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Not checked yet. Refresh to read Kup configuration.');
  const mounted = useRef(false);
  const locked = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current++; };
  }, []);

  async function run(action: 'refresh' | 'settings') {
    if (locked.current) return;
    locked.current = true;
    const token = ++generation.current;
    setBusy(true);
    setStatus(action === 'refresh' ? 'Reading Kup configuration…' : 'Opening Kup settings…');
    if (action === 'refresh') setSnapshot(null);
    try {
      if (action === 'refresh') {
        const next = await fetchRowynBackupsSnapshot();
        if (mounted.current && token === generation.current) { setSnapshot(next); setStatus(backupSummary(next)); }
      } else {
        const result = await openRowynSettings('backups');
        if (mounted.current && token === generation.current) setStatus(result);
      }
    } catch (error) {
      if (mounted.current && token === generation.current) setStatus(`Unavailable: ${String(error)}`);
    } finally {
      locked.current = false;
      if (mounted.current && token === generation.current) setBusy(false);
    }
  }

  return <div id="hub-section-backups" className="page-content rowyn-mvp">
    <section className="glass dashboard-card"><span className="home-eyebrow">Rowyn Backups</span><h1>Backups</h1>
      <p className="card-copy">This page reads your real Kup configuration — plans, destinations, included files, and the last completed run Kup recorded — and Open Kup settings takes you straight to Kup’s own settings, where plans are created, edited, and run. Rowyn itself does not run backups or verify destination connectivity or backup contents. Versioned backups retain history; mirrors do not. A backup on the same disk does not protect against disk failure. OS rollback does not restore personal files.</p>
      <div className="home-actions">
        <button className="action-button action-button-primary" onClick={() => void run('refresh')} disabled={busy}>{busy ? 'Working…' : 'Refresh Kup status'}</button>
        <button className="action-button" onClick={() => void run('settings')} disabled={busy}>Open Kup settings</button>
      </div>
      <p role="status" className="card-copy">{status}</p>
      <section aria-labelledby="restore-guide-title">
        <h2 id="restore-guide-title">Restore files manually</h2>
        <p className="card-copy">Kup records a completed backup, but that does not prove the destination is connected or that a restore will work. Rowyn never restores files, creates folders, or runs restore commands.</p>
        <h3>Versioned backups</h3>
        <ol><li>Connect the configured backup destination and open Kup’s tray/applet.</li><li>Choose the option to browse/open backup files and select the snapshot containing the file.</li><li>Copy the desired file to a <strong>new, separate destination folder</strong> that is not the original source folder. Do not overwrite the original.</li></ol>
        <h3>Mirror backups</h3>
        <p className="card-copy">Browse the connected mirror in Dolphin and manually copy the needed file to a new, separate destination folder—not the original source folder. A mirror follows deletions and changes, so it may not contain older versions.</p>
        <h3>Test restore one file</h3>
        <ol><li>Choose one non-sensitive file you recognize from a backup.</li><li>Use the appropriate method above and copy it into a separate test folder you choose; never select the source folder or overwrite an existing file.</li><li>Open the copied file and confirm it is usable, then remove your test copy yourself if desired.</li></ol>
        <p className="card-copy">This is a manual test, not proof supplied by Rowyn. If no plan or destination is configured, first use Open Kup settings to configure one and connect its destination. The settings button remains available even when configuration cannot be read.</p>
      </section>
    </section>
    {snapshot?.plans.map((plan, index) => {
      const freshness = backupFreshness(plan.lastCompleted);
      return <section className="glass dashboard-card" key={`${plan.name}-${index}`}>
        <h2>{plan.name}</h2><p className="card-copy">Status: {backupStatus(plan)} · Type: {plan.kind === 'versioned' ? 'Versioned' : 'Mirror'}</p>
        <p className="card-copy">Destination: {plan.destination || 'Not configured'}</p>
        <p className="card-copy">Included: {plan.defaultIncluded ? 'Kup defaults apply; open settings to review included files' : plan.included.length ? plan.included.join(', ') : 'No paths listed'}</p>
        <p className="card-copy">Excluded: {plan.excluded.length ? plan.excluded.join(', ') : 'None listed'}</p>
        <p className="card-copy" data-freshness={freshness.state}>Last completed backup recorded by Kup: {plan.lastCompleted ?? 'Not reported'} ({freshness.label}; not independently verified)</p>
        {freshness.state === 'stale' || freshness.state === 'old' ? <p className="card-copy" role="status">This plan looks due for another run. Rowyn does not start backups; use Kup to run and verify one.</p> : null}
      </section>;
    })}
  </div>;
}
