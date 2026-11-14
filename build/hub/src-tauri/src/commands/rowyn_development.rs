//! Dedicated, fail-closed backend for the fixed Rowyn Ubuntu development box.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    process::{Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
const NAME: &str = "rowyn-ubuntu-dev";
// Digest-pinned: the 26.10 tag moves, and a moving tag cannot decide which
// image the managed environment is verified against.
const IMAGE: &str =
    "docker.io/library/ubuntu@sha256:b5194b8e58989da0865ebc80078bf9f5f8742ec6a867c53f6ae699f6036756f7";
// Containers created before the digest pin report the tag; the Rowyn labels
// still decide ownership.
const IMAGE_TAG_ALIAS: &str = "docker.io/library/ubuntu:26.10";
// USN-8996-1 (CVE-2026-109518): no published Ubuntu image carries this fix yet,
// so the managed container installs the fixed apt itself before any preset.
const APT_FLOOR: &str = "3.3.3ubuntu0.1";
// A container carrying the Rowyn labels stays ours even when it still runs a
// digest the pin has since moved past; only the tag and other images differ.
fn managed_image(image: &str) -> bool {
    image == IMAGE_TAG_ALIAS || image.starts_with("docker.io/library/ubuntu@sha256:")
}
const LABEL: &str = "io.rowyn.development";
const LABEL_VALUE: &str = "ubuntu-v1";
const MAX_OUTPUT: usize = 512 * 1024;
// Resolve the container tooling instead of assuming /usr/bin. A hardcoded path
// meant a host that installs distrobox through its own installer
// (/usr/local/bin) or podman from a non-package location reported the whole
// environment as unavailable, disabling every action even with a box present.
// Package locations are tried first so a user-writable PATH entry cannot stand
// in for the reviewed binaries; PATH itself is the last resort.
const PODMAN_FALLBACKS: &[&str] = &["/usr/bin/podman", "/usr/local/bin/podman", "/usr/sbin/podman"];
const DISTROBOX_FALLBACKS: &[&str] = &["/usr/bin/distrobox", "/usr/local/bin/distrobox"];
const TERMINAL_FALLBACKS: &[&str] = &["/usr/bin/konsole", "/usr/local/bin/konsole"];
fn resolve_tool(name: &str, fallbacks: &[&str]) -> Option<std::path::PathBuf> {
    fallbacks
        .iter()
        .map(std::path::PathBuf::from)
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
        "podman is not installed, so the development environment cannot be read. Install podman and distrobox to use it.",
    )
}
fn distrobox() -> Result<String, String> {
    tool(
        "distrobox",
        DISTROBOX_FALLBACKS,
        "distrobox is not installed, so the development environment cannot be created or entered.",
    )
}
fn terminal() -> String {
    resolve_tool("konsole", TERMINAL_FALLBACKS)
        .map(|path| path.to_string_lossy().into_owned())
        .unwrap_or_else(|| "/usr/bin/konsole".into())
}
const MAX_JOBS: usize = 64;
const PRESETS: &[(&str, &[&str])] = &[
    (
        "git-build",
        &["git", "build-essential", "pkg-config", "cmake"],
    ),
    ("python", &["python3", "python3-pip", "python3-venv"]),
    ("node", &["nodejs", "npm"]),
    ("rust", &["rustc", "cargo"]),
];
fn preset_packages(name: &str) -> Result<&'static [&'static str], String> {
    PRESETS
        .iter()
        .find(|(key, _)| *key == name)
        .map(|(_, packages)| *packages)
        .ok_or_else(|| "Unsupported development preset.".into())
}
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
// Every package operation refreshes indexes, takes the fixed apt from the
// security archive, and refuses to continue below the USN-8996-1 floor.
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
fn preset_commands(preset: &str) -> Result<Vec<Vec<String>>, String> {
    let packages = preset_packages(preset)?;
    let mut commands = apt_refresh_commands();
    let mut install = sudo_apt(&["install", "-y", "--no-install-recommends"]);
    install.extend(packages.iter().map(|p| (*p).into()));
    commands.push(install);
    let mut verify = vec![
        "/usr/bin/dpkg-query".into(),
        "-W".into(),
        "-f=${db:Status-Status}\\n".into(),
    ];
    verify.extend(packages.iter().map(|p| (*p).into()));
    commands.push(verify);
    Ok(commands)
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
        _ => Err("Managed environment ownership or identity changed.".into()),
    }
}
fn validate_owned_container() -> Result<String, String> {
    let matches: Vec<_> = inspect_all()?
        .into_iter()
        .filter(|c| c.name == NAME)
        .collect();
    match matches.as_slice() {
        [c] if ownership(c) => Ok(c.id.clone()),
        _ => Err("Managed environment ownership or identity could not be verified.".into()),
    }
}
fn validate_open_directory(
    home: &std::path::Path,
    path: &std::path::Path,
) -> Result<std::path::PathBuf, String> {
    let home = home
        .canonicalize()
        .map_err(|_| "Home directory unavailable.")?;
    if path.to_string_lossy().chars().any(|c| c.is_control())
        || path.to_string_lossy().starts_with('-')
    {
        return Err("Invalid directory path.".into());
    }
    let path = path.canonicalize().map_err(|_| "Directory unavailable.")?;
    if !path.is_dir() || !path.starts_with(&home) {
        return Err("Directory must be inside your home directory.".into());
    }
    Ok(path)
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
pub(crate) struct DevelopmentState {
    state: String,
    name: String,
    image: String,
    status: String,
    detail: String,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DevelopmentJob {
    id: String,
    status: String,
    detail: String,
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
    job: DevelopmentJob,
    cancel: Option<Arc<std::sync::atomic::AtomicBool>>,
}
static JOBS: OnceLock<Mutex<(HashMap<String, Entry>, VecDeque<String>)>> = OnceLock::new();
fn jobs() -> &'static Mutex<(HashMap<String, Entry>, VecDeque<String>)> {
    JOBS.get_or_init(|| Mutex::new((HashMap::new(), VecDeque::new())))
}
fn validate_name(n: &str) -> Result<(), String> {
    if n == NAME {
        Ok(())
    } else {
        Err("Unsupported development environment name.".into())
    }
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
        "--label io.rowyn.development=ubuntu-v1",
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
            "name=^rowyn-ubuntu-dev$",
            "--format",
            "json",
        ],
        Duration::from_secs(10),
    )?)
}
fn state_inner() -> Result<DevelopmentState, String> {
    let v: Vec<_> = inspect_all()?
        .into_iter()
        .filter(|r| r.name == NAME)
        .collect();
    match v.as_slice() {
        [] => Ok(DevelopmentState {
            state: "absent".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "absent".into(),
            detail: "Environment not created.".into(),
        }),
        [c] if ownership(c) => Ok(DevelopmentState {
            state: "managed".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: c.status.clone(),
            detail: "Rowyn-managed environment.".into(),
        }),
        _ => Ok(DevelopmentState {
            state: "collision".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "unknown".into(),
            detail: "Reserved name is not owned by Rowyn; no changes made.".into(),
        }),
    }
}
#[tauri::command]
pub(crate) async fn rowyn_development_list() -> Result<DevelopmentState, String> {
    tauri::async_runtime::spawn_blocking(state_inner)
        .await
        .map_err(|_| "Development worker failed".to_string())?
}
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
        .map_err(|_| "Could not start development command.".to_string())?;
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
                "Development command timed out.".into()
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
                return Err("Could not monitor development command.".into());
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
            "Development command failed.".into()
        } else {
            format!("Development command failed: {}", sample)
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
fn perform(
    op: String,
    confirmation: Option<String>,
    cancel: Arc<std::sync::atomic::AtomicBool>,
    job_id: &str,
) -> Result<String, String> {
    use std::sync::atomic::Ordering;
    let _guard = LIFECYCLE
        .get_or_init(|| Mutex::new(()))
        .try_lock()
        .map_err(|_| "Development lifecycle is busy.".to_string())?;
    if cancel.load(Ordering::SeqCst) {
        return Err("Cancelled.".into());
    }
    match op.as_str() {
        op if op.starts_with("preset:") => {
            let preset = &op[7..];
            let commands = preset_commands(preset)?;
            let packages = preset_packages(preset)?;
            if confirmation.as_deref() != Some(format!("install:{preset}").as_str()) {
                return Err("Package installation requires explicit confirmation.".into());
            }
            if state_inner()?.state != "managed" {
                return Err("Managed environment unavailable.".into());
            }
            let container_id = validate_owned_container()?;
            let verify_index = commands.len() - 1;
            for (index, command) in commands.iter().enumerate() {
                validate_owned_identity(&inspect_all()?, &container_id)?;
                if cancel.load(Ordering::SeqCst) {
                    return Err("Cancelled.".into());
                }
                let (step, timeout) = match index {
                    0 => ("Refreshing apt indexes.", Duration::from_secs(300)),
                    1 => (
                        "Upgrading apt to the USN-8996-1 floor.",
                        Duration::from_secs(300),
                    ),
                    2 => ("Checking the apt security floor.", Duration::from_secs(60)),
                    index if index == verify_index => (
                        "Verifying installed packages.",
                        Duration::from_secs(60),
                    ),
                    _ => ("Installing preset packages.", Duration::from_secs(900)),
                };
                job_detail(job_id, step.into());
                let args = distrobox_command_args(command, &container_id);
                let mut c = Command::new(distrobox()?);
                c.args(&args)
                    .env("DBX_CONTAINER_MANAGER", "podman")
                    .env("LC_ALL", "C");
                if index != verify_index {
                    child_reporting(c, cancel.clone(), timeout, Some(job_id))?;
                } else {
                    let output = run_bounded_command(c, Duration::from_secs(60)).map_err(|_| {
                        "Package verification unavailable or timed out.".to_string()
                    })?;
                    if !output.status.success()
                        || output.stdout.len() > MAX_OUTPUT
                        || output.stderr.len() > MAX_OUTPUT
                    {
                        return Err(
                            "Package verification failed; apt may have changed package state."
                                .into(),
                        );
                    }
                    let lines: Vec<_> = String::from_utf8_lossy(&output.stdout)
                        .lines()
                        .map(str::to_owned)
                        .collect();
                    if lines.len() != packages.len() || lines.iter().any(|line| line != "installed")
                    {
                        return Err(
                            "Package verification incomplete; apt may have changed package state."
                                .into(),
                        );
                    }
                    validate_owned_identity(&inspect_all()?, &container_id)?;
                    if cancel.load(Ordering::SeqCst) {
                        return Err("Cancelled.".into());
                    }
                }
            }
            Ok(format!(
                "Installed and verified preset {preset}: {}.",
                packages.join(", ")
            ))
        }
        "create" => {
            if confirmation.is_some() {
                return Err("Create does not accept confirmation.".into());
            }
            if state_inner()?.state != "absent" {
                return Err("Reserved name is occupied.".into());
            }
            let mut c = Command::new(distrobox()?);
            c.args(create_args())
                .env("DBX_CONTAINER_MANAGER", "podman")
                .env("LC_ALL", "C");
            child(c, cancel.clone(), Duration::from_secs(300))?;
            if state_inner()?.state != "managed" {
                return Err(
                    "Create command finished, but managed environment could not be verified."
                        .into(),
                );
            }
            let container_id = validate_owned_container()?;
            for (index, command) in apt_refresh_commands().iter().enumerate() {
                validate_owned_identity(&inspect_all()?, &container_id)?;
                if cancel.load(Ordering::SeqCst) {
                    return Err("Cancelled.".into());
                }
                job_detail(
                    job_id,
                    match index {
                        0 => "Refreshing apt indexes.",
                        1 => "Upgrading apt to the USN-8996-1 floor.",
                        _ => "Checking the apt security floor.",
                    }
                    .into(),
                );
                let args = distrobox_command_args(command, &container_id);
                let mut c = Command::new(distrobox()?);
                c.args(&args)
                    .env("DBX_CONTAINER_MANAGER", "podman")
                    .env("LC_ALL", "C");
                child_reporting(c, cancel.clone(), Duration::from_secs(300), Some(job_id))
                    .map_err(|_| {
                        "Environment created, but the apt security floor could not be installed."
                            .to_string()
                    })?;
            }
            Ok("Environment created and apt updated to the USN-8996-1 floor.".into())
        }
        "delete" => {
            if confirmation.as_deref() != Some(NAME) {
                return Err("Delete requires exact environment-name confirmation.".into());
            }
            let c = inspect_all()?
                .into_iter()
                .find(|r| r.name == NAME)
                .ok_or("Environment is absent.")?;
            if !ownership(&c) {
                return Err("Environment ownership could not be verified.".into());
            }
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
            if !ownership(&c) || c.name != NAME || c.status.to_lowercase().contains("running") {
                return Err("Container changed; removal refused.".into());
            }
            run(&podman()?, &["rm", &c.id], Duration::from_secs(30))?;
            if inspect_all()?.iter().any(|r| r.id == c.id) {
                return Err("Removal command finished, but container still exists.".into());
            }
            Ok("Environment deleted; container processes stopped.".into())
        }
        _ => Err("Unsupported operation.".into()),
    }
}
fn id() -> String {
    format!(
        "development-{}-{}",
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
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DevelopmentPlan {
    preset: String,
    target: String,
    packages: Vec<String>,
    commands: Vec<Vec<String>>,
}
#[tauri::command]
pub(crate) async fn rowyn_development_preset_preview(
    preset: String,
) -> Result<DevelopmentPlan, String> {
    let packages = preset_packages(&preset)?;
    let commands = preset_commands(&preset)?;
    Ok(DevelopmentPlan {
        preset: preset.clone(),
        target: NAME.into(),
        packages: packages.iter().map(|p| (*p).into()).collect(),
        commands,
    })
}
#[tauri::command]
pub(crate) async fn rowyn_development_start(
    operation: String,
    confirmation: Option<String>,
) -> Result<DevelopmentJob, String> {
    let preset_operation = operation
        .strip_prefix("preset:")
        .and_then(|preset| preset_packages(preset).ok().map(|_| preset));
    if !matches!(operation.as_str(), "create" | "delete") && preset_operation.is_none() {
        return Err("Unsupported development operation.".into());
    }
    if let Some(preset) = preset_operation {
        if confirmation.as_deref() != Some(format!("install:{preset}").as_str()) {
            return Err("Package installation requires explicit confirmation.".into());
        }
    } else if operation == "delete" && confirmation.as_deref() != Some(NAME) {
        return Err("Delete requires exact environment-name confirmation.".into());
    }
    if operation == "create" && confirmation.is_some() {
        return Err("Create does not accept confirmation.".into());
    }
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
            return Err("A development lifecycle job is already running.".into());
        }
        x.0.insert(
            ident.clone(),
            Entry {
                job: DevelopmentJob {
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
            perform(operation, confirmation, flag, &worker_id),
        )
    });
    Ok(DevelopmentJob {
        id: ident,
        status: "running".into(),
        detail: "Operation started.".into(),
    })
}
#[tauri::command]
pub(crate) async fn rowyn_development_job(id: String) -> Result<DevelopmentJob, String> {
    jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?
        .0
        .get(&id)
        .map(|e| e.job.clone())
        .ok_or_else(|| "Unknown development job.".into())
}
#[tauri::command]
pub(crate) async fn rowyn_development_cancel(id: String) -> Result<DevelopmentJob, String> {
    let mut x = jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?;
    let e = x.0.get_mut(&id).ok_or("Unknown development job.")?;
    if let Some(c) = &e.cancel {
        c.store(true, std::sync::atomic::Ordering::SeqCst);
        e.job.detail = "Cancellation requested.".into()
    }
    Ok(e.job.clone())
}
pub fn handle_development_cli(args: &[std::ffi::OsString]) -> Option<Result<(), String>> {
    if args.first().map(|a| a == "--development-directory") != Some(true) {
        return None;
    }
    Some((|| {
        if args.len() != 2 {
            return Err("Expected one directory argument.".into());
        }
        let raw = std::path::PathBuf::from(&args[1]);
        if raw.to_string_lossy().contains("://") {
            return Err("Directory URLs are not accepted.".into());
        }
        let home = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .ok_or("Home directory unavailable.")?;
        let path = validate_open_directory(&home, &raw)?;
        let _guard = LIFECYCLE
            .get_or_init(|| Mutex::new(()))
            .try_lock()
            .map_err(|_| "Development lifecycle is busy.".to_string())?;
        if state_inner()?.state != "managed" {
            return Err("Managed environment unavailable.".into());
        }
        let container_id = validate_owned_container()?;
        let mut c = Command::new(terminal());
        c.args(["--separate", "--workdir"])
            .arg(&path)
            .arg("-e")
            .arg(distrobox()?)
            .args(["enter", "--name", &container_id, "--", "/bin/sh", "-l"])
            .env("DBX_CONTAINER_MANAGER", "podman");
        spawn_detached(&mut c).map_err(|_| "Could not request terminal window.".to_string())
    })())
}

#[tauri::command]
pub(crate) async fn rowyn_development_open() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let _g = LIFECYCLE
            .get_or_init(|| Mutex::new(()))
            .try_lock()
            .map_err(|_| "Development lifecycle is busy.".to_string())?;
        if state_inner()?.state != "managed" {
            return Err("Managed environment unavailable.".into());
        }
        let container_id = validate_owned_container()?;
        let mut c = Command::new(terminal());
        c.args([
            "--separate",
            "-e",
            &distrobox()?,
            "enter",
            "--name",
            &container_id,
        ]);
        c.env("DBX_CONTAINER_MANAGER", "podman");
        spawn_detached(&mut c).map_err(|_| "Could not request terminal window.".to_string())?;
        Ok("Terminal launch requested; readiness unverified.".into())
    })
    .await
    .map_err(|_| "Development worker failed".to_string())?
}
#[cfg(test)]
#[path = "rowyn_development_tests.rs"]
mod tests;
