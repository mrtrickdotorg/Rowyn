"""Disposable-fixture tests for updater status persistence validation."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from rowyn_fixture_paths import scratch_directory

HELPER = Path(__file__).resolve().parents[2] / "shared/rowyn-update-watcher-status"


class StatusStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = scratch_directory("rowyn-update-status-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "status"
        self.env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(self.root))

    def run_helper(self, *args):
        return subprocess.run([str(HELPER), *map(str, args)], env=self.env, capture_output=True)

    def read(self):
        return json.loads((self.root / "status.json").read_text())

    def test_preserves_success_when_start_overwrites_previous_terminal_record(self):
        self.assertEqual(self.run_helper("start", 100).returncode, 0)
        self.assertEqual(self.run_helper("finish", "current", "no_update", 110).returncode, 0)
        self.assertEqual(self.run_helper("start", 120).returncode, 0)
        d = self.read()
        self.assertEqual(d["last_success_at"], 110)
        self.assertIsNone(d["finished_at"])

    def test_failed_finish_keeps_prior_success(self):
        self.assertEqual(self.run_helper("start", 100).returncode, 0)
        self.assertEqual(self.run_helper("finish", "current", "no_update", 110).returncode, 0)
        self.assertEqual(self.run_helper("start", 120).returncode, 0)
        self.assertEqual(self.run_helper("finish", "failed", "runtime_auth_io", 130).returncode, 0)
        d = self.read()
        self.assertEqual((d["started_at"], d["finished_at"], d["last_success_at"]), (120, 130, 110))

    def test_rejects_malformed_history_without_replacing_file(self):
        self.root.mkdir(parents=True)
        bad = {"schema_version":1,"started_at":100,"finished_at":110,"outcome":"failed","reason_code":"runtime_auth_io","last_success_at":120,"last_staged_at":None}
        p = self.root / "status.json"
        p.write_text(json.dumps(bad)); before = p.read_bytes()
        self.assertNotEqual(self.run_helper("start", 130).returncode, 0)
        self.assertEqual(p.read_bytes(), before)

    def test_rejects_symlink_status(self):
        self.root.mkdir(parents=True)
        target = self.root / "target"
        target.write_text("{}")
        (self.root / "status.json").symlink_to(target)
        self.assertNotEqual(self.run_helper("start", 100).returncode, 0)
        self.assertTrue((self.root / "status.json").is_symlink())

    def test_rejects_overlong_status(self):
        self.root.mkdir(parents=True)
        (self.root / "status.json").write_bytes(b" " * 8193)
        self.assertNotEqual(self.run_helper("start", 100).returncode, 0)

    def test_reason_mismatch_and_invalid_timestamp_are_rejected(self):
        self.assertNotEqual(self.run_helper("finish", "failed", "battery", 100).returncode, 0)
        self.assertNotEqual(self.run_helper("start", True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
