#!/usr/bin/env python3
"""Coverage for the Brave default-browser wiring: the Flatpak preinstall
declaration + boot oneshot (shared/brave.preinstall,
shared/rowyn-flatpak-preinstall.service) and the mimeapps defaults
(shared/browser-defaults.sh)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/browser-defaults.sh"
PREINSTALL = ROOT / "shared/brave.preinstall"
UNIT = ROOT / "shared/rowyn-flatpak-preinstall.service"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

BRAVE_DESKTOP = "com.brave.Browser.desktop"


def ini(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
        elif "=" in line:
            key, value = line.split("=", 1)
            sections[current][key.strip()] = value.strip()
    return sections


class BrowserDefaultsTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        done = subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(cls.root)],
            text=True, capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        assert done.returncode == 0, done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_system_mimeapps_defaults_to_brave(self):
        defaults = ini(self.root / "etc/xdg/mimeapps.list")["Default Applications"]
        for mime in ("x-scheme-handler/http", "x-scheme-handler/https",
                     "text/html", "application/xhtml+xml"):
            self.assertEqual(defaults[mime], BRAVE_DESKTOP, mime)

    def test_skel_seed_matches_system_defaults(self):
        system = (self.root / "etc/xdg/mimeapps.list").read_bytes()
        skel = (self.root / "etc/skel/.config/mimeapps.list").read_bytes()
        self.assertEqual(system, skel)

    def test_idempotent(self):
        again = subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(self.root)],
            text=True, capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        self.assertEqual(again.returncode, 0, again.stderr)
        defaults = ini(self.root / "etc/xdg/mimeapps.list")["Default Applications"]
        self.assertEqual(defaults["x-scheme-handler/https"], BRAVE_DESKTOP)


class PreinstallDeclaration(unittest.TestCase):
    def test_brave_is_the_preinstalled_app(self):
        sections = ini(PREINSTALL)
        self.assertIn("Flatpak Preinstall com.brave.Browser", sections)
        group = sections["Flatpak Preinstall com.brave.Browser"]
        self.assertEqual(group.get("Branch"), "stable")
        self.assertEqual(group.get("IsRuntime"), "false")

    def test_unit_runs_flatpak_preinstall_for_the_system(self):
        unit = UNIT.read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/flatpak preinstall --system -y --noninteractive", unit)
        self.assertIn("After=network-online.target", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_containerfile_and_workflow_wire_it(self):
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("/usr/share/flatpak/preinstall.d/rowyn-brave.preinstall", containerfile)
        self.assertIn("systemctl enable rowyn-flatpak-preinstall.service", containerfile)
        self.assertIn("/ctx/shared/browser-defaults.sh --root /", containerfile)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rowyn-brave.preinstall", workflow)
        self.assertIn("rowyn-flatpak-preinstall.service", workflow)


if __name__ == "__main__":
    unittest.main()
