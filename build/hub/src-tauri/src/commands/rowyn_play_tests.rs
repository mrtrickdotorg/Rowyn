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
fn kwin_vrr_policy_reads_only_the_wayland_section() {
    let kwinrc = "[Compositing]\nVrrPolicy=2\n[Wayland]\nVrrPolicy=1\n";
    assert_eq!(kwin_vrr_policy(kwinrc).as_deref(), Some("Automatic (fullscreen applications)"));
    assert_eq!(kwin_vrr_policy("[Wayland]\nVrrPolicy=0\n").as_deref(), Some("Never"));
    assert_eq!(kwin_vrr_policy("").as_deref(), None);
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
