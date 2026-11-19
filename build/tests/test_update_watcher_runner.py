import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "shared/rowyn-update-watcher-run"

class RunnerTests(unittest.TestCase):
    def run_fake(self, script):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "fake"
            p.write_text("#!/bin/sh\n" + script)
            p.chmod(0o755)
            return subprocess.run([str(RUNNER), str(p)], text=True, capture_output=True, timeout=10)

    def test_only_fixed_machine_result(self):
        r = self.run_fake("echo secret-from-child >&2\necho 'ROWYN_UPDATE_RESULT outcome=staged reason=update_staged'\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "ROWYN_UPDATE_RESULT outcome=staged reason=update_staged")
        self.assertNotIn("secret", r.stdout + r.stderr)

    def test_metered_cap_stage_is_a_fixed_machine_result(self):
        r = self.run_fake("echo 'ROWYN_UPDATE_RESULT outcome=staged reason=metered_cap'\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "ROWYN_UPDATE_RESULT outcome=staged reason=metered_cap")

    def test_nonzero_cannot_claim_staged(self):
        r = self.run_fake("echo 'ROWYN_UPDATE_RESULT outcome=staged reason=update_staged'\nexit 7\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outcome=failed reason=upgrade_failed", r.stdout)

    def test_nonzero_preserves_one_recognized_failure_classification(self):
        r = self.run_fake("echo 'ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io'\nexit 1\n")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io")

    def test_duplicate_failure_markers_fail_closed(self):
        r = self.run_fake("echo 'ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io'; echo 'ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io'; exit 1\n")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "ROWYN_UPDATE_RESULT outcome=failed reason=upgrade_failed")

    def test_unknown_and_contradictory_markers_fail_closed(self):
        for body in ("echo other", "echo 'ROWYN_UPDATE_RESULT outcome=current reason=no_update'; echo 'ROWYN_UPDATE_RESULT outcome=staged reason=update_staged'", "echo 'ROWYN_UPDATE_RESULT outcome=current reason=no_update'; echo 'ROWYN_UPDATE_RESULT malformed'"):
            r = self.run_fake(body + "\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("outcome=failed reason=unknown_failure", r.stdout)

if __name__ == "__main__":
    unittest.main()
