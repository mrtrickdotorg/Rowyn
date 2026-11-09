import { useEffect, useRef, useState } from 'react';
import { ExternalLink } from '../components/ExternalLink';
import { cancelInstall, fetchInstalledFlatpaks, installFlatpak, uninstallFlatpak, waitInstallJob, invalidateSharedReads, getInFlightJob, type InstalledFlatpaksResult } from '../services/liveData';
import { rowynControllerScan, openRowynLauncher, rowynSteamGamesScan } from '../services/rowynMvp';
import { LAUNCHERS, launcherState, installLauncher, createPlayStatusOwner, launchOptions, steamCompatibilityLinks, type Reading, type SteamScan } from '../services/rowynMvpModel';
import { copyText } from '../services/rowynClipboard';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = {
  Gaming: 'hub-section-gaming',
  SteamLibrary: 'hub-section-steam-library',
  Compatibility: 'hub-section-compatibility',
  Controllers: 'hub-section-controllers',
};

// Confirmation lives in the page: a webview without script dialogs answers
// window.confirm with false, which silently cancelled installs and removals.
type PendingLauncher = { kind: 'install' | 'remove'; id: string; name: string };

function launcherStateLabel(scan: InstalledFlatpaksResult | null, state: string): string {
  if (!scan) return 'Reading installed launchers…';
  if (state === 'unavailable') return 'Unavailable — a Flatpak scope could not be read';
  return state;
}

function launcherStateDetail(scan: InstalledFlatpaksResult | null, state: string): string | null {
  if (!scan) return 'Install and removal stay disabled until the read finishes.';
  if (state !== 'unavailable') return null;
  return `Scopes reported as unreadable: ${scan.failedScopes.join(', ') || 'unknown'}. Choose Refresh launchers to try again; unknown is not the same as not installed.`;
}

export function Play() {
  useSectionFollow(SECTIONS);
  const [scan, setScan] = useState<InstalledFlatpaksResult | null>(null);
  const [steamScan, setSteamScan] = useState<SteamScan | null>(null);
  const [controllers, setControllers] = useState<Reading[] | null>(null);
  const [status, setStatus] = useState('Reading installed launchers…');
  const [busy, setBusy] = useState(false);
  const [activeInstall, setActiveInstall] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [boundary, setBoundary] = useState<'flatpak' | 'host'>('flatpak');
  const [game, setGame] = useState('');
  const [gaming, setGaming] = useState(true);
  const [mimalloc, setMimalloc] = useState(false);
  const [pending, setPending] = useState<PendingLauncher | null>(null);
  const lock = useRef(false);
  const statusOwner = useRef(createPlayStatusOwner(setStatus));
  const options = launchOptions(boundary, gaming, mimalloc);
  const tracked = activeInstall ?? getInFlightJob('install');
  async function refreshLaunchers() {
    invalidateSharedReads('installed-flatpaks');
    const next = await fetchInstalledFlatpaks(); setScan(next);
    return !next ? 'Launcher scan unavailable. Use the installed Rowyn Hub and retry.' : next.failedScopes.length
      ? `Partial scan: ${next.failedScopes.join(', ')} unavailable. Unknown is not the same as not installed.` : 'Launcher scan finished.';
  }
  async function run(pendingMessage: string, action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { await statusOwner.current.run(pendingMessage, action); }
    finally { setActiveInstall(getInFlightJob('install') ?? null); lock.current = false; setBusy(false); }
  }
  useEffect(() => {
    // One read when the page opens so the four cards show this account's real
    // state instead of "unavailable" until someone presses refresh. No timers.
    void run('Reading installed launchers…', refreshLaunchers);
  }, []);
  async function confirmPending() {
    const action = pending;
    if (!action) return;
    setPending(null);
    const launcher = LAUNCHERS.find((entry) => entry.id === action.id);
    if (!launcher) return;
    if (action.kind === 'install') {
      void run(`Installing ${launcher.name}…`, async () => {
        const result = await installLauncher(launcher.id, { install: installFlatpak, wait: waitInstallJob }, setActiveInstall);
        setActiveInstall(null);
        const refreshed = await refreshLaunchers(); return `${result} ${refreshed}`;
      });
      return;
    }
    void run(`Removing ${launcher.name}…`, async () => {
      const result = await uninstallFlatpak(launcher.id);
      const refreshed = await refreshLaunchers(); return `${result} ${refreshed}`;
    });
  }
  const installedCount = scan ? LAUNCHERS.filter((launcher) => launcherState(launcher.id, scan) === 'installed').length : null;
  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card" id="hub-section-gaming">
      <span className="home-eyebrow">Rowyn Play · MVP</span><h1>Play</h1>
      <p className="card-copy">Four launchers, installed as system-wide Flatpaks from Flathub — the same installation Discover uses — with the same jobs as Apps. No native package installs or system tuning. A launcher being installed does not establish game compatibility. Steam game discovery is an explicit, read-only scan of local app manifests; it does not validate or launch games.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading installed launchers…', refreshLaunchers)}>Refresh launchers</button>
        {tracked && <button className="action-button" disabled={cancelBusy} onClick={async () => {
          setCancelBusy(true);
          try { await statusOwner.current.cancel(cancelInstall); }
          finally { setCancelBusy(false); }
        }}>Cancel install</button>}
        {tracked && !busy && <button className="action-button" onClick={() => run('Reconnecting to install…', () => waitInstallJob(tracked, 1800))}>Resume install status</button>}
      </div>
      <p className="card-copy" role="status">{status}</p>
      {installedCount !== null && <p className="card-copy">{installedCount} of {LAUNCHERS.length} launchers installed for this account.</p>}
      {busy && <p className="card-copy" role="progressbar" aria-label="Play action in progress">Working… Install jobs remain cancellable here or in Apps.</p>}
      <div className="rowyn-launchers">{LAUNCHERS.map((launcher) => {
        const state = launcherState(launcher.id, scan);
        const detail = launcherStateDetail(scan, state);
        return <article className="rowyn-launcher" key={launcher.id}><h2>{launcher.name}</h2>
          <p className="card-copy">{launcher.description}</p><p className="rowyn-state">{launcherStateLabel(scan, state)}</p><code>{launcher.id}</code>
          {detail && <p className="card-copy">{detail}</p>}
          <div className="home-actions"><button className="action-button" disabled={busy || state !== 'installed'} onClick={() => run(`Opening ${launcher.name}…`, () => openRowynLauncher(launcher.id))}>Open {launcher.name}</button>
          <button className="action-button" disabled={busy || !!pending || state !== 'not installed' || !!tracked} onClick={() => setPending({ kind: 'install', id: launcher.id, name: launcher.name })}>Install {launcher.name}</button>
          <button className="action-button" disabled={busy || !!pending || state !== 'installed' || !!tracked} onClick={() => setPending({ kind: 'remove', id: launcher.id, name: launcher.name })}>Remove {launcher.name}</button></div>
        </article>;
      })}</div>
      {pending && <section className="glass dashboard-card" role="alertdialog" aria-labelledby="rowyn-play-confirm">
        <h2 id="rowyn-play-confirm">{pending.kind === 'install' ? `Install ${pending.name}?` : `Remove ${pending.name}?`}</h2>
        <p className="card-copy">{pending.kind === 'install'
          ? `Downloads ${pending.name} from Flathub into the system-wide Flatpak installation — the same one Discover uses. This can take several minutes, and installing it does not establish game compatibility.`
          : `Removes ${pending.name} from the system-wide Flatpak installation. Game files, saves and compatibility data are not deleted.`}</p>
        <div className="home-actions">
          <button className="action-button" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          <button className="action-button" disabled={busy} onClick={() => void confirmPending()}>
            {pending.kind === 'install' ? `Install ${pending.name}` : `Remove ${pending.name}`}
          </button>
        </div>
      </section>}
    </section>
    <section className="glass dashboard-card" id="hub-section-steam-library"><h2>Installed Steam games</h2>
      <p className="card-copy">Local metadata only: scans Steam app manifests in common native and Flatpak library paths when you ask. It never reads game files, sends a network request, launches a title, or rates compatibility. ProtonDB links are community reports and do not mean Rowyn tested the game.</p>
      <button className="action-button" disabled={busy} onClick={() => run('Reading Steam library metadata…', async () => {
        setSteamScan(null);
        const next = await rowynSteamGamesScan(); setSteamScan(next);
        if (!next.available) return 'Steam was not detected in the common native or Flatpak paths. A custom library location may not have been scanned.';
        if (next.warnings) return `Partial scan: ${next.warnings} library item(s) were skipped or unreadable; detected titles are listed below, but others may be missing.`;
        return next.games.length ? `Detected ${next.games.length} installed title(s) from local Steam manifests. Compatibility was not tested.` : 'No fully installed titles found in the scanned libraries; other locations may not have been scanned.';
      })}>Scan Steam games (local only)</button>
      {!steamScan && <p className="card-copy">Not scanned yet. No scan runs automatically.</p>}
      {steamScan && !steamScan.available && <p className="card-copy">Steam was not detected in common locations. No titles are inferred from this result.</p>}
      {steamScan && steamScan.available && !steamScan.games.length && <p className="card-copy">No fully installed titles were detected in the scanned metadata.</p>}
      {steamScan?.games.map((item) => {
        const links = steamCompatibilityLinks(item.app_id);
        return <article className="rowyn-launcher" key={item.app_id}><h3>{item.name}</h3>
          <p className="card-copy">Steam App ID {item.app_id} · detected from local metadata; compatibility not tested by Rowyn.</p>
          <div className="home-actions"><ExternalLink href={links.protondb}>ProtonDB community reports (not a guarantee)</ExternalLink>
            <ExternalLink href={links.steamStore}>Steam store page</ExternalLink></div>
        </article>;
      })}
      {!!steamScan?.warnings && <p className="card-copy" role="status">Partial scan: skipped or unreadable library metadata may hide additional titles.</p>}
    </section>
    <section className="glass dashboard-card" id="hub-section-compatibility"><h2>Compatibility &amp; anti-cheat</h2>
      <p className="card-copy">Guidance reviewed 2026-10-31; not Rowyn-validated game results. Linux/Proton support changes with game, launcher and anti-cheat updates. Kernel-level Windows anti-cheat generally cannot run under Proton. Easy Anti-Cheat and BattlEye support requires the game developer to enable Linux/Proton support; the middleware name alone proves nothing. Check the exact title and multiplayer mode before buying or installing.</p>
      <p className="card-copy">Provenance: <ExternalLink href="https://partner.steamgames.com/doc/steamdeck/proton">Valve’s Proton / anti-cheat documentation</ExternalLink> (developer guidance), <ExternalLink href="https://www.protondb.com/">ProtonDB</ExternalLink> and <ExternalLink href="https://areweanticheatyet.com/">Are We Anti-Cheat Yet?</ExternalLink> (community reports, not guarantees). Links require a web browser; no compatibility lookup or network probe runs in this page.</p>
      <label>Game label (local only; never executed)<input value={game} maxLength={80} onChange={(event) => setGame(event.target.value)} placeholder="Your game" /></label>
      <label>Where does this game run?<select value={boundary} onChange={(event) => setBoundary(event.target.value as 'flatpak' | 'host')}><option value="flatpak">Flatpak Steam / sandboxed launcher</option><option value="host">Host-native Steam / host command</option></select></label>
      <label><input type="checkbox" checked={gaming} disabled={boundary === 'flatpak'} onChange={(event) => setGaming(event.target.checked)} /> Use Rowyn gaming slice / GameMode when available</label>
      <label><input type="checkbox" checked={mimalloc} disabled={boundary === 'flatpak'} onChange={(event) => setMimalloc(event.target.checked)} /> Try mimalloc for this game only (experimental; may regress or warn on 32-bit games)</label>
      <p className="card-copy">Host wrappers /usr/bin/rowyn-game-launch and /usr/bin/rowyn-mimalloc-run are not generally accessible inside Flatpak Steam. Do not paste host paths there or use flatpak-spawn to escape the sandbox. The Flatpak-safe default below adds no host wrapper. The host-native example is for an already installed host launcher; this Hub does not install one. Undo by removing the launch options.</p>
      <p>{game || 'Selected game'} — paste into that game’s launch options:</p><pre aria-label="Launch options">{options}</pre>
      <button className="action-button" disabled={busy} onClick={() => run('Copying launch options…', async () => {
        const result = await copyText(options);
        return result.ok ? 'Launch options copied. No configuration was changed.' : result.reason;
      })}>Copy launch options</button>
    </section>
    <section className="glass dashboard-card" id="hub-section-controllers"><h2>Controllers</h2>
      <p className="card-copy">Connect or pair a controller, then rescan. This reads the shipped controller checker only; it does not pair devices, load drivers, change permissions, or claim input testing passed.</p>
      <button className="action-button" disabled={busy} onClick={() => run('Reading controller readiness…', async () => {
        setControllers(null); const next = await rowynControllerScan(); setControllers(next); return 'Controller scan finished. Review findings below.';
      })}>Rescan controllers</button>
      {!controllers && <p className="card-copy">Not checked or unavailable. Scan errors appear above.</p>}
      {controllers?.map((item) => <div key={item.name}><h3>{item.name} <span className="rowyn-state">{item.state}</span></h3><pre>{item.detail}</pre></div>)}
    </section>
  </div>;
}
