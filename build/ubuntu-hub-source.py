#!/usr/bin/env python3
"""Export the pinned upstream System Hub source, then port it to Rowyn on Ubuntu.

The port is deliberately scoped: Home/Dashboard, Apps, Updates and VPN
survive upstream. This PC, Move In, the legacy Play and shell-only Dev Tools
surfaces are cut. Small checked-in build/hub overlays add Rowyn-owned
read-only Diagnostics and Play MVP routes plus a dedicated Development workspace without restoring legacy helpers;
unreferenced upstream page files remain typecheckable. The Updates channel is repointed at
Rowyn's own releases and GHCR repository, and the update helpers the Hub
shells out to are repointed at the Rowyn implementations the payload
ships (shared/rowyn-safe-upgrade.sh, shared/rowyn-bootc-guard.sh,
shared/rowyn-finalize-staged.sh — fixed-operation counterparts of the upstream
Rust helpers, with a passwordless sudoers allowlist). Branch switching
stays registered in Rust; the guard it calls implements only Rowyn's
single stable channel and refuses the upstream's other channels.

The VPN destination is the upstream openconnect client (GlobalProtect "gp"
profile, SAML redirect/cookie/reconnect flow); it ports unchanged apart
from its on-disk paths, which take the Rowyn name. The payload image
provides its runtime stack — openconnect, vpnc-scripts, ksshaskpass (the
askpass the backend fails closed without), and firewalld (the lockdown
feature's one firewall-cmd call) — see the Containerfile.

A final branding sweep rewrites every remaining upstream brand string
in the tree — update status copy, service-layer error prompts, dialog
text, exe-handler suggestion data, and the focus-session inhibit reason
(with its matcher in the shared crate kept in step) — and rekeys the
boot-health OS identity check in updates.rs to Rowyn's os-release ID
(ID=rowyn; the upstream check keyed to ID=kythos would flag every Rowyn
system unhealthy). The sweep is mechanical (counted replace-alls per
file) so a new upstream string fails the port closed in the branding
regression test instead of shipping under the wrong name. Provenance
data that legitimately names the upstream system
(src/data/compat_games.json, whose notes record where a compatibility
verdict came from) is not rewritten.

Never executes upstream build scripts. The input checkout is read-only; git
archive exports exactly the pinned objects.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
# The PATHS allowlist below names the upstream layout (the pinned
# repo's own directory names, needed to address git archive). On the
# way into the export the directories are renamed to Rowyn's layout,
# so every path the rest of this port — and every Rowyn build —
# touches carries the Rowyn name.
EXPORT_DIR_RENAMES = (
    ("src/kyth-hub-web", "src/rowyn-hub-web"),
    ("src/kyth-shared-rs", "src/rowyn-shared-rs"),
)


def export_member_path(name):
    for old, new in EXPORT_DIR_RENAMES:
        if name == old or name.startswith(old + "/"):
            return new + name[len(old):]
    return name
PATHS = ("src/kyth-hub-web", "src/kyth-shared-rs",
         "third_party/gtk-glib-0.18.5", "build_files/exe-handler-apps.json",
         "LICENSE")
KEPT_DESTINATIONS = ("Apps", "Updates", "VPN")
EXPECTED_DESTINATIONS = {"Play", "Apps", "This PC", "Move In", "VPN", "Updates"}

# --- Legacy junk cut (approved by Patrick, Oct 30, 2026) -------------------
# The audit counted what the two ports pull over from the upstream tree: the shared
# crate arrives whole (304 .rs files, ~78k lines) although the two shells
# between them reach only a minority of its modules, and 92 helper-binary
# targets are exported although neither image build compiles any of them.
# The cut below removes, from the exported tree: the helper binaries and
# their manifest sections, the unused library modules (with the whole
# runtime-updater/PGP heritage), the pgp dependency that only the updater
# manifest verifier used, and — on the Hub side — the gaming/dev-tools
# command modules, the `just` recipe runner, and the privileged-socket
# client for a root daemon Rowyn does not ship. The keep set is the
# reviewed reachability closure over the post-cut shells; a module that
# is not listed is dropped with its declaration, so upstream additions
# fail the port closed instead of riding along unreviewed.
SHARED_CRATE_BIN_FILES = 92
SHARED_CRATE_BIN_SECTIONS = 91
SHARED_CRATE_KEEP_MODULES = (
    "atomic_io",
    "commands",
    "diagnostic_report",
    "diagnostics_scrub",
    "system",
    "system::account",
    "system::app_suggestions",
    "system::boot_health",
    "system::boot_runtime",
    "system::bootc",
    "system::bootc_policy",
    "system::bootc_query",
    "system::btrfs_status",
    "system::cloud_oauth",
    "system::controllers",
    "system::deployment_history",
    "system::desktop_stack",
    "system::drivers",
    "system::drives",
    "system::exe_trust",
    "system::firmware",
    "system::firstboot",
    "system::fonts_ready",
    "system::gaming_compat",
    "system::gaming_library",
    "system::gaming_slice",
    "system::gpu",
    "system::hardware_policy",
    "system::hardware_view",
    "system::installer_validation",
    "system::jobs",
    "system::memory_pressure",
    "system::mesa_version",
    "system::mok_verify",
    "system::network_identity",
    "system::network_preset",
    "system::pipewire",
    "system::plasma_hdr",
    "system::printing",
    "system::probe",
    "system::process",
    "system::recovery_status",
    "system::registry",
    "system::runtime_output",
    "system::security_container",
    "system::smb",
    "system::snapshot",
    "system::software_catalog",
    "system::storage",
    "system::telemetry",
    "system::update_availability",
    "system::update_status",
    "system::updates_unified",
    "system::vpn_saml",
    "system::windows_installer",
    "system::windows_verify",
    "transfer",
    "url_encode",
)


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old[:100]!r}")
    return text.replace(old, new)


def replace_all_counted(text, old, new):
    """Replace every occurrence, failing closed when the anchor is absent.

    Returns (text, count) so the port can report exactly what it rewrote in
    production code and in the upstream test fixtures that share the file.
    """
    count = text.count(old)
    if count == 0:
        raise ValueError(f"expected at least one source anchor: {old[:100]!r}")
    return text.replace(old, new), count


def cut_between(text, start, end):
    """Delete [start, end) — start inclusive, end exclusive — failing closed.

    Both anchors must occur exactly once and in order, so a cut that spans a
    large JSX/Rust block cannot silently widen or vanish when upstream moves.
    """
    if text.count(start) != 1:
        raise ValueError(f"expected exactly one cut start anchor: {start[:100]!r}")
    if text.count(end) != 1:
        raise ValueError(f"expected exactly one cut end anchor: {end[:100]!r}")
    begin = text.index(start)
    finish = text.index(end)
    if finish <= begin:
        raise ValueError(f"cut end anchor precedes its start: {start[:60]!r} .. {end[:60]!r}")
    return text[:begin] + text[finish:]


def strip_shared_crate_bins(root):
    """Delete the shared crate's helper-binary sources and their [[bin]]
    manifest sections. Neither image build compiles them (the payload
    builds only the shell package; the ISO names its three installer bins
    explicitly), and a [[bin]] section naming a deleted file — or a file
    left behind with no section — would break the crate's manifest, so
    the two leave together, counted."""
    crate = root / "src/rowyn-shared-rs"
    bins = sorted((crate / "src").glob("*_bin.rs"))
    if len(bins) != SHARED_CRATE_BIN_FILES:
        raise ValueError(
            f"expected {SHARED_CRATE_BIN_FILES} shared-crate bin sources, found {len(bins)}")
    manifest_path = crate / "Cargo.toml"
    lines = manifest_path.read_text().splitlines(keepends=True)
    kept, sections, index = [], 0, 0
    while index < len(lines):
        if lines[index].rstrip() == "[[bin]]":
            sections += 1
            index += 1
            while index < len(lines) and not lines[index].startswith("["):
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    if sections != SHARED_CRATE_BIN_SECTIONS:
        raise ValueError(
            f"expected {SHARED_CRATE_BIN_SECTIONS} [[bin]] sections, found {sections}")
    manifest_path.write_text("".join(kept))
    for path in bins:
        path.unlink()
    return f"{len(bins)} bin sources + {sections} [[bin]] sections"


def prune_shared_crate(root):
    """Drop the shared crate's unreviewed library modules and the pgp
    dependency. A module survives only if it is in
    SHARED_CRATE_KEEP_MODULES (the shells' reachability closure); its
    file and its `pub mod` declaration leave together, and the manifest
    loses the rPGP dependency only the pruned updater-manifest verifier
    used. Fails closed when a kept module is missing upstream or the
    declared set afterwards is not exactly the keep set."""
    import re as _re
    crate = root / "src/rowyn-shared-rs"
    manifest_path = crate / "Cargo.toml"
    text = manifest_path.read_text()
    text = replace_once(
        text,
        "# OpenPGP verification for the KythOS-signed updater manifest (see\n"
        "# system/update_manifest.rs). rPGP: pure Rust, so the same code builds\n"
        "# on the dev VM, in CI, and in the Fedora image with no system crypto\n"
        "# libraries. Verify-only use — nothing here can sign.\n"
        "pgp = \"0.19\"\n",
        "")
    manifest_path.write_text(text)

    keep = set(SHARED_CRATE_KEEP_MODULES)
    dropped = []
    for modfile, prefix in ((crate / "src/lib.rs", ""), (crate / "src/system/mod.rs", "system::")):
        lines = modfile.read_text().splitlines(keepends=True)
        declared = {}
        for index, line in enumerate(lines):
            match = _re.fullmatch(r"pub mod ([a-z0-9_]+);", line.rstrip("\n"))
            if match:
                declared[prefix + match.group(1)] = index
        expected_here = {name for name in keep
                         if name.startswith(prefix) and "::" not in name[len(prefix):]}
        missing = expected_here - set(declared)
        if missing:
            raise ValueError(f"kept modules missing from {modfile.name}: {sorted(missing)}")
        remove = set()
        for dotted, index in declared.items():
            if dotted in keep:
                continue
            remove.add(index)
            if index > 0 and lines[index - 1].startswith("#[cfg("):
                remove.add(index - 1)
            dropped.append(dotted)
            name = dotted.rsplit("::", 1)[-1]
            path = (crate / "src" / f"{name}.rs") if not prefix else (crate / "src/system" / f"{name}.rs")
            if not path.is_file():
                raise ValueError(f"module file missing for {dotted}: {path}")
            path.unlink()
        modfile.write_text("".join(
            line for index, line in enumerate(lines) if index not in remove))
    remaining = sorted(
        str(path.relative_to(crate / "src"))
        for path in (crate / "src").rglob("*.rs"))
    expected = sorted(
        ["lib.rs", "system/mod.rs"]
        + [f"{name}.rs" for name in keep if "::" not in name and name != "system"]
        + [f"system/{name.rsplit('::', 1)[-1]}.rs" for name in keep if name.startswith("system::")])
    if remaining != expected:
        raise ValueError(
            f"shared-crate tree after prune is not the keep set: {remaining} != {expected}")
    return f"{len(dropped)} modules + pgp dependency"


def sync_cargo_lock(crate, absent_packages):
    """Re-sync a shell crate's Cargo.lock with cargo itself after a
    manifest dependency removal, then assert the removed packages are
    gone. A --locked build rejects any lock cargo would rewrite, and the
    exact rewrite (orphan pruning plus multi-version reference
    normalization) is feature-resolution semantics only cargo owns —
    a past Python re-implementation kept optional dependencies that the
    removed crate's features had activated. `cargo fetch` performs the
    conservative sync: locked versions are preserved, only the removed
    subtrees leave. Fails closed when cargo is unavailable or errors."""
    import shutil
    if shutil.which("cargo") is None:
        raise ValueError("cargo is required to re-sync Cargo.lock after a dependency removal")
    result = subprocess.run(
        ["cargo", "fetch"], cwd=crate, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(
            f"cargo fetch failed to re-sync {crate}/Cargo.lock: {result.stderr[-400:]}")
    lock = (crate / "Cargo.lock").read_text()
    present = set()
    for block in lock.split("[[package]]")[1:]:
        for line in block.splitlines():
            if line.startswith('name = "'):
                present.add(line.split('"')[1])
                break
    survivors = sorted(set(absent_packages) & present)
    if survivors:
        raise ValueError(f"packages still in Cargo.lock after sync: {survivors}")
    return f"{len(present)} packages remain"


# --- Full internal rename (approved by Patrick, Oct 31, 2026) --------------
# The passes above rebrand user-visible copy and repoint the helpers
# Rowyn ships; this pass renames what remains internal -- the shared
# crate's package name and every `kyth_shared::` path, on-disk data
# paths (telemetry, probe cache, the exe-handler app database), the
# stage-progress wire token the safe-upgrade helper emits, env
# vars, and the injected SAML script's identifiers -- with one uniform
# map, so both ends of every renamed contract move together. The
# exported directories already took the Rowyn name at extraction
# (EXPORT_DIR_RENAMES). Three classes of literal are masked out, each
# an external name the code must keep matching rather than a name
# Rowyn chose:
#   * upstream dependency paths under kyth-os/... remain external; Rowyn-owned
#     repository and registry references are repointed to mrtrickdotorg first;
#   * "KythOS Secure Boot": the certificate name mokutil reports for
#     an enrolled MOK -- the Dashboard check matches external output;
#   * "kyth-vpnc-script": a negative regression needle in vpn_saml's
#     tests pinning that the upstream script name stays out of argv.
# One file is excluded outright: src/data/compat_games.json, whose
# notes are provenance data recording that a verdict came from
# upstream validation; rewriting them would falsify the record (the
# branding regression test allowlists exactly this file).
RENAME_PROTECTED = (
    "kyth-os/kyth",
    "mrtrickdotorg/Rowyn",
    "kyth-os",
    "KythOS Secure Boot",
    "kyth-vpnc-script",
)
RENAME_EXCLUDED = {"src/rowyn-hub-web/src/data/compat_games.json"}
RENAME_PAIRS = (
    ("KythOS", "Rowyn"),
    ("KYTHOS", "ROWYN"),
    ("kythos", "rowyn"),
    ("KYTH", "ROWYN"),
    ("Kyth", "Rowyn"),
    ("kyth", "rowyn"),
)


def _mask_rename_protected(text):
    slots = []
    for literal in RENAME_PROTECTED:
        if literal in text:
            marker = f"\x00{len(slots)}\x00"
            text = text.replace(literal, marker)
            slots.append((marker, literal))
    return text, slots


def _unmask_rename_protected(text, slots):
    for marker, literal in slots:
        text = text.replace(marker, literal)
    return text


def repoint_upstream_refs(root):
    """Repoint upstream-product repo/image references to Rowyn's own.

    The feedback issue destination, the shared crate's bootc REGISTRY
    constant, and parser/test-fixture image refs name the upstream
    tracker's repo and image repository; in Rowyn's tree they must use the
    destination owner and image. The upstream org remains external only for
    unrelated dependencies; the main project path follows the move.
    Runs immediately before rename_tree, which preserves the resulting
    destination paths while the branding pass runs."""
    files = 0
    refs = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if str(path.relative_to(root)) in RENAME_EXCLUDED:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, ValueError):
            continue
        renamed = text.replace("ghcr.io/kyth-os/kyth", "ghcr.io/mrtrickdotorg/rowyn")
        renamed = renamed.replace("github.com/kyth-os/kyth", "github.com/mrtrickdotorg/Rowyn")
        renamed = renamed.replace("kyth-os/kyth", "mrtrickdotorg/Rowyn")
        if renamed != text:
            refs += (text.count("kyth-os/kyth"))
            path.write_text(renamed)
            files += 1
    return f"{files} files, {refs} references"


def rename_tree(root):
    """Apply the uniform rename map to every text file in the export,
    then fail closed if any unprotected upstream identifier survives."""
    changed = 0
    tokens = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if str(path.relative_to(root)) in RENAME_EXCLUDED:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, ValueError):
            continue
        # Rowyn moved organizations; update project-owned references before the
        # generic branding map protects unrelated kyth-os upstream references.
        text = text.replace("github.com/kyth-os/Rowyn", "github.com/mrtrickdotorg/Rowyn")
        text = text.replace("github.com/kyth-os/rowyn", "github.com/mrtrickdotorg/Rowyn")
        text = text.replace("ghcr.io/kyth-os/rowyn", "ghcr.io/mrtrickdotorg/rowyn")
        text = text.replace("kyth-os/Rowyn", "mrtrickdotorg/Rowyn")
        text = text.replace("kyth-os/rowyn", "mrtrickdotorg/rowyn")
        masked, slots = _mask_rename_protected(text)
        renamed = masked
        for old, new in RENAME_PAIRS:
            tokens += renamed.count(old)
            renamed = renamed.replace(old, new)
        renamed = _unmask_rename_protected(renamed, slots)
        if renamed != text:
            path.write_text(renamed)
            changed += 1
    offenders = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(root))
        if rel in RENAME_EXCLUDED:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, ValueError):
            continue
        masked, _ = _mask_rename_protected(text)
        if "kyth" in masked.lower():
            offenders.append(rel)
    if offenders:
        raise ValueError(f"upstream identifiers survived the rename: {offenders}")
    return f"{changed} files, {tokens} identifiers"


def export_source(repo, destination):
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", PIN + "^{commit}"], text=True).strip()
    if actual != PIN:
        raise ValueError("hub source commit mismatch")
    if destination.exists():
        raise ValueError("source destination must not exist (export fresh pinned objects)")
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", PIN, *PATHS])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        # node_modules is an upstream symlink to the developer's workspace.
        # Never export symlinks, submodules, or anything outside the allowlist.
        for member in stream.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                continue
            path = destination / export_member_path(member.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            member_stream = stream.extractfile(member)
            assert member_stream is not None
            path.write_bytes(member_stream.read())
            path.chmod(member.mode & 0o777)


def apply_rowyn_qol(root):
    """Wire small desktop additions into the existing exported Hub."""
    web = root / "src/rowyn-hub-web"
    manifest_path = web / "src/data/hubRoutes.json"
    manifest = json.loads(manifest_path.read_text())
    if any(d["key"] == "Backups" for d in manifest["destinations"]):
        raise ValueError("Backups destination already exists")
    manifest["destinations"].append({
        "key": "Backups", "route": "/backups", "sections": [{
            "key": "Backups", "title": "Backups",
            "description": "Configure file backups and inspect recorded completion."}]})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    app = web / "src/App.tsx"
    text = replace_once(app.read_text(), 'const Apps = lazy(',
        'const Backups = lazy(() => import("./pages/Backups").then(({ Backups: page }) => ({ default: page })));\nconst Apps = lazy(')
    text = replace_once(text, '  "/apps": "Apps",', '  "/backups": "Backups",\n  "/apps": "Apps",')
    text = replace_once(text, '              <Route path="/apps"',
        '              <Route path="/backups" element={<Backups />} />\n              <Route path="/apps"')
    app.write_text(text)
    sidebar = web / "src/components/Sidebar.tsx"
    sidebar.write_text(replace_once(sidebar.read_text(), '  Apps: { hint:',
        '  Backups: { hint: "Personal files and backup plans", Icon: IconShield },\n  Apps: { hint:'))
    sections = web / "src/data/hubSections.ts"
    text = replace_once(sections.read_text(), '  Diagnostics: IconDatabase,',
        '  Backups: IconDatabase,\n  Development: IconDatabase,\n  SteamLibrary: IconGamepad,\n  Readiness: IconShield,\n  Storage: IconDatabase,\n  Power: IconChip,\n  Diagnostics: IconDatabase,')
    sections.write_text(text + '\nexport const BACKUPS_SECTIONS = sectionsFor("Backups");\n')
    destinations = web / "src/data/destinations.ts"
    text = replace_once(destinations.read_text(), '  APPS_SECTIONS,', '  BACKUPS_SECTIONS,\n  APPS_SECTIONS,')
    destinations.write_text(replace_once(text, '  Apps: APPS_SECTIONS,', '  Backups: BACKUPS_SECTIONS,\n  Apps: APPS_SECTIONS,'))
    apps = web / "src/pages/Apps.tsx"
    text = 'import { AppsDefaultsAccess } from "./AppsDefaultsAccess";\n' + apps.read_text()
    apps.write_text(replace_once(text, '      <HubPage', '      <AppsDefaultsAccess />\n      <HubPage'))
    updates = web / "src/pages/Updates.tsx"
    text = ('import { RecoveryClarity } from "../components/RecoveryClarity";\n'
            'import { BootHealthPanel } from "../components/BootHealthPanel";\n'
            'import { RowynChannelCard } from "../components/RowynChannelCard";\n') + updates.read_text()
    # The update flow leads the page: hero, guidance, next step, details. The two
    # read-only panels follow it in one row instead of pushing the hero down.
    # Pass 2 adds the channel card: current channel from the booted
    # deployment, stable<->testing switching through bootc_switch_branch.
    updates.write_text(replace_once(text, '<UpdatesOverview />',
        '<UpdatesOverview /><section className="rowyn-updates-panels">'
        '<RecoveryClarity /><BootHealthPanel /><RowynChannelCard /></section>'))
    overview = web / "src/components/UpdatesOverview.tsx"
    text = overview.read_text()
    # The hero chip and the guidance block already name the update state, so this
    # card restated it a third time between them and the next-step actions.
    text = cut_between(text, "  const systemStatusLabel = !loaded\n",
                       "  const guidance: UpdateGuidance = (() => {")
    text = cut_between(text, '      <div className="updates-status-grid updates-status-grid-single"',
                       '      <div className="updates-actions-card updates-primary-actions">')
    overview.write_text(text)
    app_store = web / "src/components/AppStoreSection.tsx"
    text = 'import { AppRemovalConfirmation } from "./AppRemovalConfirmation";\n' + app_store.read_text()
    text = replace_once(text,
        '  const onUninstall = (app: CardApp) => { void run(`uninstall-${app.id}`, `Removing ${app.name}…`, async () => { const result = await uninstallFlatpak(app.id); await refreshInstalled(); return result; }); };',
        '  const [removal, setRemoval] = useState<CardApp | null>(null);\n'
        '  const onUninstall = (app: CardApp) => { setRemoval(app); };\n'
        '  const confirmRemoval = () => {\n'
        '    if (!removal || busy !== null) return;\n'
        '    const app = removal; setRemoval(null);\n'
        '    void run(`uninstall-${app.id}`, `Removing ${app.name}…`, async () => { const result = await uninstallFlatpak(app.id); await refreshInstalled(); return result; });\n'
        '  };')
    text = replace_once(text, '  return <LiveSectionCard section={section} live={live}>',
        '  return <LiveSectionCard section={section} live={live}>\n'
        '    {removal && <AppRemovalConfirmation key={removal.id} app={removal} busy={busy !== null} onCancel={() => setRemoval(null)} onConfirm={confirmRemoval} />}')
    app_store.write_text(text)
    commands = web / "src-tauri/src/commands/mod.rs"
    commands.write_text(commands.read_text() + '\npub(crate) mod rowyn_backups;\npub(crate) mod rowyn_settings;\npub(crate) mod rowyn_support;\npub(crate) mod rowyn_care;\npub(crate) mod rowyn_readiness;\n')
    main = web / "src-tauri/src/main.rs"
    text = replace_once(main.read_text(), 'tauri::generate_handler![',
        'tauri::generate_handler![\n'
        '            commands::rowyn_readiness::rowyn_readiness_snapshot,\n'
        '            commands::rowyn_care::rowyn_storage_snapshot,\n'
        '            commands::rowyn_care::rowyn_power_snapshot,\n'
        '            commands::rowyn_backups::rowyn_backups_snapshot,\n'
        '            commands::rowyn_settings::rowyn_open_settings,\n'
        '            commands::rowyn_development::rowyn_development_preset_preview,')
    text = replace_once(text, 'fn main() {', '''fn main() {
    let development_args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if let Some(result) = commands::rowyn_development::handle_development_cli(&development_args) {
        if let Err(error) = result {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }''')
    main.write_text(text)


def apply_rowyn_mvp(root):
    """Add Rowyn-owned pages after the upstream cuts; never restore gaming.rs.

    The overlays are normal reviewable source files. Only the native bridge
    and manifest wiring are spliced into the pinned source, with anchors.
    """
    import shutil
    web = root / "src/rowyn-hub-web"
    manifest_path = web / "src/data/hubRoutes.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["destinations"] = [d for d in manifest["destinations"] if d["key"] != "Play"]
    manifest["destinations"] += [
        {"key": "Development", "route": "/development", "sections": [
            {"key": "Development", "title": "Development", "description": "Create and manage a dedicated Ubuntu development environment."}]},
        {"key": "Diagnostics", "route": "/diagnostics", "sections": [
            {"key": "Diagnostics", "title": "Diagnostics", "description": "Read-only OS, kernel, image, graphics, controller, memory and disk checks; preview a local report."},
            {"key": "Readiness", "title": "System readiness", "description": "Explicit read-only setup and service readiness with safe next steps."},
            {"key": "Storage", "title": "Storage care", "description": "Review backing-disk pressure, app sizes, and explicitly confirmed cleanup workflows."},
            {"key": "Power", "title": "Power and sleep", "description": "Read battery health, charging capabilities, power profile and sleep policy; open KDE settings."}]},
        {"key": "Play", "route": "/play", "sections": [
            {"key": "Gaming", "title": "Launchers", "description": "Install or open Steam, Heroic, Lutris and Bottles as Flatpaks."},
            {"key": "SteamLibrary", "title": "Installed games", "description": "Local Steam metadata, read only when you ask."},
            {"key": "Compatibility", "title": "Compatibility", "description": "Dated support guidance, anti-cheat caveats and per-game launch options."},
            {"key": "Controllers", "title": "Controllers", "description": "Read-only controller readiness; rescan after connecting a gamepad."}]},
        {"key": "Work", "route": "/work", "sections": [
            {"key": "Printing", "title": "Printing", "description": "CUPS queues, a driverless printer scan, one confirmed test page and adding a queue."},
            {"key": "Office", "title": "Office and mail", "description": "Office fonts, LibreOffice and Betterbird, Microsoft 365 web apps and Outlook archives."},
            {"key": "Focus", "title": "Focus and connectivity", "description": "Timed focus sessions and the work network identity."}]},
    ]
    # Work setup is its own destination: the Apps page is the software
    # center, and printing/office/focus live on Work, under Play in the rail.
    apps_entry = next((entry for entry in manifest["destinations"] if entry["key"] == "Apps"), None)
    if apps_entry is None:
        raise ValueError("Apps destination missing from hubRoutes.json")
    if [section["key"] for section in apps_entry["sections"]] != ["App Store", "Work Setup"]:
        raise ValueError("Apps sections changed upstream: " + ", ".join(section["key"] for section in apps_entry["sections"]))
    apps_entry["sections"] = [section for section in apps_entry["sections"] if section["key"] == "App Store"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    app = web / "src/App.tsx"
    text = app.read_text()
    # The raw-pin test may still contain Play; the production cut has removed it.
    if 'const Play = lazy(' in text:
        text = replace_once(text, './pages/Play', './pages/RowynPlay')
    else:
        text = replace_once(text, 'const Apps = lazy(', 'const Play = lazy(() => import("./pages/RowynPlay").then(({ Play: page }) => ({ default: page })));\nconst Apps = lazy(')
        text = replace_once(text, '  "/apps": "Apps",', '  "/play": "Play",\n  "/apps": "Apps",')
        text = replace_once(text, '              <Route path="/apps"', '              <Route path="/play" element={<Play />} />\n              <Route path="/apps"')
    text = replace_once(text, 'const Apps = lazy(', 'const Development = lazy(() => import("./pages/RowynDevelopment").then(({ Development: page }) => ({ default: page })));\nconst Diagnostics = lazy(() => import("./pages/RowynDiagnostics").then(({ Diagnostics: page }) => ({ default: page })));\nconst Apps = lazy(')
    text = replace_once(text, '  "/apps": "Apps",', '  "/development": "Development",\n  "/diagnostics": "Diagnostics",\n  "/apps": "Apps",')
    text = replace_once(text, '              <Route path="/apps"', '              <Route path="/development" element={<Development />} />\n              <Route path="/diagnostics" element={<Diagnostics />} />\n              <Route path="/apps"')
    text = replace_once(text, 'const Apps = lazy(',
        'const Work = lazy(() => import("./pages/RowynWork").then(({ Work: page }) => ({ default: page })));\nconst Apps = lazy(')
    text = replace_once(text, '  "/play": "Play",\n', '  "/play": "Play",\n  "/work": "Work",\n')
    text = replace_once(text, '              <Route path="/apps"',
        '              <Route path="/work" element={<Work />} />\n              <Route path="/apps"')
    app.write_text(text)
    sidebar = web / "src/components/Sidebar.tsx"
    text = sidebar.read_text()
    old_play = '  Play: { hint: "Games, boost, controllers", Icon: IconPlay },\n'
    if old_play in text:
        text = replace_once(text, old_play, '')
    text = replace_once(text, '  Apps: { hint:', '  Development: { hint: "Dedicated Ubuntu development environment", Icon: IconTerminal },\n  Diagnostics: { hint: "Read-only system report", Icon: IconShield },\n  Play: { hint: "Launchers, compatibility, controllers", Icon: IconGrid },\n  Apps: { hint:')
    text = replace_once(text, '  Play: { hint: "Launchers, compatibility, controllers", Icon: IconGrid },\n',
        '  Play: { hint: "Launchers, compatibility, controllers", Icon: IconGrid },\n'
        '  Work: { hint: "Printing, office and focus time", Icon: IconMonitor },\n')
    text = replace_once(text, '  Apps: { hint: "Discover and work setup", Icon: IconGrid },',
        '  Apps: { hint: "Discover and install apps", Icon: IconGrid },')
    sidebar.write_text(text)
    sections = web / "src/data/hubSections.ts"
    sections.write_text(replace_once(sections.read_text(),
        '  if (!destination) {',
        '  if (!destination && ["This PC", "Move In"].includes(destinationKey)) return [];\n  if (!destination) {')
        + '\nexport const DIAGNOSTICS_SECTIONS = sectionsFor("Diagnostics");\n'
        + 'export const DEVELOPMENT_SECTIONS = sectionsFor("Development");\n'
        + 'export const WORK_SECTIONS = sectionsFor("Work");\n')
    text = sections.read_text()
    text = replace_once(text, '  Diagnostics: IconDatabase,',
        '  Printing: IconMonitor,\n  Office: IconGrid,\n  Focus: IconChip,\n  Diagnostics: IconDatabase,')
    sections.write_text(text)
    destinations = web / "src/data/destinations.ts"
    text = replace_once(destinations.read_text(), '  APPS_SECTIONS,', '  DIAGNOSTICS_SECTIONS,\n  DEVELOPMENT_SECTIONS,\n  APPS_SECTIONS,')
    text = replace_once(text, '  Apps: APPS_SECTIONS,', '  Development: DEVELOPMENT_SECTIONS,\n  Diagnostics: DIAGNOSTICS_SECTIONS,\n  Apps: APPS_SECTIONS,')
    text = replace_once(text, '  APPS_SECTIONS,', '  WORK_SECTIONS,\n  APPS_SECTIONS,')
    text = replace_once(text, '  Apps: APPS_SECTIONS,', '  Work: WORK_SECTIONS,\n  Apps: APPS_SECTIONS,')
    destinations.write_text(text)
    apps_page = web / "src/pages/Apps.tsx"
    text = apps_page.read_text()
    text = replace_once(text, 'import { WorkSetupSection } from "../components/WorkSetupSection";\n', "")
    text = replace_once(text, '          "Work Setup": WorkSetupSection,\n', "")
    text = replace_once(text, 'Find trusted apps, keep them current, and set up the tools you use every day.',
                        'Find trusted apps, keep them current, and remove the ones you no longer need. Work setup lives on Work.')
    text = replace_once(text,
        '// The Apps destination is the software center up front: the App Store\n'
        '// workspace on top, Work Setup below it — no status-card overview, no\n'
        '// start-here actions, no tab switcher. Both workspaces always render in\n'
        '// manifest order, so an incoming ?section= deep link scrolls (HubPage)\n'
        '// instead of tab-switching.\n',
        '// The Apps destination is the software center: the App Store workspace,\n'
        '// no status-card overview, no start-here actions, no tab switcher. Work\n'
        '// setup moved to its own destination, so this page stays about software;\n'
        '// an incoming ?section= deep link still scrolls (HubPage) instead of\n'
        '// tab-switching.\n')
    apps_page.write_text(text)
    package = web / "package.json"
    package_json = json.loads(package.read_text())
    scripts = package_json.setdefault("scripts", {})
    scripts["test:rowyn-mvp"] = "node --test " + " ".join(
        f"tests/{test.name}" for test in sorted((Path(__file__).resolve().parent / "hub/tests").glob("rowyn-*.test.mjs")))
    package.write_text(json.dumps(package_json, indent=2) + "\n")
    overlay = Path(__file__).resolve().parent / "hub"
    for source in sorted(overlay.rglob("*")):
        if source.is_file() and source.relative_to(overlay).parts[0] in {"src", "src-tauri", "tests"}:
            target = web / source.relative_to(overlay)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    commands_mod = web / "src-tauri/src/commands/mod.rs"
    commands_mod.write_text(commands_mod.read_text() + '\npub(crate) mod rowyn_diagnostics;\npub(crate) mod rowyn_play;\npub(crate) mod rowyn_steam;\npub(crate) mod rowyn_development;\npub(crate) mod rowyn_boot_health;\n')
    main = web / "src-tauri/src/main.rs"
    main.write_text(replace_once(main.read_text(), 'tauri::generate_handler![',
        'tauri::generate_handler![\n'
        '            commands::rowyn_diagnostics::rowyn_diagnostics_snapshot,\n'
        '            commands::rowyn_diagnostics::rowyn_controller_scan,\n'
        '            commands::rowyn_diagnostics::rowyn_export_report,\n'
        '            commands::rowyn_play::rowyn_launcher_open,\n'
        '            commands::rowyn_steam::rowyn_steam_games_scan,\n'
        '            commands::rowyn_development::rowyn_development_list,\n'
        '            commands::rowyn_development::rowyn_development_start,\n'
        '            commands::rowyn_development::rowyn_development_job,\n'
        '            commands::rowyn_development::rowyn_development_cancel,\n'
        '            commands::rowyn_development::rowyn_development_open,\n'
        '            commands::rowyn_boot_health::rowyn_boot_health_snapshot,'))
    commands_mod = web / "src-tauri/src/commands/mod.rs"
    commands_mod.write_text(commands_mod.read_text() + '\npub(crate) mod rowyn_work;\n')
    main.write_text(replace_once(main.read_text(), 'tauri::generate_handler![',
        'tauri::generate_handler![\n'
        '            commands::rowyn_work::rowyn_print_queues,\n'
        '            commands::rowyn_work::rowyn_print_test_page,\n'
        '            commands::rowyn_work::rowyn_print_add_queue,\n'))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rowyn_port_tests", overlay / "port_tests.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.adapt(web, replace_once)
    apply_rowyn_qol(root)


def apply_rowyn_daily_driver(root):
    """Daily-driver program (Nov 2026): catalog breadth + one Flatpak scope.

    Runs after the rename, on the final Rowyn tree. Two changes:

    1. The shared crate's curated catalog gains the apps a Windows
       convert looks for first (Firefox, Betterbird — the mail client
       the exe-handler already recommends — Bitwarden, and a Work pack
       with VS Code, Zoom, Slack and the community Teams client) plus
       ProtonPlus in the Gaming pack. Every id was verified against
       Flathub on 2026-11-09; the crate's own test re-validates the id
       shape, and com.microsoft.Teams is deliberately absent (it does
       not exist on Flathub; the community client is the honest path).

    2. Installs unify on the system Flatpak installation — the one
       Discover uses — so an app installed from the Hub reads as
       installed everywhere. The App Store/Play install command used
       the per-user scope, which made the two disagree about what was
       installed. The system path reuses the established
       system_flatpak_* pattern from the update/uninstall code (flatpak
       runs as the user; the polkit-authorised system helper performs
       the change; refusal becomes administrator guidance, not a raw
       D-Bus error). Uninstall already resolves the installed scope, so
       legacy per-user installs stay removable, and updates keep
       covering both scopes for the same reason.
    """
    catalog = root / "src/rowyn-shared-rs/src/system/software_catalog.rs"
    text = catalog.read_text()

    def app_block(app_id, label, description, indent="                "):
        # Match the file's own formatting: short descriptions stay on
        # one line, long ones wrap the way the neighboring entries do.
        if len(description) > 80:
            desc = (f"{indent}    description:\n"
                    f"{indent}        \"{description}\"\n"
                    f"{indent}            .to_string(),\n")
        else:
            desc = f"{indent}    description: \"{description}\".to_string(),\n"
        return (
            f"{indent}CatalogApp {{\n"
            f"{indent}    id: \"{app_id}\".to_string(),\n"
            f"{indent}    label: \"{label}\".to_string(),\n"
            f"{indent}    selected: true,\n"
            f"{desc}"
            f"{indent}}},\n")

    ludusavi = (
        "                CatalogApp {\n"
        "                    id: \"com.github.mtkennerly.ludusavi\".to_string(),\n"
        "                    label: \"Ludusavi\".to_string(),\n"
        "                    selected: true,\n"
        "                    description:\n"
        "                        \"Back up and restore PC game save files across hundreds of titles.\"\n"
        "                            .to_string(),\n"
        "                },\n")
    text = replace_once(text, ludusavi, ludusavi + app_block(
        "com.vysp3r.ProtonPlus", "ProtonPlus",
        "Install and update Proton-GE and other compatibility tools for Steam and launchers."))
    text = replace_once(
        text,
        '"Steam, Epic/GOG, compatibility launchers, saves, and standalone .exe support."',
        '"Steam, Epic/GOG, compatibility launchers, saves, Proton-GE tooling, and standalone .exe support."')

    localsend = (
        "                CatalogApp {\n"
        "                    id: \"org.localsend.localsend_app\".to_string(),\n"
        "                    label: \"LocalSend\".to_string(),\n"
        "                    selected: true,\n"
        "                    description: \"Send files to nearby devices over the local network.\".to_string(),\n"
        "                },\n")
    everyday_additions = (
        app_block("org.mozilla.firefox", "Firefox",
                  "Web browser from Mozilla, for anyone who does not want Brave.")
        + app_block("eu.betterbird.Betterbird", "Betterbird Mail",
                    "Desktop email and calendar, Thunderbird-compatible — the Outlook replacement Rowyn suggests.")
        + app_block("com.bitwarden.desktop", "Bitwarden",
                    "Password manager; unlocks the vault you already use."))
    work_pack = (
        "        StarterPack {\n"
        "            name: \"Work\".to_string(),\n"
        "            desc: \"Code editor, calls and chat for people arriving from Windows workplaces.\".to_string(),\n"
        "            apps: vec![\n"
        + app_block("com.visualstudio.code", "Visual Studio Code",
                    "Code editor from Microsoft, packaged as a Flatpak.")
        + app_block("us.zoom.Zoom", "Zoom", "Video meetings and calls.")
        + app_block("com.slack.Slack", "Slack", "Team chat for work.")
        + app_block("com.github.IsmaelMartinez.teams_for_linux", "Teams for Linux",
                    "Community Microsoft Teams client; the official Teams app is web-only on Linux.")
        + "            ],\n"
        "        },\n")
    text = replace_once(
        text,
        localsend + "            ],\n        },\n    ]\n}",
        localsend + everyday_additions + "            ],\n        },\n"
        + work_pack + "    ]\n}")

    geforce_anchor = '            flatpak_id: "".to_string(),\n        },\n    ]\n}'
    familiar_additions = "".join(
        "        FamiliarApp {\n"
        f"            windows_name: \"{name}\".to_string(),\n"
        f"            description: \"{desc}\".to_string(),\n"
        f"            flatpak_id: \"{app_id}\".to_string(),\n"
        "        },\n"
        for name, desc, app_id in (
            ("Outlook", "Use Betterbird for desktop email and calendar.",
             "eu.betterbird.Betterbird"),
            ("Microsoft Teams",
             "Use Teams for Linux, the community client; official Teams is web-only on Linux.",
             "com.github.IsmaelMartinez.teams_for_linux"),
            ("Visual Studio Code", "Install Visual Studio Code from Flatpak.",
             "com.visualstudio.code"),
        ))
    text = replace_once(text, geforce_anchor,
                        '            flatpak_id: "".to_string(),\n        },\n'
                        + familiar_additions + "    ]\n}")

    text = replace_once(
        text,
        "/// Project one catalog result into the explicit, user-scoped install argv",
        "/// Project one catalog result into the explicit, system-scoped install argv")
    text = replace_once(
        text,
        '["flatpak", "install", "--user", "-y", "flathub", app_id]',
        '["flatpak", "install", "--system", "-y", "flathub", app_id]')
    text = replace_once(
        text,
        "fn projects_only_valid_user_scoped_flatpak_installs()",
        "fn projects_only_valid_system_scoped_flatpak_installs()")
    text = replace_once(
        text,
        '            vec![\n                "flatpak",\n                "install",\n                "--user",',
        '            vec![\n                "flatpak",\n                "install",\n                "--system",')
    catalog.write_text(text)

    main_rs = root / "src/rowyn-hub-web/src-tauri/src/main.rs"
    text = main_rs.read_text()
    install_helpers = '''
/// Fixed argv for installing one app into the system installation —
/// the same installation Discover uses, so installed state agrees
/// everywhere. The id is validated here again so this builder is safe
/// on its own, and follows `--` so it can never be read as an option.
fn system_flatpak_install_args(app_id: &str) -> Result<Vec<String>, String> {
    commands::privilege::validate_flatpak_id(app_id)?;
    Ok(["install", "--system", "--noninteractive", "-y", "flathub", "--", app_id]
        .iter()
        .map(|arg| arg.to_string())
        .collect())
}

/// Turn a failed system-scope install into text the user can act on.
/// polkit refusing the (non-interactive) request surfaces as a D-Bus
/// "... not allowed for user" error from the system helper.
fn system_flatpak_install_failure(stderr: &str) -> String {
    let lower = stderr.to_lowercase();
    if lower.contains("not allowed for user")
        || lower.contains("not authorized")
        || lower.contains("authorization")
        || lower.contains("authentication is required")
    {
        return "Installing a system-wide app needs an administrator. Sign in as an \
                administrator (a member of the sudo group) or ask one to install it."
            .to_string();
    }
    let detail = stderr.trim();
    if detail.is_empty() {
        "Flatpak failed without reporting a reason.".to_string()
    } else {
        detail.to_string()
    }
}

/// Run one system-scope install as the user, bounded by the Flatpak
/// transfer tier and killed within one poll tick on cancel: Flatpak's
/// own polkit-authorised system helper performs the change.
fn system_flatpak_install(app_id: &str, cancel: &AtomicBool) -> Result<(), String> {
    let args = system_flatpak_install_args(app_id)?;
    let mut command = Command::new("/usr/bin/flatpak");
    command.args(&args).stdin(std::process::Stdio::null());
    match rowyn_shared::system::process::run_bounded_command_cancel(
        command,
        timeout_for(JobTimeoutClass::FlatpakTransfer),
        cancel,
    ) {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(system_flatpak_install_failure(
            &commands::process::bounded_text(&output.stderr),
        )),
        Err(error) if error.kind() == std::io::ErrorKind::TimedOut => {
            Err("Installing the app took too long and was stopped.".to_string())
        }
        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
            Err("Cancelled.".to_string())
        }
        Err(error) => Err(format!("Could not start Flatpak: {error}")),
    }
}

'''
    text = replace_once(
        text,
        "fn system_flatpak_update(cancel: &AtomicBool) -> Result<(), String> {",
        install_helpers
        + "fn system_flatpak_update(cancel: &AtomicBool) -> Result<(), String> {")
    old_install_body = '''        // Ensure the per-user Flathub remote exists first (idempotent):
        // the security/gaming install paths already did this, so a missing
        // remote failed installs from THIS page only. A setup failure is
        // the job's failure, with the setup step named.
        if let Err(error) = commands::job::ensure_flathub_user_remote(&cancel) {
            app_installs().finish(
                &job_for_thread,
                "failed",
                format!("Could not set up Flathub: {error}"),
            );
            return;
        }
        if cancel.load(std::sync::atomic::Ordering::Relaxed) {
            return;
        }
        // Unbounded `.output()` hangs on a stalled mirror with no way to
        // cancel; run bounded and cancellable like the other install paths.
        let mut command = std::process::Command::new("/usr/bin/flatpak");
        command.args(["install", "--user", "-y", "flathub", "--", &app_id]);
        let result = rowyn_shared::system::process::run_bounded_command_cancel(
            command,
            timeout_for(JobTimeoutClass::FlatpakTransfer),
            &cancel,
        );
        let (state, detail) = match result {
            Ok(output) if output.status.success() => {
                ("complete", "Installation complete.".to_string())
            }
            Ok(output) => ("failed", commands::process::bounded_text(&output.stderr)),
            // A timeout is not "could not start": the install ran and was
            // stopped at the transfer bound. Name it honestly (the update
            // path already special-cased this; install did not).
            Err(err) if err.kind() == std::io::ErrorKind::TimedOut => (
                "failed",
                "Installing took too long and was stopped.".to_string(),
            ),
            Err(err) => ("failed", format!("Could not start Flatpak: {err}")),
        };
        app_installs().finish(&job_for_thread, state, detail);'''
    new_install_body = '''        // Install into the system installation — the same one
        // Discover uses — so an app installed here reads as installed
        // everywhere; the per-user scope made the two disagree about
        // what was installed. The system Flathub remote is the image's
        // static declaration, so there is no per-user remote setup
        // step to fail before the install can start.
        if cancel.load(std::sync::atomic::Ordering::Relaxed) {
            return;
        }
        let (state, detail) = match system_flatpak_install(&app_id, &cancel) {
            Ok(()) => ("complete", "Installation complete.".to_string()),
            Err(error) => ("failed", error),
        };
        app_installs().finish(&job_for_thread, state, detail);'''
    text = replace_once(text, old_install_body, new_install_body)

    # New Play/Steam commands live in the Rowyn-owned overlay modules;
    # their invoke-handler registrations are added here, after the
    # renamed steam registration line.
    registration_anchor = "            commands::rowyn_steam::rowyn_steam_games_scan,\n"
    if text.count(registration_anchor) != 1:
        raise SystemExit("generate_handler steam registration anchor drifted")
    text = text.replace(
        registration_anchor,
        registration_anchor
        + "            commands::rowyn_steam::rowyn_steam_play_status,\n"
        + "            commands::rowyn_steam::rowyn_steam_play_apply,\n"
        + "            commands::rowyn_play::rowyn_display_modes_scan,\n"
        + "            commands::rowyn_play::rowyn_vulkan_layers_scan,\n"
        + "            commands::rowyn_play::rowyn_vulkan_layer_install,\n",
        1,
    )
    main_rs.write_text(text)


def apply_rowyn_move_in(root):
    """Wire the Rowyn-native Windows migration destination (MoveIn).

    The upstream "Move In" destination was cut and stays cut (its route
    and component names remain absent, as the port tests assert); this
    is a separate Rowyn-owned successor — destination key MoveIn, route
    /windows-move-in, page RowynMoveIn, backend commands::rowyn_migrate
    — wired with the same anchored splices the Backups destination
    uses. Runs after the rename, on the final Rowyn tree.
    """
    web = root / "src/rowyn-hub-web"
    manifest_path = web / "src/data/hubRoutes.json"
    manifest = json.loads(manifest_path.read_text())
    if any(d["key"] == "MoveIn" for d in manifest["destinations"]):
        raise ValueError("MoveIn destination already exists")
    manifest["destinations"].append({
        "key": "MoveIn", "route": "/windows-move-in", "sections": [{
            "key": "MoveIn", "title": "Move in from Windows",
            "description": "Detect a Windows installation and import documents, pictures, bookmarks and wallpaper — read-only on the Windows side."}]})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    app = web / "src/App.tsx"
    text = replace_once(app.read_text(), 'const Apps = lazy(',
        'const WindowsMoveIn = lazy(() => import("./pages/RowynMoveIn").then(({ MoveIn: page }) => ({ default: page })));\nconst Apps = lazy(')
    text = replace_once(text, '  "/apps": "Apps",', '  "/windows-move-in": "MoveIn",\n  "/apps": "Apps",')
    text = replace_once(text, '              <Route path="/apps"',
        '              <Route path="/windows-move-in" element={<WindowsMoveIn />} />\n              <Route path="/apps"')
    app.write_text(text)
    sidebar = web / "src/components/Sidebar.tsx"
    sidebar.write_text(replace_once(sidebar.read_text(), '  Apps: { hint:',
        '  MoveIn: { hint: "Bring your files from Windows", Icon: IconHome },\n  Apps: { hint:'))
    sections = web / "src/data/hubSections.ts"
    text = replace_once(sections.read_text(), '  Diagnostics: IconDatabase,',
        '  MoveIn: IconDatabase,\n  Diagnostics: IconDatabase,')
    sections.write_text(text + '\nexport const MOVEIN_SECTIONS = sectionsFor("MoveIn");\n')
    destinations = web / "src/data/destinations.ts"
    text = replace_once(destinations.read_text(), '  APPS_SECTIONS,', '  MOVEIN_SECTIONS,\n  APPS_SECTIONS,')
    destinations.write_text(replace_once(text, '  Apps: APPS_SECTIONS,', '  MoveIn: MOVEIN_SECTIONS,\n  Apps: APPS_SECTIONS,'))
    commands = web / "src-tauri/src/commands/mod.rs"
    commands.write_text(commands.read_text() + '\npub(crate) mod rowyn_migrate;\n')
    main = web / "src-tauri/src/main.rs"
    text = main.read_text()
    anchor = "            commands::rowyn_steam::rowyn_steam_games_scan,\n"
    if text.count(anchor) != 1:
        raise SystemExit("generate_handler steam registration anchor drifted (move-in)")
    main.write_text(text.replace(
        anchor,
        anchor
        + "            commands::rowyn_migrate::rowyn_migrate_scan,\n"
        + "            commands::rowyn_migrate::rowyn_migrate_mount,\n"
        + "            commands::rowyn_migrate::rowyn_migrate_import,\n",
        1))


def apply_rowyn_pass2(root):
    """Pass 2 (Nov 2026): compete with Bazzite on gaming + productivity.

    Catalog-only here (payload natives and the vkBasalt wiring live in
    their own commits): the Gaming pack gains the overlay/proton tooling
    depth Bazzite ships (Protontricks, GOverlay, MangoJuice), emulation
    (RetroArch) and VR streaming (WiVRn, with its Flatpak caveat in the
    copy); a Tuning & Peripherals pack collects the Flatpak tuning
    front-ends (LACT, OpenRGB, Piper, Solaar — CoreCtrl and Input
    Remapper are payload natives, named in the pack description so the
    split is discoverable); the Creator pack gains EasyEffects; the
    Work pack gains OnlyOffice, and the familiar-apps Office entry
    names it as the Microsoft-format fidelity option next to
    LibreOffice. Every new id was verified against Flathub on
    2026-11-09 (HTTP 200); pdfarranger's guessed id was NOT (404), so
    PDF Arranger ships as a payload package instead.
    """
    catalog = root / "src/rowyn-shared-rs/src/system/software_catalog.rs"
    text = catalog.read_text()

    def app_block(app_id, label, description, indent="                "):
        # Same formatting rule as the daily-driver pass: short
        # descriptions stay on one line, long ones wrap.
        if len(description) > 80:
            desc = (f"{indent}    description:\n"
                    f"{indent}        \"{description}\"\n"
                    f"{indent}            .to_string(),\n")
        else:
            desc = f"{indent}    description: \"{description}\".to_string(),\n"
        return (
            f"{indent}CatalogApp {{\n"
            f"{indent}    id: \"{app_id}\".to_string(),\n"
            f"{indent}    label: \"{label}\".to_string(),\n"
            f"{indent}    selected: true,\n"
            f"{desc}"
            f"{indent}}},\n")

    def block_of(app_id, label, description):
        return app_block(app_id, label, description)

    protonplus = block_of(
        "com.vysp3r.ProtonPlus", "ProtonPlus",
        "Install and update Proton-GE and other compatibility tools for Steam and launchers.")
    gaming_additions = (
        app_block("com.github.Matoking.protontricks", "Protontricks",
                  "Run winetricks verbs inside a game's Proton prefix — the fix behind many 'works with tweaks' verdicts.")
        + app_block("io.github.benjamimgois.goverlay", "GOverlay",
                    "Graphical settings editor for MangoHud, vkBasalt and other overlays.")
        + app_block("io.github.radiolamp.mangojuice", "MangoJuice",
                    "A compact settings front-end for the MangoHud overlay.")
        + app_block("org.libretro.RetroArch", "RetroArch",
                    "Emulation front-end for classic consoles and computers.")
        + app_block("io.github.wivrn.wivrn", "WiVRn",
                    "Stream PC VR games to a standalone headset over Wi-Fi. With Flatpak Steam, follow the VR notes in Rowyn's gaming guide — the sandbox adds caveats."))
    text = replace_once(text, protonplus, protonplus + gaming_additions)
    text = replace_once(
        text,
        '"Steam, Epic/GOG, compatibility launchers, saves, Proton-GE tooling, and standalone .exe support."',
        '"Steam, Epic/GOG, launchers, saves, Proton/overlay tooling, emulation, VR streaming, and standalone .exe support."')

    audacity = block_of("org.audacityteam.Audacity", "Audacity",
                        "Multi-track audio editor and recorder.")
    text = replace_once(text, audacity, audacity + app_block(
        "com.github.wwmm.easyeffects", "EasyEffects",
        "System-wide audio effects: equalizer, limiter and microphone noise suppression."))

    teams = block_of(
        "com.github.IsmaelMartinez.teams_for_linux", "Teams for Linux",
        "Community Microsoft Teams client; the official Teams app is web-only on Linux.")
    text = replace_once(text, teams, teams + app_block(
        "org.onlyoffice.desktopeditors", "OnlyOffice",
        "Office suite built around Microsoft formats; the closest DOCX/XLSX fidelity on Linux."))

    tuning_pack = (
        "        StarterPack {\n"
        "            name: \"Tuning & Peripherals\".to_string(),\n"
        "            desc: \"GPU tuning, RGB lighting and gaming-peripheral setup. CoreCtrl and Input Remapper are built into Rowyn; these are the remaining front-ends.\".to_string(),\n"
        "            apps: vec![\n"
        + app_block("io.github.ilya_zlobintsev.LACT", "LACT",
                    "GPU overclocking, fan curves and power limits for AMD and NVIDIA.")
        + app_block("org.openrgb.OpenRGB", "OpenRGB",
                    "One control panel for RGB lighting across brands.")
        + app_block("org.freedesktop.Piper", "Piper",
                    "Configure gaming mice — buttons, DPI, LEDs — on Rowyn's built-in ratbagd.")
        + app_block("io.github.pwr_solaar.solaar", "Solaar",
                    "Manage Logitech receivers, pairing and device settings.")
        + "            ],\n"
        "        },\n")
    text = replace_once(
        text,
        "            ],\n        },\n    ]\n}\n\n#[derive(Debug, Clone, Serialize)]\npub struct FamiliarApp {",
        "            ],\n        },\n" + tuning_pack + "    ]\n}\n\n#[derive(Debug, Clone, Serialize)]\npub struct FamiliarApp {")

    text = replace_once(
        text,
        '            description: "LibreOffice is the drop-in Office suite.".to_string(),',
        '            description: "LibreOffice is the drop-in Office suite; OnlyOffice (Work pack) tracks Microsoft DOCX/XLSX formats most closely.".to_string(),')
    catalog.write_text(text)


def apply_rowyn_streaming(root):
    """Pass 2 streaming (Nov 2026): Sunshine one-click setup support.

    Registers the Rowyn-owned rowyn_sunshine commands (status, firewall
    open, start-at-login) and adds Moonlight — the client half of the
    pair — to the Gaming pack next to WiVRn. The Sunshine install
    itself rides the standard catalog/job path (it is a Play-page
    action, not a catalog preselect: a stream host is opt-in)."""
    web = root / "src/rowyn-hub-web"
    commands = web / "src-tauri/src/commands/mod.rs"
    commands.write_text(commands.read_text() + '\npub(crate) mod rowyn_sunshine;\n')
    main = web / "src-tauri/src/main.rs"
    text = main.read_text()
    anchor = "            commands::rowyn_migrate::rowyn_migrate_import,\n"
    if text.count(anchor) != 1:
        raise SystemExit("generate_handler migrate registration anchor drifted (streaming)")
    main.write_text(text.replace(
        anchor,
        anchor
        + "            commands::rowyn_sunshine::rowyn_sunshine_status,\n"
        + "            commands::rowyn_sunshine::rowyn_sunshine_firewall_open,\n"
        + "            commands::rowyn_sunshine::rowyn_sunshine_autostart_enable,\n",
        1))

    catalog = root / "src/rowyn-shared-rs/src/system/software_catalog.rs"
    text = catalog.read_text()
    wivrn = (
        "                CatalogApp {\n"
        "                    id: \"io.github.wivrn.wivrn\".to_string(),\n"
        "                    label: \"WiVRn\".to_string(),\n"
        "                    selected: true,\n"
        "                    description:\n"
        "                        \"Stream PC VR games to a standalone headset over Wi-Fi. With Flatpak Steam, follow the VR notes in Rowyn's gaming guide — the sandbox adds caveats.\"\n"
        "                            .to_string(),\n"
        "                },\n")
    moonlight = (
        "                CatalogApp {\n"
        "                    id: \"com.moonlight_stream.Moonlight\".to_string(),\n"
        "                    label: \"Moonlight\".to_string(),\n"
        "                    selected: true,\n"
        "                    description: \"Stream games to this machine from a Sunshine host.\".to_string(),\n"
        "                },\n")
    catalog.write_text(replace_once(text, wivrn, wivrn + moonlight))


def apply_rowyn_deb_path(root):
    """D3 guarded .deb path (Nov 2026, Patrick's decision): the Apps
    page gains the RowynDebCard, backed by the rowyn_deb commands —
    .deb installs land in the dedicated rowyn-deb Distrobox, never on
    the immutable host, and only after rowyn-deb-inspect clears the
    package as a user app (system packages are refused)."""
    web = root / "src/rowyn-hub-web"
    commands = web / "src-tauri/src/commands/mod.rs"
    commands.write_text(commands.read_text() + '\npub(crate) mod rowyn_deb;\n')
    main = web / "src-tauri/src/main.rs"
    text = main.read_text()
    anchor = "            commands::rowyn_sunshine::rowyn_sunshine_autostart_enable,\n"
    if text.count(anchor) != 1:
        raise SystemExit("generate_handler sunshine registration anchor drifted (deb path)")
    main.write_text(text.replace(
        anchor,
        anchor
        + "            commands::rowyn_deb::rowyn_deb_state,\n"
        + "            commands::rowyn_deb::rowyn_deb_inspect,\n"
        + "            commands::rowyn_deb::rowyn_deb_start,\n"
        + "            commands::rowyn_deb::rowyn_deb_job,\n"
        + "            commands::rowyn_deb::rowyn_deb_cancel,\n",
        1))

    apps = web / "src/pages/Apps.tsx"
    text = apps.read_text()
    text = 'import { RowynDebCard } from "../components/RowynDebCard";\n' + text
    anchor = " <AppsDefaultsAccess />\n"
    if text.count(anchor) != 1:
        raise SystemExit("Apps defaults-access anchor drifted (deb path)")
    apps.write_text(text.replace(anchor, anchor + " <RowynDebCard />\n", 1))

def port_source(root):
    changes = []
    web = root / "src/rowyn-hub-web"
    crate = web / "src-tauri"

    # --- Tauri identity: Rowyn Hub, com.rowyn.hub -------------------------
    conf = crate / "tauri.conf.json"
    text = conf.read_text()
    text = replace_once(text, '"productName": "Kyth Hub"', '"productName": "Rowyn Hub"')
    text = replace_once(text, '"identifier": "com.kythos.hub"', '"identifier": "com.rowyn.hub"')
    text = replace_once(text, '"title": "Kyth Hub"', '"title": "Rowyn Hub"')
    conf.write_text(text)
    changes.append("tauri.conf.json: productName/identifier/title -> Rowyn Hub / com.rowyn.hub")

    # --- Crate identity: kyth-hub-shell -> rowyn-hub-shell ----------------
    cargo = crate / "Cargo.toml"
    text = cargo.read_text()
    if text.count("kyth-hub-shell") != 2:
        raise ValueError(
            f"expected exactly two kyth-hub-shell anchors in Cargo.toml, found {text.count('kyth-hub-shell')}")
    text = text.replace("kyth-hub-shell", "rowyn-hub-shell")
    text = replace_once(
        text,
        'description = "Native shell for the Kyth Hub web frontend"',
        'description = "Native shell for the Rowyn Hub web frontend"',
    )
    cargo.write_text(text)
    # --locked builds read the root package name from Cargo.lock too; keep
    # the lockfile's single root entry in step with the manifest rename.
    lock = crate / "Cargo.lock"
    text = lock.read_text()
    text = replace_once(text, 'name = "kyth-hub-shell"\nversion = "0.1.0"',
                        'name = "rowyn-hub-shell"\nversion = "0.1.0"')
    lock.write_text(text)
    # The shared crate's directory took the Rowyn name at extraction, so
    # the dependency path follows now — the Cargo.lock re-sync later in
    # this port resolves it with cargo and would fail against the old
    # path. The package name itself is renamed by the rename pass at
    # the end of the port, together with every `kyth_shared::` path.
    text = cargo.read_text()
    text = replace_once(text, 'path = "../../kyth-shared-rs"', 'path = "../../rowyn-shared-rs"')
    cargo.write_text(text)
    changes.append("Cargo.toml/Cargo.lock: package + bin kyth-hub-shell -> rowyn-hub-shell")

    # --- Browser-chrome title ---------------------------------------------
    index = web / "index.html"
    text = index.read_text()
    text = replace_once(text, "<title>Kyth Hub</title>", "<title>Rowyn Hub</title>")
    index.write_text(text)

    # --- Destination cut: hubRoutes.json is the navigation source of truth.
    # destinations.ts, deepLink.ts, search.ts, and the sidebar all derive
    # from this manifest, so filtering it here removes the cut pages from
    # every derived surface at once. Fail closed if upstream's destination
    # set is not exactly the reviewed one.
    manifest_path = web / "src/data/hubRoutes.json"
    manifest = json.loads(manifest_path.read_text())
    keys = [entry["key"] for entry in manifest["destinations"]]
    if set(keys) != EXPECTED_DESTINATIONS:
        raise ValueError(f"hub destination set changed upstream: {sorted(keys)}")
    manifest["destinations"] = [
        entry for entry in manifest["destinations"] if entry["key"] in KEPT_DESTINATIONS
    ]
    text = json.dumps(manifest, indent=2) + "\n"
    text = replace_once(text, "one KythOS software center", "one Rowyn software center")
    manifest_path.write_text(text)
    changes.append(
        "hubRoutes.json: destinations cut to " + ", ".join(KEPT_DESTINATIONS))

    # --- App.tsx: lazy imports, routes, and crumbs for the cut pages ------
    app = web / "src/App.tsx"
    text = app.read_text()
    def only_line(haystack, needle):
        matches = [line for line in haystack.splitlines(keepends=True) if needle in line]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one line containing {needle!r}, found {len(matches)}")
        return matches[0]

    for page, route in (("Play", "/play"), ("DevTools", "/dev-tools"),
                        ("ThisPc", "/this-pc"), ("MoveIn", "/move-in")):
        text = replace_once(text, only_line(text, f"const {page} = lazy("), "")
        text = replace_once(text, only_line(text, f'<Route path="{route}"'), "")
    for route, label in (("/play", "Play"), ("/dev-tools", "Dev Tools"),
                         ("/this-pc", "This PC"), ("/move-in", "Move In")):
        text = replace_once(text, f'  "{route}": "{label}",\n', "")
    # VPN is a kept destination: its lazy import, route, and crumb entry
    # must survive untouched. Fail closed if upstream renames any of them,
    # so the restore can never silently degrade to a missing page.
    for anchor in ('const Vpn = lazy(() => import("./pages/Vpn")',
                   '<Route path="/vpn" element={<Vpn />} />',
                   '  "/vpn": "VPN",\n'):
        if text.count(anchor) != 1:
            raise ValueError(f"expected exactly one kept VPN anchor in App.tsx: {anchor[:80]!r}")
    app.write_text(text)
    changes.append("App.tsx: cut /play, /dev-tools, /this-pc, /move-in routes; /vpn kept")

    # --- Sidebar: branding, presentation map, Dev Tools splice ------------
    sidebar = web / "src/components/Sidebar.tsx"
    text = sidebar.read_text()
    text = replace_once(
        text,
        'import { IconHome, IconPlay, IconGrid, IconLock, IconMonitor, IconRefresh, IconShield, IconTerminal, IconTransfer } from "./icons";',
        'import { IconHome, IconMonitor, IconGrid, IconLock, IconRefresh, IconShield, IconTerminal } from "./icons";')
    text = replace_once(
        text,
        "// in the rail automatically. Home and Dev Tools are shell-only routes\n"
        "// with no manifest destination, so they are spliced in at their\n"
        "// established positions around the derived entries. Presentation (hint +",
        "// in the rail automatically. Home is a shell-only route with no\n"
        "// manifest destination, so it is prepended to the derived entries.\n"
        "// Presentation (hint +")
    for entry in (
        '  Play: { hint: "Games, boost, controllers", Icon: IconPlay },\n',
        '  "This PC": { hint: "Health, hardware, repair", Icon: IconMonitor },\n',
        '  "Move In": { hint: "Files, saves, workflows", Icon: IconTransfer },\n',
    ):
        text = replace_once(text, entry, "")
    # The VPN presentation entry is kept with its destination; fail closed
    # if upstream renames it (the sidebar would fall back to a wrong icon).
    vpn_entry = '  VPN: { hint: "Secure work connections", Icon: IconLock },\n'
    if text.count(vpn_entry) != 1:
        raise ValueError("expected exactly one kept VPN presentation entry in Sidebar.tsx")
    text = replace_once(
        text,
        '  ...workspaceDestinations.slice(0, 2),\n'
        '  { to: "/dev-tools", label: "Dev Tools", hint: "AI dev box, editors, agents", Icon: IconTerminal },\n'
        '  ...workspaceDestinations.slice(2),\n',
        '  ...workspaceDestinations,\n')
    text = replace_once(text, 'aria-label="Kyth Hub navigation"', 'aria-label="Rowyn Hub navigation"')
    text = replace_once(text, '<div className="brand-name">Kyth Hub</div>',
                        '<div className="brand-name">Rowyn Hub</div>')
    text = replace_once(text, '<div className="brand-kicker">System protection</div>',
                        '<div className="brand-kicker">System tools</div>')
    text = replace_once(text, '<div className="sidebar-status-row">Kyth Hub</div>',
                        '<div className="sidebar-status-row">Rowyn Hub</div>')
    sidebar.write_text(text)
    changes.append("Sidebar.tsx: Rowyn Hub branding, cut presentation entries + Dev Tools splice; VPN entry kept")

    # --- Topbar breadcrumb branding ---------------------------------------
    topbar = web / "src/components/Topbar.tsx"
    text = topbar.read_text()
    text = replace_once(text, "Kyth Hub / {crumb}", "Rowyn Hub / {crumb}")
    topbar.write_text(text)

    # --- search.ts: drop the hardcoded Dev Tools destination entry --------
    search = web / "src/search.ts"
    text = search.read_text()
    text = replace_once(
        text,
        'import { IconGrid, IconHome, IconTerminal } from "./components/icons";',
        'import { IconGrid, IconHome } from "./components/icons";')
    text = replace_once(
        text,
        '  entries.push({\n'
        '    key: "dest:Dev Tools",\n'
        '    title: "Dev Tools",\n'
        '    description: "AI dev box, editors, agents",\n'
        '    destination: "Destination",\n'
        '    route: "/dev-tools",\n'
        '    Icon: IconTerminal,\n'
        '  });\n',
        "")
    search.write_text(text)
    changes.append("search.ts: cut hardcoded Dev Tools search entry")

    # --- Dashboard: branding, and cut the parts that navigate to cut pages.
    # The firstboot Play-onboarding card and the This PC action buttons
    # would land on removed routes; the firstboot fetch plumbing goes with
    # its only consumer (tsc rejects unused locals).
    dashboard = web / "src/pages/Dashboard.tsx"
    text = dashboard.read_text()
    text = replace_once(text, '<span className="home-eyebrow">KythOS command center</span>',
                        '<span className="home-eyebrow">Rowyn command center</span>')
    text = replace_once(text, "  fetchFirstbootAppsStatus,\n", "")
    text = replace_once(text, "  type FirstbootAppsStatus,\n", "")
    text = replace_once(
        text, "  const [firstboot, setFirstboot] = useState<FirstbootAppsStatus | null>(null);\n", "")
    text = replace_once(
        text,
        "const [nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery, nextFirstboot] = await Promise.all([\n"
        "        fetchGuardianSnapshot(), fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchFirstbootAppsStatus(),\n"
        "      ]);",
        "const [nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery] = await Promise.all([\n"
        "        fetchGuardianSnapshot(), fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]);")
    text = replace_once(text, "      setFirstboot(nextFirstboot);\n", "")
    text = replace_once(
        text,
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(), fetchFirstbootAppsStatus(),\n"
        "      ]).then(([nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery, nextFirstboot]) => {",
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]).then(([nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery]) => {")
    text = replace_once(text, "        setFirstboot((prev) => nextFirstboot ?? prev);\n", "")
    card_start = text.index('        {firstboot && firstboot.state !== "ready" && (')
    card_end = text.index('        <div className="home-card-grid">', card_start)
    text = text[:card_start] + text[card_end:]
    text = replace_once(
        text,
        '<div className="home-actions"><ActionButton label="Run health check" onClick={() => navigate("/this-pc?section=Guardian")} /><ActionButton label="Check updates" onClick={() => navigate("/updates")} /><ActionButton label="Open Repair" onClick={() => navigate("/this-pc?section=Repair")} /><ActionButton label="Open Hardware" onClick={() => navigate("/this-pc?section=Hardware")} /></div>',
        '<div className="home-actions"><ActionButton label="Check updates" onClick={() => navigate("/updates")} /></div>')
    text = replace_once(text, '"Guardian data is unavailable; open This PC to retry."',
                        '"Guardian data is unavailable."')
    text = replace_once(text, '"Boot health data is unavailable; open Diagnostics for recovery guidance."',
                        '"Boot health data is unavailable."')
    dashboard.write_text(text)
    changes.append("Dashboard.tsx: Rowyn branding; cut Play onboarding card + This PC actions")

    # --- Kept-surface display strings -------------------------------------
    hero = web / "src/components/HeroCard.tsx"
    text = hero.read_text()
    text = replace_once(text, "}}>KythOS</span>", "}}>Rowyn</span>")
    hero.write_text(text)

    store = web / "src/components/AppStoreSection.tsx"
    text = store.read_text()
    text = replace_once(text, '<span className="app-eyebrow">Kyth software center</span>',
                        '<span className="app-eyebrow">Rowyn software center</span>')
    text = replace_once(text, "Apps is the KythOS software center", "Apps is the Rowyn software center")
    text = replace_once(text, '"A trusted application for KythOS."', '"A trusted application for Rowyn."')
    text = replace_once(text, '<span className="app-eyebrow">Curated for KythOS</span>',
                        '<span className="app-eyebrow">Curated for Rowyn</span>')
    text = replace_once(text, "through the native Kyth bridge.", "through the native Rowyn bridge.")
    store.write_text(text)

    overview = web / "src/components/UpdatesOverview.tsx"
    text = overview.read_text()
    text, kythos_count = replace_all_counted(text, "KythOS", "Rowyn")
    overview.write_text(text)
    changes.append(f"kept surfaces: HeroCard/AppStoreSection branding; "
                   f"UpdatesOverview KythOS -> Rowyn ({kythos_count} strings)")

    # --- Updates channel: Rowyn releases + GHCR repository -----------------
    updates = crate / "src/commands/updates.rs"
    text = updates.read_text()
    text = replace_once(
        text,
        'const IMAGE_RELEASES_API: &str = "https://api.github.com/repos/kyth-os/kyth/releases?per_page=100";',
        'const IMAGE_RELEASES_API: &str = "https://api.github.com/repos/mrtrickdotorg/Rowyn/releases?per_page=100";')
    text, image_count = replace_all_counted(text, "ghcr.io/kyth-os/kyth@", "ghcr.io/mrtrickdotorg/rowyn@")
    text, release_count = replace_all_counted(
        text, "github.com/kyth-os/kyth/releases", "github.com/mrtrickdotorg/Rowyn/releases")
    # The update actions shell out to fixed helper binaries; Rowyn ships
    # its own implementations under its own name (see shared/), so every
    # reference — launch argv, require_binary prechecks, and the prose
    # that names them — is repointed. Without this the Updates page's
    # check, stage, and apply actions all fail on missing binaries.
    text, stage_path_count = replace_all_counted(
        text, "/usr/bin/kyth-safe-upgrade", "/usr/bin/rowyn-safe-upgrade")
    text, stage_prose_count = replace_all_counted(
        text, "kyth-safe-upgrade", "rowyn-safe-upgrade")
    text, guard_path_count = replace_all_counted(
        text, "/usr/bin/kyth-bootc-guard", "/usr/bin/rowyn-bootc-guard")
    text, guard_prose_count = replace_all_counted(
        text, "kyth-bootc-guard", "rowyn-bootc-guard")
    text, finalize_count = replace_all_counted(
        text, "/usr/libexec/kyth-finalize-staged", "/usr/libexec/rowyn-finalize-staged")
    text = replace_once(
        text,
        "The native KythOS update helper is not installed on this system.",
        "The native Rowyn update helper is not installed on this system.")
    text = replace_once(
        text,
        "The KythOS bootc guard helper is not installed on this system.",
        "The Rowyn bootc guard helper is not installed on this system.")
    updates.write_text(text)
    changes.append(
        f"updates.rs: releases API -> mrtrickdotorg/Rowyn; image refs -> ghcr.io/mrtrickdotorg/rowyn@ "
        f"({image_count} literals incl. fixtures); release URLs -> mrtrickdotorg/Rowyn ({release_count}); "
        f"update helpers -> rowyn-safe-upgrade ({stage_path_count} paths + {stage_prose_count} prose), "
        f"rowyn-bootc-guard ({guard_path_count} paths + {guard_prose_count} prose), "
        f"rowyn-finalize-staged ({finalize_count} paths)")

    # --- Shared crate: the bootc query layer calls the same guard -------
    # update_check / status_commands in bootc_query.rs invoke the guard
    # by absolute path (via `sudo -n` for unprivileged callers) — that is
    # the Updates page's *check* path, so it is repointed with the rest.
    # The image-reference fallback also names the upstream GHCR repository in a
    # regex, a substring probe, and its test fixtures; all take Rowyn's.
    bootc_query = root / "src/rowyn-shared-rs/src/system/bootc_query.rs"
    text = bootc_query.read_text()
    text, query_guard_count = replace_all_counted(
        text, "/usr/bin/kyth-bootc-guard", "/usr/bin/rowyn-bootc-guard")
    text = replace_once(text, r"ghcr\.io/kyth-os/kyth", r"ghcr\.io/mrtrickdotorg/rowyn")
    text, query_ref_count = replace_all_counted(
        text, "ghcr.io/kyth-os/kyth", "ghcr.io/mrtrickdotorg/rowyn")
    text, query_prose_count = replace_all_counted(
        text, "kyth-safe-upgrade", "rowyn-safe-upgrade")
    # bootc's composefs backend (Rowyn's) prints different check output than
    # the ostree backend the classifier was written for: "No changes in: <ref>"
    # when the image is already pulled, otherwise only ManifestDiff::print()
    # ("Total new layers:" / "Removed layers:" / "Added layers:") with no
    # "Update available for:" line. Unknown successful output was rejected, so
    # a real update surfaced as "Check unavailable". Teach the classifier the
    # diff block, requiring both its first and last lines (fail closed).
    text = replace_once(
        text,
        """    if lower.contains("update available for:") {
        Some("available")
    } else if""",
        """    if lower.contains("update available for:")
        || (lower.contains("total new layers:") && lower.contains("added layers:"))
    {
        Some("available")
    } else if""")
    text = replace_once(
        text,
        """    #[test]
    fn update_check_state_rejects_unknown_success_output() {""",
        """    #[test]
    fn update_check_state_accepts_composefs_manifest_diff_output() {
        let diff = "Total new layers: 30    Size: 3.2 GB\n\
                    Removed layers:   25    Size: 2.6 GB\n\
                    Added layers:     27    Size: 3.2 GB";
        assert_eq!(update_check_state(diff), Some("available"));
        assert_eq!(update_check_state("Total new layers: 30"), None);
    }

    #[test]
    fn update_check_state_rejects_unknown_success_output() {""")
    bootc_query.write_text(text)
    changes.append(
        f"bootc_query.rs: guard -> /usr/bin/rowyn-bootc-guard ({query_guard_count} paths); "
        f"composefs ManifestDiff check output -> available; "
        f"image-reference fallback -> ghcr.io/mrtrickdotorg/rowyn (1 regex + {query_ref_count} literals "
        f"incl. fixtures); prose ({query_prose_count})")

    # --- Feedback issues go to the Rowyn repository ------------------------
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(text, "/// Opens a prefilled `kyth-os/kyth` issue",
                        "/// Opens a prefilled `mrtrickdotorg/Rowyn` issue")
    text = replace_once(text, '        "https://github.com/kyth-os/kyth",',
                        '        "https://github.com/mrtrickdotorg/Rowyn",')
    main_rs.write_text(text)
    changes.append("main.rs: feedback issue URL -> mrtrickdotorg/Rowyn")

    # --- VPN: on-disk paths take the Rowyn name ---------------------------
    # The backend logic (openconnect argv, SAML flow, askpass gate, the one
    # firewall-cmd lockdown call) ports unchanged; only the paths a user or
    # admin could meet are renamed. Each replace_once asserts exactly one
    # anchor per file, so the saved-profile path is renamed in exactly the
    # two places it is constructed (vpn.rs writer, main.rs reader).
    vpn_rs = crate / "src/commands/vpn.rs"
    text = vpn_rs.read_text()
    text = replace_once(text, ".config/kyth-vpn-connect", ".config/rowyn-vpn-connect")
    text = replace_once(text, "/etc/kyth/network.toml", "/etc/rowyn/network.toml")
    vpn_rs.write_text(text)
    text = main_rs.read_text()
    text = replace_once(text, ".config/kyth-vpn-connect", ".config/rowyn-vpn-connect")
    main_rs.write_text(text)
    preset = root / "src/rowyn-shared-rs/src/system/network_preset.rs"
    text = preset.read_text()
    text = replace_once(text, "/etc/kyth/network.toml", "/etc/rowyn/network.toml")
    preset.write_text(text)
    changes.append(
        "vpn.rs + main.rs: saved profile -> ~/.config/rowyn-vpn-connect (2 anchors); "
        "network_preset.rs + vpn.rs comment: preset -> /etc/rowyn/network.toml")

    # --- VPN surfaces: user-visible branding -------------------------------
    vpn_page = web / "src/pages/Vpn.tsx"
    text = vpn_page.read_text()
    text = replace_once(text, "complete sign-in securely inside Kyth Hub.",
                        "complete sign-in securely inside Rowyn Hub.")
    vpn_page.write_text(text)
    vpn_section = web / "src/components/VpnSection.tsx"
    text = vpn_section.read_text()
    text = replace_once(text, "available from the installed Kyth Hub.",
                        "available from the installed Rowyn Hub.")
    vpn_section.write_text(text)
    changes.append("Vpn.tsx + VpnSection.tsx: Kyth Hub -> Rowyn Hub (2 strings)")

    # --- Branding sweep: no upstream brand copy survives ------------------
    # The passes above rebranded the files they touched, and strings in
    # shared modules, data files, and untouched components slipped
    # through — Patrick's installed-system screenshot caught the Updates
    # page rendering "KythOS is up to date. No restart is needed." out of
    # updateMessages.ts. Sweep the rest mechanically: counted
    # replace-alls per file, each reported, with the regression test
    # asserting zero survivors outside the one provenance allowlist
    # entry (compat_games.json — see the module docstring).
    sweep_notes = []

    def sweep(relpath, pairs):
        path = root / relpath
        text = path.read_text()
        notes = []
        for old, new in pairs:
            text, count = replace_all_counted(text, old, new)
            notes.append(f"{old} x{count}")
        path.write_text(text)
        sweep_notes.append(f"{path.name}: " + ", ".join(notes))

    web_src = "src/rowyn-hub-web/src"
    # Update status/error copy shared by the Updates and Apps pages;
    # this file carries the exact sentence from the screenshot.
    sweep(f"{web_src}/components/updateMessages.ts", (("KythOS", "Rowyn"),))
    # Service-layer copy: the Secure Boot prompt, a comment, and the
    # not-in-shell error family ("... from the installed Kyth Hub.").
    sweep(f"{web_src}/services/liveData.ts",
          (("KythOS", "Rowyn"), ("Kyth Hub", "Rowyn Hub")))
    # Apps page leftovers the section anchors above did not cover: the
    # footer heading, the Kali card copy, and the dev-environments copy.
    # Recipe ids (setup-kyth-dev-box) are backend identifiers, not copy,
    # and stay untouched.
    store = web / "src/components/AppStoreSection.tsx"
    text = store.read_text()
    text, store_os = replace_all_counted(text, "KythOS", "Rowyn")
    text, store_hub = replace_all_counted(text, "Kyth Hub", "Rowyn Hub")
    text = replace_once(text, "Set up optional Kyth development, AI, and Android tooling",
                        "Set up optional Rowyn development, AI, and Android tooling")
    text = replace_once(text, 'label="Kyth developer box"', 'label="Rowyn developer box"')
    store.write_text(text)
    sweep_notes.append(f"AppStoreSection.tsx: KythOS x{store_os}, Kyth Hub x{store_hub}, dev copy x2")
    # The exe-handler dialog is mounted by the shell itself (App.tsx
    # imports it directly), so its copy is a kept surface even though it
    # is not a page; its suggestion data ships the same branding.
    sweep(f"{web_src}/components/ExeHandlerDialog.tsx",
          (("KythOS", "Rowyn"), ("Kyth runs Windows software", "Rowyn runs Windows software")))
    sweep("build_files/exe-handler-apps.json", (("KythOS", "Rowyn"),))
    # The fallback note renders on kept sections (VPN among them) when a
    # live reading has not arrived; it named the upstream probe unit.
    live_card = web / "src/components/LiveSectionCard.tsx"
    text = live_card.read_text()
    text = replace_once(text, "kyth-probe.service fills this in on a real KythOS install.",
                        "The probe service fills this in on a real Rowyn install.")
    live_card.write_text(text)
    sweep_notes.append("LiveSectionCard.tsx: probe fallback note")
    # Work Setup is a kept Apps section (focus sessions); its copy pairs
    # with the focus-session inhibit reason rebranded in main.rs below.
    work_setup = web / "src/components/WorkSetupSection.tsx"
    text = work_setup.read_text()
    text = replace_once(text, "The focus timer ended, but Kyth",
                        "The focus timer ended, but Rowyn")
    text = replace_once(text, "Kyth keeps the PC awake", "Rowyn keeps the PC awake")
    work_setup.write_text(text)
    sweep_notes.append("WorkSetupSection.tsx: focus copy x2")
    # Cut-surface components stay in the tree unreferenced; their copy
    # takes the same name so the tree carries exactly one brand.
    for relpath, pairs in (
        (f"{web_src}/components/CompatibilitySection.tsx", (("KythOS", "Rowyn"),)),
        (f"{web_src}/components/DevToolsWizard.tsx", (("KythOS", "Rowyn"),)),
        (f"{web_src}/components/GamingSection.tsx", (("KythOS", "Rowyn"), ("Kyth", "Rowyn"))),
        (f"{web_src}/components/MoveInOverview.tsx", (("KythOS", "Rowyn"),)),
        (f"{web_src}/components/PerformanceSection.tsx", (("KythOS", "Rowyn"),)),
        (f"{web_src}/components/PlayOverview.tsx", (("KythOS", "Rowyn"),)),
    ):
        sweep(relpath, pairs)
    repair = web / "src/components/RepairSection.tsx"
    text = repair.read_text()
    text = replace_once(text, "Kyth does not choose or delete backup data for you.",
                        "Rowyn does not choose or delete backup data for you.")
    repair.write_text(text)
    sweep_notes.append("RepairSection.tsx: backup copy")
    theme = web / "src/styles/theme.css"
    text = theme.read_text()
    text = replace_once(text, "KythOS Hub", "Rowyn Hub")
    theme.write_text(text)
    sweep_notes.append("theme.css: header comment")

    # Shell (Rust): the focus-session inhibit reason is user-visible in
    # systemd-inhibit listings; the exe-handler suggestions and the
    # config-path error render in the dialog flow. The shared crate's
    # stale-inhibit reaper matches the reason literally, so it is swept
    # in the same pass to keep the pair consistent.
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text, main_os = replace_all_counted(text, "KythOS", "Rowyn")
    text, main_hub = replace_all_counted(text, "Kyth Hub", "Rowyn Hub")
    text = replace_once(text, '"invalid Kyth configuration path"',
                        '"invalid Rowyn configuration path"')
    main_rs.write_text(text)
    sweep_notes.append(f"main.rs: KythOS x{main_os}, Kyth Hub x{main_hub}, config-path error")
    process_rs = root / "src/rowyn-shared-rs/src/system/process.rs"
    text = process_rs.read_text()
    text, reap_count = replace_all_counted(
        text, "KythOS Focus Session", "Rowyn Focus Session")
    process_rs.write_text(text)
    sweep_notes.append(f"process.rs: focus-session matcher x{reap_count} (pairs with main.rs)")

    # The boot-health fallback in updates.rs keyed the OS identity check
    # to upstream's os-release ID; on Rowyn (ID=rowyn) it appended a
    # spurious failure and reported the system unhealthy. Rekey it.
    updates = crate / "src/commands/updates.rs"
    text = updates.read_text()
    text = replace_once(
        text,
        '== "ID=kythos" || line.trim() == "ID=\\"kythos\\""',
        '== "ID=rowyn" || line.trim() == "ID=\\"rowyn\\""')
    text = replace_once(
        text,
        '"KythOS identity: /usr/lib/os-release is not ID=kythos"',
        '"Rowyn identity: /usr/lib/os-release is not ID=rowyn"')
    updates.write_text(text)
    sweep_notes.append("updates.rs: boot-health identity rekeyed ID=kythos -> ID=rowyn")

    # Shared-crate display strings the shell surfaces: the boot-health
    # quarantine summary (boot menu / status) and a desktop-stack check
    # detail shown in health views.
    boot_health = root / "src/rowyn-shared-rs/src/system/boot_health.rs"
    text = boot_health.read_text()
    text = replace_once(text, "KythOS update quarantined after",
                        "Rowyn update quarantined after")
    boot_health.write_text(text)
    desktop_stack = root / "src/rowyn-shared-rs/src/system/desktop_stack.rs"
    text = desktop_stack.read_text()
    text = replace_once(text, "KythOS ships Plasma Wayland only",
                        "Rowyn ships Plasma Wayland only")
    desktop_stack.write_text(text)
    sweep_notes.append("boot_health.rs + desktop_stack.rs: display strings")
    changes.append("branding sweep: " + "; ".join(sweep_notes))

    # ======================================================================
    # Audit fixes for the kept pages (Dashboard, Apps, Updates, VPN).
    # Every patch below is anchored and fails the build if upstream drifts.
    # ======================================================================
    audit_notes = []

    # --- B3: no `just` recipe buttons ---------------------------------------
    # Every RecipeButton runs /usr/bin/just against /usr/share/ublue-os/
    # justfile; Rowyn ships neither and the recipes are Fedora-only. The
    # buttons are cut from the kept pages (the Rust run_hub_action command
    # stays registered but has no caller; RecipeButton stays exported for
    # the unreferenced cut pages). The Flatpak-based host-tools grid and the
    # Kali card do not use just and stay.
    store = web / "src/components/AppStoreSection.tsx"
    text = store.read_text()
    text = replace_once(
        text,
        'import { ActionStatus, ProgressRing, RecipeButton, useSectionAction } from "./SectionActions";',
        'import { ActionStatus, ProgressRing, useSectionAction } from "./SectionActions";')
    text = cut_between(
        text,
        '<div className="app-special-card app-dev-card">',
        "</section>\n    </div> : <SectionFallbackNote")
    text = cut_between(
        text,
        '<p className="app-subsection-copy">More Rowyn applications</p>',
        "<ActionStatus status={status} /></div>\n  </LiveSectionCard>")
    store.write_text(text)

    work_setup = web / "src/components/WorkSetupSection.tsx"
    text = work_setup.read_text()
    text = replace_once(
        text,
        'import { ActionButton, ActionStatus, RecipeButton, useSectionAction } from "./SectionActions";',
        'import { ActionButton, ActionStatus, useSectionAction } from "./SectionActions";')
    text = replace_once(
        text,
        '          <RecipeButton recipe="install-ms-fonts" label="Install Office fonts" busy={busy} run={run} runCancel={runCancel} />\n'
        '          <RecipeButton recipe="setup-printer" label="Set up a printer" busy={busy} run={run} runCancel={runCancel} />\n',
        "")
    text = replace_once(
        text, '"Microsoft-compatible fonts support office documents."',
        '"Metric-compatible fonts (Liberation) keep Office documents laid out correctly."')
    work_setup.write_text(text)

    vpn_section = web / "src/components/VpnSection.tsx"
    text = vpn_section.read_text()
    text = replace_once(
        text,
        'import { ActionButton, ActionStatus, ProgressRing, RecipeButton, useSectionAction } from "./SectionActions";',
        'import { ActionButton, ActionStatus, ProgressRing, useSectionAction } from "./SectionActions";')
    text = replace_once(
        text,
        '          <RecipeButton recipe="setup-tailscale" label="Set up Tailscale" busy={busy} run={run} runCancel={runCancel} />\n',
        "")
    vpn_section.write_text(text)
    audit_notes.append(
        "B3: cut 12 just RecipeButtons (AppStore dev card x5 + footer x3, Work Setup x2, VPN x1) and their imports")

    # --- D2: no Guardian surface ---------------------------------------------
    # kyth-guardian is not shipped and ~/.local/state/kyth/guardian.json
    # never exists, so the tile read "Healthy" from nothing. Cut the
    # Dashboard tile + history card (with their fetch plumbing; tsc rejects
    # unused locals) and the Topbar bell, whose only source was Guardian.
    dashboard = web / "src/pages/Dashboard.tsx"
    text = dashboard.read_text()
    text = replace_once(text, 'import type { GuardianEvent } from "../data/dashboardTypes";\n', "")
    text = replace_once(text, 'import { GuardianHistoryCard } from "../components/GuardianHistoryCard";\n', "")
    text = replace_once(
        text,
        "  fetchGuardianSnapshot,\n  dismissGuardianRecommendation,\n  invokeGuardianExecute,\n", "")
    text = replace_once(
        text,
        "  relativeTime,\n  type BootRuntimeCheck,\n  type GuardianSnapshot,\n",
        "  type BootRuntimeCheck,\n")
    text = replace_once(
        text, "  const [guardian, setGuardian] = useState<GuardianSnapshot | null>(null);\n", "")
    text = replace_once(
        text,
        "const [nextGuardian, nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery] = await Promise.all([\n"
        "        fetchGuardianSnapshot(), fetchUpdateChannel(),",
        "const [nextChannel, nextGpu, nextStorage, nextUser, nextBoot, nextRecovery] = await Promise.all([\n"
        "        fetchUpdateChannel(),")
    text = replace_once(text, "      setGuardian(nextGuardian);\n", "")
    text = replace_once(
        text,
        "const hasInitialReadings = nextGuardian !== null || nextBoot !== null",
        "const hasInitialReadings = nextBoot !== null")
    text = replace_once(
        text,
        "    // The 60 s refresh re-reads every tile, not just Guardian — the other\n"
        "    // seven readings used to freeze at their mount-time values for the\n"
        "    // page's whole life. A failed re-read keeps the last good value: one\n"
        "    // transient failure must not blank a tile that was reading fine (the\n"
        "    // Guardian-only version of this poll did exactly that).\n",
        "    // The 60 s refresh re-reads every tile. A failed re-read keeps the\n"
        "    // last good value: one transient failure must not blank a tile that\n"
        "    // was reading fine.\n")
    text = replace_once(
        text,
        "        fetchGuardianSnapshot(), fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]).then(([nextGuardian, nextChannel,",
        "        fetchUpdateChannel(), fetchGpuName(), fetchStorageFree(),\n"
        "        fetchUserName(), fetchBootRuntimeChecks(), fetchRecoveryStatus(),\n"
        "      ]).then(([nextChannel,")
    text = replace_once(text, "        setGuardian((prev) => nextGuardian ?? prev);\n", "")
    text = cut_between(text, "  const recentGuardianHistory = ", "  const hasReadings = ")
    text = replace_once(
        text,
        "const hasReadings = guardian !== null || bootChecks !== null",
        "const hasReadings = bootChecks !== null")
    text = replace_once(
        text,
        "  const guardianGood = guardian === null ? null : guardian.pendingCount === 0;\n", "")
    text = cut_between(text, "  const guardianLabel = ", "  const dashboardLabel = ")
    text = replace_once(text, '{guardianGood === false || healthGood === false ? "Needs attention" : dashboardLabel}',
                        '{healthGood === false ? "Needs attention" : dashboardLabel}')
    text = replace_once(
        text,
        '          <HomeCard icon="✓" label="Guardian" value={guardianLabel} detail={guardianDetail} good={guardianGood} pendingLabel="PENDING" />\n',
        "")
    text = cut_between(text, '      <div className="home-content-heading">', "    </div>\n  );\n}\n")
    dashboard.write_text(text)

    topbar = web / "src/components/Topbar.tsx"
    text = topbar.read_text()
    text = replace_once(text, 'import { IconSearch, IconBell } from "./icons";',
                        'import { IconSearch } from "./icons";')
    text = replace_once(text, 'import { fetchGuardianSnapshot } from "../services/liveData";\n', "")
    text = cut_between(text, "  // The bell shipped with a permanently-lit unread dot and no source",
                       "  const results: SearchResult[] = open")
    text = cut_between(text, "  // Refetch on an interval: a one-shot fetch at shell mount would show",
                       "  useEffect(() => {\n    const onPointerDown")
    text = cut_between(text, '        <button\n          className="glass icon-button"',
                       "      </div>\n    </header>")
    topbar.write_text(text)
    audit_notes.append(
        "D2: Dashboard Guardian tile/history card + fetch plumbing and Topbar Guardian bell cut "
        "(HeroCard is unreferenced upstream; Sidebar/search have no Guardian entry)")

    # --- D3: no release notes / update history; "stable" channel name -------
    # The channel is `stable` and mrtrickdotorg/Rowyn has no GitHub releases, so the
    # release-notes block and the Update history card were always empty.
    # Cut both (state, effects, helpers, imports go with them) and map the
    # raw channel for display. No network call is added.
    overview = web / "src/components/UpdatesOverview.tsx"
    text = overview.read_text()
    text = replace_once(text, 'import { ExternalLink } from "./ExternalLink";\n', "")
    text = replace_once(
        text, "  fetchUpdateReleaseHistory,\n  fetchUpdateReleaseSummary,\n", "")
    text = replace_once(
        text, "  type UpdateHistoryEntry,\n  type UpdateReleaseSummary,\n", "")
    text = cut_between(text, "const RELEASE_SUMMARY_RETRY_DELAY_MS", "function updatePhaseIndex")
    text = cut_between(
        text, "  const [releaseSummary, setReleaseSummary]",
        "  // The update job (if any) that was already in flight when this page")
    text = cut_between(
        text, "  const updateDigest = updateStatus?.remote_digest ?? null;\n",
        '  // "blocked" (e.g. a quarantined update held back for safety) and "busy"\n')
    text = cut_between(
        text, "      {(releaseSummary || releaseSummaryLoading || releaseSummaryUnavailable) && (",
        '      <div className="updates-actions-card updates-primary-actions">')
    text = cut_between(
        text, '      <section className="updates-history-card"',
        '      <details className="updates-details">')
    overview.write_text(text)

    live_data = web / "src/services/liveData.ts"
    text = live_data.read_text()
    # Rowyn's own channels are `stable` (the current update target) and `test`
    # (planned). Name both here so a future `:test` image reads as "Test"
    # instead of falling through to the raw tag; unknown tags still fall back
    # to the tag itself rather than being given an invented name.
    text = replace_once(text, '  latest: "Stable (latest)",\n',
                        '  stable: "Stable",\n  test: "Test",\n  latest: "Stable (latest)",\n')
    live_data.write_text(text)
    audit_notes.append(
        "D3: release-notes block + Update history card cut from UpdatesOverview; "
        "CHANNEL_DISPLAY maps stable -> Stable and test -> Test")

    # --- B2: system Flatpak update/uninstall without the Kyth daemon ---------
    # commands::privilege::flatpak_update / flatpak_uninstall talk to
    # /run/kyth/privileged.sock, a root daemon Rowyn does not ship. The
    # system phase now runs /usr/bin/flatpak --system as the user and relies
    # on Flatpak's own polkit-authorised system helper (flatpak 1.18.4 from
    # Ubuntu stonking ships org.freedesktop.Flatpak.policy + a rules.d file:
    # app-update is allow_active=yes, and app-uninstall is YES for an active
    # local member of the sudo group, auth_admin_keep otherwise). Note that
    # `-y` and `--noninteractive` both disable *interactive* polkit
    # authorisation, and without a TTY Flatpak cannot ask its yes/no
    # question, so a non-sudo user's uninstall is denied (not prompted) and
    # reported as such.
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "            privileged_flatpak_uninstall(&app_id).map(|detail| (true, detail))\n",
        "            system_flatpak_uninstall(&app_id, &cancel).map(|detail| (true, detail))\n")
    text = replace_once(
        text,
        "fn privileged_flatpak_uninstall(app_id: &str) -> Result<String, String> {\n"
        "    commands::privilege::flatpak_uninstall(app_id)\n"
        "}\n",
        r"""/// Fixed argv for the system-scope Flatpak update. Rowyn ships no root
/// daemon: `flatpak` runs as the user and the system installation is
/// changed by Flatpak's own polkit-authorised system helper. `-y` and
/// `--noninteractive` also turn off interactive polkit authorisation (and
/// Flatpak could not ask its yes/no question without a TTY anyway), so only
/// operations polkit allows without a prompt succeed: updates for an active
/// local session, and removals for an active local member of the sudo group.
fn system_flatpak_update_args() -> Vec<String> {
    ["update", "--system", "--noninteractive", "-y"]
        .iter()
        .map(|arg| arg.to_string())
        .collect()
}

/// Fixed argv for removing one system-scope app. The id is validated here
/// again so this builder is safe on its own, and follows `--` so it can
/// never be read as an option.
fn system_flatpak_uninstall_args(app_id: &str) -> Result<Vec<String>, String> {
    commands::privilege::validate_flatpak_id(app_id)?;
    Ok(["uninstall", "--system", "--noninteractive", "-y", "--", app_id]
        .iter()
        .map(|arg| arg.to_string())
        .collect())
}

/// Turn a failed system-scope Flatpak run into text the user can act on.
/// polkit refusing the (non-interactive) request surfaces as a D-Bus
/// "... not allowed for user" error from the system helper.
fn system_flatpak_failure(stderr: &str, removing: bool) -> String {
    let lower = stderr.to_lowercase();
    if lower.contains("not allowed for user")
        || lower.contains("not authorized")
        || lower.contains("authorization")
        || lower.contains("authentication is required")
    {
        return if removing {
            "Removing a system-wide app needs an administrator. Sign in as an administrator \
             (a member of the sudo group) or ask one to remove it."
                .to_string()
        } else {
            "System-wide app updates need an administrator. Sign in as an administrator \
             (a member of the sudo group) or ask one to run the update."
                .to_string()
        };
    }
    let detail = stderr.trim();
    if detail.is_empty() {
        "Flatpak failed without reporting a reason.".to_string()
    } else {
        detail.to_string()
    }
}

/// Run `flatpak --system ...` as the user: bounded by the Flatpak transfer
/// tier and killed within one poll tick when the job's cancel flag is set.
fn run_system_flatpak(args: &[String], removing: bool, cancel: &AtomicBool) -> Result<(), String> {
    let mut command = Command::new("/usr/bin/flatpak");
    command.args(args).stdin(std::process::Stdio::null());
    match kyth_shared::system::process::run_bounded_command_cancel(
        command,
        timeout_for(JobTimeoutClass::FlatpakTransfer),
        cancel,
    ) {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(system_flatpak_failure(
            &commands::process::bounded_text(&output.stderr),
            removing,
        )),
        Err(error) if error.kind() == std::io::ErrorKind::TimedOut => Err(if removing {
            "Removing the app took too long and was stopped.".to_string()
        } else {
            "Updating system-wide apps took too long and was stopped.".to_string()
        }),
        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
            Err("Cancelled.".to_string())
        }
        Err(error) => Err(format!("Could not start Flatpak: {error}")),
    }
}

fn system_flatpak_update(cancel: &AtomicBool) -> Result<(), String> {
    run_system_flatpak(&system_flatpak_update_args(), false, cancel)
}

fn system_flatpak_uninstall(app_id: &str, cancel: &AtomicBool) -> Result<String, String> {
    let args = system_flatpak_uninstall_args(app_id)?;
    run_system_flatpak(&args, true, cancel).map(|()| format!("Uninstalled {app_id}."))
}
""")
    text = replace_once(
        text,
        "        // \"running\" job. The user phase runs as the user (not through the\n"
        "        // privileged socket), so the flatpak transfer tier applies; the\n"
        "        // system phase keeps the daemon's own 900s OPERATION_TIMEOUT.\n",
        "        // \"running\" job. Both phases run `flatpak` as the user, so the\n"
        "        // flatpak transfer tier bounds each of them.\n")
    text = replace_once(
        text,
        "        // (A cancel that lands mid-flight in phase 2 still waits for the\n"
        "        // daemon's own bounded reply; the socket read cannot be preempted.)\n",
        "        // (A cancel that lands during phase 2 kills that flatpak child\n"
        "        // within one poll tick.)\n")
    text = replace_once(
        text,
        "        let system_result = commands::privilege::flatpak_update().map(|_| ());\n",
        "        let system_result = system_flatpak_update(&cancel);\n")
    main_rs.write_text(text)
    privilege = crate / "src/commands/privilege.rs"
    text = privilege.read_text()
    text = replace_once(
        text, "pub(crate) fn flatpak_uninstall(app_id: &str)",
        "#[allow(dead_code)]\npub(crate) fn flatpak_uninstall(app_id: &str)")
    text = replace_once(
        text, "pub(crate) fn flatpak_update()",
        "#[allow(dead_code)]\npub(crate) fn flatpak_update()")
    privilege.write_text(text)
    audit_notes.append(
        "B2: system Flatpak update/uninstall run /usr/bin/flatpak --system --noninteractive -y as the user "
        "(Flatpak's own polkit helper) instead of /run/kyth/privileged.sock")

    # --- D5: external links need a web browser -------------------------------
    # The Hub reported "Opened in browser." after spawning xdg-open even when
    # Rowyn has no https handler (it ships no browser). Check first.
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "#[tauri::command]\nfn open_m365_app(name: String) -> Result<String, String> {\n"
        "    let (url, _) = m365_app(&name).ok_or_else(|| \"unknown Microsoft 365 app\".to_string())?;\n",
        r"""const NO_BROWSER_MESSAGE: &str =
    "No web browser is installed. Install one from Discover, then try again.";

/// The `.desktop` ids to look for on disk for a handler id: the id itself,
/// then the Desktop Entry spec's nested form where `-` stands for `/`
/// (`kde-foo.desktop` may live at `applications/kde/foo.desktop`).
fn desktop_id_candidates(id: &str) -> Vec<String> {
    let mut candidates = vec![id.to_string()];
    let mut nested = id.to_string();
    while let Some(position) = nested.find('-') {
        nested.replace_range(position..position + 1, "/");
        candidates.push(nested.clone());
    }
    candidates
}

/// The first non-empty line of `xdg-mime query default` output, when it
/// names a plain `.desktop` id (no path components).
fn parse_default_handler(stdout: &str) -> Option<String> {
    let id = stdout.lines().map(str::trim).find(|line| !line.is_empty())?;
    (id.ends_with(".desktop") && !id.starts_with('/') && !id.contains("..")).then(|| id.to_string())
}

/// XDG data directories that can hold `applications/*.desktop`.
fn xdg_data_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    match std::env::var_os("XDG_DATA_HOME").filter(|value| !value.is_empty()) {
        Some(home) => dirs.push(PathBuf::from(home)),
        None => {
            if let Some(home) = std::env::var_os("HOME") {
                dirs.push(PathBuf::from(home).join(".local/share"));
            }
        }
    }
    let system = std::env::var("XDG_DATA_DIRS")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "/usr/local/share:/usr/share".to_string());
    dirs.extend(system.split(':').filter(|dir| !dir.is_empty()).map(PathBuf::from));
    dirs
}

fn desktop_entry_exists(id: &str, data_dirs: &[PathBuf]) -> bool {
    data_dirs.iter().any(|dir| {
        desktop_id_candidates(id)
            .iter()
            .any(|candidate| dir.join("applications").join(candidate).is_file())
    })
}

/// Fail before launching when nothing can open an https URL: `xdg-open`
/// exits quietly (or hands the URL to nothing) on a system without a
/// browser, and the Hub used to report success regardless.
fn ensure_https_handler() -> Result<(), String> {
    let argv = [
        "/usr/bin/xdg-mime".to_string(),
        "query".to_string(),
        "default".to_string(),
        "x-scheme-handler/https".to_string(),
    ];
    let output = kyth_shared::system::process::run_bounded(&argv, std::time::Duration::from_secs(5))
        .map_err(|error| format!("Could not check for a web browser: {error}"))?;
    if !output.status.success() {
        return Err(NO_BROWSER_MESSAGE.to_string());
    }
    let handler = parse_default_handler(&String::from_utf8_lossy(&output.stdout))
        .ok_or_else(|| NO_BROWSER_MESSAGE.to_string())?;
    if desktop_entry_exists(&handler, &xdg_data_dirs()) {
        Ok(())
    } else {
        Err(NO_BROWSER_MESSAGE.to_string())
    }
}

#[tauri::command]
fn open_m365_app(name: String) -> Result<String, String> {
    let (url, _) = m365_app(&name).ok_or_else(|| "unknown Microsoft 365 app".to_string())?;
    ensure_https_handler()?;
""")
    text = replace_once(
        text,
        "        return Err(\"invalid external URL\".to_string());\n    }\n",
        "        return Err(\"invalid external URL\".to_string());\n    }\n    ensure_https_handler()?;\n")
    main_rs.write_text(text)
    audit_notes.append("D5: open_m365_app/open_external_url check for an https handler before xdg-open")

    # --- D1 / D6: shared crate ---------------------------------------------------
    history = root / "src/rowyn-shared-rs/src/system/deployment_history.rs"
    text = history.read_text()
    text = replace_once(
        text,
        '    let data = crate::system::probe::read_section("bootc-status-data").unwrap_or(Value::Null);\n',
        '    let data = crate::system::probe::read_section("bootc-status-data")\n'
        '        .or_else(crate::system::bootc_query::fetch_status_data)\n'
        '        .unwrap_or(Value::Null);\n')
    history.write_text(text)
    audit_notes.append("D1: deployment_history falls back to bootc_query::fetch_status_data")

    fonts = root / "src/rowyn-shared-rs/src/system/fonts_ready.rs"
    text = fonts.read_text()
    text = replace_once(
        text, "//! Checks Noto Sans + Arial via fc-list; mirrors Python's 5s timeout each.",
        "//! Checks Noto Sans + an Arial-metric font via fc-list (5s timeout each).\n"
        "//! Liberation Sans/Serif/Mono are metric-compatible with Arial, Times New\n"
        "//! Roman, and Courier New, so Liberation Sans counts as Office-font ready.")
    text = replace_once(
        text,
        '    let has_ms = run_fc(":family=Arial")\n'
        '        .map(|s| !s.trim().is_empty())\n'
        '        .unwrap_or(false);\n'
        '    if has_noto && has_ms {\n'
        '        (true, "Noto + MS fonts ready".to_string())\n'
        '    } else if has_noto {\n'
        '        (\n'
        '            false,\n'
        '            "Noto ready, MS via ujust install-ms-fonts".to_string(),\n'
        '        )\n'
        '    } else {\n'
        '        (false, "fonts check pending".to_string())\n'
        '    }\n'
        '}\n',
        '    let has_office_metrics = [":family=Liberation Sans", ":family=Arial"]\n'
        '        .iter()\n'
        '        .any(|pattern| {\n'
        '            run_fc(pattern)\n'
        '                .map(|s| !s.trim().is_empty())\n'
        '                .unwrap_or(false)\n'
        '        });\n'
        '    fonts_status(has_noto, has_office_metrics)\n'
        '}\n'
        '\n'
        '/// The verdict and user-facing copy for the two font probes.\n'
        'fn fonts_status(has_noto: bool, has_office_metrics: bool) -> (bool, String) {\n'
        '    if has_noto && has_office_metrics {\n'
        '        (\n'
        '            true,\n'
        '            "Noto + metric-compatible Office fonts ready (Liberation)".to_string(),\n'
        '        )\n'
        '    } else if has_noto {\n'
        '        (\n'
        '            false,\n'
        '            "Noto ready; metric-compatible Office fonts (Liberation Sans) not found"\n'
        '                .to_string(),\n'
        '        )\n'
        '    } else {\n'
        '        (false, "fonts check pending".to_string())\n'
        '    }\n'
        '}\n')
    text = replace_once(
        text,
        "    #[test]\n    fn returns_tuple() {",
        '    #[test]\n'
        '    fn liberation_or_arial_counts_as_office_fonts_ready() {\n'
        '        let (ready, detail) = fonts_status(true, true);\n'
        '        assert!(ready);\n'
        '        assert!(detail.contains("Liberation"));\n'
        '        let (ready, detail) = fonts_status(true, false);\n'
        '        assert!(!ready);\n'
        '        assert!(detail.contains("Liberation Sans"));\n'
        '        assert_eq!(fonts_status(false, true), (false, "fonts check pending".to_string()));\n'
        '        assert_eq!(fonts_status(false, false), (false, "fonts check pending".to_string()));\n'
        '    }\n'
        '\n'
        '    #[test]\n'
        '    fn copy_never_mentions_ujust() {\n'
        '        for (noto, metrics) in [(true, true), (true, false), (false, false)] {\n'
        '            let (_, detail) = fonts_status(noto, metrics);\n'
        '            assert!(!detail.contains("ujust") && !detail.contains("MS "), "{detail}");\n'
        '        }\n'
        '    }\n'
        '\n'
        '    #[test]\n    fn returns_tuple() {')
    fonts.write_text(text)
    audit_notes.append("D6: fonts_ready counts Liberation Sans (or Arial) as Office-font ready; no ujust copy")

    # --- Rust unit tests for the shell-side logic added above ----------------------
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        '        .expect("error while running the Rowyn Hub shell");\n}\n',
        r'''        .expect("error while running the Rowyn Hub shell");
}

#[cfg(test)]
mod audit_fix_tests {
    use super::*;

    #[test]
    fn system_flatpak_update_runs_directly_as_the_user() {
        assert_eq!(
            system_flatpak_update_args(),
            ["update", "--system", "--noninteractive", "-y"]
        );
    }

    #[test]
    fn system_flatpak_uninstall_validates_the_id_and_follows_a_separator() {
        assert_eq!(
            system_flatpak_uninstall_args("org.mozilla.firefox").unwrap(),
            [
                "uninstall",
                "--system",
                "--noninteractive",
                "-y",
                "--",
                "org.mozilla.firefox"
            ]
        );
        for bad in ["", "-v.x", "--all", "org.mozilla.firefox; rm", "a b.c", "x"] {
            assert!(system_flatpak_uninstall_args(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn system_flatpak_failures_are_accurate_and_never_blame_a_helper_service() {
        let denied = "error: Flatpak system operation Uninstall not allowed for user";
        let removal = system_flatpak_failure(denied, true);
        assert!(removal.contains("administrator") && removal.contains("remove"));
        let update = system_flatpak_failure(denied, false);
        assert!(update.contains("administrator") && update.contains("update"));
        assert_eq!(
            system_flatpak_failure("  error: remote not found \n", false),
            "error: remote not found"
        );
        assert_eq!(
            system_flatpak_failure("", true),
            "Flatpak failed without reporting a reason."
        );
        for text in [removal, update] {
            let lower = text.to_lowercase();
            assert!(!lower.contains("helper service") && !lower.contains("privileged"));
        }
    }

    #[test]
    fn handler_ids_parse_and_nested_desktop_ids_are_tried() {
        assert_eq!(
            parse_default_handler("\n firefox.desktop \n"),
            Some("firefox.desktop".to_string())
        );
        for bad in ["", "\n\n", "firefox", "/usr/share/applications/x.desktop", "../x.desktop"] {
            assert_eq!(parse_default_handler(bad), None, "{bad:?}");
        }
        assert_eq!(
            desktop_id_candidates("kde-org-foo.desktop"),
            ["kde-org-foo.desktop", "kde/org-foo.desktop", "kde/org/foo.desktop"]
        );
    }

    #[test]
    fn desktop_entries_are_found_in_data_dirs_only_when_they_exist() {
        let root = std::env::temp_dir().join(format!("rowyn-hub-desktop-{}", std::process::id()));
        let applications = root.join("applications");
        fs::create_dir_all(applications.join("kde")).unwrap();
        fs::write(applications.join("firefox.desktop"), "[Desktop Entry]\n").unwrap();
        fs::write(applications.join("kde/browser.desktop"), "[Desktop Entry]\n").unwrap();
        let dirs = vec![PathBuf::from("/nonexistent-rowyn-dir"), root.clone()];
        assert!(desktop_entry_exists("firefox.desktop", &dirs));
        assert!(desktop_entry_exists("kde-browser.desktop", &dirs));
        assert!(!desktop_entry_exists("chromium.desktop", &dirs));
        assert!(!desktop_entry_exists("firefox.desktop", &[]));
        fs::remove_dir_all(&root).unwrap();
    }
}
''')
    main_rs.write_text(text)
    audit_notes.append("main.rs: audit_fix_tests (system flatpak argv/failure text, https handler lookup)")
    changes.append("audit fixes: " + "; ".join(audit_notes))

    # ======================================================================
    # Slice 1.5: Kyth-shaped content and dead helpers out of the kept
    # surfaces. The B3/D2 audit cut the kept pages' buttons and tiles;
    # this pass removes what they pointed at — the Kali catalog card and
    # its backend, the Guardian command family (the shell's only
    # invocations of /usr/bin/kyth-guardian), and the open-* commands
    # that spawn /usr/bin/kyth-welcome-launch — so nothing in the shipped
    # Hub can try to run a helper Rowyn does not ship. The cut pages'
    # frontend plumbing in liveData.ts stays (it keeps those unreferenced
    # files compiling, the same trade B2 made for privilege.rs); the
    # Flatpak host-tools grid that shared security.rs with the Kali
    # lifecycle is generic Apps functionality and stays untouched.
    # ======================================================================
    slice15_notes = []

    # --- Kali card + service functions off the Apps page -------------------
    store = web / "src/components/AppStoreSection.tsx"
    text = store.read_text()
    text = replace_once(
        text,
        "  fetchKaliStatus, fetchSecHostTools, createKaliBox, exportKaliApps,\n"
        "  removeKaliBox, enterKaliTerminal, installSecHostTool, uninstallSecHostTool,\n",
        "  fetchSecHostTools, installSecHostTool, uninstallSecHostTool,\n")
    text = replace_once(
        text, "  cancelAppUpdate, cancelInstall, cancelSecurityJob,\n",
        "  cancelAppUpdate, cancelInstall,\n")
    text = cut_between(text, "\nfunction KaliCard({", "\nfunction HostToolsGrid({")
    text = replace_once(text, "<KaliCard busy={busy} run={run} runCancel={runCancel} />", "")
    store.write_text(text)

    live_data = web / "src/services/liveData.ts"
    text = live_data.read_text()
    # The page that owns the consequence confirms before invoking this; a
    # webview without script dialogs answered the service-level dialog with
    # false and reported "Cancelled." for every removal.
    text = replace_once(text, '  if (!confirmUserAction(`Uninstall ${id}? This removes the application from this system.`)) return "Cancelled.";\n', "")
    text = replace_once(
        text, "; `removeKaliBox` has its own prompt below. */", ". */")
    text = replace_once(
        text, '  "nvidia_install",\n  "kali_remove",\n', '  "nvidia_install",\n')
    text = replace_once(
        text,
        "// Security tab: Kali distrobox lifecycle + host-side (Flatpak) tools grid.\n"
        "// Kali create/export/remove run as background jobs (security_job_status),\n"
        "// same running/complete/failed shape as installFlatpak/uninstallFlatpak\n"
        "// above — polled longer since a \"kali-linux-everything\" pull can run many\n"
        "// minutes. Reported status text, not a live percentage; see\n"
        "// kyth-shared-rs's security_container module doc for why.\n",
        "// Security tab: host-side (Flatpak) tools grid. Installs and removals\n"
        "// run as background jobs (security_job_status), same\n"
        "// running/complete/failed shape as installFlatpak/uninstallFlatpak\n"
        "// above. Reported status text, not a live percentage; see\n"
        "// kyth-shared-rs's security_container module doc for why.\n")
    text = cut_between(text, "/** Kali box presence.", "export interface SecHostTool {")
    text = cut_between(
        text, "export async function createKaliBox(", "export async function installSecHostTool(")
    live_data.write_text(text)
    slice15_notes.append(
        "Kali card + render site cut from AppStoreSection (HostToolsGrid stays); "
        "Kali service functions and the kali_remove privileged-op entry cut from liveData.ts")

    # --- Kali backend out of commands/security.rs --------------------------
    security = crate / "src/commands/security.rs"
    text = security.read_text()
    text = replace_once(
        text,
        "//! Security tab bridge: the Kali distrobox lifecycle (create/enter/export/\n"
        "//! remove) and the host-side (Flatpak) security-tools grid. Command argv\n"
        "//! comes entirely from `kyth_shared::system::security_container`'s fixed\n"
        "//! templates/catalog — nothing here accepts a free-form command or Flatpak\n"
        "//! id from the webview.\n",
        "//! Security tab bridge: the host-side (Flatpak) security-tools grid.\n"
        "//! Command argv comes entirely from\n"
        "//! `kyth_shared::system::security_container`'s fixed catalog — nothing\n"
        "//! here accepts a free-form command or Flatpak id from the webview.\n")
    text = replace_once(
        text,
        "use kyth_shared::system::security_container::{\n"
        "    self, KaliTier, DEFAULT_KALI_BOX, DEFAULT_KALI_IMAGE,\n"
        "};\n",
        "use kyth_shared::system::security_container;\n")
    text = replace_once(text, "use std::sync::atomic::{AtomicBool, Ordering};\n", "")
    text = cut_between(
        text, "/// Single-flight for Kali box lifecycle operations",
        "#[tauri::command]\npub(crate) fn security_job_status")
    if text.count("#[cfg(test)]\nmod tests {") != 1:
        raise ValueError("expected exactly one test module in security.rs")
    text = text[:text.index("#[cfg(test)]\nmod tests {")].rstrip() + "\n"
    security.write_text(text)
    slice15_notes.append(
        "security.rs: Kali lifecycle commands + single-flight slot + their unit test cut; "
        "host-tools grid and security job plumbing untouched")

    # --- Guardian + welcome-launch commands out of the shell ---------------
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "static GUARDIAN_CHECKS: OnceLock<JobStore> = OnceLock::new();\n"
        "fn guardian_checks() -> &'static JobStore {\n"
        "    GUARDIAN_CHECKS.get_or_init(JobStore::default)\n"
        "}\n\n",
        "")
    text = replace_once(
        text, "static GUARDIAN_OP_RUNNING: AtomicBool = AtomicBool::new(false);\n", "")
    text = cut_between(
        text,
        "#[derive(Serialize)]\nstruct GuardianPendingResponse {",
        "#[derive(serde::Serialize)]\nstruct MokStatusResponse {")
    text = replace_once(
        text,
        "            guardian_snapshot,\n"
        "            guardian_check,\n"
        "            guardian_check_status,\n"
        "            guardian_check_cancel,\n"
        "            guardian_control,\n",
        "")
    text = replace_once(
        text, "            guardian_execute_recipe,\n            guardian_dismiss,\n", "")
    text = replace_once(
        text, " and every saved share is lost. Same pattern as guardian state.\n",
        " and every saved share is lost.\n")
    text = cut_between(
        text, "#[tauri::command]\nfn open_cloud_storage_app()", "fn m365_app(")
    text = cut_between(
        text, "#[tauri::command]\nfn open_move_files_app()",
        "#[tauri::command]\nfn ipp_discover()")
    text = replace_once(text, "            open_cloud_storage_app,\n", "")
    text = replace_once(text, "            open_move_files_app,\n", "")
    text = replace_once(text, "            open_network_shares_app,\n", "")
    text = replace_once(
        text,
        "            commands::security::kali_status,\n"
        "            commands::security::kali_create,\n"
        "            commands::security::kali_export,\n"
        "            commands::security::kali_remove,\n"
        "            commands::security::kali_enter_terminal,\n",
        "")
    main_rs.write_text(text)
    slice15_notes.append(
        "main.rs: Guardian command family (7 commands, job store, op slot) and the three "
        "kyth-welcome-launch open-* commands deregistered and deleted; kali_* registrations removed")

    # HubPage is the kept pages' shared shell; its comments still named
    # the removed launcher as the deep-link source.
    hub_page = web / "src/pages/HubPage.tsx"
    text = hub_page.read_text()
    text = replace_once(
        text,
        "// Anchor for a stacked workspace so ?section= deep links (search,\n"
        "// welcome-launch) can scroll straight to it.\n",
        "// Anchor for a stacked workspace so ?section= deep links (search)\n"
        "// can scroll straight to it.\n")
    text = replace_once(
        text,
        "// `kyth-welcome-launch --page Guardian` can land on it (deepLink.ts builds\n"
        "// those URLs).",
        "// an external launcher can land on it (deepLink.ts builds\n"
        "// those URLs).")
    hub_page.write_text(text)
    slice15_notes.append("HubPage.tsx: comments no longer name kyth-welcome-launch")
    changes.append("slice 1.5 cleanup: " + "; ".join(slice15_notes))

    # ======================================================================
    # Legacy junk cut (approved Oct 30, 2026): everything the port pulled
    # over from Kyth that Rowyn does not need. The shell cuts come first
    # (they shrink what the shared crate must keep), then the shared
    # crate itself is pruned to the reviewed closure.
    # ======================================================================
    cut_notes = []

    # --- Orphaned Kyth desktop data files ----------------------------------
    # No importer exists in the frontend or the shell; Rowyn ships its own
    # shared/com.rowyn.hub.desktop in the payload.
    for rel in ("src/data/com.kythos.hub.desktop", "src/data/kyth-welcome.desktop"):
        target = web / rel
        if not target.is_file():
            raise ValueError(f"expected upstream data file to remove: {rel}")
        target.unlink()
    cut_notes.append("com.kythos.hub.desktop + kyth-welcome.desktop data files removed")

    # --- Gaming / dev-tools command modules --------------------------------
    # Their pages were cut in slice 1; nothing in the kept frontend calls
    # these commands (only liveData wrappers for the cut pages name them,
    # the same kept-plumbing trade slice 1.5 made). The modules and their
    # invoke-handler registrations leave together.
    for name in ("gaming", "dev_tools"):
        target = crate / f"src/commands/{name}.rs"
        if not target.is_file():
            raise ValueError(f"expected upstream command module to remove: {name}.rs")
        target.unlink()
    commands_mod = crate / "src/commands/mod.rs"
    text = commands_mod.read_text()
    text = replace_once(text, "pub(crate) mod dev_tools;\n", "")
    text = replace_once(text, "pub(crate) mod gaming;\n", "")
    commands_mod.write_text(text)
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "            commands::gaming::gaming_tools,\n"
        "            commands::gaming::gaming_tool_install,\n"
        "            commands::gaming::gaming_tool_uninstall,\n"
        "            commands::gaming::gaming_tool_launch,\n"
        "            commands::gaming::gaming_job_status,\n"
        "            commands::gaming::gaming_job_cancel,\n"
        "            commands::gaming::fix_discord_screenshare,\n"
        "            commands::gaming::fix_obs_pipewire,\n"
        "            commands::gaming::open_game_folder,\n"
        "            commands::gaming::gaming_perf_status,\n"
        "            commands::gaming::scx_status,\n"
        "            commands::gaming::scx_available,\n"
        "            commands::gaming::scx_set_scheduler,\n"
        "            commands::gaming::per_game_profile,\n"
        "            commands::gaming::save_per_game_profile,\n"
        "            commands::gaming::per_game_launch_options,\n"
        "            commands::dev_tools::dev_tools_catalog_list,\n"
        "            commands::dev_tools::ai_dev_box_status,\n"
        "            commands::dev_tools::dev_tools_install_selection,\n"
        "            commands::dev_tools::dev_tools_job_status,\n"
        "            commands::dev_tools::dev_tools_job_cancel,\n",
        "")
    main_rs.write_text(text)
    cut_notes.append("commands::gaming (16) + commands::dev_tools (5) modules and registrations removed")

    # --- The `just` recipe runner -------------------------------------------
    # run_hub_action executed Kyth's ublue just recipes; the B3 pass cut
    # every RecipeButton from the kept pages and Rowyn ships no justfile.
    # The command, its HubAction recipe enum, its job store and slot, the
    # just_list command, and their tests leave; system::just itself is
    # pruned with the shared crate below (nothing else references it).
    updates = crate / "src/commands/updates.rs"
    text = updates.read_text()
    # HubAction was the file's only Deserialize derive; the import goes
    # with the enum or the shell gains an unused-import warning.
    text = replace_once(
        text, "use serde::{Deserialize, Serialize};\n", "use serde::Serialize;\n")
    text = replace_once(
        text,
        "static HUB_ACTION_JOBS: OnceLock<JobStore> = OnceLock::new();\n"
        "static UPDATE_JOBS: OnceLock<JobStore> = OnceLock::new();\n"
        "\n"
        "fn hub_action_jobs() -> &'static JobStore {\n"
        "    HUB_ACTION_JOBS.get_or_init(JobStore::default)\n"
        "}\n"
        "\n",
        "static UPDATE_JOBS: OnceLock<JobStore> = OnceLock::new();\n"
        "\n")
    text = cut_between(
        text,
        "#[derive(Serialize)]\npub(crate) struct JustRecipeResponse {",
        "/// Start an Updates-page operation as a native Rust-managed job.")
    text = cut_between(
        text,
        "#[tauri::command]\npub(crate) fn run_hub_action(action: HubAction) -> Result<HubActionLaunch, String> {",
        "fn update_store_status(")
    text = cut_between(
        text,
        "#[tauri::command]\npub(crate) fn hub_action_status(job: String) -> crate::InstallStatus {",
        "#[tauri::command]\npub(crate) fn bootc_upgrade()")
    text = replace_once(text, "    use super::{HubAction, *};\n", "    use super::*;\n")
    text = cut_between(
        text,
        "    #[test]\n    fn hub_action_deserializes_allowlisted_recipe() {",
        "    #[test]\n    fn stage_marker_parses_pct_phase_and_detail() {")
    text = cut_between(
        text,
        "    #[test]\n    fn hub_action_read_only_classification_is_explicit() {",
        "    #[test]\n    fn mutating_hub_actions_claim_the_slot_before_the_job_exists() {")
    text = cut_between(
        text,
        "    #[test]\n    fn mutating_hub_actions_claim_the_slot_before_the_job_exists() {",
        "    #[test]\n    fn switch_branch_uses_the_long_transfer_timeout_tier() {")
    updates.write_text(text)
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "            commands::updates::just_list,\n"
        "            commands::updates::run_hub_action,\n"
        "            commands::updates::hub_action_status,\n"
        "            commands::updates::hub_action_cancel,\n",
        "")
    main_rs.write_text(text)
    job_rs = crate / "src/commands/job.rs"
    text = job_rs.read_text()
    text = cut_between(
        text,
        "/// Process-wide single-flight for MUTATING Hub actions",
        "/// Ensure the per-user Flathub remote exists")
    text = cut_between(
        text,
        "    #[test]\n    fn hub_action_slot_is_single_flight_until_dropped() {",
        "    #[test]\n    fn task_job_deadline_fails_a_task_that_ignores_cancellation() {")
    job_rs.write_text(text)
    cut_notes.append(
        "run_hub_action + HubAction enum + just_list + hub-action slot removed "
        "(commands, registrations, job store, tests)")

    # --- Privileged-socket client -------------------------------------------
    # commands::privilege was the client for Kyth's root daemon at
    # /run/kyth/privileged.sock, which Rowyn does not ship; the B2 pass
    # already moved system Flatpak work to direct /usr/bin/flatpak runs.
    # The socket client, its request validation for daemon-only
    # operations, the three registered commands, and their tests leave.
    # validate_flatpak_id stays: main.rs's system-Flatpak argv builders
    # call it (5 sites), so the file reduces to that one gate and its
    # test. No kept page invokes privileged_action (its frontend callers
    # are the cut This PC / Play / Move In sections only).
    privilege = crate / "src/commands/privilege.rs"
    text = privilege.read_text()
    text = cut_between(
        text, "use std::collections::HashMap;", "pub(crate) fn validate_flatpak_id(")
    text = cut_between(
        text, "/// Hub-side precheck delegates to the single shared gate",
        "#[cfg(test)]")
    tail_start = text.index("    use serde_json::json;")
    tail = text[tail_start:]
    for marker in ("validated_request", "cancel_marks_the_job",
                   "bounded_connect", "typed_payload_rejects"):
        if marker not in tail:
            raise ValueError(f"privilege.rs test tail no longer contains {marker}")
    text = text[:tail_start].rstrip() + "\n}\n"
    text = replace_once(
        text,
        "pub(crate) fn validate_flatpak_id(value: &str) -> Result<(), String> {",
        "//! The one piece of the upstream privilege module Rowyn keeps: the\n"
        "//! Flatpak id gate that main.rs's system-Flatpak argv builders call.\n"
        "//! The privileged-socket client for the upstream root daemon (which Rowyn\n"
        "//! does not ship) was removed by the legacy junk cut.\n"
        "\n"
        "pub(crate) fn validate_flatpak_id(value: &str) -> Result<(), String> {")
    privilege.write_text(text)
    main_rs = crate / "src/main.rs"
    text = main_rs.read_text()
    text = replace_once(
        text,
        "            commands::privilege::privileged_action,\n"
        "            commands::privilege::privileged_action_status,\n"
        "            commands::privilege::privileged_action_cancel,\n",
        "")
    main_rs.write_text(text)
    cut_notes.append(
        "privilege.rs socket client (/run/kyth/privileged.sock) and its commands removed; "
        "validate_flatpak_id kept")

    # --- Shared crate: helper binaries, dead modules, pgp -------------------
    cut_notes.append("shared crate bins: " + strip_shared_crate_bins(root))
    cut_notes.append("shared crate prune: " + prune_shared_crate(root))
    cut_notes.append("Cargo.lock re-synced after pgp removal: "
                     + sync_cargo_lock(crate, ("pgp",)))
    changes.append("legacy junk cut: " + "; ".join(cut_notes))

    apply_rowyn_mvp(root)
    changes.append("Rowyn-owned Diagnostics and Play MVP overlays applied; legacy gaming backend stays removed")

    # --- Full internal rename -------------------------------------------
    changes.append("upstream refs repointed: " + repoint_upstream_refs(root))
    changes.append("internal rename: " + rename_tree(root))
    changes.append("Cargo.lock re-synced after the kyth-shared -> rowyn-shared "
                   "package rename: " + sync_cargo_lock(crate, ("kyth-shared",)))

    apply_rowyn_daily_driver(root)
    changes.append(
        "daily-driver: catalog gains Firefox/Betterbird/Bitwarden, a Work pack "
        "(VS Code/Zoom/Slack/Teams for Linux) and ProtonPlus; familiar-apps map "
        "gains Outlook/Teams/VS Code; Hub installs unified on the system "
        "Flatpak installation (Discover's scope) via system_flatpak_install")

    apply_rowyn_move_in(root)
    changes.append(
        "MoveIn destination: Rowyn-native Windows migration surface "
        "(detect/profiles/import, read-only on the Windows side)")

    apply_rowyn_pass2(root)
    changes.append(
        "pass 2: catalog gains Protontricks/GOverlay/MangoJuice/RetroArch/WiVRn "
        "(Gaming), EasyEffects (Creator), OnlyOffice (Work) and a Tuning & "
        "Peripherals pack (LACT/OpenRGB/Piper/Solaar); familiar Office names "
        "OnlyOffice for Microsoft-format fidelity")

    apply_rowyn_streaming(root)
    changes.append(
        "pass 2 streaming: rowyn_sunshine commands registered (status, "
        "firewall, start-at-login); Moonlight joins the Gaming pack")

    apply_rowyn_deb_path(root)
    changes.append(
        "D3 guarded .deb path: rowyn_deb commands registered (state, "
        "inspect, start, job, cancel); the Apps page gains the "
        "RowynDebCard for container installs of supported app .debs")

    (root / "ROWYN-PORT-NOTICE").write_text(
        f"Pinned upstream System Hub source {PIN}, Apache-2.0 (LICENSE).\n"
        "Modified by Rowyn: Rowyn Hub identity (com.rowyn.hub), destinations cut to\n"
        "Dashboard/Apps/Updates/VPN plus Rowyn-owned Diagnostics/Play MVP overlays;\n"
        "updates channel repointed to mrtrickdotorg/Rowyn releases\n"
        "and ghcr.io/mrtrickdotorg/rowyn images, update helpers repointed to the Rowyn-shipped\n"
        "rowyn-safe-upgrade / rowyn-bootc-guard / rowyn-finalize-staged, feedback issues\n"
        "filed against mrtrickdotorg/Rowyn,\n"
        "VPN saved-profile path renamed to ~/.config/rowyn-vpn-connect and the network\n"
        "preset path to /etc/rowyn/network.toml.\n"
        "A branding sweep rewrites all remaining upstream brand display copy to Rowyn\n"
        "(update status/error text, service prompts, dialog copy, exe-handler suggestion\n"
        "data, the focus-session inhibit reason and its shared-crate matcher) and rekeys\n"
        "the boot-health OS identity check to ID=rowyn.\n"
        "A content cleanup removed the upstream-catalog Kali toolbox card from the Apps\n"
        "page (with its kali_* backend commands), the Guardian command family and\n"
        "its guardian-binary invocations (Rowyn ships no Guardian), and the\n"
        "open-* commands that spawned the upstream welcome launcher.\n"
        "A legacy junk cut removed the gaming/dev-tools command modules, the just\n"
        "recipe runner (run_hub_action/HubAction), the privileged-socket client\n"
        "for the upstream root daemon, the shared crate's 92 helper-binary targets, and\n"
        "every shared-crate library module outside the shells' reachability\n"
        "closure (incl. the runtime-updater/PGP heritage and its pgp dependency).\n"
        "A daily-driver pass extends the shared crate's curated catalog (Firefox,\n"
        "Betterbird, Bitwarden, a Work pack with VS Code/Zoom/Slack/Teams for Linux,\n"
        "ProtonPlus) and unifies Hub Flatpak installs on the system installation —\n"
        "the scope Discover uses — instead of the per-user installation.\n"
        "A full internal rename rewrites every remaining upstream identifier\n"
        "(the shared crate package and its paths, on-disk data paths, the stage\n"
        "progress wire token, env vars, the SAML script identifiers) to the Rowyn\n"
        "name, and the exported layout directories take the Rowyn name at\n"
        "extraction.\n"
        "Upstream OS/image/ISO assembly scripts are not included or invoked.\n")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    export_source(args.repo, args.destination)
    changes = port_source(args.destination)
    for change in changes:
        print(f"  - {change}")
    print(f"Ported pinned hub {PIN} to {args.destination}")


if __name__ == "__main__":
    main()
