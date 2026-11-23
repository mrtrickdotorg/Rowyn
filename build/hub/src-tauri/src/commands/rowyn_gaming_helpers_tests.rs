use super::*;

#[test]
fn a_clean_root_is_default_and_names_the_missing_helpers() {
    let temp = tempfile::tempdir().unwrap();
    let status = gaming_helpers_at(temp.path());
    assert_eq!(status.tune, "default");
    assert!(!status.installed);
    assert!(status.detail.contains("off"), "{}", status.detail);
    for missing in [TUNE_BIN, GAME_LAUNCH_BIN, MIMALLOC_BIN, GAMING_SLICE] {
        assert!(status.detail.contains(missing), "{}", status.detail);
    }
}

#[test]
fn the_sysctl_dropin_is_the_whole_on_off_state() {
    let temp = tempfile::tempdir().unwrap();
    let conf = temp.path().join("etc/sysctl.d/90-rowyn-gaming.conf");
    std::fs::create_dir_all(conf.parent().unwrap()).unwrap();
    std::fs::write(&conf, "vm.dirty_ratio = 5\n").unwrap();
    let on = gaming_helpers_at(temp.path());
    assert_eq!(on.tune, "gaming");
    assert!(on.detail.contains("on"), "{}", on.detail);
    std::fs::remove_file(&conf).unwrap();
    assert_eq!(gaming_helpers_at(temp.path()).tune, "default");
}

#[test]
fn every_shipped_helper_file_must_be_present() {
    let temp = tempfile::tempdir().unwrap();
    for path in [TUNE_BIN, GAME_LAUNCH_BIN, MIMALLOC_BIN] {
        let path = temp.path().join(path.trim_start_matches('/'));
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "#!/bin/sh\n").unwrap();
    }
    assert!(!gaming_helpers_at(temp.path()).installed);
    let slice = temp
        .path()
        .join(GAMING_SLICE.trim_start_matches('/'));
    std::fs::create_dir_all(slice.parent().unwrap()).unwrap();
    std::fs::write(&slice, "[Slice]\n").unwrap();
    let status = gaming_helpers_at(temp.path());
    assert!(status.installed);
    assert!(!status.detail.contains("missing"), "{}", status.detail);
}

#[test]
fn the_tune_switch_has_only_two_fixed_actions() {
    assert_eq!(tune_action(true), "on");
    assert_eq!(tune_action(false), "off");
}
