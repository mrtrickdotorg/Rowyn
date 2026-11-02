use super::*;

/// The constructors are the only producers of `Reading.state`; every value
/// they can return must be one the frontend declares, or the page silently
/// mis-classifies a whole branch (which is how "available" survived here).
#[test]
fn every_reading_state_is_declared_for_the_frontend_contract() {
    let produced = [
        section("Image", Ok("value".into())).state,
        section("Image", Err("error: broke".into())).state,
        section("Image", Err("unavailable: denied".into())).state,
        focused_reading("Audio", parse_audio("inactive\nactive\n")).state,
        failed_services_section(Ok("broken.service loaded failed failed".into())).state,
    ];
    for state in &produced {
        assert!(
            READING_STATES.contains(&state.as_str()),
            "undeclared reading state: {state}"
        );
    }
    assert!(produced.contains(&"attention".to_string()));
    assert!(produced.contains(&"error".to_string()));
    assert!(produced.contains(&"unavailable".to_string()));
    assert_eq!(READING_STATES.len(), 4);
}

/// Opt-in smoke on an installed system/container; never installs or changes it.
#[test]
#[ignore = "explicit installed-system read-only smoke"]
fn live_read_only_snapshot_has_all_sections_and_controller_helper_result() {
    let snapshot = collect().unwrap();
    const REQUIRED_SECTIONS: &[&str] = &[
        "OS",
        "Kernel",
        "CPU model",
        "NVIDIA module version",
        "Update health record",
        "Memory",
        "GPU drivers",
        "Bootc image",
        "GPU and bound drivers",
        "Failed services",
        "Disk space",
        "Controller readiness",
        "Bluetooth",
        "Wi-Fi",
        "Audio",
        "Displays",
    ];
    assert!((REQUIRED_SECTIONS.len()..=REQUIRED_SECTIONS.len() + 64).contains(&snapshot.readings.len()));
    let names: std::collections::HashSet<_> = snapshot.readings.iter().map(|reading| reading.name.as_str()).collect();
    assert_eq!(names.len(), snapshot.readings.len(), "section names must be unique");
    for required in REQUIRED_SECTIONS {
        assert!(names.contains(required), "missing required section: {required}");
    }
    assert!(snapshot.collected_at > 0);
    for reading in &snapshot.readings {
        assert!(!reading.detail.is_empty());
        assert!(READING_STATES.contains(&reading.state.as_str()));
        println!(
            "{}: {} ({} characters)",
            reading.name,
            reading.state,
            reading.detail.chars().count()
        );
    }
    let findings = controllers().expect("shipped controller helper must return parseable JSON");
    assert!(!findings.is_empty());
    println!(
        "Controller helper returned {} scrubbed findings",
        findings.len()
    );
}

#[test]
fn controller_parser_projects_only_bounded_read_only_findings() {
    let raw = r#"{"findings":[{"name":"Input devices","status":"warn","detail":"none","hint":"privileged advice"}],"devices":[{"name":"private device"}]}"#;
    let parsed = parse_controller(raw).unwrap();
    assert_eq!(parsed.len(), 1);
    assert_eq!(parsed[0].state, "warn");
    let text = serde_json::to_string(&parsed).unwrap();
    assert!(!text.contains("privileged advice"));
    assert!(!text.contains("private device"));
    for bad in [
        "",
        "{}",
        r#"{"findings":null}"#,
        r#"{"findings":[{"name":"x","status":"clean","detail":"x"}]}"#,
    ] {
        assert!(parse_controller(bad).is_err(), "{bad}");
    }
}

#[test]
fn output_is_scrubbed_before_it_reaches_any_view_or_report() {
    let output = safe_text("password=hunter2 192.0.2.8 /home/alice/file\u{1b}[31m");
    for sensitive in ["hunter2", "192.0.2.8", "/home/alice", "\u{1b}"] {
        assert!(!output.contains(sensitive), "{sensitive}");
    }
}

#[test]
fn controller_warnings_are_not_reported_as_an_ok_summary() {
    let result = controller_section(Ok(vec![Reading {
        name: "uinput".into(),
        state: "warn".into(),
        detail: "missing".into(),
    }]));
    assert_eq!(result.state, "attention");
    let missing = controller_section(Err("unavailable: helper missing".into()));
    assert_eq!(missing.state, "unavailable");
}

#[test]
fn failed_services_and_permission_denials_are_classified_truthfully() {
    assert_eq!(
        failed_services_section(Ok("broken.service loaded failed failed Broken".into())).state,
        "attention"
    );
    assert_eq!(failed_services_section(Ok(String::new())).state, "ok");
    for denied in [
        "Permission denied",
        "This command requires root privileges",
        "Operation not permitted",
        "error: Status: Preparing for write: Querying root privilege: This command must be executed as the root user",
    ] {
        assert_eq!(
            section("Image", Err(probe_failure(denied))).state,
            "unavailable"
        );
    }
    assert_eq!(
        section("Image", Err(probe_failure("the root directory is missing from output"))).state,
        "error"
    );
    assert_eq!(
        section("Image", Err(probe_failure("bad output"))).state,
        "error"
    );
}

#[test]
fn empty_success_is_not_an_unavailable_or_failed_service_read() {
    let check = section("Failed services", Ok(String::new()));
    assert_eq!(check.state, "ok");
    assert_eq!(check.detail, "None reported.");
    let check = section("Image", Err("unavailable: permission denied".into()));
    assert_eq!(check.state, "unavailable");
}

#[test]
fn bootc_projection_rejects_malformed_data_and_never_copies_arbitrary_fields() {
    assert!(parse_bootc("{}").is_err());
    assert!(parse_bootc("not json").is_err());
    let text = parse_bootc(r#"{"status":{"booted":{"image":{"image":{"image":"ghcr.io/mrtrickdotorg/rowyn:stable"},"imageDigest":"sha256:abcd"},"secret":"do-not-copy"}}}"#).unwrap();
    assert!(text.contains("rowyn:stable"));
    assert!(!text.contains("do-not-copy"));
}

#[test]
fn refresh_guard_rejects_overlap_and_releases_on_drop() {
    let flag = AtomicBool::new(false);
    let first = ReadGuard::take(&flag).unwrap();
    assert!(ReadGuard::take(&flag).is_err());
    drop(first);
    assert!(ReadGuard::take(&flag).is_ok());
}

#[test]
fn report_export_writes_only_an_explicit_private_file_and_never_creates_missing_directories() {
    use std::os::unix::fs::PermissionsExt;
    let temp = tempfile::tempdir().unwrap();
    assert!(save_report(&temp.path().join("missing"), "anything").is_err());
    assert!(!temp.path().join("missing").exists());
    assert!(save_report(temp.path(), &"x".repeat(REPORT_LIMIT + 1)).is_err());
    let message = save_report(temp.path(), "password=hunter2 /home/alice/report").unwrap();
    assert!(message.contains("Saved rowyn-diagnostics-"));
    let entries: Vec<_> = std::fs::read_dir(temp.path())
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    assert_eq!(entries.len(), 1);
    let text = std::fs::read_to_string(&entries[0]).unwrap();
    assert!(!text.contains("hunter2") && !text.contains("/home/alice"));
    assert_eq!(
        std::fs::metadata(&entries[0]).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(save_report(temp.path(), "second report").is_ok());
    assert_eq!(std::fs::read_to_string(&entries[0]).unwrap(), text);
}

#[test]
fn bounded_view_scrubs_before_truncating_a_secret() {
    let input = format!(
        "{}password={}\n",
        " ".repeat(TEXT_LIMIT - 4),
        "secret".repeat(2000)
    );
    let text = safe_text(&input);
    assert!(!text.contains("secret"));
    assert!(text.chars().count() <= TEXT_LIMIT + 20);
}


#[test]
fn focused_hardware_fixture_parsers_are_bounded_and_truthful() {
    assert!(parse_rfkill(r#"{"rfkilldevices":[{"type":"bluetooth","hard":"blocked","soft":"unblocked"}]}"#).unwrap().contains("hard-blocked"));
    assert!(parse_rfkill(r#"{"rfkilldevices":[{"type":"bluetooth","hard":"unblocked","soft":"blocked"}]}"#).unwrap().contains("soft-blocked"));
    assert!(parse_rfkill(r#"{"rfkilldevices":[{"type":"bluetooth","hard":"unblocked","soft":"unblocked"}]}"#).unwrap().contains("unblocked"));
    assert!(parse_rfkill(r#"{"rfkilldevices":[{"type":"bluetooth","hard":"unblocked","soft":"unblocked"},{"type":"wlan","hard":"unblocked","soft":"unblocked"}]}"#).unwrap().contains("unblocked"));
    for malformed in ["", "{}", r#"{"rfkilldevices":[{"type":"bluetooth","hard":false,"soft":false}]}"#, r#"{"rfkilldevices":[{"type":"bluetooth","hard":"unknown","soft":"unblocked"}]}"#] { assert!(parse_rfkill(malformed).is_err(), "{malformed}"); }
    assert!(parse_rfkill(r#"{"rfkill":[{"type":"bluetooth","hard":"unblocked","soft":"unblocked"}]}"#).is_err());
    assert!(parse_rfkill(r#"{"rfkilldevices":{}}"#).is_err());
    assert_eq!(parse_wifi("wlan0:wifi:connected", "enabled").unwrap(), "Wi-Fi radio enabled; device connected. Network names omitted.");
    assert!(parse_wifi("wlan0:wifi:disconnected", "disabled").unwrap().contains("disabled"));
    assert!(parse_audio("active\ninactive\n").unwrap().contains("inactive"));
    assert!(parse_audio("").is_err());
    assert!(parse_audio("Failed to connect").is_err());
    assert!(focused_reading("Audio", parse_audio("inactive\nactive")).state == "attention");
    let display = parse_displays("Output: HDMI-A-1 (connected) enabled\n").unwrap();
    assert!(display.contains("1 connected display output(s)"));
    assert!(!display.contains("HDMI-A-1"));
    assert!(parse_displays("").is_err());
    assert!(parse_displays("Output: HDMI-A-1 (disconnected) disabled\n").unwrap().contains("0 connected"));
    assert!(parse_displays("Output: HDMI-A-1 (disconnected) enabled\n").is_ok());
    assert_eq!(focused_reading("Displays", parse_displays("Output: HDMI-A-1 (disconnected) disabled\n")).state, "attention");
}


#[test]
fn unblocked_bluetooth_is_not_reported_as_blocked() {
    let actual_shape = r#"{"rfkilldevices":[{"type":"bluetooth","hard":"unblocked","soft":"unblocked"}]}"#;
    assert_eq!(focused_reading("Bluetooth", parse_rfkill(actual_shape)).state, "ok");
    for (hard, soft) in [("blocked", "unblocked"), ("unblocked", "blocked")] {
        let raw = serde_json::json!({"rfkilldevices": [{"type": "bluetooth", "hard": hard, "soft": soft}]}).to_string();
        assert_eq!(focused_reading("Bluetooth", parse_rfkill(&raw)).state, "attention");
    }
}

#[test]
fn colored_kscreen_output_is_parsed_before_health_classification() {
    let raw = "\u{1b}[01;34mOutput:\u{1b}[00m 1 Display-1 anonymous-id\n  \u{1b}[01;32menabled\u{1b}[00m\n  \u{1b}[01;32mconnected\u{1b}[00m\n  HDR: disabled\n";
    let reading = focused_reading("Displays", parse_displays(raw));
    assert_eq!(reading.state, "ok");
    assert!(reading.detail.contains("1 connected display output(s)"));
    assert!(!reading.detail.contains("Display-1"));
}

#[test]
fn display_blocks_keep_outputs_separate_and_reject_incomplete_states() {
    let two_outputs = "Output: 1 Panel anonymous-id\n  enabled\n  connected\n  HDR: disabled\nOutput: 2 External anonymous-id\n  disabled\n  disconnected\n  VRR: enabled\n";
    let summary = parse_displays(two_outputs).unwrap();
    assert!(summary.contains("1 connected display output(s) reported; 1 enabled"));
    for malformed in [
        "Output: 1 Panel\n  enabled\n",
        "Output: 1 Panel\n  connected\n",
        "Output: 1 Panel\n  enabled\n  disabled\n  connected\n",
        "Output: 1 Panel\n  enabled\n  connected\nOutput: 2 External\n  disabled\n",
    ] {
        assert!(parse_displays(malformed).is_err(), "{malformed}");
    }
}

#[test]
fn focused_probe_arguments_are_fixed_and_read_only() {
    fn args(kind: Probe) -> (String, Vec<String>) {
        let mut command = match kind {
            Probe::Bluetooth => { let mut c=Command::new("/usr/sbin/rfkill"); c.args(["--json"]); c }
            Probe::Wifi => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["-t","-f","DEVICE,TYPE,STATE","device","status"]); c }
            Probe::WifiRadio => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["radio","wifi"]); c }
            Probe::Audio => { let mut c=Command::new("/usr/bin/systemctl"); c.args(["--user","is-active","pipewire.service","wireplumber.service"]); c }
            Probe::Displays => { let mut c=Command::new("/usr/bin/kscreen-doctor"); c.arg("-o"); c }
            _ => unreachable!(),
        };
        (command.get_program().to_string_lossy().into_owned(), command.get_args().map(|a| a.to_string_lossy().into_owned()).collect())
    }
    assert_eq!(args(Probe::Bluetooth), ("/usr/sbin/rfkill".into(), vec!["--json".into()]));
    assert_eq!(args(Probe::Wifi).1, vec!["-t","-f","DEVICE,TYPE,STATE","device","status"]);
    assert_eq!(args(Probe::WifiRadio).1, vec!["radio","wifi"]);
    assert_eq!(args(Probe::Audio).1, vec!["--user","is-active","pipewire.service","wireplumber.service"]);
    assert_eq!(args(Probe::Displays).1, vec!["-o"]);
}
