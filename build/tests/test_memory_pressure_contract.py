#!/usr/bin/env python3
"""The memory-pressure agreement: zram, sysctls and systemd-oomd as one
policy, not three independent knobs.

Rowyn's swappiness (180) is only sane because two other pieces ship
with it: zram (swapping goes to compressed RAM, not disk) and
systemd-oomd (a PSI-driven killer that sheds the worst user cgroup
before pressure becomes a freeze). This contract runs both tuning
helpers and fails if any leg of that triangle changes without the
others — the failure mode it guards is a future edit that keeps the
aggressive swappiness while dropping oomd or the zram config.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PERF = ROOT / "shared/performance-defaults.sh"
DESKTOP = ROOT / "shared/desktop-tuning.sh"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

ZRAM = "usr/lib/systemd/zram-generator.conf"
SYSCTL = "usr/lib/sysctl.d/90-rowyn-performance.conf"
OOMD = "usr/lib/systemd/oomd.conf.d/90-rowyn.conf"
USER_SLICE = "usr/lib/systemd/system/user.slice.d/90-rowyn-oomd.conf"
USER_AT = "usr/lib/systemd/system/user@.service.d/90-rowyn-oomd.conf"


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
            sections.setdefault(current, {})[key.strip()] = value.strip()
    return sections


def install_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"apt-get install -y\b(.*?)&&", text, re.S):
        tokens.update(match.group(1).replace("\\", " ").split())
    return tokens


class MemoryPressureContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for helper in (PERF, DESKTOP):
            result = subprocess.run(
                ["/bin/bash", str(helper), "--root", str(self.root)],
                text=True, capture_output=True,
                env=dict(os.environ, PATH="/usr/bin:/bin"))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_zram_is_compressed_ram_at_top_priority(self):
        zram = ini(self.root / ZRAM)["zram0"]
        self.assertEqual(zram["zram-size"], "min(ram / 2, 8192)")
        self.assertEqual(zram["compression-algorithm"], "zstd")
        self.assertEqual(zram["swap-priority"], "100")

    def test_sysctl_pair_matches_zram(self):
        sysctls = ini(self.root / SYSCTL)
        flat = {k: v for section in sysctls.values() for k, v in section.items()}
        # Swapping must prefer the RAM device and never read ahead on it.
        self.assertEqual(flat["vm.page-cluster"], "0")
        self.assertGreater(int(flat["vm.swappiness"]), 100)

    def test_oomd_policy_matches_the_slice_dropins(self):
        oomd = ini(self.root / OOMD)["OOM"]
        self.assertEqual(oomd["SwapUsedLimit"], "85%")
        global_limit = oomd["DefaultMemoryPressureLimit"]
        self.assertEqual(oomd["DefaultMemoryPressureDurationSec"], "40s")
        user_slice = ini(self.root / USER_SLICE)["Slice"]
        self.assertEqual(user_slice["ManagedOOMSwap"], "kill")
        self.assertEqual(user_slice["ManagedOOMMemoryPressure"], "kill")
        # The three limits must move together: a slice limit below the
        # global default would reintroduce the game-loading kills the
        # raised threshold exists to prevent.
        self.assertEqual(user_slice["ManagedOOMMemoryPressureLimit"], global_limit)
        user_at = ini(self.root / USER_AT)["Service"]
        self.assertEqual(user_at["ManagedOOMMemoryPressureLimit"], global_limit)

    def test_aggressive_swappiness_never_ships_without_oomd(self):
        sysctls = ini(self.root / SYSCTL)
        flat = {k: v for section in sysctls.values() for k, v in section.items()}
        self.assertGreater(int(flat["vm.swappiness"]), 100)
        # ...so the image must carry oomd, enable it, and carry the
        # zram generator that makes the swap target exist.
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        tokens = install_tokens(containerfile)
        self.assertIn("systemd-oomd", tokens)
        self.assertIn("systemd-zram-generator", tokens)
        self.assertRegex(containerfile, r"systemctl enable [^\n]*systemd-oomd\.service")
        self.assertIn("performance-defaults.sh --root /", containerfile)
        self.assertIn("desktop-tuning.sh --root /", containerfile)

    def test_workflow_smoke_covers_both_binaries(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/lib/systemd/system-generators/zram-generator", workflow)
        self.assertIn("test -x /usr/lib/systemd/systemd-oomd", workflow)


if __name__ == "__main__":
    unittest.main()
