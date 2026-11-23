//! B6: the opt-in gaming helpers ship in the image, but nothing in the Hub
//! said so. This module reports whether `rowyn-game-launch`,
//! `rowyn-mimalloc-run`, the gaming slice and `rowyn-tune` are present, reads
//! the tune drop-in, and switches the latency profile through the shipped
//! `rowyn-tune` helper under the same `sudo -A` askpass discipline as the
//! Sunshine firewall action.
//!
//! Only the fixed helper runs, and the boolean picks one of its two fixed
//! arguments; no text from the page reaches a command line.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::rowyn_sunshine::prepare_sudo_command;

/// The drop-in `rowyn-tune on` writes (its default when `ROWYN_TUNE_CONF` is
/// unset); its presence is the whole on/off state.
pub(crate) const TUNE_CONF: &str = "/etc/sysctl.d/90-rowyn-gaming.conf";
pub(crate) const TUNE_BIN: &str = "/usr/bin/rowyn-tune";
pub(crate) const GAME_LAUNCH_BIN: &str = "/usr/bin/rowyn-game-launch";
pub(crate) const MIMALLOC_BIN: &str = "/usr/bin/rowyn-mimalloc-run";
pub(crate) const GAMING_SLICE: &str = "/usr/lib/systemd/user/rowyn-gaming.slice";

#[derive(Debug, serde::Serialize)]
pub(crate) struct GamingHelpers {
    /// "gaming" when the tune drop-in exists, "default" otherwise.
    tune: &'static str,
    /// Every shipped helper file is present.
    installed: bool,
    detail: String,
}

/// `rowyn-tune` accepts only these two actions.
pub(crate) fn tune_action(enabled: bool) -> &'static str {
    if enabled {
        "on"
    } else {
        "off"
    }
}

fn under(root: &Path, absolute: &str) -> PathBuf {
    root.join(absolute.trim_start_matches('/'))
}

fn gaming_helpers_at(root: &Path) -> GamingHelpers {
    let tuned = under(root, TUNE_CONF).exists();
    let missing: Vec<&str> = [TUNE_BIN, GAME_LAUNCH_BIN, MIMALLOC_BIN, GAMING_SLICE]
        .into_iter()
        .filter(|path| !under(root, path).exists())
        .collect();
    let installed = missing.is_empty();
    let mut detail = if tuned {
        "The gaming latency profile is on: rowyn-tune's sysctl drop-in is present, so it applies now and at boot. It trades sustained disk-write throughput for latency; builds and large copies can run slower while it is on.".to_string()
    } else {
        "The gaming latency profile is off: no rowyn-tune drop-in is present, so the kernel defaults apply.".to_string()
    };
    if !installed {
        detail.push_str(&format!(
            " Rowyn gaming helpers missing from this install: {}.",
            missing.join(", ")
        ));
    }
    GamingHelpers {
        tune: if tuned { "gaming" } else { "default" },
        installed,
        detail,
    }
}

#[tauri::command]
pub(crate) async fn rowyn_gaming_helpers_status() -> Result<GamingHelpers, String> {
    tauri::async_runtime::spawn_blocking(|| gaming_helpers_at(Path::new("/")))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub(crate) async fn rowyn_gaming_tune_apply(enabled: bool) -> Result<GamingHelpers, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if !Path::new(TUNE_BIN).exists() {
            return Err("rowyn-tune is missing from this install, so the gaming profile cannot be changed here.".to_string());
        }
        let mut command = Command::new("sudo");
        command
            .arg("-A")
            .arg(TUNE_BIN)
            .arg(tune_action(enabled))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        prepare_sudo_command(&mut command)?;
        let output = command
            .output()
            .map_err(|error| format!("Could not run rowyn-tune: {error}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if stderr.is_empty() {
                "rowyn-tune failed; the gaming profile was not changed.".to_string()
            } else {
                stderr
            });
        }
        Ok(gaming_helpers_at(Path::new("/")))
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
#[path = "rowyn_gaming_helpers_tests.rs"]
mod tests;
