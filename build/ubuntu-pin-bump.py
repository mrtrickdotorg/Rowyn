#!/usr/bin/env python3
"""Resolve and move the pinned official Ubuntu image digest.

The payload and the live ISO must start from one digest-pinned Ubuntu image;
build/ubuntu-only-check.py enforces that they agree. Canonical replaces the
image behind the tag, so this script resolves the tag and rewrites every pin
surface in one step, refusing if a stale copy of the digest is left behind.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "docker.io/library/ubuntu:26.10"
REGISTRY = "https://registry-1.docker.io"
REPOSITORY = "library/ubuntu"
REFERENCE = "26.10"
PIN = re.compile(r"^docker\.io/library/ubuntu@sha256:[0-9a-f]{64}$")
PIN_TEXT = re.compile(r"docker\.io/library/ubuntu@sha256:[0-9a-f]{64}")
# Every surface that carries a copy of the same official Ubuntu image moves
# together: the payload, the managed development container that the Hub
# creates, and the documentation that names the pin. The live ISO is
# deliberately NOT a surface: installer-iso.yml resolves this pin at build
# time (`--pinned`), because the scheduled bump pushes with the default
# GITHUB_TOKEN, which GitHub refuses for workflow-file changes — a pin copy
# inside a workflow file makes every bump unpushable.
SURFACES = (
    "Containerfile",
    "build/hub/src-tauri/src/commands/rowyn_development.rs",
    "build/hub/src-tauri/src/commands/rowyn_deb.rs",
    "build/hub/src/services/rowynDevelopmentModel.ts",
    "docs/building.md",
)
# Untracked working trees that never ship and duplicate the pin by design.
IGNORED_PREFIXES = (".git/", ".tools/", "hub-source/", "build-assets/")


class PinError(Exception):
    pass


def _token() -> str:
    url = (
        "https://auth.docker.io/token?service=registry.docker.io"
        f"&scope=repository:{REPOSITORY}:pull"
    )
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)["token"]


def resolve() -> str:
    """Return the digest the tag points at right now, as a FROM-usable ref."""
    request = urllib.request.Request(
        f"{REGISTRY}/v2/{REPOSITORY}/manifests/{REFERENCE}",
        method="HEAD",
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.oci.image.index.v1+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        digest = response.headers.get("Docker-Content-Digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise PinError(f"registry returned no usable digest for {TAG}: {digest!r}")
    return f"docker.io/library/ubuntu@{digest}"


def pinned(root: Path = ROOT) -> str:
    """Return the digest the repository pins, requiring every surface to agree."""
    found = set()
    for name in SURFACES:
        found.update(PIN_TEXT.findall((root / name).read_text(encoding="utf-8")))
    if len(found) != 1:
        raise PinError("pin surfaces disagree: " + ", ".join(sorted(found)) or "none found")
    return found.pop()


def stragglers(root: Path, current: str) -> list[str]:
    """Tracked files that still carry the old digest outside the pin surfaces."""
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    stale = []
    for name in tracked:
        if name in SURFACES or name.startswith(IGNORED_PREFIXES):
            continue
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if current in text:
            stale.append(name)
    return stale


def apply(root: Path, image: str) -> bool:
    if not PIN.match(image):
        raise PinError("refusing a non-Ubuntu digest pin: " + image)
    current = pinned(root)
    stale = stragglers(root, current)
    if stale:
        raise PinError(
            "the old digest is still hardcoded in " + ", ".join(stale)
            + "; derive it from the pin instead of copying it"
        )
    if current == image:
        return False
    for name in SURFACES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(current, image), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--pinned", action="store_true", help="print the digest the repository pins")
    choice.add_argument("--resolve", action="store_true", help="print the digest the tag resolves to")
    choice.add_argument("--apply", metavar="IMAGE", help="rewrite both pin surfaces to IMAGE")
    args = parser.parse_args(argv)
    try:
        if args.pinned:
            print(pinned())
        elif args.resolve:
            print(resolve())
        else:
            print("updated" if apply(ROOT, args.apply) else "unchanged")
    except PinError as error:
        sys.exit(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
