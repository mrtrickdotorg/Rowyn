#!/usr/bin/env python3
"""Coverage for rowyn-deb-inspect, the .deb classifier behind the
guarded install path (D3, decided Nov 12, 2026).

Fixtures are production-shaped: the control stanzas and file lists in
fixtures/deb/ were captured from the vendors' own published packages
on 2026-11-12 — google-chrome-stable and steam-launcher verbatim from
the real .deb files, code/docker-ce/virtualbox-dkms verbatim from the
publishers' Packages indices, zoom/discord/tailscale in the same
published layout. The awkward parts are kept: Steam ships host udev
rules and a vendor apt source next to a real app (allowed, with
notes); Chrome bundles a cron updater (allowed, with a note).

Both sides of the decision are pinned: the convert staples classify as
user-app, the DKMS driver package and the system daemons classify as
system with named reasons.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "shared/rowyn-deb-inspect"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "deb"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("rowyn_deb_inspect", str(TOOL))
    spec = importlib.util.spec_from_loader("rowyn_deb_inspect", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL_MODULE = load_tool()


def fixture(name):
    fields = TOOL_MODULE.parse_control(
        (FIXTURES / f"{name}.control").read_text(encoding="utf-8"))
    files = (FIXTURES / f"{name}.files").read_text(encoding="utf-8").splitlines()
    return fields, files


def signals(pairs):
    return [signal for signal, _text in pairs]


class FixtureClassification(unittest.TestCase):
    def classify(self, name):
        fields, files = fixture(name)
        verdict, reasons, notes, desktops, binaries = TOOL_MODULE.classify(
            fields, files)
        return verdict, reasons, notes, desktops, binaries

    def test_chrome_is_a_user_app_with_an_updater_note(self):
        verdict, reasons, notes, desktops, _ = self.classify(
            "google-chrome-stable")
        self.assertEqual(verdict, "user-app")
        self.assertEqual(reasons, [])
        self.assertIn("bundled-updater", signals(notes))
        self.assertIn("/usr/share/applications/google-chrome.desktop", desktops)

    def test_vscode_is_a_user_app_with_a_flatpak_hint(self):
        verdict, reasons, _, desktops, _ = self.classify("code")
        self.assertEqual((verdict, reasons), ("user-app", []))
        self.assertIn("/usr/share/applications/code.desktop", desktops)
        self.assertEqual(
            TOOL_MODULE.FLATPAK_HINTS["code"],
            ("Visual Studio Code", "com.visualstudio.code"))

    def test_zoom_and_discord_are_user_apps(self):
        for name in ("zoom", "discord"):
            verdict, reasons, _, desktops, _ = self.classify(name)
            self.assertEqual((verdict, reasons), ("user-app", []), name)
            self.assertTrue(desktops, name)

    def test_steam_is_a_user_app_despite_host_udev_rules(self):
        # The production shape that must not be refused: the real
        # steam-launcher .deb ships /lib/udev/rules.d entries next to
        # the app. Rules alone are inert in the container; the app is
        # what matters, and the rules become a note.
        verdict, reasons, notes, desktops, _ = self.classify("steam-launcher")
        self.assertEqual((verdict, reasons), ("user-app", []))
        self.assertIn("host-device-rules", signals(notes))
        self.assertIn("vendor-source", signals(notes))
        self.assertIn("/usr/share/applications/steam.desktop", desktops)

    def test_dkms_driver_package_is_refused(self):
        verdict, reasons, _, _, _ = self.classify("virtualbox-dkms")
        self.assertEqual(verdict, "system")
        self.assertIn("kernel-module", signals(reasons))
        self.assertIn("dkms-dependency", signals(reasons))

    def test_docker_daemon_is_refused_as_a_system_service(self):
        verdict, reasons, _, _, _ = self.classify("docker-ce")
        self.assertEqual(verdict, "system")
        self.assertIn("system-service", signals(reasons))

    def test_tailscale_daemon_is_refused_as_a_system_service(self):
        verdict, reasons, _, _, _ = self.classify("tailscale")
        self.assertEqual(verdict, "system")
        self.assertIn("system-service", signals(reasons))

    def test_rules_only_package_is_refused_as_host_configuration(self):
        fields = {"Package": "steam-devices", "Version": "1:1.0",
                  "Maintainer": "Valve Corporation"}
        files = ["/lib/udev/rules.d/60-steam-input.rules",
                 "/lib/udev/hwdb.d/60-steam-input.hwdb"]
        verdict, reasons, _, _, _ = TOOL_MODULE.classify(fields, files)
        self.assertEqual(verdict, "system")
        self.assertIn("host-configuration-only", signals(reasons))

    def test_essential_and_module_packages_are_refused(self):
        fields = {"Package": "linux-image-7.3.0-10-generic",
                  "Version": "7.3.0-10", "Priority": "optional"}
        verdict, reasons, _, _, _ = TOOL_MODULE.classify(
            fields, ["/boot/vmlinuz-7.3.0-10-generic",
                     "/lib/modules/7.3.0-10-generic/kernel/fs/x.ko"])
        self.assertEqual(verdict, "system")
        self.assertIn("kernel-module", signals(reasons))
        self.assertIn("boot-integration", signals(reasons))
        self.assertIn("essential-package", signals(reasons))


def build_deb(directory: Path, name: str) -> Path:
    """Build a real .deb from a fixture pair with dpkg-deb itself."""
    fields, files = fixture(name)
    tree = directory / name
    (tree / "DEBIAN").mkdir(parents=True)
    # dpkg-deb insists on 0755 regardless of the caller's umask.
    (tree / "DEBIAN").chmod(0o755)
    control = []
    for key in ("Package", "Version", "Architecture", "Maintainer",
                "Installed-Size", "Depends", "Pre-Depends", "Section",
                "Priority", "Essential"):
        if key in fields:
            control.append(f"{key}: {fields[key]}")
    description = fields.get("Description", "Fixture package.")
    control.append("Description: " + description.replace("\n", "\n "))
    (tree / "DEBIAN" / "control").write_text(
        "\n".join(control) + "\n", encoding="utf-8")
    for entry in files:
        path = tree / entry.lstrip("/")
        if entry.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture\n")
    deb = directory / f"{name}.deb"
    done = subprocess.run(
        ["/usr/bin/dpkg-deb", "--build", "--root-owner-group", str(tree),
         str(deb)],
        text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
    return deb


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb unavailable")
class InspectToolEntryPath(unittest.TestCase):
    """The tool itself, run the way the handler and the Hub run it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def inspect(self, name):
        deb = build_deb(self.dir, name)
        done = subprocess.run(
            [str(TOOL), str(deb)], text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_real_deb_shapes_end_to_end(self):
        chrome = self.inspect("google-chrome-stable")
        self.assertEqual(chrome["package"], "google-chrome-stable")
        self.assertEqual(chrome["verdict"], "user-app")
        self.assertEqual(chrome["signature"], "none")
        self.assertGreater(chrome["file_size_bytes"], 0)
        self.assertEqual(len(chrome["sha256"]), 64)

        steam = self.inspect("steam-launcher")
        self.assertEqual(steam["verdict"], "user-app")
        self.assertEqual(steam["flatpak_hint"]["id"],
                         "com.valvesoftware.Steam")

        dkms = self.inspect("virtualbox-dkms")
        self.assertEqual(dkms["verdict"], "system")
        self.assertIn("dkms-dependency",
                      [reason["signal"] for reason in dkms["reasons"]])

        docker = self.inspect("docker-ce")
        self.assertEqual(docker["verdict"], "system")

    def test_lines_format_for_the_shell_handler(self):
        deb = build_deb(self.dir, "discord")
        done = subprocess.run(
            [str(TOOL), "--format", "lines", str(deb)],
            text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        pairs = dict(
            line.split("=", 1) for line in done.stdout.splitlines())
        self.assertEqual(pairs["package"], "discord")
        self.assertEqual(pairs["verdict"], "user-app")
        self.assertEqual(pairs["flatpak_hint"],
                         "Discord (com.discordapp.Discord)")

    def test_invalid_file_is_a_clean_error(self):
        bogus = self.dir / "bogus.deb"
        bogus.write_bytes(b"not a deb")
        done = subprocess.run(
            [str(TOOL), str(bogus)], text=True, capture_output=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("rowyn-deb-inspect:", done.stderr)


if __name__ == "__main__":
    unittest.main()
