#!/usr/bin/env python3
"""Coverage for Rowyn's desktop tuning helper and its wiring."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/desktop-tuning.sh"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

RULES = "usr/lib/udev/rules.d/60-rowyn-ioschedulers.rules"
JOURNALD = "usr/lib/systemd/journald.conf.d/90-rowyn.conf"
BALOO = "etc/xdg/baloofilerc"
AUDIO = "usr/libexec/rowyn-gamemode-audio"
GAMEMODE = "etc/gamemode.ini"


def ini(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            sections[current] = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            sections[current][key.strip()] = value.strip()
    return sections


class DesktopTuningTests(unittest.TestCase):
    def run_helper(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root), *args],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def snapshot(self, root: Path) -> dict[str, tuple[str, int]]:
        return {
            str(p.relative_to(root)): (p.read_text(encoding="utf-8"), p.stat().st_mode)
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    def test_helper_is_executable_strict_and_valid_shell(self):
        self.assertTrue(os.access(HELPER, os.X_OK))
        self.assertIn("set -euo pipefail", HELPER.read_text(encoding="utf-8"))
        self.assertEqual(subprocess.run(["bash", "-n", str(HELPER)]).returncode, 0)

    def test_rejects_unknown_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_helper(Path(tmp), "--bogus").returncode, 2)

    def test_io_scheduler_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            rules = (root / RULES).read_text()
            lines = [l for l in rules.splitlines() if l and not l.startswith("#")]
            self.assertIn(
                'ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"', lines)
            self.assertIn(
                'ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"', lines)
            # Only whole sd* disks are eligible; everything else jumps to the end.
            self.assertIn('ENV{DEVTYPE}!="disk", GOTO="rowyn_ioscheduler_end"', lines)
            self.assertRegex(rules, r'KERNEL!="sd\[a-z\]\|sd\[a-z\]\[a-z\]", GOTO="rowyn_ioscheduler_end"')
            self.assertIn('LABEL="rowyn_ioscheduler_end"', lines)
            # Exactly two assignments, so nvme/loop/ram/zram/dm/vd are never set.
            assigns = [l for l in lines if 'queue/scheduler}="' in l]
            self.assertEqual(len(assigns), 2)
            for device in ("nvme", "loop", "ram", "zram", "dm-", "vd"):
                self.assertNotRegex("\n".join(assigns), device)
            # The assignments must come after the sd-only guard.
            self.assertLess(rules.index('KERNEL!="sd'), rules.index("queue/scheduler"))
            # Every GOTO has a matching LABEL.
            for target in re.findall(r'GOTO="([^"]+)"', rules):
                self.assertIn(f'LABEL="{target}"', rules)

    def test_journald_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            self.assertEqual(ini(root / JOURNALD), {"Journal": {
                "SystemMaxUse": "200M",
                "SystemMaxFileSize": "32M",
                "RuntimeMaxUse": "64M",
                "MaxRetentionSec": "2week",
                "ForwardToSyslog": "no",
            }})

    def test_baloo_filename_only_in_xdg_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            parsed = ini(root / BALOO)
            # Filename search on, content extraction off: the extractor
            # is Baloo's expensive half. "only basic indexing" is the
            # [General] key Baloo's own config writer emits.
            self.assertEqual(parsed["Basic Settings"], {"Indexing-Enabled": "true"})
            self.assertEqual(parsed["General"]["only basic indexing"], "true")
            excludes = parsed["General"]["exclude folders[$e]"].split(",")
            for folder in ("$HOME/snap", "$HOME/VirtualBox VMs", "$HOME/VMs",
                           "$HOME/.local/share/containers"):
                self.assertIn(folder, excludes)
            self.assertFalse((root / "etc/skel").exists())

    def test_background_reindexers_are_deprioritized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            for unit in ("plocate-updatedb.service", "man-db.service"):
                path = root / f"usr/lib/systemd/system/{unit}.d/90-rowyn.conf"
                self.assertEqual(ini(path), {"Service": {
                    "Nice": "10",
                    "IOSchedulingClass": "idle",
                    "CPUWeight": "25",
                    "IOWeight": "25",
                }}, unit)

    def test_gamemode_audio_helper_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            script = root / AUDIO
            self.assertTrue(os.access(script, os.X_OK))
            text = script.read_text()
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertIn("pw-metadata -n settings 0 clock.force-quantum", text)
            self.assertIn("quantum=256", text)
            self.assertIn("quantum=0", text)
            self.assertEqual(subprocess.run(["sh", "-n", str(script)]).returncode, 0)
            # No pw-metadata on PATH: must still exit 0 silently.
            for arg in ("start", "end", "bogus", ""):
                result = subprocess.run(
                    ["/bin/sh", str(script), arg], text=True, capture_output=True,
                    env={"PATH": "/nonexistent"})
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, "")
            # Fake pw-metadata records the arguments.
            bindir = root / "fakebin"
            bindir.mkdir()
            log = root / "calls"
            fake = bindir / "pw-metadata"
            fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexit 1\n')
            fake.chmod(0o755)
            for arg in ("start", "end"):
                result = subprocess.run(
                    ["/bin/sh", str(script), arg], capture_output=True,
                    env={"PATH": f"{bindir}:/usr/bin:/bin"})
                self.assertEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines(), [
                "-n settings 0 clock.force-quantum 256",
                "-n settings 0 clock.force-quantum 0",
            ])
            # Only the [custom] hooks: the package defaults must not be clobbered.
            # Repeated keys are appended by GameMode, so check the raw lines;
            # the audio hook stays first (test_system_defaults covers the rest).
            lines = [l for l in (root / GAMEMODE).read_text().splitlines()
                     if l and not l.startswith("#")]
            self.assertEqual(lines[2:5], [
                "[custom]",
                "start=/usr/libexec/rowyn-gamemode-audio start",
                "end=/usr/libexec/rowyn-gamemode-audio end",
            ])

    def test_files_are_commented_and_modes_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            snap = self.snapshot(root)
            # Files added later are covered by test_system_defaults.
            self.assertTrue({RULES, JOURNALD, BALOO, AUDIO, GAMEMODE} <= set(snap))
            for path, (text, mode) in snap.items():
                self.assertEqual(
                    mode & 0o777,
                    0o755 if path.startswith("usr/libexec/") else 0o644, path)
                self.assertTrue(any(l.startswith("#") for l in text.splitlines()), path)
            self.assertFalse(any(p.startswith("var/") for p in snap))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            first = self.snapshot(root)
            self.assertEqual(self.run_helper(root).returncode, 0)
            self.assertEqual(self.snapshot(root), first)


class DesktopTuningWiringTests(unittest.TestCase):
    def test_containerfile_calls_helper_after_performance_defaults(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        call = "/ctx/shared/desktop-tuning.sh --root /"
        self.assertIn(call, text)
        self.assertGreater(text.index(call),
                           text.index("/ctx/shared/performance-defaults.sh --root /"))
        run_block = text[:text.index(call)].rsplit("\nRUN", 1)[1]
        self.assertIn("--mount=type=bind,from=ctx", run_block)

    def test_containerfile_disables_wait_online_with_networkmanager_enabled(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("systemctl disable NetworkManager-wait-online.service", text)
        enables = [e for e in re.findall(r"systemctl enable ([^&]+)&&", text)
                   if "sddm.service" in e]
        self.assertIn("NetworkManager.service", enables[0].replace("\\", " ").split())
        self.assertNotIn("NetworkManager-wait-online", enables[0])

    def test_pipewire_provides_pw_metadata(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertRegex(text, r"\bpipewire\b")  # pipewire depends on pipewire-bin
        self.assertRegex(text, r"\bgamemode\b")

    def test_workflow_smoke_checks(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for needle in (
            "/usr/lib/udev/rules.d/60-rowyn-ioschedulers.rules",
            "/usr/lib/systemd/journald.conf.d/90-rowyn.conf",
            "/etc/xdg/baloofilerc",
            "network-online.target.wants/NetworkManager-wait-online.service",
            "/usr/libexec/rowyn-gamemode-audio",
            "/etc/gamemode.ini",
            "/usr/bin/pw-metadata",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
