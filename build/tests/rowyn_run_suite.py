#!/usr/bin/env python3
"""Run the Python contract suite and record which tests skipped.

`build/validate.sh` compares the recorded skips against
`build/tests/skip-baseline.txt`. A test that stops running because a fixture
or tool is missing must fail validation instead of quietly shrinking
coverage, so the skip set is part of the contract rather than console noise.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIPS: list[str] = []


class RecordingResult(unittest.TextTestResult):
    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        SKIPS.append(test.id())
        super().addSkip(test, reason)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} REPORT", file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.discover(
        str(HERE), pattern="test_*.py", top_level_dir=str(HERE)
    )
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    recorded = sorted(set(SKIPS))
    Path(argv[1]).write_text(
        "".join(f"{test}\n" for test in recorded), encoding="utf-8"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
