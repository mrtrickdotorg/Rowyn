export const DEVELOPMENT_NAME = 'rowyn-ubuntu-dev';

export type DevelopmentState = {
  state: 'absent' | 'managed' | 'collision' | 'unavailable';
  detail?: string;
};

export type Job = {
  id: string;
  status: 'running' | 'succeeded' | 'failed' | 'cancelled';
  detail: string;
};

export type JobControllerDeps = {
  start: (op: 'create' | 'delete' | `preset:${string}`, confirmation?: string) => Promise<Job>;
  poll: (id: string) => Promise<Job>;
  cancel: (id: string) => Promise<Job>;
  onJob: (job: Job | null) => void;
  onMessage: (message: string) => void;
  refresh: () => Promise<void>;
  onActiveId?: (id: string | null) => void;
  onPaused?: (paused: boolean) => void;
  intervalMs?: number;
  maxPollErrors?: number;
  maxPolls?: number;
};

export function createJobController(deps: JobControllerDeps) {
  let mounted = true;
  let generation = 0;
  let inFlight = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let current: Job | null = null;
  let paused = false;
  let pollErrors = 0;
  let pollCount = 0;

  const publish = (job: Job | null) => {
    if (mounted) {
      current = job;
      deps.onJob(job);
    }
  };
  const setPaused = (value: boolean) => {
    paused = value;
    if (mounted) deps.onPaused?.(value);
  };
  const finish = async (job: Job, token: number) => {
    if (job.status === 'running') return;
    if (!mounted || token !== generation) return;
    publish(job);
    deps.onActiveId?.(null);
    try {
      await deps.refresh();
    } catch (error) {
      if (mounted && token === generation) {
        deps.onMessage(`Refresh status unavailable: ${String(error)}`);
      }
    }
    if (mounted && token === generation) deps.onMessage(job.detail);
  };
  const schedule = (token: number) => {
    if (token !== generation || !mounted || current?.status !== 'running' || paused) return;
    timer = setTimeout(() => void doPoll(token), deps.intervalMs ?? 1000);
  };

  async function doPoll(token: number) {
    if (!mounted || token !== generation || !current || current.status !== 'running' || paused) return;
    if (pollCount >= (deps.maxPolls ?? POLL_LIMIT)) {
      setPaused(true);
      deps.onMessage('Job polling limit reached; the operation may continue. Resume status checks when ready.');
      return;
    }
    pollCount++;
    try {
      const job = await deps.poll(current.id);
      if (!mounted || token !== generation) return;
      pollErrors = 0;
      publish(job);
      if (job.status === 'running') schedule(token);
      else await finish(job, token);
    } catch (error) {
      if (!mounted || token !== generation) return;
      pollErrors++;
      if (pollErrors >= (deps.maxPollErrors ?? 3)) {
        setPaused(true);
        deps.onMessage(`Job status unavailable: ${String(error)}. The operation may continue; resume status checks when ready.`);
        return;
      }
      deps.onMessage(`Job status unavailable: ${String(error)}`);
      schedule(token);
    }
  }

  const beginResume = (id: string) => {
    if (!mounted || inFlight) return;
    clearTimeout(timer);
    const token = ++generation;
    current = { id, status: 'running', detail: 'Checking job status' };
    pollErrors = 0;
    pollCount = 0;
    setPaused(false);
    publish(current);
    deps.onActiveId?.(id);
    void doPoll(token);
  };

  return {
    get job() { return current; },
    get locked() { return inFlight; },
    get paused() { return paused; },
    async start(op: 'create' | 'delete' | `preset:${string}`, confirmation?: string) {
      if (!mounted || inFlight || current?.status === 'running') return;
      inFlight = true;
      const token = ++generation;
      pollErrors = 0;
      pollCount = 0;
      setPaused(false);
      try {
        const job = await deps.start(op, confirmation);
        if (!mounted || token !== generation) return;
        publish(job);
        deps.onActiveId?.(job.status === 'running' ? job.id : null);
        if (job.status === 'running') {
          deps.onMessage(job.detail);
          schedule(token);
        } else {
          await finish(job, token);
        }
      } catch (error) {
        if (mounted && token === generation) {
          deps.onMessage(error instanceof Error ? error.message : String(error));
          try { await deps.refresh(); } catch { /* Keep the original error visible. */ }
        }
      } finally {
        if (mounted && token === generation) inFlight = false;
      }
    },
    resume(id: string) { beginResume(id); },
    async resumePolling() {
      if (!mounted || current?.status !== 'running') return;
      clearTimeout(timer);
      const token = ++generation;
      pollErrors = 0;
      pollCount = 0;
      setPaused(false);
      await doPoll(token);
    },
    async cancel() {
      if (!mounted || inFlight || current?.status !== 'running') return;
      inFlight = true;
      const token = ++generation;
      clearTimeout(timer);
      try {
        const job = await deps.cancel(current.id);
        if (!mounted || token !== generation) return;
        publish(job);
        if (job.status === 'running') {
          pollErrors = 0;
          pollCount = 0;
          setPaused(false);
          deps.onMessage(job.detail);
          schedule(token);
        } else {
          await finish(job, token);
        }
      } catch (error) {
        if (mounted && token === generation) {
          deps.onMessage(`Cancel failed: ${String(error)}`);
          schedule(token);
        }
      } finally {
        if (mounted && token === generation) inFlight = false;
      }
    },
    async refresh() {
      if (!mounted || inFlight) return;
      inFlight = true;
      try { await deps.refresh(); }
      finally { if (mounted) inFlight = false; }
    },
    async open(fn: () => Promise<string>) {
      if (!mounted || inFlight) return;
      inFlight = true;
      try {
        const message = await fn();
        if (mounted) deps.onMessage(message);
      } catch (error) {
        if (mounted) deps.onMessage(String(error));
      } finally {
        if (mounted) inFlight = false;
      }
    },
    dispose() {
      mounted = false;
      generation++;
      clearTimeout(timer);
    },
  };
}

export const DEVELOPMENT_PRESETS = [
  { id: 'git-build', label: 'Git + build tools', packages: ['git', 'build-essential', 'pkg-config', 'cmake'] },
  { id: 'python', label: 'Python', packages: ['python3', 'python3-pip', 'python3-venv'] },
  { id: 'node', label: 'Node.js', packages: ['nodejs', 'npm'] },
  { id: 'rust', label: 'Rust', packages: ['rustc', 'cargo'] },
] as const;
export function presetOperation(id: string): `preset:${string}` | null {
  return DEVELOPMENT_PRESETS.some(preset => preset.id === id) ? `preset:${id}` : null;
}

// Digest-pinned: the 26.10 tag moves, and a moving tag cannot be a security
// boundary. No published Ubuntu image carries the USN-8996-1 apt fix yet, so
// the managed container also upgrades apt before any preset installs packages.
export const DEVELOPMENT_IMAGE =
  'docker.io/library/ubuntu@sha256:b5194b8e58989da0865ebc80078bf9f5f8742ec6a867c53f6ae699f6036756f7';
export const DEVELOPMENT_IMAGE_LABEL = 'Ubuntu 26.10 (digest-pinned)';
export const APT_FLOOR = '3.3.3ubuntu0.1';
export const POLL_LIMIT = 1800;
export const DELETE_WARNING = 'Deleting the container loses installed packages; running processes are stopped and your shared home directory is retained; deletion does not roll back files.';

export function describeState(value: DevelopmentState | null) {
  if (!value || !['absent', 'managed', 'collision', 'unavailable'].includes(value.state)) {
    return {
      canCreate: false,
      canOpen: false,
      canDelete: false,
      message: value?.detail || 'Development environment status is unavailable.',
    };
  }
  return {
    canCreate: value.state === 'absent',
    canOpen: value.state === 'managed',
    canDelete: value.state === 'managed',
    message: value.detail || ({
      absent: 'No Rowyn-managed Ubuntu environment exists.',
      managed: 'Rowyn-managed Ubuntu environment is available.',
      collision: 'A container uses the reserved name but is not owned by Rowyn; it will not be changed.',
      unavailable: 'Status unavailable.',
    } as Record<string, string>)[value.state],
  };
}

export function deleteConfirmation(input: string) {
  return input === DEVELOPMENT_NAME ? null : `Type ${DEVELOPMENT_NAME} to confirm deletion.`;
}

export function shouldPoll(job: Job | null) {
  return job?.status === 'running';
}

export function isRunningJob(job: Job | null) {
  return job?.status === 'running';
}
