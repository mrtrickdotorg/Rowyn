use super::*;

const DIGEST: &str = "sha256:00000000000000000000000000000000000000000000000000000000000000aa";

#[test]
fn the_command_is_one_literal_allowlisted_argv() {
    assert_eq!(
        argv(),
        vec![
            "/usr/bin/sudo".to_string(),
            "-n".to_string(),
            "/usr/libexec/rowyn-boot-health".to_string(),
            "clear-quarantine".to_string(),
            "-".to_string(),
        ]
    );
}

#[test]
fn only_a_lowercase_sha256_digest_is_accepted() {
    assert_eq!(digest_argument(DIGEST).unwrap(), DIGEST);
    let long = format!("sha256:{}", "a".repeat(65));
    let short = format!("sha256:{}", "a".repeat(63));
    for bad in [
        "",
        "-",
        "--wipe-slot=all",
        "sha256:",
        "SHA256:00000000000000000000000000000000000000000000000000000000000000aa",
        "sha256:00000000000000000000000000000000000000000000000000000000000000AA",
        "sha256:000000000000000000000000000000000000000000000000000000000000000g",
        "sha256:00000000000000000000000000000000000000000000000000000000000000aa\n",
        " sha256:00000000000000000000000000000000000000000000000000000000000000aa",
        long.as_str(),
        short.as_str(),
    ] {
        assert!(digest_argument(bad).is_err(), "{bad:?} must be refused");
    }
}

#[test]
fn clearing_requires_the_explicit_confirmation() {
    assert!(require_confirmation(false).is_err());
    assert!(require_confirmation(true).is_ok());
    assert!(require_confirmation(false)
        .unwrap_err()
        .contains("explicit confirmation"));
}

#[test]
fn the_record_must_be_readable_json_with_a_quarantine_map() {
    let record = r#"{"schema_version":1,"known_good":{},"failures":{},"quarantined":{"sha256:aa":{"reason":"manual"}},"rollback":{},"last_boot":{}}"#;
    assert_eq!(quarantined(record).unwrap(), vec!["sha256:aa".to_string()]);
    for bad in [
        "not json",
        "[]",
        r#"{"schema_version":1}"#,
        r#"{"schema_version":1,"quarantined":[]}"#,
    ] {
        assert!(quarantined(bad).is_err(), "{bad:?} must be refused");
    }
}

#[test]
fn a_clean_record_has_no_digest_to_clear() {
    let record = r#"{"schema_version":1,"known_good":{},"failures":{},"quarantined":{},"rollback":{},"last_boot":{}}"#;
    assert!(quarantined(record).unwrap().is_empty());
    assert!(refusal(DIGEST).contains("not quarantined now"));
    assert!(refusal(DIGEST).contains("nothing was changed"));
}

#[test]
fn the_helper_reads_the_digest_from_stdin_not_argv() {
    let source = include_str!("rowyn_boot_health_actions.rs");
    for forbidden in [
        "pkexec",
        "bash",
        "sh -c",
        "Command::new(program)",
        "stdin(Stdio::inherit",
        "clear-quarantine\", digest",
    ] {
        assert!(
            !source.contains(forbidden),
            "bridge must not contain {forbidden:?}"
        );
    }
    // The digest is written to stdin, never interpolated into the command.
    assert!(source.contains("run_bounded_with_input(&argv(), input.as_bytes()"));
    assert!(!source.contains("argv().push"));
}

#[test]
fn the_first_line_helper_is_bounded() {
    assert_eq!(first_line(b"\n\n  permission denied\nmore\n"), "permission denied");
    assert_eq!(first_line(b""), "");
    assert_eq!(first_line(&vec![b'x'; 4096]).len(), 200);
}
