//! Small curated launcher opener; installs remain in the existing Flatpak jobs.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use std::process::{Command, Stdio};
use std::time::Duration;

fn launcher_id(id: &str) -> Result<&str, String> {
    match id {
        "com.valvesoftware.Steam"
        | "com.heroicgameslauncher.hgl"
        | "net.lutris.Lutris"
        | "com.usebottles.bottles" => Ok(id),
        _ => Err("Unsupported launcher.".into()),
    }
}
fn installed_scope<'a>(
    id: &str,
    user: &str,
    system: &str,
    failed: &[&str],
) -> Result<&'a str, String> {
    if user.lines().any(|line| line.trim() == id) {
        return Ok("--user");
    }
    if system.lines().any(|line| line.trim() == id) {
        return Ok("--system");
    }
    if !failed.is_empty() {
        Err("Launcher state unavailable: a Flatpak scope could not be read.".into())
    } else {
        Err("Launcher is not installed. Install it first.".into())
    }
}
fn scope_apps(scope: &str) -> Result<String, String> {
    let mut command = Command::new("/usr/bin/flatpak");
    command
        .args(["list", scope, "--app", "--columns=application"])
        .env("LC_ALL", "C");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|_| "Flatpak installed-app scan unavailable".to_string())?;
    if !output.status.success() || output.stdout.len() > 256_000 {
        return Err("Flatpak installed-app scan failed".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}
#[tauri::command]
pub(crate) async fn rowyn_launcher_open(id: String) -> Result<String, String> {
    launcher_id(&id)?;
    tauri::async_runtime::spawn_blocking(move || {
        let user = scope_apps("--user");
        let system = scope_apps("--system");
        let failed: Vec<&str> = [("user", &user), ("system", &system)].iter()
            .filter_map(|(scope, result)| result.is_err().then_some(*scope)).collect();
        let scope = installed_scope(&id, user.as_deref().unwrap_or(""), system.as_deref().unwrap_or(""), &failed)?;
        let mut command = Command::new("/usr/bin/flatpak");
        command.args(["run", scope, "--", &id]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        spawn_detached(&mut command).map_err(|_| "Could not start launcher.".to_string())?;
        // Detached application: do not claim its window or login succeeded.
        Ok("Launch requested. If no window appears, inspect the launcher itself; window readiness is not verified.".into())
    }).await.map_err(|_| "Launcher worker failed".to_string())?
}
// --- Display HDR/VRR status -------------------------------------------------
// Read-only: parse the same kscreen-doctor output the Diagnostics page
// probes, surface the per-output Hdr:/Vrr: fields verbatim, and report the
// KWin VRR policy from kwinrc. Capability words come from the display stack
// itself; anything absent is reported as not reported, never guessed.

#[derive(Debug, serde::Serialize)]
pub(crate) struct DisplayMode {
    name: String,
    connected: bool,
    hdr: Option<String>,
    vrr: Option<String>,
}

#[derive(Debug, serde::Serialize)]
pub(crate) struct DisplayModes {
    outputs: Vec<DisplayMode>,
    vrr_policy: Option<String>,
    detail: String,
}

fn parse_display_modes(raw: &str) -> Vec<DisplayMode> {
    let clean = rowyn_shared::system::process::strip_ansi(raw);
    let mut outputs: Vec<DisplayMode> = Vec::new();
    for line in clean.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("Output:") {
            // "Output: 1 eDP-1 enabled connected primary laptop panel"
            let mut parts = rest.split_whitespace();
            let _index = parts.next();
            let name = parts.next().unwrap_or("output").to_string();
            let connected = parts.any(|token| token == "connected");
            outputs.push(DisplayMode { name, connected, hdr: None, vrr: None });
        } else if let Some(current) = outputs.last_mut() {
            if let Some(value) = trimmed.strip_prefix("Hdr:") {
                current.hdr = Some(value.trim().to_string());
            } else if let Some(value) = trimmed.strip_prefix("Vrr:") {
                current.vrr = Some(value.trim().to_string());
            }
        }
    }
    outputs
}

fn kwin_vrr_policy(kwinrc: &str) -> Option<String> {
    let mut in_wayland = false;
    for line in kwinrc.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') {
            in_wayland = trimmed == "[Wayland]";
            continue;
        }
        if in_wayland {
            if let Some(value) = trimmed.strip_prefix("VrrPolicy=") {
                return Some(match value.trim() {
                    "0" => "Never".to_string(),
                    "1" => "Automatic (fullscreen applications)".to_string(),
                    "2" => "Always".to_string(),
                    other => format!("Unrecognized value {other}"),
                });
            }
        }
    }
    None
}

#[tauri::command]
pub(crate) async fn rowyn_display_modes_scan() -> Result<DisplayModes, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new("/usr/bin/kscreen-doctor");
        command.arg("-o").env("LC_ALL", "C");
        let output = run_bounded_command(command, Duration::from_secs(8))
            .map_err(|_| "Display scan unavailable.".to_string())?;
        if !output.status.success() || output.stdout.len() > 256_000 {
            return Err("Display scan failed.".into());
        }
        let outputs = parse_display_modes(&String::from_utf8_lossy(&output.stdout));
        if outputs.is_empty() {
            return Err("No display outputs were reported.".into());
        }
        let kwinrc = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .map(|home| home.join(".config/kwinrc"))
            .and_then(|path| std::fs::read_to_string(path).ok())
            .unwrap_or_default();
        let vrr_policy = kwin_vrr_policy(&kwinrc);
        let connected = outputs.iter().filter(|output| output.connected).count();
        Ok(DisplayModes {
            detail: format!(
                "{connected} connected output(s). HDR and VRR values are reported by the display stack (kscreen); “incapable” means the display or connection does not offer the feature."
            ),
            outputs,
            vrr_policy,
        })
    })
    .await
    .map_err(|_| "Display scan worker failed".to_string())?
}

// --- Vulkan layer extensions (MangoHud, gamescope) --------------------------
// Both reach Flatpak games as Freedesktop Vulkan-layer extensions, not as
// apps: the layer must be installed for the same runtime branch the game
// uses. Steam's branch is read from flatpak's own metadata; installs pin
// exactly that branch and nothing else.

pub(crate) const MANGOHUD_LAYER: &str = "org.freedesktop.Platform.VulkanLayer.MangoHud";
pub(crate) const GAMESCOPE_LAYER: &str = "org.freedesktop.Platform.VulkanLayer.gamescope";

#[derive(Debug, serde::Serialize)]
pub(crate) struct VulkanLayers {
    steam_runtime_branch: Option<String>,
    mangohud_branches: Vec<String>,
    gamescope_branches: Vec<String>,
    detail: String,
}

fn layer_branches(list_output: &str, layer_id: &str) -> Vec<String> {
    let mut branches: Vec<String> = list_output
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            let rest = line.strip_prefix(layer_id)?.strip_prefix('/')?;
            // ref form: <id>/<arch>/<branch>, possibly with an origin suffix
            let mut parts = rest.split('/');
            let _arch = parts.next()?;
            parts.next().map(str::to_string)
        })
        .collect();
    branches.sort();
    branches.dedup();
    branches
}

fn steam_runtime_branch(info_output: &str) -> Option<String> {
    for line in info_output.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("Runtime:") {
            // org.freedesktop.Platform/x86_64/24.08
            let mut parts = rest.trim().rsplit('/');
            return parts.next().map(str::to_string);
        }
    }
    None
}

fn flatpak_text(args: &[&str], timeout: Duration) -> Result<String, String> {
    let mut command = Command::new("/usr/bin/flatpak");
    command.args(args).env("LC_ALL", "C");
    let output = run_bounded_command(command, timeout)
        .map_err(|_| "Flatpak query unavailable.".to_string())?;
    if !output.status.success() || output.stdout.len() > 512_000 {
        return Err("Flatpak query failed.".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn scan_vulkan_layers() -> VulkanLayers {
    let refs = flatpak_text(&["list", "--all", "--columns=ref"], Duration::from_secs(10))
        .unwrap_or_default();
    let branch = flatpak_text(&["info", "com.valvesoftware.Steam"], Duration::from_secs(10))
        .ok()
        .and_then(|info| steam_runtime_branch(&info));
    let mangohud = layer_branches(&refs, MANGOHUD_LAYER);
    let gamescope = layer_branches(&refs, GAMESCOPE_LAYER);
    let detail = match &branch {
        Some(branch) => format!(
            "Steam uses the Freedesktop {branch} runtime. A Vulkan layer only reaches Steam games when it is installed for that same branch."
        ),
        None => "Steam was not found, so its runtime branch is unknown; layers install for the branch Steam reports once it is installed.".to_string(),
    };
    VulkanLayers {
        steam_runtime_branch: branch,
        mangohud_branches: mangohud,
        gamescope_branches: gamescope,
        detail,
    }
}

#[tauri::command]
pub(crate) async fn rowyn_vulkan_layers_scan() -> Result<VulkanLayers, String> {
    Ok(tauri::async_runtime::spawn_blocking(scan_vulkan_layers)
        .await
        .map_err(|_| "Vulkan layer scan worker failed".to_string())?)
}

#[tauri::command]
pub(crate) async fn rowyn_vulkan_layer_install(layer: String) -> Result<VulkanLayers, String> {
    let layer_id = match layer.as_str() {
        "mangohud" => MANGOHUD_LAYER,
        "gamescope" => GAMESCOPE_LAYER,
        _ => return Err("Unsupported layer.".into()),
    };
    tauri::async_runtime::spawn_blocking(move || {
        let before = scan_vulkan_layers();
        let branch = before
            .steam_runtime_branch
            .clone()
            .ok_or("Steam is not installed; its runtime branch decides which layer branch to install.")?;
        let installed = if layer_id == MANGOHUD_LAYER {
            &before.mangohud_branches
        } else {
            &before.gamescope_branches
        };
        if installed.iter().any(|found| found == &branch) {
            return Ok(before);
        }
        let reference = format!("{layer_id}//{branch}");
        let mut command = Command::new("/usr/bin/flatpak");
        command
            .args(["install", "--system", "--noninteractive", "-y", "flathub", "--", &reference])
            .stdin(Stdio::null());
        let output = run_bounded_command(command, Duration::from_secs(900))
            .map_err(|_| "Layer install did not complete in time.".to_string())?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
            if stderr.contains("not authorized")
                || stderr.contains("permission denied")
                || stderr.contains("polkit")
            {
                return Err("The administrator authorization for the system-wide install was not given. Nothing was installed.".into());
            }
            return Err("Layer install failed. Nothing was claimed installed; check Discover or try again.".into());
        }
        Ok(scan_vulkan_layers())
    })
    .await
    .map_err(|_| "Vulkan layer install worker failed".to_string())?
}

#[cfg(test)]
#[path = "rowyn_play_tests.rs"]
mod tests;
