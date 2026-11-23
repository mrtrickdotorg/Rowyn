import { useEffect, useRef, useState } from 'react';
import { ExternalLink } from '../components/ExternalLink';
import { cancelInstall, fetchInstalledFlatpaks, installFlatpak, uninstallFlatpak, waitInstallJob, invalidateSharedReads, getInFlightJob, type InstalledFlatpaksResult } from '../services/liveData';
import { rowynControllerScan, openRowynLauncher, rowynSteamGamesScan, rowynSteamRecentPlay, rowynSteamShaderCache, rowynSteamPlayStatus, rowynSteamPlayApply, rowynDisplayModesScan, rowynVulkanLayersScan, rowynVulkanLayerInstall, rowynMangohudConfig, rowynSunshineStatus, rowynSunshineFirewallOpen, rowynSunshineAutostartEnable, rowynGamescopeStatus, rowynGamemodeStatus, rowynGamingHelpersStatus, rowynGamingTuneApply, type SteamPlayStatus, type DisplayModes, type VulkanLayers, type MangohudConfig, type SunshineStatus, type GamescopeStatus, type GamemodeStatus, type RecentPlay, type ShaderCacheStatus, type ControllerReport, type GamingHelpers } from '../services/rowynMvp';
import { LAUNCHERS, launcherState, installLauncher, createPlayStatusOwner, launchOptions, steamCompatibilityLinks, compatVerdict, compatReviewedOn, compatVerdictCount, type SteamScan } from '../services/rowynMvpModel';
import { loadProfiles, saveProfiles, profileFor, isDefaultProfile, assembleProfileOptions, type GameProfile } from '../services/rowynPlayProfilesModel';
import { openRowynSettings } from '../services/rowynSettings';
import { playSetupSteps, SETUP_MARKERS } from '../services/rowynPlaySetupModel';
import { formatPlaytime, formatLastPlayed, recentPlayStatus } from '../services/rowynPlayRecentModel';
import { shaderPreCachingLabel, SHADER_PRE_CACHING_TIP } from '../services/rowynPlayShaderModel';
import { mangohudConfigLabel, mangohudConfigPath, MANGOHUD_READ_NOTE, MANGOHUD_RECOMMENDED } from '../services/rowynPlayMangohudModel';
import { coverInitials, steamLibraryEntries, filterSteamLibrary, steamLibraryCoverage, steamLibraryCoverPlan, steamLibraryCoverageNote, steamLibraryProvenance, steamStateChip, type SteamStateFilter } from '../services/rowynSteamLibraryModel';
import { CONTROLLER_READ_NOTE, controllerAdvice, controllerSummary } from '../services/rowynControllerModel';
import { HELPER_EXAMPLES, GAMING_HELPERS_NOTE, TUNE_ADMIN_NOTE, TUNE_OFF_NOTE, HOST_ONLY_NOTE, gamingTuneLabel, tuneIsOn } from '../services/rowynPlayHelpersModel';
import { copyText } from '../services/rowynClipboard';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = {
  Gaming: 'hub-section-gaming',
  SteamLibrary: 'hub-section-steam-library',
  Compatibility: 'hub-section-compatibility',
  Streaming: 'hub-section-streaming',
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
  const [libraryQuery, setLibraryQuery] = useState('');
  const [libraryFilter, setLibraryFilter] = useState<SteamStateFilter>('all');
  const [recentPlay, setRecentPlay] = useState<RecentPlay | null>(null);
  const [recentFailed, setRecentFailed] = useState(false);
  const [shaderCache, setShaderCache] = useState<ShaderCacheStatus | null>(null);
  const [shaderFailed, setShaderFailed] = useState(false);
  const [mangohudConfig, setMangohudConfig] = useState<MangohudConfig | null>(null);
  const [mangohudFailed, setMangohudFailed] = useState(false);
  const [controllers, setControllers] = useState<ControllerReport | null>(null);
  const [status, setStatus] = useState('Reading installed launchers…');
  const [busy, setBusy] = useState(false);
  const [activeInstall, setActiveInstall] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [boundary, setBoundary] = useState<'flatpak' | 'host'>('flatpak');
  const [game, setGame] = useState('');
  const [gaming, setGaming] = useState(true);
  const [mimalloc, setMimalloc] = useState(false);
  const [mangohud, setMangohud] = useState(false);
  const [vkbasalt, setVkbasalt] = useState(false);
  const [gameMode, setGameMode] = useState(false);
  const [launchGamemode, setLaunchGamemode] = useState(false);
  const [steamPlay, setSteamPlay] = useState<SteamPlayStatus | null>(null);
  const [layers, setLayers] = useState<VulkanLayers | null>(null);
  const [displays, setDisplays] = useState<DisplayModes | null>(null);
  const [profiles, setProfiles] = useState<Record<string, GameProfile>>(() => loadProfiles());
  const [profileWarning, setProfileWarning] = useState<string | null>(null);
  const [sunshine, setSunshine] = useState<SunshineStatus | null>(null);
  const [helpers, setHelpers] = useState<GamingHelpers | null>(null);
  const [gamescope, setGamescope] = useState<GamescopeStatus | null>(null);
  const [gamescopeFailed, setGamescopeFailed] = useState(false);
  const [gamemodeStatus, setGamemodeStatus] = useState<GamemodeStatus | null>(null);
  const [gamemodeFailed, setGamemodeFailed] = useState(false);
  // Which of the four on-open reads failed. A later successful re-read wins
  // over a stale flag, so a manual Check always clears its own step.
  const [checkFailure, setCheckFailure] = useState<{ steamPlay?: boolean; layers?: boolean; displays?: boolean; controllers?: boolean }>({});

  function updateProfile(appId: string, patch: Partial<GameProfile>) {
    setProfiles((current) => {
      const next = { ...current, [appId]: { ...profileFor(current, appId), ...patch } };
      setProfileWarning(saveProfiles(next));
      return next;
    });
  }
  const [pending, setPending] = useState<PendingLauncher | null>(null);
  const lock = useRef(false);
  const statusOwner = useRef(createPlayStatusOwner(setStatus));
  // GameMode only changes the string when the sandbox it applies to actually
  // carries the client, so an unusable flag never reaches the copy button.
  const gamemodeUsable = gamemodeStatus?.available === true;
  const gamemodeDaemon = gamemodeStatus?.daemon_installed === true;
  const options = launchOptions(boundary, gaming, mimalloc, mangohud, vkbasalt, gamemodeUsable ? gameMode : false);
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
  useEffect(() => {
    // Host gamescope and GameMode availability gate per-game options, so both
    // are read once when the page opens. Bounded reads, never timers.
    let cancelled = false;
    rowynGamescopeStatus()
      .then((next) => { if (!cancelled) setGamescope(next); })
      .catch(() => { if (!cancelled) setGamescopeFailed(true); });
    rowynGamemodeStatus()
      .then((next) => { if (!cancelled) setGamemodeStatus(next); })
      .catch(() => { if (!cancelled) setGamemodeFailed(true); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    // The four read-only checks run once when the page opens, so the setup
    // strip reports this machine instead of "not checked yet". Each is a
    // bounded native read; the manual buttons remain for a fresh read, and
    // nothing here is a timer.
    let cancelled = false;
    const failed = (key: 'steamPlay' | 'layers' | 'displays' | 'controllers') => {
      if (!cancelled) setCheckFailure((current) => ({ ...current, [key]: true }));
    };
    rowynSteamPlayStatus().then((next) => { if (!cancelled) setSteamPlay(next); }).catch(() => failed('steamPlay'));
    rowynVulkanLayersScan().then((next) => { if (!cancelled) setLayers(next); }).catch(() => failed('layers'));
    rowynDisplayModesScan().then((next) => { if (!cancelled) setDisplays(next); }).catch(() => failed('displays'));
    rowynControllerScan().then((next) => { if (!cancelled) setControllers(next); }).catch(() => failed('controllers'));
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    // Steam's own play history: read once so the Steam section opens with it
    // instead of an empty list. Read-only and bounded like the checks above;
    // the refresh button below is the only second read.
    let cancelled = false;
    rowynSteamRecentPlay()
      .then((next) => { if (!cancelled) setRecentPlay(next); })
      .catch(() => { if (!cancelled) setRecentFailed(true); });
    // A6: the Shader Pre-Caching flag is a second bounded read of the same
    // Steam configuration, so it opens with this machine's setting too.
    rowynSteamShaderCache()
      .then((next) => { if (!cancelled) setShaderCache(next); })
      .catch(() => { if (!cancelled) setShaderFailed(true); });
    // A7: the config read is bounded and read-only too, so the setup strip can
    // project it instead of leaving a step that never resolves.
    rowynMangohudConfig()
      .then((next) => { if (!cancelled) setMangohudConfig(next); })
      .catch(() => { if (!cancelled) setMangohudFailed(true); });
    return () => { cancelled = true; };
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
  // B1: the strip is a projection of the readings above, never a second read.
  const setupSteps = playSetupSteps({
    steam: scan ? launcherState('com.valvesoftware.Steam', scan) : 'pending',
    steamPlay,
    steamPlayFailed: checkFailure.steamPlay === true,
    layers,
    layersFailed: checkFailure.layers === true,
    controllers: controllers?.findings ?? null,
    controllersFailed: checkFailure.controllers === true,
    shaderCache, shaderFailed,
    recentPlay, recentFailed,
    mangohudConfig, mangohudFailed,
  });
  // The card list is built once per scan: one app-id verdict lookup per title,
  // then search, curated-state filter and the cover budget run over it.
  const libraryEntries = steamScan ? steamLibraryEntries(steamScan.games, compatVerdict) : [];
  const libraryVisible = filterSteamLibrary(libraryEntries, libraryQuery, libraryFilter);
  const libraryCoverPlan = steamLibraryCoverPlan(libraryVisible);
  const libraryCoverage = steamLibraryCoverage(libraryEntries);
  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card" id="hub-section-gaming">
      <span className="home-eyebrow">Rowyn Play · MVP</span><h1>Play</h1>
      <p className="card-copy">Four launchers, installed as system-wide Flatpaks from Flathub — the same installation Discover uses — with the same jobs as Apps. No native package installs or system tuning. A launcher being installed does not establish game compatibility. Steam game discovery is an explicit, read-only scan of local app manifests; it does not validate or launch games.</p>
      <section className="rowyn-setup" aria-label="Get set up to play">
        <p className="home-eyebrow">Get set up to play</p>
        <ol>{setupSteps.map((step) => <li key={step.key} className={`is-${step.state}`}>
          <span className="rowyn-setup-marker" aria-hidden="true">{SETUP_MARKERS[step.state]}</span>
          <span className="rowyn-setup-body"><strong>{step.label}</strong><span className="rowyn-setup-hint">{step.hint}</span></span>
        </li>)}</ol>
      </section>
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
      {gamemodeDaemon && <p className="card-copy"><label><input type="checkbox" checked={launchGamemode} onChange={(event) => setLaunchGamemode(event.target.checked)} /> Start launchers under GameMode (whole session: Rowyn&apos;s performance power profile and low-latency audio stay on while the launcher runs)</label></p>}
      {gamemodeStatus && !gamemodeDaemon && <p className="card-copy">GameMode&apos;s daemon (gamemoded) was not found, so launchers start normally.</p>}
      <div className="rowyn-launchers">{LAUNCHERS.map((launcher) => {
        const state = launcherState(launcher.id, scan);
        const detail = launcherStateDetail(scan, state);
        return <article className="rowyn-launcher" key={launcher.id}><h2>{launcher.name}</h2>
          <p className="card-copy">{launcher.description}</p><p className="rowyn-state">{launcherStateLabel(scan, state)}</p><code>{launcher.id}</code>
          {detail && <p className="card-copy">{detail}</p>}
          <div className="home-actions"><button className="action-button" disabled={busy || state !== 'installed'} onClick={() => run(`Opening ${launcher.name}…`, () => openRowynLauncher(launcher.id, launchGamemode))}>Open {launcher.name}</button>
          <button className="action-button" disabled={busy || !!pending || state !== 'not installed' || !!tracked} onClick={() => setPending({ kind: 'install', id: launcher.id, name: launcher.name })}>Install {launcher.name}</button>
          <button className="action-button" disabled={busy || !!pending || state !== 'installed' || !!tracked} onClick={() => setPending({ kind: 'remove', id: launcher.id, name: launcher.name })}>Remove {launcher.name}</button></div>
        </article>;
      })}</div>
      <h3>Rowyn gaming helpers</h3>
      <p className="card-copy">{GAMING_HELPERS_NOTE}</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Rowyn gaming helper state…', async () => {
        const next = await rowynGamingHelpersStatus(); setHelpers(next);
        return gamingTuneLabel(next);
      })}>Check helper status</button>
      {helpers && (tuneIsOn(helpers)
        ? <button className="action-button" disabled={busy || !helpers.installed} onClick={() => run('Asking for administrator access…', async () => {
          const next = await rowynGamingTuneApply(false); setHelpers(next);
          return `Gaming profile disabled. ${TUNE_OFF_NOTE}`;
        })}>Disable gaming profile</button>
        : <button className="action-button" disabled={busy || !helpers.installed} onClick={() => run('Asking for administrator access…', async () => {
          const next = await rowynGamingTuneApply(true); setHelpers(next);
          return `${gamingTuneLabel(next)} It applies now and at boot, and trades sustained disk-write throughput for latency.`;
        })}>Enable gaming profile</button>)}</div>
      <p className="card-copy" role="status">{gamingTuneLabel(helpers)}</p>
      {helpers && <p className="card-copy">{helpers.detail} {TUNE_ADMIN_NOTE}</p>}
      <p className="card-copy">{HOST_ONLY_NOTE}</p>
      {HELPER_EXAMPLES.map((example) => <div key={example.key}>
        <p className="card-copy">{example.label}</p><pre>{example.command}</pre>
        <button className="action-button" disabled={busy} onClick={() => run('Copying the wrapper example…', async () => {
          const result = await copyText(example.command);
          return result.ok ? 'Wrapper example copied. No launch options were changed.' : result.reason;
        })}>Copy wrapper example</button>
      </div>)}
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
    <section className="glass dashboard-card" id="hub-section-steam-library"><h2>Steam library</h2>
      <p className="card-copy rowyn-provenance">{steamLibraryProvenance(compatReviewedOn(), compatVerdictCount())}</p>
      <h3>Shader pre-caching</h3>
      <p className="card-copy" role="status">{shaderFailed && !shaderCache ? 'The shader pre-caching read could not run.' : shaderPreCachingLabel(shaderCache)}</p>
      <p className="card-copy">{SHADER_PRE_CACHING_TIP}</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Steam’s shader pre-caching setting…', async () => {
        setShaderFailed(false);
        const next = await rowynSteamShaderCache(); setShaderCache(next);
        return shaderPreCachingLabel(next);
      })}>Check shader pre-caching</button></div>
      <h3>Recently played</h3>
      <p className="card-copy">Steam’s last-played time and playtime counter, shown as Steam recorded them — Rowyn measures nothing here. Titles that are not installed on this machine have no local name, so they are counted rather than labelled.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Steam play history…', async () => {
        setRecentPlay(null); setRecentFailed(false);
        const next = await rowynSteamRecentPlay(); setRecentPlay(next);
        return recentPlayStatus(next);
      })}>Refresh recently played</button></div>
      {!recentPlay && <p className="card-copy" role="status">{recentFailed ? 'Steam’s local profile could not be read. Start Steam once, then refresh.' : 'Reading Steam’s local profile…'}</p>}
      {recentPlay && <p className="card-copy" role="status">{recentPlayStatus(recentPlay)}</p>}
      {!!recentPlay?.games.length && <ul className="card-copy" aria-label="Recently played titles">{recentPlay.games.map((item) => <li key={item.app_id}><strong>{item.name}</strong> — {formatPlaytime(item.playtime_minutes)} · {formatLastPlayed(item.last_played)}</li>)}</ul>}
      <h3>Installed titles</h3>
      <p className="card-copy">The library comes first: Steam’s own cached cover art where this machine has it, a one-word state when the curated data has a verdict, and everything else folded into one <strong>Launch options</strong> disclosure per title. A title with no cached artwork shows a letter tile. Covers are local files — read, never fetched.</p>
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
      {profileWarning && <p className="card-copy" role="status">{profileWarning}</p>}
      {!!steamScan?.games.length && <p className="card-copy" role="status">{steamLibraryCoverageNote(libraryCoverage, libraryCoverPlan.entries.length, libraryCoverPlan.budgeted)}</p>}
      {!!steamScan?.games.length && <div className="home-actions">
        <label>Search installed titles<input value={libraryQuery} maxLength={80} onChange={(event) => setLibraryQuery(event.target.value)} placeholder="Title or Steam App ID" /></label>
        <label>Show<select value={libraryFilter} onChange={(event) => setLibraryFilter(event.target.value as SteamStateFilter)}><option value="all">All titles</option><option value="native">Native</option><option value="proton">Proton</option><option value="tweaks">Tweaks</option><option value="blocked">Blocked</option><option value="unreviewed">No curated note</option></select></label>
      </div>}
      {!!steamScan?.games.length && !libraryVisible.length && <p className="card-copy">No installed title matches this search and filter.</p>}
      {!!libraryCoverPlan.entries.length && <div className="rowyn-library">{libraryCoverPlan.entries.map(({ game: item, verdict }) => {
        const links = steamCompatibilityLinks(item.app_id);
        // Attributed once, by app id, when the library was built: a scanned
        // title is never used to borrow a same-named remake's note.
        const chip = steamStateChip(verdict);
        const profile = profileFor(profiles, item.app_id);
        // gamescope is a host-side wrapper, never a Flatpak layer step: the
        // option is offered only on the host boundary, and only when this
        // session resolves the binary. GameMode is the mirror image: the client
        // has to exist inside Steam's sandbox. Either saved flag is masked
        // rather than emitted as a command that would fail at launch.
        const gamescopeUsable = boundary === 'host' && gamescope?.available === true;
        const profileForAssembly = {
          ...profile,
          gamescope: gamescopeUsable ? profile.gamescope : false,
          gamemode: gamemodeUsable ? profile.gamemode : false,
        };
        const assembled = assembleProfileOptions(boundary, profileForAssembly);
        return <article className="rowyn-library-card" key={item.app_id}>
          {item.cover
            ? <img className="rowyn-library-cover" src={item.cover} alt={`${item.name} — Steam’s locally cached cover art`} loading="lazy" />
            : <div className="rowyn-library-cover is-empty" aria-hidden="true">{coverInitials(item.name)}</div>}
          <div className="rowyn-library-body">
            <h3>{item.name}</h3>
            <p className="rowyn-library-meta">{chip && <span className={`rowyn-state ${chip.tone}`}>{chip.label}</span>}<span>Steam App ID {item.app_id}</span></p>
            <details className="rowyn-disclosure">
              <summary>Launch options</summary>
              <div className="rowyn-disclosure-body">
                {verdict && <p className="card-copy"><span className="rowyn-state">{verdict.label}</span> — {verdict.note} Anti-cheat: {verdict.anticheat}.</p>}
                <h4>Launch profile for this game</h4>
          <p className="card-copy">Remembered per title on this machine and rebuilt below using the boundary chosen in Compatibility (currently: {boundary === 'flatpak' ? 'Flatpak Steam' : 'host-native'}). Paste the result into Steam → Properties → Launch Options for this game; the builder above remains the unsaved default profile.</p>
          <label><input type="checkbox" checked={profile.mangohud} onChange={(event) => updateProfile(item.app_id, { mangohud: event.target.checked })} /> MangoHud overlay</label>
          <label><input type="checkbox" checked={profile.vkbasalt} onChange={(event) => updateProfile(item.app_id, { vkbasalt: event.target.checked })} /> vkBasalt post-processing</label>
          {boundary === 'flatpak' && gamemodeUsable && <label><input type="checkbox" checked={profile.gamemode} onChange={(event) => updateProfile(item.app_id, { gamemode: event.target.checked })} /> GameMode (gamemoderun %command%): audio and power-profile hooks apply; renice and CPU pinning cannot inside the sandbox</label>}
          {boundary === 'flatpak' && !gamemodeUsable && <p className="card-copy">{gamemodeFailed ? 'GameMode availability could not be read, so the option is not offered.' : gamemodeStatus ? gamemodeStatus.detail : 'Checking for GameMode…'}</p>}
          {gamescopeUsable && <label><input type="checkbox" checked={profile.gamescope} onChange={(event) => updateProfile(item.app_id, { gamescope: event.target.checked })} /> gamescope (host: wraps this command in the gamescope compositor)</label>}
          {boundary === 'host' && !gamescopeUsable && <p className="card-copy">{gamescopeFailed ? 'gamescope availability could not be read, so the option is not offered.' : gamescope ? gamescope.detail : 'Checking for gamescope…'}</p>}
          <label><input type="checkbox" checked={profile.gaming} disabled={boundary === 'flatpak'} onChange={(event) => updateProfile(item.app_id, { gaming: event.target.checked })} /> Rowyn gaming slice / GameMode wrapper (host only)</label>
          <label><input type="checkbox" checked={profile.mimalloc} disabled={boundary === 'flatpak'} onChange={(event) => updateProfile(item.app_id, { mimalloc: event.target.checked })} /> mimalloc allocator wrapper (host only, experimental)</label>
          <label>Extra launch options (kept verbatim)<input value={profile.extra} maxLength={200} onChange={(event) => updateProfile(item.app_id, { extra: event.target.value })} placeholder="e.g. -high or PROTON_LOG=1" /></label>
          <p className="card-copy">Launch options: <code>{assembled}</code></p>
          <div className="home-actions"><button className="action-button" onClick={() => void copyText(assembled)}>Copy launch options</button>
          {!isDefaultProfile(profile) && <button className="action-button" onClick={() => updateProfile(item.app_id, { mangohud: false, vkbasalt: false, gamescope: false, gamemode: false, gaming: false, mimalloc: false, extra: '' })}>Reset profile</button>}</div>
                  <div className="home-actions"><ExternalLink href={links.protondb}>ProtonDB community reports</ExternalLink>
                    <ExternalLink href={links.steamStore}>Steam store page</ExternalLink></div>
              </div>
            </details>
          </div>
        </article>;
      })}</div>}
      {!!steamScan?.warnings && <p className="card-copy" role="status">Partial scan: skipped or unreadable library metadata may hide additional titles.</p>}
    </section>
    <section className="glass dashboard-card" id="hub-section-compatibility"><h2>Compatibility &amp; anti-cheat</h2>
      <p className="card-copy">Kernel-level Windows anti-cheat generally cannot run under Proton. Easy Anti-Cheat and BattlEye support requires the game developer to enable Linux/Proton support; the middleware name alone proves nothing. Check the exact title and multiplayer mode before buying or installing.</p>
      <p className="card-copy rowyn-provenance">Provenance — reviewed 2026-10-31: local guidance, not Rowyn-validated game results, and Linux/Proton support changes with game, launcher and anti-cheat updates. Sources: <ExternalLink href="https://partner.steamgames.com/doc/steamdeck/proton">Valve’s Proton / anti-cheat documentation</ExternalLink> (developer guidance), <ExternalLink href="https://www.protondb.com/">ProtonDB</ExternalLink> and <ExternalLink href="https://areweanticheatyet.com/">Are We Anti-Cheat Yet?</ExternalLink> (community reports, not guarantees). Links require a web browser; no compatibility lookup or network probe runs in this page.</p>
      <label>Game label (local only; never executed)<input value={game} maxLength={80} onChange={(event) => setGame(event.target.value)} placeholder="Your game" /></label>
      <label>Where does this game run?<select value={boundary} onChange={(event) => setBoundary(event.target.value as 'flatpak' | 'host')}><option value="flatpak">Flatpak Steam / sandboxed launcher</option><option value="host">Host-native Steam / host command</option></select></label>
      <label><input type="checkbox" checked={gaming} disabled={boundary === 'flatpak'} onChange={(event) => setGaming(event.target.checked)} /> Use Rowyn gaming slice / GameMode when available</label>
      {boundary === 'flatpak' && gamemodeUsable && <label><input type="checkbox" checked={gameMode} onChange={(event) => setGameMode(event.target.checked)} /> GameMode (gamemoderun %command%): Rowyn&apos;s low-latency audio and performance power profile apply; renice and CPU pinning cannot inside the sandbox</label>}
      {boundary === 'flatpak' && !gamemodeUsable && <p className="card-copy">{gamemodeFailed ? 'GameMode availability could not be read, so the option is not offered.' : gamemodeStatus ? gamemodeStatus.detail : 'Checking for GameMode…'}</p>}
      <label><input type="checkbox" checked={mimalloc} disabled={boundary === 'flatpak'} onChange={(event) => setMimalloc(event.target.checked)} /> Try mimalloc for this game only (experimental; may regress or warn on 32-bit games)</label>
      <label><input type="checkbox" checked={mangohud} onChange={(event) => setMangohud(event.target.checked)} /> MangoHud overlay (frame rate and frame-time readout; needs the MangoHud Vulkan layer below)</label>
      <label><input type="checkbox" checked={vkbasalt} onChange={(event) => setVkbasalt(event.target.checked)} /> vkBasalt post-processing (sharpening and colour effects such as CAS; needs the vkBasalt Vulkan layer below)</label>
      <p className="card-copy">Host wrappers /usr/bin/rowyn-game-launch and /usr/bin/rowyn-mimalloc-run are not generally accessible inside Flatpak Steam. Do not paste host paths there or use flatpak-spawn to escape the sandbox. The Flatpak-safe default below adds no host wrapper. The host-native example is for an already installed host launcher; this Hub does not install one. Undo by removing the launch options.</p>
      <p className="card-copy">Proton-GE: some games run better on the community Proton-GE build than on Valve’s Proton. ProtonPlus — in the Apps page’s Gaming pack — installs and updates Proton-GE for Steam and the other launchers; pick the tool per game in Steam → Properties → Compatibility. Trying it is reversible: switch back to Proton Experimental the same way.</p>
      <h3>Steam Play for all titles</h3>
      <p className="card-copy">Windows games need Steam Play enabled globally, or each title needs its compatibility tool set by hand. This reads Steam’s own configuration; the one-click apply quits-safe (it refuses while Steam is running, keeps a backup of the original file, and only ever sets the default tool to Proton Experimental).</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Steam Play state…', async () => {
        const next = await rowynSteamPlayStatus(); setSteamPlay(next);
        return next.detail;
      })}>Check Steam Play</button>
      {steamPlay && steamPlay.steam_present && !steamPlay.mapping_present && <button className="action-button" disabled={busy || steamPlay.steam_running} onClick={() => run('Enabling Steam Play for all titles…', async () => {
        const next = await rowynSteamPlayApply(); setSteamPlay(next);
        return next.mapping_present ? 'Steam Play is now enabled for all titles (Proton Experimental).' : next.detail;
      })}>Enable Steam Play for all titles</button>}</div>
      {!steamPlay && <p className="card-copy">{checkFailure.steamPlay ? 'The Steam Play check could not run; use Check Steam Play to retry.' : 'Reading Steam’s configuration…'}</p>}
      {steamPlay && <p className="card-copy">{steamPlay.detail}{steamPlay.steam_running && !steamPlay.mapping_present ? ' Quit Steam to enable the one-click apply.' : ''}</p>}
      <h3>Vulkan layers for Flatpak games</h3>
      <p className="card-copy">MangoHud, gamescope and vkBasalt reach Flatpak games as Freedesktop Vulkan-layer extensions, matched to the runtime branch Steam itself uses — a layer installed for the wrong branch does nothing. On a host-native install the same tools come from the distribution inside Distrobox instead.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Vulkan layers…', async () => {
        const next = await rowynVulkanLayersScan(); setLayers(next);
        return next.detail;
      })}>Check Vulkan layers</button>
      {layers?.steam_runtime_branch && !layers.mangohud_branches.includes(layers.steam_runtime_branch) && <button className="action-button" disabled={busy} onClick={() => run('Installing the MangoHud layer…', async () => {
        const next = await rowynVulkanLayerInstall('mangohud'); setLayers(next);
        return next.mangohud_branches.includes(next.steam_runtime_branch ?? '') ? `MangoHud layer installed for runtime ${next.steam_runtime_branch}.` : 'MangoHud layer install finished; re-check the layer state.';
      })}>Install MangoHud layer</button>}
      {layers?.steam_runtime_branch && !layers.gamescope_branches.includes(layers.steam_runtime_branch) && <button className="action-button" disabled={busy} onClick={() => run('Installing the gamescope layer…', async () => {
        const next = await rowynVulkanLayerInstall('gamescope'); setLayers(next);
        return next.gamescope_branches.includes(next.steam_runtime_branch ?? '') ? `gamescope layer installed for runtime ${next.steam_runtime_branch}.` : 'gamescope layer install finished; re-check the layer state.';
      })}>Install gamescope layer</button>}
      {layers?.steam_runtime_branch && !layers.vkbasalt_branches.includes(layers.steam_runtime_branch) && <button className="action-button" disabled={busy} onClick={() => run('Installing the vkBasalt layer…', async () => {
        const next = await rowynVulkanLayerInstall('vkbasalt'); setLayers(next);
        return next.vkbasalt_branches.includes(next.steam_runtime_branch ?? '') ? `vkBasalt layer installed for runtime ${next.steam_runtime_branch}.` : 'vkBasalt layer install finished; re-check the layer state.';
      })}>Install vkBasalt layer</button>}</div>
      {!layers && <p className="card-copy">{checkFailure.layers ? 'The layer list could not be read; use Check Vulkan layers to retry.' : 'Reading installed Flatpak layers…'}</p>}
      {layers && <p className="card-copy">{layers.detail} MangoHud branches installed: {layers.mangohud_branches.join(', ') || 'none'}. gamescope branches installed: {layers.gamescope_branches.join(', ') || 'none'}. vkBasalt branches installed: {layers.vkbasalt_branches.join(', ') || 'none'}.</p>}
      <h3>MangoHud settings</h3>
      <p className="card-copy">MangoHud reads one configuration file. Flathub Steam reads it inside its sandbox at the path below; guides that point at ~/.config/MangoHud do nothing for Flathub Steam. Rowyn reads the path and the file, and never writes either.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading MangoHud’s config path…', async () => {
        setMangohudFailed(false);
        const next = await rowynMangohudConfig(); setMangohudConfig(next);
        return mangohudConfigLabel(next);
      })}>Check MangoHud settings</button></div>
      {!mangohudConfig && <p className="card-copy" role="status">{mangohudFailed ? 'The MangoHud settings read could not run; use Check MangoHud settings to retry.' : 'Not read yet.'}</p>}
      {mangohudConfig && <><p className="card-copy" role="status">{mangohudConfigLabel(mangohudConfig)}</p>
        <p className="card-copy">Config path: <code>{mangohudConfigPath(mangohudConfig)}</code></p>
        <details className="rowyn-disclosure"><summary>Recommended starting config (not written by Rowyn)</summary><div className="rowyn-disclosure-body"><p className="card-copy">{MANGOHUD_READ_NOTE}</p><pre>{MANGOHUD_RECOMMENDED}</pre><div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Copying the recommended MangoHud config…', async () => {
          const result = await copyText(MANGOHUD_RECOMMENDED);
          return result.ok ? 'Recommended MangoHud config copied. Nothing was written.' : result.reason;
        })}>Copy recommended config</button></div></div></details></>}
      <h3>Displays: HDR and variable refresh</h3>
      <p className="card-copy">What the display stack reports for each output. “Incapable” means the display or its connection does not offer the feature — no setting changes that. Enable HDR per display and variable refresh in System Settings → Display &amp; Monitor; KWin’s variable-refresh policy applies to fullscreen games.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading display capabilities…', async () => {
        const next = await rowynDisplayModesScan(); setDisplays(next);
        return next.detail;
      })}>Check displays</button>
      <button className="action-button" disabled={busy} onClick={() => run('Opening Display settings…', () => openRowynSettings('displays'))}>Open Display settings</button></div>
      {!displays && <p className="card-copy">{checkFailure.displays ? 'The display scan could not run; use Check displays to retry.' : 'Reading display capabilities…'}</p>}
      {displays && <p className="card-copy">{displays.detail}</p>}
      {displays?.outputs.map((output) => <p className="card-copy" key={output.name}>{output.name} — {output.connected ? 'connected' : 'disconnected'} · HDR: {output.hdr ?? 'not reported'} · VRR: {output.vrr ?? 'not reported'}</p>)}
      <p>{game || 'Selected game'} — paste into that game’s launch options:</p><pre aria-label="Launch options">{options}</pre>
      <button className="action-button" disabled={busy} onClick={() => run('Copying launch options…', async () => {
        const result = await copyText(options);
        return result.ok ? 'Launch options copied. No configuration was changed.' : result.reason;
      })}>Copy launch options</button>
    </section>
    <section className="glass dashboard-card" id="hub-section-streaming"><h2>Game streaming (Sunshine)</h2>
      <p className="card-copy">Sunshine turns this machine into a stream host for Moonlight clients — a TV, a laptop, a phone. Rowyn automates the tedious parts: the install (the standard system Flatpak job), start-at-login, and opening Sunshine’s documented firewall ports (TCP 47984/47989/48010, UDP 47998–48000/48002/48010, admin prompt via the same askpass path as VPN). The input devices Sunshine injects (keyboard, mouse, gamepad) reach your session through udev rules Rowyn ships in the image — nothing to configure.</p>
      <p className="card-copy">What stays manual, honestly: pairing a client is done in Sunshine’s own web interface at https://localhost:47990 after it first runs, and a client on another machine uses Moonlight — offered in the Apps page’s Gaming pack for the receiving side.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading Sunshine state…', async () => {
        const next = await rowynSunshineStatus(); setSunshine(next);
        return next.detail;
      })}>Check Sunshine</button>
      {sunshine && !sunshine.installed && <button className="action-button" disabled={busy} onClick={() => run('Installing Sunshine…', async () => {
        const result = await installFlatpak('dev.lizardbyte.app.Sunshine');
        const next = await rowynSunshineStatus(); setSunshine(next);
        return `${result} ${next.detail}`;
      })}>Install Sunshine</button>}
      {sunshine?.installed && !sunshine.autostart && <button className="action-button" disabled={busy} onClick={() => run('Enabling start-at-login…', async () => {
        const next = await rowynSunshineAutostartEnable(); setSunshine(next);
        return next.detail;
      })}>Start Sunshine at login</button>}
      {sunshine?.installed && !sunshine.firewall_open && <button className="action-button" disabled={busy} onClick={() => run('Opening the Sunshine firewall ports…', async () => {
        const next = await rowynSunshineFirewallOpen(); setSunshine(next);
        return next.detail;
      })}>Open firewall ports</button>}</div>
      {!sunshine && <p className="card-copy">Not checked yet.</p>}
      {sunshine && <p className="card-copy">{sunshine.detail}</p>}
    </section>
    <section className="glass dashboard-card" id="hub-section-controllers"><h2>Controllers</h2>
      <p className="card-copy">Connect or pair a controller, then rescan. This reads the shipped controller checker only; it does not pair devices, load drivers, change permissions, or claim input testing passed.</p>
      <button className="action-button" disabled={busy} onClick={() => run('Reading controller readiness…', async () => {
        setControllers(null); const next = await rowynControllerScan(); setControllers(next); return 'Controller scan finished. Review findings below.';
      })}>Rescan controllers</button>
      {!controllers && <p className="card-copy">{checkFailure.controllers ? 'The controller check could not run; use Rescan controllers to retry.' : 'Reading input devices…'}</p>}
      {controllers && <ul className="rowyn-controller-summary" aria-label="Controller readiness summary">{controllerSummary(controllers.findings).map((line) => <li key={line.key} className={`is-${line.state}`}><span className={`rowyn-state is-${line.state}`}>{line.label}</span><span>{line.detail}</span></li>)}</ul>}
      {controllers && <p className="card-copy">{CONTROLLER_READ_NOTE}</p>}
      {!!controllerAdvice(controllers).length && <><h3>If a pad is not recognised</h3>{controllerAdvice(controllers).map((hint) => <p className="card-copy" key={hint.family}><strong>{hint.label}</strong> — {hint.hint}</p>)}</>}
      {controllers && <details className="rowyn-disclosure rowyn-controller-findings"><summary>Technical findings ({controllers.findings.length})</summary><div className="rowyn-disclosure-body">{controllers.findings.map((item) => <div key={item.name}><h4>{item.name} <span className="rowyn-state">{item.state}</span></h4><pre>{item.detail}</pre></div>)}</div></details>}
    </section>
  </div>;
}
