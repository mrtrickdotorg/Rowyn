"""Fixture roots that satisfy Rowyn's privileged-writer path policy.

``rowyn-update-watcher-status`` and the Hub's status reader refuse a path
whose ancestors are not root- or user-owned, or are group/world-writable
unless the directory is root-owned and sticky. ``/tmp`` normally satisfies
that, but not every host is normal: a sandbox can own ``/tmp`` as another
uid, and a ``umask 002`` checkout can sit under a group-writable parent.
Tests that only need a scratch directory must not fail for that reason, so
the fixture uses a directory that passes the same check and skips with a
message naming the host condition when none exists.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path


def compliant(path: Path) -> bool:
    """True when ``path`` and every ancestor pass the writer's policy."""
    euid = os.geteuid()
    for current in (path, *path.parents):
        try:
            info = current.stat()
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode):
            return False
        if info.st_uid not in (0, euid):
            return False
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if info.st_mode & 0o022 and not sticky_root:
            return False
    return True


def _candidates() -> list[Path]:
    candidates: list[Path] = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates.append(Path(runtime))
    candidates.append(Path("/run/user") / str(os.geteuid()))
    candidates.append(Path(tempfile.gettempdir()))
    candidates.append(Path("/dev/shm"))
    candidates.append(Path("/var/tmp"))
    return candidates


def scratch_directory(prefix: str) -> tempfile.TemporaryDirectory:
    """A ``TemporaryDirectory`` under a policy-compliant root.

    Skips the calling test with an explicit reason when no candidate root
    qualifies, so a host with an unusual temp-dir layout reports the cause
    instead of a helper exit status unrelated to the code under test.
    """
    tried: list[str] = []
    for base in _candidates():
        if not compliant(base):
            tried.append(f"{base} (rejected)")
            continue
        candidate = tempfile.TemporaryDirectory(prefix=prefix, dir=base)
        if compliant(Path(candidate.name)):
            return candidate
        candidate.cleanup()
        tried.append(f"{base} (created dir rejected)")
    raise unittest.SkipTest(
        "no temp directory satisfies the update-status path policy "
        "(root- or user-owned, non-group/world-writable ancestors): "
        + ", ".join(tried)
    )
