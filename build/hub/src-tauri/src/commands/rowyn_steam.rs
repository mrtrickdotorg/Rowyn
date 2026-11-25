//! Read-only Steam metadata scanner: installed titles from Steam's app
//! manifests, and play history from Steam's own per-account local profile.
//! It reads only Steam's metadata and never reports install paths, launches
//! games, measures frame or latency figures, or rates titles.
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

const MAX_VDF_BYTES: u64 = 256 * 1024;
const MAX_MANIFEST_BYTES: u64 = 32 * 1024;
const MAX_LIBRARIES: usize = 32;
const MAX_MANIFESTS: usize = 2048;
const MAX_DIRECTORY_ENTRIES: usize = 8192;
const MAX_TOKENS: usize = 50_000;
// localconfig.vdf is per account and carries friends, cloud and app state far
// beyond the apps block, so it gets its own bounded budget rather than the
// 256 KB manifest limit.
const MAX_LOCAL_CONFIG_BYTES: u64 = 4 * 1024 * 1024;
const MAX_LOCAL_CONFIG_TOKENS: usize = 500_000;
const MAX_ACCOUNTS: usize = 8;
const MAX_RECENT_APPS: usize = 4096;
/// How many rows the page receives; the model behind them is unbounded.
const RECENT_LIMIT: usize = 8;
/// Plausibility bounds: junk stays out rather than becoming history.
const MAX_PLAYTIME_MINUTES: u64 = 60 * 24 * 366 * 30;
const MAX_LAST_PLAYED: u64 = 4_102_444_800; // 2100-01-01
/// Steam's cached library artwork. A 600×900 capsule is typically 15–650 KB;
/// the cap keeps a corrupt or replaced file from entering the page.
const MAX_COVER_BYTES: u64 = 768 * 1024;
/// Across one scan: room for a large library, still one bounded message.
const MAX_COVER_TOTAL_BYTES: usize = 8 * 1024 * 1024;
const MAX_COVER_VARIANTS: usize = 32;
/// Steam's per-app `LaunchOptions` field is short (the Play page lets a player
/// append 200 characters to a generated command). A longer value is clamped for
/// display, and the reconciliation then reports drift rather than a match it
/// cannot prove.
const MAX_LAUNCH_OPTIONS_CHARS: usize = 1024;
/// Preferred artwork first: the 600×900 capsule is the portrait cover.
const COVER_NAMES: [&str; 4] = [
    "library_600x900.jpg",
    "library_header.jpg",
    "library_capsule.jpg",
    "header.jpg",
];

#[derive(Debug, Serialize)]
pub(crate) struct SteamGame {
    app_id: String,
    name: String,
    /// A data URL of Steam's own cached cover art, when this machine has it.
    cover: Option<String>,
    /// Steam plays this title inside a Proton or Steam Linux Runtime container
    /// (its own CompatToolMapping entry, else the "0" default). That container
    /// has its own /usr and no GameMode client library, so a preload cannot
    /// register the game.
    container: bool,
    /// The launch options Steam itself records for this title, from the most
    /// recently used account's `localconfig.vdf`. `None` means Steam has none
    /// recorded for it; whether the profile could be read at all is reported
    /// once, on `SteamScan::launch_options_read`.
    launch_options: Option<String>,
}

#[derive(Debug, Serialize)]
pub(crate) struct SteamScan {
    available: bool,
    warnings: usize,
    /// A per-account profile was parsed, so a `None` launch_options means
    /// "Steam recorded nothing" rather than "not known".
    launch_options_read: bool,
    /// What the folder list itself said, so a title on an unreadable second
    /// drive is named as such instead of disappearing behind a warning count.
    libraries: LibraryCoverage,
    games: Vec<SteamGame>,
}

#[derive(Debug, Serialize)]
pub(crate) struct LibraryCoverage {
    /// Distinct folders this scan read; the install root counts as one.
    scanned: usize,
    /// Folders Steam lists that this scan could not read: an unmounted drive, a
    /// card that is not inserted, or a path this user cannot open.
    unavailable: usize,
    /// `libraryfolders.vdf` was parsed, so a declared folder was visited or
    /// counted rather than skipped.
    list_read: bool,
}

#[derive(Debug, Clone)]
enum Value {
    Text(String),
    Object(Vec<(String, Value)>),
}

#[derive(Debug)]
enum Token {
    Text(String),
    Open,
    Close,
}

fn tokenize_vdf(input: &str, max_tokens: usize) -> Result<Vec<Token>, String> {
    let bytes = input.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            byte if byte.is_ascii_whitespace() => index += 1,
            b'{' => {
                tokens.push(Token::Open);
                index += 1;
            }
            b'}' => {
                tokens.push(Token::Close);
                index += 1;
            }
            b'/' if bytes.get(index + 1) == Some(&b'/') => {
                while index < bytes.len() && bytes[index] != b'\n' {
                    index += 1;
                }
            }
            b'"' => {
                index += 1;
                let mut value = Vec::new();
                let mut closed = false;
                while index < bytes.len() {
                    match bytes[index] {
                        b'"' => {
                            index += 1;
                            closed = true;
                            break;
                        }
                        b'\\' if index + 1 < bytes.len() => {
                            index += 1;
                            value.push(bytes[index]);
                            index += 1;
                        }
                        byte => {
                            value.push(byte);
                            index += 1;
                        }
                    }
                }
                if !closed {
                    return Err("unterminated quoted value".into());
                }
                tokens.push(Token::Text(String::from_utf8_lossy(&value).into_owned()));
            }
            _ => {
                let start = index;
                while index < bytes.len()
                    && !bytes[index].is_ascii_whitespace()
                    && !matches!(bytes[index], b'{' | b'}')
                {
                    index += 1;
                }
                if start == index {
                    return Err("invalid token".into());
                }
                tokens.push(Token::Text(
                    String::from_utf8_lossy(&bytes[start..index]).into_owned(),
                ));
            }
        }
        if tokens.len() > max_tokens {
            return Err("VDF has too many entries".into());
        }
    }
    Ok(tokens)
}

fn parse_pairs(
    tokens: &[Token],
    index: &mut usize,
    depth: usize,
    nested: bool,
) -> Result<Vec<(String, Value)>, String> {
    if depth > 16 {
        return Err("VDF nesting is too deep".into());
    }
    let mut pairs = Vec::new();
    loop {
        match tokens.get(*index) {
            None if !nested => return Ok(pairs),
            None => return Err("unclosed VDF object".into()),
            Some(Token::Close) if nested => {
                *index += 1;
                return Ok(pairs);
            }
            Some(Token::Close) => return Err("unexpected closing brace".into()),
            Some(Token::Open) => return Err("expected a VDF key".into()),
            Some(Token::Text(key)) => {
                let key = key.clone();
                *index += 1;
                let value = match tokens.get(*index) {
                    Some(Token::Text(text)) => {
                        *index += 1;
                        Value::Text(text.clone())
                    }
                    Some(Token::Open) => {
                        *index += 1;
                        Value::Object(parse_pairs(tokens, index, depth + 1, true)?)
                    }
                    _ => return Err("missing VDF value".into()),
                };
                pairs.push((key, value));
            }
        }
    }
}

fn parse_vdf_bounded(input: &str, max_tokens: usize) -> Result<Vec<(String, Value)>, String> {
    let input = input.strip_prefix('\u{feff}').unwrap_or(input);
    let tokens = tokenize_vdf(input, max_tokens)?;
    let mut index = 0;
    parse_pairs(&tokens, &mut index, 0, false)
}

fn parse_vdf(input: &str) -> Result<Vec<(String, Value)>, String> {
    parse_vdf_bounded(input, MAX_TOKENS)
}

fn text<'a>(object: &'a [(String, Value)], key: &str) -> Option<&'a str> {
    object.iter().find_map(|(name, value)| {
        if name.eq_ignore_ascii_case(key) {
            if let Value::Text(value) = value {
                Some(value.as_str())
            } else {
                None
            }
        } else {
            None
        }
    })
}

fn object<'a>(object: &'a [(String, Value)], key: &str) -> Option<&'a [(String, Value)]> {
    object.iter().find_map(|(name, value)| {
        if name.eq_ignore_ascii_case(key) {
            if let Value::Object(value) = value {
                Some(value.as_slice())
            } else {
                None
            }
        } else {
            None
        }
    })
}

fn valid_library_path(path: &str) -> bool {
    !path.is_empty()
        && path.len() <= 4096
        && !path.chars().any(char::is_control)
        && Path::new(path).is_absolute()
}

/// One Steam install's folder list. `libraryfolders.vdf` is the only place a
/// second drive, external SSD or SD card is declared, so it is parsed whenever
/// it exists and every declared folder is visited — the common install path
/// alone would hide those titles.
struct LibraryList {
    paths: Vec<PathBuf>,
    /// The list was parsed, so a declared folder was visited or counted.
    list_read: bool,
    /// Declared folders this scan could not read.
    unavailable: usize,
}

fn library_list(root: &Path, warnings: &mut usize) -> LibraryList {
    let mut list = LibraryList {
        paths: vec![root.to_path_buf()],
        list_read: false,
        unavailable: 0,
    };
    let file = root.join("steamapps/libraryfolders.vdf");
    let metadata = match fs::symlink_metadata(&file) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        Ok(_) => {
            *warnings += 1;
            return list;
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return list,
        Err(_) => {
            *warnings += 1;
            return list;
        }
    };
    if metadata.len() > MAX_VDF_BYTES {
        *warnings += 1;
        return list;
    }
    let mut source = String::new();
    if File::open(&file)
        .and_then(|file| file.take(MAX_VDF_BYTES + 1).read_to_string(&mut source))
        .is_err()
        || source.len() as u64 > MAX_VDF_BYTES
    {
        *warnings += 1;
        return list;
    }
    let parsed = match parse_vdf(&source) {
        Ok(parsed) => parsed,
        Err(_) => {
            *warnings += 1;
            return list;
        }
    };
    let Some(libraries) = object(&parsed, "libraryfolders") else {
        *warnings += 1;
        return list;
    };
    list.list_read = true;
    for (_, entry) in libraries {
        // Current Steam writes an object per library; older clients wrote the
        // path as the entry's own value, which still names a real folder.
        let path = match entry {
            Value::Object(fields) => match text(fields, "path") {
                Some(path) => path,
                None => continue,
            },
            Value::Text(path) => path.as_str(),
            _ => continue,
        };
        if !valid_library_path(path) {
            *warnings += 1;
            continue;
        }
        if list.paths.len() >= MAX_LIBRARIES {
            *warnings += 1;
            break;
        }
        let path = PathBuf::from(path);
        if path.is_dir() {
            list.paths.push(path);
        } else {
            // An unmounted drive or an unreadable mount is counted, not
            // dropped: the page names it instead of implying a clean scan.
            list.unavailable += 1;
            *warnings += 1;
        }
    }
    list
}

fn parse_manifest(path: &Path, expected_id: &str, warnings: &mut usize) -> Option<SteamGame> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            *warnings += 1;
            return None;
        }
    };
    if metadata.len() > MAX_MANIFEST_BYTES {
        *warnings += 1;
        return None;
    }
    let mut source = String::new();
    if File::open(path)
        .and_then(|file| {
            file.take(MAX_MANIFEST_BYTES + 1)
                .read_to_string(&mut source)
        })
        .is_err()
        || source.len() as u64 > MAX_MANIFEST_BYTES
    {
        *warnings += 1;
        return None;
    }
    let parsed = match parse_vdf(&source) {
        Ok(parsed) => parsed,
        Err(_) => {
            *warnings += 1;
            return None;
        }
    };
    let Some(state) = object(&parsed, "AppState") else {
        *warnings += 1;
        return None;
    };
    let (Some(app_id), Some(name), Some(flags)) = (
        text(state, "appid"),
        text(state, "name"),
        text(state, "StateFlags"),
    ) else {
        *warnings += 1;
        return None;
    };
    if app_id != expected_id || app_id.parse::<u32>().ok().filter(|id| *id > 0).is_none() {
        *warnings += 1;
        return None;
    }
    // Steam's FullyInstalled bit is 4. Omit queued, partial and other
    // manifests rather than presenting them as installed games.
    let flags = match flags.parse::<u32>() {
        Ok(flags) => flags,
        Err(_) => {
            *warnings += 1;
            return None;
        }
    };
    if flags & 4 == 0 || flags & (32 | 128) != 0 {
        return None;
    }
    let name = name.trim();
    if name.is_empty() || name.len() > 160 || name.chars().any(char::is_control) {
        *warnings += 1;
        return None;
    }
    Some(SteamGame {
        app_id: app_id.to_string(),
        name: name.to_string(),
        cover: None,
        container: false,
        launch_options: None,
    })
}

/// The Steam install directories that exist on this machine, de-duplicated
/// through their canonical paths. Shared by the manifest scan and the local
/// profile read so both look where Steam actually is.
fn steam_roots(home: &Path) -> Vec<PathBuf> {
    let mut candidates = vec![
        home.join(".local/share/Steam"),
        home.join(".steam/steam"),
        home.join(".steam/root"),
        home.join(".var/app/com.valvesoftware.Steam/.local/share/Steam"),
        home.join(".var/app/com.valvesoftware.Steam/data/Steam"),
    ];
    if let Some(data_home) = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from) {
        if data_home.is_absolute() {
            candidates.push(data_home.join("Steam"));
        }
    }
    let mut roots = Vec::new();
    for candidate in candidates {
        if !candidate.is_dir() {
            continue;
        }
        let canonical = candidate.canonicalize().unwrap_or(candidate);
        if !roots.contains(&canonical) {
            roots.push(canonical);
        }
    }
    roots
}

fn scan_steam_libraries(home: &Path, with_covers: bool) -> SteamScan {
    let roots = steam_roots(home);
    if roots.is_empty() {
        return SteamScan {
            available: false,
            warnings: 0,
            launch_options_read: false,
            libraries: LibraryCoverage {
                scanned: 0,
                unavailable: 0,
                list_read: false,
            },
            games: Vec::new(),
        };
    }
    let mut warnings = 0;
    let mut libraries = Vec::new();
    let mut unavailable = 0usize;
    let mut list_read = false;
    for root in &roots {
        let list = library_list(root, &mut warnings);
        list_read |= list.list_read;
        unavailable += list.unavailable;
        for library in list.paths {
            let canonical = library.canonicalize().unwrap_or(library);
            if libraries.len() < MAX_LIBRARIES && !libraries.contains(&canonical) {
                libraries.push(canonical);
            } else if libraries.len() >= MAX_LIBRARIES {
                warnings += 1;
            }
        }
    }
    // Steam's own answer to "does this title run in a container": the global
    // CompatToolMapping in config.vdf, per app with the "0" default behind it.
    // Read once per scan and bounded like the other config reads.
    let compat_tokens = roots.iter().find_map(|root| {
        let path = root.join("config/config.vdf");
        if fs::metadata(&path).ok()?.len() > MAX_LOCAL_CONFIG_BYTES {
            return None;
        }
        let source = fs::read_to_string(&path).ok()?;
        tokenize_spans(&source).ok()
    });
    // Count before the list is consumed: this is how many folders the scan
    // actually read, including any Steam declared on another drive.
    let scanned_libraries = libraries.len();
    let mut games = BTreeMap::new();
    let mut examined = 0;
    for library in libraries {
        let directory = library.join("steamapps");
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                warnings += 1;
                continue;
            }
        };
        let mut directory_entries = 0;
        for entry in entries {
            directory_entries += 1;
            if directory_entries > MAX_DIRECTORY_ENTRIES {
                warnings += 1;
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => {
                    warnings += 1;
                    continue;
                }
            };
            let filename = entry.file_name();
            let filename = filename.to_string_lossy();
            let Some(app_id) = filename
                .strip_prefix("appmanifest_")
                .and_then(|name| name.strip_suffix(".acf"))
            else {
                continue;
            };
            if app_id.is_empty() || !app_id.bytes().all(|byte| byte.is_ascii_digit()) {
                continue;
            }
            examined += 1;
            if examined > MAX_MANIFESTS {
                warnings += 1;
                break;
            }
            if let Some(game) = parse_manifest(&entry.path(), app_id, &mut warnings) {
                games.entry(game.app_id.clone()).or_insert(game);
            }
        }
        if examined >= MAX_MANIFESTS {
            break;
        }
    }
    let mut games: Vec<SteamGame> = games.into_values().collect();
    if let Some(tokens) = &compat_tokens {
        for game in &mut games {
            game.container = compat_tool_in(tokens, &game.app_id)
                .is_some_and(|tool| launches_in_container(&tool));
        }
    }
    // What Steam will actually run, read back per title. The page compares this
    // with the command it builds, so a saved profile is never presented as if
    // it were applied.
    let (launch_options_read, recorded) = scan_launch_options(&roots, &mut warnings);
    for game in &mut games {
        game.launch_options = recorded.get(&game.app_id).cloned();
    }
    // Covers are the one part of the scan that reads image bytes; they are
    // attached under a per-file and per-scan budget, and only for the explicit
    // library scan — the recent-play read asks for names alone.
    if with_covers {
        let mut spent = 0usize;
        for game in &mut games {
            if spent >= MAX_COVER_TOTAL_BYTES {
                break;
            }
            let Some(path) = roots
                .iter()
                .find_map(|root| cover_file(root, &game.app_id))
            else {
                continue;
            };
            let Some(cover) = read_cover(&path, &mut warnings) else {
                continue;
            };
            if spent + cover.len() > MAX_COVER_TOTAL_BYTES {
                warnings += 1;
                break;
            }
            spent += cover.len();
            game.cover = Some(cover);
        }
    }
    SteamScan {
        available: true,
        warnings,
        launch_options_read,
        libraries: LibraryCoverage {
            scanned: scanned_libraries,
            unavailable,
            list_read,
        },
        games,
    }
}

/// The cover Steam cached for `app_id`, preferring the 600×900 capsule. Modern
/// clients store each variant in a content-addressed directory
/// (`librarycache/<appid>/<hash>/library_600x900.jpg`); older ones wrote
/// `<appid>_library_600x900.jpg` beside it. Both are local files only.
fn cover_file(root: &Path, app_id: &str) -> Option<PathBuf> {
    let cache = root.join("appcache/librarycache");
    let mut variants = Vec::new();
    if let Ok(entries) = fs::read_dir(cache.join(app_id)) {
        for entry in entries.flatten().take(MAX_COVER_VARIANTS) {
            let path = entry.path();
            let is_dir = fs::symlink_metadata(&path)
                .map(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
                .unwrap_or(false);
            if is_dir {
                variants.push(path);
            }
        }
    }
    variants.sort();
    for name in COVER_NAMES {
        let legacy = cache.join(format!("{app_id}_{name}"));
        if legacy.is_file() {
            return Some(legacy);
        }
        if let Some(directory) = variants
            .iter()
            .find(|directory| directory.join(name).is_file())
        {
            return Some(directory.join(name));
        }
    }
    None
}

fn read_cover(path: &Path, warnings: &mut usize) -> Option<String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            *warnings += 1;
            return None;
        }
    };
    if metadata.len() == 0 || metadata.len() > MAX_COVER_BYTES {
        *warnings += 1;
        return None;
    }
    let mut bytes = Vec::new();
    if File::open(path)
        .and_then(|file| file.take(MAX_COVER_BYTES + 1).read_to_end(&mut bytes))
        .is_err()
        || bytes.len() as u64 > MAX_COVER_BYTES
    {
        *warnings += 1;
        return None;
    }
    // Only a JPEG is embedded: a truncated or swapped file must not become a
    // broken image in the page.
    if !bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        *warnings += 1;
        return None;
    }
    Some(format!("data:image/jpeg;base64,{}", base64(&bytes)))
}

/// Base64 for the cover data URL. Written here rather than pulling in a crate:
/// the encoding is fixed, and the scan is the only caller.
fn base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for chunk in bytes.chunks(3) {
        let a = chunk[0];
        let b = *chunk.get(1).unwrap_or(&0);
        let c = *chunk.get(2).unwrap_or(&0);
        out.push(TABLE[(a >> 2) as usize] as char);
        out.push(TABLE[((a & 0b11) << 4 | b >> 4) as usize] as char);
        out.push(if chunk.len() > 1 {
            TABLE[((b & 0b1111) << 2 | c >> 6) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            TABLE[(c & 0b111111) as usize] as char
        } else {
            '='
        });
    }
    out
}

#[tauri::command]
pub(crate) async fn rowyn_steam_games_scan() -> Result<SteamScan, String> {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the current user's home directory.".to_string())?;
    tauri::async_runtime::spawn_blocking(move || scan_steam_libraries(&home, true))
        .await
        .map_err(|error| format!("Steam library scan did not complete: {error}"))
}

// --- Recorded launch options ----------------------------------------------
// The per-account profile is the only place Steam records the launch options a
// title will really run with. Reading that back is what lets the Play page say
// whether the command it built is in effect, instead of showing a saved profile
// as though it had been applied. Read-only: nothing here writes Steam's files,
// starts Steam or launches a game.

fn valid_account_id(account: &str) -> bool {
    !account.is_empty() && account.len() <= 32 && account.bytes().all(|byte| byte.is_ascii_digit())
}

/// Steam's app keys are written as decimals; normalizing through `u32` makes a
/// zero-padded key match the manifest's own id.
fn normalized_app_id(app_id: &str) -> Option<String> {
    app_id
        .parse::<u32>()
        .ok()
        .filter(|id| *id > 0)
        .map(|id| id.to_string())
}

fn clamp_chars(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.to_string();
    }
    let mut clamped: String = value.chars().take(limit).collect();
    clamped.push('…');
    clamped
}

/// The account Steam last used, from the `MostRecent` flag in
/// `config/loginusers.vdf` (falling back to the newest `Timestamp`). This file
/// is optional and often absent, so a missing one is not a failed read.
fn most_recent_account(root: &Path) -> Option<String> {
    let path = root.join("config/loginusers.vdf");
    let mut ignored = 0;
    let source = read_bounded_text(&path, MAX_VDF_BYTES, &mut ignored)?;
    let parsed = parse_vdf(&source).ok()?;
    let users = object(&parsed, "users")?;
    let mut newest: Option<(u64, String)> = None;
    for (account, value) in users {
        let Value::Object(fields) = value else {
            continue;
        };
        if !valid_account_id(account) {
            continue;
        }
        if text(fields, "MostRecent") == Some("1") {
            return Some(account.clone());
        }
        let timestamp = text(fields, "Timestamp")
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0);
        let newer = match &newest {
            Some((best, _)) => timestamp > *best,
            None => true,
        };
        if newer {
            newest = Some((timestamp, account.clone()));
        }
    }
    newest.map(|(_, account)| account)
}

/// One account's recorded launch options, keyed by normalized app id. Empty
/// values are skipped: Steam writes `"LaunchOptions" ""` for a title whose
/// options were cleared, which is the same as none recorded.
fn recorded_launch_options(apps: &[(String, Value)]) -> BTreeMap<String, String> {
    let mut recorded = BTreeMap::new();
    for (app_id, value) in apps.iter().take(MAX_RECENT_APPS) {
        let Value::Object(fields) = value else {
            continue;
        };
        let Some(app_id) = normalized_app_id(app_id) else {
            continue;
        };
        let Some(options) = text(fields, "LaunchOptions") else {
            continue;
        };
        let options = options.trim();
        if options.is_empty() {
            continue;
        }
        recorded.insert(app_id, clamp_chars(options, MAX_LAUNCH_OPTIONS_CHARS));
    }
    recorded
}

/// `(profile_read, launch options by app id)`. The active account decides,
/// because that is whose options Steam will run; when `loginusers.vdf` cannot
/// name it, the profile carrying the most recorded state is the best evidence
/// of the account in use rather than a guess at a name.
fn scan_launch_options(
    roots: &[PathBuf],
    warnings: &mut usize,
) -> (bool, BTreeMap<String, String>) {
    let mut accounts: Vec<(String, BTreeMap<String, String>)> = Vec::new();
    for root in roots {
        let entries = match fs::read_dir(root.join("userdata")) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                *warnings += 1;
                continue;
            }
        };
        let mut directory_entries = 0;
        for entry in entries {
            directory_entries += 1;
            if directory_entries > MAX_DIRECTORY_ENTRIES {
                *warnings += 1;
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => {
                    *warnings += 1;
                    continue;
                }
            };
            let account = entry.file_name();
            let account = account.to_string_lossy();
            if !valid_account_id(&account) {
                continue;
            }
            let path = entry.path().join("config/localconfig.vdf");
            if !path.is_file() {
                continue;
            }
            if accounts.len() >= MAX_ACCOUNTS {
                *warnings += 1;
                break;
            }
            let Some(apps) = local_config_apps(&path, warnings) else {
                continue;
            };
            accounts.push((account.to_string(), recorded_launch_options(&apps)));
        }
    }
    if accounts.is_empty() {
        return (false, BTreeMap::new());
    }
    if let Some(recent) = roots.iter().find_map(|root| most_recent_account(root)) {
        if let Some((_, recorded)) = accounts.iter().find(|(account, _)| *account == recent) {
            return (true, recorded.clone());
        }
    }
    let mut best = BTreeMap::new();
    for (_, recorded) in &accounts {
        if recorded.len() > best.len() {
            best = recorded.clone();
        }
    }
    (true, best)
}

// --- Recently played ------------------------------------------------------
// Steam keeps each account's own play history in
// `userdata/<account id>/config/localconfig.vdf` (`LastPlayed` and
// `Playtime` in minutes). This read reports those values as Steam wrote
// them: no frame rate, no latency, no derived score. Steam names an app only
// in its manifest, so a title that is not installed here is counted rather
// than labelled with an invented name.

#[derive(Debug, Serialize)]
pub(crate) struct RecentGame {
    app_id: String,
    name: String,
    last_played: u64,
    playtime_minutes: Option<u64>,
}

#[derive(Debug, Serialize)]
pub(crate) struct RecentPlay {
    /// A Steam install directory was found at all.
    steam_detected: bool,
    /// At least one account's local profile was read successfully.
    profile_read: bool,
    warnings: usize,
    /// Recently played app ids with no local manifest, so no local title.
    unnamed: usize,
    games: Vec<RecentGame>,
}

/// Read at most `limit` bytes of a regular file, counting one warning on any
/// symlinked, oversized or unreadable path so a partial read stays visible
/// instead of looking like a clean empty result.
fn read_bounded_text(path: &Path, limit: u64, warnings: &mut usize) -> Option<String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            *warnings += 1;
            return None;
        }
    };
    if metadata.len() > limit {
        *warnings += 1;
        return None;
    }
    let mut source = String::new();
    if File::open(path)
        .and_then(|file| file.take(limit + 1).read_to_string(&mut source))
        .is_err()
        || source.len() as u64 > limit
    {
        *warnings += 1;
        return None;
    }
    Some(source)
}

fn local_config_apps(path: &Path, warnings: &mut usize) -> Option<Vec<(String, Value)>> {
    let source = read_bounded_text(path, MAX_LOCAL_CONFIG_BYTES, warnings)?;
    let parsed = match parse_vdf_bounded(&source, MAX_LOCAL_CONFIG_TOKENS) {
        Ok(parsed) => parsed,
        Err(_) => {
            *warnings += 1;
            return None;
        }
    };
    let Some(apps) = object(&parsed, "UserLocalConfigStore")
        .and_then(|store| object(store, "Software"))
        .and_then(|software| object(software, "Valve"))
        .and_then(|valve| object(valve, "Steam"))
        .and_then(|steam| object(steam, "apps"))
    else {
        *warnings += 1;
        return None;
    };
    Some(apps.to_vec())
}

/// One app's `apps` entry as (normalized app id, last played, playtime).
fn recent_entry(app_id: &str, fields: &[(String, Value)]) -> Option<(String, u64, Option<u64>)> {
    if app_id.is_empty() || !app_id.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let id = app_id.parse::<u32>().ok().filter(|id| *id > 0)?;
    let last_played = text(fields, "LastPlayed")?.parse::<u64>().ok()?;
    if last_played == 0 || last_played > MAX_LAST_PLAYED {
        return None;
    }
    // Steam's local profile writes minutes under "Playtime"; the Web API's
    // "playtime_forever" spelling appears in some builds, so both are read
    // rather than showing a played title with no playtime.
    let playtime_minutes = text(fields, "Playtime")
        .or_else(|| text(fields, "playtime_forever"))
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|minutes| *minutes <= MAX_PLAYTIME_MINUTES);
    Some((id.to_string(), last_played, playtime_minutes))
}

fn scan_recent_play(home: &Path) -> RecentPlay {
    let roots = steam_roots(home);
    if roots.is_empty() {
        return RecentPlay {
            steam_detected: false,
            profile_read: false,
            warnings: 0,
            unnamed: 0,
            games: Vec::new(),
        };
    }
    // Titles come from the manifests, not from a guess: reuse the same
    // bounded scan the library card uses.
    let names: BTreeMap<String, String> = scan_steam_libraries(home, false)
        .games
        .into_iter()
        .map(|game| (game.app_id, game.name))
        .collect();
    let mut warnings = 0;
    let mut profile_read = false;
    let mut accounts = 0;
    let mut merged: BTreeMap<String, (u64, Option<u64>)> = BTreeMap::new();
    for root in roots {
        let entries = match fs::read_dir(root.join("userdata")) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                warnings += 1;
                continue;
            }
        };
        let mut directory_entries = 0;
        for entry in entries {
            directory_entries += 1;
            if directory_entries > MAX_DIRECTORY_ENTRIES {
                warnings += 1;
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => {
                    warnings += 1;
                    continue;
                }
            };
            let account = entry.file_name();
            let account = account.to_string_lossy();
            if account.is_empty()
                || account.len() > 32
                || !account.bytes().all(|byte| byte.is_ascii_digit())
            {
                continue;
            }
            let path = entry.path().join("config/localconfig.vdf");
            if !path.is_file() {
                continue;
            }
            if accounts >= MAX_ACCOUNTS {
                warnings += 1;
                break;
            }
            accounts += 1;
            let Some(apps) = local_config_apps(&path, &mut warnings) else {
                continue;
            };
            profile_read = true;
            for (index, (app_id, value)) in apps.iter().enumerate() {
                if index >= MAX_RECENT_APPS {
                    warnings += 1;
                    break;
                }
                let Value::Object(fields) = value else {
                    continue;
                };
                let Some((app_id, last_played, playtime)) = recent_entry(app_id, fields) else {
                    continue;
                };
                merged
                    .entry(app_id)
                    .and_modify(|existing| {
                        if last_played > existing.0 {
                            existing.0 = last_played;
                        }
                        // `Option<u64>` orders `Some` above `None`, so this
                        // keeps a measured playtime over a missing one.
                        if playtime > existing.1 {
                            existing.1 = playtime;
                        }
                    })
                    .or_insert((last_played, playtime));
            }
        }
    }
    let mut unnamed = 0;
    let mut games = Vec::new();
    for (app_id, (last_played, playtime_minutes)) in merged {
        let Some(name) = names.get(&app_id) else {
            unnamed += 1;
            continue;
        };
        games.push(RecentGame {
            app_id,
            name: name.clone(),
            last_played,
            playtime_minutes,
        });
    }
    games.sort_by(|a, b| {
        b.last_played
            .cmp(&a.last_played)
            .then_with(|| a.app_id.cmp(&b.app_id))
    });
    games.truncate(RECENT_LIMIT);
    RecentPlay {
        steam_detected: true,
        profile_read,
        warnings,
        unnamed,
        games,
    }
}

#[tauri::command]
pub(crate) async fn rowyn_steam_recent_play() -> Result<RecentPlay, String> {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the current user's home directory.".to_string())?;
    tauri::async_runtime::spawn_blocking(move || scan_recent_play(&home))
        .await
        .map_err(|error| format!("Steam play history read did not complete: {error}"))
}

// --- Shader pre-caching ---------------------------------------------------
// Steam's Settings → Downloads → "Enable Shader Pre-Caching" toggle is kept in
// the global `config/config.vdf` — not the per-account profile — under
// `InstallConfigStore → Software → Valve → Steam → ShaderCacheManager` as
// `DisableShaderCache` ("0" enabled, "1" disabled). Steam writes that key only
// for a changed setting, so an absent key means Steam's own default (on)
// applies; it is reported as not recorded rather than guessed. This read never
// edits the file, starts Steam or measures anything.

#[derive(Debug, Serialize)]
pub(crate) struct ShaderCacheStatus {
    /// A Steam install directory was found at all.
    steam_detected: bool,
    /// A global config.vdf was read as Steam's own configuration.
    config_read: bool,
    /// None: Steam has not recorded the setting, so its default applies.
    pre_caching: Option<bool>,
    warnings: usize,
}

fn steam_config_section(config: &[(String, Value)]) -> Option<&[(String, Value)]> {
    object(config, "InstallConfigStore")
        .and_then(|store| object(store, "Software"))
        .and_then(|software| object(software, "Valve"))
        .and_then(|valve| object(valve, "Steam"))
}

/// Ok(None) when Steam wrote no ShaderCacheManager block; Err when the block
/// exists but its flag is not a value Steam writes.
fn pre_caching_flag(steam: &[(String, Value)]) -> Result<Option<bool>, ()> {
    let Some(manager) = object(steam, "ShaderCacheManager") else {
        return Ok(None);
    };
    match text(manager, "DisableShaderCache") {
        None => Ok(None),
        Some("0") => Ok(Some(true)),
        Some("1") => Ok(Some(false)),
        Some(_) => Err(()),
    }
}

fn read_shader_cache(home: &Path) -> ShaderCacheStatus {
    let roots = steam_roots(home);
    if roots.is_empty() {
        return ShaderCacheStatus {
            steam_detected: false,
            config_read: false,
            pre_caching: None,
            warnings: 0,
        };
    }
    let mut warnings = 0;
    let mut config_read = false;
    let mut pre_caching = None;
    for root in roots {
        let path = root.join("config/config.vdf");
        // A Steam location that never wrote a global configuration is not a
        // failed read; only an unreadable existing file is counted.
        if !path.is_file() {
            continue;
        }
        let Some(source) = read_bounded_text(&path, MAX_VDF_BYTES, &mut warnings) else {
            continue;
        };
        let parsed = match parse_vdf(&source) {
            Ok(parsed) => parsed,
            Err(_) => {
                warnings += 1;
                continue;
            }
        };
        let Some(steam) = steam_config_section(&parsed) else {
            warnings += 1;
            continue;
        };
        config_read = true;
        match pre_caching_flag(steam) {
            Ok(Some(value)) => {
                pre_caching = Some(value);
                break;
            }
            Ok(None) => {}
            Err(_) => warnings += 1,
        }
    }
    ShaderCacheStatus {
        steam_detected: true,
        config_read,
        pre_caching,
        warnings,
    }
}

#[tauri::command]
pub(crate) async fn rowyn_steam_shader_cache() -> Result<ShaderCacheStatus, String> {
    let home = home_dir()?;
    tauri::async_runtime::spawn_blocking(move || read_shader_cache(&home))
        .await
        .map_err(|error| format!("Shader pre-caching read did not complete: {error}"))
}

// --- MangoHud config path -------------------------------------------------
// Flathub Steam reads MangoHud's settings inside its sandbox from
// `~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf`, not the
// `~/.config/MangoHud/MangoHud.conf` most guides name. A host-native MangoHud
// reads `$XDG_CONFIG_HOME/MangoHud/MangoHud.conf` when that variable is an
// absolute path, and the home `.config` path otherwise. This read reports both
// paths (home-relative when they are under home), whether each config exists,
// and how many settings it holds. It never creates, edits or backs up either
// file: a missing config is reported as missing, and the page only offers text
// to copy.

const MANGOHUD_FLATPAK_PATH: &str =
    ".var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf";
const MANGOHUD_NATIVE_PATH: &str = ".config/MangoHud/MangoHud.conf";
const MANGOHUD_CONFIG_FILE: &str = "MangoHud/MangoHud.conf";
const MAX_MANGOHUD_BYTES: u64 = 64 * 1024;

#[derive(Debug, Serialize)]
pub(crate) struct MangohudConfig {
    /// A Steam install was found in a common native or Flatpak location.
    steam_detected: bool,
    /// Flathub Steam's own root exists, which is the boundary this path serves.
    flatpak_steam: bool,
    /// Home-relative, so no absolute home path reaches the page.
    path: &'static str,
    exists: bool,
    readable: bool,
    /// Non-empty, non-comment lines in the existing config.
    settings: usize,
    /// The host path MangoHud reads outside Flathub Steam, resolved from
    /// `$XDG_CONFIG_HOME` when it is an absolute path. `~/…` when under home.
    native_path: String,
    native_exists: bool,
    native_readable: bool,
    native_settings: usize,
    warnings: usize,
}

fn count_mangohud_settings(text: &str) -> usize {
    text.lines()
        .filter(|line| {
            let line = line.trim();
            !line.is_empty() && !line.starts_with('#')
        })
        .count()
}

/// (exists, readable, settings) for one config path. A symlinked or
/// non-regular file counts as present but unread, and a warning keeps that
/// visible instead of reading as an empty config.
fn mangohud_file(path: &Path, warnings: &mut usize) -> (bool, bool, usize) {
    if fs::symlink_metadata(path).is_err() {
        return (false, false, 0);
    }
    match read_bounded_text(path, MAX_MANGOHUD_BYTES, warnings) {
        Some(text) => (true, true, count_mangohud_settings(&text)),
        None => (true, false, 0),
    }
}

/// The host config this boundary actually reads, and a display string that
/// keeps a home path relative: `~/…` under home, the absolute path otherwise.
fn mangohud_native_config(
    home: &Path,
    xdg_config_home: Option<&Path>,
) -> (std::path::PathBuf, String) {
    let absolute = xdg_config_home
        .filter(|dir| dir.is_absolute())
        .map(|dir| dir.join(MANGOHUD_CONFIG_FILE))
        .unwrap_or_else(|| home.join(MANGOHUD_NATIVE_PATH));
    let display = absolute
        .strip_prefix(home)
        .map(|relative| format!("~/{}", relative.display()))
        .unwrap_or_else(|_| absolute.to_string_lossy().into_owned());
    (absolute, display)
}

fn read_mangohud_config_at(
    home: &Path,
    xdg_config_home: Option<&Path>,
) -> MangohudConfig {
    let mut warnings = 0;
    let (exists, readable, settings) = mangohud_file(
        &home.join(MANGOHUD_FLATPAK_PATH),
        &mut warnings,
    );
    let (native_absolute, native_path) = mangohud_native_config(home, xdg_config_home);
    let (native_exists, native_readable, native_settings) =
        mangohud_file(&native_absolute, &mut warnings);
    MangohudConfig {
        steam_detected: !steam_roots(home).is_empty(),
        flatpak_steam: home.join(".var/app/com.valvesoftware.Steam").is_dir(),
        path: MANGOHUD_FLATPAK_PATH,
        exists,
        readable,
        settings,
        native_path,
        native_exists,
        native_readable,
        native_settings,
        warnings,
    }
}

/// Command entry point: the host config path follows XDG_CONFIG_HOME when the
/// environment sets an absolute one.
fn read_mangohud_config(home: &Path) -> MangohudConfig {
    let xdg = std::env::var_os("XDG_CONFIG_HOME").map(std::path::PathBuf::from);
    read_mangohud_config_at(home, xdg.as_deref())
}

#[tauri::command]
pub(crate) async fn rowyn_mangohud_config() -> Result<MangohudConfig, String> {
    let home = home_dir()?;
    tauri::async_runtime::spawn_blocking(move || read_mangohud_config(&home))
        .await
        .map_err(|error| format!("MangoHud config read did not complete: {error}"))
}

// --- Steam Play default mapping -------------------------------------------
// The shared reader (system::gaming_compat) deliberately never writes VDF:
// editing config.vdf while Steam runs corrupts it, because Steam rewrites
// the file as it exits. This writer is the one bounded exception, and it
// keeps the reader's caution: it refuses while Steam is running, edits by
// byte splice so everything outside the CompatToolMapping block stays
// byte-identical, keeps the first pre-edit backup, writes atomically, and
// fails closed — guiding the user to Steam's own setting — on any file
// whose structure it does not fully recognize.

#[derive(Debug, Clone, PartialEq)]
enum SpanKind {
    Text,
    Open,
    Close,
}

#[derive(Debug, Clone)]
struct SpanToken {
    kind: SpanKind,
    start: usize,
    end: usize,
    text: String,
}

fn tokenize_spans(input: &str) -> Result<Vec<SpanToken>, String> {
    let bytes = input.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            byte if byte.is_ascii_whitespace() => index += 1,
            b'{' => {
                tokens.push(SpanToken { kind: SpanKind::Open, start: index, end: index + 1, text: String::new() });
                index += 1;
            }
            b'}' => {
                tokens.push(SpanToken { kind: SpanKind::Close, start: index, end: index + 1, text: String::new() });
                index += 1;
            }
            b'/' if bytes.get(index + 1) == Some(&b'/') => {
                while index < bytes.len() && bytes[index] != b'\n' {
                    index += 1;
                }
            }
            b'"' => {
                let start = index;
                index += 1;
                let mut value = Vec::new();
                let mut closed = false;
                while index < bytes.len() {
                    match bytes[index] {
                        b'"' => {
                            index += 1;
                            closed = true;
                            break;
                        }
                        b'\\' if index + 1 < bytes.len() => {
                            index += 1;
                            value.push(bytes[index]);
                            index += 1;
                        }
                        byte => {
                            value.push(byte);
                            index += 1;
                        }
                    }
                }
                if !closed {
                    return Err("unterminated quoted value".into());
                }
                tokens.push(SpanToken {
                    kind: SpanKind::Text,
                    start,
                    end: index,
                    text: String::from_utf8_lossy(&value).into_owned(),
                });
            }
            _ => {
                let start = index;
                while index < bytes.len()
                    && !bytes[index].is_ascii_whitespace()
                    && !matches!(bytes[index], b'{' | b'}')
                {
                    index += 1;
                }
                if start == index {
                    return Err("invalid token".into());
                }
                tokens.push(SpanToken {
                    kind: SpanKind::Text,
                    start,
                    end: index,
                    text: String::from_utf8_lossy(&bytes[start..index]).into_owned(),
                });
            }
        }
        if tokens.len() > MAX_TOKENS {
            return Err("VDF has too many entries".into());
        }
    }
    Ok(tokens)
}

/// (key, open, close) token indexes of the direct child block named `key`
/// within tokens[start..limit], or None. Scalars and deeper blocks are
/// skipped structurally; malformed shapes yield None (fail closed).
fn child_block(tokens: &[SpanToken], start: usize, limit: usize, key: &str) -> Option<(usize, usize, usize)> {
    let mut index = start;
    while index < limit {
        match tokens[index].kind {
            SpanKind::Text => {
                let next = tokens.get(index + 1)?;
                match next.kind {
                    SpanKind::Open => {
                        let mut depth = 0usize;
                        let mut close = index + 1;
                        loop {
                            match tokens.get(close)?.kind {
                                SpanKind::Open => depth += 1,
                                SpanKind::Close => {
                                    depth -= 1;
                                    if depth == 0 {
                                        break;
                                    }
                                }
                                SpanKind::Text => {}
                            }
                            close += 1;
                            if close > limit {
                                return None;
                            }
                        }
                        if tokens[index].text.eq_ignore_ascii_case(key) {
                            return Some((index, index + 1, close));
                        }
                        index = close + 1;
                    }
                    SpanKind::Text => index += 2,
                    SpanKind::Close => return None,
                }
            }
            _ => return None,
        }
    }
    None
}

fn child_scalar<'a>(tokens: &'a [SpanToken], start: usize, limit: usize, key: &str) -> Option<&'a str> {
    let mut index = start;
    while index < limit {
        if tokens[index].kind != SpanKind::Text {
            return None;
        }
        let next = tokens.get(index + 1)?;
        match next.kind {
            SpanKind::Text => {
                if tokens[index].text.eq_ignore_ascii_case(key) {
                    return Some(next.text.as_str());
                }
                index += 2;
            }
            SpanKind::Open => {
                let (_, _, close) = child_block(tokens, index, limit, &tokens[index].text.clone())?;
                index = close + 1;
            }
            SpanKind::Close => return None,
        }
    }
    None
}

fn steam_block(tokens: &[SpanToken]) -> Option<(usize, usize, usize)> {
    let (_, open, close) = child_block(tokens, 0, tokens.len(), "InstallConfigStore")?;
    let (_, open, close) = child_block(tokens, open + 1, close, "Software")?;
    let (_, open, close) = child_block(tokens, open + 1, close, "Valve")?;
    child_block(tokens, open + 1, close, "Steam")
}

/// The tool named by the global ("0") CompatToolMapping entry, if any.
fn compat_default_tool(input: &str) -> Option<String> {
    let tokens = tokenize_spans(input).ok()?;
    let (_, open, close) = steam_block(&tokens)?;
    let (_, map_open, map_close) = child_block(&tokens, open + 1, close, "CompatToolMapping")?;
    let (_, zero_open, zero_close) = child_block(&tokens, map_open + 1, map_close, "0")?;
    child_scalar(&tokens, zero_open + 1, zero_close, "name").map(str::to_string)
}

/// The compat tool Steam will use for one title: its own CompatToolMapping
/// entry, else the global ("0") default. `None` when Steam records neither,
/// which is the native-Linux case.
fn compat_tool_in(tokens: &[SpanToken], app_id: &str) -> Option<String> {
    let (_, open, close) = steam_block(tokens)?;
    let (_, map_open, map_close) = child_block(tokens, open + 1, close, "CompatToolMapping")?;
    for id in [app_id, "0"] {
        let Some((_, entry_open, entry_close)) = child_block(tokens, map_open + 1, map_close, id)
        else {
            continue;
        };
        if let Some(name) = child_scalar(tokens, entry_open + 1, entry_close, "name") {
            return Some(name.to_string());
        }
    }
    None
}

#[cfg(test)]
fn compat_tool_for(input: &str, app_id: &str) -> Option<String> {
    let tokens = tokenize_spans(input).ok()?;
    compat_tool_in(&tokens, app_id)
}

/// Proton and the Steam Linux Runtime both start the game inside a
/// pressure-vessel container with its own /usr. GameMode's client library is
/// not there and the container refuses host /usr shares, so the preloaded
/// client cannot register the game (ValveSoftware/steam-runtime#814).
fn launches_in_container(tool: &str) -> bool {
    let tool = tool.to_ascii_lowercase();
    tool.contains("proton")
        || tool.contains("steamlinuxruntime")
        || tool.contains("steam linux runtime")
}

fn line_indent(input: &str, byte: usize) -> String {
    let start = input[..byte].rfind('\n').map(|found| found + 1).unwrap_or(0);
    let prefix = &input[start..byte];
    if prefix.chars().all(|c| c == ' ' || c == '\t') {
        prefix.to_string()
    } else {
        String::new()
    }
}

fn render_default_entry(indent: &str) -> String {
    format!(
        "{indent}\"0\"\n{indent}{{\n{indent}\t\"name\"\t\t\"proton_experimental\"\n{indent}\t\"config\"\t\t\"\"\n{indent}\t\"priority\"\t\t\"75\"\n{indent}}}"
    )
}

/// Return `input` with the global CompatToolMapping default set to Proton
/// Experimental, or an error describing why the file was left untouched.
fn apply_default_compat_mapping(input: &str) -> Result<String, String> {
    if compat_default_tool(input).as_deref() == Some("proton_experimental") {
        return Ok(input.to_string());
    }
    // Fail closed on anything the strict parser cannot read end to end.
    parse_vdf(input)?;
    let tokens = tokenize_spans(input)?;
    let (_, steam_open, steam_close) = steam_block(&tokens)
        .ok_or("Steam's section was not found in config.vdf; enable Steam Play in Steam → Settings → Compatibility instead.")?;
    let edited = match child_block(&tokens, steam_open + 1, steam_close, "CompatToolMapping") {
        Some((map_key, map_open, map_close)) => {
            match child_block(&tokens, map_open + 1, map_close, "0") {
                Some((zero_key, _, zero_close)) => {
                    let indent = line_indent(input, tokens[zero_key].start);
                    let mut out = String::with_capacity(input.len());
                    out.push_str(&input[..tokens[zero_key].start]);
                    out.push_str(&render_default_entry(&indent));
                    out.push_str(&input[tokens[zero_close].end..]);
                    out
                }
                None => {
                    // A scalar "0" is not a shape Steam writes; refuse it.
                    if child_scalar(&tokens, map_open + 1, map_close, "0").is_some() {
                        return Err("The existing default compatibility entry has an unexpected shape; enable Steam Play in Steam → Settings → Compatibility instead.".into());
                    }
                    let indent = format!("{}\t", line_indent(input, tokens[map_key].start));
                    let mut out = String::with_capacity(input.len() + 128);
                    out.push_str(&input[..tokens[map_open].end]);
                    out.push('\n');
                    out.push_str(&render_default_entry(&indent));
                    out.push_str(&input[tokens[map_open].end..]);
                    out
                }
            }
        }
        None => {
            let indent = format!("{}\t", line_indent(input, tokens[steam_open - 1].start));
            let mapping = format!(
                "{indent}\"CompatToolMapping\"\n{indent}{{\n{}\n{indent}}}",
                render_default_entry(&format!("{indent}\t"))
            );
            let mut out = String::with_capacity(input.len() + 256);
            out.push_str(&input[..tokens[steam_open].end]);
            out.push('\n');
            out.push_str(&mapping);
            out.push_str(&input[tokens[steam_open].end..]);
            out
        }
    };
    if compat_default_tool(&edited).as_deref() != Some("proton_experimental") {
        return Err("The edited configuration did not verify; config.vdf was left unchanged.".into());
    }
    parse_vdf(&edited)?;
    Ok(edited)
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the current user's home directory.".to_string())
}

#[tauri::command]
pub(crate) async fn rowyn_steam_play_status(
) -> Result<rowyn_shared::system::gaming_compat::SteamPlayStatus, String> {
    let home = home_dir()?;
    tauri::async_runtime::spawn_blocking(move || {
        Ok(rowyn_shared::system::gaming_compat::steam_play_status(&home))
    })
    .await
    .map_err(|error| format!("Steam Play status did not complete: {error}"))?
}

#[tauri::command]
pub(crate) async fn rowyn_steam_play_apply(
) -> Result<rowyn_shared::system::gaming_compat::SteamPlayStatus, String> {
    let home = home_dir()?;
    tauri::async_runtime::spawn_blocking(move || {
        use rowyn_shared::system::gaming_compat as compat;
        if compat::steam_running() {
            return Err("Steam is running. Quit Steam completely first — Steam rewrites config.vdf as it exits, which would undo or corrupt the change.".into());
        }
        let vdf = compat::steam_config_vdf(&home);
        if !vdf.exists() {
            return Err("The Flatpak Steam installation was not found. Install Steam from the Play page first.".into());
        }
        let metadata = fs::symlink_metadata(&vdf)
            .map_err(|error| format!("Could not inspect Steam's configuration: {error}"))?;
        if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 1024 * 1024 {
            return Err("Steam's configuration file has an unexpected shape; enable Steam Play in Steam → Settings → Compatibility instead.".into());
        }
        let text = fs::read_to_string(&vdf)
            .map_err(|error| format!("Could not read Steam's configuration: {error}"))?;
        let updated = apply_default_compat_mapping(&text)?;
        if updated != text {
            if compat::steam_running() {
                return Err("Steam started while the change was being prepared. Quit Steam and try again; nothing was written.".into());
            }
            let backup = vdf.with_extension("vdf.rowyn-backup");
            if !backup.exists() {
                fs::write(&backup, &text)
                    .map_err(|error| format!("Could not back up Steam's configuration: {error}"))?;
            }
            let temp = vdf.with_extension("vdf.rowyn-tmp");
            fs::write(&temp, &updated)
                .map_err(|error| format!("Could not write Steam's configuration: {error}"))?;
            fs::rename(&temp, &vdf)
                .map_err(|error| format!("Could not replace Steam's configuration: {error}"))?;
        }
        Ok(compat::steam_play_status(&home))
    })
    .await
    .map_err(|error| format!("Steam Play change did not complete: {error}"))?
}

#[cfg(test)]
#[path = "rowyn_steam_tests.rs"]
mod tests;
