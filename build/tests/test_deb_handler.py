#!/usr/bin/env python3
"""Coverage for the .deb handler (D3, decided Nov 12, 2026):
installed-apps.sh registers rowyn-deb-handler as the default handler
for Debian packages; the handler inspects with rowyn-deb-inspect,
offers the Flatpak first, stages user apps for the Hub's guarded
Distrobox install, and refuses system packages with the reason."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
POLICY_SCRIPT = ROOT / "shared/installed-apps.sh"
HANDLER = ROOT / "shared/rowyn-deb-handler"
INSPECT = ROOT / "shared/rowyn-deb-inspect"
DESKTOP = ROOT / "shared/rowyn-deb-handler.desktop"
WORKFLOW = ROOT / ".github/workflows/build.yml"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "deb"


def fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


INSPECT_LINES = {
    "google-chrome-stable": """package=google-chrome-stable
version=157.0.8103.78-1
maintainer=Chrome Linux Team <chromium-dev@chromium.org>
description=The web browser from Google
installed_size_kib=462190
file_size_bytes=147497676
signature=none
verdict=user-app
reasons=
notes=it bundles its own updater, which cannot run inside the container; update it by installing a newer .deb
flatpak_hint=
desktop_files=/usr/share/applications/google-chrome.desktop
binaries=
""",
    "code": """package=code
version=1.108.0-1767881962
maintainer=Microsoft Corporation <vscode-linux@microsoft.com>
description=Code editing. Redefined.
installed_size_kib=457535
file_size_bytes=112000000
signature=none
verdict=user-app
reasons=
notes=
flatpak_hint=Visual Studio Code (com.visualstudio.code)
desktop_files=/usr/share/applications/code.desktop
binaries=
""",
    "fonts-fixture": """package=fonts-fixture
version=1.0
maintainer=Example Fonts <fonts@example.com>
description=A font package
installed_size_kib=512
file_size_bytes=50000
signature=none
verdict=user-app
reasons=
notes=
flatpak_hint=
desktop_files=
binaries=
""",
    "virtualbox-dkms": """package=virtualbox-dkms
version=7.2.20-dfsg-2
maintainer=Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
description=x86 virtualization solution - kernel module sources for dkms
installed_size_kib=6902
file_size_bytes=883670
signature=none
verdict=system
reasons=it ships kernel modules, which only the OS image itself can provide; it builds kernel modules with DKMS, which only works in the OS image itself
notes=
flatpak_hint=
desktop_files=
binaries=
""",
}


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
        inspect = self.root / "usr/bin/rowyn-deb-inspect"
        self.assertTrue(inspect.exists())
        self.assertTrue(os.access(inspect, os.X_OK))
        desktop = self.root / "usr/share/applications/rowyn-deb-handler.desktop"
        self.assertIn("MimeType=application/vnd.debian.binary-package;",
                      desktop.read_text(encoding="utf-8"))

    def test_kde_default_association(self):
        mimeapps = (self.root / "etc/xdg/kde-mimeapps.list").read_text(encoding="utf-8")
        self.assertIn("[Default Applications]", mimeapps)
        self.assertIn(
            "application/vnd.debian.binary-package=rowyn-deb-handler.desktop",
            mimeapps)

    def test_policy_records_the_guarded_path(self):
        policy = (self.root / "usr/share/rowyn/installed-apps/policy.conf").read_text(
            encoding="utf-8")
        self.assertIn("DebPackages=flatpak-first+guarded-distrobox", policy)


class DebHandlerBehavior(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.calls = self.dir / "calls.log"
        self.state = self.dir / "state"
        fake(self.bin / "notify-send",
             f'echo "notify $*" >> "{self.calls}"\n')
        fake(self.bin / "rowyn-hub-shell",
             f'echo "hub-shell $*" >> "{self.calls}"\n')
        self.deb = self.dir / "app.deb"
        self.deb.write_bytes(b"!<arch>\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_handler(self, kdialog_exit: int, inspect_fixture: str | None,
                    real_inspect: bool = False) -> subprocess.CompletedProcess:
        fake(self.bin / "kdialog",
             f'echo "kdialog $*" >> "{self.calls}"\nexit {kdialog_exit}\n')
        if real_inspect:
            (self.bin / "rowyn-deb-inspect").symlink_to(INSPECT)
        elif inspect_fixture is not None:
            lines = INSPECT_LINES[inspect_fixture].replace('"', '\\"')
            body = "\n".join(f'echo "{line}"' for line in lines.splitlines())
            fake(self.bin / "rowyn-deb-inspect", body + "\n")
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        env["XDG_STATE_HOME"] = str(self.state)
        env["HOME"] = str(self.dir)
        return subprocess.run(
            ["/bin/sh", str(HANDLER), str(self.deb)],
            text=True, capture_output=True, env=env)

    def test_user_app_dialog_shows_identity_signature_and_routes(self):
        done = self.run_handler(2, "google-chrome-stable")  # Not now
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("google-chrome-stable 157.0.8103.78-1", calls)
        self.assertIn("Chrome Linux Team", calls)
        self.assertIn("no embedded signature", calls)
        self.assertIn("Flatpaks first", calls)
        self.assertIn("Install in app container", calls)
        self.assertIn("rowyn-deb", calls)
        self.assertIn("bundles its own updater", calls)
        self.assertNotIn("hub-shell", calls)

    def test_flatpak_hint_is_named_when_one_exists(self):
        done = self.run_handler(2, "code")
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("Visual Studio Code (com.visualstudio.code)", calls)
        self.assertIn("better-supported route", calls)

    def test_install_choice_stages_the_file_and_opens_the_hub(self):
        done = self.run_handler(0, "google-chrome-stable")
        self.assertEqual(done.returncode, 0, done.stderr)
        pending = self.state / "rowyn" / "deb-pending"
        self.assertTrue(pending.exists())
        self.assertEqual(pending.read_text(encoding="utf-8").strip(),
                         str(self.deb.resolve()))
        self.assertIn("hub-shell --page Apps",
                      self.calls.read_text(encoding="utf-8"))

    def test_search_choice_opens_the_hub_without_staging(self):
        done = self.run_handler(1, "google-chrome-stable")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.state / "rowyn" / "deb-pending").exists())
        self.assertIn("hub-shell --page Apps",
                      self.calls.read_text(encoding="utf-8"))

    def test_packageless_deb_is_explained_without_an_install_offer(self):
        done = self.run_handler(1, "fonts-fixture")  # Close
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("fonts-fixture 1.0", calls)
        self.assertIn("nothing to install", calls)
        self.assertNotIn("Install in app container", calls)
        self.assertNotIn("hub-shell", calls)
        self.assertFalse((self.state / "rowyn" / "deb-pending").exists())

    def test_system_package_is_refused_with_its_reason(self):
        done = self.run_handler(1, "virtualbox-dkms")  # Close
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("cannot be installed on Rowyn", calls)
        self.assertIn("DKMS", calls)
        self.assertNotIn("Install in app container", calls)
        self.assertNotIn("hub-shell", calls)
        self.assertFalse((self.state / "rowyn" / "deb-pending").exists())

    def build_fixture_deb(self, name: str) -> Path:
        fields = {}
        current = None
        for line in (FIXTURES / f"{name}.control").read_text(
                encoding="utf-8").splitlines():
            if line.startswith((" ", "\t")) and current:
                fields[current] += "\n" + line[1:]
            elif ":" in line:
                current, value = line.split(":", 1)
                fields[current] = value.strip()
        tree = self.dir / f"tree-{name}"
        debian = tree / "DEBIAN"
        debian.mkdir(parents=True)
        debian.chmod(0o755)
        control = []
        for key in ("Package", "Version", "Architecture", "Maintainer",
                    "Installed-Size", "Depends", "Section", "Priority"):
            if key in fields:
                control.append(f"{key}: {fields[key]}")
        control.append("Description: " + fields.get(
            "Description", "Fixture.").replace("\n", "\n "))
        (debian / "control").write_text("\n".join(control) + "\n",
                                        encoding="utf-8")
        for entry in (FIXTURES / f"{name}.files").read_text(
                encoding="utf-8").splitlines():
            target = tree / entry.lstrip("/")
            if entry.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture\n")
        deb = self.dir / f"{name}.deb"
        built = subprocess.run(
            ["/usr/bin/dpkg-deb", "--build", "--root-owner-group",
             str(tree), str(deb)], text=True, capture_output=True)
        assert built.returncode == 0, built.stderr
        return deb

    def test_real_inspector_and_real_deb_through_the_handler(self):
        # The production entry path end to end: a real fixture .deb,
        # the real rowyn-deb-inspect, the real handler script.
        self.deb = self.build_fixture_deb("discord")
        done = self.run_handler(0, None, real_inspect=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("discord 0.0.124", calls)
        self.assertIn("Discord (com.discordapp.Discord)", calls)
        pending = self.state / "rowyn" / "deb-pending"
        self.assertEqual(pending.read_text(encoding="utf-8").strip(),
                         str(self.deb.resolve()))

    def test_real_system_deb_is_refused_through_the_handler(self):
        self.deb = self.build_fixture_deb("docker-ce")
        done = self.run_handler(1, None, real_inspect=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn("cannot be installed on Rowyn", calls)
        self.assertIn("system service", calls)
        self.assertNotIn("Install in app container", calls)


class DebHandlerFallback(unittest.TestCase):
    """Without rowyn-deb-inspect the handler keeps the original
    explainer behaviour (older images, unreadable files)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.calls = self.dir / "calls.log"
        fake(self.bin / "dpkg-deb",
             'if [ "$1" = "-f" ]; then echo "google-chrome-stable"; exit 0; fi\nexit 1\n')
        self.deb = self.dir / "chrome.deb"
        self.deb.write_bytes(b"!<arch>\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dialog_names_the_package_and_the_policy(self):
        fake(self.bin / "kdialog",
             f'echo "kdialog $*" >> "{self.calls}"\nexit 1\n')
        fake(self.bin / "notify-send",
             f'echo "notify $*" >> "{self.calls}"\n')
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        done = subprocess.run(
            ["/bin/sh", str(HANDLER), str(self.deb)],
            text=True, capture_output=True, env=env)
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
        self.assertIn("test -x /usr/bin/rowyn-deb-inspect", workflow)
        self.assertIn("kde-mimeapps.list", workflow)


if __name__ == "__main__":
    unittest.main()
