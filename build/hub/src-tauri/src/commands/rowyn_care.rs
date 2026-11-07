//! Read-only storage and power snapshots. All probes use fixed paths/arguments.
use rowyn_shared::system::process::run_bounded_command;
use serde::Serialize;
use serde_json::Value;
use std::{collections::HashSet, fs::File, io::Read, path::Path, time::Duration};

const LIMIT: usize = 64 * 1024;
const INVENTORY_LIMIT: usize = 1000;
#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Volume {
    name: String,
    free_bytes: Option<u64>,
    total_bytes: Option<u64>,
}
#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Entry {
    reference: String,
    name: String,
    scope: String,
    installed_size: Option<String>,
}
#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Inventory {
    category: String,
    count: Option<u64>,
    estimated_bytes: Option<u64>,
    status: String,
    detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    entries: Option<Vec<Entry>>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct StorageSnapshot {
    state: String,
    volumes: Vec<Volume>,
    inventory: Vec<Inventory>,
}
#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Availability {
    state: String,
    value: Option<Value>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Battery {
    capacity: Option<f64>,
    status: Option<String>,
    health_percent: Option<f64>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PowerSnapshot {
    state: String,
    batteries: Vec<Battery>,
    charge_limit: Availability,
    profile: Availability,
    capabilities: Value,
}

fn read_bounded(path: &Path, cap: usize) -> Result<String, String> {
    let f = File::open(path).map_err(|e| e.to_string())?;
    let mut b = Vec::new();
    f.take((cap + 1) as u64)
        .read_to_end(&mut b)
        .map_err(|e| e.to_string())?;
    if b.len() > cap {
        return Err("value exceeds limit".into());
    }
    String::from_utf8(b).map_err(|_| "invalid UTF-8".into())
}
fn safe_text(s: &str) -> String {
    rowyn_shared::diagnostics_scrub::scrub_logs(&rowyn_shared::system::process::strip_ansi(s))
}
fn command(program: &str, args: &[&str], timeout: u64) -> Result<String, String> {
    let mut c = std::process::Command::new(program);
    c.args(args).env("LC_ALL", "C");
    let o = run_bounded_command(c, Duration::from_secs(timeout))
        .map_err(|e| safe_text(&e.to_string()))?;
    if !o.status.success() {
        return Err(safe_text(&String::from_utf8_lossy(&o.stderr)));
    }
    if o.stdout.len() > LIMIT {
        return Err("command output exceeds limit".into());
    }
    String::from_utf8(o.stdout).map_err(|_| "command returned invalid UTF-8".into())
}
fn parse_flatpak(raw: &str) -> Result<Vec<Entry>, String> {
    if raw.len() > LIMIT {
        return Err("Flatpak output exceeds limit".into());
    }
    let mut entries = Vec::new();
    for line in raw.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let p: Vec<_> = line.split('\t').collect();
        if p.len() != 4 || p.iter().any(|x| x.contains('\n') || x.contains('\r')) {
            return Err("unexpected Flatpak list format".into());
        }
        if p[0].is_empty() || p[1].is_empty() || p[2].is_empty() {
            return Err("incomplete Flatpak row".into());
        }
        entries.push(Entry {
            reference: p[0].to_owned(),
            name: p[1].to_owned(),
            scope: p[2].to_owned(),
            installed_size: if p[3].is_empty() {
                None
            } else {
                Some(p[3].to_owned())
            },
        });
        if entries.len() > INVENTORY_LIMIT {
            return Err("Flatpak inventory exceeds row limit".into());
        }
    }
    Ok(entries)
}
fn parse_podman(raw: &str) -> Result<(Option<u64>, Option<u64>), String> {
    if raw.len() > LIMIT {
        return Err("Podman output exceeds limit".into());
    }
    let v: Value = serde_json::from_str(raw).map_err(|_| "invalid Podman JSON")?;
    let items = v
        .get("Images")
        .and_then(Value::as_array)
        .ok_or("unsupported Podman df format")?;
    if items.len() > INVENTORY_LIMIT {
        return Err("Podman inventory exceeds row limit".into());
    }
    let count = u64::try_from(items.len()).ok();
    for x in items {
        if !x.is_object()
            || !x
                .as_object()
                .is_some_and(|o| o.contains_key("size") || o.contains_key("Size"))
        {
            return Err("unsupported Podman image format".into());
        }
    }
    Ok((count, None))
}
#[cfg(target_os = "linux")]
fn volume(path: &Path, _name: &str) -> Result<(u64, u64, u64), String> {
    use std::os::unix::ffi::OsStrExt;
    let c = std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(|_| "invalid path")?;
    let mut s = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(c.as_ptr(), s.as_mut_ptr()) } != 0 {
        return Err("filesystem statistics unavailable".into());
    }
    let s = unsafe { s.assume_init() };
    let unit = if s.f_frsize > 0 {
        s.f_frsize as u64
    } else {
        s.f_bsize as u64
    };
    if unit == 0 {
        return Err("filesystem block size unavailable".into());
    }
    let free = (s.f_bavail as u64)
        .checked_mul(unit)
        .ok_or("filesystem size overflow")?;
    let total = (s.f_blocks as u64)
        .checked_mul(unit)
        .ok_or("filesystem size overflow")?;
    if total == 0 || free > total {
        return Err("filesystem capacity unavailable".into());
    }
    // Btrfs subvolumes may have distinct st_dev values but share f_fsid.
    Ok((s.f_fsid as u64, free, total))
}
fn storage_collect() -> StorageSnapshot {
    let mut volumes = Vec::new();
    let mut devices = HashSet::new();
    let mut failed = false;
    let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
    for (p, n) in [
        (Path::new("/var"), "System data"),
        (home.as_deref().unwrap_or(Path::new("/nonexistent")), "Home"),
    ] {
        match volume(p, n) {
            Ok((d, f, t)) => {
                if devices.insert(d) {
                    volumes.push(Volume {
                        name: n.into(),
                        free_bytes: Some(f),
                        total_bytes: Some(t),
                    })
                }
            }
            Err(_) => failed = true,
        }
    }
    let flat = command(
        "/usr/bin/flatpak",
        &["list", "--app", "--columns=ref,name,installation,size"],
        8,
    )
    .and_then(|s| parse_flatpak(&s));
    let mut inventory = Vec::new();
    match flat {
        Ok(entries) => {
            let count = entries.len() as u64;
            inventory.push(Inventory {
                category: "flatpakApps".into(),
                count: Some(count),
                estimated_bytes: None,
                status: "available".into(),
                detail: "Installed sizes are reported by Flatpak; shared objects are not summed."
                    .into(),
                entries: Some(entries),
            })
        }
        Err(e) => {
            failed = true;
            inventory.push(Inventory {
                category: "flatpakApps".into(),
                count: None,
                estimated_bytes: None,
                status: "unavailable".into(),
                detail: safe_text(&e),
                entries: None,
            })
        }
    }
    inventory.push(Inventory {
        category: "downloads".into(),
        count: None,
        estimated_bytes: None,
        status: "unavailable".into(),
        detail: "Not measured; review files in Dolphin".into(),
        entries: None,
    });
    // Resolve podman the way Development does; pinning the package path made
    // a local install look like missing image storage on this page only.
    let podman = match super::rowyn_development::podman_path() {
        Some(path) => command(&path.to_string_lossy(), &["system", "df", "--format=json"], 8)
            .and_then(|s| parse_podman(&s)),
        None => Err("podman is not installed, so image storage could not be measured.".into()),
    };
    match podman {
        Ok((count, size)) => inventory.push(Inventory {
            category: "podmanImages".into(),
            count,
            estimated_bytes: size,
            status: "available".into(),
            detail: "Image count only; shared sizes are not added.".into(),
            entries: None,
        }),
        Err(e) => {
            failed = true;
            inventory.push(Inventory {
                category: "podmanImages".into(),
                count: None,
                estimated_bytes: None,
                status: "unavailable".into(),
                detail: safe_text(&e),
                entries: None,
            })
        }
    }
    let usable = !volumes.is_empty() || inventory.iter().any(|item| item.status == "available");
    let incomplete = failed || inventory.iter().any(|item| item.status == "unavailable");
    StorageSnapshot {
        state: if !usable {
            "unavailable"
        } else if incomplete {
            "partial"
        } else {
            "ok"
        }
        .into(),
        volumes,
        inventory,
    }
}
#[tauri::command]
pub(crate) async fn rowyn_storage_snapshot() -> Result<StorageSnapshot, String> {
    tauri::async_runtime::spawn_blocking(|| Ok(storage_collect()))
        .await
        .map_err(|_| String::from("storage worker failed"))?
}
fn read_optional(dir: &Path, key: &str) -> Result<Option<String>, String> {
    use std::io::ErrorKind;
    let path = dir.join(key);
    let f = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut b = Vec::new();
    f.take(65).read_to_end(&mut b).map_err(|e| e.to_string())?;
    if b.len() > 64 {
        return Err("value exceeds limit".into());
    }
    let s = String::from_utf8(b).map_err(|_| "invalid UTF-8".to_string())?;
    Ok(Some(s.trim().to_owned()))
}
fn number(s: Option<String>) -> Option<f64> {
    s?.parse::<f64>()
        .ok()
        .filter(|n| n.is_finite() && *n >= 0.0)
}
#[cfg(test)]
fn power_collect_at(root: &Path, profiles: Result<String, String>) -> PowerSnapshot {
    power_collect_with(
        root,
        profiles,
        Err("Sleep capability not measured in this fixture".into()),
    )
}
fn measurement_pair(
    dir: &Path,
    first: &str,
    second: &str,
    failed: &mut bool,
) -> Option<(f64, f64)> {
    let left = read_optional(dir, first);
    let right = read_optional(dir, second);
    let mut decode = |value: Result<Option<String>, String>| match value {
        Ok(None) => None,
        Ok(Some(text)) => {
            let parsed = number(Some(text)).filter(|n| *n > 0.0);
            if parsed.is_none() {
                *failed = true;
            }
            parsed
        }
        Err(_) => {
            *failed = true;
            None
        }
    };
    decode(left).zip(decode(right))
}
fn power_collect_with(
    root: &Path,
    profiles: Result<String, String>,
    sleep: Result<String, String>,
) -> PowerSnapshot {
    let mut batteries = Vec::new();
    let mut failed = false;
    let mut thresholds = Vec::new();
    let mut threshold_failed = false;
    let mut entries = match std::fs::read_dir(root) {
        Ok(v) => v,
        Err(_) => {
            return PowerSnapshot {
                state: "unavailable".into(),
                batteries,
                charge_limit: Availability {
                    state: "unavailable".into(),
                    value: None,
                },
                profile: availability(profiles),
                capabilities: sleep_capabilities(sleep),
            }
        }
    };
    for item in entries.by_ref().take(16) {
        let Ok(item) = item else {
            failed = true;
            continue;
        };
        let dir = item.path();
        match read_optional(&dir, "type") {
            Ok(Some(kind)) if kind == "Battery" => {}
            Ok(Some(kind)) if !kind.is_empty() => continue,
            _ => {
                failed = true;
                continue;
            }
        }
        let present = read_optional(&dir, "present");
        if present.is_err() || matches!(&present, Ok(Some(value)) if value != "0" && value != "1") {
            failed = true;
        }
        if matches!(present,Ok(Some(ref p)) if p=="0") {
            continue;
        }
        let cap = read_optional(&dir, "capacity");
        let status = read_optional(&dir, "status");
        let energy = measurement_pair(&dir, "energy_full", "energy_full_design", &mut failed);
        let charge_pair = measurement_pair(&dir, "charge_full", "charge_full_design", &mut failed);
        if [cap.as_ref().err(), status.as_ref().err()]
            .iter()
            .any(|x| x.is_some())
        {
            failed = true
        }
        let pair = energy.or(charge_pair);
        let health = pair
            .map(|(full, design)| full / design * 100.0)
            .filter(|value| value.is_finite() && *value <= 200.0);
        let capacity = cap
            .as_ref()
            .ok()
            .and_then(|value| number(value.clone()))
            .filter(|value| *value <= 100.0);
        if pair.is_some() && health.is_none() || matches!(&cap, Ok(Some(_))) && capacity.is_none() {
            failed = true;
        }
        batteries.push(Battery {
            capacity,
            status: status.ok().flatten().filter(|s| !s.is_empty()),
            health_percent: health,
        });
        match read_optional(&dir, "charge_control_end_threshold") {
            Ok(Some(v)) => {
                let parsed = v.parse::<u64>().ok().filter(|n| *n <= 100);
                if parsed.is_none() {
                    failed = true;
                    threshold_failed = true;
                }
                thresholds.push(parsed)
            }
            Ok(None) => thresholds.push(None),
            Err(_) => {
                thresholds.push(None);
                threshold_failed = true;
                failed = true
            }
        }
    }
    let charge = if batteries.len() == 1 {
        match thresholds.first().copied().flatten() {
            Some(v) => Availability {
                state: "available".into(),
                value: Some(Value::from(v)),
            },
            None => Availability {
                state: if threshold_failed {
                    "unavailable"
                } else {
                    "unsupported"
                }
                .into(),
                value: None,
            },
        }
    } else {
        Availability {
            state: if threshold_failed {
                "unavailable"
            } else if thresholds.iter().any(Option::is_none) {
                "unsupported"
            } else {
                "unavailable"
            }
            .into(),
            value: None,
        }
    };
    if entries.next().is_some() {
        failed = true;
    }
    let capabilities = sleep_capabilities(sleep);
    let state = if failed
        || availability(profiles.clone()).state != "available"
        || capabilities["state"] == "unavailable"
    {
        "partial"
    } else {
        "ok"
    };
    PowerSnapshot {
        state: state.into(),
        batteries,
        charge_limit: charge,
        profile: availability(profiles),
        capabilities,
    }
}
fn sleep_capabilities(r: Result<String, String>) -> Value {
    match r {
        Ok(s) => {
            if s.split_whitespace()
                .any(|token| !["mem", "standby", "freeze", "disk"].contains(&token))
            {
                return serde_json::json!({"state":"unavailable","value":null});
            }
            let v: Vec<String> = s
                .split_whitespace()
                .filter(|x| ["mem", "standby", "freeze", "disk"].contains(x))
                .map(str::to_owned)
                .collect();
            if v.is_empty() {
                serde_json::json!({"state":"unsupported","value":null})
            } else {
                serde_json::json!({"state":"available","value":v})
            }
        }
        Err(_) => serde_json::json!({"state":"unavailable","value":null}),
    }
}
fn availability(r: Result<String, String>) -> Availability {
    match r {
        Ok(s) => {
            let p = s.trim();
            if ["power-saver", "balanced", "performance"].contains(&p) {
                Availability {
                    state: "available".into(),
                    value: Some(Value::String(p.into())),
                }
            } else {
                Availability {
                    state: "unavailable".into(),
                    value: None,
                }
            }
        }
        Err(_) => Availability {
            state: "unavailable".into(),
            value: None,
        },
    }
}
fn power_collect() -> PowerSnapshot {
    let profiles = command("/usr/bin/powerprofilesctl", &["get"], 8);
    let sleep = read_bounded(Path::new("/sys/power/state"), 64);
    power_collect_with(Path::new("/sys/class/power_supply"), profiles, sleep)
}
#[tauri::command]
pub(crate) async fn rowyn_power_snapshot() -> Result<PowerSnapshot, String> {
    tauri::async_runtime::spawn_blocking(|| Ok(power_collect()))
        .await
        .map_err(|_| String::from("power worker failed"))?
}
#[cfg(test)]
#[path = "rowyn_care_tests.rs"]
mod tests;
