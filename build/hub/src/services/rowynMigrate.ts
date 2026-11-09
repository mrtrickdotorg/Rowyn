import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';
import type { ImportReport, WindowsInstall } from './rowynMigrateModel';

function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}

export async function rowynMigrateScan(): Promise<WindowsInstall[]> {
  nativeOnly();
  return invoke<WindowsInstall[]>('rowyn_migrate_scan');
}

export async function rowynMigrateMount(device: string): Promise<WindowsInstall[]> {
  nativeOnly();
  return invoke<WindowsInstall[]>('rowyn_migrate_mount', { device });
}

export async function rowynMigrateImport(
  mountpoint: string,
  profile: string,
  categories: string[],
): Promise<ImportReport> {
  nativeOnly();
  return invoke<ImportReport>('rowyn_migrate_import', { mountpoint, profile, categories });
}
