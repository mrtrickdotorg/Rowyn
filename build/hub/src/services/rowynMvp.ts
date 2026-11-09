import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';
import type { DiagnosticsSnapshot, Reading, SteamScan } from './rowynMvpModel';

// Native reads have their own 8s-per-probe bounds and backend single-flight.
// No timer race here: clearing a UI timeout while native work continues would
// allow another click to stack work. The read settles only with the backend.
function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}
export async function rowynDiagnosticsSnapshot(): Promise<DiagnosticsSnapshot> {
  nativeOnly();
  return invoke<DiagnosticsSnapshot>('rowyn_diagnostics_snapshot');
}
export async function rowynControllerScan(): Promise<Reading[]> {
  nativeOnly();
  return invoke<Reading[]>('rowyn_controller_scan');
}
export async function exportRowynReport(report: string): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_export_report', { report });
}
export async function openRowynLauncher(id: string): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_launcher_open', { id });
}
export async function rowynSteamGamesScan(): Promise<SteamScan> {
  nativeOnly();
  return invoke<SteamScan>('rowyn_steam_games_scan');
}
export interface SteamPlayStatus { steam_present: boolean; steam_running: boolean; mapping_present: boolean; detail: string }
export async function rowynSteamPlayStatus(): Promise<SteamPlayStatus> {
  nativeOnly();
  return invoke<SteamPlayStatus>('rowyn_steam_play_status');
}
export async function rowynSteamPlayApply(): Promise<SteamPlayStatus> {
  nativeOnly();
  return invoke<SteamPlayStatus>('rowyn_steam_play_apply');
}
export interface DisplayMode { name: string; connected: boolean; hdr: string | null; vrr: string | null }
export interface DisplayModes { outputs: DisplayMode[]; vrr_policy: string | null; detail: string }
export async function rowynDisplayModesScan(): Promise<DisplayModes> {
  nativeOnly();
  return invoke<DisplayModes>('rowyn_display_modes_scan');
}
export interface VulkanLayers { steam_runtime_branch: string | null; mangohud_branches: string[]; gamescope_branches: string[]; detail: string }
export async function rowynVulkanLayersScan(): Promise<VulkanLayers> {
  nativeOnly();
  return invoke<VulkanLayers>('rowyn_vulkan_layers_scan');
}
export async function rowynVulkanLayerInstall(layer: 'mangohud' | 'gamescope'): Promise<VulkanLayers> {
  nativeOnly();
  return invoke<VulkanLayers>('rowyn_vulkan_layer_install', { layer });
}
