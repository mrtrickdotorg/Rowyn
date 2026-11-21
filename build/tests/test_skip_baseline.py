"""The skip baseline records a reason and a guard, and both are enforced."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import skip_baseline


def entry(test, guards, reason):
    return skip_baseline.Entry(test, tuple(guards), reason)


class BaselineFormat(unittest.TestCase):
    def test_the_repository_baseline_parses_with_a_reason_and_guard_each(self):
        entries = skip_baseline.parse_baseline(
            (ROOT / "build/tests/skip-baseline.txt").read_text(encoding="utf-8"))
        self.assertTrue(entries)
        for listed in entries:
            self.assertTrue(listed.guards, listed.test)
            self.assertTrue(listed.reason, listed.test)

    def test_a_line_without_a_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            skip_baseline.parse_baseline(
                "test_mod.Case.test_one\tenv:ROWYN_TEST_UPSTREAM_REPO\n")

    def test_an_unknown_guard_is_rejected(self):
        with self.assertRaises(ValueError):
            skip_baseline.parse_baseline("test_mod.Case.test_one\talways\treason\n")


class Verify(unittest.TestCase):
    def check(self, entries, recorded, env=None):
        return skip_baseline.verify(entries, recorded, env={} if env is None else env)

    def test_a_listed_skip_is_accepted_while_its_guard_holds(self):
        entries = [entry("t.C.m", ["env:ROWYN_TEST_UPSTREAM_REPO"], "set ROWYN_TEST_UPSTREAM_REPO")]
        self.assertEqual(self.check(entries, {"t.C.m": "set ROWYN_TEST_UPSTREAM_REPO"}), [])

    def test_a_changed_reason_fails_even_though_the_test_is_listed(self):
        # A fixture that fails to build must not pass as the listed skip.
        entries = [entry("t.C.m", ["env:ROWYN_TEST_UPSTREAM_REPO"], "set ROWYN_TEST_UPSTREAM_REPO")]
        problems = self.check(entries, {"t.C.m": "fixture failed to build"})
        self.assertTrue(any("skip reason changed" in p for p in problems), problems)

    def test_a_skip_without_a_baseline_entry_fails(self):
        problems = self.check([], {"t.C.m": "anything"})
        self.assertTrue(any("without a baseline entry" in p for p in problems), problems)

    def test_a_skip_while_no_guard_applies_fails(self):
        # The variable is set, so this cannot be the listed skip.
        entries = [entry("t.C.m", ["env:ROWYN_TEST_UPSTREAM_REPO"], "set ROWYN_TEST_UPSTREAM_REPO")]
        problems = self.check(entries, {"t.C.m": "set ROWYN_TEST_UPSTREAM_REPO"},
                              env={"ROWYN_TEST_UPSTREAM_REPO": "/checkout"})
        self.assertTrue(any("none of its guards applies" in p for p in problems), problems)

    def test_a_listed_test_that_runs_while_its_guard_holds_is_stale(self):
        entries = [entry("t.C.m", ["env:ROWYN_TEST_UPSTREAM_REPO"], "set ROWYN_TEST_UPSTREAM_REPO")]
        problems = self.check(entries, {})
        self.assertTrue(any("stale entry" in p for p in problems), problems)

    def test_a_listed_test_may_run_once_its_prerequisite_is_supplied(self):
        entries = [entry("t.C.m", ["env:ROWYN_TEST_UPSTREAM_REPO"], "set ROWYN_TEST_UPSTREAM_REPO")]
        self.assertEqual(self.check(entries, {}, env={"ROWYN_TEST_UPSTREAM_REPO": "/checkout"}), [])

    def test_an_absent_path_guard_tracks_the_generated_tree(self):
        generated = entry("t.C.m", ["absent:hub-source/nowhere"], "run the port first")
        self.assertEqual(self.check([generated], {"t.C.m": "run the port first"}), [])
        present = entry("t.C.m", ["absent:build"], "run the port first")
        problems = self.check([present], {"t.C.m": "run the port first"})
        self.assertTrue(any("none of its guards applies" in p for p in problems), problems)
        self.assertEqual(self.check([present], {}), [])


if __name__ == "__main__":
    unittest.main()
