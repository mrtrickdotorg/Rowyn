"""Rowyn-owned Hub MVP: pin-export and safety contracts (no VM needed)."""
import json
import os
import re
from pathlib import Path
import tempfile
import unittest

from test_hub_port import ROOT, hub, _pinned_repo


class MvpRoutes(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_native_pages_are_reachable_without_restoring_legacy_surfaces(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-mvp-routes-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            self.assertTrue(callable(getattr(hub, "apply_rowyn_mvp", None)),
                            "port must apply checked-in Rowyn MVP overlays")
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            manifest = json.loads((web / "src/data/hubRoutes.json").read_text())
            destinations = {d["key"]: d for d in manifest["destinations"]}
            self.assertEqual(destinations["Diagnostics"]["route"], "/diagnostics")
            self.assertEqual([s["key"] for s in destinations["Play"]["sections"]],
                             ["Gaming", "SteamLibrary", "Compatibility", "Controllers"])
            app = (web / "src/App.tsx").read_text()
            for name in ("Diagnostics", "Play"):
                self.assertIn(f'./pages/Rowyn{name}', app)
                self.assertIn(f'path="/{name.lower()}"', app)
            sidebar = (web / "src/components/Sidebar.tsx").read_text()
            self.assertIn('Diagnostics: { hint:', sidebar)
            self.assertIn('Play: { hint: "Launchers, compatibility, controllers"', sidebar)
            self.assertIn('Diagnostics: DIAGNOSTICS_SECTIONS',
                          (web / "src/data/destinations.ts").read_text())
            self.assertNotIn('Performance', json.dumps(destinations["Play"]))
            for name in ("Diagnostics", "Play"):
                page = (web / f"src/pages/Rowyn{name}.tsx").read_text()
                self.assertIn('Rowyn', page)
                self.assertNotIn('RecipeButton', page)
                self.assertNotIn('setInterval', page)


class HubSectionLayout(unittest.TestCase):
    """A section must never inherit a page root's centring margins.

    `.rowyn-mvp`/`.rowyn-home` centre themselves with `margin: 0 auto`, and auto
    side margins cancel a flex child's stretch. The pages that host Rowyn
    sections are flex columns (`.apps-page`, `.updates-page`, `.play-page`), so
    a section picking up one of those classes collapses to the width of its
    longest sentence and floats in the middle of the workspace.
    """
    PAGE_ROOTS = {".rowyn-mvp", ".rowyn-home"}

    @staticmethod
    def _has_auto_side_margin(body):
        for side, value in re.findall(r"margin(-[a-z]+)?\s*:\s*([^;]+);", body):
            if side in ("-left", "-right", "-inline"):
                return "auto" in value.split()
            if not side:
                parts = value.split()
                if len(parts) >= 2 and "auto" in (parts[1], parts[-1]):
                    return True
        return False

    def test_auto_side_margins_belong_to_page_roots_only(self):
        raw = (ROOT / "build/hub/src/styles/rowynMvp.css").read_text()
        css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        centred = {
            selector.strip()
            for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
            if self._has_auto_side_margin(rule.group(2))
            for selector in rule.group(1).split(",")
        }
        self.assertTrue(centred, "the page roots still centre themselves")
        self.assertLessEqual(centred, self.PAGE_ROOTS)

    def test_page_root_classes_are_only_used_on_page_roots(self):
        for path in sorted((ROOT / "build/hub/src").rglob("*.tsx")):
            for match in re.finditer(
                    r'className="([^"]*\browyn-(?:mvp|home)(?![\w-])[^"]*)"',
                    path.read_text()):
                self.assertIn("page-content", match.group(1),
                              f"{path.name} uses a page-root class on an embedded section")

    def test_apps_defaults_section_owns_its_layout(self):
        apps = (ROOT / "build/hub/src/pages/AppsDefaultsAccess.tsx").read_text()
        self.assertIn("rowyn-apps-defaults", apps)
        self.assertNotIn("rowyn-mvp", apps)


class DevelopmentImagePin(unittest.TestCase):
    """The managed box must pin its image and raise apt before presets."""

    def test_rust_and_typescript_agree_on_the_pinned_image_and_apt_floor(self):
        model = (ROOT / "build/hub/src/services/rowynDevelopmentModel.ts").read_text()
        native = (ROOT / "build/hub/src-tauri/src/commands/rowyn_development.rs").read_text()
        pinned = re.search(r"docker\.io/library/ubuntu@sha256:[0-9a-f]{64}", model)
        self.assertIsNotNone(pinned, "development image must be digest-pinned")
        self.assertIn(pinned.group(0), native)
        for needle in ("3.3.3ubuntu0.1", "--only-upgrade", "apt_refresh_commands",
                       "apt_floor_command", "--compare-versions"):
            self.assertIn(needle, native)
        self.assertIn("3.3.3ubuntu0.1", model)
        self.assertNotIn("ubuntu:26.10'", model)


class MvpDevelopment(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_development_is_registered_as_a_rowyn_owned_overlay(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-development-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            manifest = json.loads((web / "src/data/hubRoutes.json").read_text())
            destinations = {d["key"]: d for d in manifest["destinations"]}
            self.assertEqual(destinations["Development"]["route"], "/development")
            self.assertEqual([s["key"] for s in destinations["Development"]["sections"]], ["Development"])
            self.assertIn('Development: DEVELOPMENT_SECTIONS',
                          (web / "src/data/destinations.ts").read_text())
            self.assertTrue((web / "src/pages/RowynDevelopment.tsx").is_file())
            page = (web / "src/pages/RowynDevelopment.tsx").read_text()
            self.assertIn('id="hub-section-development"', page)
            app = (web / "src/App.tsx").read_text()
            self.assertIn('path="/development"', app)
            self.assertIn('./pages/RowynDevelopment', app)
            self.assertEqual((web / "src/components/Sidebar.tsx").read_text().count('Development: { hint:'), 1)
            self.assertIn('Development: { hint: "Dedicated Ubuntu development environment", Icon: IconTerminal }',
                          (web / "src/components/Sidebar.tsx").read_text())
            handlers = (web / "src-tauri/src/main.rs").read_text()
            for command in ("list", "start", "job", "cancel", "open"):
                self.assertIn(f"commands::rowyn_development::rowyn_development_{command}", handlers)
            self.assertIn("pub(crate) mod rowyn_development;",
                          (web / "src-tauri/src/commands/mod.rs").read_text())
            self.assertTrue((web / "src/services/rowynDevelopment.ts").is_file())
            package = json.loads((web / "package.json").read_text())
            self.assertIn("rowyn-development.test.mjs",
                          package["scripts"]["test:rowyn-mvp"])
            self.assertIn("Development", (web / "src/pages/Dashboard.tsx").read_text())


class MvpHomeDashboard(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_control_room_overlay_replaces_pinned_dashboard_with_safe_navigation(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-home-dashboard-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            page = (web / "src/pages/Dashboard.tsx").read_text()
            for needle in (
                "CONTROL ROOM", "home-destination", "fetchBootRuntimeChecks",
                "fetchRecoveryStatus", "fetchUpdateChannel", "Status unavailable",
                "route: '/play'", "route: '/diagnostics'", "route: '/apps'",
                "route: '/updates'", "route: '/vpn'",
            ):
                self.assertIn(needle, page)
            for forbidden in ("Guardian", "RecipeButton", "installFlatpak"):
                self.assertNotIn(forbidden, page)
            css = (web / "src/styles/rowynMvp.css").read_text()
            for needle in (".rowyn-home", ".home-destination", "prefers-reduced-motion", "focus-visible"):
                self.assertIn(needle, css)


class MvpDiagnostics(unittest.TestCase):
    def test_read_only_bridge_has_fixed_bounded_scrubbed_probes_and_explicit_export(self):
        path = ROOT / "build/hub/src-tauri/src/commands/rowyn_diagnostics.rs"
        self.assertTrue(path.is_file(), "read-only native diagnostics bridge is missing")
        text = path.read_text()
        for needle in ('/usr/bin/bootc', '/usr/bin/lspci', '/usr/bin/systemctl',
                       '/usr/bin/rowyn-controller-check', '/usr/bin/df', '/proc/meminfo',
                       '/proc/sys/kernel/osrelease', '/usr/lib/os-release',
                       'run_bounded_command', 'Duration::from_secs(8)', 'scrub_logs',
                       'create_new(true)', 'rowyn_diagnostics_snapshot', 'rowyn_controller_scan',
                       'rowyn_export_report', 'parse_controller', 'mod tests'):
            self.assertIn(needle, text)
        for forbidden in ('sudo', 'pkexec', 'bash', 'sh -c', '.output()', 'Command::new(program)'):
            self.assertNotIn(forbidden, text)
        page = (ROOT / "build/hub/src/pages/RowynDiagnostics.tsx").read_text()
        for needle in ('Refresh checks', 'Copy report', 'Save report to Downloads',
                       'Report preview', 'rowynDiagnosticsSnapshot', 'exportRowynReport'):
            self.assertIn(needle, page)
        for forbidden in ('setInterval', 'repair', 'installFlatpak'):
            self.assertNotIn(forbidden, page)
        # Section following lives in one shared hook, so the page itself runs
        # no effect that could turn into an automatic probe or launch.
        self.assertIn('useSectionFollow(SECTIONS)', page)
        self.assertEqual(page.count('useEffect(() => {'), 0)
        hook = (ROOT / "build/hub/src/hooks/useSectionFollow.ts").read_text()
        self.assertIn("addEventListener('hashchange'", hook)
        for forbidden in ('rowynDiagnosticsSnapshot(', 'exportRowynReport(', 'openRowynSettings(',
                          'rowynStorageSnapshot(', 'rowynPowerSnapshot(', 'invoke(', 'fetch('):
            self.assertNotIn(forbidden, hook)


class MvpSteamGames(unittest.TestCase):
    def test_scan_is_manual_read_only_and_never_claims_compatibility(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('Scan Steam games (local only)', 'rowynSteamGamesScan',
                       'not mean Rowyn tested', 'ProtonDB community reports',
                       'No scan runs automatically', 'custom library location may not have been scanned'):
            self.assertIn(needle, page)
        service = (ROOT / "build/hub/src/services/rowynMvp.ts").read_text()
        self.assertIn("rowyn_steam_games_scan", service)
        scanner = ROOT / "build/hub/src-tauri/src/commands/rowyn_steam.rs"
        self.assertTrue(scanner.is_file(), "native Steam metadata scanner is missing")
        text = scanner.read_text()
        for needle in ('libraryfolders.vdf', 'appmanifest_', 'MAX_LIBRARIES',
                       'MAX_MANIFESTS', 'StateFlags', 'pub(crate) async fn rowyn_steam_games_scan'):
            self.assertIn(needle, text)
        for forbidden in ('Command::new', 'sudo', 'pkexec', 'rowyn-game-launch'):
            self.assertNotIn(forbidden, text)

    def test_recent_play_reads_steams_local_profile_without_measuring_it(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('Recently played', 'rowynSteamRecentPlay', 'formatPlaytime',
                       'formatLastPlayed', 'RecentPlay', 'no local name'):
            self.assertIn(needle, page)
        service = (ROOT / "build/hub/src/services/rowynMvp.ts").read_text()
        self.assertIn("'rowyn_steam_recent_play'", service)
        model = (ROOT / "build/hub/src/services/rowynPlayRecentModel.ts").read_text()
        for needle in ('playtime not recorded', 'last played', 'Rowyn measured nothing here'):
            self.assertIn(needle, model)
        for forbidden in ('fetch(', 'setInterval', 'setTimeout'):
            self.assertNotIn(forbidden, model)
        rust = (ROOT / "build/hub/src-tauri/src/commands/rowyn_steam.rs").read_text()
        for needle in ('localconfig.vdf', 'LastPlayed', 'Playtime', 'MAX_LOCAL_CONFIG_BYTES',
                       'pub(crate) async fn rowyn_steam_recent_play', 'spawn_blocking'):
            self.assertIn(needle, rust)
        # The recent-play read stays a local file read: no network client, no
        # launcher, and no frame-rate or latency fields invented for it.
        for forbidden in ('reqwest', 'TcpStream', 'UdpSocket', 'avg_fps', 'latency_ms'):
            self.assertNotIn(forbidden, rust)
        port = (ROOT / "build/ubuntu-hub-source.py").read_text()
        self.assertIn("commands::rowyn_steam::rowyn_steam_recent_play", port)

    def test_steam_library_leads_with_covers_and_one_disclosure(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('rowyn-library', 'rowyn-library-cover', 'coverInitials',
                       'steamStateChip', 'sortSteamGames', '<summary>Launch options</summary>',
                       'letter tile', 'cached cover art'):
            self.assertIn(needle, page)
        # The per-title editor moved into the disclosure; it did not disappear.
        for needle in ('updateProfile(item.app_id', 'assembleProfileOptions(boundary',
                       'Copy launch options', 'ProtonDB community reports'):
            self.assertIn(needle, page)
        model = (ROOT / "build/hub/src/services/rowynSteamLibraryModel.ts").read_text()
        for needle in ('sortSteamGames', 'coverInitials', 'steamLibraryCoverNote', 'steamStateChip'):
            self.assertIn(needle, model)
        for forbidden in ('fetch(', 'https://', 'setInterval', 'setTimeout'):
            self.assertNotIn(forbidden, model)
        rust = (ROOT / "build/hub/src-tauri/src/commands/rowyn_steam.rs").read_text()
        for needle in ('appcache/librarycache', 'library_600x900.jpg', 'MAX_COVER_BYTES',
                       'MAX_COVER_TOTAL_BYTES', 'cover: Option<String>', 'fn base64',
                       'scan_steam_libraries(&home, true)', 'scan_steam_libraries(home, false)'):
            self.assertIn(needle, rust)
        # Covers are bytes from Steam's cache on this disk, never a download.
        for forbidden in ('reqwest', 'TcpStream', 'UdpSocket'):
            self.assertNotIn(forbidden, rust)

    def test_shader_pre_caching_reads_steams_flag_without_editing_it(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('Shader pre-caching', 'rowynSteamShaderCache', 'shaderPreCachingLabel',
                       'SHADER_PRE_CACHING_TIP'):
            self.assertIn(needle, page)
        service = (ROOT / "build/hub/src/services/rowynMvp.ts").read_text()
        self.assertIn("'rowyn_steam_shader_cache'", service)
        model = (ROOT / "build/hub/src/services/rowynPlayShaderModel.ts").read_text()
        for needle in ('Shader Pre-Caching: on', 'Shader Pre-Caching: off',
                       'not recorded', 'Settings → Downloads'):
            self.assertIn(needle, model)
        for forbidden in ('fetch(', 'setInterval', 'setTimeout'):
            self.assertNotIn(forbidden, model)
        rust = (ROOT / "build/hub/src-tauri/src/commands/rowyn_steam.rs").read_text()
        for needle in ('config/config.vdf', 'ShaderCacheManager', 'DisableShaderCache',
                       'pub(crate) async fn rowyn_steam_shader_cache', 'spawn_blocking'):
            self.assertIn(needle, rust)
        # The read reports the flag and never rewrites Steam's configuration:
        # no write, rename, backup or launcher in the shader-code section.
        body = rust.split('struct ShaderCacheStatus', 1)[1].split(
            '// --- Steam Play default mapping', 1)[0]
        for forbidden in ('fs::write', 'fs::rename', 'Command::new', 'reqwest'):
            self.assertNotIn(forbidden, body)
        port = (ROOT / "build/ubuntu-hub-source.py").read_text()
        self.assertIn("commands::rowyn_steam::rowyn_steam_shader_cache", port)


class MvpPlay(unittest.TestCase):
    def test_play_uses_curated_flatpak_jobs_and_honest_host_boundary(self):
        page = (ROOT / "build/hub/src/pages/RowynPlay.tsx").read_text()
        for needle in ('installFlatpak', 'waitInstallJob', 'cancelInstall', 'fetchInstalledFlatpaks',
                       'rowynControllerScan', 'openRowynLauncher', 'Flatpak', 'not Rowyn-validated',
                       '2026-10-31', 'launchOptions'):
            self.assertIn(needle, page)
        bridge = ROOT / "build/hub/src-tauri/src/commands/rowyn_play.rs"
        self.assertTrue(bridge.is_file())
        text = bridge.read_text()
        for needle in ('launcher_id', 'installed_scope', 'rowyn_launcher_open', 'run_bounded_command', 'mod tests'):
            self.assertIn(needle, text)
        for forbidden in ('sudo', 'pkexec', 'bash', 'Command::new(program)', 'gaming::'):
            self.assertNotIn(forbidden, text)


class MvpWork(unittest.TestCase):
    """Work is its own destination: printing, office and focus, with a bridge."""

    def test_work_page_and_printing_bridge_are_rowyn_owned_and_bounded(self):
        page = (ROOT / "build/hub/src/pages/RowynWork.tsx").read_text()
        for needle in ("hub-section-printing", "hub-section-office", "hub-section-focus",
                       "Print test page", "Add print queue", "Open printer settings", "ipp-usb",
                       "parseDiscoveredPrinters", "addQueuePrompt",
                       "useSectionFollow(SECTIONS)", 'role="alertdialog"'):
            self.assertIn(needle, page)
        for forbidden in ("setInterval", "setTimeout", "confirmUserAction", "window.confirm"):
            self.assertNotIn(forbidden, page)
        model = (ROOT / "build/hub/src/services/rowynWorkModel.ts").read_text()
        for needle in ("queuesSummary", "queueStateLabel", "testPagePrompt", "unknown",
                       "parseDiscoveredPrinters", "addQueuePrompt", "DiscoveredPrinter"):
            self.assertIn(needle, model)
        service = (ROOT / "build/hub/src/services/rowynWork.ts").read_text()
        for needle in ("rowyn_print_queues", "rowyn_print_test_page", "rowyn_print_add_queue"):
            self.assertIn(needle, service)
        bridge = ROOT / "build/hub/src-tauri/src/commands/rowyn_work.rs"
        self.assertTrue(bridge.is_file(), "printing bridge is missing")
        text = bridge.read_text()
        for needle in ("/usr/bin/lpstat", "/usr/share/rowyn/print-test-page.txt",
                       "/usr/sbin/lpadmin", "queue_argument", "parse_lpstat", "run_bounded_command",
                       "add_queue_args", "printer_uri", "queue_names",
                       "rowyn_print_queues", "rowyn_print_test_page", "rowyn_print_add_queue",
                       "mod tests"):
            self.assertIn(needle, text)
        for forbidden in ("sudo", "pkexec", "sh -c", "Command::new(program)"):
            self.assertNotIn(forbidden, text)

    def test_image_ships_the_printing_stack_and_the_test_page(self):
        container = (ROOT / "Containerfile").read_text()
        for token in ("ipp-usb", "avahi-daemon", "avahi-daemon.service",
                      "print-test-page.txt"):
            self.assertIn(token, container)
        self.assertTrue((ROOT / "shared/print-test-page.txt").is_file())

    def test_port_splits_work_out_of_apps_and_registers_the_bridge(self):
        port = (ROOT / "build/ubuntu-hub-source.py").read_text()
        for token in ('{"key": "Work", "route": "/work"', '"/work": "Work"',
                      "RowynWork", "pub(crate) mod rowyn_work;",
                      "commands::rowyn_work::rowyn_print_queues",
                      "commands::rowyn_work::rowyn_print_test_page",
                      "commands::rowyn_work::rowyn_print_add_queue",
                      "WORK_SECTIONS", "Work: { hint:",
                      'Apps: { hint: "Discover and install apps"'):
            self.assertIn(token, port)


class MvpExportTests(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_exported_frontend_tests_follow_supported_surface_without_dead_imports(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-mvp-tests-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.apply_rowyn_mvp(root)
            web = root / "src/rowyn-hub-web"
            contract = (web / "tests/command-contract.test.mjs").read_text()
            self.assertNotIn('const gamingRust = await readFile', contract)
            self.assertIn('App Store install waits out slow mirrors', contract)
            self.assertIn('Updates page gives plain-language next steps', contract)
            self.assertIn('frontend stays behind the typed Tauri/Rust boundary', contract)
            self.assertIn('for (const file of await sourceFiles(resolve(root, "src")))', contract)
            self.assertIn('bug-hunt round 4: secrets stay out of strings, writes stay atomic', contract)
            self.assertIn('bug-hunt round 6: fail closed everywhere', contract)
            self.assertIn('bug-hunt round 2 closes races and verifier gaps', contract)
            smoke = (web / "tests/hub-shell-smoke.test.mjs").read_text()
            self.assertIn('RowynDiagnostics.tsx', smoke)
            self.assertIn('RowynPlay.tsx', smoke)
            self.assertIn('RowynDevelopment.tsx', smoke)
            self.assertNotIn('"ThisPc.tsx":', smoke)


if __name__ == "__main__":
    unittest.main()
