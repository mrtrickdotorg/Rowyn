use super::*;
use std::io::Write;
use std::os::unix::fs::MetadataExt;

const RECORD: &str = r#"{"schema_version":1,"known_good":{},"failures":{},"quarantined":{},"rollback":{},"last_boot":{}}"#;

fn temp_record(name: &str) -> std::path::PathBuf {
    let path = std::env::temp_dir().join(format!(
        "rowyn-boot-health-{}-{}",
        name,
        std::process::id()
    ));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&path)
        .expect("temp record");
    file.write_all(RECORD.as_bytes()).expect("write record");
    path
}

#[test]
fn accepts_a_minimal_schema_v1_record() {
    assert_eq!(validate(RECORD).unwrap(), RECORD);
}

#[test]
fn rejects_invalid_json_arrays_and_missing_schema() {
    assert!(validate("{").is_err());
    assert!(validate("[]").is_err());
    assert!(validate(r#"{"known_good":{}}"#).is_err());
    assert!(validate(r#"{"schema_version":2}"#).is_err());
    assert!(validate(r#"{"schema_version":"1"}"#).is_err());
}

#[test]
fn rejects_oversized_records() {
    let mut record = String::from(r#"{"schema_version":1,"padding":""#);
    record.push_str(&"x".repeat(LIMIT));
    record.push_str(r#""}"#);
    assert!(validate(&record).is_err());
}

#[test]
fn command_takes_no_arguments_and_reads_a_fixed_path() {
    assert_eq!(PATH, "/var/lib/rowyn/boot-health.json");
    assert!(LIMIT >= 64 * 1024, "the record must allow every state the helper writes");
}

#[test]
fn a_fixture_record_is_read_without_root_ownership() {
    let path = temp_record("fixture");
    let read = read_record(&path, false);
    std::fs::remove_file(&path).ok();
    assert_eq!(read.unwrap(), RECORD);
}

#[test]
fn the_real_path_still_requires_a_root_owned_safe_file() {
    let path = temp_record("owned-by-test");
    let owned_by_root = std::fs::metadata(&path).unwrap().uid() == 0;
    let read = read_record(&path, true);
    std::fs::remove_file(&path).ok();
    if !owned_by_root {
        assert!(
            read.is_err(),
            "a record the reader owns must not satisfy the shipping rule"
        );
    }
}

#[test]
fn a_missing_fixture_says_so_instead_of_blaming_the_system() {
    let missing = std::env::temp_dir().join(format!(
        "rowyn-boot-health-absent-{}",
        std::process::id()
    ));
    std::fs::remove_file(&missing).ok();
    let fixture = read_record(&missing, false).unwrap_err();
    let real = read_record(&missing, true).unwrap_err();
    assert!(fixture.contains("debug boot-health path override"), "{fixture}");
    assert!(real.contains("No boot-health record has been written"), "{real}");
}

#[test]
fn the_override_is_gated_to_debug_builds() {
    // Release compiles the lookup to a constant `None`, so the record can only
    // come from the fixed path there. Pin the gate, not a runtime value that
    // depends on the developer's environment.
    let source = include_str!("rowyn_boot_health.rs");
    assert!(source.contains("#[cfg(debug_assertions)]"));
    assert!(source.contains("#[cfg(not(debug_assertions))]"));
    assert!(source.contains("ROWYN_BOOT_HEALTH_PATH"));
}
