//! Explicit bounded local readiness. No repair, network scan, journal or privileged query.
use super::rowyn_backups::BackupSnapshot;
use rowyn_shared::system::process::run_bounded_command;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Read,
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Component, Path},
    process::{Command, Stdio},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

const GETENT: &str = "/usr/bin/getent";
const SYSTEMCTL: &str = "/usr/bin/systemctl";
const STATUS_PATH: &str = "/var/lib/rowyn/update-watcher/status.json";
const STATUS_LIMIT: usize = 8192;
const SERVICE_ARGS: &[&str] = &[
    "show",
    "--no-pager",
    "--no-ask-password",
    "--property=Id,LoadState,ActiveState,Result",
    "cups.service",
    "cups-browsed.service",
    "systemd-journald.service",
    "rsyslog.service",
    "syslog.socket",
];
const UNITS: [&str; 5] = [
    "cups.service",
    "cups-browsed.service",
    "systemd-journald.service",
    "rsyslog.service",
    "syslog.socket",
];

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ReadinessSnapshot {
    checked_at: u64,
    checks: Vec<ReadinessCheck>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ReadinessCheck {
    id: &'static str,
    title: &'static str,
    state: &'static str,
    detail: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    update_attempt: Option<UpdateAttempt>,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateAttempt {
    started_at: u64,
    finished_at: Option<u64>,
    outcome: String,
    reason_code: String,
    last_success_at: Option<u64>,
    last_staged_at: Option<u64>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredAttempt {
    schema_version: u8,
    started_at: u64,
    finished_at: Option<u64>,
    outcome: String,
    reason_code: String,
    last_success_at: Option<u64>,
    last_staged_at: Option<u64>,
}
enum UpdateRead {
    Missing,
    Unsafe,
    Known(UpdateAttempt),
}
struct ProbeOutput {
    code: Option<i32>,
    stdout: Vec<u8>,
}
struct UnitReading {
    load: String,
    active: String,
    result: String,
}
fn check(
    id: &'static str,
    title: &'static str,
    state: &'static str,
    detail: &'static str,
) -> ReadinessCheck {
    ReadinessCheck {
        id,
        title,
        state,
        detail,
        update_attempt: None,
    }
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn update_from_bytes(raw: &[u8], clock: u64) -> UpdateRead {
    if raw.len() > STATUS_LIMIT {
        return UpdateRead::Unsafe;
    }
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(raw) else {
        return UpdateRead::Unsafe;
    };
    let fields = [
        "schema_version",
        "started_at",
        "finished_at",
        "outcome",
        "reason_code",
        "last_success_at",
        "last_staged_at",
    ];
    if !value
        .as_object()
        .is_some_and(|o| o.len() == fields.len() && fields.iter().all(|key| o.contains_key(*key)))
    {
        return UpdateRead::Unsafe;
    }
    let Ok(a) = serde_json::from_slice::<StoredAttempt>(raw) else {
        return UpdateRead::Unsafe;
    };
    let pair = match a.outcome.as_str() {
        "running" => a.reason_code == "attempt_started",
        "current" => a.reason_code == "no_update",
        "staged" => matches!(a.reason_code.as_str(), "update_staged" | "metered_cap"),
        "deferred" => matches!(
            a.reason_code.as_str(),
            "battery" | "metered" | "upgrade_locked"
        ),
        "failed" => matches!(
            a.reason_code.as_str(),
            "runtime_auth_io" | "upgrade_failed" | "unknown_failure"
        ),
        "disabled" => a.reason_code == "policy_disabled",
        "invalid_policy" => a.reason_code == "invalid_policy",
        "interrupted" => a.reason_code == "cancelled",
        _ => false,
    };
    if a.schema_version != 1
        || !pair
        || a.started_at > clock
        || a.started_at > 253402300799
        || (a.outcome == "running") != a.finished_at.is_none()
        || a.finished_at
            .is_some_and(|t| t < a.started_at || t > clock || t > 253402300799)
        || a.last_success_at
            .is_some_and(|t| t > clock || t > a.finished_at.unwrap_or(a.started_at))
        || a.last_staged_at
            .is_some_and(|t| a.last_success_at.is_none_or(|success| t > success))
        || (matches!(a.outcome.as_str(), "current" | "staged")
            && a.last_success_at != a.finished_at)
        || (a.outcome == "staged" && a.last_staged_at != a.finished_at)
    {
        return UpdateRead::Unsafe;
    }
    if (a.outcome == "running" && clock.saturating_sub(a.started_at) > 45 * 60)
        || a.finished_at
            .is_some_and(|t| clock.saturating_sub(t) > 48 * 60 * 60)
    {
        return UpdateRead::Unsafe;
    }
    UpdateRead::Known(UpdateAttempt {
        started_at: a.started_at,
        finished_at: a.finished_at,
        outcome: a.outcome,
        reason_code: a.reason_code,
        last_success_at: a.last_success_at,
        last_staged_at: a.last_staged_at,
    })
}
fn read_update_file(path: &Path, clock: u64, owner: u32) -> UpdateRead {
    if !path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return UpdateRead::Unsafe;
    }
    for parent in path.ancestors().skip(1) {
        let meta = match fs::symlink_metadata(parent) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return UpdateRead::Missing,
            Err(_) => return UpdateRead::Unsafe,
        };
        let sticky_root = meta.uid() == 0 && meta.mode() & 0o1000 != 0;
        if !meta.is_dir()
            || !matches!(meta.uid(), 0) && meta.uid() != owner
            || meta.mode() & 0o022 != 0 && !sticky_root
        {
            return UpdateRead::Unsafe;
        }
    }
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)
    {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return UpdateRead::Missing,
        Err(_) => return UpdateRead::Unsafe,
    };
    let Ok(meta) = file.metadata() else {
        return UpdateRead::Unsafe;
    };
    if !meta.is_file()
        || meta.uid() != owner
        || meta.mode() & 0o022 != 0
        || meta.len() > STATUS_LIMIT as u64
        || meta.nlink() != 1
    {
        return UpdateRead::Unsafe;
    }
    let mut raw = Vec::new();
    if file
        .take((STATUS_LIMIT + 1) as u64)
        .read_to_end(&mut raw)
        .is_err()
    {
        return UpdateRead::Unsafe;
    }
    update_from_bytes(&raw, clock)
}
fn update_check(read: &UpdateRead) -> ReadinessCheck {
    let (state,detail)=match read {
        UpdateRead::Missing=>("needs-setup","No automatic update attempt is recorded. Open Updates to read the current deployment state."),
        UpdateRead::Unsafe=>("unavailable","Automatic update history is missing required safety/freshness information or cannot be read. No success is assumed."),
        UpdateRead::Known(a)=>match(a.outcome.as_str(),a.reason_code.as_str()) {
            ("current",_)=>("ready","The last automatic check reported no update. This is recorded history, not a new deployment check."),
            ("staged",_)=>("attention","The last automatic attempt staged an update. Open Updates to read the current deployment state; no restart is requested here."),
            ("deferred","battery")=>("attention","Automatic staging was deferred on battery power. A deferral is not a successful update check."),
            ("deferred","metered")=>("attention","Automatic staging was deferred on a metered connection. No update success was recorded."),
            ("deferred",_)=>("attention","Automatic staging was deferred because another update operation held the lock."),
            ("failed","runtime_auth_io")=>("attention","Automatic staging failed while opening runtime auth metadata. Review Diagnostics; this does not establish bad credentials."),
            ("failed",_)=>("attention","The last automatic update attempt failed. Review Diagnostics and Updates before retrying."),
            ("disabled",_)=>("needs-setup","Automatic staging is disabled by policy. Manual update controls remain in Updates."),
            ("invalid_policy",_)=>("attention","Automatic staging refused an invalid policy. No update was started."),
            ("interrupted",_)=>("attention","The last automatic update attempt was interrupted. Its completion is not assumed."),
            _=>("attention","An automatic update attempt is running. Completion and staging are not yet confirmed."),
        },
    };
    let mut result = check("updates", "Updates", state, detail);
    if let UpdateRead::Known(a) = read {
        result.update_attempt = Some(a.clone());
    }
    result
}
fn probe_command(exe: &str, args: &[&str]) -> Option<ProbeOutput> {
    let mut command = Command::new(exe);
    command.args(args).env("LC_ALL", "C").stdin(Stdio::null());
    let result = run_bounded_command(command, Duration::from_secs(4)).ok()?;
    if result.stdout.len() > 4096 || result.stderr.len() > 4096 {
        return None;
    }
    Some(ProbeOutput {
        code: result.status.code(),
        stdout: result.stdout,
    })
}
fn parse_units(probe: Option<ProbeOutput>) -> BTreeMap<String, UnitReading> {
    let mut result = BTreeMap::new();
    let Some(output) = probe else {
        return result;
    };
    if output.code != Some(0) || output.stdout.len() > 4096 {
        return result;
    }
    let Ok(text) = std::str::from_utf8(&output.stdout) else {
        return result;
    };
    let mut fields = BTreeMap::new();
    let finish =
        |fields: &mut BTreeMap<&str, &str>, result: &mut BTreeMap<String, UnitReading>| -> bool {
            let Some(id) = fields.get("Id") else {
                return fields.is_empty();
            };
            let (Some(load), Some(active), Some(unit_result)) = (
                fields.get("LoadState"),
                fields.get("ActiveState"),
                fields.get("Result"),
            ) else {
                return false;
            };
            if !UNITS.contains(id) || result.contains_key(*id) {
                return false;
            }
            result.insert(
                (*id).to_string(),
                UnitReading {
                    load: (*load).to_string(),
                    active: (*active).to_string(),
                    result: (*unit_result).to_string(),
                },
            );
            fields.clear();
            true
        };
    for line in text.lines().chain(std::iter::once("")) {
        if line.is_empty() {
            if !finish(&mut fields, &mut result) {
                return BTreeMap::new();
            }
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            return BTreeMap::new();
        };
        if key == "Id" && fields.contains_key("Id") {
            if !finish(&mut fields, &mut result) {
                return BTreeMap::new();
            }
        }
        if !matches!(key, "Id" | "LoadState" | "ActiveState" | "Result")
            || value.len() > 80
            || fields.insert(key, value).is_some()
        {
            return BTreeMap::new();
        }
    }
    result
}
fn active(unit: Option<&UnitReading>) -> Option<bool> {
    unit.and_then(|u| {
        if u.load != "loaded" {
            None
        } else {
            match u.active.as_str() {
                "active" if u.result.is_empty() || u.result == "success" => Some(true),
                "active" => Some(false),
                "inactive" | "failed" | "activating" | "deactivating" => Some(false),
                _ => None,
            }
        }
    })
}
fn snapshot_with<F>(
    mut probe: F,
    backup: Result<BackupSnapshot, String>,
    update: UpdateRead,
    development: Option<bool>,
    clock: u64,
) -> ReadinessSnapshot
where
    F: FnMut(&str, &[&str]) -> Option<ProbeOutput>,
{
    let identity = probe(GETENT, &["passwd", "cups-browsed"]).and_then(|output| {
        if output.code == Some(2) && output.stdout.is_empty() {
            Some(false)
        } else if output.code == Some(0) && output.stdout.len() <= 4096 {
            std::str::from_utf8(&output.stdout).ok().and_then(|s| {
                let entries = s.trim().split(':').collect::<Vec<_>>();
                (entries.len() == 7
                    && entries[0] == "cups-browsed"
                    && entries[2].parse::<u32>().is_ok()
                    && entries[3].parse::<u32>().is_ok())
                .then_some(true)
            })
        } else {
            None
        }
    });
    let units = parse_units(probe(SYSTEMCTL, SERVICE_ARGS));
    let printing=match(identity,active(units.get("cups.service")),active(units.get("cups-browsed.service"))) {
        (None,_,_)|(_,None,_)|(_,_,None)=>check("printing","Printing","unavailable","Printing service readiness could not be read. Open Print settings for the current desktop controls."),
        (Some(false),_,_)=>check("printing","Printing","attention","The printer discovery service identity is missing. This requires an image-level provisioning fix, not an automatic repair."),
        (_,Some(false),_)=>check("printing","Printing","attention","The CUPS printing service is not active. Open Print settings or Diagnostics to review it."),
        (_,_,Some(false))=>check("printing","Printing","attention","CUPS is active but printer discovery is not. Manual printer setup may still be available."),
        _=>check("printing","Printing","ready","Printing services are active. No printer connection or test page has been exercised."),
    };
    let legacy_failed = ["rsyslog.service", "syslog.socket"].iter().any(|key| {
        units.get(*key).is_some_and(|u| {
            u.active == "failed" || (!u.result.is_empty() && u.result != "success")
        })
    });
    let logging=match active(units.get("systemd-journald.service")) {
        None=>check("logging","System logging","unavailable","System journal readiness could not be read. No journal contents were accessed."),
        Some(false)=>check("logging","System logging","attention","The system journal is not active. Review Diagnostics; no service is restarted here."),
        Some(true) if legacy_failed=>check("logging","System logging","attention","The system journal is active, but a legacy syslog unit has failed. Rowyn defaults to journald-only logging."),
        _=>check("logging","System logging","ready","The system journal is active. Journal contents and retention configuration were not read or changed."),
    };
    let backups=match backup {
        Err(_)=>check("backups","Backups","unavailable","Kup configuration could not be read safely. Open Backups for its detailed status."),
        Ok(b) if b.plans.iter().any(|p|p.enabled)=>check("backups","Backups","ready","An enabled Kup plan is configured. The backup destination and a restore have not been tested."),
        _=>check("backups","Backups","needs-setup","No enabled Kup plans are configured. Open Backups to configure a plan."),
    };
    let dev=match development {
        Some(true)=>check("development","Development tools","ready","Podman and Distrobox executables are available. No container or preset has been tested."),
        Some(false)=>check("development","Development tools","needs-setup","Podman or Distrobox is unavailable. Open Development to review prerequisites; no host packages are installed here."),
        None=>check("development","Development tools","unavailable","Development prerequisites could not be read. No container has been queried or changed."),
    };
    ReadinessSnapshot {
        checked_at: clock,
        checks: vec![printing, logging, update_check(&update), backups, dev],
    }
}
fn development_prerequisites() -> Option<bool> {
    let mut present = true;
    for path in ["/usr/bin/podman", "/usr/bin/distrobox"] {
        match fs::metadata(path) {
            Ok(m) => present &= m.is_file() && m.permissions().mode() & 0o111 != 0,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => present = false,
            Err(_) => return None,
        }
    }
    Some(present)
}
fn collect() -> ReadinessSnapshot {
    let clock = now();
    let update = read_update_file(Path::new(STATUS_PATH), clock, 0);
    snapshot_with(
        probe_command,
        super::rowyn_backups::collect(),
        update,
        development_prerequisites(),
        clock,
    )
}
#[tauri::command]
pub(crate) async fn rowyn_readiness_snapshot() -> Result<ReadinessSnapshot, String> {
    tauri::async_runtime::spawn_blocking(collect)
        .await
        .map_err(|_| "Readiness collection is unavailable.".into())
}
#[cfg(test)]
#[path = "rowyn_readiness_tests.rs"]
mod tests;
