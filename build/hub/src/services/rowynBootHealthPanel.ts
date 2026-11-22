import type { BootHealthSnapshot } from './rowynBootHealthModel';

/**
 * The panel's read cycle, kept out of the component so both refresh paths can
 * be exercised without a DOM: the Refresh button and the re-read that follows a
 * cleared quarantine. The bridge is a parameter, so a test passes a counting
 * stand-in and asserts how many times the record was actually re-read.
 *
 * The component owns nothing but state and markup: it publishes views here and
 * hands `onCleared` to BootQuarantineClear, which calls it only after the bridge
 * reported a real clear.
 */
export interface BootHealthPanelView {
  snapshot: BootHealthSnapshot | null;
  error: string;
  busy: boolean;
}

export const BOOT_HEALTH_VIEW_EMPTY: BootHealthPanelView = { snapshot: null, error: '', busy: false };

export type LoadBootHealth = () => Promise<BootHealthSnapshot>;
export type PublishBootHealthView = (view: BootHealthPanelView) => void;

export interface BootHealthPanel {
  /** Reads the record again and replaces whatever is shown. */
  read: () => Promise<void>;
  /** What a cleared quarantine asks for: the record is read again, nothing else. */
  onCleared: () => Promise<void>;
  view: () => BootHealthPanelView;
  /** The component's effect activates the panel it renders. */
  activate: () => void;
  /** Unmount stops publication and drops any read still in flight. */
  dispose: () => void;
}

export function createBootHealthPanel(
  load: LoadBootHealth,
  publish: PublishBootHealthView = () => {},
): BootHealthPanel {
  let view = BOOT_HEALTH_VIEW_EMPTY;
  let generation = 0;
  let active = true;

  function show(next: BootHealthPanelView) {
    view = next;
    if (active) publish(view);
  }

  async function read() {
    // One read at a time wins: a slower earlier answer cannot overwrite a
    // newer one, which matters when a clear re-reads while Refresh is running.
    const token = ++generation;
    show({ snapshot: view.snapshot, error: '', busy: true });
    try {
      const snapshot = await load();
      if (!active || token !== generation) return;
      show({ snapshot, error: '', busy: false });
    } catch (cause) {
      if (!active || token !== generation) return;
      show({
        snapshot: null,
        error: cause instanceof Error ? cause.message : String(cause),
        busy: false,
      });
    }
  }

  return {
    read,
    onCleared: () => read(),
    view: () => view,
    activate: () => { active = true; },
    dispose: () => { active = false; generation++; },
  };
}
