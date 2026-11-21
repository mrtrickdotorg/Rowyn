"""Fresh-export integration checks for Rowyn's daily-driver additions."""
import json
from pathlib import Path
import tempfile
import unittest
from test_hub_port import ROOT, hub, _pinned_repo


class QolPackagingTests(unittest.TestCase):
    def test_native_report_uses_trusted_provenance_and_one_sanitizer(self):
        diagnostics = (ROOT / "build/hub/src-tauri/src/commands/rowyn_diagnostics.rs").read_text()
        self.assertIn("rowyn_support::refresh_public_provenance()", diagnostics)
        self.assertIn("rowyn_support::sanitize_report", diagnostics)
        self.assertNotIn("let clean = scrub_logs(&strip_ansi(report));", diagnostics)

    def test_provenance_notification_and_dolphin_menu_are_packaged(self):
        container = (ROOT / "Containerfile").read_text()
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        for token in ("ARG ROWYN_SOURCE_REVISION=unknown", "write-build-info.py", "rowyn-update-notify.timer", "systemctl --global enable rowyn-update-notify.timer", "/usr/share/kio/servicemenus/rowyn-development-here.desktop"):
            self.assertIn(token, container)
        self.assertIn('--build-arg "ROWYN_SOURCE_REVISION=${input_revision}"', workflow)
        for package in ("kup-backup", "bup", "rsync", "kde-config-flatpak", "libnotify-bin"):
            self.assertIn(package, container.split())


class QolBootHealthTests(unittest.TestCase):
    def test_hub_reads_and_surfaces_the_boot_health_record(self):
        port = (ROOT / "build/ubuntu-hub-source.py").read_text()
        for token in ("rowyn_boot_health_snapshot",
                      "pub(crate) mod rowyn_boot_health;",
                      "BootHealthPanel"):
            self.assertIn(token, port)
        dashboard = (ROOT / "build/hub/src/pages/Dashboard.tsx").read_text()
        self.assertIn("<BootHealthPanel compact />", dashboard)
        panel = (ROOT / "build/hub/src/components/BootHealthPanel.tsx").read_text()
        for token in ("rowynBootHealthSnapshot", "Refresh boot health",
                      "Read-only. Clearing a quarantine is a terminal-only action."):
            self.assertIn(token, panel)
        bridge = (ROOT / "build/hub/src-tauri/src/commands/rowyn_boot_health.rs").read_text()
        for token in ("/var/lib/rowyn/boot-health.json", "O_NOFOLLOW", "spawn_blocking",
                      "rowyn_boot_health_snapshot", "mod tests"):
            self.assertIn(token, bridge)
        for forbidden in ("sudo", "pkexec", "bash", "Command::new", ".output()",
                          "clear-quarantine"):
            self.assertNotIn(forbidden, bridge)


class QolUpdateChannel(unittest.TestCase):
    """The channel the device follows must be named, never invented.

    `current_update_channel` resolves the tag from the probe cache or bootc's
    image reference; the UI maps that tag to a display name. Rowyn's channels
    are `stable` today and `test` when it is published, so both need a label
    or a real system reads as a bare tag.
    """

    def test_rowyn_channels_are_named_and_unknown_tags_stay_raw(self):
        port = (ROOT / "build/ubuntu-hub-source.py").read_text()
        self.assertIn('stable: "Stable"', port)
        self.assertIn('test: "Test"', port)
        dashboard = (ROOT / "build/hub/src/pages/Dashboard.tsx").read_text()
        # An unreadable channel says so rather than defaulting to a channel name.
        self.assertIn("updateChannel ?? 'Unavailable'", dashboard)
        self.assertIn('label="Update channel"', dashboard)

    def test_channel_resolution_reads_bootc_probe_then_image_reference(self):
        bootc = (ROOT / "hub-source/src/rowyn-shared-rs/src/system/bootc.rs")
        policy = (ROOT / "hub-source/src/rowyn-shared-rs/src/system/bootc_policy.rs")
        if not bootc.exists() or not policy.exists():
            self.skipTest("ported tree is not present; run the port first")
        resolution = bootc.read_text()
        self.assertIn('read_section("bootc-branch")', resolution)
        self.assertIn("image_reference()", resolution)
        self.assertIn("branch_from_ref", resolution)
        # A digest-only reference has no tag, so it must not guess a channel.
        self.assertIn("pub fn branch_from_ref", policy.read_text())


class QolHomeStatusRow(unittest.TestCase):
    """Home must not open on two permanent failure cards off a Rowyn image."""

    def test_home_hides_the_status_row_when_neither_source_exists(self):
        root = ROOT / "build/hub/src"
        dashboard = (root / "pages/Dashboard.tsx").read_text()
        self.assertIn("hidden={!statusRowVisible}", dashboard)
        self.assertIn("<BootHealthPanel compact />", dashboard)
        self.assertIn("<UpdateStatusBanner />", dashboard)
        for path, source in (("components/UpdateStatusBanner.tsx", "updates"),
                             ("components/BootHealthPanel.tsx", "bootHealth")):
            self.assertIn(f"reportStatusSource('{source}'", (root / path).read_text())
        # `hidden` has to beat the section's own display rule.
        self.assertIn(".rowyn-home-section[hidden]", (root / "styles/rowynMvp.css").read_text())


class QolIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_backup_workspace_is_registered_once_with_native_handlers(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-qol-export-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            destinations = json.loads((web / "src/data/hubRoutes.json").read_text())["destinations"]
            backups = [d for d in destinations if d["key"] == "Backups"]
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0]["route"], "/backups")
            self.assertIn('path="/backups"', (web / "src/App.tsx").read_text())
            self.assertEqual((web / "src/components/Sidebar.tsx").read_text().count('Backups: { hint:'), 1)
            self.assertIn("Backups: BACKUPS_SECTIONS", (web / "src/data/destinations.ts").read_text())
            handlers = (web / "src-tauri/src/main.rs").read_text()
            for name in ("rowyn_backups_snapshot", "rowyn_open_settings", "rowyn_development_preset_preview"):
                self.assertIn(name, handlers)
            self.assertNotIn("commands::dev_tools::", handlers)
            self.assertIn("handle_development_cli", handlers)
            self.assertIn("SetupChecklist", (web / "src/pages/Dashboard.tsx").read_text())
            self.assertIn("AppsDefaultsAccess", (web / "src/pages/Apps.tsx").read_text())
            updates_page = (web / "src/pages/Updates.tsx").read_text()
            self.assertNotIn("UpdateStatusBanner", updates_page)
            self.assertLess(updates_page.index("<UpdatesOverview />"),
                            updates_page.index("rowyn-updates-panels"))
            self.assertIn("RecoveryClarity", (web / "src/pages/Updates.tsx").read_text())
            diagnostics_page = (web / "src/pages/RowynDiagnostics.tsx").read_text()
            self.assertIn("<StorageCare />", diagnostics_page)
            self.assertIn("<PowerCare />", diagnostics_page)
            section_source = (web / "src/data/hubSections.ts").read_text()
            self.assertIn("Storage: IconDatabase", section_source)
            self.assertIn("Power: IconChip", section_source)
            self.assertIn("SteamLibrary: IconGamepad", section_source)
            for name in ("rowyn_storage_snapshot", "rowyn_power_snapshot"):
                self.assertIn(f"commands::rowyn_care::{name}", handlers)
            self.assertIn("pub(crate) mod rowyn_care;", (web / "src-tauri/src/commands/mod.rs").read_text())
            app_store = (web / "src/components/AppStoreSection.tsx").read_text()
            self.assertIn("<AppRemovalConfirmation", app_store)
            self.assertIn("setRemoval(app)", app_store)
            self.assertIn("tests/rowyn-removal.test.mjs", json.loads((web / "package.json").read_text())["scripts"]["test:rowyn-mvp"])


if __name__ == "__main__":
    unittest.main()
