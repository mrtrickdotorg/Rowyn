#!/usr/bin/env python3
"""Coverage for the telemetry writer: rowyn-telemetry-record inserts rows
the Hub reader's exact SELECT can read back (extended schema), and
rowyn-game-launch records sessions without disturbing exit codes."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RECORDER = ROOT / "shared/rowyn-telemetry-record"
GAMING = ROOT / "shared/gaming-defaults.sh"

# The reader's extended-schema query, copied from system/telemetry.rs.
READER_SQL = (
    "SELECT game_name, started_at, duration_s, avg_fps, p1_low_fps,"
    " stutter_count, scheduler, avg_latency_ms, p99_latency_ms"
    " FROM sessions ORDER BY started_at DESC LIMIT ?"
)


class TelemetryRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "telemetry.db"
        self.env = dict(os.environ, ROWYN_TELEMETRY_DB=str(self.db))

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(RECORDER), *args], text=True, capture_output=True, env=self.env)

    def test_insert_reads_back_through_the_reader_query(self):
        done = self.record("portal2", "1762300000", "1830")
        self.assertEqual(done.returncode, 0, done.stderr)
        connection = sqlite3.connect(self.db)
        rows = connection.execute(READER_SQL, (10,)).fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        (game, started, duration, avg_fps, p1, stutter, scheduler,
         avg_latency, p99) = rows[0]
        self.assertEqual(game, "portal2")
        self.assertEqual(started, 1762300000.0)
        self.assertEqual(duration, 1830.0)
        # Unmeasured figures stay NULL rather than invented.
        self.assertIsNone(avg_fps)
        self.assertIsNone(p1)
        self.assertIsNone(avg_latency)
        self.assertIsNone(p99)
        self.assertEqual(stutter, 0)
        self.assertTrue(scheduler)

    def test_sessions_accumulate_newest_first(self):
        self.record("a-game", "1000", "10")
        self.record("b-game", "2000", "20")
        connection = sqlite3.connect(self.db)
        names = [row[0] for row in connection.execute(READER_SQL, (10,))]
        connection.close()
        self.assertEqual(names, ["b-game", "a-game"])

    def test_bad_input_is_rejected_without_a_row(self):
        for args in (("", "1", "1"), ("game", "0", "1"), ("game", "1", "-5"),
                     ("game", "soon", "1"), ("game",)):
            done = self.record(*args)
            self.assertNotEqual(done.returncode, 0, args)
        self.assertFalse(self.db.exists())


class GameLaunchRecording(unittest.TestCase):
    def test_wrapper_records_and_preserves_the_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            subprocess.run([str(GAMING), "--root", str(root)], check=True)
            launcher = root / "usr/bin/rowyn-game-launch"
            text = launcher.read_text(encoding="utf-8")
            self.assertIn("rowyn-telemetry-record", text)
            self.assertIn("exit $status", text)
            # A failing game keeps its own exit code through the wrapper.
            done = subprocess.run(
                [str(launcher), "sh", "-c", "exit 7"],
                capture_output=True, text=True,
                env=dict(os.environ, PATH=os.environ["PATH"]))
            self.assertEqual(done.returncode, 7)


if __name__ == "__main__":
    unittest.main()
