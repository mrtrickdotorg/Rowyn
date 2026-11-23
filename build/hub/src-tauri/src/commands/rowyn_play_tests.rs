use super::*;
#[test]
fn only_four_curated_launcher_ids_are_accepted() {
    for id in [
        "com.valvesoftware.Steam",
        "com.heroicgameslauncher.hgl",
        "net.lutris.Lutris",
        "com.usebottles.bottles",
    ] {
        assert_eq!(launcher_id(id).unwrap(), id);
    }
    for id in [
        "",
        "org.example.App",
        "--system",
        "com.valvesoftware.Steam;id",
        "../Steam",
    ] {
        assert!(launcher_id(id).is_err(), "{id}");
    }
}
#[test]
fn installed_scope_is_explicit_and_missing_scope_is_not_false_success() {
    assert_eq!(
        installed_scope(
            "com.valvesoftware.Steam",
            "com.valvesoftware.Steam\n",
            "",
            &[]
        )
        .unwrap(),
        "--user"
    );
    assert_eq!(
        installed_scope(
            "com.usebottles.bottles",
            "",
            "com.usebottles.bottles\n",
            &[]
        )
        .unwrap(),
        "--system"
    );
    assert!(
        installed_scope("com.usebottles.bottles", "", "", &["system"])
            .unwrap_err()
            .contains("unavailable")
    );
    assert!(installed_scope("com.usebottles.bottles", "", "", &[])
        .unwrap_err()
        .contains("not installed"));
    assert!(installed_scope(
        "com.valvesoftware.Steam",
        "com.valvesoftware.SteamBeta\n",
        "",
        &[]
    )
    .is_err());
}

#[test]
fn gamemode_daemon_prefers_a_running_unit_and_reads_not_found() {
    let idle = ("loaded".to_string(), "inactive".to_string());
    let running = ("loaded".to_string(), "active".to_string());
    let absent = ("not-found".to_string(), "inactive".to_string());
    let both = daemon_state_from(Some(idle.clone()), Some(running));
    assert!(both.installed && both.active);
    assert_eq!(both.scope, Some("system"));
    let user_only = daemon_state_from(Some(idle), None);
    assert!(user_only.installed && !user_only.active);
    assert_eq!(user_only.scope, Some("user"));
    let missing = daemon_state_from(None, Some(absent));
    assert!(!missing.installed);
    assert_eq!(missing.scope, None);
}

#[test]
fn unit_state_needs_both_load_and_active_fields() {
    assert_eq!(
        unit_state("LoadState=loaded\nActiveState=active\n"),
        Some(("loaded".to_string(), "active".to_string()))
    );
    assert_eq!(unit_state("ActiveState=active\n"), None);
    assert_eq!(unit_state(""), None);
}

#[test]
fn the_sandbox_client_is_read_from_the_deployed_app_files() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("flatpak");
    let id = "com.valvesoftware.Steam";
    assert!(!flatpak_gamemoderun_in(&[root.clone()], id));
    let bin = root.join("app").join(id).join("current/active/files/bin");
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::write(bin.join("gamemoderun"), b"#!/bin/sh\n").unwrap();
    assert!(flatpak_gamemoderun_in(&[root], id));
    // A directory of that name is not an executable.
    assert!(!flatpak_gamemoderun_in(&[temp.path().join("elsewhere")], id));
}

#[test]
fn gamemode_status_promises_only_what_both_pieces_support() {
    let installed = DaemonState { installed: true, active: false, scope: Some("user") };
    let absent = DaemonState { installed: false, active: false, scope: None };
    let ready = gamemode_status(DaemonState { installed: true, active: true, scope: Some("user") }, true);
    assert!(ready.available);
    assert!(ready.detail.contains("Per-process registration fails"));
    let no_client = gamemode_status(installed.clone(), false);
    assert!(!no_client.available);
    assert!(no_client.detail.contains("ships no gamemoderun"));
    let no_daemon = gamemode_status(absent, true);
    assert!(!no_daemon.available);
    assert!(no_daemon.detail.contains("nothing to activate"));
}

#[test]
fn gamescope_is_offered_only_when_the_binary_resolves() {
    let found = gamescope_status_from(Some(std::path::PathBuf::from("/usr/bin/gamescope")));
    assert!(found.available);
    assert!(!found.detail.contains("not in this session's PATH"));
    let missing = gamescope_status_from(None);
    assert!(!missing.available);
    assert!(missing.detail.contains("not in this session's PATH"));
    assert!(missing.detail.contains("Distrobox"));
}

#[test]
fn display_modes_parse_hdr_and_vrr_per_output() {
    let raw = "Output: 1 eDP-1 enabled connected primary laptop panel\n\tModes: 1:1920x1080@60.00*\n\tVrr: incapable\n\tHdr: incapable\nOutput: 2 HDMI-A-1 enabled connected\n\tModes: 1:3840x2160@60.00*\n\tVrr: Automatic\n\tHdr: enabled\nOutput: 3 DP-1 disabled disconnected\n";
    let outputs = parse_display_modes(raw);
    assert_eq!(outputs.len(), 3);
    assert_eq!(outputs[0].name, "eDP-1");
    assert!(outputs[0].connected);
    assert_eq!(outputs[0].vrr.as_deref(), Some("incapable"));
    assert_eq!(outputs[1].hdr.as_deref(), Some("enabled"));
    assert!(!outputs[2].connected);
    assert_eq!(outputs[2].hdr, None);
}

#[test]
fn display_vrr_is_taken_from_the_output_that_reports_it() {
    // Plasma 6 stores adaptive sync per output; two outputs can disagree, and
    // each value is reported as the display stack gives it — never merged into
    // one page-level policy.
    let raw = "Output: 1 DP-1 enabled connected\n\tVrr: Automatic\nOutput: 2 HDMI-A-1 enabled connected\n\tVrr: Never\n";
    let outputs = parse_display_modes(raw);
    assert_eq!(outputs[0].vrr.as_deref(), Some("Automatic"));
    assert_eq!(outputs[1].vrr.as_deref(), Some("Never"));
}

#[test]
fn layer_branches_and_steam_runtime_parse_flatpak_output() {
    let refs = "org.freedesktop.Platform.VulkanLayer.MangoHud/x86_64/24.08\norg.freedesktop.Platform.VulkanLayer.MangoHud/x86_64/23.08\norg.freedesktop.Platform.VulkanLayer.gamescope/x86_64/24.08\norg.freedesktop.Platform.VulkanLayer.vkBasalt/x86_64/24.08\ncom.valvesoftware.Steam/x86_64/stable\n";
    assert_eq!(layer_branches(refs, MANGOHUD_LAYER), vec!["23.08".to_string(), "24.08".to_string()]);
    assert_eq!(layer_branches(refs, GAMESCOPE_LAYER), vec!["24.08".to_string()]);
    assert_eq!(layer_branches(refs, VKBASALT_LAYER), vec!["24.08".to_string()]);
    let info = "ID: com.valvesoftware.Steam\nRuntime: org.freedesktop.Platform/x86_64/24.08\nSdk: org.freedesktop.Sdk/x86_64/24.08\n";
    assert_eq!(steam_runtime_branch(info).as_deref(), Some("24.08"));
    assert_eq!(steam_runtime_branch("no runtime here"), None);
}
