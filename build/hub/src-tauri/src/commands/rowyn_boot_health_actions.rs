//! The one confirmed write the Hub makes to the boot-health record.
//!
//! Reading stays in `rowyn_boot_health`; this module only clears a quarantine,
//! and it exists because the terminal command
//! (`sudo rowyn-boot-health clear-quarantine <digest>`) is the wrong place to
//! send someone who is looking at the panel that shows the problem.
//!
//! The command is one literal, allowlisted argv — `sudo -n
//! /usr/libexec/rowyn-boot-health clear-quarantine -` — with the digest on
//! stdin, so the sudoers grant needs no wildcard and the digest never appears
//! in `ps`. Nothing here downloads, stages, rolls back or restarts anything;
//! clearing a quarantine only lets the digest be staged again.
use rowyn_shared::system::process::run_bounded_with_input;
use std::time::Duration;

const SUDO: &str = "/usr/bin/sudo";
const HELPER: &str = "/usr/libexec/rowyn-boot-health";
const TIMEOUT: Duration = Duration::from_secs(20);
const MAX_OUTPUT: usize = 16 * 1024;
/// `sha256:` plus 64 lowercase hex characters.
const MAX_DIGEST: usize = 71;

fn argv() -> Vec<String> {
    [SUDO, "-n", HELPER, "clear-quarantine", "-"]
        .into_iter()
        .map(str::to_string)
        .collect()
}

/// Only a digest the helper itself would accept, in the form bootc prints it.
fn digest_argument(digest: &str) -> Result<&str, String> {
    let valid = digest.len() == MAX_DIGEST
        && digest.starts_with("sha256:")
        && digest.as_bytes()[7..]
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte));
    if !valid {
        return Err("That is not a sha256 image digest, so nothing was changed.".into());
    }
    Ok(digest)
}

/// The quarantine is destructive to undo, not to leave: the page states the
/// consequence and the command refuses without that acknowledgement.
fn require_confirmation(confirmed: bool) -> Result<(), String> {
    if confirmed {
        Ok(())
    } else {
        Err("Clearing a quarantine needs an explicit confirmation.".into())
    }
}

fn quarantined(record: &str) -> Result<Vec<String>, String> {
    let value: serde_json::Value = serde_json::from_str(record).map_err(|_| {
        "The boot-health record is not valid JSON, so nothing was changed.".to_string()
    })?;
    let entries = value
        .get("quarantined")
        .and_then(|entry| entry.as_object())
        .ok_or_else(|| {
            "The boot-health record has no quarantine list, so nothing was changed.".to_string()
        })?;
    Ok(entries.keys().cloned().collect())
}

fn refusal(digest: &str) -> String {
    format!(
        "{digest} is not quarantined now, so nothing was changed. Refresh boot health to read the current record."
    )
}

fn first_line(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes)
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("")
        .chars()
        .take(200)
        .collect()
}

#[tauri::command]
pub(crate) async fn rowyn_clear_boot_quarantine(
    digest: String,
    confirmed: bool,
) -> Result<String, String> {
    let digest = digest_argument(&digest)?.to_string();
    require_confirmation(confirmed)?;
    tauri::async_runtime::spawn_blocking(move || {
        // Fail closed: if the record cannot be read, do not run a helper that
        // would answer "not quarantined" from a state the Hub never saw.
        let record = super::rowyn_boot_health::collect()?;
        if !quarantined(&record)?.iter().any(|entry| entry == &digest) {
            return Err(refusal(&digest));
        }
        let input = format!("{digest}\n");
        let output = run_bounded_with_input(&argv(), input.as_bytes(), TIMEOUT).map_err(|_| {
            "The boot-health helper did not answer, so the quarantine was left in place."
                .to_string()
        })?;
        if output.stdout.len().saturating_add(output.stderr.len()) > MAX_OUTPUT {
            return Err(
                "The boot-health helper answered with more text than expected; refresh boot health to read the record."
                    .into(),
            );
        }
        if !output.status.success() {
            let reason = first_line(&output.stderr);
            return Err(if reason.is_empty() {
                "The boot-health helper did not clear the quarantine.".to_string()
            } else {
                format!("The boot-health helper did not clear the quarantine: {reason}")
            });
        }
        Ok(format!(
            "Quarantine cleared for {digest}. That deployment may be staged again; nothing was downloaded, installed, or restarted."
        ))
    })
    .await
    .map_err(|_| "Boot-health action worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_boot_health_actions_tests.rs"]
mod tests;
