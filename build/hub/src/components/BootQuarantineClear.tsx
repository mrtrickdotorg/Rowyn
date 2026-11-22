import { useState } from 'react';
import { clearBootQuarantine } from '../services/rowynQuarantine';

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
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function clear() {
    setBusy(true);
    setMessage('');
    try {
      const detail = await clearBootQuarantine(digest, true);
      setOpen(false);
      setMessage(detail);
      onCleared();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="rowyn-quarantine-action">
      {!open && (
        <button className="action-button" type="button" onClick={() => setOpen(true)} disabled={busy}>
          Clear quarantine
        </button>
      )}
      {/* In-card confirmation, like RowynDebCard: no backdrop and no focus
          trap, so the dialog is not claimed to be modal. */}
      {open && (
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
            <button className="action-button action-button-primary" type="button" onClick={() => void clear()} disabled={busy}>
              {busy ? 'Clearing…' : 'Clear quarantine'}
            </button>
            <button className="action-button" type="button" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {message && <span className="card-copy" role="status">{message}</span>}
    </span>
  );
}
