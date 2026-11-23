"""Overlay contracts: section anchors, bridge wiring, and CI coverage."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "build/hub"
PORT = ROOT / "build/ubuntu-hub-source.py"
WORKFLOW = ROOT / ".github/workflows/build.yml"
CONTAINER = ROOT / "Containerfile"
HELPER = ROOT / "shared/rowyn-boot-health"

# Manifest sections and the file that renders each `hub-section-*` anchor.
# Storage and Power live in the shared SystemCare component, not the page.
SECTION_ANCHORS = {
    "src/pages/RowynPlay.tsx": {
        "Gaming": "hub-section-gaming",
        "SteamLibrary": "hub-section-steam-library",
        "Compatibility": "hub-section-compatibility",
        "Controllers": "hub-section-controllers",
    },
    "src/pages/RowynWork.tsx": {
        "Printing": "hub-section-printing",
        "Office": "hub-section-office",
        "Focus": "hub-section-focus",
    },
    "src/pages/RowynDiagnostics.tsx": {"Diagnostics": "hub-section-diagnostics"},
    "src/components/SystemCare.tsx": {
        "Storage": "hub-section-storage",
        "Power": "hub-section-power",
    },
    "src/pages/Backups.tsx": {"Backups": "hub-section-backups"},
    "src/pages/RowynDevelopment.tsx": {"Development": "hub-section-development"},
}
ROUTES_WITH_SECTIONS = (
    "src/pages/RowynPlay.tsx",
    "src/pages/RowynWork.tsx",
    "src/pages/RowynDiagnostics.tsx",
    "src/pages/Backups.tsx",
    "src/pages/RowynDevelopment.tsx",
)

KCM_PACKAGES = ("bluedevil", "kscreen", "powerdevil", "plasma-nm", "plasma-pa",
                "kde-config-flatpak", "kup-backup")
SETTINGS_TARGETS = ("defaults", "file-associations", "flatpak-permissions",
                    "bluetooth", "wifi", "audio", "displays", "backups", "power",
                    "printers")


class SectionReachability(unittest.TestCase):
    def test_every_declared_section_has_exactly_one_rendered_anchor(self):
        for relative, mapping in SECTION_ANCHORS.items():
            text = (HUB / relative).read_text(encoding="utf-8")
            for section, anchor in mapping.items():
                self.assertEqual(text.count(f'id="{anchor}"'), 1,
                                 f"{relative}: section {section} must render {anchor} once")

    def test_each_rowyn_route_follows_its_declared_sections(self):
        for relative in ROUTES_WITH_SECTIONS:
            text = (HUB / relative).read_text(encoding="utf-8")
            self.assertIn("useSectionFollow(SECTIONS)", text,
                          f"{relative} must follow its declared sections")

    def test_play_has_one_compatibility_section(self):
        play = (HUB / "src/pages/RowynPlay.tsx").read_text(encoding="utf-8")
        self.assertEqual(play.count("Compatibility &amp; anti-cheat"), 1)
        self.assertEqual(play.count('id="hub-section-compatibility"'), 1)

    def test_play_manifest_declares_the_steam_library_section(self):
        port = PORT.read_text(encoding="utf-8")
        for section in ("Gaming", "SteamLibrary", "Compatibility", "Controllers"):
            self.assertIn(f'{{"key": "{section}"', port)

    def test_hub_sources_never_poll(self):
        # Dashboard refreshed itself every 60 s and escaped this contract,
        # because only pages with section anchors were scanned. Bounded reads
        # run when a page opens or when the user asks for them, never on a timer.
        offenders = sorted(
            str(path.relative_to(HUB))
            for path in (HUB / "src").rglob("*")
            if path.suffix in {".ts", ".tsx"}
            and "setInterval" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(offenders, [], "bounded reads run on open or on request, never on a timer")

    def test_dashboard_refreshes_only_when_the_user_asks(self):
        dashboard = (HUB / "src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        self.assertIn("Refresh readings", dashboard)
        self.assertIn("refreshReadings", dashboard)
        self.assertNotIn("Refreshes automatically", dashboard)


class BootHealthWiring(unittest.TestCase):
    def test_model_validates_the_record_the_helper_writes(self):
        helper = HELPER.read_text(encoding="utf-8")
        model = (HUB / "src/services/rowynBootHealthModel.ts").read_text(encoding="utf-8")
        for key in ("schema_version", "known_good", "failures", "quarantined",
                    "rollback", "last_boot"):
            self.assertIn(f'"{key}"', helper, f"helper must write {key}")
            self.assertIn(key, model, f"model must read {key}")
        self.assertIn("BOOT_HEALTH_SCHEMA = 1", model)

    def test_bridge_is_bounded_read_only_and_registered(self):
        bridge = (HUB / "src-tauri/src/commands/rowyn_boot_health.rs").read_text(encoding="utf-8")
        for token in ("/var/lib/rowyn/boot-health.json", "O_NOFOLLOW", "LIMIT",
                      "spawn_blocking", "rowyn_boot_health_snapshot", "mod tests"):
            self.assertIn(token, bridge)
        for forbidden in ("sudo", "pkexec", "Command::new", "clear-quarantine",
                          "bootc rollback", "fs::write"):
            self.assertNotIn(forbidden, bridge)
        port = PORT.read_text(encoding="utf-8")
        self.assertIn("pub(crate) mod rowyn_boot_health;", port)
        self.assertIn("commands::rowyn_boot_health::rowyn_boot_health_snapshot", port)

    def test_panel_lands_on_updates_and_home(self):
        port = PORT.read_text(encoding="utf-8")
        self.assertIn("BootHealthPanel", port)
        self.assertIn("<BootHealthPanel />", port)
        dashboard = (HUB / "src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        self.assertIn("<BootHealthPanel compact />", dashboard)

    def test_panel_exposes_no_write_path(self):
        panel = (HUB / "src/components/BootHealthPanel.tsx").read_text(encoding="utf-8")
        # The panel may describe rollback attempts; it must not offer to start
        # one, clear a quarantine, elevate, or write anything.
        for forbidden in ("clear-quarantine", "pkexec", "sudo", "systemctl",
                          "invoke(", "fs::", "writeFile"):
            self.assertNotIn(forbidden, panel)
        self.assertIn("asks for confirmation first", panel)
        self.assertIn("Read-only", panel)


class SettingsCoverage(unittest.TestCase):
    def test_ui_targets_match_the_bridge_allowlist_and_power_is_the_power_page(self):
        ts = (HUB / "src/services/rowynSettings.ts").read_text(encoding="utf-8")
        rust = (HUB / "src-tauri/src/commands/rowyn_settings.rs").read_text(encoding="utf-8")
        for target in SETTINGS_TARGETS:
            self.assertIn(f'"{target}"', rust, f"bridge must map {target}")
            self.assertIn(target, ts, f"UI must know {target}")
        self.assertIn('"power" => Ok("kcm_powerdevilprofilesconfig")', rust)
        self.assertIn('const POWER_ALTERNATES: &[&str] = &["kcm_powerdevil"]', rust)

    def test_every_module_provider_is_installed_explicitly(self):
        packages = CONTAINER.read_text(encoding="utf-8").split()
        for package in KCM_PACKAGES:
            self.assertIn(package, packages, f"{package} must be explicit, not a recommend")

    def test_image_smoke_checks_probe_binaries(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for binary in ("/usr/bin/nmcli", "/usr/sbin/rfkill", "/usr/bin/kscreen-doctor",
                       "/usr/bin/df"):
            self.assertIn(f"test -x {binary}", workflow)
        # bootc-rootfs.sh deletes /var, so the payload has no dpkg database:
        # package presence is asserted in the Containerfile instead.
        self.assertNotIn('dpkg -s "$package"', workflow)
        self.assertIn(
            "for package in bluedevil kscreen powerdevil plasma-nm plasma-pa "
            "kde-config-flatpak kup-backup; do", CONTAINER.read_text(encoding="utf-8"))


class DiagnosticsStateVocabulary(unittest.TestCase):
    """The page must speak the bridge's state vocabulary, not one of its own.

    The page once tested for "available" — a state rowyn_diagnostics.rs never
    emits — so every healthy check rendered in the warning tone and the
    "Needs attention" count included all of them.
    """

    @staticmethod
    def _declared(pattern: str, text: str) -> list:
        match = re.search(pattern, text)
        assert match is not None, "READING_STATES declaration is missing"
        return re.findall(r"['\"]([a-z]+)['\"]", match.group(1))

    def test_bridge_and_model_declare_the_same_reading_states(self):
        rust = (HUB / "src-tauri/src/commands/rowyn_diagnostics.rs").read_text(encoding="utf-8")
        model = (HUB / "src/services/rowynMvpModel.ts").read_text(encoding="utf-8")
        bridge = self._declared(r"READING_STATES[^=]*=\s*\[([^\]]*)\]", rust)
        frontend = self._declared(r"READING_STATES[^=]*=\s*\[([^\]]*)\]", model)
        self.assertEqual(bridge, frontend, "the page and the bridge must share one state list")
        self.assertEqual(sorted(bridge), sorted({"ok", "attention", "error", "unavailable"}))

    def test_page_classifies_through_the_model_instead_of_inventing_a_state(self):
        page = (HUB / "src/pages/RowynDiagnostics.tsx").read_text(encoding="utf-8")
        self.assertNotIn("'available'", page)
        self.assertIn("readingNeedsAttention(reading.state)", page)
        self.assertIn("readingTone(reading.state)", page)
        self.assertIn("readingStateLabel(reading.state)", page)


class ContainerToolResolution(unittest.TestCase):
    """Readiness, storage and Development must agree on where podman lives.

    Readiness and storage pinned /usr/bin while Development already searched
    package and local locations, so a host with podman in /usr/local/bin
    reported the development environment as unavailable on two pages even
    though the third one could manage it.
    """

    def test_callers_resolve_container_tools_instead_of_pinning_package_paths(self):
        development = (HUB / "src-tauri/src/commands/rowyn_development.rs").read_text(encoding="utf-8")
        readiness = (HUB / "src-tauri/src/commands/rowyn_readiness.rs").read_text(encoding="utf-8")
        care = (HUB / "src-tauri/src/commands/rowyn_care.rs").read_text(encoding="utf-8")
        self.assertIn("pub(crate) fn podman_path()", development)
        self.assertIn("pub(crate) fn distrobox_path()", development)
        self.assertIn("rowyn_development::podman_path()", readiness)
        self.assertIn("rowyn_development::distrobox_path()", readiness)
        self.assertIn("rowyn_development::podman_path()", care)
        for name, text in (("readiness", readiness), ("storage", care)):
            self.assertNotIn('\"/usr/bin/podman\"', text, f"{name} must not pin the package path")
            self.assertNotIn('\"/usr/bin/distrobox\"', text, f"{name} must not pin the package path")


class HubTestJob(unittest.TestCase):
    def test_ci_runs_the_hub_suites_against_the_pinned_export(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("hub-tests:", workflow)
        for token in ("npm ci", "node --test", "ubuntu-hub-source.py",
                      "cargo test --locked rowyn_ -- --nocapture"):
            self.assertIn(token, workflow)

    def test_ci_fails_instead_of_skipping_when_a_fixture_base_is_missing(self):
        # The twelve policy-fixture tests return early with a "skipping" note
        # when the host has no policy-compliant temp base, and libtest hides
        # that note for a passing test: without --nocapture and a check on the
        # note, a run where they never executed still looks green.
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('grep -Fq "skipping "', workflow)
        self.assertIn("passed < 130", workflow)


if __name__ == "__main__":
    unittest.main()


class PlayDisplayReadings(unittest.TestCase):
    """Play reports the display stack's own per-output adaptive-sync value.

    The page parsed kwinrc's [Wayland] VrrPolicy, a key Plasma 6 no longer
    writes — the policy moved per output into KScreen — so the reading said
    "not set (KWin default)" on machines where VRR was on, and offered no
    route to change it.
    """

    def test_bridge_reads_per_output_vrr_and_not_kwinrc(self):
        rust = (HUB / "src-tauri/src/commands/rowyn_play.rs").read_text(encoding="utf-8")
        # The key Plasma 6 stopped writing, and the file it lived in.
        self.assertNotIn("VrrPolicy", rust, "Plasma 6 keeps the VRR policy per output")
        self.assertNotIn(".config/kwinrc", rust)
        self.assertNotIn("kwin_vrr_policy", rust)
        self.assertIn('strip_prefix("Vrr:")', rust)

    def test_page_offers_display_settings_beside_the_readings(self):
        page = (HUB / "src/pages/RowynPlay.tsx").read_text(encoding="utf-8")
        settings = (HUB / "src/services/rowynSettings.ts").read_text(encoding="utf-8")
        self.assertIn("openRowynSettings('displays')", page)
        self.assertIn("Open Display settings", page)
        # The target the page opens must stay on the bridge's allowlist.
        self.assertIn("displays: 'kcm_kscreen'", settings)
