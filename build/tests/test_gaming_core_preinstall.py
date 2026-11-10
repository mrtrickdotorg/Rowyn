#!/usr/bin/env python3
"""Coverage for the gaming-core preinstall declaration
(shared/gaming-core.preinstall): Steam, Lutris and Heroic ship from
first boot through the same Flatpak preinstall mechanism as Brave —
the distribution's identity feature — and stay uninstallable because
preinstall tracks the system installation rather than the image."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PREINSTALL = ROOT / "shared/gaming-core.preinstall"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

GAMING_CORE = {
    "com.valvesoftware.Steam": "Steam",
    "net.lutris.Lutris": "Lutris",
    "com.heroicgameslauncher.hgl": "Heroic",
}


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


class GamingCorePreinstall(unittest.TestCase):
    def test_exactly_the_gaming_core_is_declared(self):
        sections = ini(PREINSTALL)
        self.assertEqual(
            set(sections),
            {f"Flatpak Preinstall {app_id}" for app_id in GAMING_CORE})
        for app_id in GAMING_CORE:
            group = sections[f"Flatpak Preinstall {app_id}"]
            self.assertEqual(group.get("Branch"), "stable", app_id)
            self.assertEqual(group.get("IsRuntime"), "false", app_id)

    def test_containerfile_and_workflow_wire_it(self):
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "/usr/share/flatpak/preinstall.d/rowyn-gaming-core.preinstall",
            containerfile)
        self.assertIn("/ctx/shared/gaming-core.preinstall", containerfile)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rowyn-gaming-core.preinstall", workflow)
        for app_id in GAMING_CORE:
            self.assertIn(app_id, workflow)


if __name__ == "__main__":
    unittest.main()
