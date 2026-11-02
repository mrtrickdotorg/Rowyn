#!/usr/bin/env python3
"""Contracts for the per-user containers policy seeded through /etc/skel.

The system policy (shared/update-policy.sh) is fail-closed by design —
bootc update verification depends on it — but that strictness rejected
every rootless podman/distrobox pull from outside docker.io/ghcr.io on
installed systems (Patrick, Oct 29, 2026: distrobox could not pull
quay.io/toolbx/ubuntu-toolbox). containers/image reads a user's
~/.config/containers/policy.json in preference to the system file, so
the fix seeds a permissive policy through /etc/skel while the system
policy stays strict. These tests pin both halves so neither can drift:
the skel policy must stay exactly the permissive default, and the
system policy must stay reject-by-default with the Rowyn scope
signature-enforced.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
USER_HELPER = ROOT / "shared/containers-user-policy.sh"
SYSTEM_HELPER = ROOT / "shared/update-policy.sh"
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"
REPOSITORY = "ghcr.io/mrtrickdotorg/rowyn"
KEY_PATH = "/usr/share/rowyn/update.pub"


def run_helper(helper: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(helper), "--root", str(root)],
        text=True,
        capture_output=True,
        env=dict(os.environ, PATH="/usr/bin:/bin"),
    )


class ContainersUserPolicyTests(unittest.TestCase):
    def install_user_policy(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="rowyn-containers-user-policy-")
        root = Path(temporary.name)
        result = run_helper(USER_HELPER, root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return temporary, root / "etc/skel/.config/containers/policy.json"

    def test_seeds_permissive_user_policy_into_skel(self):
        temporary, policy_path = self.install_user_policy()
        with temporary:
            self.assertTrue(policy_path.is_file())
            self.assertEqual(stat.S_IMODE(policy_path.stat().st_mode), 0o644)
            # The entire content is the permissive default — nothing else.
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(policy, {"default": [{"type": "insecureAcceptAnything"}]})

    def test_helper_writes_only_the_skel_policy(self):
        temporary, policy_path = self.install_user_policy()
        with temporary:
            root = policy_path.parents[4]
            # No system policy, no /var or /root state from this helper.
            self.assertFalse((root / "etc/containers").exists())
            self.assertFalse((root / "var").exists())
            self.assertFalse((root / "root").exists())

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-containers-user-policy-") as temporary:
            root = Path(temporary)
            first = run_helper(USER_HELPER, root)
            self.assertEqual(first.returncode, 0, first.stderr)
            policy_path = root / "etc/skel/.config/containers/policy.json"
            before = hashlib.sha256(policy_path.read_bytes()).hexdigest()

            second = run_helper(USER_HELPER, root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, hashlib.sha256(policy_path.read_bytes()).hexdigest())

    def test_system_policy_stays_fail_closed_with_signed_rowyn_scope(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-system-policy-") as temporary:
            root = Path(temporary)
            result = run_helper(SYSTEM_HELPER, root)
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = json.loads(
                (root / "etc/containers/policy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(policy["default"], [{"type": "reject"}])
            docker = policy["transports"]["docker"]
            self.assertEqual(docker["docker.io"], [{"type": "insecureAcceptAnything"}])
            self.assertEqual(docker["ghcr.io"], [{"type": "insecureAcceptAnything"}])
            self.assertEqual(docker[REPOSITORY], [{
                "type": "sigstoreSigned",
                "keyPath": KEY_PATH,
                "signedIdentity": {
                    "type": "exactRepository",
                    "dockerRepository": REPOSITORY,
                },
            }])

    def test_containerfile_installs_skel_policy_after_stateless_normalization(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("/ctx/shared/containers-user-policy.sh --root /", text)
        self.assertIn("/usr/libexec/rowyn-update-policy --root /", text)
        self.assertLess(
            text.index("/ctx/shared/bootc-rootfs.sh"),
            text.index("/ctx/shared/containers-user-policy.sh"),
        )

    def test_workflow_smoke_checks_skel_policy(self):
        text = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -s /etc/skel/.config/containers/policy.json", text)
        self.assertIn(
            "python3 -m json.tool /etc/skel/.config/containers/policy.json", text
        )


if __name__ == "__main__":
    unittest.main()
