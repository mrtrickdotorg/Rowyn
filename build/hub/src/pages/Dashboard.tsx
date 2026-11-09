import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { SetupChecklist } from './SetupChecklist';
import { useStatusRow } from '../services/useStatusRow';
import { BootHealthPanel } from '../components/BootHealthPanel';
import { RowynReadiness } from '../components/RowynReadiness';
import { UpdateStatusBanner } from '../components/UpdateStatusBanner';
import { degradedDashboardAcceptance, recordHubAcceptance } from '../services/acceptance';
import {
  fetchBootRuntimeChecks,
  fetchGpuName,
  fetchRecoveryStatus,
  fetchStorageFree,
  fetchUpdateChannel,
  fetchUserName,
  type BootRuntimeCheck,
  type RecoveryStatus,
} from '../services/liveData';
import '../styles/rowynMvp.css';

type Reading<T> = T | null;

function StatusCard({ icon, label, value, detail, tone = 'neutral', title }: {
  icon: string;
  label: string;
  value: string;
  detail: string;
  tone?: 'neutral' | 'attention';
  title?: string;
}) {
  return (
    <article className={`rowyn-home-stat rowyn-home-stat-${tone}`}>
      <div className="rowyn-home-stat-head">
        <span className="rowyn-home-icon" aria-hidden="true">{icon}</span>
        <span className="rowyn-home-stat-label">{label}</span>
      </div>
      <strong className="rowyn-home-stat-value" title={title}>{value}</strong>
      <span className="rowyn-home-stat-detail">{detail}</span>
    </article>
  );
}

const destinations = [
  { label: 'Work', description: 'Set up printing, open office tools, and make time to focus.', route: '/work', icon: '▤', accent: 'blue' },
  { label: 'Backups', description: 'Configure file backups and review recorded completion.', route: '/backups', icon: '↥', accent: 'mint' },
  { label: 'Development', description: 'Create and manage a dedicated Ubuntu development environment.', route: '/development', icon: '⌘', accent: 'violet' },
  { label: 'Play', description: 'Launchers, local game discovery, and controller checks.', route: '/play', icon: '✦', accent: 'berry' },
  { label: 'Apps', description: 'Browse and manage the apps available on this system.', route: '/apps', icon: '▦', accent: 'blue' },
  { label: 'Updates', description: 'Review system updates and available rollback options.', route: '/updates', icon: '↻', accent: 'amber' },
  { label: 'Diagnostics', description: 'Run read-only checks and review a report before sharing.', route: '/diagnostics', icon: '⌁', accent: 'mint' },
  { label: 'VPN', description: 'Manage your saved secure-work connections.', route: '/vpn', icon: '◈', accent: 'violet' },
  { label: 'MoveIn', description: 'Bring documents, pictures, bookmarks, and your wallpaper from Windows.', route: '/windows-move-in', icon: '⇥', accent: 'amber' },
] as const;

/** Drop vendor boilerplate and the PCI id so a long GPU name fits a stat card. */
function readableDevice(name: string): string {
  return name
    .replace(/^(?:Advanced Micro Devices, Inc\.|Intel Corporation|NVIDIA Corporation)\s*/i, '')
    .replace(/\[(?:AMD\/ATI|Intel|NVIDIA)\]\s*/i, '')
    .replace(/\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]\s*$/i, '')
    .trim() || name;
}

export function Dashboard() {
  const navigate = useNavigate();
  const [bootChecks, setBootChecks] = useState<Reading<BootRuntimeCheck[]>>(null);
  const [recovery, setRecovery] = useState<Reading<RecoveryStatus>>(null);
  const [updateChannel, setUpdateChannel] = useState<string | null>(null);
  const [gpuName, setGpuName] = useState<string | null>(null);
  const [storageFree, setStorageFree] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const statusRowVisible = useStatusRow();

  useEffect(() => {
    let cancelled = false;
    let degraded = false;

    async function readStatus() {
      if (await degradedDashboardAcceptance()) {
        degraded = true;
        if (!cancelled) {
          setLoaded(true);
          void recordHubAcceptance('dashboard', JSON.stringify({ state: 'degraded', label: 'Status unavailable' }));
        }
        return;
      }
      const [nextBoot, nextRecovery, nextChannel, nextGpu, nextStorage, nextUser] = await Promise.all([
        fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchUpdateChannel(),
        fetchGpuName(), fetchStorageFree(), fetchUserName(),
      ]);
      if (cancelled) return;
      setBootChecks(nextBoot);
      setRecovery(nextRecovery);
      setUpdateChannel(nextChannel);
      setGpuName(nextGpu);
      setStorageFree(nextStorage);
      setUserName(nextUser);
      setLoaded(true);
      const hasReading = nextBoot !== null || nextRecovery !== null || nextChannel !== null || nextGpu !== null || nextStorage !== null;
      void recordHubAcceptance('dashboard', JSON.stringify({ state: hasReading ? 'live' : 'degraded', label: hasReading ? 'System overview' : 'Status unavailable' }));
    }

    void readStatus();
    const refresh = window.setInterval(() => {
      if (degraded) return;
      void Promise.all([
        fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchUpdateChannel(),
        fetchGpuName(), fetchStorageFree(), fetchUserName(),
      ]).then(([nextBoot, nextRecovery, nextChannel, nextGpu, nextStorage, nextUser]) => {
        if (cancelled) return;
        setBootChecks((current) => nextBoot ?? current);
        setRecovery((current) => nextRecovery ?? current);
        setUpdateChannel((current) => nextChannel ?? current);
        setGpuName((current) => nextGpu ?? current);
        setStorageFree((current) => nextStorage ?? current);
        setUserName((current) => nextUser ?? current);
      });
    }, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(refresh);
    };
  }, []);

  const passed = bootChecks?.filter((check) => check.passed).length ?? 0;
  const total = bootChecks?.length ?? 0;
  const hasReading = bootChecks !== null || recovery !== null || updateChannel !== null || gpuName !== null || storageFree !== null;
  const needsReview = bootChecks?.some((check) => !check.passed) === true || Boolean(recovery?.quarantined_digest);
  const statusLabel = !loaded ? 'Checking system…' : !hasReading ? 'Status unavailable' : needsReview ? 'Review recommended' : 'Readings available';
  const bootLabel = !loaded ? 'Checking…' : bootChecks === null ? 'Unavailable' : total === 0 ? 'Not reported' : `${passed} of ${total} passing`;
  const recoveryLabel = !loaded ? 'Checking…' : recovery === null ? 'Unavailable' : recovery.quarantined_digest ? 'Review required' : recovery.has_rollback ? 'Rollback ready' : 'No rollback recorded';
  const healthDetail = !loaded ? 'Reading boot checks.' : bootChecks === null ? 'Boot health data could not be read.' : total === 0 ? 'No boot checks were reported.' : needsReview && bootChecks.some((check) => !check.passed) ? 'Open Diagnostics to review checks needing attention.' : 'Reported boot checks are passing.';
  const recoveryDetail = !loaded ? 'Reading recovery safeguards.' : recovery === null ? 'Recovery data could not be read.' : recovery.quarantined_digest ? 'A deployment was quarantined; review Updates.' : recovery.has_rollback ? 'A previous deployment is available.' : 'No previous deployment is recorded.';

  return (
    <div className="page-content rowyn-home">
      <section className="rowyn-home-hero" aria-labelledby="rowyn-home-title">
        <div className="rowyn-home-hero-copy">
          <span className="home-eyebrow">ROWYN · CONTROL ROOM</span>
          <h1 id="rowyn-home-title">Welcome home{userName ? `, ${userName}` : ''}</h1>
          <p>Your system at a glance. Choose a workspace when you’re ready to do more.</p>
        </div>
        <div className={`rowyn-home-status ${needsReview ? 'is-attention' : hasReading ? 'is-reading' : 'is-pending'}`} role="status" aria-live="polite">
          <span className="rowyn-home-status-dot" aria-hidden="true" />{statusLabel}
        </div>
        <span className="rowyn-home-orbit rowyn-home-orbit-one" aria-hidden="true" />
        <span className="rowyn-home-orbit rowyn-home-orbit-two" aria-hidden="true" />
      </section>

      <section className="rowyn-home-section" aria-labelledby="rowyn-home-status-title" hidden={!statusRowVisible}>
        <div className="rowyn-home-section-heading">
          <div><span className="home-eyebrow">READ-ONLY CHECKS</span><h2 id="rowyn-home-status-title">System status</h2></div>
          <span className="rowyn-home-section-aside">Refresh a reading here; changes belong in Updates and Diagnostics</span>
        </div>
        <div className="rowyn-home-status-grid">
          <UpdateStatusBanner />
          <BootHealthPanel compact />
        </div>
      </section>

      <section className="rowyn-home-section" aria-labelledby="rowyn-home-snapshot-title">
        <div className="rowyn-home-section-heading">
          <div><span className="home-eyebrow">THE QUICK READ</span><h2 id="rowyn-home-snapshot-title">System snapshot</h2></div>
          <span className="rowyn-home-refresh-note">Refreshes automatically · checks are read-only</span>
        </div>
        <div className="rowyn-home-stats">
          <StatusCard icon="⌁" label="Boot health" value={bootLabel} detail={healthDetail} tone={needsReview ? 'attention' : 'neutral'} />
          <StatusCard icon="↶" label="Recovery" value={recoveryLabel} detail={recoveryDetail} tone={recovery?.quarantined_digest ? 'attention' : 'neutral'} />
          <StatusCard icon="◈" label="Update channel" value={!loaded ? 'Checking…' : updateChannel ?? 'Unavailable'} detail="The release stream this device follows." />
          <StatusCard icon="▣" label="Device" value={!loaded ? 'Checking…' : gpuName ? readableDevice(gpuName) : 'Graphics unavailable'} detail={storageFree ? `${storageFree} free storage` : 'Storage reading unavailable'} title={gpuName ?? undefined} />
        </div>
      </section>

      <section className="rowyn-home-section" aria-labelledby="rowyn-home-workspaces-title">
        <div className="rowyn-home-section-heading">
          <div><span className="home-eyebrow">PICK YOUR NEXT STEP</span><h2 id="rowyn-home-workspaces-title">Your workspaces</h2></div>
          <span className="rowyn-home-section-aside">A little more Rowyn, one click away</span>
        </div>
        <div className="rowyn-home-destinations">
          {destinations.map((item) => (
            <button key={item.route} type="button" className={`home-destination home-destination-${item.accent}`} onClick={() => navigate(item.route)}>
              <span className="home-destination-icon" aria-hidden="true">{item.icon}</span>
              <span className="home-destination-copy"><strong>{item.label}</strong><span>{item.description}</span></span>
              <span className="home-destination-arrow" aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      </section>

      <RowynReadiness />
      <SetupChecklist navigate={navigate} />
    </div>
  );
}
