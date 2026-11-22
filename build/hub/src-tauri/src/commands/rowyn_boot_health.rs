//! Read-only access to Rowyn's boot-health record.
//!
//! `/usr/libexec/rowyn-boot-health` writes `/var/lib/rowyn/boot-health.json` as
//! root with mode 0644. The Hub reads it as the signed-in user and does nothing
//! else: no writes, no quarantine changes, no elevation. Parsing and display
//! live in the frontend (rowynBootHealthModel.ts) so it can be unit-tested; this
//! command only guarantees the bytes are a bounded, root-owned, regular file
//! holding a JSON object.
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::{fs::OpenOptions, path::Path};

const PATH: &str = "/var/lib/rowyn/boot-health.json";
const LIMIT: usize = 256 * 1024;

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

/// The validated record text. `pub(crate)` so the one writer module
/// (`rowyn_boot_health_actions`) can check a digest is really quarantined
/// against the same bounded, root-owned read this module performs.
pub(crate) fn collect() -> Result<String, String> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(Path::new(PATH))
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                "No boot-health record has been written on this system yet.".to_string()
            } else {
                "The boot-health record is unavailable.".to_string()
            }
        })?;
    let meta = file
        .metadata()
        .map_err(|_| "The boot-health record is unavailable.".to_string())?;
    // Written by root for unprivileged readers: require the owner and the mode
    // the helper promises instead of the reader's own uid.
    if !meta.is_file() || meta.len() as usize > LIMIT || meta.uid() != 0 || meta.mode() & 0o022 != 0 {
        return Err("The boot-health record is unavailable or unsafe.".into());
    }
    let mut text = String::new();
    file.take((LIMIT + 1) as u64)
        .read_to_string(&mut text)
        .map_err(|_| "The boot-health record is unreadable.".to_string())?;
    validate(&text)
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
