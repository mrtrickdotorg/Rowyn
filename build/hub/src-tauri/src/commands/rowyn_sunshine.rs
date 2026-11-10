//! Rowyn Sunshine setup: the automatable parts of turning this machine
//! into a Moonlight game-stream host, and honest status for the rest.
//!
//! What the Hub can do itself:
//! - report whether the Sunshine Flatpak is installed (the install
//!   itself goes through the standard Apps/Play job path);
//! - enable start-at-login by writing the user's autostart entry
//!   (Sunshine's Flatpak ships no systemd user unit to enable);
//! - open Sunshine's documented ports in firewalld through the same
//!   `sudo -A` askpass discipline the VPN code uses.
//!
//! What stays manual, and the page copy says so: pairing a Moonlight
//! client happens in Sunshine's web UI (https://localhost:47990), and
//! controller/keyboard injection needs /dev/uinput + /dev/uhid access
//! for the desktop session — Rowyn ships uaccess udev rules in the
//! image for exactly this, and the Flatpak itself requests
//! `--device=all`, so no group juggling is left for the user.

use std::process::Command;

pub(crate) const SUNSHINE_ID: &str = "dev.lizardbyte.app.Sunshine";

// Sunshine's documented ports (LizardByte port-forwarding docs):
// TCP 47984 (pairing/HTTPS), 47989 (discovery/HTTP), 48010 (RTSP);
// UDP 47998-48000 (video/audio/control), 48002 (microphone), 48010.
pub(crate) const SUNSHINE_TCP_PORTS: [&str; 3] = ["47984/tcp", "47989/tcp", "48010/tcp"];
pub(crate) const SUNSHINE_UDP_PORTS: [&str; 3] = ["47998-48000/udp", "48002/udp", "48010/udp"];

const ASKPASS_PATH: &str = "/usr/bin/ksshaskpass";

#[derive(Debug, serde::Serialize)]
pub(crate) struct SunshineStatus {
    installed: bool,
    autostart: bool,
    firewall_open: bool,
    detail: String,
}

fn run_capture(program: &str, args: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(args)
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|error| format!("Could not run {program}: {error}"))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(if stderr.is_empty() {
            format!("{program} failed")
        } else {
            stderr
        })
    }
}

fn sunshine_installed() -> bool {
    run_capture("flatpak", &["info", SUNSHINE_ID]).is_ok()
}

fn autostart_path() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME").map(|home| {
        std::path::PathBuf::from(home)
            .join(".config/autostart")
            .join("dev.lizardbyte.app.Sunshine.desktop")
    })
}

fn autostart_enabled() -> bool {
    autostart_path().is_some_and(|path| path.exists())
}

/// Which of Sunshine's ports are missing from a `firewall-cmd
/// --list-ports` answer. The UDP video range may be listed as one
/// range token or as its three single ports; both count as open.
pub(crate) fn missing_ports(list_output: &str) -> Vec<String> {
    let listed: Vec<&str> = list_output.split_whitespace().collect();
    let mut missing: Vec<String> = Vec::new();
    for port in SUNSHINE_TCP_PORTS.iter().chain(SUNSHINE_UDP_PORTS.iter()) {
        if listed.contains(port) {
            continue;
        }
        if *port == "47998-48000/udp"
            && ["47998/udp", "47999/udp", "48000/udp"]
                .iter()
                .all(|single| listed.contains(single))
        {
            continue;
        }
        missing.push((*port).to_string());
    }
    missing
}

fn scan_sunshine() -> SunshineStatus {
    let installed = sunshine_installed();
    let autostart = autostart_enabled();
    let (firewall_open, firewall_note) = match run_capture("firewall-cmd", &["--list-ports"]) {
        Ok(listed) => {
            let missing = missing_ports(&listed);
            if missing.is_empty() {
                (true, String::new())
            } else {
                (false, format!(" Missing ports: {}.", missing.join(", ")))
            }
        }
        Err(_) => (
            false,
            " firewalld did not answer, so its port state is unknown.".to_string(),
        ),
    };
    let detail = if !installed {
        "Sunshine is not installed yet. Install it, then enable start-at-login and open the firewall ports here."
            .to_string()
    } else {
        format!(
            "Sunshine is installed. Start-at-login is {}; the firewall ports are {}.{firewall_note}",
            if autostart { "on" } else { "off" },
            if firewall_open { "open" } else { "not all open" },
        )
    };
    SunshineStatus {
        installed,
        autostart,
        firewall_open,
        detail,
    }
}

#[tauri::command]
pub(crate) async fn rowyn_sunshine_status() -> Result<SunshineStatus, String> {
    tauri::async_runtime::spawn_blocking(scan_sunshine)
        .await
        .map_err(|error| error.to_string())
}

/// Same askpass discipline as the VPN module: scrub the environment to
/// the desktop policy set, then pin the fixed helper; fail closed when
/// the helper is missing so sudo never dies without a prompt.
fn prepare_sudo_command(command: &mut Command) -> Result<(), String> {
    let inherited = std::env::vars().collect::<std::collections::BTreeMap<_, _>>();
    let desktop = rowyn_shared::commands::environment_for(
        rowyn_shared::commands::EnvironmentPolicy::Desktop,
        &inherited,
    );
    command.env_clear().envs(desktop);
    if std::path::Path::new(ASKPASS_PATH).exists() {
        command.env("SUDO_ASKPASS", ASKPASS_PATH);
        Ok(())
    } else {
        Err("Cannot prompt for administrator access: the askpass helper is missing.".to_string())
    }
}

fn run_sudo_firewall(args: &[String]) -> Result<String, String> {
    let mut command = Command::new("sudo");
    command
        .arg("-A")
        .arg("firewall-cmd")
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    prepare_sudo_command(&mut command)?;
    let output = command
        .output()
        .map_err(|error| format!("Could not run firewall-cmd: {error}"))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(if stderr.is_empty() {
            "firewall-cmd failed".to_string()
        } else {
            stderr
        })
    }
}

#[tauri::command]
pub(crate) async fn rowyn_sunshine_firewall_open() -> Result<SunshineStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let mut args: Vec<String> = vec!["--permanent".to_string()];
        for port in SUNSHINE_TCP_PORTS.iter().chain(SUNSHINE_UDP_PORTS.iter()) {
            args.push(format!("--add-port={port}"));
        }
        run_sudo_firewall(&args)?;
        run_sudo_firewall(&["--reload".to_string()])?;
        Ok(scan_sunshine())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_sunshine_autostart_enable() -> Result<SunshineStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        if !sunshine_installed() {
            return Err("Install Sunshine first; the autostart entry points at it.".to_string());
        }
        let path = autostart_path()
            .ok_or("No home directory is set, so the autostart entry has nowhere to go.")?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("Could not create the autostart folder: {error}"))?;
        }
        let entry = "[Desktop Entry]\n\
                     Type=Application\n\
                     Name=Sunshine\n\
                     Comment=Game stream host — start at login (enabled by Rowyn Hub)\n\
                     Exec=flatpak run dev.lizardbyte.app.Sunshine\n\
                     X-GNOME-Autostart-enabled=true\n";
        std::fs::write(&path, entry)
            .map_err(|error| format!("Could not write the autostart entry: {error}"))?;
        Ok(scan_sunshine())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
#[path = "rowyn_sunshine_tests.rs"]
mod tests;
