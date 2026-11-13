//! Dedicated, fail-closed backend for Rowyn's guarded .deb path (D3).
//!
//! The host is an immutable bootc image, so a downloaded .deb is never
//! installed on the host. Supported app .debs are installed into one
//! dedicated Distrobox, `rowyn-deb`, and exported to the host menu with
//! distrobox-export. Every install is gated on rowyn-deb-inspect — the
//! same classifier the file handler shows the user — re-run here, so a
//! staged file is never trusted on the handler's say-so. System
//! packages (kernel modules, DKMS, system services, boot integration)
//! are refused with the classifier's reasons.
use rowyn_shared::system::process::run_bounded_command;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
const NAME: &str = "rowyn-deb";
// Same digest pin as the development environment: the 26.10 tag moves,
// and a moving tag cannot decide which image a managed environment is
// verified against. build/tests pin the two modules to the same digest.
const IMAGE: &str =
    "docker.io/library/ubuntu@sha256:262df65c0ec6bf8d9dcc87454f4330a05fae4b97825f45854af3b136666584c2";
const IMAGE_TAG_ALIAS: &str = "docker.io/library/ubuntu:26.10";
// USN-8996-1 (CVE-2026-109518): no published Ubuntu image carries this
// fix yet, so the managed container installs the fixed apt itself
// before any package operation.
const APT_FLOOR: &str = "3.3.3ubuntu0.1";
fn managed_image(image: &str) -> bool {
    image == IMAGE_TAG_ALIAS || image.starts_with("docker.io/library/ubuntu@sha256:")
}
const LABEL: &str = "io.rowyn.deb";
const LABEL_VALUE: &str = "deb-v1";
const MAX_OUTPUT: usize = 512 * 1024;
const MAX_EXPORTED_BINS: usize = 8;
const PODMAN_FALLBACKS: &[&str] = &["/usr/bin/podman", "/usr/local/bin/podman", "/usr/sbin/podman"];
const DISTROBOX_FALLBACKS: &[&str] = &["/usr/bin/distrobox", "/usr/local/bin/distrobox"];
const INSPECT_FALLBACKS: &[&str] = &["/usr/bin/rowyn-deb-inspect", "/usr/local/bin/rowyn-deb-inspect"];
fn resolve_tool(name: &str, fallbacks: &[&str]) -> Option<PathBuf> {
    fallbacks
        .iter()
        .map(PathBuf::from)
        .chain(
            std::env::var_os("PATH")
                .map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
                .unwrap_or_default()
                .into_iter()
                .map(|dir| dir.join(name)),
        )
        .find(|candidate| candidate.is_file())
}
fn tool(name: &str, fallbacks: &[&str], missing: &str) -> Result<String, String> {
    resolve_tool(name, fallbacks)
        .map(|path| path.to_string_lossy().into_owned())
        .ok_or_else(|| missing.to_string())
}
fn podman() -> Result<String, String> {
    tool(
        "podman",
        PODMAN_FALLBACKS,
        "podman is not installed, so the app container cannot be read. Install podman and distrobox to use it.",
    )
}
fn distrobox() -> Result<String, String> {
    tool(
        "distrobox",
        DISTROBOX_FALLBACKS,
        "distrobox is not installed, so the app container cannot be created or entered.",
    )
}
fn inspect_tool() -> Result<String, String> {
    tool(
        "rowyn-deb-inspect",
        INSPECT_FALLBACKS,
        "rowyn-deb-inspect is not installed, so the package cannot be verified. The install is refused.",
    )
}
const MAX_JOBS: usize = 64;
fn sudo_apt(arguments: &[&str]) -> Vec<String> {
    let mut command: Vec<String> = [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/env",
        "DEBIAN_FRONTEND=noninteractive",
        "/usr/bin/apt-get",
    ]
    .iter()
    .map(|argument| (*argument).into())
    .collect();
    command.extend(arguments.iter().map(|argument| (*argument).into()));
    command
}
fn apt_floor_command() -> Vec<String> {
    vec![
        "/usr/bin/sh".into(),
        "-c".into(),
        format!(
            "v=$(/usr/bin/dpkg-query -W -f='${{Version}}' apt) && /usr/bin/dpkg --compare-versions \"$v\" ge {APT_FLOOR}"
        ),
    ]
}
// Every package operation that adds software refreshes indexes, takes
// the fixed apt from the security archive, and refuses to continue
// below the USN-8996-1 floor.
fn apt_refresh_commands() -> Vec<Vec<String>> {
    vec![
        sudo_apt(&["update"]),
        sudo_apt(&[
            "install",
            "-y",
            "--no-install-recommends",
            "--only-upgrade",
            "apt",
        ]),
        apt_floor_command(),
    ]
}
fn distrobox_command_args(command: &[String], container_id: &str) -> Vec<String> {
    let mut args = vec![
        "enter".into(),
        "--name".into(),
        container_id.into(),
        "--".into(),
    ];
    args.extend(command.iter().cloned());
    args
}
fn validate_owned_identity(
    containers: &[ContainerIdentity],
    expected_id: &str,
) -> Result<String, String> {
    match containers
        .iter()
        .filter(|c| c.name == NAME)
        .collect::<Vec<_>>()
        .as_slice()
    {
        [c] if ownership(c) && c.id == expected_id => Ok(c.id.clone()),
        _ => Err("App container ownership or identity changed.".into()),
    }
}
fn validate_owned_container() -> Result<String, String> {
    let matches: Vec<_> = inspect_all()?
        .into_iter()
        .filter(|c| c.name == NAME)
        .collect();
    match matches.as_slice() {
        [c] if ownership(c) => Ok(c.id.clone()),
        _ => Err("App container ownership or identity could not be verified.".into()),
    }
}
static LIFECYCLE: OnceLock<Mutex<()>> = OnceLock::new();
#[derive(Clone, Debug)]
struct ContainerIdentity {
    id: String,
    name: String,
    image: String,
    labels: BTreeMap<String, String>,
    status: String,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DebContainerState {
    state: String,
    name: String,
    image: String,
    status: String,
    detail: String,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DebJob {
    id: String,
    status: String,
    detail: String,
}
#[derive(Serialize, Deserialize, Clone, Debug)]
pub(crate) struct DebApp {
    package: String,
    version: String,
    source: String,
    exported_apps: Vec<String>,
    exported_bins: Vec<String>,
    installed_at: u64,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DebState {
    container: DebContainerState,
    apps: Vec<DebApp>,
    pending: Option<String>,
}
#[derive(Serialize, Deserialize, Clone, Debug)]
pub(crate) struct DebSignal {
    signal: String,
    text: String,
}
#[derive(Serialize, Deserialize, Clone, Debug)]
pub(crate) struct DebFlatpakHint {
    label: String,
    id: String,
}
#[derive(Serialize, Deserialize, Clone, Debug)]
pub(crate) struct DebInspectReport {
    package: String,
    version: String,
    architecture: String,
    maintainer: String,
    description: String,
    installed_size_kib: u64,
    file_size_bytes: u64,
    sha256: String,
    signature: String,
    verdict: String,
    reasons: Vec<DebSignal>,
    notes: Vec<DebSignal>,
    desktop_files: Vec<String>,
    binaries: Vec<String>,
    flatpak_hint: Option<DebFlatpakHint>,
}
#[derive(Deserialize)]
struct PodmanRow {
    #[serde(rename = "Id", alias = "ID", default)]
    id: String,
    #[serde(rename = "Names", default)]
    names: Vec<String>,
    #[serde(rename = "Image", default)]
    image: String,
    #[serde(rename = "State", default)]
    state: String,
    #[serde(rename = "Labels", default)]
    labels: BTreeMap<String, String>,
}
struct Entry {
    job: DebJob,
    cancel: Option<Arc<std::sync::atomic::AtomicBool>>,
}
static JOBS: OnceLock<Mutex<(HashMap<String, Entry>, VecDeque<String>)>> = OnceLock::new();
fn jobs() -> &'static Mutex<(HashMap<String, Entry>, VecDeque<String>)> {
    JOBS.get_or_init(|| Mutex::new((HashMap::new(), VecDeque::new())))
}
fn ownership(c: &ContainerIdentity) -> bool {
    c.name == NAME
        && managed_image(&c.image)
        && c.labels.get(LABEL).map(String::as_str) == Some(LABEL_VALUE)
        && c.labels.get("manager").map(String::as_str) == Some("distrobox")
}
fn parse_list(b: &[u8]) -> Result<Vec<ContainerIdentity>, String> {
    if b.len() > MAX_OUTPUT {
        return Err("Podman output exceeded limit.".into());
    }
    let rows: Vec<PodmanRow> = serde_json::from_slice(b)
        .map_err(|_| "Podman returned invalid container data.".to_string())?;
    if rows.len() > 128 {
        return Err("Podman returned too many containers.".into());
    }
    let mut parsed = Vec::with_capacity(rows.len());
    for r in rows {
        let Some(n) = r.names.first() else {
            return Err("Podman returned a container without a name.".into());
        };
        if r.id.len() != 64
            || !r
                .id
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err("Podman returned an invalid container ID.".into());
        }
        parsed.push(ContainerIdentity {
            id: r.id,
            name: n.trim_start_matches('/').into(),
            image: r.image,
            labels: r.labels,
            status: r.state,
        });
    }
    Ok(parsed)
}
fn create_args() -> Vec<&'static str> {
    vec![
        "create",
        "--name",
        NAME,
        "--image",
        IMAGE,
        "--additional-flags",
        "--label io.rowyn.deb=deb-v1",
        "--yes",
    ]
}
fn run(exe: &str, args: &[&str], timeout: Duration) -> Result<Vec<u8>, String> {
    let mut c = Command::new(exe);
    c.args(args).env("LC_ALL", "C");
    if exe.ends_with("distrobox") {
        c.env("DBX_CONTAINER_MANAGER", "podman");
    }
    let o = run_bounded_command(c, timeout)
        .map_err(|_| format!("{} unavailable or timed out.", exe))?;
    if !o.status.success() {
        return Err(format!("{} failed.", exe));
    }
    if o.stdout.len() > MAX_OUTPUT || o.stderr.len() > MAX_OUTPUT {
        return Err("Command output exceeded limit.".into());
    }
    Ok(o.stdout)
}
fn inspect_all() -> Result<Vec<ContainerIdentity>, String> {
    parse_list(&run(
        &podman()?,
        &[
            "ps",
            "-a",
            "--filter",
            "name=^rowyn-deb$",
            "--format",
            "json",
        ],
        Duration::from_secs(10),
    )?)
}
fn state_inner() -> Result<DebContainerState, String> {
    let v: Vec<_> = inspect_all()?
        .into_iter()
        .filter(|r| r.name == NAME)
        .collect();
    match v.as_slice() {
        [] => Ok(DebContainerState {
            state: "absent".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "absent".into(),
            detail: "App container not created.".into(),
        }),
        [c] if ownership(c) => Ok(DebContainerState {
            state: "managed".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: c.status.clone(),
            detail: "Rowyn-managed app container.".into(),
        }),
        _ => Ok(DebContainerState {
            state: "collision".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "unknown".into(),
            detail: "Reserved name is not owned by Rowyn; no changes made.".into(),
        }),
    }
}

// --- User state: the installed-apps registry and the staged file ----

fn state_dir() -> Result<PathBuf, String> {
    let base = match std::env::var_os("XDG_STATE_HOME") {
        Some(value) if Path::new(&value).is_absolute() => PathBuf::from(value),
        _ => {
            let home = std::env::var_os("HOME").ok_or("Home directory unavailable.")?;
            PathBuf::from(home).join(".local").join("state")
        }
    };
    Ok(base.join("rowyn"))
}
fn registry_path_in(dir: &Path) -> PathBuf {
    dir.join("deb-apps.json")
}
fn read_registry_in(dir: &Path) -> Result<Vec<DebApp>, String> {
    let path = registry_path_in(dir);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let bytes = std::fs::read(&path)
        .map_err(|_| "The .deb app registry could not be read.".to_string())?;
    if bytes.len() > MAX_OUTPUT {
        return Err("The .deb app registry is unexpectedly large.".into());
    }
    serde_json::from_slice(&bytes)
        .map_err(|_| "The .deb app registry is damaged; no changes were made.".to_string())
}
fn write_registry_in(dir: &Path, apps: &[DebApp]) -> Result<(), String> {
    std::fs::create_dir_all(dir)
        .map_err(|_| "The .deb app registry directory could not be created.".to_string())?;
    let tmp = dir.join("deb-apps.json.tmp");
    let body = serde_json::to_vec_pretty(apps)
        .map_err(|_| "The .deb app registry could not be encoded.".to_string())?;
    std::fs::write(&tmp, body)
        .map_err(|_| "The .deb app registry could not be written.".to_string())?;
    std::fs::rename(&tmp, registry_path_in(dir))
        .map_err(|_| "The .deb app registry could not be saved.".to_string())
}
fn read_registry() -> Result<Vec<DebApp>, String> {
    read_registry_in(&state_dir()?)
}
fn home_dir() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME").ok_or("Home directory unavailable.")?;
    PathBuf::from(home)
        .canonicalize()
        .map_err(|_| "Home directory unavailable.".to_string())
}
/// A .deb the Hub will touch must be a regular file inside the user's
/// home (Distrobox shares home at the same path, which is also how the
/// container reads it) — never a device, a system path, or a flag.
fn validate_deb_file(home: &Path, path: &Path) -> Result<PathBuf, String> {
    if path.to_string_lossy().chars().any(|c| c.is_control())
        || path.to_string_lossy().starts_with('-')
    {
        return Err("Invalid package path.".into());
    }
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if !name.ends_with(".deb") {
        return Err("The file is not a .deb package.".into());
    }
    let path = path
        .canonicalize()
        .map_err(|_| "Package file unavailable.".to_string())?;
    if !path.is_file() || !path.starts_with(home) {
        return Err("The package must be a file inside your home directory.".into());
    }
    Ok(path)
}
fn pending_inner() -> Option<String> {
    let dir = state_dir().ok()?;
    let raw = std::fs::read_to_string(dir.join("deb-pending")).ok()?;
    let candidate = raw.trim();
    if candidate.is_empty() || candidate.len() > 4096 {
        return None;
    }
    let home = home_dir().ok()?;
    validate_deb_file(&home, Path::new(candidate))
        .ok()
        .map(|path| path.to_string_lossy().into_owned())
}

// --- Inspection ------------------------------------------------------

fn parse_inspect_output(bytes: &[u8]) -> Result<DebInspectReport, String> {
    if bytes.len() > MAX_OUTPUT {
        return Err("Inspection output exceeded limit.".into());
    }
    let report: DebInspectReport = serde_json::from_slice(bytes)
        .map_err(|_| "The inspection tool returned invalid data.".to_string())?;
    if report.package.is_empty() || report.version.is_empty() {
        return Err("The inspection tool returned an incomplete report.".into());
    }
    Ok(report)
}
fn run_inspect(path: &Path) -> Result<DebInspectReport, String> {
    let exe = inspect_tool()?;
    let output = run(
        &exe,
        &[path.to_string_lossy().as_ref()],
        Duration::from_secs(120),
    )
    .map_err(|_| "The package could not be inspected; the install is refused.".to_string())?;
    parse_inspect_output(&output)
}

// --- Jobs ------------------------------------------------------------

const PRESET_OUTPUT_TAIL: usize = 64 * 1024;
fn job_detail(id: &str, detail: String) {
    if let Ok(mut registry) = jobs().lock() {
        if let Some(entry) = registry.0.get_mut(id) {
            entry.job.detail = detail.chars().take(1024).collect();
        }
    }
}
fn sanitized_tail(bytes: &[u8]) -> String {
    use rowyn_shared::{diagnostics_scrub::scrub_logs, system::process::strip_ansi};
    let text = scrub_logs(&strip_ansi(&String::from_utf8_lossy(bytes)));
    let clean: String = text
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .collect();
    clean
        .chars()
        .rev()
        .take(1200)
        .collect::<String>()
        .chars()
        .rev()
        .collect()
}
fn capture_tail<R: std::io::Read + Send + 'static>(mut pipe: R) -> thread::JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut tail = VecDeque::new();
        let mut buffer = [0u8; 4096];
        while let Ok(count) = pipe.read(&mut buffer) {
            if count == 0 {
                break;
            }
            for byte in &buffer[..count] {
                tail.push_back(*byte);
                if tail.len() > PRESET_OUTPUT_TAIL {
                    tail.pop_front();
                }
            }
        }
        tail.into_iter().collect()
    })
}
fn child_reporting(
    mut c: Command,
    flag: Arc<std::sync::atomic::AtomicBool>,
    timeout: Duration,
    job_id: Option<&str>,
) -> Result<String, String> {
    use std::io::Read;
    use std::os::unix::process::CommandExt;
    use std::sync::atomic::Ordering;
    c.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let mut p = c
        .spawn()
        .map_err(|_| "Could not start container command.".to_string())?;
    let stdout = p.stdout.take().unwrap();
    let stderr = p.stderr.take().unwrap();
    let out_reader = capture_tail(stdout);
    let err_reader = capture_tail(stderr);
    let group = p.id() as i32;
    let t = std::time::Instant::now();
    let status = loop {
        if flag.load(Ordering::SeqCst) || t.elapsed() > timeout {
            unsafe {
                libc::kill(-group, libc::SIGTERM);
            }
            thread::sleep(Duration::from_millis(250));
            unsafe {
                libc::kill(-group, libc::SIGKILL);
            }
            let _ = p.wait();
            let _ = out_reader.join();
            let _ = err_reader.join();
            return Err(if flag.load(Ordering::SeqCst) {
                "Cancelled; apt/package state may have changed and no automatic repair was attempted.".into()
            } else {
                "Container command timed out.".into()
            });
        }
        match p.try_wait() {
            Ok(Some(s)) => break s,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(_) => {
                unsafe {
                    libc::kill(-group, libc::SIGKILL);
                }
                let _ = p.wait();
                return Err("Could not monitor container command.".into());
            }
        }
    };
    unsafe {
        libc::kill(-group, libc::SIGKILL);
    }
    let out = out_reader.join().unwrap_or_default();
    let err = err_reader.join().unwrap_or_default();
    let sample = sanitized_tail(if !err.is_empty() { &err } else { &out });
    if let Some(id) = job_id {
        if !sample.is_empty() {
            job_detail(
                id,
                format!(
                    "{} Output: {}",
                    if status.success() {
                        "Command completed."
                    } else {
                        "Command failed."
                    },
                    sample
                ),
            );
        }
    }
    if status.success() {
        Ok(sample)
    } else {
        Err(if sample.is_empty() {
            "Container command failed.".into()
        } else {
            format!("Container command failed: {}", sample)
        })
    }
}
fn child(
    c: Command,
    flag: Arc<std::sync::atomic::AtomicBool>,
    timeout: Duration,
) -> Result<(), String> {
    child_reporting(c, flag, timeout, None).map(|_| ())
}
fn enter_command(container_id: &str, command: &[String]) -> Result<Command, String> {
    let mut c = Command::new(distrobox()?);
    c.args(distrobox_command_args(command, container_id))
        .env("DBX_CONTAINER_MANAGER", "podman")
        .env("LC_ALL", "C");
    Ok(c)
}
fn run_apt_refresh(
    container_id: &str,
    cancel: &Arc<std::sync::atomic::AtomicBool>,
    job_id: &str,
) -> Result<(), String> {
    for (index, command) in apt_refresh_commands().iter().enumerate() {
        validate_owned_identity(&inspect_all()?, container_id)?;
        job_detail(
            job_id,
            match index {
                0 => "Refreshing apt indexes.",
                1 => "Upgrading apt to the USN-8996-1 floor.",
                _ => "Checking the apt security floor.",
            }
            .into(),
        );
        let c = enter_command(container_id, command)?;
        child_reporting(c, cancel.clone(), Duration::from_secs(300), Some(job_id))?;
    }
    Ok(())
}
fn create_environment(
    cancel: &Arc<std::sync::atomic::AtomicBool>,
    job_id: &str,
) -> Result<String, String> {
    if state_inner()?.state != "absent" {
        return Err("Reserved name is occupied.".into());
    }
    job_detail(job_id, "Creating the app container.".into());
    let mut c = Command::new(distrobox()?);
    c.args(create_args())
        .env("DBX_CONTAINER_MANAGER", "podman")
        .env("LC_ALL", "C");
    child(c, cancel.clone(), Duration::from_secs(600))?;
    if state_inner()?.state != "managed" {
        return Err(
            "Create command finished, but the app container could not be verified.".into(),
        );
    }
    let container_id = validate_owned_container()?;
    run_apt_refresh(&container_id, cancel, job_id).map_err(|_| {
        "App container created, but the apt security floor could not be installed.".to_string()
    })?;
    Ok(container_id)
}
fn package_status(container_id: &str, package: &str) -> Result<String, String> {
    let mut c = enter_command(
        container_id,
        &[
            "/usr/bin/dpkg-query".into(),
            "-W".into(),
            "-f=${db:Status-Status}".into(),
            package.into(),
        ],
    )?;
    let output = run_bounded_command(c, Duration::from_secs(60))
        .map_err(|_| "Package verification unavailable or timed out.".to_string())?;
    if output.stdout.len() > MAX_OUTPUT || output.stderr.len() > MAX_OUTPUT {
        return Err("Package verification output exceeded limit.".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
fn export_command(app: Option<&str>, bin: Option<&str>, delete: bool) -> Vec<String> {
    let mut command: Vec<String> = vec!["distrobox-export".into()];
    if let Some(app) = app {
        command.push("--app".into());
        command.push(app.into());
    }
    if let Some(bin) = bin {
        command.push("--bin".into());
        command.push(bin.into());
    }
    if delete {
        command.push("--delete".into());
    }
    command
}
fn unexport_all(
    container_id: &str,
    app: &DebApp,
    cancel: &Arc<std::sync::atomic::AtomicBool>,
    job_id: &str,
) -> Result<(), String> {
    for exported in &app.exported_apps {
        job_detail(job_id, format!("Removing the menu entry for {}.", exported));
        let c = enter_command(container_id, &export_command(Some(exported), None, true))?;
        child_reporting(c, cancel.clone(), Duration::from_secs(120), Some(job_id))?;
    }
    for bin in &app.exported_bins {
        job_detail(job_id, format!("Removing the exported command {}.", bin));
        let c = enter_command(container_id, &export_command(None, Some(bin), true))?;
        child_reporting(c, cancel.clone(), Duration::from_secs(120), Some(job_id))?;
    }
    Ok(())
}
fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[derive(Clone, Debug, PartialEq)]
enum PlannedOp {
    Create,
    Delete,
    Remove(String),
    Install,
}
fn valid_package_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 128
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"+.-".contains(&b))
        && name
            .bytes()
            .next()
            .map(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
            .unwrap_or(false)
}
fn plan_operation(
    operation: &str,
    confirmation: Option<&str>,
    path: Option<&str>,
) -> Result<PlannedOp, String> {
    match operation {
        "create" => {
            if confirmation.is_some() {
                return Err("Create does not accept confirmation.".into());
            }
            Ok(PlannedOp::Create)
        }
        "delete" => {
            if confirmation != Some(NAME) {
                return Err("Delete requires exact container-name confirmation.".into());
            }
            Ok(PlannedOp::Delete)
        }
        "install" => {
            if path.map(str::is_empty).unwrap_or(true) {
                return Err("Install requires a package file.".into());
            }
            Ok(PlannedOp::Install)
        }
        other => {
            if let Some(package) = other.strip_prefix("remove:") {
                if !valid_package_name(package) {
                    return Err("Unsupported package name.".into());
                }
                if confirmation != Some(package) {
                    return Err("Removal requires exact package-name confirmation.".into());
                }
                return Ok(PlannedOp::Remove(package.to_string()));
            }
            Err("Unsupported operation.".into())
        }
    }
}

fn perform(
    plan: PlannedOp,
    confirmation: Option<String>,
    path: Option<String>,
    cancel: Arc<std::sync::atomic::AtomicBool>,
    job_id: &str,
) -> Result<String, String> {
    use std::sync::atomic::Ordering;
    let _guard = LIFECYCLE
        .get_or_init(|| Mutex::new(()))
        .try_lock()
        .map_err(|_| "App container lifecycle is busy.".to_string())?;
    if cancel.load(Ordering::SeqCst) {
        return Err("Cancelled.".into());
    }
    match plan {
        PlannedOp::Create => {
            create_environment(&cancel, job_id)?;
            Ok("App container created and apt updated to the USN-8996-1 floor.".into())
        }
        PlannedOp::Install => {
            let raw_path = path.ok_or("Install requires a package file.")?;
            let home = home_dir()?;
            let file = validate_deb_file(&home, Path::new(&raw_path))?;
            job_detail(job_id, "Inspecting the package.".into());
            let report = run_inspect(&file)?;
            if report.verdict != "user-app" {
                let reasons = report
                    .reasons
                    .iter()
                    .map(|reason| reason.text.clone())
                    .collect::<Vec<_>>()
                    .join("; ");
                return Err(format!(
                    "This package is a system package and cannot be installed in the app container: {reasons}"
                ));
            }
            if confirmation.as_deref() != Some(format!("install:{}", report.package).as_str()) {
                return Err("Package installation requires explicit confirmation.".into());
            }
            if report.desktop_files.is_empty() && report.binaries.is_empty() {
                return Err(
                    "This package contains no application or command to add to your menu (it may be fonts or data for another program); nothing was installed."
                        .into(),
                );
            }
            let container_id = match state_inner()?.state.as_str() {
                "managed" => validate_owned_container()?,
                "absent" => create_environment(&cancel, job_id)?,
                _ => return Err("App container is not owned by Rowyn; no changes made.".into()),
            };
            run_apt_refresh(&container_id, &cancel, job_id)?;
            job_detail(
                job_id,
                format!("Installing {} {} into the app container.", report.package, report.version),
            );
            let install = sudo_apt(&["install", "-y", file.to_string_lossy().as_ref()]);
            let c = enter_command(&container_id, &install)?;
            child_reporting(c, cancel.clone(), Duration::from_secs(1200), Some(job_id))?;
            validate_owned_identity(&inspect_all()?, &container_id)?;
            if package_status(&container_id, &report.package)? != "installed" {
                return Err(
                    "Install command finished, but the package is not installed; apt state may have changed."
                        .into(),
                );
            }
            let mut exported_apps = Vec::new();
            for desktop in &report.desktop_files {
                let app_id = desktop
                    .rsplit('/')
                    .next()
                    .unwrap_or_default()
                    .trim_end_matches(".desktop");
                if app_id.is_empty() {
                    continue;
                }
                job_detail(job_id, format!("Adding {} to your application menu.", app_id));
                let c = enter_command(
                    &container_id,
                    &export_command(Some(app_id), None, false),
                )?;
                child_reporting(c, cancel.clone(), Duration::from_secs(120), Some(job_id))?;
                exported_apps.push(app_id.to_string());
            }
            let mut exported_bins = Vec::new();
            if exported_apps.is_empty() {
                for bin in report.binaries.iter().take(MAX_EXPORTED_BINS) {
                    job_detail(job_id, format!("Exporting the {} command.", bin));
                    let c = enter_command(
                        &container_id,
                        &export_command(None, Some(bin), false),
                    )?;
                    child_reporting(c, cancel.clone(), Duration::from_secs(120), Some(job_id))?;
                    exported_bins.push(bin.clone());
                }
            }
            let dir = state_dir()?;
            let mut apps = read_registry_in(&dir)?;
            apps.retain(|app| app.package != report.package);
            apps.push(DebApp {
                package: report.package.clone(),
                version: report.version.clone(),
                source: file
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                exported_apps,
                exported_bins,
                installed_at: now_secs(),
            });
            apps.sort_by(|a, b| a.package.cmp(&b.package));
            write_registry_in(&dir, &apps)?;
            Ok(format!(
                "Installed {} {} into the app container (rowyn-deb).",
                report.package, report.version
            ))
        }
        PlannedOp::Remove(package) => {
            let dir = state_dir()?;
            let mut apps = read_registry_in(&dir)?;
            let Some(index) = apps.iter().position(|app| app.package == package) else {
                return Err("That package is not recorded as installed.".into());
            };
            if state_inner()?.state != "managed" {
                return Err("App container unavailable.".into());
            }
            let container_id = validate_owned_container()?;
            let app = apps[index].clone();
            unexport_all(&container_id, &app, &cancel, job_id)?;
            job_detail(job_id, format!("Removing {} from the app container.", package));
            let remove = sudo_apt(&["remove", "-y", &package]);
            let c = enter_command(&container_id, &remove)?;
            child_reporting(c, cancel.clone(), Duration::from_secs(600), Some(job_id))?;
            validate_owned_identity(&inspect_all()?, &container_id)?;
            if package_status(&container_id, &package) == Ok("installed".to_string()) {
                return Err(
                    "Removal command finished, but the package is still installed.".into(),
                );
            }
            apps.remove(index);
            write_registry_in(&dir, &apps)?;
            Ok(format!(
                "Removed {} from the app container and your menu.",
                package
            ))
        }
        PlannedOp::Delete => {
            let dir = state_dir()?;
            let apps = read_registry_in(&dir)?;
            match state_inner()?.state.as_str() {
                "managed" => {
                    let container_id = validate_owned_container()?;
                    for app in &apps {
                        unexport_all(&container_id, app, &cancel, job_id)?;
                    }
                    let c = inspect_all()?
                        .into_iter()
                        .find(|r| r.name == NAME)
                        .ok_or("App container is absent.")?;
                    if c.status.to_lowercase().contains("running") {
                        run(
                            &podman()?,
                            &["stop", "--time", "10", &c.id],
                            Duration::from_secs(20),
                        )?;
                    }
                    if cancel.load(Ordering::SeqCst) {
                        return Err("Cancelled.".into());
                    }
                    let c = inspect_all()?
                        .into_iter()
                        .find(|r| r.id == c.id)
                        .ok_or("Container identity changed.".to_string())?;
                    if !ownership(&c) || c.name != NAME || c.status.to_lowercase().contains("running")
                    {
                        return Err("Container changed; removal refused.".into());
                    }
                    run(&podman()?, &["rm", &c.id], Duration::from_secs(30))?;
                    if inspect_all()?.iter().any(|r| r.id == c.id) {
                        return Err("Removal command finished, but container still exists.".into());
                    }
                }
                "absent" => {
                    if apps.is_empty() {
                        return Err("App container is absent.".into());
                    }
                }
                _ => return Err("App container is not owned by Rowyn; no changes made.".into()),
            }
            write_registry_in(&dir, &[])?;
            Ok("App container deleted; its apps were removed and your home directory was retained.".into())
        }
    }
}
fn id() -> String {
    format!(
        "deb-{}-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
        std::process::id()
    )
}
fn finish(id: &str, result: Result<String, String>) {
    if let Ok(mut x) = jobs().lock() {
        if let Some(e) = x.0.get_mut(id) {
            match result {
                Ok(s) => {
                    e.job.status = "succeeded".into();
                    e.job.detail = s
                }
                Err(s) => {
                    e.job.status = if s.starts_with("Cancelled") {
                        "cancelled"
                    } else {
                        "failed"
                    }
                    .into();
                    e.job.detail = s
                }
            }
            e.cancel = None
        }
    }
}

#[tauri::command]
pub(crate) async fn rowyn_deb_state() -> Result<DebState, String> {
    tauri::async_runtime::spawn_blocking(|| {
        Ok(DebState {
            container: state_inner()?,
            apps: read_registry()?,
            pending: pending_inner(),
        })
    })
    .await
    .map_err(|_| "App container worker failed".to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_deb_inspect(path: String) -> Result<DebInspectReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let home = home_dir()?;
        let file = validate_deb_file(&home, Path::new(&path))?;
        run_inspect(&file)
    })
    .await
    .map_err(|_| "Inspection worker failed".to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_deb_start(
    operation: String,
    confirmation: Option<String>,
    path: Option<String>,
) -> Result<DebJob, String> {
    let plan = plan_operation(&operation, confirmation.as_deref(), path.as_deref())?;
    let ident = id();
    let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    {
        let mut x = jobs()
            .lock()
            .map_err(|_| "Job registry unavailable".to_string())?;
        while x.1.len() >= MAX_JOBS {
            if let Some(old) = x.1.pop_front() {
                if x.0
                    .get(&old)
                    .map(|e| e.job.status != "running")
                    .unwrap_or(true)
                {
                    x.0.remove(&old);
                } else {
                    x.1.push_front(old);
                    break;
                }
            }
        }
        if x.0.len() >= MAX_JOBS {
            return Err("Job registry is full.".into());
        }
        if x.0.values().any(|e| e.job.status == "running") {
            return Err("An app container job is already running.".into());
        }
        x.0.insert(
            ident.clone(),
            Entry {
                job: DebJob {
                    id: ident.clone(),
                    status: "running".into(),
                    detail: "Operation started.".into(),
                },
                cancel: Some(flag.clone()),
            },
        );
        x.1.push_back(ident.clone())
    }
    let worker_id = ident.clone();
    thread::spawn(move || {
        finish(
            &worker_id,
            perform(plan, confirmation, path, flag, &worker_id),
        )
    });
    Ok(DebJob {
        id: ident,
        status: "running".into(),
        detail: "Operation started.".into(),
    })
}

#[tauri::command]
pub(crate) async fn rowyn_deb_job(id: String) -> Result<DebJob, String> {
    jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?
        .0
        .get(&id)
        .map(|e| e.job.clone())
        .ok_or_else(|| "Unknown app container job.".into())
}

#[tauri::command]
pub(crate) async fn rowyn_deb_cancel(id: String) -> Result<DebJob, String> {
    let mut x = jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?;
    let e = x.0.get_mut(&id).ok_or("Unknown app container job.")?;
    if let Some(c) = &e.cancel {
        c.store(true, std::sync::atomic::Ordering::SeqCst);
        e.job.detail = "Cancellation requested.".into()
    }
    Ok(e.job.clone())
}

#[cfg(test)]
#[path = "rowyn_deb_tests.rs"]
mod tests;
