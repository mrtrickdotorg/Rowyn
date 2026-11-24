//! Small curated launcher opener; installs remain in the existing Flatpak jobs.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use std::path::{Path, PathBuf};
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
pub(crate) async fn rowyn_launcher_open(
    id: String,
    gamemode: Option<bool>,
) -> Result<String, String> {
    launcher_id(&id)?;
    let gamemode = gamemode.unwrap_or(false);
    tauri::async_runtime::spawn_blocking(move || {
        let user = scope_apps("--user");
        let system = scope_apps("--system");
        let failed: Vec<&str> = [("user", &user), ("system", &system)].iter()
            .filter_map(|(scope, result)| result.is_err().then_some(*scope)).collect();
        let scope = installed_scope(&id, user.as_deref().unwrap_or(""), system.as_deref().unwrap_or(""), &failed)?;
        // GameMode wraps the launcher on the host, so its session hooks run for
        // as long as the launcher does. The sandbox is never entered or escaped:
        // gamemoderun starts the same `flatpak run` this command always starts.
        let mut command = if gamemode {
            if !Path::new(GAMEMODERUN).is_file() {
                return Err("GameMode is not installed on this system, so the launcher was not started under it.".into());
            }
            let mut command = Command::new(GAMEMODERUN);
            command.args(["/usr/bin/flatpak", "run", scope, "--", &id]);
            command
        } else {
            let mut command = Command::new("/usr/bin/flatpak");
            command.args(["run", scope, "--", &id]);
            command
        };
        command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        spawn_detached(&mut command).map_err(|_| "Could not start launcher.".to_string())?;
        // Detached application: do not claim its window or login succeeded.
        Ok(if gamemode {
            "Launch requested under GameMode. GameMode stays active while the launcher runs; window readiness is not verified."
        } else {
            "Launch requested. If no window appears, inspect the launcher itself; window readiness is not verified."
        }
        .into())
    }).await.map_err(|_| "Launcher worker failed".to_string())?
}
// --- Display HDR/VRR status -------------------------------------------------
// Read-only: parse the same kscreen-doctor output the Diagnostics page
// probes and surface the per-output Hdr:/Vrr: fields verbatim. Plasma 6 keeps
// the adaptive-sync policy per output (KScreen), so there is no page-level
// policy to report beside them. Capability words come from the display stack
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
        // Plasma 6 keeps the adaptive-sync policy per output (KScreen's own
        // Output setting), not in kwinrc's [Wayland] section, so that key is
        // not read here: the per-output value below is what the display stack
        // itself reports, and it is the only one that can be trusted.
        let connected = outputs.iter().filter(|output| output.connected).count();
        Ok(DisplayModes {
            detail: format!(
                "{connected} connected output(s). HDR and VRR values are reported per output by the display stack (kscreen); “incapable” means the display or connection does not offer the feature."
            ),
            outputs,
        })
    })
    .await
    .map_err(|_| "Display scan worker failed".to_string())?
}

// --- Vulkan layer extensions (MangoHud, gamescope, vkBasalt) ---------------
// All reach Flatpak games as Freedesktop Vulkan-layer extensions, not as
// apps: the layer must be installed for the same runtime branch the game
// uses. Steam's branch is read from flatpak's own metadata; installs pin
// exactly that branch and nothing else.

pub(crate) const MANGOHUD_LAYER: &str = "org.freedesktop.Platform.VulkanLayer.MangoHud";
pub(crate) const GAMESCOPE_LAYER: &str = "org.freedesktop.Platform.VulkanLayer.gamescope";
pub(crate) const VKBASALT_LAYER: &str = "org.freedesktop.Platform.VulkanLayer.vkBasalt";

#[derive(Debug, serde::Serialize)]
pub(crate) struct VulkanLayers {
    steam_runtime_branch: Option<String>,
    mangohud_branches: Vec<String>,
    gamescope_branches: Vec<String>,
    vkbasalt_branches: Vec<String>,
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
    let vkbasalt = layer_branches(&refs, VKBASALT_LAYER);
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
        vkbasalt_branches: vkbasalt,
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
        "vkbasalt" => VKBASALT_LAYER,
        _ => return Err("Unsupported layer.".into()),
    };
    tauri::async_runtime::spawn_blocking(move || {
        let before = scan_vulkan_layers();
        let branch = before
            .steam_runtime_branch
            .clone()
            .ok_or("Steam is not installed; its runtime branch decides which layer branch to install.")?;
        let installed = match layer_id {
            MANGOHUD_LAYER => &before.mangohud_branches,
            VKBASALT_LAYER => &before.vkbasalt_branches,
            _ => &before.gamescope_branches,
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

// --- GameMode (gamemoded) state and the Flatpak client ----------------------
// Rowyn's GameMode hooks (low-latency PipeWire, the performance power profile)
// only run when something asks gamemoded to start. The per-game launch option
// for a sandboxed game is `gamemoderun %command%`, which needs the client
// inside Steam's own sandbox, and the daemon on the host. Both are read here.
// The client can come from the app's own files or from the runtime the app
// declares (freedesktop-sdk 26.08 ships gamemoderun, mounted at /usr in the
// sandbox), so the option is offered only when a deployed file proves it
// exists. Nothing is assumed.

const GAMEMODERUN: &str = "/usr/games/gamemoderun";

#[derive(Debug, PartialEq, Clone)]
struct DaemonState {
    installed: bool,
    active: bool,
    scope: Option<&'static str>,
}

fn unit_state(raw: &str) -> Option<(String, String)> {
    let mut load = None;
    let mut active = None;
    for line in raw.lines() {
        let line = line.trim();
        if let Some(value) = line.strip_prefix("LoadState=") {
            load = Some(value.trim().to_string());
        } else if let Some(value) = line.strip_prefix("ActiveState=") {
            active = Some(value.trim().to_string());
        }
    }
    Some((load?, active?))
}

/// GameMode's daemon is D-Bus-activated, so an installed-but-inactive unit is
/// normal and is not reported as broken. A running unit of either scope wins.
fn daemon_state_from(
    user: Option<(String, String)>,
    system: Option<(String, String)>,
) -> DaemonState {
    let mut found: Vec<(&'static str, (String, String))> = Vec::new();
    for (scope, state) in [("user", user), ("system", system)] {
        if let Some(state) = state {
            if state.0 != "not-found" && !state.0.is_empty() {
                found.push((scope, state));
            }
        }
    }
    match found
        .iter()
        .find(|(_, (_, active))| active == "active")
        .or_else(|| found.first())
    {
        Some((scope, (_, active))) => DaemonState {
            installed: true,
            active: active == "active",
            scope: Some(scope),
        },
        None => DaemonState {
            installed: false,
            active: false,
            scope: None,
        },
    }
}

fn systemctl_show(scope: &str) -> Option<(String, String)> {
    let mut command = Command::new("/usr/bin/systemctl");
    if scope == "user" {
        command.arg("--user");
    }
    command
        .args(["show", "-p", "LoadState", "-p", "ActiveState", "gamemoded"])
        .env("LC_ALL", "C");
    let output = run_bounded_command(command, Duration::from_secs(5)).ok()?;
    if !output.status.success() {
        return None;
    }
    unit_state(&String::from_utf8_lossy(&output.stdout))
}

/// A deployed ref keeps `active` as a symlink to its current commit; some
/// flatpak versions nest that under `current/`. Whichever exists is the
/// deployment in use, so no commit hash is guessed.
fn flatpak_deployment(branch_dir: &Path) -> Option<PathBuf> {
    [branch_dir.join("active"), branch_dir.join("current/active")]
        .into_iter()
        .find(|path| path.is_dir())
}

/// Every deployed arch/branch of one ref under a flatpak installation root.
fn flatpak_deployments(root: &Path, kind: &str, id: &str) -> Vec<PathBuf> {
    let mut deployments = Vec::new();
    let Ok(arches) = std::fs::read_dir(root.join(kind).join(id)) else {
        return deployments;
    };
    for arch in arches.flatten() {
        let Ok(branches) = std::fs::read_dir(arch.path()) else {
            continue;
        };
        for branch in branches.flatten() {
            if let Some(deployment) = flatpak_deployment(&branch.path()) {
                deployments.push(deployment);
            }
        }
    }
    deployments
}

/// `runtime=ID/ARCH/BRANCH` from the app's own metadata, so the runtime the
/// sandbox will actually mount is read rather than guessed.
fn flatpak_app_runtime(deployment: &Path) -> Option<(String, String, String)> {
    let metadata = std::fs::read_to_string(deployment.join("metadata")).ok()?;
    let runtime = metadata
        .lines()
        .find_map(|line| line.trim().strip_prefix("runtime="))?;
    let mut parts = runtime.trim().split('/');
    Some((
        parts.next()?.to_string(),
        parts.next()?.to_string(),
        parts.next()?.to_string(),
    ))
}

/// `gamemoderun %command%` runs inside Steam's sandbox, so the client has to be
/// in the sandbox's `/usr` or `/app`: the app's own files, or the runtime the
/// app declares, which mounts at `/usr`. A missing client is a fact about this
/// Steam build, not an error.
fn flatpak_gamemoderun_in(roots: &[PathBuf], app_id: &str) -> bool {
    roots.iter().any(|root| {
        flatpak_deployments(root, "app", app_id)
            .into_iter()
            .any(|deployment| {
                if deployment.join("files/bin/gamemoderun").is_file() {
                    return true;
                }
                let Some((id, arch, branch)) = flatpak_app_runtime(&deployment) else {
                    return false;
                };
                flatpak_deployment(&root.join("runtime").join(id).join(arch).join(branch))
                    .is_some_and(|runtime| runtime.join("files/bin/gamemoderun").is_file())
            })
    })
}

fn flatpak_app_roots(home: &Path) -> Vec<PathBuf> {
    vec![
        home.join(".local/share/flatpak"),
        PathBuf::from("/var/lib/flatpak"),
    ]
}

#[derive(Debug, serde::Serialize)]
pub(crate) struct GamemodeStatus {
    daemon_installed: bool,
    daemon_active: bool,
    daemon_scope: Option<String>,
    sandbox_client: bool,
    available: bool,
    detail: String,
}

fn gamemode_status(daemon: DaemonState, sandbox_client: bool) -> GamemodeStatus {
    let detail = if !sandbox_client {
        "Neither this Steam Flatpak nor the runtime it uses ships gamemoderun, so `gamemoderun %command%` would fail as an unknown command. Rowyn's GameMode hooks can still cover a whole launcher session: start the launcher under GameMode instead."
    } else if !daemon.installed {
        "gamemoderun is present in Steam's sandbox, but gamemoded is not installed, so there is nothing to activate. Install the gamemode daemon."
    } else {
        "GameMode's client is present in Steam's sandbox, but in-game activation has not been tested on the Rowyn image yet. Per-process registration is not expected to cross the Flatpak PID namespace, so treat renice and CPU pinning as unconfirmed; a Proton or Steam Linux Runtime title cannot load the client at all."
    };
    GamemodeStatus {
        daemon_installed: daemon.installed,
        daemon_active: daemon.active,
        daemon_scope: daemon.scope.map(str::to_string),
        sandbox_client,
        available: daemon.installed && sandbox_client,
        detail: detail.to_string(),
    }
}

#[tauri::command]
pub(crate) async fn rowyn_gamemode_status() -> Result<GamemodeStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let daemon = daemon_state_from(systemctl_show("user"), systemctl_show("system"));
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_default();
        gamemode_status(
            daemon,
            flatpak_gamemoderun_in(&flatpak_app_roots(&home), "com.valvesoftware.Steam"),
        )
    })
    .await
    .map_err(|_| "GameMode check worker failed".to_string())
}

// --- Host gamescope availability -------------------------------------------
// The per-game gamescope option is a host-side wrapper: it prefixes the
// command with `gamescope`. The image deliberately installs no gamescope
// (docs/gaming.md), so the Play page offers that option only when this
// session can actually resolve the binary. A PATH miss is reported as
// unavailable, never assumed.

#[derive(Debug, serde::Serialize)]
pub(crate) struct GamescopeStatus {
    available: bool,
    detail: String,
}

fn gamescope_status_from(found: Option<std::path::PathBuf>) -> GamescopeStatus {
    match found {
        Some(_) => GamescopeStatus {
            available: true,
            detail: "gamescope is available to this session; the option wraps the game's command in it."
                .into(),
        },
        None => GamescopeStatus {
            available: false,
            detail: "gamescope is not in this session's PATH, so a gamescope launch option would fail. Rowyn does not install it: install gamescope on the host, or run the game inside a Distrobox container that provides it."
                .into(),
        },
    }
}

#[tauri::command]
pub(crate) async fn rowyn_gamescope_status() -> Result<GamescopeStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        gamescope_status_from(rowyn_shared::system::process::find_executable("gamescope"))
    })
    .await
    .map_err(|_| "gamescope check worker failed".to_string())
}

#[cfg(test)]
#[path = "rowyn_play_tests.rs"]
mod tests;
