import { useState } from 'react';
import { clearBootQuarantine } from '../services/rowynQuarantine';
import {
  beginQuarantineClear,
  cancelQuarantineDialog,
  finishQuarantineClear,
  openQuarantineDialog,
  QUARANTINE_DIALOG_CLOSED,
  type QuarantineDialogState,
} from '../services/rowynQuarantineModel';

/**
 * The confirmation itself. Pure props and no state of its own, so the markup a
 * user sees can be rendered and checked without a DOM.
 */
export function QuarantineDialog({
  digest,
  reasonLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  digest: string;
  reasonLabel?: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    /* In-card confirmation, like RowynDebCard: no backdrop and no focus trap,
       so the dialog is not claimed to be modal. */
    <div
      role="alertdialog"
      aria-modal="false"
      aria-label="Clear this quarantine?"
      className="rowyn-quarantine-dialog glass"
    >
      <p><strong>Clear the quarantine for this deployment?</strong></p>
      <p>
        <code className="break-all">{digest}</code>
        {reasonLabel ? ` — ${reasonLabel}` : ''}
      </p>
      <p className="card-copy">
        Clearing only allows this image to be staged again. Nothing is downloaded, installed,
        or restarted, and the boot checks still judge it on its next boot.
      </p>
      <div className="home-actions">
        <button className="action-button action-button-primary" type="button" onClick={onConfirm} disabled={busy}>
          {busy ? 'Clearing…' : 'Clear quarantine'}
        </button>
        <button className="action-button" type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * One explicit, confirmed quarantine clear.
 *
 * The read-only panel shows the quarantine; this is the only write, it happens
 * only after the dialog is confirmed, and the copy states what clearing does
 * and does not do (nothing is downloaded, installed, or restarted).
 */
export function BootQuarantineClear({
  digest,
  reasonLabel,
  onCleared,
}: {
  digest: string;
  reasonLabel?: string;
  onCleared: () => void;
}) {
  const [state, setState] = useState<QuarantineDialogState>(QUARANTINE_DIALOG_CLOSED);

  async function confirm() {
    setState(beginQuarantineClear);
    // `onCleared` runs only after the bridge reports a real clear, so a refusal
    // can never look like a freshly read record.
    setState(await finishQuarantineClear(digest, clearBootQuarantine, onCleared));
  }

  return (
    <span className="rowyn-quarantine-action">
      {!state.open && (
        <button
          className="action-button"
          type="button"
          onClick={() => setState(openQuarantineDialog)}
          disabled={state.busy}
        >
          Clear quarantine
        </button>
      )}
      {state.open && (
        <QuarantineDialog
          digest={digest}
          reasonLabel={reasonLabel}
          busy={state.busy}
          onConfirm={() => void confirm()}
          onCancel={() => setState(cancelQuarantineDialog)}
        />
      )}
      {state.message && <span className="card-copy" role="status">{state.message}</span>}
    </span>
  );
}
