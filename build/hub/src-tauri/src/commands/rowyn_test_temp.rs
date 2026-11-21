//! Fixture directories for tests whose files are judged by a path policy.
//!
//! `read_update_file` and the update policy require every ancestor of a
//! fixture to be root- or user-owned and not group/world-writable, with a
//! root-owned sticky directory (the usual /tmp) as the one exception. The
//! machine's temp directory is not guaranteed to qualify: a sandbox can own
//! /tmp as another uid, and a umask 002 checkout can sit under a
//! group-writable parent. A fixture therefore picks a compliant base, or the
//! test skips with a message, rather than failing for an unrelated host
//! reason.
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::PathBuf;

/// Base directory for fixtures that must satisfy the update policy's path
/// checks: every ancestor root- or user-owned and not group/world-writable,
/// with a root-owned sticky directory as the exception.
pub(crate) fn compliant_temp_base() -> Option<PathBuf> {
    let euid = unsafe { libc::geteuid() } as u32;
    let candidates = [
        std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from),
        Some(PathBuf::from(format!("/run/user/{euid}"))),
        Some(std::env::temp_dir()),
        Some(PathBuf::from("/dev/shm")),
        Some(PathBuf::from("/var/tmp")),
    ];
    candidates.into_iter().flatten().find(|base| {
        base.ancestors()
            .all(|ancestor| match std::fs::metadata(ancestor) {
                Ok(meta) => {
                    let sticky_root = meta.uid() == 0 && meta.mode() & 0o1000 != 0;
                    meta.is_dir()
                        && (meta.uid() == 0 || meta.uid() == euid)
                        && (meta.mode() & 0o022 == 0 || sticky_root)
                }
                Err(_) => false,
            })
    })
}

/// A fresh, empty fixture directory under [`compliant_temp_base`], created
/// user-only so a permissive umask cannot make the fixture itself unsafe.
/// `None` means the host offers no compliant base: report that and return
/// instead of failing.
pub(crate) fn compliant_fixture(label: &str) -> Option<PathBuf> {
    let dir = compliant_temp_base()?.join(format!("rowyn-{label}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir(&dir).ok()?;
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).ok()?;
    Some(dir)
}
