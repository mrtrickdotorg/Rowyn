use super::*;
use std::fs;
use std::path::Path;

fn manifest(app_id: u32, title: &str, flags: u32) -> String {
    format!(
        "\"AppState\" {{\n\t\"appid\" \"{app_id}\"\n\t\"name\" \"{title}\"\n\t\"StateFlags\" \"{flags}\"\n\t\"installdir\" \"ignored\"\n}}\n"
    )
}

fn write_game(library: &Path, app_id: u32, title: &str, flags: u32) {
    let steamapps = library.join("steamapps");
    fs::create_dir_all(&steamapps).unwrap();
    fs::write(
        steamapps.join(format!("appmanifest_{app_id}.acf")),
        manifest(app_id, title, flags),
    )
    .unwrap();
}

#[test]
fn scans_native_and_flatpak_libraries_without_exposing_paths() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let native = home.join(".local/share/Steam");
    let flatpak = home.join(".var/app/com.valvesoftware.Steam/.local/share/Steam");
    let external = home.join("mounted-games");
    fs::create_dir_all(native.join("steamapps")).unwrap();
    fs::create_dir_all(flatpak.join("steamapps")).unwrap();
    fs::create_dir_all(external.join("steamapps")).unwrap();
    fs::write(
        native.join("steamapps/libraryfolders.vdf"),
        format!(
            "\"libraryfolders\" {{\n\t\"0\" {{ \"path\" \"{}\" }}\n\t\"1\" {{ \"path\" \"{}\" }}\n}}",
            native.display(), external.display()
        ),
    )
    .unwrap();
    write_game(&native, 620, "Portal 2", 4);
    write_game(&external, 570, "Dota 2", 4);
    write_game(&flatpak, 440, "Team Fortress 2", 4);
    write_game(&native, 730, "Counter-Strike 2 (not installed)", 2);
    write_game(&native, 8230, "Game files are corrupt", 4 | 128);

    let result = scan_steam_libraries(home, false);
    assert!(result.available);
    assert_eq!(
        result
            .games
            .iter()
            .map(|game| game.app_id.as_str())
            .collect::<Vec<_>>(),
        vec!["440", "570", "620"]
    );
    assert_eq!(result.games[2].name, "Portal 2");
    let json = serde_json::to_string(&result).unwrap();
    assert!(!json.contains(&home.to_string_lossy().to_string()));
    assert!(!json.contains("mounted-games"));
}

#[test]
fn missing_steam_install_is_unavailable_not_an_empty_library() {
    let temporary = tempfile::tempdir().unwrap();
    let result = scan_steam_libraries(temporary.path(), false);
    assert!(!result.available);
    assert!(result.games.is_empty());
}

#[test]
fn malformed_library_file_is_visible_as_partial_not_a_clean_empty_scan() {
    let temporary = tempfile::tempdir().unwrap();
    let steam = temporary.path().join(".local/share/Steam");
    fs::create_dir_all(steam.join("steamapps")).unwrap();
    fs::write(
        steam.join("steamapps/libraryfolders.vdf"),
        "not valid { \"path\"",
    )
    .unwrap();
    let result = scan_steam_libraries(temporary.path(), false);
    assert!(result.available);
    assert!(result.warnings > 0);
}

const JPEG: [u8; 8] = [0xFF, 0xD8, 0xFF, 0xE0, 0x01, 0x02, 0x03, 0x04];

#[test]
fn library_scan_attaches_steams_cached_cover_art_locally() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    write_game(&steam, 620, "Portal 2", 4);
    write_game(&steam, 570, "Dota 2", 4);
    let cache = steam.join("appcache/librarycache");
    // Modern layout: one content-addressed directory per artwork variant.
    fs::create_dir_all(cache.join("620/abcdef")).unwrap();
    fs::write(cache.join("620/abcdef/library_header.jpg"), JPEG).unwrap();
    fs::write(cache.join("620/abcdef/library_600x900.jpg"), JPEG).unwrap();
    // A file that is not a JPEG is not embedded as a broken image.
    fs::create_dir_all(cache.join("570/abcdef")).unwrap();
    fs::write(cache.join("570/abcdef/library_600x900.jpg"), b"<html>").unwrap();

    let scan = scan_steam_libraries(home, true);
    let portal = scan.games.iter().find(|game| game.app_id == "620").unwrap();
    let cover = portal.cover.as_deref().unwrap();
    assert!(cover.starts_with("data:image/jpeg;base64,"));
    // 8 bytes encode to 12 base64 characters, with no padding left over.
    assert_eq!(cover.trim_start_matches("data:image/jpeg;base64,").len(), 12);
    assert_eq!(scan.games.iter().find(|game| game.app_id == "570").unwrap().cover, None);
    assert!(scan.warnings > 0);
    // The name-only path (recent play) never reads image bytes.
    let names = scan_steam_libraries(home, false);
    assert!(names.games.iter().all(|game| game.cover.is_none()));
    let json = serde_json::to_string(&scan).unwrap();
    assert!(!json.contains(&home.to_string_lossy().to_string()));
    assert!(!json.contains("librarycache"));
}

#[test]
fn legacy_flat_cover_path_is_read_and_oversized_art_is_refused() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    write_game(&steam, 440, "Team Fortress 2", 4);
    write_game(&steam, 620, "Portal 2", 4);
    let cache = steam.join("appcache/librarycache");
    fs::create_dir_all(&cache).unwrap();
    // Older clients wrote the artwork beside the per-app directories.
    fs::write(cache.join("440_library_600x900.jpg"), JPEG).unwrap();
    fs::create_dir_all(cache.join("620/abcdef")).unwrap();
    let mut oversized = vec![0xFFu8, 0xD8, 0xFF];
    oversized.resize(MAX_COVER_BYTES as usize + 1, 0);
    fs::write(cache.join("620/abcdef/library_600x900.jpg"), oversized).unwrap();

    let scan = scan_steam_libraries(home, true);
    assert!(scan
        .games
        .iter()
        .find(|game| game.app_id == "440")
        .unwrap()
        .cover
        .is_some());
    assert_eq!(
        scan.games.iter().find(|game| game.app_id == "620").unwrap().cover,
        None
    );
    assert!(scan.warnings > 0);
}

#[test]
fn base64_matches_the_standard_alphabet_and_padding() {
    assert_eq!(base64(b""), "");
    assert_eq!(base64(b"M"), "TQ==");
    assert_eq!(base64(b"Ma"), "TWE=");
    assert_eq!(base64(b"Man"), "TWFu");
    assert_eq!(base64(b"any carnal pleasure."), "YW55IGNhcm5hbCBwbGVhc3VyZS4=");
    assert_eq!(base64(&JPEG), "/9j/4AECAwQ=");
}

const LOCAL_CONFIG_APPS: &str = r#""UserLocalConfigStore"
{
	"Software"
	{
		"Valve"
		{
			"Steam"
			{
				"apps"
				{
					"620"
					{
						"LastPlayed"		"1762300000"
						"Playtime"		"1830"
					}
					"570"
					{
						"LastPlayed"		"1762400000"
						"playtime_forever"	"120"
					}
					"440"
					{
						"LastPlayed"		"1762200000"
					}
					"400"
					{
						"LastPlayed"		"1762500000"
					}
					"0"
					{
						"LastPlayed"		"1762600000"
					}
					"not-an-id"
					{
						"LastPlayed"		"1762700000"
					}
				}
			}
		}
	}
}
"#;

fn write_profile(home: &Path, account: &str, content: &str) {
    let config = home
        .join(".local/share/Steam/userdata")
        .join(account)
        .join("config");
    fs::create_dir_all(&config).unwrap();
    fs::write(config.join("localconfig.vdf"), content).unwrap();
}

fn local_config(entries: &[(&str, u64, Option<u64>)]) -> String {
    let mut out = String::from(
        "\"UserLocalConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"apps\"\n\t\t\t\t{\n",
    );
    for (app_id, last_played, playtime) in entries {
        out.push_str(&format!(
            "\t\t\t\t\t\"{app_id}\"\n\t\t\t\t\t{{\n\t\t\t\t\t\t\"LastPlayed\"\t\t\"{last_played}\"\n"
        ));
        if let Some(minutes) = playtime {
            out.push_str(&format!(
                "\t\t\t\t\t\t\"Playtime\"\t\t\"{minutes}\"\n"
            ));
        }
        out.push_str("\t\t\t\t\t}\n");
    }
    out.push_str("\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n");
    out
}

#[test]
fn recent_play_reports_steam_values_newest_first_without_inventing_anything() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    write_game(&steam, 570, "Dota 2", 4);
    write_game(&steam, 620, "Portal 2", 4);
    write_game(&steam, 440, "Team Fortress 2", 4);
    write_profile(home, "12345678", LOCAL_CONFIG_APPS);

    let result = scan_recent_play(home);
    assert!(result.steam_detected);
    assert!(result.profile_read);
    assert_eq!(result.warnings, 0);
    // 400 is played but not installed here, so Steam has no local name for it.
    assert_eq!(result.unnamed, 1);
    assert_eq!(
        result
            .games
            .iter()
            .map(|game| game.app_id.as_str())
            .collect::<Vec<_>>(),
        vec!["570", "620", "440"]
    );
    assert_eq!(result.games[0].name, "Dota 2");
    assert_eq!(result.games[0].playtime_minutes, Some(120));
    assert_eq!(result.games[0].last_played, 1_762_400_000);
    assert_eq!(result.games[1].name, "Portal 2");
    assert_eq!(result.games[1].playtime_minutes, Some(1830));
    // A missing counter is reported as missing, never as zero minutes.
    assert_eq!(result.games[2].playtime_minutes, None);
    let json = serde_json::to_string(&result).unwrap();
    assert!(!json.contains(&home.to_string_lossy().to_string()));
    assert!(!json.contains("12345678"));
}

#[test]
fn recent_play_merges_accounts_and_reports_a_missing_profile() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    fs::create_dir_all(steam.join("steamapps")).unwrap();
    write_game(&steam, 620, "Portal 2", 4);

    // Steam found, but no account has run for this user yet.
    let empty = scan_recent_play(home);
    assert!(empty.steam_detected);
    assert!(!empty.profile_read);
    assert!(empty.games.is_empty());

    write_profile(home, "111", &local_config(&[("620", 1_762_300_000, Some(10))]));
    write_profile(home, "222", &local_config(&[("620", 1_762_400_000, Some(30))]));
    let merged = scan_recent_play(home);
    assert_eq!(merged.games.len(), 1);
    assert_eq!(merged.games[0].last_played, 1_762_400_000);
    assert_eq!(merged.games[0].playtime_minutes, Some(30));
}

#[test]
fn recent_play_without_steam_is_not_detected_and_an_unreadable_profile_is_partial() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let missing = scan_recent_play(home);
    assert!(!missing.steam_detected);
    assert!(!missing.profile_read);

    let steam = home.join(".local/share/Steam");
    fs::create_dir_all(steam.join("steamapps")).unwrap();
    write_profile(home, "123", "not a vdf {");
    let partial = scan_recent_play(home);
    assert!(partial.steam_detected);
    assert!(!partial.profile_read);
    assert!(partial.warnings > 0);
    assert!(partial.games.is_empty());
}

#[test]
fn shader_cache_reports_steams_flag_and_never_invents_one() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    // No Steam at all: no configuration to read, and no claim about it.
    let missing = read_shader_cache(home);
    assert!(!missing.steam_detected);
    assert!(!missing.config_read);
    assert_eq!(missing.pre_caching, None);

    fs::create_dir_all(home.join(".local/share/Steam/steamapps")).unwrap();
    // Steam is installed but has not written its global configuration yet.
    let empty = read_shader_cache(home);
    assert!(empty.steam_detected);
    assert!(!empty.config_read);
    assert_eq!(empty.pre_caching, None);
    assert_eq!(empty.warnings, 0);

    write_global_config(home, &global_config(Some("0")));
    let on = read_shader_cache(home);
    assert!(on.config_read);
    assert_eq!(on.pre_caching, Some(true));
    assert_eq!(on.warnings, 0);

    write_global_config(home, &global_config(Some("1")));
    assert_eq!(read_shader_cache(home).pre_caching, Some(false));

    // Steam writes the flag for a changed setting; an absent flag is reported
    // as unrecorded (its default applies), never as on or off.
    write_global_config(home, &global_config(None));
    let unset = read_shader_cache(home);
    assert!(unset.config_read);
    assert_eq!(unset.pre_caching, None);
    assert_eq!(unset.warnings, 0);
}

#[test]
fn shader_cache_keeps_junk_and_unreadable_config_visible() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    fs::create_dir_all(home.join(".local/share/Steam/steamapps")).unwrap();

    // A value Steam never writes is not read as on or off.
    write_global_config(home, &global_config(Some("2")));
    let junk = read_shader_cache(home);
    assert!(junk.config_read);
    assert_eq!(junk.pre_caching, None);
    assert!(junk.warnings > 0);

    write_global_config(home, "not a vdf {");
    let broken = read_shader_cache(home);
    assert!(!broken.config_read);
    assert_eq!(broken.pre_caching, None);
    assert!(broken.warnings > 0);
    // A read leaves the file byte-identical: no backup, temp file or rewrite.
    assert_eq!(
        fs::read_to_string(home.join(".local/share/Steam/config/config.vdf")).unwrap(),
        "not a vdf {"
    );
    assert!(!home.join(".local/share/Steam/config/config.vdf.rowyn-backup").exists());
}

#[test]
fn mangohud_config_reads_the_flatpak_path_without_writing() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let missing = read_mangohud_config_at(home, None);
    assert!(!missing.steam_detected);
    assert!(!missing.flatpak_steam);
    assert!(!missing.exists);
    assert_eq!(missing.settings, 0);
    assert!(!missing.native_exists);
    assert!(!missing.native_readable);
    assert_eq!(missing.native_settings, 0);
    assert!(missing
        .path
        .ends_with(".var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf"));
    assert_eq!(missing.native_path, "~/.config/MangoHud/MangoHud.conf");

    fs::create_dir_all(home.join(".var/app/com.valvesoftware.Steam/.local/share/Steam")).unwrap();
    let empty = read_mangohud_config_at(home, None);
    assert!(empty.steam_detected);
    assert!(empty.flatpak_steam);
    assert!(!empty.exists);

    let path = home.join(MANGOHUD_FLATPAK_PATH);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let contents = "# the overlay\nfps_limit=0\ngpu_stats\n\n   \ncpu_stats\n";
    fs::write(&path, contents).unwrap();
    let read = read_mangohud_config_at(home, None);
    assert!(read.exists);
    assert!(read.readable);
    assert_eq!(read.settings, 3);
    // A read leaves the file byte-identical and creates no backup beside it.
    assert_eq!(fs::read_to_string(&path).unwrap(), contents);
    assert!(!home
        .join(format!("{MANGOHUD_FLATPAK_PATH}.rowyn-backup"))
        .exists());
    let json = serde_json::to_string(&read).unwrap();
    assert!(!json.contains(&home.to_string_lossy().to_string()));
    assert!(json.contains(".var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf"));
}

#[test]
fn mangohud_native_path_follows_xdg_config_home_when_it_is_absolute() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let xdg = home.join("xdg-config");
    let conf = xdg.join("MangoHud/MangoHud.conf");
    fs::create_dir_all(conf.parent().unwrap()).unwrap();
    fs::write(&conf, "gpu_stats\nfps_limit=0\n").unwrap();
    // The default path holds a different config that must not be counted.
    let default = home.join(MANGOHUD_NATIVE_PATH);
    fs::create_dir_all(default.parent().unwrap()).unwrap();
    fs::write(&default, "gpu_stats\ncpu_stats\nframe_timing\n").unwrap();

    let resolved = read_mangohud_config_at(home, Some(&xdg));
    assert_eq!(resolved.native_path, "~/xdg-config/MangoHud/MangoHud.conf");
    assert!(resolved.native_exists);
    assert!(resolved.native_readable);
    assert_eq!(resolved.native_settings, 2);
    // The Flatpak path is unchanged by XDG_CONFIG_HOME.
    assert_eq!(
        resolved.path,
        ".var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf"
    );
    // A read leaves both files byte-identical.
    assert_eq!(fs::read_to_string(&conf).unwrap(), "gpu_stats\nfps_limit=0\n");
    assert_eq!(
        fs::read_to_string(&default).unwrap(),
        "gpu_stats\ncpu_stats\nframe_timing\n"
    );

    // Unset (None) or a relative variable falls back to ~/.config.
    for fallback in [None, Some(Path::new("relative/config"))] {
        let plain = read_mangohud_config_at(home, fallback);
        assert_eq!(plain.native_path, "~/.config/MangoHud/MangoHud.conf");
        assert_eq!(plain.native_settings, 3);
    }

    // A config root outside home is named absolutely rather than as ~/.
    let outside = tempfile::tempdir().unwrap();
    let external = read_mangohud_config_at(home, Some(outside.path()));
    assert!(!external.native_exists);
    assert!(external.native_path.starts_with('/'));
    assert!(!external.native_path.starts_with("~/"));
}

#[test]
fn mangohud_config_keeps_the_ignored_host_path_and_unreadable_files_visible() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    fs::create_dir_all(home.join(".local/share/Steam")).unwrap();
    fs::create_dir_all(home.join(".config/MangoHud")).unwrap();
    fs::write(home.join(MANGOHUD_NATIVE_PATH), "gpu_stats\nfps_limit=0\n").unwrap();
    // The host path exists and is read for the host boundary, but Flathub
    // Steam still does not read it.
    let native_only = read_mangohud_config_at(home, None);
    assert!(native_only.steam_detected);
    assert!(!native_only.flatpak_steam);
    assert!(!native_only.exists);
    assert!(native_only.native_exists);
    assert!(native_only.native_readable);
    assert_eq!(native_only.native_settings, 2);

    // A symlinked config is present but deliberately not followed.
    let target = home.join("elsewhere.conf");
    fs::write(&target, "gpu_stats\n").unwrap();
    let conf = home.join(MANGOHUD_FLATPAK_PATH);
    fs::create_dir_all(conf.parent().unwrap()).unwrap();
    std::os::unix::fs::symlink(&target, &conf).unwrap();
    let linked = read_mangohud_config_at(home, None);
    assert!(linked.exists);
    assert!(!linked.readable);
    assert!(linked.warnings > 0);
    assert_eq!(fs::read_to_string(&target).unwrap(), "gpu_stats\n");
}

#[test]
fn parses_vdf_bom_and_comments() {
    let parsed = parse_vdf(concat!(
        "\u{feff}",
        r#"// Steam library list
"libraryfolders" { "0" { "path" "/games/library" } }"#
    ))
    .unwrap();
    let libraries = object(&parsed, "libraryfolders").unwrap();
    let first = match &libraries[0].1 {
        Value::Object(fields) => fields,
        _ => panic!("expected library object"),
    };
    assert_eq!(text(first, "path"), Some("/games/library"));
}

fn write_global_config(home: &Path, body: &str) {
    let config = home.join(".local/share/Steam/config");
    fs::create_dir_all(&config).unwrap();
    fs::write(config.join("config.vdf"), body).unwrap();
}

/// A global config.vdf in Steam's shape. `Some("0")`/`Some("1")` writes the
/// pre-caching flag; None leaves the ShaderCacheManager block without one,
/// which is what Steam does while pre-caching stays at its default.
fn global_config(flag: Option<&str>) -> String {
    let manager = match flag {
        Some(flag) => format!(
            "\t\t\t\t\"ShaderCacheManager\"\n\t\t\t\t{{\n\t\t\t\t\t\"DisableShaderCache\"\t\t\"{flag}\"\n\t\t\t\t}}\n"
        ),
        None => String::from(
            "\t\t\t\t\"ShaderCacheManager\"\n\t\t\t\t{\n\t\t\t\t\t\"HasCurrentBucket\"\t\t\"1\"\n\t\t\t\t}\n",
        ),
    };
    format!(
        "\"InstallConfigStore\"\n{{\n\t\"Software\"\n\t{{\n\t\t\"Valve\"\n\t\t{{\n\t\t\t\"Steam\"\n\t\t\t{{\n{manager}\t\t\t}}\n\t\t}}\n\t}}\n}}\n"
    )
}

const CONFIG_WITHOUT_MAPPING: &str = "\"InstallConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"CDKeys\"\n\t\t\t\t{\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n";

const CONFIG_WITH_OTHER_TOOL: &str = "\"InstallConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"CompatToolMapping\"\n\t\t\t\t{\n\t\t\t\t\t\"1234560\"\n\t\t\t\t\t{\n\t\t\t\t\t\t\"name\"\t\t\"proton_9_0\"\n\t\t\t\t\t}\n\t\t\t\t\t\"0\"\n\t\t\t\t\t{\n\t\t\t\t\t\t\"name\"\t\t\"proton_9_0\"\n\t\t\t\t\t\t\"config\"\t\t\"\"\n\t\t\t\t\t\t\"priority\"\t\t\"75\"\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n";

#[test]
fn mapping_inserted_when_absent_and_rest_of_file_untouched() {
    let updated = apply_default_compat_mapping(CONFIG_WITHOUT_MAPPING).unwrap();
    assert_eq!(compat_default_tool(&updated).as_deref(), Some("proton_experimental"));
    // Everything before the Steam block's opening brace is byte-identical,
    // and the pre-existing CDKeys block survives verbatim.
    let marker = "\"Steam\"\n\t\t\t{\n";
    assert_eq!(
        updated.split(marker).next(),
        CONFIG_WITHOUT_MAPPING.split(marker).next()
    );
    assert!(updated.contains("\"CDKeys\""));
    parse_vdf(&updated).unwrap();
}

#[test]
fn existing_default_replaced_and_per_game_entries_preserved() {
    let updated = apply_default_compat_mapping(CONFIG_WITH_OTHER_TOOL).unwrap();
    assert_eq!(compat_default_tool(&updated).as_deref(), Some("proton_experimental"));
    // The per-game override for 1234560 keeps its own tool.
    assert!(updated.contains("\"1234560\""));
    assert_eq!(updated.matches("proton_9_0").count(), 1);
    parse_vdf(&updated).unwrap();
}

#[test]
fn mapping_block_without_default_gains_one() {
    let config = CONFIG_WITH_OTHER_TOOL.replace(
        "\t\t\t\t\t\"0\"\n\t\t\t\t\t{\n\t\t\t\t\t\t\"name\"\t\t\"proton_9_0\"\n\t\t\t\t\t\t\"config\"\t\t\"\"\n\t\t\t\t\t\t\"priority\"\t\t\"75\"\n\t\t\t\t\t}\n",
        "",
    );
    let updated = apply_default_compat_mapping(&config).unwrap();
    assert_eq!(compat_default_tool(&updated).as_deref(), Some("proton_experimental"));
    assert!(updated.contains("\"1234560\""));
    parse_vdf(&updated).unwrap();
}

#[test]
fn apply_is_idempotent_once_experimental_is_the_default() {
    let once = apply_default_compat_mapping(CONFIG_WITHOUT_MAPPING).unwrap();
    let twice = apply_default_compat_mapping(&once).unwrap();
    assert_eq!(once, twice);
}

#[test]
fn apply_fails_closed_on_unrecognized_shapes() {
    // No Steam section at all.
    assert!(apply_default_compat_mapping("\"InstallConfigStore\"\n{\n}\n").is_err());
    // Truncated / unclosed.
    assert!(apply_default_compat_mapping("\"InstallConfigStore\"\n{\n\t\"Software\"\n\t{\n").is_err());
    // Not VDF.
    assert!(apply_default_compat_mapping("hello world").is_err());
}

fn config_with_mapping(entries: &[(u32, &str)]) -> String {
    let mut body = String::from(
        "\"InstallConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"CompatToolMapping\"\n\t\t\t\t{\n",
    );
    for (app_id, tool) in entries {
        body.push_str(&format!(
            "\t\t\t\t\t\"{app_id}\"\n\t\t\t\t\t{{\n\t\t\t\t\t\t\"name\"\t\t\"{tool}\"\n\t\t\t\t\t}}\n"
        ));
    }
    body.push_str("\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n");
    body
}

#[test]
fn a_titles_compat_tool_comes_from_its_entry_then_the_global_default() {
    let config = config_with_mapping(&[(1245620, "proton_9_0"), (0, "proton_experimental")]);
    assert_eq!(compat_tool_for(&config, "1245620").as_deref(), Some("proton_9_0"));
    // No entry of its own: the "0" default decides, so a title switched to
    // Proton by "Enable Steam Play for all titles" is still a container run.
    assert_eq!(compat_tool_for(&config, "620").as_deref(), Some("proton_experimental"));
    assert_eq!(compat_tool_for(&config_with_mapping(&[]), "620"), None);
}

#[test]
fn container_tools_are_proton_and_the_steam_linux_runtime() {
    for tool in [
        "proton_9_0",
        "proton_experimental",
        "Proton-GE",
        "steamlinuxruntime_sniper",
        "Steam Linux Runtime 3.0 (sniper)",
    ] {
        assert!(launches_in_container(tool), "{tool}");
    }
    // A native title has no mapping at all, and a blank name decides nothing.
    assert!(!launches_in_container(""));
    assert!(!launches_in_container("native"));
}

#[test]
fn a_proton_title_is_flagged_as_a_container_launch_in_the_scan() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    fs::create_dir_all(steam.join("steamapps")).unwrap();
    fs::create_dir_all(steam.join("config")).unwrap();
    write_game(&steam, 620, "Portal 2", 4);
    write_game(&steam, 1245620, "Elden Ring", 4);
    fs::write(
        steam.join("config/config.vdf"),
        config_with_mapping(&[(1245620, "proton_9_0")]),
    )
    .unwrap();
    let scan = scan_steam_libraries(home, false);
    let flags: Vec<(&str, bool)> = scan
        .games
        .iter()
        .map(|game| (game.app_id.as_str(), game.container))
        .collect();
    // Sorted by app id, and only the Proton title is a container launch.
    assert_eq!(flags, vec![("1245620", true), ("620", false)]);
    assert!(serde_json::to_string(&scan).unwrap().contains("\"container\":true"));

    // The global default applies to every title without its own entry.
    fs::write(
        steam.join("config/config.vdf"),
        config_with_mapping(&[(0, "proton_experimental")]),
    )
    .unwrap();
    let shifted = scan_steam_libraries(home, false);
    assert!(shifted.games.iter().all(|game| game.container));
}

fn profile_with_launch_options(entries: &[(&str, &str)]) -> String {
    let mut out = String::from(
        "\"UserLocalConfigStore\"\n{\n\t\"Software\"\n\t{\n\t\t\"Valve\"\n\t\t{\n\t\t\t\"Steam\"\n\t\t\t{\n\t\t\t\t\"apps\"\n\t\t\t\t{\n",
    );
    for (app_id, options) in entries {
        out.push_str(&format!(
            "\t\t\t\t\t\"{app_id}\"\n\t\t\t\t\t{{\n\t\t\t\t\t\t\"LaunchOptions\"\t\t\"{options}\"\n\t\t\t\t\t}}\n"
        ));
    }
    out.push_str("\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n");
    out
}

fn write_login_users(home: &Path, entries: &[(&str, bool, u64)]) {
    let config = home.join(".local/share/Steam/config");
    fs::create_dir_all(&config).unwrap();
    let mut body = String::from("\"users\"\n{\n");
    for (account, most_recent, timestamp) in entries {
        body.push_str(&format!(
            "\t\"{account}\"\n\t{{\n\t\t\"Timestamp\"\t\t\"{timestamp}\"\n\t\t\"MostRecent\"\t\t\"{}\"\n\t}}\n",
            if *most_recent { "1" } else { "0" }
        ));
    }
    body.push_str("}\n");
    fs::write(config.join("loginusers.vdf"), body).unwrap();
}

#[test]
fn recorded_launch_options_come_from_the_account_steam_used_last() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    write_game(&steam, 620, "Portal 2", 4);
    write_game(&steam, 570, "Dota 2", 4);

    // Steam installed but never run here: nothing to reconcile, and the scan
    // says the profile was not read rather than claiming no options exist.
    let unread = scan_steam_libraries(home, false);
    assert!(!unread.launch_options_read);
    assert!(unread.games.iter().all(|game| game.launch_options.is_none()));

    write_profile(
        home,
        "111",
        &profile_with_launch_options(&[("620", "MANGOHUD=1 %command%")]),
    );
    write_profile(
        home,
        "222",
        &profile_with_launch_options(&[("570", "gamemoderun %command%")]),
    );
    // 222 is the account Steam used last, so its options are the ones that
    // would run — not the other account's, and not a merge of both.
    write_login_users(
        home,
        &[("111", false, 1_700_000_000), ("222", true, 1_760_000_000)],
    );
    let scan = scan_steam_libraries(home, false);
    assert!(scan.launch_options_read);
    let portal = scan.games.iter().find(|game| game.app_id == "620").unwrap();
    let dota = scan.games.iter().find(|game| game.app_id == "570").unwrap();
    assert_eq!(portal.launch_options, None);
    assert_eq!(dota.launch_options.as_deref(), Some("gamemoderun %command%"));
    let json = serde_json::to_string(&scan).unwrap();
    assert!(!json.contains("111"));
    assert!(!json.contains("222"));

    // Switching the active account switches which text the page reconciles
    // against, because that is the text Steam would run.
    write_login_users(
        home,
        &[("111", true, 1_760_000_000), ("222", false, 1_700_000_000)],
    );
    let switched = scan_steam_libraries(home, false);
    let portal = switched.games.iter().find(|game| game.app_id == "620").unwrap();
    let dota = switched.games.iter().find(|game| game.app_id == "570").unwrap();
    assert_eq!(portal.launch_options.as_deref(), Some("MANGOHUD=1 %command%"));
    assert_eq!(dota.launch_options, None);
}

#[test]
fn launch_option_reads_fail_closed_instead_of_inventing_an_empty_state() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let steam = home.join(".local/share/Steam");
    write_game(&steam, 620, "Portal 2", 4);

    // Steam writes an empty value when a player clears the field; that is the
    // same as none recorded, not a command that could accidentally match.
    write_profile(home, "123", &profile_with_launch_options(&[("620", "")]));
    let cleared = scan_steam_libraries(home, false);
    assert!(cleared.launch_options_read);
    assert_eq!(cleared.games[0].launch_options, None);

    // An unparseable profile is a failed read, and it stays visible as one.
    write_profile(home, "123", "not a vdf {");
    let broken = scan_steam_libraries(home, false);
    assert!(!broken.launch_options_read);
    assert!(broken.warnings > 0);
    assert_eq!(broken.games[0].launch_options, None);
}
