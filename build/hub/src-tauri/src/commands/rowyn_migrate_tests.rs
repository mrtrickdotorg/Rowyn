use super::*;
use std::fs;

fn windows_tree(base: &Path) -> PathBuf {
    let root = base.join("win");
    fs::create_dir_all(root.join("Windows")).unwrap();
    let profile = root.join("Users").join("Pat");
    for dir in ["Documents", "Pictures", "Desktop", "Downloads"] {
        fs::create_dir_all(profile.join(dir)).unwrap();
    }
    fs::create_dir_all(root.join("Users").join("Public")).unwrap();
    fs::write(profile.join("Documents").join("taxes.txt"), b"1040").unwrap();
    fs::write(profile.join("Documents").join("notes.txt"), b"hello").unwrap();
    fs::write(profile.join("Pictures").join("cat.jpg"), b"\xff\xd8").unwrap();
    root
}

#[test]
fn lsblk_parse_keeps_only_ntfs_with_children() {
    let json = r#"{"blockdevices":[
      {"name":"sda","fstype":null,"mountpoint":null,"children":[
        {"name":"sda1","fstype":"vfat","mountpoint":"/boot/efi"},
        {"name":"sda2","fstype":"ntfs","mountpoint":null},
        {"name":"sda3","fstype":"ntfs","mountpoint":"/run/media/u/WIN"}]},
      {"name":"nvme0n1p1","fstype":"ext4","mountpoint":"/"}]}"#;
    let partitions = ntfs_partitions(json);
    assert_eq!(partitions.len(), 2);
    assert_eq!(partitions[0].device, "/dev/sda2");
    assert_eq!(partitions[0].mountpoint, None);
    assert_eq!(partitions[1].mountpoint.as_deref(), Some("/run/media/u/WIN"));
    assert!(ntfs_partitions("not json").is_empty());
}

#[test]
fn profiles_detected_and_system_dirs_excluded() {
    let temp = tempfile::tempdir().unwrap();
    let root = windows_tree(temp.path());
    let profiles = windows_profiles(&root).unwrap();
    assert_eq!(profiles, vec!["Pat".to_string()]);
    // A plain data NTFS root is not a Windows installation.
    let plain = temp.path().join("plain");
    fs::create_dir_all(plain.join("Users")).unwrap();
    assert!(windows_profiles(&plain).is_none());
}

#[test]
fn import_copies_categories_and_never_overwrites() {
    let temp = tempfile::tempdir().unwrap();
    let root = windows_tree(temp.path());
    let home = temp.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let report = run_import(
        &root,
        "Pat",
        &["documents".into(), "pictures".into()],
        &home,
    )
    .unwrap();
    assert_eq!(report.categories[0].copied_files, 2);
    assert_eq!(report.categories[1].copied_files, 1);
    let copied = home.join("Documents").join("From Windows").join("taxes.txt");
    assert_eq!(fs::read(&copied).unwrap(), b"1040");
    // Second run: everything already exists -> all skipped, no failures.
    let again = run_import(&root, "Pat", &["documents".into()], &home).unwrap();
    assert_eq!(again.categories[0].copied_files, 0);
    assert_eq!(again.categories[0].skipped, 2);
    assert_eq!(again.categories[0].failed, 0);
}

#[test]
fn import_refuses_unknown_profile_and_category() {
    let temp = tempfile::tempdir().unwrap();
    let root = windows_tree(temp.path());
    let home = temp.path().join("home");
    fs::create_dir_all(&home).unwrap();
    assert!(run_import(&root, "../Pat", &["documents".into()], &home).is_err());
    assert!(run_import(&root, "Nobody", &["documents".into()], &home).is_err());
    assert!(run_import(&root, "Pat", &["everything".into()], &home).is_err());
}

#[test]
fn chrome_bookmarks_convert_to_importable_html() {
    let json = r#"{"roots":{"bookmark_bar":{"type":"folder","name":"Bookmarks bar","children":[
      {"type":"url","name":"Example & <Co>","url":"https://example.com/?a=1&b=2"},
      {"type":"folder","name":"Work","children":[
        {"type":"url","name":"Docs","url":"https://docs.example.com/"}]}]},
      "other":{"type":"folder","name":"Other","children":[]}}}"#;
    let html = chrome_bookmarks_to_html(json).unwrap();
    assert!(html.contains("NETSCAPE-Bookmark-file-1"));
    assert!(html.contains("Example &amp; &lt;Co&gt;"));
    assert!(html.contains("https://example.com/?a=1&amp;b=2"));
    assert!(html.contains("<H3>Work</H3>"));
    assert!(chrome_bookmarks_to_html("{broken").is_err());
    assert!(chrome_bookmarks_to_html("{}").is_err());
}

#[test]
fn bookmarks_and_wallpaper_import_from_fixture_tree() {
    let temp = tempfile::tempdir().unwrap();
    let root = windows_tree(temp.path());
    let profile = root.join("Users").join("Pat");
    let chrome = profile.join("AppData/Local/Google/Chrome/User Data/Default");
    fs::create_dir_all(&chrome).unwrap();
    fs::write(
        chrome.join("Bookmarks"),
        r#"{"roots":{"bookmark_bar":{"type":"folder","name":"bar","children":[{"type":"url","name":"X","url":"https://x.example/"}]}}}"#,
    )
    .unwrap();
    let themes = profile.join("AppData/Roaming/Microsoft/Windows/Themes");
    fs::create_dir_all(&themes).unwrap();
    fs::write(themes.join("TranscodedWallpaper"), b"\xff\xd8wallpaper").unwrap();
    let home = temp.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let report = run_import(&root, "Pat", &["bookmarks".into(), "wallpaper".into()], &home).unwrap();
    assert_eq!(report.categories[0].copied_files, 1);
    let html = fs::read_to_string(home.join("Documents/Bookmarks/Chrome-bookmarks.html")).unwrap();
    assert!(html.contains("https://x.example/"));
    assert_eq!(report.categories[1].copied_files, 1);
    assert!(home.join("Pictures/windows-wallpaper.jpg").exists());
}
