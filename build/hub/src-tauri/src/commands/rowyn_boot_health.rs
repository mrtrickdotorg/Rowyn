//! Read-only access to Rowyn's boot-health record.
//!
//! `/usr/libexec/rowyn-boot-health` writes `/var/lib/rowyn/boot-health.json` as
//! root with mode 0644. The Hub reads it as the signed-in user and does nothing
//! else: no writes, no quarantine changes, no elevation. Parsing and display
//! live in the frontend (rowynBootHealthModel.ts) so it can be unit-tested; this
//! command only guarantees the bytes are a bounded, root-owned, regular file
//! holding a JSON object.
//!
//! A debug build can read a fixture instead, named by
//! `ROWYN_BOOT_HEALTH_PATH`, so the panel and its confirmation can be exercised
//! without root and without touching the real record. That override is compiled
//! out of release builds, and the fixed path keeps its owner/mode check either
//! way.
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::{fs::OpenOptions, path::Path};

const PATH: &str = "/var/lib/rowyn/boot-health.json";
const LIMIT: usize = 256 * 1024;

/// The fixture path a debug build was pointed at, if any. Release builds never
/// look at the variable: the record can only come from [`PATH`].
fn fixture_path() -> Option<std::path::PathBuf> {
    #[cfg(debug_assertions)]
    {
        std::env::var_os("ROWYN_BOOT_HEALTH_PATH")
            .filter(|value| !value.is_empty())
            .map(std::path::PathBuf::from)
    }
    #[cfg(not(debug_assertions))]
    {
        None
    }
}

fn validate(raw: &str) -> Result<String, String> {
    if raw.len() > LIMIT {
        return Err("The boot-health record exceeds its size limit.".into());
    }
    let value: serde_json::Value = serde_json::from_str(raw)
        .map_err(|_| "The boot-health record is not valid JSON.".to_string())?;
    let object = value
        .as_object()
        .ok_or("The boot-health record is not an object.")?;
    if object.get("schema_version").and_then(|value| value.as_u64()) != Some(1) {
        return Err("The boot-health record uses an unsupported schema version.".into());
    }
    Ok(raw.to_owned())
}

fn unavailable(error: &std::io::Error, require_root: bool) -> String {
    if error.kind() != std::io::ErrorKind::NotFound {
        return "The boot-health record is unavailable.".to_string();
    }
    if require_root {
        "No boot-health record has been written on this system yet.".to_string()
    } else {
        "No file exists at the debug boot-health path override.".to_string()
    }
}

/// Read and validate one record file.
///
/// `require_root` is the shipping rule: the helper writes the record as root for
/// unprivileged readers, so the owner and mode are checked rather than the
/// reader's own uid. Only a debug fixture — a file the developer made and owns —
/// skips that check.
fn read_record(path: &Path, require_root: bool) -> Result<String, String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)
        .map_err(|error| unavailable(&error, require_root))?;
    let meta = file
        .metadata()
        .map_err(|_| "The boot-health record is unavailable.".to_string())?;
    let unsafe_owner = require_root && (meta.uid() != 0 || meta.mode() & 0o022 != 0);
    if !meta.is_file() || meta.len() as usize > LIMIT || unsafe_owner {
        return Err("The boot-health record is unavailable or unsafe.".into());
    }
    let mut text = String::new();
    file.take((LIMIT + 1) as u64)
        .read_to_string(&mut text)
        .map_err(|_| "The boot-health record is unreadable.".to_string())?;
    validate(&text)
}

/// The validated record text. `pub(crate)` so the one writer module
/// (`rowyn_boot_health_actions`) can check a digest is really quarantined
/// against the same bounded, root-owned read this module performs.
pub(crate) fn collect() -> Result<String, String> {
    match fixture_path() {
        Some(path) => read_record(&path, false),
        None => read_record(Path::new(PATH), true),
    }
}

#[tauri::command]
pub(crate) async fn rowyn_boot_health_snapshot() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(collect)
        .await
        .map_err(|_| "Boot health worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_boot_health_tests.rs"]
mod tests;
