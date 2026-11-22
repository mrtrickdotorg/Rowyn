/**
 * The confirmation dialog's states, kept out of the component so the click path
 * is testable without a DOM and without a Tauri backend.
 *
 * The component owns one of these values and calls the transitions below; the
 * bridge call itself is a parameter, so a test can assert the exact arguments
 * that reach it.
 */
export interface QuarantineDialogState {
  open: boolean;
  busy: boolean;
  message: string;
  /** True only after the bridge reported success: the panel refreshes on it. */
  cleared: boolean;
}

export const QUARANTINE_DIALOG_CLOSED: QuarantineDialogState = {
  open: false,
  busy: false,
  message: '',
  cleared: false,
};

/** The bridge also reports success through this message. */
export type ClearQuarantine = (digest: string, confirmed: boolean) => Promise<string>;

export function openQuarantineDialog(state: QuarantineDialogState): QuarantineDialogState {
  return { ...state, open: true, busy: false, message: '', cleared: false };
}

/**
 * Cancel is inert while the bridge is running: the helper is already clearing,
 * and there is nothing to abandon. It returns the state unchanged rather than a
 * copy so no caller can observe a half-closed dialog.
 */
export function cancelQuarantineDialog(state: QuarantineDialogState): QuarantineDialogState {
  if (state.busy) return state;
  return { ...state, open: false };
}

export function beginQuarantineClear(state: QuarantineDialogState): QuarantineDialogState {
  return { ...state, busy: true, message: '', cleared: false };
}

/**
 * Runs the confirmed clear: always with `confirmed: true`, since reaching this
 * function means the dialog was acknowledged. A refusal keeps the dialog open
 * with the bridge's own message and asks for no refresh.
 */
export async function finishQuarantineClear(
  digest: string,
  clear: ClearQuarantine,
): Promise<QuarantineDialogState> {
  try {
    const message = await clear(digest, true);
    return { open: false, busy: false, message, cleared: true };
  } catch (cause) {
    return {
      open: true,
      busy: false,
      message: cause instanceof Error ? cause.message : String(cause),
      cleared: false,
    };
  }
}
