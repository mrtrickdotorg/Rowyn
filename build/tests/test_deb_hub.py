#!/usr/bin/env python3
"""Coverage for the Hub half of the D3 guarded .deb path (decided
Nov 12, 2026): the rowyn_deb backend owns the dedicated rowyn-deb
Distrobox, re-runs rowyn-deb-inspect before any install, refuses
system packages, and the Apps page carries the RowynDebCard. The
overlay-source contracts run ungated; the generated-tree contract is
gated on the pinned upstream checkout like the other port tests."""
from __future__ import annotations

import re
from pathlib import Path
import tempfile
import unittest

from test_hub_port import ROOT, hub, _pinned_repo

HUB_OVERLAY = ROOT / "build/hub"
BACKEND = HUB_OVERLAY / "src-tauri/src/commands/rowyn_deb.rs"
DEV_BACKEND = HUB_OVERLAY / "src-tauri/src/commands/rowyn_development.rs"
PORT = ROOT / "build/ubuntu-hub-source.py"

COMMANDS = ("rowyn_deb_state", "rowyn_deb_inspect", "rowyn_deb_start",
            "rowyn_deb_job", "rowyn_deb_cancel")


class DebHubOverlay(unittest.TestCase):
    def test_overlay_files_exist(self):
        for relative in (
                "src-tauri/src/commands/rowyn_deb.rs",
                "src-tauri/src/commands/rowyn_deb_tests.rs",
                "src/components/RowynDebCard.tsx",
                "src/services/rowynDeb.ts",
                "src/services/rowynDebModel.ts",
                "tests/rowyn-deb.test.mjs"):
            self.assertTrue((HUB_OVERLAY / relative).is_file(), relative)

    def test_backend_owns_one_dedicated_container(self):
        backend = BACKEND.read_text(encoding="utf-8")
        self.assertIn('const NAME: &str = "rowyn-deb";', backend)
        self.assertIn('"io.rowyn.deb"', backend)
        self.assertIn('"deb-v1"', backend)
        self.assertIn('name=^rowyn-deb$', backend)
        # No host elevation anywhere: the only sudo is the fixed argv
        # used *inside* the container, mirroring the development box.
        self.assertNotIn("pkexec", backend)
        for line in backend.splitlines():
            if "sudo" in line and "sudo_apt" not in line:
                self.assertIn("/usr/bin/sudo", line, line)

    def test_backend_shares_the_development_image_pin_and_apt_floor(self):
        backend = BACKEND.read_text(encoding="utf-8")
        development = DEV_BACKEND.read_text(encoding="utf-8")
        pin = re.search(r"docker\.io/library/ubuntu@sha256:[0-9a-f]{64}",
                        development)
        self.assertIsNotNone(pin)
        self.assertIn(pin.group(0), backend)
        for needle in ("3.3.3ubuntu0.1", "apt_refresh_commands",
                       "apt_floor_command", "--only-upgrade"):
            self.assertIn(needle, backend)

    def test_install_is_gated_on_a_fresh_inspection(self):
        backend = BACKEND.read_text(encoding="utf-8")
        self.assertIn("run_inspect(&file)?", backend)
        self.assertIn('verdict != "user-app"', backend)
        self.assertIn('format!("install:{}", report.package)', backend)
        self.assertIn("validate_deb_file", backend)
        self.assertIn("deb-pending", backend)
        self.assertIn("deb-apps.json", backend)
        # Removal unexports before it removes the package.
        remove = backend.split("PlannedOp::Remove(package) => {", 1)[1]
        self.assertLess(remove.index("unexport_all"),
                        remove.index('sudo_apt(&["remove"'))

    def test_port_registers_the_surface_on_the_apps_page(self):
        port = PORT.read_text(encoding="utf-8")
        self.assertIn("def apply_rowyn_deb_path(root):", port)
        self.assertIn("pub(crate) mod rowyn_deb;", port)
        for command in COMMANDS:
            self.assertIn(f"commands::rowyn_deb::{command}", port)
        self.assertIn("RowynDebCard", port)
        self.assertIn("<AppsDefaultsAccess />", port)


class DebHubGeneratedTree(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_deb_path_is_registered_in_the_generated_tree(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-deb-hub-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            self.assertIn("pub(crate) mod rowyn_deb;",
                          (web / "src-tauri/src/commands/mod.rs").read_text(
                              encoding="utf-8"))
            handlers = (web / "src-tauri/src/main.rs").read_text(encoding="utf-8")
            for command in COMMANDS:
                self.assertIn(f"commands::rowyn_deb::{command}", handlers)
            apps = (web / "src/pages/Apps.tsx").read_text(encoding="utf-8")
            self.assertIn('import { RowynDebCard } from "../components/RowynDebCard";',
                          apps)
            self.assertLess(apps.index("<AppsDefaultsAccess />"),
                            apps.index("<RowynDebCard />"))
            self.assertTrue(
                (web / "src-tauri/src/commands/rowyn_deb.rs").is_file())
            self.assertTrue((web / "src/components/RowynDebCard.tsx").is_file())
            self.assertTrue((web / "tests/rowyn-deb.test.mjs").is_file())
            import json
            package = json.loads((web / "package.json").read_text(encoding="utf-8"))
            self.assertIn("rowyn-deb.test.mjs",
                          package["scripts"]["test:rowyn-mvp"])


if __name__ == "__main__":
    unittest.main()
