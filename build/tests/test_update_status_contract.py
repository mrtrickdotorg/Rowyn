#!/usr/bin/env python3
"""Writer-to-reader contract for the Rowyn watcher status record.

The Hub's passive update status projects the record that
rowyn-update-watcher-status writes to
/var/lib/rowyn/update-watcher/status.json (see apply_rowyn_update_status
in build/ubuntu-hub-source.py). The bug this locks against: the port
originally left the Hub reading the upstream watcher's snapshot file —
a path and schema nothing on a Rowyn system ever writes — so the Home
card showed "Update status unavailable / No recent update check."
permanently while the watcher ran fine.

These tests pin the two ends together:
- the real writer's byte shape (compact JSON, fixed key order, one
  trailing newline), which the generated Rust reader's fixtures mirror;
- the generator's projection for every outcome the writer can emit;
- (gated) the generated reader itself, after a real port.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/rowyn-update-watcher-status"
GENERATOR = ROOT / "build/ubuntu-hub-source.py"
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
EXPECTED_KEYS = [
    "schema_version", "started_at", "finished_at", "outcome",
    "reason_code", "last_success_at", "last_staged_at",
]


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WriterShapeTests(unittest.TestCase):
    """The writer's emitted documents keep the shape the reader pins."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rowyn-status-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "status"
        self.env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(self.root))

    def run_helper(self, *args):
        result = subprocess.run(
            [str(HELPER), *map(str, args)], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def raw(self):
        return (self.root / "status.json").read_bytes()

    def assert_canonical_shape(self, doc):
        self.assertEqual(list(doc.keys()), EXPECTED_KEYS)
        canonical = json.dumps(doc, separators=(",", ":")) + "\n"
        self.assertEqual(self.raw().decode(), canonical)

    def test_current_finish_shape(self):
        self.run_helper("start", 1762992000)
        self.run_helper("finish", "current", "no_update", 1762992300)
        doc = json.loads(self.raw())
        self.assertEqual(doc, {
            "schema_version": 1, "started_at": 1762992000,
            "finished_at": 1762992300, "outcome": "current",
            "reason_code": "no_update", "last_success_at": 1762992300,
            "last_staged_at": None})
        self.assert_canonical_shape(doc)

    def test_staged_finish_shape(self):
        self.run_helper("start", 1762992000)
        self.run_helper("finish", "staged", "update_staged", 1762992300)
        doc = json.loads(self.raw())
        self.assertEqual(doc["last_success_at"], 1762992300)
        self.assertEqual(doc["last_staged_at"], 1762992300)
        self.assert_canonical_shape(doc)

    def test_deferred_finish_carries_prior_success(self):
        self.run_helper("start", 1762991000)
        self.run_helper("finish", "current", "no_update", 1762991500)
        self.run_helper("start", 1762992000)
        self.run_helper("finish", "deferred", "battery", 1762992100)
        doc = json.loads(self.raw())
        self.assertEqual(doc["outcome"], "deferred")
        self.assertEqual(doc["last_success_at"], 1762991500)
        self.assertLessEqual(doc["last_success_at"], doc["started_at"])
        self.assert_canonical_shape(doc)

    def test_failed_and_disabled_finish_shapes(self):
        self.run_helper("start", 1762992000)
        self.run_helper("finish", "failed", "upgrade_failed", 1762992100)
        doc = json.loads(self.raw())
        self.assertIsNone(doc["last_success_at"])
        self.assert_canonical_shape(doc)
        self.run_helper("start", 1762993000)
        self.run_helper("finish", "disabled", "policy_disabled", 1762993001)
        doc = json.loads(self.raw())
        self.assertEqual(doc["outcome"], "disabled")
        self.assert_canonical_shape(doc)


class GeneratorProjectionTests(unittest.TestCase):
    """The port's reader projection covers every writer outcome."""

    @classmethod
    def setUpClass(cls):
        cls.script = GENERATOR.read_text()

    def test_reader_targets_the_rowyn_record(self):
        self.assertIn("apply_rowyn_update_status", self.script)
        self.assertIn(
            'ROWYN_WATCHER_STATUS_PATH: &str = '
            '"/var/lib/rowyn/update-watcher/status.json"',
            self.script)
        self.assertIn("read_rowyn_watcher_status()", self.script)

    def test_every_outcome_projects(self):
        for outcome in ("current", "staged", "running", "deferred",
                        "failed", "disabled", "invalid_policy",
                        "interrupted"):
            self.assertIn(f'"{outcome}" =>', self.script, outcome)
        for state in ("uptodate", "available", "busy", "deferred",
                      "disabled", "blocked", "error"):
            self.assertIn(f'"{state}"', self.script, state)

    def test_up_to_date_detail_carries_the_check_time(self):
        self.assertIn('format!("Last checked {}.', self.script)

    def test_banner_source_states_include_deferred_and_disabled(self):
        self.assertIn("'deferred', 'disabled'", self.script)


def _pinned_repo():
    repo = os.environ.get("ROWYN_TEST_UPSTREAM_REPO")
    if not repo:
        return None
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", PIN + "^{commit}"],
        text=True, capture_output=True)
    if result.returncode != 0 or result.stdout.strip() != PIN:
        return None
    return Path(repo)


@unittest.skipUnless(
    _pinned_repo(),
    "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit")
class PinnedReaderTests(unittest.TestCase):
    """After a real port, the generated reader consumes the record."""

    def test_generated_reader_projects_rowyn_record(self):
        hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-status-") as temp:
            root = Path(temp) / "tree"
            hub.export_source(repo, root)
            hub.port_source(root)
            status = (root / "src/rowyn-shared-rs/src/system"
                      / "update_status.rs").read_text()
            self.assertIn(
                'pub const ROWYN_WATCHER_STATUS_PATH: &str = '
                '"/var/lib/rowyn/update-watcher/status.json";', status)
            self.assertIn("read_rowyn_watcher_status()", status)
            self.assertIn("rowyn_watcher_status(data, &record)", status)
            self.assertIn("current_record_projects_uptodate_with_real_check_time",
                          status)
            self.assertIn("Last checked 2025-11-13 00:05 UTC.", status)
            banner = (root / "src/rowyn-hub-web/src/components"
                      / "UpdateStatusBanner.tsx").read_text()
            self.assertIn("'deferred', 'disabled'", banner)


if __name__ == "__main__":
    unittest.main()
