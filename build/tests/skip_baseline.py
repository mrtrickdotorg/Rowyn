#!/usr/bin/env python3
"""Compare the suite's recorded skips against build/tests/skip-baseline.txt.

A skip is only allowed while the reason it reports still applies. Each
baseline entry records that reason verbatim and the guard that makes the skip
legitimate, so a test that skips for a different cause — a fixture that failed
to build, a tool that broke — fails instead of passing as a listed skip, and a
listed test that stops skipping while its guard still holds is reported as
stale instead of living in the file forever.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = HERE / "skip-baseline.txt"
GUARD_KINDS = ("env", "optin", "absent", "tool", "nonroot")


@dataclass(frozen=True)
class Entry:
    test: str
    guards: tuple[str, ...]
    reason: str


def _guard(term: str) -> str:
    kind, _, value = term.partition(":")
    if kind not in GUARD_KINDS:
        raise ValueError(
            f"unknown guard {term!r}; want one of {', '.join(GUARD_KINDS)}"
        )
    if kind != "nonroot" and not value:
        raise ValueError(f"guard {term!r} needs a value")
    return term


def parse_baseline(text: str) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 3:
            raise ValueError(f"line {number}: want TEST<TAB>GUARD<TAB>REASON")
        test, guards, reason = (field.strip() for field in fields)
        if not test or not guards or not reason:
            raise ValueError(f"line {number}: empty field")
        if test in seen:
            raise ValueError(f"line {number}: {test} is listed twice")
        seen.add(test)
        entries.append(
            Entry(
                test,
                tuple(_guard(term.strip()) for term in guards.split(",")),
                reason,
            )
        )
    return entries


def guard_holds(term: str, env, root: Path, which=shutil.which) -> bool:
    """True while `term` still explains the skip.

    Guards are ORed by the caller: the test must skip while one of them holds,
    and must run once none does.
    """
    kind, _, value = term.partition(":")
    if kind == "env":
        return not env.get(value)
    if kind == "optin":
        return env.get(value) != "1"
    if kind == "absent":
        return not (root / value).exists()
    if kind == "tool":
        return which(value) is None
    if kind == "nonroot":
        return os.geteuid() != 0
    raise ValueError(f"unknown guard {term!r}")


def parse_report(text: str) -> dict[str, str]:
    recorded: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        test, _, reason = raw.partition("\t")
        recorded[test.strip()] = reason.strip()
    return recorded


def verify(entries, recorded, env=None, root=ROOT, which=shutil.which) -> list[str]:
    env = os.environ if env is None else env
    by_test = {entry.test: entry for entry in entries}
    problems: list[str] = []
    for test, reason in sorted(recorded.items()):
        entry = by_test.get(test)
        if entry is None:
            problems.append(f"skipped without a baseline entry: {test}: {reason}")
            continue
        if reason != entry.reason:
            problems.append(
                f"skip reason changed for {test}:\n"
                f"    expected: {entry.reason}\n"
                f"    recorded: {reason}"
            )
        if not any(guard_holds(term, env, root, which) for term in entry.guards):
            problems.append(
                f"skipped while none of its guards applies: {test} ({reason})"
            )
    for entry in entries:
        if entry.test in recorded:
            continue
        holds = [term for term in entry.guards if guard_holds(term, env, root, which)]
        if holds:
            problems.append(
                f"stale entry: {entry.test} did not skip while "
                f"{', '.join(holds)} holds"
            )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} REPORT", file=sys.stderr)
        return 2
    try:
        entries = parse_baseline(BASELINE.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"[skips] {BASELINE}: {error}", file=sys.stderr)
        return 2
    recorded = parse_report(Path(argv[1]).read_text(encoding="utf-8"))
    problems = verify(entries, recorded)
    if problems:
        print(
            "[skips] recorded skips do not match build/tests/skip-baseline.txt:",
            file=sys.stderr,
        )
        for problem in problems:
            for line in problem.splitlines():
                print(f"    {line}", file=sys.stderr)
        return 1
    print(f"[skips] {len(recorded)} expected skip(s) match the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
