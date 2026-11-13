export interface DebContainerState {
  state: string;
  name: string;
  image: string;
  status: string;
  detail: string;
}

export interface DebApp {
  package: string;
  version: string;
  source: string;
  exported_apps: string[];
  exported_bins: string[];
  installed_at: number;
}

export interface DebState {
  container: DebContainerState;
  apps: DebApp[];
  pending: string | null;
}

export interface DebSignal {
  signal: string;
  text: string;
}

export interface DebFlatpak {
  label: string;
  id: string;
}

export interface DebInspectReport {
  package: string;
  version: string;
  architecture: string;
  maintainer: string;
  description: string;
  installed_size_kib: number;
  file_size_bytes: number;
  sha256: string;
  signature: string;
  verdict: string;
  reasons: DebSignal[];
  notes: DebSignal[];
  desktop_files: string[];
  binaries: string[];
  flatpak_hint: DebFlatpak | null;
}

export interface DebJob {
  id: string;
  status: string;
  detail: string;
}

export type DebOperation = "create" | "install" | "delete" | `remove:${string}`;

export interface DebClient {
  start(operation: DebOperation, confirmation?: string, path?: string): Promise<DebJob>;
  job(id: string): Promise<DebJob>;
  cancel(id: string): Promise<DebJob>;
}

export interface DebJobCallbacks {
  onJob(job: DebJob): void;
  onError(error: unknown): void;
}

export interface DebJobController {
  running: boolean;
  start(operation: DebOperation, confirmation?: string, path?: string): Promise<void>;
  cancel(): Promise<void>;
  dispose(): void;
}

export function isDebOperation(value: string): value is DebOperation {
  return value === "create" || value === "install" || value === "delete"
    || /^remove:[a-z0-9][a-z0-9+.-]*$/.test(value);
}

export function describeState(state: DebContainerState): string {
  if (state.state === "managed") {
    return `The app container is ready (rowyn-deb, ${state.status}). Apps installed from .deb files live here, not in the system — they keep working across Rowyn updates.`;
  }
  if (state.state === "collision") {
    return "A container named rowyn-deb exists, but Rowyn does not own it. No changes were made; remove the conflicting container or rename it before continuing.";
  }
  return "The app container has not been created yet. Creating it is a one-time step that downloads an Ubuntu environment (a few hundred megabytes). Apps you install from .deb files will live inside it, separate from the system.";
}

export function signatureText(report: DebInspectReport): string {
  if (report.signature === "embedded") {
    return "This file carries an embedded signature from its publisher.";
  }
  return "This file is NOT signed. That is normal for vendor .deb downloads — only install it if you downloaded it from the vendor's own website.";
}

export function deleteConfirmation(): string {
  return "rowyn-deb";
}

export function installConfirmation(report: DebInspectReport): string {
  return `install:${report.package}`;
}

export function removalOperation(app: DebApp): DebOperation {
  return `remove:${app.package}`;
}

export function removalConfirmation(app: DebApp): string {
  return app.package;
}

export function needsConfirmation(operation: DebOperation): boolean {
  return operation !== "create";
}

export function createJobController(
  client: DebClient,
  callbacks: DebJobCallbacks,
  pollIntervalMs = 800,
): DebJobController {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let activeId: string | null = null;
  let disposed = false;
  let running = false;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const poll = async () => {
    if (disposed || activeId === null) return;
    try {
      const job = await client.job(activeId);
      if (disposed) return;
      if (job.status === "running") {
        callbacks.onJob(job);
        timer = setTimeout(() => void poll(), pollIntervalMs);
        return;
      }
      running = false;
      activeId = null;
      callbacks.onJob(job);
    } catch (error) {
      if (disposed) return;
      running = false;
      activeId = null;
      callbacks.onError(error);
    }
  };

  return {
    get running() {
      return running;
    },
    async start(operation, confirmation, path) {
      if (running || disposed) return;
      running = true;
      try {
        const job = await client.start(operation, confirmation, path);
        if (disposed) return;
        activeId = job.id;
        callbacks.onJob(job);
        if (job.status === "running") {
          timer = setTimeout(() => void poll(), pollIntervalMs);
        } else {
          running = false;
          activeId = null;
        }
      } catch (error) {
        running = false;
        if (!disposed) callbacks.onError(error);
      }
    },
    async cancel() {
      if (activeId === null || disposed) return;
      try {
        const job = await client.cancel(activeId);
        if (!disposed) callbacks.onJob(job);
      } catch (error) {
        if (!disposed) callbacks.onError(error);
      }
    },
    dispose() {
      disposed = true;
      running = false;
      activeId = null;
      clearTimer();
    },
  };
}
