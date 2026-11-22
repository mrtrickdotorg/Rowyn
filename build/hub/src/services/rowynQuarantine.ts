import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';

function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}

/**
 * Clears one quarantine through the native bridge. `confirmed` is the page's
 * acknowledgement that clearing only allows the digest to be staged again; the
 * bridge refuses without it and re-checks that the digest is still quarantined
 * in the record it reads itself.
 */
export async function clearBootQuarantine(digest: string, confirmed: boolean): Promise<string> {
  nativeOnly();
  return await invoke<string>('rowyn_clear_boot_quarantine', { digest, confirmed });
}
