//! Rowyn's read-only diagnostics. No caller-selected program, argv or path.
use rowyn_shared::{
    diagnostics_scrub::scrub_logs,
    system::process::{run_bounded_command, strip_ansi},
};
use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Manager;

const TEXT_LIMIT: usize = 12_000;
const REPORT_LIMIT: usize = 128_000;
static SNAPSHOT_RUNNING: AtomicBool = AtomicBool::new(false);
static CONTROLLER_RUNNING: AtomicBool = AtomicBool::new(false);

/// Every `Reading.state` this module emits. `rowynMvpModel.ts` mirrors the
/// list and `test_hub_overlay_contract` fails when the two drift: the page
/// once tested for "available", a state this bridge never sends, so every
/// healthy check fell into the warning branch and was counted as needing
/// attention.
pub(crate) const READING_STATES: [&str; 4] = ["ok", "attention", "error", "unavailable"];

struct ReadGuard<'a>(&'a AtomicBool);
impl<'a> ReadGuard<'a> {
    fn take(flag: &'a AtomicBool) -> Result<Self, String> {
        flag.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| Self(flag))
            .map_err(|_| "A check is already running.".into())
    }
}
impl Drop for ReadGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Clone, Serialize)]
pub(crate) struct Reading {
    pub name: String,
    pub state: String,
    pub detail: String,
}
#[derive(Serialize)]
pub(crate) struct Snapshot {
    pub collected_at: u64,
    pub readings: Vec<Reading>,
}

fn safe_text(text: &str) -> String {
    // Scrub BEFORE truncation so a cutoff cannot split a secret pattern.
    let clean = super::rowyn_support::sanitize_report(text);
    let mut bounded: String = clean
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .take(TEXT_LIMIT)
        .collect();
    if clean.chars().count() > TEXT_LIMIT {
        bounded.push_str("\n[output truncated]");
    }
    bounded
}
fn section(name: &str, result: Result<String, String>) -> Reading {
    let (state, detail) = match result {
        Ok(value) => (
            "ok",
            if value.trim().is_empty() {
                "None reported.".into()
            } else {
                value
            },
        ),
        Err(value) => (
            if value.starts_with("error:") {
                "error"
            } else {
                "unavailable"
            },
            value,
        ),
    };
    Reading {
        name: name.into(),
        state: state.into(),
        detail: safe_text(&detail),
    }
}
fn probe_failure(stderr: &str) -> String {
    let lower = stderr.to_lowercase();
    let prefix = if [
        "permission denied",
        "access denied",
        "not authorized",
        "authentication required",
        "must be root",
        "requires root",
        "operation not permitted",
        "this command must be executed as the root user",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
    {
        "unavailable"
    } else {
        "error"
    };
    format!("{prefix}: check failed: {}", safe_text(stderr))
}
fn failed_services_section(result: Result<String, String>) -> Reading {
    let has_failures = result.as_ref().is_ok_and(|text| !text.trim().is_empty());
    let mut reading = section("Failed services", result);
    if has_failures {
        reading.state = "attention".into();
    }
    reading
}
fn read_fixed(path: &str) -> Result<String, String> {
    let mut text = String::new();
    fs::File::open(path)
        .and_then(|file| file.take(512_001).read_to_string(&mut text))
        .map_err(|error| format!("unavailable: {error}"))?;
    if text.len() > 512_000 {
        return Err("error: reading exceeded size limit".into());
    }
    Ok(text)
}

#[derive(Clone, Copy)]
enum Probe {
    Bootc,
    Gpu,
    FailedServices,
    Controller,
    Disk,
    Bluetooth, Wifi, WifiRadio, Audio, Displays,
}
fn rfkill_state<'a>(device: &'a serde_json::Value, key: &str) -> Option<&'a str> { device[key].as_str() }
fn parse_rfkill(raw: &str) -> Result<String, String> {
    let data: serde_json::Value = serde_json::from_str(raw).map_err(|_| "error: invalid rfkill JSON")?;
    let devices = data["rfkilldevices"].as_array().ok_or("error: invalid rfkill device list")?;
    let bt: Vec<_> = devices.iter().filter(|d| d["type"].as_str() == Some("bluetooth")).collect();
    if bt.is_empty() { return Ok("Bluetooth radio not present.".into()); }
    if bt.iter().any(|d| rfkill_state(d, "hard") == Some("blocked")) { return Ok("Bluetooth radio hard-blocked.".into()); }
    if bt.iter().any(|d| rfkill_state(d, "hard") != Some("unblocked") || rfkill_state(d, "soft").is_none()) { return Err("unavailable: Bluetooth block state not reported".into()); }
    if bt.iter().all(|d| rfkill_state(d, "soft") == Some("blocked")) { return Ok("Bluetooth radio soft-blocked.".into()); }
    if bt.iter().all(|d| rfkill_state(d, "soft") == Some("unblocked")) { return Ok("Bluetooth radio unblocked; connection not checked.".into()); }
    Err("unavailable: Bluetooth block state not reported".into())
}
fn parse_wifi(raw: &str, radio: &str) -> Result<String, String> {
    let rows: Vec<_> = raw.lines().filter_map(|line| { let f: Vec<_> = line.split(':').collect(); (f.len() >= 3 && f[1] == "wifi").then_some(f[2]) }).collect();
    if rows.is_empty() { return Ok("No Wi-Fi device reported.".into()); }
    match radio.trim() { "enabled" if rows.iter().any(|s| *s == "connected") => Ok("Wi-Fi radio enabled; device connected. Network names omitted.".into()), "enabled" => Ok("Wi-Fi radio enabled; no connected device reported.".into()), "disabled" => Ok("Wi-Fi radio disabled.".into()), _ => Err("unavailable: Wi-Fi radio state not reported".into()) }
}
fn parse_audio(raw: &str) -> Result<String, String> {
    let states: Vec<_> = raw.lines().map(str::trim).filter(|s| !s.is_empty()).collect();
    if states.len() != 2 || states.iter().any(|s| !["active", "inactive", "failed", "activating", "deactivating"].contains(s)) {
        return Err("unavailable: audio service state not fully reported".into());
    }
    Ok(format!("PipeWire: {}; WirePlumber: {}. Service readiness only; audio output is not tested.", states[0], states[1]))
}
fn parse_displays(raw: &str) -> Result<String, String> {
    // kscreen-doctor emits ANSI colors even with stdout piped.
    let raw = strip_ansi(raw);
    let mut outputs: Vec<(Option<bool>, Option<bool>)> = Vec::new();
    for line in raw.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("Output:") {
            outputs.push((None, None));
            // Older kscreen-doctor versions put states inline in the header.
            let tokens: Vec<_> = trimmed.split_whitespace().skip(1).collect();
            let current = outputs.last_mut().unwrap();
            for token in tokens {
                let token = token.trim_matches(['(', ')', ',']);
                match token {
                    "connected" | "disconnected" => {
                        if current.0.replace(token == "connected").is_some() { return Err("unavailable: display state output malformed".into()); }
                    }
                    "enabled" | "disabled" => {
                        if current.1.replace(token == "enabled").is_some() { return Err("unavailable: display state output malformed".into()); }
                    }
                    _ => {}
                }
            }
        } else if let Some(current) = outputs.last_mut() {
            match trimmed {
                "connected" | "disconnected" => if current.0.replace(trimmed == "connected").is_some() { return Err("unavailable: display state output malformed".into()); },
                "enabled" | "disabled" => if current.1.replace(trimmed == "enabled").is_some() { return Err("unavailable: display state output malformed".into()); },
                _ => {}
            }
        }
    }
    if outputs.is_empty() { return Err("unavailable: no display outputs reported".into()); }
    if outputs.iter().any(|(connection, power)| connection.is_none() || power.is_none()) { return Err("unavailable: display state output malformed".into()); }
    let connected = outputs.iter().filter(|(connection, _)| *connection == Some(true)).count();
    let enabled = outputs.iter().filter(|(_, power)| *power == Some(true)).count();
    Ok(format!("{} connected display output(s) reported; {} enabled. Identifiers and configuration omitted.", connected, enabled))
}
fn focused_reading(name: &str, result: Result<String, String>) -> Reading {
    let reading = section(name, result);
    if reading.state != "ok" { return reading; }
    let lower = reading.detail.to_lowercase();
    let attention = match name {
        "Bluetooth" => lower.contains("hard-blocked") || lower.contains("soft-blocked") || lower.contains("not present"),
        "Wi-Fi" => lower.contains("disabled") || lower.contains("no connected device") || lower.contains("not present"),
        "Audio" => lower.contains("inactive") || lower.contains("failed"),
        "Displays" => lower.contains("0 enabled") || lower.contains("0 connected"),
        _ => false,
    };
    Reading { state: if attention { "attention" } else { "ok" }.into(), ..reading }
}
fn probe(kind: Probe) -> Result<String, String> {
    let mut command = match kind {
        Probe::Bootc => {
            let mut c = Command::new("/usr/bin/bootc");
            c.args(["status", "--json"]);
            c
        }
        Probe::Gpu => {
            let mut c = Command::new("/usr/bin/lspci");
            c.args(["-nnk"]);
            c
        }
        Probe::FailedServices => {
            let mut c = Command::new("/usr/bin/systemctl");
            c.args(["--failed", "--no-legend", "--no-pager", "--plain"]);
            c
        }
        Probe::Controller => {
            let mut c = Command::new("/usr/bin/rowyn-controller-check");
            c.arg("--json");
            c
        }
        Probe::Disk => { let mut c = Command::new("/usr/bin/df"); c.args(["-h", "--output=size,used,avail,pcent,target", "/", "/var"]); c }            Probe::Bluetooth => { let mut c = Command::new("/usr/sbin/rfkill"); c.args(["--json"]); c }
        Probe::Wifi => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["-t","-f","DEVICE,TYPE,STATE","device","status"]); c }
        Probe::WifiRadio => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["radio","wifi"]); c }
        Probe::Audio => { let mut c=Command::new("/usr/bin/systemctl"); c.args(["--user","is-active","pipewire.service","wireplumber.service"]); c }
        Probe::Displays => { let mut c=Command::new("/usr/bin/kscreen-doctor"); c.arg("-o"); c }
    };
    command
        .env("LC_ALL", "C")
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|error| format!("unavailable: check could not complete: {error}"))?;
    if !output.status.success() && !matches!(kind, Probe::Audio) {
        return Err(probe_failure(&String::from_utf8_lossy(&output.stderr)));
    }
    if matches!(kind, Probe::Audio) && output.stdout.is_empty() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(if stderr.trim().is_empty() { "unavailable: audio service state not reported".into() } else { probe_failure(&stderr) });
    }
    if output.stdout.len() > 256_000 {
        return Err("error: check exceeded output limit".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}
fn parse_bootc(raw: &str) -> Result<String, String> {
    let data: serde_json::Value =
        serde_json::from_str(raw).map_err(|_| "error: invalid bootc JSON")?;
    let booted = &data["status"]["booted"]["image"];
    let image = booted["image"]["image"]
        .as_str()
        .ok_or("unavailable: booted image not reported")?;
    let digest = booted["imageDigest"].as_str().unwrap_or("unavailable");
    Ok(format!("Booted image: {image}\nDigest: {digest}"))
}
/// One curated line per controller family this scan actually saw. The helper's
/// own `hint` text is dropped with its devices and USB id strings; this table is
/// the only advice the page shows, and it names no command, file or mutation.
const CONTROLLER_FAMILY_HINTS: [(&str, &str, &str); 5] = [
    (
        "playstation",
        "PlayStation pads",
        "DualSense and DualShock 4 pads are handled by the in-kernel hid_playstation driver; Rowyn ships nothing extra for them. Pair them in System Settings → Bluetooth, or connect by USB.",
    ),
    (
        "xbox",
        "Xbox pads",
        "Xbox pads use the in-kernel xpad driver. Bluetooth models have to be paired in System Settings → Bluetooth first; a wired pad is the quickest way to tell a pairing problem from a driver problem.",
    ),
    (
        "nintendo",
        "Switch pads",
        "Switch Pro Controllers use the in-kernel hid_nintendo driver. Without joycond, which Rowyn does not ship, a Joy-Con pair appears as two separate controllers.",
    ),
    (
        "8bitdo",
        "8BitDo pads",
        "If an 8BitDo pad is not recognised, switch it to its Xbox or Switch mode (see the pad's manual); the generic and Xbox drivers then pick it up.",
    ),
    (
        "xbox-dongle",
        "Xbox wireless adapter",
        "The Microsoft wireless adapter needs the out-of-tree xone driver, which Rowyn does not ship. Wired Xbox pads and Bluetooth work without it.",
    ),
];

#[derive(Serialize)]
pub(crate) struct ControllerHint {
    family: String,
    label: String,
    hint: String,
}

#[derive(Serialize)]
pub(crate) struct ControllerReport {
    findings: Vec<Reading>,
    /// Curated advice for the families this scan saw, in table order.
    hints: Vec<ControllerHint>,
    /// Whether Flatpak Steam's own sandbox can reach /dev/input. Absent when the
    /// helper read failed before the launcher question could be asked.
    steam_sandbox: Option<SteamControllerSandbox>,
}

/// What Flatpak Steam's sandbox is granted, and whether this session could see
/// a pad at all. A pad that this session can open is not visible to a sandboxed
/// launcher without `devices=` covering /dev/input, so the Controllers card has
/// to report both or it reports the pad as ready when Steam cannot reach it.
#[derive(Debug, Serialize, Clone)]
pub(crate) struct SteamControllerSandbox {
    /// The permission query ran; false is "unknown", never "granted".
    checked: bool,
    installed: bool,
    /// Every device class `[Context] devices=` grants, lowercased.
    devices: Vec<String>,
    /// `all` or `input` covers /dev/input inside the sandbox.
    input_granted: bool,
    /// This machine exposes at least one /dev/input/event* node.
    host_input_nodes: bool,
    /// This session can open such a node for reading.
    host_readable: bool,
    /// True only when a sandboxed Steam would be able to reach a pad.
    reachable: bool,
    detail: String,
}

impl SteamControllerSandbox {
    fn reading(&self) -> Reading {
        let state = if !self.checked || !self.installed {
            "info"
        } else if self.reachable {
            "ok"
        } else {
            "warn"
        };
        Reading {
            name: "Flatpak Steam input".into(),
            state: state.into(),
            detail: safe_text(&self.detail),
        }
    }
}

/// The `[Context] devices=` list from `flatpak info --show-permissions`. Only
/// that section counts: the same key spelled elsewhere is not the sandbox.
fn granted_devices(raw: &str) -> Option<Vec<String>> {
    let mut in_context = false;
    for line in raw.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            in_context = line == "[Context]";
            continue;
        }
        if !in_context {
            continue;
        }
        let Some(value) = line.strip_prefix("devices=") else {
            continue;
        };
        return Some(
            value
                .split(';')
                .map(str::trim)
                .filter(|device| !device.is_empty() && device.len() <= 32)
                .map(str::to_ascii_lowercase)
                .collect(),
        );
    }
    None
}

/// Ok(None) when Flatpak Steam is not installed: there is no sandbox to ask
/// about, which is different from a permission read that failed.
fn flatpak_steam_devices() -> Result<Option<Vec<String>>, String> {
    let mut command = Command::new("/usr/bin/flatpak");
    command
        .args(["info", "--show-permissions", "com.valvesoftware.Steam"])
        .env("LC_ALL", "C")
        .env("PATH", "/usr/bin:/bin");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|error| format!("unavailable: launcher check could not complete: {error}"))?;
    if output.stdout.len() > 64_000 {
        return Err("error: permission output exceeded size limit".into());
    }
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if stderr.to_lowercase().contains("not installed") {
            return Ok(None);
        }
        return Err(probe_failure(&stderr));
    }
    Ok(Some(
        granted_devices(&String::from_utf8_lossy(&output.stdout)).unwrap_or_default(),
    ))
}

/// `(a node exists, this session can open one)`. Opening an event node is
/// read-only and consumes nothing; no event is read and no ioctl is issued.
fn host_input_nodes() -> (bool, bool) {
    let Ok(entries) = fs::read_dir("/dev/input") else {
        return (false, false);
    };
    let mut found = false;
    for entry in entries.flatten().take(4096) {
        let name = entry.file_name();
        if !name.to_string_lossy().starts_with("event") {
            continue;
        }
        found = true;
        if fs::File::open(entry.path()).is_ok() {
            return (true, true);
        }
    }
    (found, false)
}

fn classify_steam_sandbox(
    checked: bool,
    installed: bool,
    devices: Vec<String>,
    host_input_nodes: bool,
    host_readable: bool,
) -> SteamControllerSandbox {
    let input_granted = devices.iter().any(|device| device == "all" || device == "input");
    let granted = if devices.is_empty() {
        "no device classes".to_string()
    } else {
        devices.join(", ")
    };
    let (reachable, detail) = if !checked {
        (
            false,
            "Flatpak Steam’s permissions could not be read, so whether its sandbox can reach a controller is unknown.".to_string(),
        )
    } else if !installed {
        (
            false,
            "Flatpak Steam is not installed, so there is no launcher sandbox to check.".to_string(),
        )
    } else if !input_granted {
        (
            false,
            format!(
                "Flatpak Steam is granted only {granted}; /dev/input is hidden inside its sandbox, so a controller cannot reach it there."
            ),
        )
    } else if !host_input_nodes {
        (
            false,
            format!(
                "Flatpak Steam is granted {granted}, but this machine exposes no /dev/input/event* node."
            ),
        )
    } else if !host_readable {
        (
            false,
            format!(
                "Flatpak Steam is granted {granted}, but this session cannot open /dev/input/event* (permissions)."
            ),
        )
    } else {
        (
            true,
            format!("Flatpak Steam is granted {granted}; /dev/input is visible inside its sandbox."),
        )
    };
    SteamControllerSandbox {
        checked,
        installed,
        devices,
        input_granted,
        host_input_nodes,
        host_readable,
        reachable,
        detail,
    }
}

fn steam_controller_sandbox() -> SteamControllerSandbox {
    let (nodes, readable) = host_input_nodes();
    match flatpak_steam_devices() {
        Ok(Some(devices)) => classify_steam_sandbox(true, true, devices, nodes, readable),
        Ok(None) => classify_steam_sandbox(true, false, Vec::new(), nodes, readable),
        Err(_) => classify_steam_sandbox(false, false, Vec::new(), nodes, readable),
    }
}

/// The family a controller-like input device name belongs to. The name is read
/// only to choose advice: no device name, path or id leaves this function, so a
/// Bluetooth pad with no USB row still gets the curated line for its family.
fn controller_family(name: &str) -> Option<&'static str> {
    let name = name.to_ascii_lowercase();
    if name.contains("8bitdo") {
        return Some("8bitdo");
    }
    if name.contains("dualsense") || name.contains("dualshock") || name.contains("playstation") {
        return Some("playstation");
    }
    if name.contains("xbox") || name.contains("x-box") {
        return Some("xbox");
    }
    if name.contains("joy-con")
        || name.contains("joycon")
        || name.contains("switch")
        || name.contains("pro controller")
    {
        return Some("nintendo");
    }
    if name.contains("steam") {
        return Some("steam");
    }
    None
}

/// Curated hints for the families this scan saw, in the table's order. Families
/// come from the helper's USB rows and from controller-like input device names,
/// so a wired and a Bluetooth pad of the same family share one line.
fn controller_hints(data: &serde_json::Value) -> Vec<ControllerHint> {
    let mut detected: Vec<&'static str> = Vec::new();
    if let Some(usb) = data["usb"].as_array() {
        for entry in usb.iter().take(64) {
            let Some(family) = entry["family"].as_str() else {
                continue;
            };
            if let Some((key, _, _)) = CONTROLLER_FAMILY_HINTS
                .iter()
                .find(|(key, _, _)| *key == family)
            {
                detected.push(key);
            }
        }
    }
    if let Some(devices) = data["devices"].as_array() {
        for entry in devices.iter().take(128) {
            if entry["gamepad_like"].as_bool() != Some(true) {
                continue;
            }
            let Some(name) = entry["name"].as_str() else {
                continue;
            };
            if let Some(family) = controller_family(name) {
                detected.push(family);
            }
        }
    }
    CONTROLLER_FAMILY_HINTS
        .iter()
        .filter(|(key, _, _)| detected.contains(key))
        .map(|(key, label, hint)| ControllerHint {
            family: (*key).to_string(),
            label: (*label).to_string(),
            hint: (*hint).to_string(),
        })
        .collect()
}

fn parse_controller(raw: &str) -> Result<ControllerReport, String> {
    let data: serde_json::Value =
        serde_json::from_str(raw).map_err(|_| "error: invalid controller JSON")?;
    let findings = data["findings"]
        .as_array()
        .filter(|f| !f.is_empty() && f.len() <= 64)
        .ok_or("error: invalid controller findings")?;
    let findings = findings
        .iter()
        .map(|finding| {
            let name = finding["name"]
                .as_str()
                .ok_or("error: missing controller name")?;
            let state = finding["status"]
                .as_str()
                .filter(|s| ["ok", "warn", "fail", "info"].contains(s))
                .ok_or("error: invalid controller status")?;
            let detail = finding["detail"]
                .as_str()
                .ok_or("error: missing controller detail")?;
            // Do not project helper hints, raw devices, identifiers or suggested mutations.
            Ok(Reading {
                name: safe_text(name),
                state: state.into(),
                detail: safe_text(detail),
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(ControllerReport {
        findings,
        hints: controller_hints(&data),
        steam_sandbox: None,
    })
}
/// Format an epoch second as "YYYY-MM-DD HH:MM UTC" (civil-from-days,
/// no date crate). Only used for display; tests pin known vectors.
fn fmt_epoch_utc(epoch: u64) -> String {
    let days = (epoch / 86_400) as i64;
    let secs = epoch % 86_400;
    let (hour, minute) = (secs / 3600, (secs % 3600) / 60);
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02} UTC")
}

fn fmt_seconds(value: f64) -> String {
    if value >= 60.0 {
        let minutes = (value / 60.0).floor() as u64;
        format!("{minutes}m {:.1}s", value - minutes as f64 * 60.0)
    } else {
        format!("{value:.1}s")
    }
}

fn perf_reading(name: &str, state: &str, detail: String) -> Reading {
    Reading {
        name: name.into(),
        state: state.into(),
        detail: safe_text(&detail),
    }
}

fn valid_history(doc: &serde_json::Value) -> Option<&Vec<serde_json::Value>> {
    if doc.get("schema_version").and_then(serde_json::Value::as_i64) != Some(1) {
        return None;
    }
    let boots = doc.get("boots")?.as_array()?;
    let entry_ok = |entry: &serde_json::Value| {
        entry.get("boot_id").and_then(serde_json::Value::as_str).is_some_and(|id| !id.is_empty())
            && entry.get("sampled_at").and_then(serde_json::Value::as_u64).is_some_and(|ts| ts > 0)
            && (entry.get("boot").is_none_or(|boot| boot.is_null() || boot.is_object()))
            && (entry.get("suspend").is_none_or(|suspend| {
                suspend.is_null()
                    || (suspend.get("success").and_then(serde_json::Value::as_u64).is_some()
                        && suspend.get("fail").and_then(serde_json::Value::as_u64).is_some())
            }))
    };
    boots.iter().all(entry_ok).then_some(boots)
}

/// Boot timing from the baseline history (rowyn-performance-record).
/// The newest entry is the current boot; unmeasured segments stay absent.
fn boot_time_reading(raw: Result<String, String>) -> Reading {
    let name = "Boot time";
    let Ok(raw) = raw else {
        return perf_reading(name, "unavailable", "No performance baseline recorded yet.".into());
    };
    let Ok(doc) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return perf_reading(name, "unavailable", "The performance history is not readable.".into());
    };
    let Some(boots) = valid_history(&doc) else {
        return perf_reading(name, "unavailable", "The performance history is not readable.".into());
    };
    let Some(latest) = boots.iter().max_by_key(|entry| {
        entry.get("sampled_at").and_then(serde_json::Value::as_u64).unwrap_or(0)
    }) else {
        return perf_reading(name, "unavailable", "No performance baseline recorded yet.".into());
    };
    let Some(boot) = latest.get("boot").filter(|boot| boot.is_object()) else {
        return perf_reading(name, "unavailable", "Boot timing was not reported for the latest boot.".into());
    };
    let Some(total) = boot.get("total").and_then(serde_json::Value::as_f64) else {
        return perf_reading(name, "unavailable", "Boot timing was not reported for the latest boot.".into());
    };
    let headline = match (
        boot.get("target").and_then(serde_json::Value::as_str),
        boot.get("target_seconds").and_then(serde_json::Value::as_f64),
    ) {
        (Some("graphical"), Some(at)) => {
            format!("{} total · desktop reached in {}", fmt_seconds(total), fmt_seconds(at))
        }
        (Some(_), Some(at)) => {
            format!("{} total · session target reached in {}", fmt_seconds(total), fmt_seconds(at))
        }
        _ => format!("{} total", fmt_seconds(total)),
    };
    let segments: Vec<String> = ["firmware", "loader", "kernel", "initrd", "userspace"]
        .iter()
        .filter_map(|key| {
            boot.get(*key)
                .and_then(serde_json::Value::as_f64)
                .map(|value| format!("{} {}", key, fmt_seconds(value)))
        })
        .collect();
    let sampled_at = latest
        .get("sampled_at")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0);
    let mut lines = vec![headline];
    if !segments.is_empty() {
        lines.push(segments.join(" · "));
    }
    lines.push(format!(
        "Recorded {} · {} boot(s) in the baseline history.",
        fmt_epoch_utc(sampled_at),
        boots.len()
    ));
    perf_reading(name, "ok", lines.join("\n"))
}

/// Suspend reliability aggregated over every recorded boot that reported
/// kernel suspend statistics; boots without them are skipped, not zeroed.
fn suspend_reading(raw: Result<String, String>) -> Reading {
    let name = "Suspend / resume";
    let Ok(raw) = raw else {
        return perf_reading(name, "unavailable", "Suspend has not been reported on this system.".into());
    };
    let Ok(doc) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return perf_reading(name, "unavailable", "Suspend has not been reported on this system.".into());
    };
    let Some(boots) = valid_history(&doc) else {
        return perf_reading(name, "unavailable", "Suspend has not been reported on this system.".into());
    };
    let reported: Vec<&serde_json::Value> = boots
        .iter()
        .filter(|entry| entry.get("suspend").is_some_and(|suspend| suspend.is_object()))
        .collect();
    if reported.is_empty() {
        return perf_reading(name, "unavailable", "Suspend has not been reported on this system.".into());
    }
    let (mut success, mut fail) = (0u64, 0u64);
    for entry in &reported {
        success += entry["suspend"].get("success").and_then(serde_json::Value::as_u64).unwrap_or(0);
        fail += entry["suspend"].get("fail").and_then(serde_json::Value::as_u64).unwrap_or(0);
    }
    let attempts = success + fail;
    if attempts == 0 {
        return perf_reading(
            name,
            "ok",
            format!("No suspend attempted in {} recorded boot(s).", reported.len()),
        );
    }
    let percent = (success as f64 * 100.0 / attempts as f64).round() as u64;
    let mut lines = vec![format!(
        "{success} of {attempts} suspends succeeded ({percent}%) across {} recorded boot(s).",
        reported.len()
    )];
    if let Some(latest) = reported.iter().max_by_key(|entry| {
        entry.get("sampled_at").and_then(serde_json::Value::as_u64).unwrap_or(0)
    }) {
        let latest_success = latest["suspend"].get("success").and_then(serde_json::Value::as_u64).unwrap_or(0);
        let latest_fail = latest["suspend"].get("fail").and_then(serde_json::Value::as_u64).unwrap_or(0);
        lines.push(format!(
            "Latest boot: {latest_success} of {} succeeded.",
            latest_success + latest_fail
        ));
    }
    perf_reading(name, if fail > 0 { "attention" } else { "ok" }, lines.join("\n"))
}

/// Update-cycle timing from the watcher's own status record — the same
/// file the Home update card projects. Check duration and staged time
/// are recorded facts; an apply duration is not, so none is shown.
fn update_cycle_reading(raw: Result<String, String>, now: u64) -> Reading {
    let name = "Update cycle";
    let Ok(raw) = raw else {
        return perf_reading(name, "unavailable", "No update check recorded yet.".into());
    };
    let Ok(doc) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return perf_reading(name, "unavailable", "The update check record is not readable.".into());
    };
    let ts = |key: &str| {
        doc.get(key)
            .and_then(serde_json::Value::as_i64)
            .filter(|value| *value > 0 && (*value as u64) <= now + 300)
            .map(|value| value as u64)
    };
    let outcome = doc.get("outcome").and_then(serde_json::Value::as_str).unwrap_or("");
    let reason = doc.get("reason_code").and_then(serde_json::Value::as_str).unwrap_or("");
    let (started, finished) = (ts("started_at"), ts("finished_at"));
    let (headline, state) = match outcome {
        "running" => (
            match started {
                Some(at) => format!("Update check in progress since {}.", fmt_epoch_utc(at)),
                None => "Update check in progress.".into(),
            },
            "ok",
        ),
        "current" | "staged" | "failed" | "interrupted" | "deferred" => {
            let what = match outcome {
                "current" => "system up to date",
                "staged" => "update staged; it applies on the next boot",
                "failed" => "check failed",
                "interrupted" => "check interrupted",
                _ => "check deferred",
            };
            let headline = match (finished, started) {
                (Some(done), Some(began)) if done >= began => format!(
                    "Last check {} · took {} · {}.",
                    fmt_epoch_utc(done),
                    fmt_seconds((done - began) as f64),
                    what
                ),
                (Some(done), _) => format!("Last check {} · {}.", fmt_epoch_utc(done), what),
                _ => format!("Last check outcome: {what}."),
            };
            (
                headline,
                if matches!(outcome, "failed" | "interrupted") { "attention" } else { "ok" },
            )
        }
        "disabled" => ("Automatic update checks are off.".into(), "ok"),
        "invalid_policy" => ("The update policy is invalid; checks cannot run.".into(), "attention"),
        _ => return perf_reading(name, "unavailable", "The update check record is not readable.".into()),
    };
    let mut lines = vec![headline];
    if !reason.is_empty() && matches!(outcome, "deferred" | "failed" | "interrupted") {
        lines.push(format!("Reason: {reason}."));
    }
    if let Some(staged_at) = ts("last_staged_at") {
        lines.push(format!("Last update staged {}.", fmt_epoch_utc(staged_at)));
    }
    perf_reading(name, state, lines.join("\n"))
}

fn controllers() -> Result<ControllerReport, String> {
    let _guard = ReadGuard::take(&CONTROLLER_RUNNING)?;
    let mut report = parse_controller(&probe(Probe::Controller)?)?;
    // A pad this session can open is not a pad Flatpak Steam can reach: the
    // launcher's sandbox needs the devices granted, so both are reported.
    let sandbox = steam_controller_sandbox();
    report.findings.push(sandbox.reading());
    report.steam_sandbox = Some(sandbox);
    Ok(report)
}
fn controller_section(result: Result<ControllerReport, String>) -> Reading {
    match result {
        Err(error) => section("Controller readiness", Err(error)),
        Ok(report) => {
            let items = report.findings;
            let state = if items
                .iter()
                .any(|item| ["warn", "fail"].contains(&item.state.as_str()))
            {
                "attention"
            } else {
                "ok"
            };
            Reading {
                name: "Controller readiness".into(),
                state: state.into(),
                detail: safe_text(
                    &items
                        .iter()
                        .map(|item| format!("{} [{}]: {}", item.name, item.state, item.detail))
                        .collect::<Vec<_>>()
                        .join("\n"),
                ),
            }
        }
    }
}
fn collect() -> Result<Snapshot, String> {
    let _guard = ReadGuard::take(&SNAPSHOT_RUNNING)?;
    let provenance = super::rowyn_support::refresh_public_provenance();
    let mut readings = vec![
        section(
            "OS",
            read_fixed("/usr/lib/os-release").map(|text| {
                text.lines()
                    .filter(|line| {
                        ["PRETTY_NAME=", "VERSION_ID=", "ID="]
                            .iter()
                            .any(|prefix| line.starts_with(prefix))
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            }),
        ),
        section("Kernel", read_fixed("/proc/sys/kernel/osrelease")),
        section("CPU model", read_fixed("/proc/cpuinfo").and_then(|raw| {
            raw.lines().find_map(|line| line.strip_prefix("model name").and_then(|value| value.split_once(':')).map(|(_, value)| value.trim().to_owned()))
                .ok_or_else(|| "unavailable: CPU model is not reported".into())
        })),
        section("NVIDIA module version", read_fixed("/sys/module/nvidia/version")),
        section("Update health record", read_fixed("/var/lib/rowyn/boot-health.json").and_then(|raw| {
            let value: serde_json::Value = serde_json::from_str(&raw).map_err(|_| "error: invalid boot-health JSON".to_string())?;
            if !value.is_object() { return Err("error: invalid boot-health record".into()); }
            let selected: serde_json::Map<String, serde_json::Value> = ["schema_version", "failures", "quarantined", "rollback", "last_boot"]
                .into_iter().filter_map(|key| value.get(key).map(|item| (key.to_owned(), item.clone()))).collect();
            serde_json::to_string_pretty(&selected).map_err(|_| "error: could not format update health".into())
        })),
        boot_time_reading(read_fixed("/var/lib/rowyn/performance/history.json")),
        suspend_reading(read_fixed("/var/lib/rowyn/performance/history.json")),
        update_cycle_reading(
            read_fixed("/var/lib/rowyn/update-watcher/status.json"),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        ),
        section(
            "Memory",
            read_fixed("/proc/meminfo").map(|text| {
                text.lines()
                    .filter(|line| {
                        ["MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"]
                            .iter()
                            .any(|prefix| line.starts_with(prefix))
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            }),
        ),
        section(
            "GPU drivers",
            read_fixed("/proc/modules").map(|text| {
                let modules: Vec<_> = text
                    .lines()
                    .filter_map(|line| line.split_whitespace().next())
                    .filter(|module| ["nvidia", "nouveau", "amdgpu", "i915", "xe"].contains(module))
                    .collect();
                if modules.is_empty() {
                    "No listed GPU module detected; built-in drivers are not listed here.".into()
                } else {
                    modules.join(", ")
                }
            }),
        ),
        section(
            "Bootc image",
            probe(Probe::Bootc).and_then(|raw| parse_bootc(&raw)),
        ),
        section(
            "GPU and bound drivers",
            probe(Probe::Gpu)
                .map(|text| {
                    let mut include = false;
                    text.lines()
                        .filter(|line| {
                            if !line.starts_with(char::is_whitespace) {
                                include = [
                                    "VGA compatible controller",
                                    "3D controller",
                                    "Display controller",
                                ]
                                .iter()
                                .any(|key| line.contains(key));
                            }
                            include
                        })
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .and_then(|text| {
                    if text.is_empty() {
                        Err("unavailable: no graphics adapter reported".into())
                    } else {
                        Ok(text)
                    }
                }),
        ),
        failed_services_section(probe(Probe::FailedServices)),
        section("Disk space", probe(Probe::Disk)),
    ];
    readings.extend(provenance.into_iter().map(|(name, state, detail)| Reading { name, state, detail }));
    readings.push(controller_section(controllers()));
    readings.push(focused_reading("Bluetooth", probe(Probe::Bluetooth).and_then(|raw| parse_rfkill(&raw))));
    readings.push(focused_reading("Wi-Fi", probe(Probe::Wifi).and_then(|raw| parse_wifi(&raw, &probe(Probe::WifiRadio)?))));
    readings.push(focused_reading("Audio", probe(Probe::Audio).and_then(|raw| parse_audio(&raw))));
    readings.push(focused_reading("Displays", probe(Probe::Displays).and_then(|raw| parse_displays(&raw))));
    Ok(Snapshot {
        collected_at: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
        readings,
    })
}
#[tauri::command]
pub(crate) async fn rowyn_diagnostics_snapshot() -> Result<Snapshot, String> {
    tauri::async_runtime::spawn_blocking(collect)
        .await
        .map_err(|_| "Diagnostics worker failed".to_string())?
}
#[tauri::command]
pub(crate) async fn rowyn_controller_scan() -> Result<ControllerReport, String> {
    tauri::async_runtime::spawn_blocking(controllers)
        .await
        .map_err(|_| "Controller worker failed".to_string())?
}
#[tauri::command]
pub(crate) async fn rowyn_export_report(
    app: tauri::AppHandle,
    report: String,
) -> Result<String, String> {
    if report.len() > REPORT_LIMIT {
        return Err("Report exceeds size limit.".into());
    }
    let directory = app
        .path()
        .download_dir()
        .map_err(|_| "Downloads directory unavailable")?;
    tauri::async_runtime::spawn_blocking(move || save_report(&directory, &report))
        .await
        .map_err(|_| "Export worker failed".to_string())?
}
fn save_report(directory: &std::path::Path, report: &str) -> Result<String, String> {
    if report.len() > REPORT_LIMIT {
        return Err("Report exceeds size limit.".into());
    }
    if !directory.is_dir() {
        return Err("Downloads directory does not exist; nothing saved.".into());
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let name = format!("rowyn-diagnostics-{timestamp}.txt");
    let path = directory.join(&name);
    let clean = super::rowyn_support::sanitize_report(report);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)
        .map_err(|_| "Could not create report; nothing overwritten.")?;
    if file
        .write_all(clean.as_bytes())
        .and_then(|_| file.sync_all())
        .is_err()
    {
        let _ = fs::remove_file(&path);
        return Err("Could not finish saving report.".into());
    }
    Ok(format!("Saved {name} in Downloads. Review before sharing."))
}

#[cfg(test)]
#[path = "rowyn_diagnostics_tests.rs"]
mod tests;
