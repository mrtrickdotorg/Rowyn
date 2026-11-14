#!/usr/bin/env python3
"""Coverage for rowyn-performance-record, the baseline harness producer.

The Hub's Diagnostics projections (boot time, suspend ratio) read the
history file this producer writes; these tests pin the writer's byte
shape and parsing so the two sides cannot drift — the same discipline
as test_update_status_contract. systemd-analyze fixtures are verbatim
shapes (a full UEFI boot, a VM boot with userspace only, minute-scale
durations) so the parser is tested against what systemd really prints.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/rowyn-performance-record"
SERVICE = ROOT / "shared/rowyn-performance.service"
TIMER = ROOT / "shared/rowyn-performance.timer"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

ANALYZE_FULL = (
    "Startup finished in 2.847s (firmware) + 1.203s (loader) + 3.112s (kernel)"
    " + 1.456s (initrd) + 8.923s (userspace) = 17.541s\n"
    "graphical.target reached after 8.923s in userspace.\n"
)
ANALYZE_VM = (
    "Startup finished in 2.132s (userspace) \n"
    "default.target reached after 197ms in userspace.\n"
)
ANALYZE_MINUTES = (
    "Startup finished in 1min 2.345s (kernel) + 823ms (initrd)"
    " + 45.678s (userspace) = 1min 48.846s\n"
    "graphical.target reached after 45.678s in userspace.\n"
)


class PerformanceRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.dir = self.base / "performance"
        self.analyze = self.base / "analyze.txt"
        self.boot_id_file = self.base / "boot_id"
        self.sysfs = self.base / "suspend_stats"
        self.sysfs.mkdir()
        (self.sysfs / "success").write_text("5\n")
        (self.sysfs / "fail").write_text("2\n")
        self.boot_id_file.write_text("boot-aaa\n")
        self.analyze.write_text(ANALYZE_FULL)

    def run_helper(self, now="1763131800", analyze=ANALYZE_FULL, **extra):
        self.analyze.write_text(analyze)
        env = dict(
            os.environ,
            ROWYN_PERFORMANCE_DIR=str(self.dir),
            ROWYN_PERFORMANCE_NOW=now,
            ROWYN_PERFORMANCE_ANALYZE_FILE=str(self.analyze),
            ROWYN_PERFORMANCE_BOOT_ID_FILE=str(self.boot_id_file),
            ROWYN_PERFORMANCE_SYSFS=str(self.sysfs),
        )
        env.update(extra)
        return subprocess.run(
            ["/usr/bin/python3", str(HELPER)],
            text=True, capture_output=True, env=env,
        )

    def history(self):
        return json.loads((self.dir / "history.json").read_text())

    def test_records_full_boot_and_suspend(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = self.history()
        self.assertEqual(list(doc), ["schema_version", "boots"])
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(len(doc["boots"]), 1)
        entry = doc["boots"][0]
        self.assertEqual(list(entry), ["boot_id", "sampled_at", "boot", "suspend"])
        self.assertEqual(entry["boot_id"], "boot-aaa")
        self.assertEqual(entry["sampled_at"], 1763131800)
        self.assertEqual(entry["boot"], {
            "firmware": 2.847, "loader": 1.203, "kernel": 3.112,
            "initrd": 1.456, "userspace": 8.923, "total": 17.541,
            "target": "graphical", "target_seconds": 8.923,
        })
        self.assertEqual(entry["suspend"], {"success": 5, "fail": 2})
        text = (self.dir / "history.json").read_text()
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn('", "', text)  # compact separators, like the watcher store

    def test_userspace_only_boot_reports_no_invented_segments(self):
        result = self.run_helper(analyze=ANALYZE_VM)
        self.assertEqual(result.returncode, 0, result.stderr)
        boot = self.history()["boots"][0]["boot"]
        self.assertEqual(boot, {
            "userspace": 2.132, "total": 2.132,
            "target": "default", "target_seconds": 0.197,
        })

    def test_minute_and_millisecond_durations(self):
        result = self.run_helper(analyze=ANALYZE_MINUTES)
        self.assertEqual(result.returncode, 0, result.stderr)
        boot = self.history()["boots"][0]["boot"]
        self.assertEqual(boot["kernel"], 62.345)
        self.assertEqual(boot["initrd"], 0.823)
        self.assertEqual(boot["total"], 108.846)

    def test_unparseable_analyze_and_missing_sysfs_stay_null(self):
        (self.sysfs / "success").unlink()
        (self.sysfs / "fail").unlink()
        result = self.run_helper(analyze="not systemd output\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.history()["boots"][0]
        self.assertIsNone(entry["boot"])
        self.assertIsNone(entry["suspend"])

    def test_upsert_keeps_one_entry_per_boot(self):
        self.assertEqual(self.run_helper(now="100").returncode, 0)
        self.assertEqual(self.run_helper(now="200").returncode, 0)
        boots = self.history()["boots"]
        self.assertEqual(len(boots), 1)
        self.assertEqual(boots[0]["sampled_at"], 200)
        self.boot_id_file.write_text("boot-bbb\n")
        self.assertEqual(self.run_helper(now="300").returncode, 0)
        boots = self.history()["boots"]
        self.assertEqual([b["boot_id"] for b in boots], ["boot-bbb", "boot-aaa"])

    def test_history_is_capped_at_thirty_boots(self):
        seeded = {"schema_version": 1, "boots": [
            {"boot_id": f"boot-{i:02d}", "sampled_at": 1000 + i,
             "boot": None, "suspend": None} for i in range(30)
        ]}
        self.dir.mkdir(parents=True)
        (self.dir / "history.json").write_text(json.dumps(seeded))
        self.assertEqual(self.run_helper(now="5000").returncode, 0)
        boots = self.history()["boots"]
        self.assertEqual(len(boots), 30)
        self.assertEqual(boots[0]["boot_id"], "boot-aaa")
        self.assertNotIn("boot-00", [b["boot_id"] for b in boots])

    def test_corrupt_history_is_replaced_and_bad_entries_dropped(self):
        self.dir.mkdir(parents=True)
        (self.dir / "history.json").write_text("not json at all")
        self.assertEqual(self.run_helper(now="5000").returncode, 0)
        self.assertEqual(len(self.history()["boots"]), 1)

        seeded = {"schema_version": 1, "boots": [
            {"boot_id": "good", "sampled_at": 10, "boot": None,
             "suspend": {"success": 1, "fail": 0}},
            {"boot_id": "bad", "sampled_at": 20, "boot": None,
             "suspend": {"success": -1, "fail": 0}},
        ]}
        (self.dir / "history.json").write_text(json.dumps(seeded))
        self.assertEqual(self.run_helper(now="5001").returncode, 0)
        ids = [b["boot_id"] for b in self.history()["boots"]]
        self.assertEqual(ids, ["boot-aaa", "good"])

    def test_usage_and_clock_validation(self):
        result = subprocess.run(
            ["/usr/bin/python3", str(HELPER), "extra"],
            text=True, capture_output=True,
            env=dict(os.environ, ROWYN_PERFORMANCE_DIR=str(self.dir)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.run_helper(now="not-a-number").returncode, 1)

    def test_units_and_payload_wiring(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/libexec/rowyn-performance-record", service)
        timer = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnBootSec=2min", timer)
        self.assertIn("OnUnitActiveSec=15min", timer)
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        for needle in (
            "install -Dm755 /ctx/shared/rowyn-performance-record /usr/libexec/rowyn-performance-record",
            "rowyn-performance.service",
            "rowyn-performance.timer",
            "systemctl enable rowyn-performance.timer",
        ):
            self.assertIn(needle, containerfile)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/libexec/rowyn-performance-record", workflow)
        self.assertIn("timers.target.wants/rowyn-performance.timer", workflow)


if __name__ == "__main__":
    unittest.main()
