#!/usr/bin/env python3
"""Contracts for the Rowyn Hub port (build/ubuntu-hub-source.py).

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_UPSTREAM_REPO to a local upstream checkout
that contains the pinned commit; the port integration tests skip otherwise.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")


class HubPortContractTests(unittest.TestCase):
    def test_pin_and_export_allowlist(self):
        self.assertEqual(hub.PIN, PIN)
        self.assertEqual(
            set(hub.PATHS),
            {"src/kyth-hub-web", "src/kyth-shared-rs", "third_party/gtk-glib-0.18.5",
             "build_files/exe-handler-apps.json", "LICENSE"},
        )

    def test_kept_destinations_include_vpn(self):
        self.assertEqual(set(hub.KEPT_DESTINATIONS), {"Apps", "Updates", "VPN"})
        self.assertEqual(
            hub.EXPECTED_DESTINATIONS,
            {"Play", "Apps", "This PC", "Move In", "VPN", "Updates"})

    def test_replace_helpers_fail_closed(self):
        with self.assertRaises(ValueError):
            hub.replace_once("anchor anchor", "anchor", "replacement")
        with self.assertRaises(ValueError):
            hub.replace_once("", "anchor", "replacement")
        with self.assertRaises(ValueError):
            hub.replace_all_counted("nothing here", "anchor", "replacement")
        text, count = hub.replace_all_counted("a a", "a", "b")
        self.assertEqual((text, count), ("b b", 2))

    def test_cut_between_fails_closed(self):
        self.assertEqual(hub.cut_between("a[x]b", "[", "b"), "ab")
        for text, start, end in (("a a b", "a", "b"), ("a b", "z", "b"),
                                 ("a b", "a", "z"), ("b a", "a", "b")):
            with self.assertRaises(ValueError):
                hub.cut_between(text, start, end)

    def test_containerfile_builds_and_installs_hub(self):
        containerfile = (ROOT / "Containerfile").read_text()
        self.assertIn("FROM base AS hub-builder", containerfile)
        stage = containerfile.split("FROM base AS hub-builder", 1)[1].split("FROM base AS system", 1)[0]
        for package in ("libwebkit2gtk-4.1-dev", "libgtk-3-dev", "libsoup-3.0-dev",
                        "libayatana-appindicator3-dev", "librsvg2-dev", "libdbus-1-dev",
                        "cargo", "rustc", "nodejs", "npm"):
            self.assertIn(package, stage)
        self.assertIn("COPY hub-source/src/rowyn-hub-web /build/rowyn-hub-web", stage)
        self.assertIn("COPY hub-source/src/rowyn-shared-rs /build/rowyn-shared-rs", stage)
        self.assertIn("COPY hub-source/build_files/exe-handler-apps.json /build_files/exe-handler-apps.json", stage)
        self.assertIn("COPY hub-source/third_party/gtk-glib-0.18.5 /third_party/gtk-glib-0.18.5", stage)
        self.assertIn("cargo build --release --locked", stage)
        self.assertIn("COPY --from=hub-builder /rowyn-hub-shell /usr/bin/rowyn-hub-shell", containerfile)
        self.assertIn(
            "COPY shared/com.rowyn.hub.desktop /usr/share/applications/com.rowyn.hub.desktop",
            containerfile)
        self.assertIn(
            "COPY docs/assets/rowyn-logo.png /usr/share/icons/hicolor/512x512/apps/com.rowyn.hub.png",
            containerfile)
        # The Tauri shell's runtime libraries ship in the payload layer.
        self.assertIn("libwebkit2gtk-4.1-0", containerfile)
        self.assertTrue((ROOT / "docs/assets/rowyn-logo.png").is_file())

    def test_containerfile_ships_vpn_stack(self):
        containerfile = (ROOT / "Containerfile").read_text()
        system = containerfile.split("FROM base AS system", 1)[1]
        # The Hub VPN backend is an openconnect client: it spawns
        # /usr/bin/openconnect with /etc/vpnc/vpnc-script, gates sudo on
        # /usr/bin/ksshaskpass, and its lockdown feature shells out to
        # firewall-cmd — all four ship in the payload layer, and the
        # firewalld service is enabled with the other desktop services.
        for package in ("openconnect", "vpnc-scripts", "ksshaskpass", "firewalld"):
            self.assertIn(package, system)
        self.assertIn("systemctl enable sddm.service NetworkManager.service firewalld.service",
                      system)
        # The backend pins /usr/bin/openconnect and /etc/vpnc/vpnc-script;
        # Ubuntu lays both out differently, so the image bridges them with
        # symlinks (the build.yml smoke checks assert the pinned paths).
        self.assertIn("ln -s /usr/sbin/openconnect /usr/bin/openconnect", system)
        self.assertIn("ln -s /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script", system)

    def test_containerfile_ships_update_helpers(self):
        containerfile = (ROOT / "Containerfile").read_text()
        system = containerfile.split("FROM base AS system", 1)[1]
        # The Hub's Updates actions invoke three fixed helpers plus a
        # passwordless sudoers allowlist for their fixed argv (the check
        # path runs under `sudo -n`, so without the allowlist it could
        # never run). All four land in the payload, and the sudoers file
        # is syntax-checked at image build time.
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-safe-upgrade.sh /usr/bin/rowyn-safe-upgrade",
            system)
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-bootc-guard.sh /usr/bin/rowyn-bootc-guard",
            system)
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-finalize-staged.sh /usr/libexec/rowyn-finalize-staged",
            system)
        self.assertIn(
            "install -Dm440 /ctx/shared/rowyn-upgrade.sudoers /etc/sudoers.d/rowyn-upgrade",
            system)
        self.assertIn("visudo -cf /etc/sudoers.d/rowyn-upgrade", system)
        for script in ("rowyn-safe-upgrade.sh", "rowyn-bootc-guard.sh",
                       "rowyn-finalize-staged.sh"):
            path = ROOT / "shared" / script
            self.assertTrue(path.is_file(), script)
            self.assertTrue(os.access(path, os.X_OK), script)
        sudoers = (ROOT / "shared/rowyn-upgrade.sudoers").read_text()
        for entry in (
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-safe-upgrade",
            "%sudo ALL=(root) NOPASSWD: /usr/libexec/rowyn-finalize-staged",
            "%sudo ALL=(root) NOPASSWD: /usr/libexec/rowyn-finalize-staged reboot",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard status",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard status-json",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard check",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard switch-latest",
            "%sudo ALL=(root) NOPASSWD: /usr/bin/rowyn-bootc-guard switch-testing",
        ):
            self.assertIn(entry, sudoers)
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        for smoke in ("test -x /usr/bin/rowyn-safe-upgrade",
                      "test -x /usr/bin/rowyn-bootc-guard",
                      "test -x /usr/libexec/rowyn-finalize-staged",
                      "test -s /etc/sudoers.d/rowyn-upgrade"):
            self.assertIn(smoke, workflow)

    def test_port_script_carries_branding_sweep(self):
        script = (ROOT / "build/ubuntu-hub-source.py").read_text()
        # The sweep is what keeps upstream brand copy out of the shipped
        # Hub (an installed-system screenshot caught "KythOS is up to
        # date" on the Updates page). Its anchors must stay in the port:
        # the status-copy module, the service layer, the exe-handler
        # suggestion data, and the focus-session inhibit pair.
        for anchor in ("components/updateMessages.ts",
                       "services/liveData.ts",
                       "build_files/exe-handler-apps.json",
                       "KythOS Focus Session", "Rowyn Focus Session",
                       "Rowyn update quarantined after",
                       "Rowyn ships Plasma Wayland only"):
            self.assertIn(anchor, script)
        # The boot-health identity check is rekeyed to Rowyn's os-release
        # ID; keyed to upstream's, it flags every Rowyn system unhealthy.
        self.assertIn("ID=rowyn", script)
        self.assertIn("Rowyn identity: /usr/lib/os-release is not ID=rowyn", script)

    def test_port_script_carries_legacy_junk_cut(self):
        script = (ROOT / "build/ubuntu-hub-source.py").read_text()
        # The cut's machinery and anchors must stay in the port: the
        # shared-crate bin strip, the closure prune with its pgp removal,
        # and the cargo lock re-sync (a Python re-implementation of the
        # lock rewrite kept optional deps the pgp subtree had activated,
        # so cargo itself performs it).
        for anchor in ("strip_shared_crate_bins", "prune_shared_crate",
                       "sync_cargo_lock", '"cargo", "fetch"',
                       "OpenPGP verification for the KythOS-signed updater manifest",
                       "src/data/com.kythos.hub.desktop", "src/data/kyth-welcome.desktop",
                       "commands::gaming::gaming_tools,",
                       "commands::dev_tools::dev_tools_job_cancel,",
                       "pub(crate) struct JustRecipeResponse {",
                       "fn hub_action_deserializes_allowlisted_recipe() {",
                       "Process-wide single-flight for MUTATING Hub actions",
                       "use std::collections::HashMap;",
                       "pub(crate) fn validate_flatpak_id("):
            self.assertIn(anchor, script)
        self.assertEqual(hub.SHARED_CRATE_BIN_FILES, 92)
        self.assertEqual(hub.SHARED_CRATE_BIN_SECTIONS, 91)
        keep = set(hub.SHARED_CRATE_KEEP_MODULES)
        self.assertEqual(len(keep), 58)
        # Load-bearing core stays; the audit's dead set — including the
        # whole runtime-updater/PGP heritage and the modules the shell
        # cuts orphaned (system::just, the shared privileged protocol) —
        # stays out.
        for kept in ("system::bootc_query", "system::boot_health", "system::vpn_saml",
                     "system::security_container", "system::mok_verify",
                     "system::software_catalog", "system::installer_validation",
                     "transfer", "url_encode"):
            self.assertIn(kept, keep)
        for dead in ("guardian", "privileged", "config_loader", "system::just",
                     "system::installer_source", "system::update_manifest",
                     "system::updater", "system::update_coordinator",
                     "system::release_fetch", "system::signing",
                     "system::tailscale_preset", "system::session_snapshot",
                     "system::gaming_tools", "system::ai_dev"):
            self.assertNotIn(dead, keep)

    def test_port_script_carries_slice_15_cuts(self):
        script = (ROOT / "build/ubuntu-hub-source.py").read_text()
        # Slice 1.5's anchors: the Kali card and its backend, the
        # Guardian command family, and the kyth-welcome-launch open-*
        # commands. If any anchor text leaves the port script, the
        # removals silently stop happening.
        for anchor in ("struct GuardianPendingResponse {",
                       "fn open_cloud_storage_app()", "fn open_move_files_app()",
                       "\\nfunction KaliCard({",
                       "commands::security::kali_enter_terminal,",
                       "export async function createKaliBox(",
                       "/// Single-flight for Kali box lifecycle operations"):
            self.assertIn(anchor, script)

    def test_desktop_entry_identity(self):
        entry = (ROOT / "shared/com.rowyn.hub.desktop").read_text()
        for line in ("Name=Rowyn Hub", "Exec=/usr/bin/rowyn-hub-shell",
                     "Icon=com.rowyn.hub", "StartupWMClass=com.rowyn.hub",
                     "Categories=System;"):
            self.assertIn(line, entry)

    def test_build_workflow_fetches_and_ports_pinned_hub(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        # The fetch step is part of build.yml itself (a push-trigger path),
        # and the heavy lifting is the repository's own fetch-assets pattern:
        # clone, pin-check, then the anchored port script.
        self.assertIn("./build/fetch-assets.sh", workflow)
        self.assertIn(f"UPSTREAM_HUB_COMMIT: {PIN}", workflow)
        self.assertIn('git clone --filter=blob:none --no-checkout --depth 1', workflow)
        self.assertIn('rev-parse HEAD', workflow)
        self.assertIn("python3 build/ubuntu-hub-source.py", workflow)
        self.assertIn("test -x /usr/bin/rowyn-hub-shell", workflow)
        for smoke in ("test -x /usr/bin/openconnect", "test -s /etc/vpnc/vpnc-script",
                      "test -x /usr/bin/ksshaskpass", "test -x /usr/bin/firewall-cmd"):
            self.assertIn(smoke, workflow)
        import yaml
        parsed = yaml.safe_load(workflow)
        paths = parsed[True]["push"]["paths"]
        self.assertIn("build/ubuntu-hub-source.py", paths)
        self.assertIn("Containerfile", paths)
        self.assertIn("shared/**", paths)


def _pinned_repo():
    repo = os.environ.get("ROWYN_TEST_UPSTREAM_REPO")
    if not repo:
        return None
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", PIN + "^{commit}"],
        text=True, capture_output=True)
    if result.returncode != 0 or result.stdout.strip() != PIN:
        return None
    return Path(repo)


@unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit")
class PinnedHubSourceTests(unittest.TestCase):
    def test_port_is_repeatable_and_scoped(self):
        repo = _pinned_repo()
        original = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-") as temp:
            snapshots = []
            for name in ("first", "second"):
                root = Path(temp) / name
                hub.export_source(repo, root)
                hub.port_source(root)
                snapshots.append({str(p.relative_to(root)): p.read_bytes()
                                  for p in root.rglob("*") if p.is_file()})
                web = root / "src/rowyn-hub-web"
                crate = web / "src-tauri"

                conf = json.loads((crate / "tauri.conf.json").read_text())
                self.assertEqual(conf["productName"], "Rowyn Hub")
                self.assertEqual(conf["identifier"], "com.rowyn.hub")
                self.assertEqual(conf["app"]["windows"][0]["title"], "Rowyn Hub")

                cargo = (crate / "Cargo.toml").read_text()
                self.assertIn('name = "rowyn-hub-shell"', cargo)
                self.assertIn('name = "rowyn-hub-shell"', (crate / "Cargo.lock").read_text())
                self.assertNotIn("kyth-hub-shell", cargo)
                self.assertNotIn("kyth-hub-shell", (crate / "Cargo.lock").read_text())

                manifest = json.loads((web / "src/data/hubRoutes.json").read_text())
                self.assertEqual([d["key"] for d in manifest["destinations"]],
                                 ["Apps", "VPN", "Updates", "Development", "Diagnostics", "Play", "Work", "Backups", "MoveIn"])

                app = (web / "src/App.tsx").read_text()
                for route in ("/dev-tools", "/this-pc", "/move-in"):
                    self.assertNotIn(f'path="{route}"', app)
                    self.assertNotIn(f'"{route}":', app)
                for page in ("DevTools", "ThisPc", "MoveIn"):
                    self.assertNotIn(f"const {page} = lazy(", app)
                for route in ("/", "/apps", "/updates", "/vpn", "/diagnostics", "/play", "/work"):
                    self.assertIn(f'path="{route}"', app)
                # VPN restored: lazy import, route, and crumb all present.
                self.assertIn("const Vpn = lazy(", app)
                self.assertIn('"/vpn": "VPN"', app)
                # Cut page files are kept in the tree, only unreferenced.
                for page in ("Play", "DevTools", "ThisPc", "MoveIn", "Vpn"):
                    self.assertTrue((web / f"src/pages/{page}.tsx").is_file())

                sidebar = (web / "src/components/Sidebar.tsx").read_text()
                self.assertIn("Rowyn Hub", sidebar)
                self.assertIn("System tools", sidebar)
                self.assertNotIn("Kyth Hub", sidebar)
                self.assertNotIn("/dev-tools", sidebar)
                self.assertNotIn('label: "Dev Tools"', sidebar)
                self.assertEqual(sidebar.count('Development: { hint:'), 1)
                self.assertIn('Development: { hint: "Dedicated Ubuntu development environment", Icon: IconTerminal }', sidebar)
                for key in ('"This PC"', '"Move In"'):
                    self.assertNotIn(f"{key}: {{ hint:", sidebar)
                self.assertIn('VPN: { hint: "Secure work connections", Icon: IconLock },', sidebar)
                self.assertIn("IconLock", sidebar)

                search = (web / "src/search.ts").read_text()
                self.assertNotIn("dest:Dev Tools", search)
                self.assertNotIn("/dev-tools", search)

                dashboard = (web / "src/pages/Dashboard.tsx").read_text()
                self.assertIn("CONTROL ROOM", dashboard)
                self.assertIn("Your workspaces", dashboard)
                self.assertIn("home-destination", dashboard)
                self.assertNotIn('navigate("/play"', dashboard)
                self.assertNotIn("/this-pc?section=", dashboard)
                self.assertNotIn("fetchFirstbootAppsStatus", dashboard)
                self.assertNotIn("KythOS", dashboard)

                updates = (crate / "src/commands/updates.rs").read_text()
                self.assertIn("https://api.github.com/repos/mrtrickdotorg/Rowyn/releases?per_page=100", updates)
                self.assertIn("ghcr.io/mrtrickdotorg/rowyn@", updates)
                self.assertNotIn("ghcr.io/kyth-os/kyth@", updates)
                self.assertNotIn("kyth-os/kyth/releases", updates)
                # The update actions shell out to helper binaries; Rowyn
                # ships its own under its own name, so every reference —
                # launch argv and require_binary prechecks alike — is
                # repointed and no Kyth helper name survives.
                self.assertIn("/usr/bin/rowyn-safe-upgrade", updates)
                self.assertIn("/usr/bin/rowyn-bootc-guard", updates)
                self.assertIn("/usr/libexec/rowyn-finalize-staged", updates)
                self.assertNotIn("kyth-safe-upgrade", updates)
                self.assertNotIn("kyth-bootc-guard", updates)
                self.assertNotIn("kyth-finalize-staged", updates)
                self.assertNotIn("KythOS update helper", updates)
                self.assertNotIn("KythOS bootc guard helper", updates)
                # Branch switching stays registered and reaches the
                # Rowyn guard, which implements the stable and testing
                # channels.
                self.assertIn("commands::updates::bootc_switch_branch",
                              (crate / "src/main.rs").read_text())
                # The check path lives in the shared crate: its guard
                # invocations and GHCR fallback take Rowyn's name too.
                bootc_query = (root / "src/rowyn-shared-rs/src/system/bootc_query.rs").read_text()
                self.assertIn("/usr/bin/rowyn-bootc-guard", bootc_query)
                self.assertNotIn("kyth-bootc-guard", bootc_query)
                self.assertNotIn("ghcr.io/kyth-os/kyth", bootc_query)
                self.assertNotIn(r"ghcr\.io/kyth-os/kyth", bootc_query)
                self.assertIn("ghcr.io/mrtrickdotorg/rowyn", bootc_query)
                main = (crate / "src/main.rs").read_text()
                self.assertIn("commands::updates::bootc_switch_branch", main)
                self.assertIn('"https://github.com/mrtrickdotorg/Rowyn",', main)

                # VPN: the Rust backend was never cut — its commands stay
                # registered — and its on-disk paths take the Rowyn name.
                self.assertIn("commands::vpn::vpn_connect", main)
                vpn = (crate / "src/commands/vpn.rs").read_text()
                for text in (vpn, main):
                    self.assertIn(".config/rowyn-vpn-connect", text)
                    self.assertNotIn("kyth-vpn-connect", text)
                preset = (root / "src/rowyn-shared-rs/src/system/network_preset.rs").read_text()
                self.assertIn('"/etc/rowyn/network.toml"', preset)
                self.assertNotIn("/etc/kyth/network.toml", preset)
                self.assertNotIn("/etc/kyth/network.toml", vpn)
                # The GlobalProtect argv construction survives the port:
                # gp gateways are entered directly via --usergroup gateway.
                saml = (root / "src/rowyn-shared-rs/src/system/vpn_saml.rs").read_text()
                self.assertIn('"--usergroup"', saml)
                self.assertIn('"gateway"', saml)
                self.assertIn('"/etc/vpnc/vpnc-script"', saml)
                # VPN surfaces carry Rowyn branding, not Kyth's.
                self.assertNotIn("Kyth Hub", (web / "src/pages/Vpn.tsx").read_text())
                self.assertNotIn("Kyth Hub", (web / "src/components/VpnSection.tsx").read_text())

                overview = (web / "src/components/UpdatesOverview.tsx").read_text()
                self.assertNotIn("KythOS", overview)
                self.assertTrue((root / "ROWYN-PORT-NOTICE").is_file())
                with self.assertRaises(ValueError):
                    hub.export_source(repo, root)
            self.assertEqual(*snapshots)
        self.assertEqual(original, subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]))

    def test_check_classifier_accepts_composefs_manifest_diff(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-") as temp:
            root = Path(temp) / "src"
            hub.export_source(repo, root)
            hub.port_source(root)
            query = (root / "src/rowyn-shared-rs/src/system/bootc_query.rs").read_text()
            body = query.split("pub fn update_check_state(", 1)[1].split("\nfn ", 1)[0]
            self.assertIn('lower.contains("update available for:")', body)
            self.assertIn('lower.contains("total new layers:") && lower.contains("added layers:")', body)
            self.assertIn('lower.contains("no changes in:")', body)
            self.assertIn("fn update_check_state_accepts_composefs_manifest_diff_output", query)
            self.assertIn("/usr/bin/rowyn-bootc-guard", query)

    def test_daily_driver_catalog_and_install_scope(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-daily-") as temp:
            root = Path(temp) / "src"
            hub.export_source(repo, root)
            hub.port_source(root)
            catalog = (root / "src/rowyn-shared-rs/src/system/software_catalog.rs").read_text()
            # Every id the daily-driver pass adds was verified against
            # Flathub on 2026-11-09; com.microsoft.Teams does not exist
            # there and must never be offered.
            for app_id in ("org.mozilla.firefox", "eu.betterbird.Betterbird",
                           "com.bitwarden.desktop", "com.visualstudio.code",
                           "us.zoom.Zoom", "com.slack.Slack",
                           "com.github.IsmaelMartinez.teams_for_linux",
                           "com.vysp3r.ProtonPlus"):
                self.assertIn(f'id: "{app_id}"', catalog)
            self.assertIn('name: "Work".to_string()', catalog)
            self.assertNotIn("com.microsoft.Teams", catalog)
            # The shared install projection is system-scoped now.
            self.assertIn('["flatpak", "install", "--system", "-y", "flathub", app_id]', catalog)
            self.assertIn("fn projects_only_valid_system_scoped_flatpak_installs()", catalog)
            main = (root / "src/rowyn-hub-web/src-tauri/src/main.rs").read_text()
            self.assertIn("fn system_flatpak_install(", main)
            self.assertIn("fn system_flatpak_install_args(", main)
            install_body = main.split("fn install_flatpak(", 1)[1].split("fn install_status(", 1)[0]
            self.assertIn("system_flatpak_install(&app_id, &cancel)", install_body)
            self.assertNotIn('"--user"', install_body)
            self.assertNotIn("ensure_flathub_user_remote", install_body)
            for command in (
                "commands::rowyn_steam::rowyn_steam_play_status",
                "commands::rowyn_steam::rowyn_steam_play_apply",
                "commands::rowyn_play::rowyn_display_modes_scan",
                "commands::rowyn_play::rowyn_vulkan_layers_scan",
                "commands::rowyn_play::rowyn_vulkan_layer_install",
                "commands::rowyn_migrate::rowyn_migrate_scan",
                "commands::rowyn_migrate::rowyn_migrate_mount",
                "commands::rowyn_migrate::rowyn_migrate_import",
            ):
                self.assertIn(command, main)

    def test_audit_fixes_on_kept_pages(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-audit-") as temp:
            root = Path(temp) / "src"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            crate = web / "src-tauri"
            shared = root / "src/rowyn-shared-rs/src/system"
            comps = web / "src/components"

            # B3: no just recipe button on any kept page.
            for rel in ("AppStoreSection.tsx", "WorkSetupSection.tsx", "VpnSection.tsx",
                        "UpdatesOverview.tsx", "Topbar.tsx", "Sidebar.tsx"):
                self.assertNotIn("RecipeButton", (comps / rel).read_text(), rel)
            self.assertNotIn("RecipeButton", (web / "src/pages/Dashboard.tsx").read_text())
            store = (comps / "AppStoreSection.tsx").read_text()
            for recipe in ("setup-kyth-dev-box", "ai-dev-status", "ai-dev-setup", "setup-waydroid",
                           "remove-waydroid-confirmed", "install-vscode", "install-boxbuddy",
                           "install-jetbrains-toolbox"):
                self.assertNotIn(recipe, store)
            self.assertIn("<HostToolsGrid", store)
            # Slice 1.5 removed the Kali card; see the dedicated test.
            self.assertNotIn("KaliCard", store)
            self.assertIn("<ActionStatus status={status} />", store)
            work = (comps / "WorkSetupSection.tsx").read_text()
            self.assertNotIn("install-ms-fonts", work)
            self.assertNotIn("setup-printer", work)
            self.assertIn("Find network printers", work)
            self.assertNotIn("setup-tailscale", (comps / "VpnSection.tsx").read_text())
            self.assertIn("Open full VPN connection", (comps / "VpnSection.tsx").read_text())

            # D2: no Guardian surface on kept pages.
            dashboard = (web / "src/pages/Dashboard.tsx").read_text()
            self.assertNotIn("Guardian", dashboard)
            self.assertNotIn("guardian", dashboard)
            self.assertIn('label="Boot health"', dashboard)
            topbar = (comps / "Topbar.tsx").read_text()
            self.assertNotIn("Guardian", topbar)
            self.assertNotIn("IconBell", topbar)

            # D3: no release notes / update history; channel display.
            overview = (comps / "UpdatesOverview.tsx").read_text()
            for gone in ("release notes", "Update history", "updates-history-card",
                         "updates-release-card", "fetchUpdateReleaseHistory",
                         "fetchUpdateReleaseSummary", "ExternalLink"):
                self.assertNotIn(gone, overview)
            self.assertIn("Download and stage", overview)
            live_data = (web / "src/services/liveData.ts").read_text()
            self.assertIn('  stable: "Stable",\n', live_data)

            # B2: system Flatpak work runs directly, never via the Kyth socket.
            main = (crate / "src/main.rs").read_text()
            self.assertNotIn("privilege::flatpak_update()", main)
            self.assertNotIn("privileged_flatpak_uninstall", main)
            self.assertIn("let system_result = system_flatpak_update(&cancel);", main)
            self.assertIn("system_flatpak_uninstall(&app_id, &cancel)", main)
            self.assertIn('["update", "--system", "--noninteractive", "-y"]', main)
            self.assertIn('["uninstall", "--system", "--noninteractive", "-y", "--", app_id]', main)
            self.assertIn('Command::new("/usr/bin/flatpak")', main)
            self.assertIn("fn system_flatpak_failure_", main.replace("fn system_flatpak_failures_", "fn system_flatpak_failure_"))
            self.assertIn("mod audit_fix_tests", main)

            # D5: https handler is checked before either xdg-open launch.
            for name in ("open_m365_app", "open_external_url"):
                body = main.split(f"fn {name}(", 1)[1].split("\n}\n", 1)[0]
                self.assertIn("ensure_https_handler()?;", body)
                self.assertLess(body.index("ensure_https_handler()?;"), body.index("xdg-open"))
            self.assertIn("x-scheme-handler/https", main)
            self.assertIn("Install one from Discover", main)

            # D1: Recovery tile reads bootc directly when the probe is absent.
            history = (shared / "deployment_history.rs").read_text()
            self.assertIn(
                'read_section("bootc-status-data")\n        .or_else(crate::system::bootc_query::fetch_status_data)',
                history)

            # D6: Liberation Sans is ready; no ujust copy.
            fonts = (shared / "fonts_ready.rs").read_text()
            self.assertIn(":family=Liberation Sans", fonts)
            self.assertIn("fn fonts_status(", fonts)
            self.assertIn("fn liberation_or_arial_counts_as_office_fonts_ready", fonts)
            production = fonts.split("#[cfg(test)]", 1)[0]
            self.assertNotIn("ujust", production)
            self.assertNotIn("MS fonts", production)

    def test_slice_15_content_cleanup(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-slice15-") as temp:
            root = Path(temp) / "src"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            crate = web / "src-tauri"
            comps = web / "src/components"

            # Apps page: the Kali catalog card is gone — component,
            # render site, and service functions — while the generic
            # Flatpak host-tools grid in the same section stays.
            store = (comps / "AppStoreSection.tsx").read_text()
            self.assertNotIn("Kali", store)
            self.assertNotIn("kali", store)
            self.assertIn("<HostToolsGrid", store)
            self.assertIn("Specialized tools", store)
            self.assertIn("fetchSecHostTools", store)
            live_data = (web / "src/services/liveData.ts").read_text()
            self.assertNotIn("Kali", live_data)
            self.assertNotIn("kali", live_data)
            self.assertIn("fetchSecHostTools", live_data)
            self.assertIn("installSecHostTool", live_data)

            # The shell cannot invoke helpers Rowyn does not ship: the
            # Guardian command family and the kyth-welcome-launch open-*
            # commands are deregistered and deleted, with no reference
            # to either binary left in the crate.
            main = (crate / "src/main.rs").read_text()
            self.assertNotIn("kyth-guardian", main)
            self.assertNotIn("kyth-welcome-launch", main)
            self.assertNotIn("guardian", main.lower())
            for gone in ("guardian_snapshot", "guardian_check", "guardian_control",
                         "guardian_execute_recipe", "guardian_dismiss",
                         "open_cloud_storage_app", "open_move_files_app",
                         "open_network_shares_app", "commands::security::kali_"):
                self.assertNotIn(gone, main, gone)
            for kept in ("commands::vpn::vpn_connect", "commands::vpn::open_vpn_app",
                         "commands::security::sec_host_tools",
                         "commands::security::security_job_status",
                         "open_backup_app", "ipp_discover"):
                self.assertIn(kept, main, kept)
            security = (crate / "src/commands/security.rs").read_text()
            self.assertNotIn("kali", security.lower())
            self.assertIn("sec_host_tool_install", security)
            self.assertIn("security_job_status", security)

            # Kept frontend files name neither removed helper.
            hub_page = (web / "src/pages/HubPage.tsx").read_text()
            self.assertNotIn("kyth-welcome-launch", hub_page)
            self.assertNotIn("kyth-guardian", hub_page)

            # VPN page: no Tailscale recipe affordance survives (the B3
            # button cut is re-asserted here at the page level); the
            # GlobalProtect flow is untouched.
            vpn_section = (comps / "VpnSection.tsx").read_text()
            self.assertNotIn("ailscale", vpn_section)
            self.assertNotIn("ailscale", (web / "src/pages/Vpn.tsx").read_text())
            self.assertIn("Open full VPN connection", vpn_section)

    def test_legacy_junk_cut(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-junk-") as temp:
            root = Path(temp) / "export"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            crate = web / "src-tauri"
            shared = root / "src/rowyn-shared-rs"

            # Item 2: orphaned Kyth desktop data files are gone; the
            # provenance data kept for a future Play surface is not.
            self.assertFalse((web / "src/data/com.kythos.hub.desktop").exists())
            self.assertFalse((web / "src/data/kyth-welcome.desktop").exists())
            self.assertTrue((web / "src/data/compat_games.json").is_file())

            # Item 4: gaming/dev-tools command modules and registrations.
            self.assertFalse((crate / "src/commands/gaming.rs").exists())
            self.assertFalse((crate / "src/commands/dev_tools.rs").exists())
            commands_mod = (crate / "src/commands/mod.rs").read_text()
            self.assertNotIn("mod gaming", commands_mod)
            self.assertNotIn("mod dev_tools", commands_mod)
            main = (crate / "src/main.rs").read_text()
            self.assertNotIn("commands::gaming::", main)
            self.assertNotIn("commands::dev_tools::", main)

            # Item 5: no just recipe runner remains in the shell.
            updates = (crate / "src/commands/updates.rs").read_text()
            for gone in ("HubAction", "run_hub_action", "just_list",
                         "hub_action_jobs", "system::just"):
                self.assertNotIn(gone, updates, gone)
            for gone in ("commands::updates::just_list", "commands::updates::run_hub_action",
                         "commands::updates::hub_action_status",
                         "commands::updates::hub_action_cancel"):
                self.assertNotIn(gone, main, gone)
            job = (crate / "src/commands/job.rs").read_text()
            self.assertNotIn("take_hub_action_slot", job)
            self.assertNotIn("HUB_ACTION_MUTATING", job)
            self.assertIn("take_flatpak_slot", job)

            # Item 6: the privileged-socket client is gone; the Flatpak
            # id gate main.rs depends on survives.
            privilege = (crate / "src/commands/privilege.rs").read_text()
            for gone in ("privileged.sock", "connect_privileged_socket",
                         "connect_unix_with_timeout", "privileged_action",
                         "fn flatpak_update", "fn flatpak_uninstall",
                         "validated_request"):
                self.assertNotIn(gone, privilege, gone)
            self.assertIn("pub(crate) fn validate_flatpak_id", privilege)
            self.assertNotIn("commands::privilege::privileged_action", main)
            self.assertIn("commands::privilege::validate_flatpak_id", main)

            # Kept functionality is still registered (guard against
            # over-cutting): updates, VPN, security grid, exe handler.
            for kept in ("commands::updates::bootc_upgrade",
                         "commands::vpn::vpn_connect",
                         "commands::security::sec_host_tools",
                         "commands::security::security_job_status"):
                self.assertIn(kept, main, kept)

            # Items 1 + 7: the shared crate is pruned to the keep set —
            # no bin sources, no [[bin]] sections, no pgp, and exactly
            # the reviewed module files.
            self.assertEqual(list((shared / "src").glob("*_bin.rs")), [])
            manifest = (shared / "Cargo.toml").read_text()
            self.assertNotIn("[[bin]]", manifest)
            self.assertNotIn("pgp", manifest)
            remaining = sorted(
                str(p.relative_to(shared / "src"))
                for p in (shared / "src").rglob("*.rs"))
            keep = set(hub.SHARED_CRATE_KEEP_MODULES)
            expected = sorted(
                ["lib.rs", "system/mod.rs"]
                + [f"{name}.rs" for name in keep if "::" not in name and name != "system"]
                + [f"system/{name.rsplit('::', 1)[-1]}.rs"
                   for name in keep if name.startswith("system::")])
            self.assertEqual(remaining, expected)
            for gone in ("system/update_manifest.rs", "system/updater.rs",
                         "system/update_coordinator.rs", "system/release_fetch.rs",
                         "system/signing.rs", "system/just.rs",
                         "system/installer_source.rs", "guardian.rs",
                         "privileged.rs", "config_loader.rs"):
                self.assertFalse((shared / "src" / gone).exists(), gone)
            for kept in ("system/bootc_query.rs", "system/vpn_saml.rs",
                         "system/security_container.rs", "transfer.rs"):
                self.assertTrue((shared / "src" / kept).is_file(), kept)
            lock = (crate / "Cargo.lock").read_text()
            lock_names = {block.split('"')[1] for block in lock.split("[[package]]")[1:]
                          if 'name = "' in block}
            self.assertNotIn("pgp", lock_names)

    def test_no_upstream_branding_survives_port(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-brand-") as temp:
            root = Path(temp) / "export"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            crate = web / "src-tauri"

            # Frontend: no "KythOS" anywhere under src/ except the one
            # allowlisted provenance file. compat_games.json is data for
            # the cut Play surfaces whose notes record that a verdict
            # came from KythOS validation; rewriting it would falsify
            # the record, so it is the deliberate exception — and the
            # set equality below fails if any other file joins it or if
            # the exception itself disappears upstream.
            allowlist = {"src/data/compat_games.json"}
            survivors = {
                str(p.relative_to(web))
                for p in (web / "src").rglob("*")
                if p.is_file() and p.suffix in (".ts", ".tsx", ".css", ".json")
                and "KythOS" in p.read_text(errors="ignore")
            }
            self.assertEqual(survivors, allowlist)
            self.assertIn("KythOS", (web / "src/data/compat_games.json").read_text())

            # The exact sentence from Patrick's screenshot, rebranded.
            messages = (web / "src/components/updateMessages.ts").read_text()
            self.assertIn("Rowyn is up to date. No restart is needed.", messages)
            self.assertNotIn("KythOS", messages)
            # Service-layer prompts and the not-in-shell error family.
            live_data = (web / "src/services/liveData.ts").read_text()
            self.assertNotIn("KythOS", live_data)
            self.assertNotIn("Kyth Hub", live_data)
            # Exe-handler suggestion data ships in the shell.
            self.assertNotIn(
                "KythOS", (root / "build_files/exe-handler-apps.json").read_text())

            # Rust shell: no upstream brand string survives anywhere in
            # the crate, the identity check keys on Rowyn's os-release
            # ID, and the focus-session inhibit reason took the new name.
            for path in (crate / "src").rglob("*.rs"):
                self.assertNotIn("KythOS", path.read_text(),
                                 f"KythOS survived in {path.name}")
            updates = (crate / "src/commands/updates.rs").read_text()
            self.assertIn("ID=rowyn", updates)
            self.assertNotIn("ID=kythos", updates)
            main = (crate / "src/main.rs").read_text()
            self.assertIn('"--why=Rowyn Focus Session"', main)

            # Shared-crate modules whose display strings the shell
            # surfaces (and the inhibit reaper that must match the
            # reason main.rs sets) are clean and consistent.
            shared = root / "src/rowyn-shared-rs/src/system"
            for name in ("process.rs", "boot_health.rs", "desktop_stack.rs"):
                self.assertNotIn("KythOS", (shared / name).read_text(), name)
            self.assertIn("Rowyn Focus Session", (shared / "process.rs").read_text())


    def test_rename_contract_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-rename-") as temp:
            root = Path(temp) / "source"
            hub.export_source(repo, root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            crate = web / "src-tauri"
            shared = root / "src/rowyn-shared-rs"

            shared_manifest = (shared / "Cargo.toml").read_text()
            self.assertIn('name = "rowyn-shared"', shared_manifest)
            self.assertIn('name = "rowyn-shared"', (crate / "Cargo.lock").read_text())
            self.assertIn("path = \"../../rowyn-shared-rs\"", (crate / "Cargo.toml").read_text())
            main = (crate / "src/main.rs").read_text()
            self.assertIn("rowyn_shared::", main)
            self.assertNotIn("kyth_shared", main)

            updates = (crate / "src/commands/updates.rs").read_text()
            self.assertIn("ROWYN_STAGE_PROGRESS", updates)
            self.assertNotIn("KYTH_STAGE_PROGRESS", updates)
            vpn = (crate / "src/commands/vpn.rs").read_text()
            self.assertIn("submitToRowyn", vpn)
            telemetry = (shared / "src/system/telemetry.rs").read_text()
            self.assertIn(".local/share/rowyn/telemetry.db", telemetry)

            # No unmasked kyth reference survives anywhere in the
            # export. Deliberate exceptions: the GitHub org in
            # registry/identity strings, the upstream cert-name
            # matcher, one negative regression needle, and the
            # KythOS-era provenance data in compat_games.json
            # (excluded from the scan entirely).
            protected = ("KythOS Secure Boot", "kyth-vpnc-script")
            offenders = []
            for path in root.rglob("*"):
                if not path.is_file() or path.name == "compat_games.json":
                    continue
                try:
                    masked = path.read_text()
                except (UnicodeDecodeError, ValueError):
                    continue
                for literal in protected:
                    masked = masked.replace(literal, "")
                masked = masked.replace("kyth-os", "")
                if "kyth" in masked.lower():
                    offenders.append(str(path.relative_to(root)))
            self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

