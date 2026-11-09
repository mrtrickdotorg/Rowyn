//! Windows migration ("Move In" successor): detect a Windows installation
//! on an NTFS partition, list its user profiles, and copy the categories
//! the user picks into the matching XDG directories.
//!
//! The Windows side is treated as read-only evidence: detection only
//! stats and lists, mounting goes through udisks with the `ro` option,
//! and the importer opens source files read-only and never writes,
//! deletes or renames anything under the Windows mount. Imports never
//! overwrite an existing destination file — a collision is a reported
//! skip, not a silent replacement.
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use rowyn_shared::system::process::run_bounded_command;

const MAX_FILES_PER_IMPORT: usize = 20_000;
const MAX_BYTES_PER_IMPORT: u64 = 20 * 1024 * 1024 * 1024;
const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const MAX_FAILURE_REASONS: usize = 20;

#[derive(Debug, Clone, Serialize)]
pub(crate) struct WindowsInstall {
    device: String,
    mounted: bool,
    mountpoint: Option<String>,
    profiles: Vec<String>,
}

#[derive(Debug, Serialize)]
pub(crate) struct CategoryReport {
    category: String,
    copied_files: usize,
    copied_bytes: u64,
    skipped: usize,
    failed: usize,
    reasons: Vec<String>,
}

#[derive(Debug, Serialize)]
pub(crate) struct ImportReport {
    profile: String,
    categories: Vec<CategoryReport>,
    detail: String,
}

#[derive(Debug, Clone, PartialEq)]
struct Partition {
    device: String,
    mountpoint: Option<String>,
}

/// Parse `lsblk -J -b -o NAME,FSTYPE,MOUNTPOINT` output down to the NTFS
/// partitions (including children of whole disks).
fn ntfs_partitions(lsblk_json: &str) -> Vec<Partition> {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(lsblk_json) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    fn walk(node: &serde_json::Value, out: &mut Vec<Partition>) {
        if node.get("fstype").and_then(|v| v.as_str()) == Some("ntfs") {
            if let Some(name) = node.get("name").and_then(|v| v.as_str()) {
                out.push(Partition {
                    device: format!("/dev/{name}"),
                    mountpoint: node
                        .get("mountpoint")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                });
            }
        }
        if let Some(children) = node.get("children").and_then(|v| v.as_array()) {
            for child in children {
                walk(child, out);
            }
        }
    }
    if let Some(devices) = value.get("blockdevices").and_then(|v| v.as_array()) {
        for device in devices {
            walk(device, &mut out);
        }
    }
    out
}

/// Profile directory names that are not real user profiles.
const NON_PROFILE_DIRS: &[&str] = &["Public", "Default", "Default User", "All Users"];

/// If `root` looks like a Windows installation (a Windows directory and a
/// Users tree), return its profile names.
fn windows_profiles(root: &Path) -> Option<Vec<String>> {
    if !root.join("Windows").is_dir() || !root.join("Users").is_dir() {
        return None;
    }
    let mut profiles = Vec::new();
    let entries = std::fs::read_dir(root.join("Users")).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if NON_PROFILE_DIRS.contains(&name.as_str()) {
            continue;
        }
        if entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false) {
            profiles.push(name);
        }
    }
    profiles.sort();
    Some(profiles)
}

fn lsblk_partitions() -> Result<Vec<Partition>, String> {
    let mut command = Command::new("/usr/bin/lsblk");
    command
        .args(["-J", "-b", "-o", "NAME,FSTYPE,MOUNTPOINT"])
        .env("LC_ALL", "C");
    let output = run_bounded_command(command, Duration::from_secs(10))
        .map_err(|_| "Block device scan unavailable.".to_string())?;
    if !output.status.success() || output.stdout.len() > 512_000 {
        return Err("Block device scan failed.".into());
    }
    Ok(ntfs_partitions(&String::from_utf8_lossy(&output.stdout)))
}

fn scan_installs() -> Result<Vec<WindowsInstall>, String> {
    let mut installs = Vec::new();
    for partition in lsblk_partitions()? {
        let (mounted, profiles) = match &partition.mountpoint {
            Some(mountpoint) => {
                let profiles = windows_profiles(Path::new(mountpoint));
                (true, profiles)
            }
            None => (false, None),
        };
        // Unmounted NTFS partitions are reported as candidates (the user
        // may mount them read-only); mounted ones only count when they
        // actually look like Windows installations.
        if partition.mountpoint.is_none() || profiles.is_some() {
            installs.push(WindowsInstall {
                device: partition.device,
                mounted,
                mountpoint: partition.mountpoint,
                profiles: profiles.unwrap_or_default(),
            });
        }
    }
    Ok(installs)
}

#[tauri::command]
pub(crate) async fn rowyn_migrate_scan() -> Result<Vec<WindowsInstall>, String> {
    tauri::async_runtime::spawn_blocking(scan_installs)
        .await
        .map_err(|_| "Windows scan worker failed".to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_migrate_mount(device: String) -> Result<Vec<WindowsInstall>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        // The device must be one the scan itself reported as an
        // unmounted NTFS partition — never a caller-invented path.
        let partitions = lsblk_partitions()?;
        let known = partitions
            .iter()
            .any(|partition| partition.device == device && partition.mountpoint.is_none());
        if !known {
            return Err("That device is not an unmounted NTFS partition from the scan.".into());
        }
        let mut command = Command::new("/usr/bin/udisksctl");
        command
            .args(["mount", "--no-user-interaction", "-o", "ro", "-b", &device])
            .stdin(Stdio::null());
        let output = run_bounded_command(command, Duration::from_secs(60))
            .map_err(|_| "Mount did not complete.".to_string())?;
        if !output.status.success() {
            return Err("Read-only mount failed. The drive may be hibernated (Windows fast startup) or BitLocker-locked; both must be resolved in Windows. Nothing was written to it.".into());
        }
        scan_installs()
    })
    .await
    .map_err(|_| "Mount worker failed".to_string())?
}

/// The fixed category set: (key, Windows-side relative directory, XDG
/// directory name under HOME to receive it).
fn file_categories() -> Vec<(&'static str, &'static str, &'static str)> {
    vec![
        ("documents", "Documents", "Documents"),
        ("pictures", "Pictures", "Pictures"),
        ("desktop", "Desktop", "Desktop"),
        ("downloads", "Downloads", "Downloads"),
    ]
}

fn xdg_dir(home: &Path, name: &str) -> PathBuf {
    let variable = match name {
        "Documents" => "XDG_DOCUMENTS_DIR",
        "Pictures" => "XDG_PICTURES_DIR",
        "Desktop" => "XDG_DESKTOP_DIR",
        "Downloads" => "XDG_DOWNLOAD_DIR",
        _ => "",
    };
    if !variable.is_empty() {
        if let Ok(value) = std::env::var(variable) {
            if !value.is_empty() {
                return PathBuf::from(value.replace("$HOME", &home.to_string_lossy()));
            }
        }
    }
    home.join(name)
}

#[derive(Default)]
struct CopyStats {
    copied_files: usize,
    copied_bytes: u64,
    skipped: usize,
    failed: usize,
    reasons: Vec<String>,
}

impl CopyStats {
    fn fail(&mut self, reason: String) {
        self.failed += 1;
        if self.reasons.len() < MAX_FAILURE_REASONS {
            self.reasons.push(reason);
        }
    }
    fn over_budget(&self) -> bool {
        self.copied_files >= MAX_FILES_PER_IMPORT || self.copied_bytes >= MAX_BYTES_PER_IMPORT
    }
}

/// Recursively copy `source` into `destination`, never overwriting.
fn copy_tree(source: &Path, destination: &Path, stats: &mut CopyStats) {
    let entries = match std::fs::read_dir(source) {
        Ok(entries) => entries,
        Err(error) => {
            stats.fail(format!("could not read {}: {error}", source.display()));
            return;
        }
    };
    if let Err(error) = std::fs::create_dir_all(destination) {
        stats.fail(format!("could not create {}: {error}", destination.display()));
        return;
    }
    for entry in entries {
        if stats.over_budget() {
            stats.fail("import size/file budget reached; the rest was not copied".into());
            return;
        }
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => {
                stats.failed += 1;
                continue;
            }
        };
        let kind = match entry.file_type() {
            Ok(kind) => kind,
            Err(_) => {
                stats.failed += 1;
                continue;
            }
        };
        // Symlinks are skipped, not followed: a Windows junction must not
        // pull the import outside the selected profile tree.
        if kind.is_symlink() {
            stats.skipped += 1;
            continue;
        }
        let target = destination.join(entry.file_name());
        if kind.is_dir() {
            copy_tree(&entry.path(), &target, stats);
        } else if kind.is_file() {
            copy_one(&entry.path(), &target, stats);
        }
    }
}

fn copy_one(source: &Path, destination: &Path, stats: &mut CopyStats) {
    if destination.exists() {
        stats.skipped += 1;
        return;
    }
    let metadata = match std::fs::metadata(source) {
        Ok(metadata) => metadata,
        Err(error) => {
            stats.fail(format!("could not stat {}: {error}", source.display()));
            return;
        }
    };
    if metadata.len() > MAX_FILE_BYTES {
        stats.skipped += 1;
        if stats.reasons.len() < MAX_FAILURE_REASONS {
            stats.reasons.push(format!("skipped oversized file {}", source.display()));
        }
        return;
    }
    match std::fs::copy(source, destination) {
        Ok(bytes) => {
            stats.copied_files += 1;
            stats.copied_bytes += bytes;
        }
        Err(error) => stats.fail(format!("could not copy {}: {error}", source.display())),
    }
}

/// Convert a Chrome/Edge Bookmarks JSON document to the Netscape
/// bookmark HTML every browser can import. Unparseable input is an
/// error — the caller reports it instead of writing a broken file.
fn chrome_bookmarks_to_html(json_text: &str) -> Result<String, String> {
    let value: serde_json::Value =
        serde_json::from_str(json_text).map_err(|error| format!("bookmark file unreadable: {error}"))?;
    let roots = value
        .get("roots")
        .and_then(|roots| roots.as_object())
        .ok_or("bookmark file has no roots")?;
    let mut out = String::from(
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV=\"Content-Type\" CONTENT=\"text/html; charset=UTF-8\">\n<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n",
    );
    fn escape(text: &str) -> String {
        text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
    }
    fn walk(node: &serde_json::Value, out: &mut String, depth: usize) {
        let indent = "    ".repeat(depth);
        match node.get("type").and_then(|kind| kind.as_str()) {
            Some("url") => {
                if let (Some(name), Some(url)) = (
                    node.get("name").and_then(|v| v.as_str()),
                    node.get("url").and_then(|v| v.as_str()),
                ) {
                    out.push_str(&format!(
                        "{indent}<DT><A HREF=\"{}\">{}</A>\n",
                        escape(url),
                        escape(name)
                    ));
                }
            }
            Some("folder") => {
                let name = node.get("name").and_then(|v| v.as_str()).unwrap_or("Folder");
                out.push_str(&format!("{indent}<DT><H3>{}</H3>\n{indent}<DL><p>\n", escape(name)));
                if let Some(children) = node.get("children").and_then(|v| v.as_array()) {
                    for child in children {
                        walk(child, out, depth + 1);
                    }
                }
                out.push_str(&format!("{indent}</DL><p>\n"));
            }
            _ => {}
        }
    }
    for root in roots.values() {
        walk(root, &mut out, 1);
    }
    out.push_str("</DL><p>\n");
    Ok(out)
}

fn import_bookmarks(profile_root: &Path, home: &Path, stats: &mut CopyStats) {
    let destination_dir = home.join("Documents").join("Bookmarks");
    let candidates: Vec<(&str, PathBuf)> = vec![
        (
            "Chrome-bookmarks.html",
            profile_root.join("AppData/Local/Google/Chrome/User Data/Default/Bookmarks"),
        ),
        (
            "Edge-bookmarks.html",
            profile_root.join("AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks"),
        ),
    ];
    for (name, source) in candidates {
        if !source.is_file() {
            continue;
        }
        match std::fs::read_to_string(&source) {
            Ok(text) => match chrome_bookmarks_to_html(&text) {
                Ok(html) => {
                    if let Err(error) = std::fs::create_dir_all(&destination_dir) {
                        stats.fail(format!("could not create {}: {error}", destination_dir.display()));
                        continue;
                    }
                    let target = destination_dir.join(name);
                    if target.exists() {
                        stats.skipped += 1;
                        continue;
                    }
                    match std::fs::write(&target, html.as_bytes()) {
                        Ok(()) => {
                            stats.copied_files += 1;
                            stats.copied_bytes += html.len() as u64;
                        }
                        Err(error) => stats.fail(format!("could not write {name}: {error}")),
                    }
                }
                Err(error) => stats.fail(format!("{name}: {error}")),
            },
            Err(error) => stats.fail(format!("could not read bookmarks: {error}")),
        }
    }
    // Firefox keeps bookmarks in a profile's bookmarks.html (directly
    // importable) or places.sqlite (copied as-is for Firefox's own
    // restore/import tooling).
    let profiles_dir = profile_root.join("AppData/Roaming/Mozilla/Firefox/Profiles");
    if let Ok(entries) = std::fs::read_dir(&profiles_dir) {
        for entry in entries.flatten() {
            for file in ["bookmarks.html", "places.sqlite"] {
                let source = entry.path().join(file);
                if source.is_file() {
                    if let Err(error) = std::fs::create_dir_all(&destination_dir) {
                        stats.fail(format!("could not create {}: {error}", destination_dir.display()));
                        continue;
                    }
                    copy_one(&source, &destination_dir.join(format!("Firefox-{file}")), stats);
                }
            }
        }
    }
}

fn import_wallpaper(profile_root: &Path, home: &Path, stats: &mut CopyStats) {
    let themes = profile_root.join("AppData/Roaming/Microsoft/Windows/Themes");
    let source = themes.join("TranscodedWallpaper");
    if !source.is_file() {
        stats.skipped += 1;
        if stats.reasons.len() < MAX_FAILURE_REASONS {
            stats.reasons.push("no current wallpaper file found on the Windows side".into());
        }
        return;
    }
    let pictures = xdg_dir(home, "Pictures");
    if let Err(error) = std::fs::create_dir_all(&pictures) {
        stats.fail(format!("could not create {}: {error}", pictures.display()));
        return;
    }
    let target = pictures.join("windows-wallpaper.jpg");
    let before = stats.copied_files;
    copy_one(&source, &target, stats);
    if stats.copied_files > before {
        // Best-effort apply; the copied file is the deliverable either way.
        let mut command = Command::new("/usr/bin/plasma-apply-wallpaperimage");
        command.arg(&target).stdin(Stdio::null());
        let _ = run_bounded_command(command, Duration::from_secs(15));
    }
}

fn run_import(
    mountpoint: &Path,
    profile: &str,
    categories: &[String],
    home: &Path,
) -> Result<ImportReport, String> {
    let profiles = windows_profiles(mountpoint)
        .ok_or("That mount no longer looks like a Windows installation.")?;
    if !profiles.iter().any(|found| found == profile) {
        return Err("That profile is not in the detected Windows installation.".into());
    }
    // The profile name comes back from the scan (a real directory name);
    // refuse anything that could escape the Users tree regardless.
    if profile.contains('/') || profile.contains('\\') || profile == ".." || profile == "." {
        return Err("Invalid profile name.".into());
    }
    let profile_root = mountpoint.join("Users").join(profile);
    let allowed: Vec<&str> = vec!["documents", "pictures", "desktop", "downloads", "bookmarks", "wallpaper"];
    let mut reports = Vec::new();
    let mut totals = (0usize, 0usize, 0usize);
    for category in categories {
        if !allowed.contains(&category.as_str()) {
            return Err(format!("Unsupported category: {category}"));
        }
        let mut stats = CopyStats::default();
        match category.as_str() {
            "bookmarks" => import_bookmarks(&profile_root, home, &mut stats),
            "wallpaper" => import_wallpaper(&profile_root, home, &mut stats),
            key => {
                let (_, windows_dir, xdg_name) = file_categories()
                    .into_iter()
                    .find(|(known, _, _)| *known == key)
                    .ok_or("category vanished")?;
                let source = profile_root.join(windows_dir);
                if source.is_dir() {
                    let destination = xdg_dir(home, xdg_name).join("From Windows");
                    copy_tree(&source, &destination, &mut stats);
                } else {
                    stats.skipped += 1;
                    if stats.reasons.len() < MAX_FAILURE_REASONS {
                        stats.reasons.push(format!("no {windows_dir} folder for this profile"));
                    }
                }
            }
        }
        totals.0 += stats.copied_files;
        totals.1 += stats.skipped;
        totals.2 += stats.failed;
        reports.push(CategoryReport {
            category: category.clone(),
            copied_files: stats.copied_files,
            copied_bytes: stats.copied_bytes,
            skipped: stats.skipped,
            failed: stats.failed,
            reasons: stats.reasons,
        });
    }
    Ok(ImportReport {
        profile: profile.to_string(),
        categories: reports,
        detail: format!(
            "Imported {} file(s) for {profile}; {} skipped, {} failed. The Windows drive was only read, never written.",
            totals.0, totals.1, totals.2
        ),
    })
}

#[tauri::command]
pub(crate) async fn rowyn_migrate_import(
    mountpoint: String,
    profile: String,
    categories: Vec<String>,
) -> Result<ImportReport, String> {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the current user's home directory.".to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        // The mountpoint must be a currently detected Windows install.
        let installs = scan_installs()?;
        let known = installs
            .iter()
            .any(|install| install.mountpoint.as_deref() == Some(mountpoint.as_str()) && install.mounted);
        if !known {
            return Err("That Windows installation is not currently mounted.".into());
        }
        run_import(Path::new(&mountpoint), &profile, &categories, &home)
    })
    .await
    .map_err(|_| "Import worker failed".to_string())?
}

#[cfg(test)]
#[path = "rowyn_migrate_tests.rs"]
mod tests;
