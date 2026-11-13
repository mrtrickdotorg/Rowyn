use super::*;
use std::fs;

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
    bad.name = "rowyn-ubuntu-dev".into();
    assert!(!ownership(&bad));
}

#[test]
fn parser_accepts_valid_rows_and_rejects_malformed_data() {
    let json = format!(
        r#"[{{"Id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","Names":["/{}"],"Image":"{}","State":"running","Labels":{{"{}":"{}","manager":"distrobox"}}}}]"#,
        NAME, IMAGE, LABEL, LABEL_VALUE
    );
    let rows = parse_list(json.as_bytes()).unwrap();
    assert_eq!(rows.len(), 1);
    assert!(ownership(&rows[0]));
    assert!(parse_list(&vec![b'x'; MAX_OUTPUT + 1]).is_err());
    assert!(parse_list(b"not json").is_err());
    assert!(parse_list(br#"[{"Id":"bad;rm","Names":["rowyn-deb"]}]"#).is_err());
}

#[test]
fn create_arguments_use_fixed_identity_and_noninteractive_mode() {
    let args = create_args();
    assert!(args.windows(2).any(|w| w == ["--name", NAME]));
    assert!(args.windows(2).any(|w| w == ["--image", IMAGE]));
    assert!(args
        .windows(2)
        .any(|w| w == ["--additional-flags", "--label io.rowyn.deb=deb-v1"]));
    assert!(args.iter().any(|a| *a == "--yes"));
    // The deb container and the development container share the digest
    // pin; build/ubuntu-pin-bump.py lists both modules in SURFACES and
    // build/tests/test_ubuntu_pin_bump.py fails on any straggler, so
    // the two cannot drift apart silently.
    assert!(IMAGE.starts_with("docker.io/library/ubuntu@sha256:"));
}

#[test]
fn operation_plans_require_their_exact_confirmations() {
    assert_eq!(plan_operation("create", None, None).unwrap(), PlannedOp::Create);
    assert!(plan_operation("create", Some("x"), None).is_err());
    assert_eq!(
        plan_operation("delete", Some(NAME), None).unwrap(),
        PlannedOp::Delete
    );
    assert!(plan_operation("delete", Some("rowyn-ubuntu-dev"), None).is_err());
    assert!(plan_operation("delete", None, None).is_err());
    assert_eq!(
        plan_operation("remove:google-chrome-stable", Some("google-chrome-stable"), None)
            .unwrap(),
        PlannedOp::Remove("google-chrome-stable".into())
    );
    assert!(plan_operation("remove:google-chrome-stable", Some("other"), None).is_err());
    assert!(plan_operation("remove:bad name", Some("bad name"), None).is_err());
    assert!(plan_operation("remove:-rf", Some("-rf"), None).is_err());
    assert_eq!(
        plan_operation("install", Some("install:code"), Some("/home/u/Downloads/code.deb"))
            .unwrap(),
        PlannedOp::Install
    );
    assert!(plan_operation("install", None, None).is_err());
    assert!(plan_operation("install", None, Some("")).is_err());
    assert!(plan_operation("nuke", None, None).is_err());
}

#[test]
fn package_names_follow_debian_rules() {
    assert!(valid_package_name("google-chrome-stable"));
    assert!(valid_package_name("libre2-9"));
    assert!(valid_package_name("g++"));
    assert!(!valid_package_name(""));
    assert!(!valid_package_name("-rf"));
    assert!(!valid_package_name("Bad Name"));
    assert!(!valid_package_name("a/b"));
    assert!(!valid_package_name("a;b"));
}

#[test]
fn deb_files_must_live_under_home() {
    let dir = std::env::temp_dir().join(format!("rowyn-deb-test-{}", now_secs()));
    let home = dir.join("home");
    let outside = dir.join("outside");
    fs::create_dir_all(home.join("Downloads")).unwrap();
    fs::create_dir_all(&outside).unwrap();
    let inside = home.join("Downloads/app.deb");
    fs::write(&inside, b"!<arch>\n").unwrap();
    let elsewhere = outside.join("app.deb");
    fs::write(&elsewhere, b"!<arch>\n").unwrap();
    let not_deb = home.join("Downloads/app.zip");
    fs::write(&not_deb, b"x").unwrap();
    let home = home.canonicalize().unwrap();
    assert!(validate_deb_file(&home, &inside).is_ok());
    assert!(validate_deb_file(&home, &elsewhere).is_err());
    assert!(validate_deb_file(&home, &not_deb).is_err());
    assert!(validate_deb_file(&home, Path::new("-rf")).is_err());
    assert!(validate_deb_file(&home, &home.join("Downloads/missing.deb")).is_err());
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn registry_roundtrips_and_rejects_damage() {
    let dir = std::env::temp_dir().join(format!("rowyn-deb-registry-{}", now_secs()));
    fs::create_dir_all(&dir).unwrap();
    assert!(read_registry_in(&dir).unwrap().is_empty());
    let apps = vec![DebApp {
        package: "discord".into(),
        version: "0.0.124".into(),
        source: "discord.deb".into(),
        exported_apps: vec!["discord".into()],
        exported_bins: vec![],
        installed_at: 42,
    }];
    write_registry_in(&dir, &apps).unwrap();
    assert_eq!(read_registry_in(&dir).unwrap()[0].package, "discord");
    fs::write(registry_path_in(&dir), b"{not json").unwrap();
    assert!(read_registry_in(&dir).is_err());
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn inspect_output_parses_the_tools_real_shape() {
    // Verbatim shape of rowyn-deb-inspect's JSON for the discord
    // fixture (build/tests/fixtures/deb), trimmed of nothing.
    let body = br#"{
      "package": "discord",
      "version": "0.0.124",
      "architecture": "amd64",
      "maintainer": "Discord Inc. <linux@discord.com>",
      "description": "All-in-one voice and text chat",
      "installed_size_kib": 189345,
      "file_size_bytes": 95000000,
      "sha256": "00",
      "signature": "none",
      "verdict": "user-app",
      "reasons": [],
      "notes": [{"signal": "host-device-rules", "text": "note"}],
      "desktop_files": ["/usr/share/applications/discord.desktop"],
      "binaries": [],
      "flatpak_hint": {"label": "Discord", "id": "com.discordapp.Discord"}
    }"#;
    let report = parse_inspect_output(body).unwrap();
    assert_eq!(report.package, "discord");
    assert_eq!(report.verdict, "user-app");
    assert_eq!(report.flatpak_hint.unwrap().id, "com.discordapp.Discord");
    assert!(parse_inspect_output(b"{}").is_err());
    assert!(parse_inspect_output(b"not json").is_err());
}

#[test]
fn export_commands_target_apps_bins_and_deletion() {
    assert_eq!(
        export_command(Some("discord"), None, false),
        vec!["distrobox-export", "--app", "discord"]
    );
    assert_eq!(
        export_command(Some("discord"), None, true),
        vec!["distrobox-export", "--app", "discord", "--delete"]
    );
    assert_eq!(
        export_command(None, Some("/usr/bin/tool"), true),
        vec!["distrobox-export", "--bin", "/usr/bin/tool", "--delete"]
    );
}

#[test]
fn controlled_child_lifecycle_reports_success_failure_and_cancellation() {
    use std::sync::atomic::AtomicBool;
    let flag = || Arc::new(AtomicBool::new(false));
    let mut success = Command::new("/bin/sh");
    success.args(["-c", "exit 0"]);
    assert!(child(success, flag(), Duration::from_secs(2)).is_ok());
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
}

#[test]
fn job_terminal_transitions_and_unknown_job_errors() {
    let key = format!("test-{}", id());
    let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    jobs().lock().unwrap().0.insert(
        key.clone(),
        Entry {
            job: DebJob {
                id: key.clone(),
                status: "running".into(),
                detail: "started".into(),
            },
            cancel: Some(flag),
        },
    );
    finish(&key, Ok("done".into()));
    let registry = jobs().lock().unwrap();
    let entry = registry.0.get(&key).unwrap();
    assert_eq!(entry.job.status, "succeeded");
    assert_eq!(entry.job.detail, "done");
}
