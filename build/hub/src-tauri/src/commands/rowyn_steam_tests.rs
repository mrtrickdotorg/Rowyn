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

    let result = scan_steam_libraries(home);
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
    let result = scan_steam_libraries(temporary.path());
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
    let result = scan_steam_libraries(temporary.path());
    assert!(result.available);
    assert!(result.warnings > 0);
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
