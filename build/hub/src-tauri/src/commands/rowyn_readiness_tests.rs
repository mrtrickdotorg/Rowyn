use super::*;
use crate::commands::rowyn_test_temp::compliant_temp_base;
fn probe(code: i32, text: &str) -> Option<ProbeOutput> {
    Some(ProbeOutput {
        code: Some(code),
        stdout: text.as_bytes().to_vec(),
    })
}
fn units(browsed: &str, legacy: &str) -> String {
    [
        ("cups.service", "active"),
        ("cups-browsed.service", browsed),
        ("systemd-journald.service", "active"),
        ("rsyslog.service", legacy),
        ("syslog.socket", "inactive"),
    ]
    .into_iter()
    .map(|(id, state)| {
        format!(
            "Id={id}\nLoadState=loaded\nActiveState={state}\nResult={}\n\n",
            if state == "failed" {
                "exit-code"
            } else {
                "success"
            }
        )
    })
    .collect()
}
fn no_backup() -> Result<super::super::rowyn_backups::BackupSnapshot, String> {
    Ok(super::super::rowyn_backups::BackupSnapshot {
        state: "none".into(),
        detail: "fixture".into(),
        plans: vec![],
    })
}
fn attempt(outcome: &str, reason: &str) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({"schema_version":1,"started_at":9990,"finished_at":9991,"outcome":outcome,"reason_code":reason,"last_success_at":if matches!(outcome,"current"|"staged"){9991}else{9000},"last_staged_at":null})).unwrap()
}
#[test]
fn injected_fixed_probes_do_not_hide_a_missing_identity() {
    let metadata = units("active", "inactive");
    let mut calls = vec![];
    let snapshot = snapshot_with(
        |exe, args| {
            calls.push((
                exe.to_string(),
                args.iter().map(|x| x.to_string()).collect::<Vec<_>>(),
            ));
            if exe == GETENT {
                probe(2, "")
            } else {
                assert_eq!(exe, SYSTEMCTL);
                probe(0, &metadata)
            }
        },
        no_backup(),
        update_from_bytes(&attempt("current", "no_update"), 10000),
        Some(true),
        10000,
    );
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].1, ["passwd", "cups-browsed"]);
    assert_eq!(calls[1].1, SERVICE_ARGS);
    assert_eq!(snapshot.checks[0].state, "attention");
    assert!(snapshot.checks[0].detail.contains("identity"));
    assert_eq!(snapshot.checks[1].state, "ready");
    assert_eq!(snapshot.checks[2].state, "ready");
    assert_eq!(snapshot.checks[3].state, "needs-setup");
    assert_eq!(snapshot.checks[4].state, "ready");
}
#[test]
fn failed_discovery_and_legacy_logging_stay_attention() {
    let metadata = units("failed", "failed");
    let snapshot = snapshot_with(
        |exe, _| {
            if exe == GETENT {
                probe(0, "cups-browsed:x:200:200:Service:/:/usr/sbin/nologin\n")
            } else {
                probe(0, &metadata)
            }
        },
        no_backup(),
        UpdateRead::Missing,
        Some(false),
        10000,
    );
    assert_eq!(snapshot.checks[0].state, "attention");
    assert_eq!(snapshot.checks[1].state, "attention");
    assert_eq!(snapshot.checks[2].state, "needs-setup");
    assert_eq!(snapshot.checks[4].state, "needs-setup");
}
#[test]
fn denied_timeout_and_malformed_reads_are_unavailable() {
    let snapshot = snapshot_with(
        |_, _| None,
        Err("private fixture path must not escape".into()),
        UpdateRead::Unsafe,
        None,
        10000,
    );
    for check in snapshot.checks {
        assert_eq!(check.state, "unavailable");
        assert!(!check.detail.contains("private fixture"));
    }
    let malformed = snapshot_with(
        |_, _| probe(0, "unrecognized output"),
        no_backup(),
        UpdateRead::Unsafe,
        None,
        10000,
    );
    assert_eq!(malformed.checks[0].state, "unavailable");
    assert_eq!(malformed.checks[1].state, "unavailable");
}
#[test]
fn actual_producer_reasons_and_history_are_projected_without_raw_output() {
    for (outcome, reason) in [("failed", "runtime_auth_io"), ("deferred", "battery")] {
        let read = update_from_bytes(&attempt(outcome, reason), 10000);
        let check = update_check(&read);
        assert_eq!(check.state, "attention");
        assert_eq!(check.update_attempt.unwrap().last_success_at, Some(9000));
    }
}
#[test]
fn inconsistent_success_bad_times_and_unknown_reasons_are_rejected() {
    let original: serde_json::Value =
        serde_json::from_slice(&attempt("current", "no_update")).unwrap();
    for (key, value) in [
        ("finished_at", serde_json::json!(true)),
        ("finished_at", serde_json::Value::Null),
        ("last_success_at", serde_json::json!(9000)),
        ("started_at", serde_json::json!(20000)),
        ("reason_code", serde_json::json!("battery")),
        ("schema_version", serde_json::json!(true)),
    ] {
        let mut bad = original.clone();
        bad[key] = value;
        assert!(
            matches!(
                update_from_bytes(&serde_json::to_vec(&bad).unwrap(), 10000),
                UpdateRead::Unsafe
            ),
            "{key}"
        );
    }
    assert!(matches!(
        update_from_bytes(&vec![b' '; 8193], 10000),
        UpdateRead::Unsafe
    ));
    assert!(matches!(
        update_from_bytes(b"bad-json", 10000),
        UpdateRead::Unsafe
    ));
}
#[test]
fn stale_attempts_do_not_look_freshly_successful() {
    let mut value: serde_json::Value =
        serde_json::from_slice(&attempt("current", "no_update")).unwrap();
    value["outcome"] = serde_json::json!("running");
    value["reason_code"] = serde_json::json!("attempt_started");
    value["started_at"] = serde_json::json!(1);
    value["finished_at"] = serde_json::Value::Null;
    value["last_success_at"] = serde_json::Value::Null;
    assert_eq!(
        update_check(&update_from_bytes(
            &serde_json::to_vec(&value).unwrap(),
            10000
        ))
        .state,
        "unavailable"
    );
    assert_ne!(
        update_check(&update_from_bytes(&attempt("current", "no_update"), 200000)).state,
        "ready"
    );
}
#[test]
fn complete_serialization_is_public_and_bounded() {
    let value = serde_json::to_value(snapshot_with(
        |_, _| None,
        no_backup(),
        UpdateRead::Missing,
        None,
        10000,
    ))
    .unwrap();
    assert_eq!(value["checkedAt"], 10000);
    let checks = value["checks"].as_array().unwrap();
    assert_eq!(checks.len(), 5);
    assert_eq!(
        checks
            .iter()
            .map(|c| c["id"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["printing", "logging", "updates", "backups", "development"]
    );
    for check in checks {
        let detail = check["detail"].as_str().unwrap();
        assert!(detail.len() <= 240);
        assert!(!detail.contains("/home/"));
        assert!(!detail.chars().any(char::is_control));
    }
}
#[test]
fn file_reader_refuses_symlinks_fifo_and_unsafe_permissions() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let Some(base) = compliant_temp_base() else {
        eprintln!(
            "skipping file_reader_refuses_symlinks_fifo_and_unsafe_permissions: \
             no temp directory satisfies the status reader's ownership policy"
        );
        return;
    };
    let root = base.join(format!(
        "rowyn-readiness-file-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |elapsed| elapsed.as_nanos())
    ));
    std::fs::create_dir(&root).unwrap();
    let file = root.join("status.json");
    std::fs::write(&file, attempt("current", "no_update")).unwrap();
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).unwrap();
    let uid = unsafe { libc::geteuid() };
    assert!(matches!(
        read_update_file(&file, 10000, uid),
        UpdateRead::Known(_)
    ));
    let link = root.join("link.json");
    symlink(&file, &link).unwrap();
    assert!(matches!(
        read_update_file(&link, 10000, uid),
        UpdateRead::Unsafe
    ));
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o666)).unwrap();
    assert!(matches!(
        read_update_file(&file, 10000, uid),
        UpdateRead::Unsafe
    ));
    let fifo = root.join("fifo");
    let name = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
    assert!(matches!(
        read_update_file(&fifo, 10000, uid),
        UpdateRead::Unsafe
    ));
    assert!(matches!(
        read_update_file(&root.join("missing"), 10000, uid),
        UpdateRead::Missing
    ));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn systemctl_newline_records_and_failed_result_are_respected() {
    let text = [
        "cups.service",
        "cups-browsed.service",
        "systemd-journald.service",
        "rsyslog.service",
        "syslog.socket",
    ]
    .into_iter()
    .map(|id| {
        format!(
            "Id={id}\nLoadState=loaded\nActiveState=active\nResult={}\n",
            if id == "cups-browsed.service" {
                "exit-code"
            } else {
                "success"
            }
        )
    })
    .collect::<String>();
    let parsed = parse_units(probe(0, &text));
    assert_eq!(
        parsed.len(),
        5,
        "systemctl records can be newline-delimited without blank lines"
    );
    let snapshot = snapshot_with(
        |exe, _| {
            if exe == GETENT {
                probe(0, "cups-browsed:x:200:200:Service:/:/usr/sbin/nologin\n")
            } else {
                probe(0, &text)
            }
        },
        no_backup(),
        UpdateRead::Missing,
        Some(false),
        10000,
    );
    assert_eq!(
        snapshot.checks[0].state, "attention",
        "active with failing Result is not ready"
    );
}
#[test]
fn failed_attempt_can_retain_success_after_attempt_started() {
    let mut value: serde_json::Value =
        serde_json::from_slice(&attempt("failed", "upgrade_failed")).unwrap();
    value["started_at"] = serde_json::json!(9900);
    value["finished_at"] = serde_json::json!(9991);
    value["last_success_at"] = serde_json::json!(9950);
    assert!(matches!(
        update_from_bytes(&serde_json::to_vec(&value).unwrap(), 10000),
        UpdateRead::Known(_)
    ));
}
