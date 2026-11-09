#!/usr/bin/env python3
"""Coverage for the first-login Hub presentation (shared/rowyn-first-run
+ its XDG autostart entry): exactly one launch per user, marker first,
never a nag."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "shared/rowyn-first-run"
DESKTOP = ROOT / "shared/rowyn-first-run.desktop"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"


class FirstRunLauncher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.launches = self.home / "launches"
        self.hub = self.home / "hub-stub"
        self.hub.write_text(
            "#!/bin/sh\necho launched >> \"$ROWYN_TEST_LAUNCHES\"\n", encoding="utf-8")
        self.hub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def run_launcher(self) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("XDG_CONFIG_HOME", None)
        env["ROWYN_HUB_SHELL"] = str(self.hub)
        env["ROWYN_TEST_LAUNCHES"] = str(self.launches)
        return subprocess.run(
            ["/bin/sh", str(LAUNCHER)], text=True, capture_output=True, env=env)

    @property
    def marker(self) -> Path:
        return self.home / ".config/rowyn/first-run-presented"

    def test_first_login_launches_the_hub_once(self):
        done = self.run_launcher()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.marker.exists(), "marker must be recorded")
        self.assertEqual(self.launches.read_text().count("launched"), 1)

    def test_second_login_never_launches_again(self):
        self.run_launcher()
        done = self.run_launcher()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.launches.read_text().count("launched"), 1)

    def test_marker_is_written_even_without_a_hub(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("XDG_CONFIG_HOME", None)
        env["ROWYN_HUB_SHELL"] = str(self.home / "missing-hub")
        done = subprocess.run(
            ["/bin/sh", str(LAUNCHER)], text=True, capture_output=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.launches.exists())


class FirstRunWiring(unittest.TestCase):
    def test_autostart_entry(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("Exec=/usr/bin/rowyn-first-run", desktop)
        self.assertIn("OnlyShowIn=KDE;", desktop)
        self.assertIn("NoDisplay=true", desktop)

    def test_containerfile_and_workflow_wire_it(self):
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("install -Dm755 /ctx/shared/rowyn-first-run /usr/bin/rowyn-first-run",
                      containerfile)
        self.assertIn("/etc/xdg/autostart/rowyn-first-run.desktop", containerfile)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/bin/rowyn-first-run", workflow)


if __name__ == "__main__":
    unittest.main()
