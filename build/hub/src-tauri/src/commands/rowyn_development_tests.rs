use super::*;
use crate::commands::rowyn_test_temp::compliant_fixture;

fn owned() -> ContainerIdentity {
    ContainerIdentity {
        id: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
        name: NAME.into(),
        image: IMAGE.into(),
        labels: [
            (LABEL.into(), LABEL_VALUE.into()),
            ("manager".into(), "distrobox".into()),
        ]
        .into_iter()
        .collect(),
        status: "running".into(),
    }
}

#[test]
fn only_fixed_development_name_is_accepted() {
    assert!(validate_name(NAME).is_ok());
    assert!(validate_name("other").is_err());
    assert!(validate_name("../ubuntu-dev").is_err());
}

#[test]
fn ownership_requires_exact_name_label_manager_and_image() {
    let valid = owned();
    assert!(ownership(&valid));
    let mut bad = valid.clone();
    bad.image = "ubuntu:latest".into();
    assert!(!ownership(&bad));
    let mut bad = valid.clone();
    bad.labels.remove(LABEL);
    assert!(!ownership(&bad));
    let mut bad = valid.clone();
    bad.labels.insert("manager".into(), "other".into());
    assert!(!ownership(&bad));
    let mut bad = valid;
    bad.name = "unrelated".into();
    assert!(!ownership(&bad));
}

#[test]
fn parser_accepts_valid_rows_and_rejects_oversize_or_malformed_data() {
    let json = format!(
        r#"[{{"Id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","Names":["/{}"],"Image":"{}","State":"running","Labels":{{"{}":"{}","manager":"distrobox"}}}}]"#,
        NAME, IMAGE, LABEL, LABEL_VALUE
    );
    let rows = parse_list(json.as_bytes()).unwrap();
    assert_eq!(rows.len(), 1);
    assert!(ownership(&rows[0]));
    assert!(parse_list(&vec![b'x'; MAX_OUTPUT + 1]).is_err());
    assert!(parse_list(b"not json").is_err());
    assert!(parse_list(br#"[{"Id":"bad;rm","Names":["rowyn-ubuntu-dev"]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","Names":[]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789abcdef","Names":["x"]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF","Names":["x"]}]"#).is_err());
}

#[test]
fn create_arguments_use_fixed_identity_and_noninteractive_mode() {
    let args = create_args();
    assert!(args.windows(2).any(|w| w == ["--name", NAME]));
    assert!(args.windows(2).any(|w| w == ["--image", IMAGE]));
    assert!(args.windows(2).any(|w| w
        == [
            "--additional-flags",
            "--label io.rowyn.development=ubuntu-v1"
        ]));
    assert!(args.iter().any(|a| *a == "--yes"));
}

#[test]
fn controlled_child_lifecycle_reports_success_failure_cancellation_and_timeout() {
    use std::sync::atomic::AtomicBool;
    let flag = || Arc::new(AtomicBool::new(false));
    let mut success = Command::new("/bin/sh");
    success.args(["-c", "exit 0"]);
    assert!(child(success, flag(), Duration::from_secs(2)).is_ok());
    let mut flood = Command::new("/usr/bin/python3");
    flood.args([
        "-c",
        "import sys; sys.stdout.write('o'*700000); sys.stderr.write('e'*700000)",
    ]);
    assert!(child(flood, flag(), Duration::from_secs(5)).is_ok());
    let mut failure = Command::new("/bin/sh");
    failure.args(["-c", "exit 9"]);
    assert!(child(failure, flag(), Duration::from_secs(2)).is_err());
    let cancelled = flag();
    let mut sleeper = Command::new("/bin/sh");
    sleeper.args(["-c", "sleep 5"]);
    let worker_flag = cancelled.clone();
    let join = thread::spawn(move || child(sleeper, worker_flag, Duration::from_secs(5)));
    thread::sleep(Duration::from_millis(100));
    cancelled.store(true, std::sync::atomic::Ordering::SeqCst);
    assert!(join.join().unwrap().unwrap_err().starts_with("Cancelled"));
    let mut sleeper = Command::new("/bin/sh");
    sleeper.args(["-c", "sleep 5"]);
    assert_eq!(
        child(sleeper, flag(), Duration::from_millis(150)).unwrap_err(),
        "Development command timed out."
    );
}

#[test]
fn job_terminal_transitions_and_unknown_job_errors() {
    let key = format!("test-{}", id());
    let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    jobs().lock().unwrap().0.insert(
        key.clone(),
        Entry {
            job: DevelopmentJob {
                id: key.clone(),
                status: "running".into(),
                detail: "started".into(),
            },
            cancel: Some(flag),
        },
    );
    finish(
        &key,
        Err("Cancelled; packages may be partially installed.".into()),
    );
    let cancelled = tauri::async_runtime::block_on(rowyn_development_job(key.clone())).unwrap();
    assert_eq!(cancelled.status, "cancelled");
    finish(&key, Ok("verified".into()));
    let job = tauri::async_runtime::block_on(rowyn_development_job(key.clone())).unwrap();
    assert_eq!(job.status, "succeeded");
    assert_eq!(job.detail, "verified");
    assert!(tauri::async_runtime::block_on(rowyn_development_job("missing".into())).is_err());
    assert!(tauri::async_runtime::block_on(rowyn_development_cancel("missing".into())).is_err());
    jobs().lock().unwrap().0.remove(&key);
}

#[test]
fn fixed_presets_build_exact_apt_plans_and_reject_arbitrary_packages() {
    assert_eq!(
        preset_packages("git-build").unwrap(),
        &["git", "build-essential", "pkg-config", "cmake"]
    );
    assert_eq!(
        preset_packages("python").unwrap(),
        &["python3", "python3-pip", "python3-venv"]
    );
    assert_eq!(preset_packages("node").unwrap(), &["nodejs", "npm"]);
    assert_eq!(preset_packages("rust").unwrap(), &["rustc", "cargo"]);
    assert!(preset_packages("git; rm -rf /").is_err());
    for preset in ["git-build", "python", "node", "rust"] {
        let plan = preset_commands(preset).unwrap();
        assert_eq!(plan.len(), 5);
        assert_eq!(plan[0], sudo_apt(&["update"]));
        assert_eq!(
            plan[1],
            sudo_apt(&[
                "install",
                "-y",
                "--no-install-recommends",
                "--only-upgrade",
                "apt"
            ])
        );
        assert_eq!(plan[2], apt_floor_command());
        assert!(plan[2][2].contains(APT_FLOOR));
        let mut install = sudo_apt(&["install", "-y", "--no-install-recommends"]);
        install.extend(
            preset_packages(preset)
                .unwrap()
                .iter()
                .map(|package| (*package).to_string()),
        );
        assert_eq!(plan[3], install);
        assert_eq!(
            plan[4],
            [
                vec!["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}\\n"],
                preset_packages(preset).unwrap().to_vec()
            ]
            .concat()
        );
        assert_eq!(
            plan[4][3..],
            preset_packages(preset)
                .unwrap()
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>()
        );
        assert_eq!(
            distrobox_command_args(&plan[0], &owned().id)[..4],
            ["enter", "--name", owned().id.as_str(), "--"]
        );
    }
    assert!(preset_commands("arbitrary").is_err());
}

#[test]
fn immutable_container_identity_is_required_and_used_for_preset_execution() {
    let original = owned();
    let replacement = ContainerIdentity {
        id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
        ..original.clone()
    };
    assert_eq!(
        validate_owned_identity(&[original.clone()], &original.id).unwrap(),
        original.id
    );
    assert!(validate_owned_identity(&[replacement], &original.id).is_err());
    assert!(validate_owned_identity(&[original.clone(), original.clone()], &original.id).is_err());
    assert_eq!(
        distrobox_command_args(&["/usr/bin/apt-get".into(), "update".into()], &original.id),
        vec![
            "enter",
            "--name",
            original.id.as_str(),
            "--",
            "/usr/bin/apt-get",
            "update"
        ]
    );
}

#[test]
fn dolphin_path_policy_accepts_only_canonical_home_directories() {
    let Some(home) = compliant_fixture("dev-home") else {
        eprintln!(
            "skipping dolphin_path_policy_accepts_only_canonical_home_directories: \
             no policy-compliant temp base"
        );
        return;
    };
    let inside = home.join("source dir");
    std::fs::create_dir_all(&inside).unwrap();
    assert_eq!(validate_open_directory(&home, &inside).unwrap(), inside);
    assert!(validate_open_directory(&home, &home.join("missing")).is_err());
    assert!(validate_open_directory(&home, std::path::Path::new("/tmp")).is_err());
    assert!(validate_open_directory(&home, std::path::Path::new("--help")).is_err());
    std::fs::remove_dir_all(home).unwrap();
}

#[test]
fn desktop_service_menu_uses_fixed_command_and_directory_mime_type() {
    let menu = include_str!("../../data/kde/servicemenus/rowyn-development-here.desktop");
    assert!(menu.contains("Exec=/usr/bin/rowyn-hub-shell --development-directory %f"));
    assert!(menu.contains("MimeType=inode/directory;"));
}

#[test]
fn managed_image_is_digest_pinned_and_legacy_tag_containers_stay_owned() {
    assert!(IMAGE.starts_with("docker.io/library/ubuntu@sha256:"));
    assert_eq!(IMAGE.rsplit('@').next().unwrap().len(), "sha256:".len() + 64);
    let mut legacy = owned();
    legacy.image = IMAGE_TAG_ALIAS.into();
    assert!(ownership(&legacy));
    let mut foreign = owned();
    foreign.image = "docker.io/library/ubuntu:latest".into();
    assert!(!ownership(&foreign));
    // A scheduled digest bump must not orphan containers created on the pin
    // the build used before it moved.
    let mut previous = owned();
    previous.image = format!("docker.io/library/ubuntu@sha256:{}", "a".repeat(64));
    assert!(ownership(&previous));
}

#[test]
fn tool_resolution_prefers_installed_locations_then_path_and_ignores_gaps() {
    let Some(dir) = compliant_fixture("dev-tools") else {
        eprintln!(
            "skipping tool_resolution_prefers_installed_locations_then_path_and_ignores_gaps: \
             no policy-compliant temp base"
        );
        return;
    };
    std::fs::create_dir_all(&dir).unwrap();
    let fallback = dir.join("rowyn-test-tool");
    std::fs::write(&fallback, b"#!/bin/sh\n").unwrap();
    // Fallbacks are full binary paths, not directories to search.
    assert_eq!(
        resolve_tool("rowyn-test-tool", &[fallback.to_str().unwrap()]),
        Some(fallback.clone())
    );
    assert!(resolve_tool("rowyn-test-tool", &[]).is_none());
    assert!(resolve_tool("rowyn-test-tool", &[dir.to_str().unwrap()]).is_none());
    assert!(resolve_tool("rowyn-absent-tool", &[dir.join("rowyn-absent-tool").to_str().unwrap()]).is_none());
    let sh = resolve_tool("sh", &[]).expect("PATH resolves sh");
    assert!(sh.is_absolute() && sh.is_file());
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn container_tool_fallbacks_cover_package_and_local_install_locations() {
    assert!(PODMAN_FALLBACKS.contains(&"/usr/bin/podman"));
    assert!(PODMAN_FALLBACKS.contains(&"/usr/local/bin/podman"));
    assert!(DISTROBOX_FALLBACKS.contains(&"/usr/bin/distrobox"));
    assert!(DISTROBOX_FALLBACKS.contains(&"/usr/local/bin/distrobox"));
    assert!(TERMINAL_FALLBACKS.contains(&"/usr/bin/konsole"));
}

#[test]
fn create_manager_label_matches_distrobox_ownership_contract() {
    assert_eq!(
        owned().labels.get("manager").map(String::as_str),
        Some("distrobox")
    );
}
