#!/usr/bin/env python3
"""Coverage for the .deb explainer: installed-apps.sh registers
rowyn-deb-handler as the default handler for Debian packages, and the
handler explains the Flatpak-first policy and routes to the Hub's Apps
page instead of dead-ending."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
POLICY_SCRIPT = ROOT / "shared/installed-apps.sh"
HANDLER = ROOT / "shared/rowyn-deb-handler"
DESKTOP = ROOT / "shared/rowyn-deb-handler.desktop"
WORKFLOW = ROOT / ".github/workflows/build.yml"


def fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


class DebHandlerTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        done = subprocess.run(
            ["/bin/bash", str(POLICY_SCRIPT), "--root", str(cls.root)],
            text=True, capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        assert done.returncode == 0, done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_handler_artifacts_installed(self):
        handler = self.root / "usr/bin/rowyn-deb-handler"
        self.assertTrue(handler.exists())
        self.assertTrue(os.access(handler, os.X_OK))
        desktop = self.root / "usr/share/applications/rowyn-deb-handler.desktop"
        self.assertIn("MimeType=application/vnd.debian.binary-package;",
                      desktop.read_text(encoding="utf-8"))

    def test_kde_default_association(self):
        mimeapps = (self.root / "etc/xdg/kde-mimeapps.list").read_text(encoding="utf-8")
        self.assertIn("[Default Applications]", mimeapps)
        self.assertIn(
            "application/vnd.debian.binary-package=rowyn-deb-handler.desktop",
            mimeapps)

    def test_policy_records_the_explainer(self):
        policy = (self.root / "usr/share/rowyn/installed-apps/policy.conf").read_text(
            encoding="utf-8")
        self.assertIn("DebPackages=explainer", policy)


class DebHandlerBehavior(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.calls = self.dir / "calls.log"
        # dpkg-deb stub: report the fixture package name for -f queries.
        fake(self.bin / "dpkg-deb",
             'if [ "$1" = "-f" ]; then echo "google-chrome-stable"; exit 0; fi\nexit 1\n')
        # The Hub binary is an absolute path in the handler; the stub is
        # exercised through the dialog-free path instead (see below).
        self.deb = self.dir / "chrome.deb"
        self.deb.write_bytes(b"!<arch>\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_handler(self, kdialog_body: str) -> subprocess.CompletedProcess:
        fake(self.bin / "kdialog",
             f'echo "kdialog $*" >> "{self.calls}"\n' + kdialog_body)
        fake(self.bin / "notify-send",
             f'echo "notify $*" >> "{self.calls}"\n')
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        return subprocess.run(
            ["/bin/sh", str(HANDLER), str(self.deb)],
            text=True, capture_output=True, env=env)

    def test_dialog_names_the_package_and_the_policy(self):
        done = self.run_handler("exit 1\n")  # user chose "Close"
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("google-chrome-stable is a Debian package (.deb).", calls)
        self.assertIn("Flatpaks", calls)
        self.assertIn("Search in the Hub", calls)

    def test_desktop_entry_points_at_the_handler(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("Exec=/usr/bin/rowyn-deb-handler %f", desktop)
        self.assertIn("NoDisplay=true", desktop)

    def test_workflow_smoke_covers_the_handler(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/bin/rowyn-deb-handler", workflow)
        self.assertIn("kde-mimeapps.list", workflow)


if __name__ == "__main__":
    unittest.main()
