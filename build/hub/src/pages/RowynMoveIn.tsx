import { useRef, useState } from 'react';
import { rowynMigrateScan, rowynMigrateMount, rowynMigrateImport } from '../services/rowynMigrate';
import { MIGRATE_CATEGORIES, categorySummary, type ImportReport, type WindowsInstall } from '../services/rowynMigrateModel';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = { MoveIn: 'hub-section-move-in' };

export function MoveIn() {
  useSectionFollow(SECTIONS);
  const [installs, setInstalls] = useState<WindowsInstall[] | null>(null);
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});
  const [report, setReport] = useState<ImportReport | null>(null);
  const [status, setStatus] = useState('Not scanned yet. The scan only reads partition and folder names.');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);

  async function run(pendingMessage: string, action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setStatus(pendingMessage);
    try { setStatus(await action()); }
    catch (error) { setStatus(`Failed: ${String(error)}`); }
    finally { lock.current = false; setBusy(false); }
  }

  function toggle(profile: string, category: string) {
    setSelected((current) => {
      const next = { ...current };
      const set = new Set(next[profile] ?? MIGRATE_CATEGORIES.map((entry) => entry.key));
      if (set.has(category)) set.delete(category); else set.add(category);
      next[profile] = set;
      return next;
    });
  }

  function chosen(profile: string): Set<string> {
    return selected[profile] ?? new Set(MIGRATE_CATEGORIES.map((entry) => entry.key));
  }

  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card" id="hub-section-move-in">
      <span className="home-eyebrow">Rowyn Move In</span><h1>Move in from Windows</h1>
      <p className="card-copy">Bring your files over from the Windows installation on this machine. The Windows drive is only ever read — mounting is read-only, and importing copies; nothing on the Windows side is changed, moved or deleted. Existing files here are never overwritten: a file that already exists is skipped and reported. Programs themselves do not move (Windows apps do not run on Linux); documents, pictures and bookmarks do.</p>
      <p className="card-copy">This is a one-time import, not a sync: files living in OneDrive will not keep updating here afterwards. If you keep using OneDrive, connect it afterwards — the Coming from Windows guide explains the rclone mount and sync options honestly.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Scanning for a Windows installation…', async () => {
        const next = await rowynMigrateScan(); setInstalls(next); setReport(null);
        if (!next.length) return 'No NTFS partitions were found. If Windows is on this machine, its drive may be BitLocker-locked or not visible to this scan.';
        const found = next.filter((install) => install.profiles.length);
        return found.length
          ? `Found ${found.length} Windows installation(s) with ${found.reduce((sum, install) => sum + install.profiles.length, 0)} user profile(s).`
          : 'NTFS partition(s) found, but none are mounted with a visible Windows installation yet. Mount one read-only below, or open it once in Files (Dolphin).';
      })}>Scan for Windows</button></div>
      <p className="card-copy" role="status">{status}</p>
      {installs?.map((install) => <article className="rowyn-launcher" key={install.device}>
        <h2>{install.device}</h2>
        {!install.mounted && <><p className="card-copy">Not mounted. Mounting is read-only: Rowyn can look, never touch.</p>
          <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run(`Mounting ${install.device} read-only…`, async () => {
            const next = await rowynMigrateMount(install.device); setInstalls(next);
            const after = next.find((entry) => entry.device === install.device);
            return after?.profiles.length ? `Mounted. ${after.profiles.length} profile(s) found.` : 'Mounted, but no Windows installation was visible on it.';
          })}>Mount read-only</button></div></>}
        {install.mounted && !install.profiles.length && <p className="card-copy">Mounted at {install.mountpoint}, but it does not look like a Windows installation (no Windows/Users folders).</p>}
        {install.profiles.map((profile) => <div key={profile}>
          <h3>{profile}</h3>
          {MIGRATE_CATEGORIES.map((category) => <label key={category.key}><input type="checkbox" checked={chosen(profile).has(category.key)} onChange={() => toggle(profile, category.key)} /> {category.label} <span className="card-copy">— {category.detail}</span></label>)}
          <div className="home-actions"><button className="action-button" disabled={busy || !install.mountpoint || chosen(profile).size === 0} onClick={() => run(`Importing ${profile}’s files…`, async () => {
            const next = await rowynMigrateImport(install.mountpoint ?? '', profile, [...chosen(profile)]);
            setReport(next);
            return next.detail;
          })}>Import selected for {profile}</button></div>
        </div>)}
      </article>)}
      {report && <section className="glass dashboard-card"><h2>Import report — {report.profile}</h2>
        <p className="card-copy">{report.detail}</p>
        {report.categories.map((category) => <div key={category.category}>
          <p className="card-copy">{categorySummary(category)}</p>
          {category.reasons.map((reason) => <p className="card-copy" key={reason}>· {reason}</p>)}
        </div>)}
        <p className="card-copy">Bookmarks: open your browser’s bookmark manager and import the HTML file from Documents → Bookmarks. Files copied from Windows keep their names; anything already here was skipped, not replaced.</p>
      </section>}
    </section>
  </div>;
}
