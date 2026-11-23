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
export interface ControllerHint { family: string; label: string; hint: string }
export interface ControllerReport { findings: Reading[]; hints: ControllerHint[] }
export async function rowynControllerScan(): Promise<ControllerReport> {
  nativeOnly();
  return invoke<ControllerReport>('rowyn_controller_scan');
}
export async function exportRowynReport(report: string): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_export_report', { report });
}
export async function openRowynLauncher(id: string, gamemode = false): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_launcher_open', { id, gamemode });
}
export async function rowynSteamGamesScan(): Promise<SteamScan> {
  nativeOnly();
  return invoke<SteamScan>('rowyn_steam_games_scan');
}
export interface RecentGame { app_id: string; name: string; last_played: number; playtime_minutes: number | null }
export interface RecentPlay { steam_detected: boolean; profile_read: boolean; warnings: number; unnamed: number; games: RecentGame[] }
export async function rowynSteamRecentPlay(): Promise<RecentPlay> {
  nativeOnly();
  return invoke<RecentPlay>('rowyn_steam_recent_play');
}
export interface ShaderCacheStatus { steam_detected: boolean; config_read: boolean; pre_caching: boolean | null; warnings: number }
export async function rowynSteamShaderCache(): Promise<ShaderCacheStatus> {
  nativeOnly();
  return invoke<ShaderCacheStatus>('rowyn_steam_shader_cache');
}
export interface MangohudConfig { steam_detected: boolean; flatpak_steam: boolean; path: string; exists: boolean; readable: boolean; settings: number; native_path: string; native_exists: boolean; warnings: number }
export async function rowynMangohudConfig(): Promise<MangohudConfig> {
  nativeOnly();
  return invoke<MangohudConfig>('rowyn_mangohud_config');
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
export interface DisplayModes { outputs: DisplayMode[]; detail: string }
export async function rowynDisplayModesScan(): Promise<DisplayModes> {
  nativeOnly();
  return invoke<DisplayModes>('rowyn_display_modes_scan');
}
export interface VulkanLayers { steam_runtime_branch: string | null; mangohud_branches: string[]; gamescope_branches: string[]; vkbasalt_branches: string[]; detail: string }
export async function rowynVulkanLayersScan(): Promise<VulkanLayers> {
  nativeOnly();
  return invoke<VulkanLayers>('rowyn_vulkan_layers_scan');
}
export interface GamemodeStatus { daemon_installed: boolean; daemon_active: boolean; daemon_scope: string | null; sandbox_client: boolean; available: boolean; detail: string }
export async function rowynGamemodeStatus(): Promise<GamemodeStatus> {
  nativeOnly();
  return invoke<GamemodeStatus>('rowyn_gamemode_status');
}
export interface GamescopeStatus { available: boolean; detail: string }
export async function rowynGamescopeStatus(): Promise<GamescopeStatus> {
  nativeOnly();
  return invoke<GamescopeStatus>('rowyn_gamescope_status');
}
export interface SunshineStatus { installed: boolean; autostart: boolean; firewall_open: boolean; detail: string }
export async function rowynSunshineStatus(): Promise<SunshineStatus> {
  nativeOnly();
  return invoke<SunshineStatus>('rowyn_sunshine_status');
}
export async function rowynSunshineFirewallOpen(): Promise<SunshineStatus> {
  nativeOnly();
  return invoke<SunshineStatus>('rowyn_sunshine_firewall_open');
}
export async function rowynSunshineAutostartEnable(): Promise<SunshineStatus> {
  nativeOnly();
  return invoke<SunshineStatus>('rowyn_sunshine_autostart_enable');
}
export async function rowynVulkanLayerInstall(layer: 'mangohud' | 'gamescope' | 'vkbasalt'): Promise<VulkanLayers> {
  nativeOnly();
  return invoke<VulkanLayers>('rowyn_vulkan_layer_install', { layer });
}
