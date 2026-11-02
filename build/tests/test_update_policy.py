#!/usr/bin/env python3
"""TDD coverage for Rowyn's image-owned bootc update signature policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/update-policy.sh"
REPOSITORY = "ghcr.io/mrtrickdotorg/rowyn"
KEY_PATH = "/usr/share/rowyn/update.pub"
# The promoted stable image at fixture time, digest-addressed so the pin stays
# valid; it carries a valid update-channel signature.
SIGNED_ROWYN_IMAGE = "docker://ghcr.io/mrtrickdotorg/rowyn@sha256:ce3936e202712576e7099385ae47eb2cd4865521a2296ba2c6b5d3ffa07d6c78"
UBUNTU_IMAGE = "docker://docker.io/library/ubuntu:latest"
UNSCOPED_IMAGE = "docker://quay.io/podman/hello:latest"


class UpdatePolicyTests(unittest.TestCase):
    def run_helper(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def run_skopeo_copy(
        self,
        policy: Path,
        registries: Path,
        source: str,
        destination: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("CONTAINERS_POLICY_JSON", None)
        return subprocess.run(
            [
                "skopeo",
                "--policy",
                str(policy),
                "--registries.d",
                str(registries),
                "copy",
                "--src-no-creds",
                source,
                f"dir:{destination}",
            ],
            text=True,
            capture_output=True,
            env=env,
            timeout=120,
        )

    def assert_runtime_fixture_available(
        self, result: subprocess.CompletedProcess[str], image: str
    ) -> None:
        if result.returncode == 0:
            return
        stderr = result.stderr.lower()
        if "rejected by policy" in stderr or "source image rejected" in stderr:
            self.fail(f"{image} was rejected by policy: {result.stderr}")
        unavailable = (
            "connection refused",
            "connection reset",
            "dial tcp",
            "forbidden",
            "i/o timeout",
            "manifest unknown",
            "no such host",
            "proxyconnect",
            "temporary failure",
            "tls handshake timeout",
            "too many requests",
            "unauthorized",
        )
        if any(marker in stderr for marker in unavailable):
            self.skipTest(f"registry fixture unavailable for {image}: {result.stderr.strip()}")
        self.fail(f"unexpected skopeo failure for {image}: {result.stderr}")

    def install_fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="rowyn-update-policy-")
        root = Path(temporary.name)
        result = self.run_helper(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = root / "etc/containers/policy.json"
        registries = root / "etc/containers/registries.d"
        return temporary, root, policy, registries

    def test_installs_image_owned_fail_closed_policy_without_root_or_var_writes(self):
        temporary, root, policy_path, _ = self.install_fixture()
        with temporary:
            self.assertTrue(policy_path.is_file())
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "var").exists())
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
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

    def test_generates_registry_attachment_configuration_for_exact_repository(self):
        temporary, root, _, registries = self.install_fixture()
        with temporary:
            config_path = registries / "rowyn-update.yaml"
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "docker:\n"
                "  ghcr.io/mrtrickdotorg/rowyn:\n"
                "    use-sigstore-attachments: true\n",
            )
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "var").exists())

    def test_policy_trusts_only_the_canonical_rowyn_repository(self):
        temporary, root, policy_path, _ = self.install_fixture()
        with temporary:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            docker = policy["transports"]["docker"]
            self.assertEqual(docker[REPOSITORY], [{
                "type": "sigstoreSigned",
                "keyPath": KEY_PATH,
                "signedIdentity": {
                    "type": "exactRepository",
                    "dockerRepository": REPOSITORY,
                },
            }])
            self.assertEqual(set(docker), {"docker.io", "ghcr.io", REPOSITORY})

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-update-policy-idempotent-") as temporary:
            root = Path(temporary)
            first = self.run_helper(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            tracked = [
                root / "etc/containers/policy.json",
                root / "etc/containers/registries.d/rowyn-update.yaml",
            ]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}

            second = self.run_helper(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, {
                path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked
            })

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_actual_rootless_docker_hub_ubuntu_remains_usable(self):
        if os.geteuid() == 0:
            self.skipTest("rootless fixture must run as a non-root user")
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            result = self.run_skopeo_copy(policy, registries, UBUNTU_IMAGE, root / "ubuntu")
            self.assert_runtime_fixture_available(result, UBUNTU_IMAGE)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_scoped_rowyn_requires_the_update_signing_key(self):
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            # This immutable registry fixture carries a valid Rowyn signature.
            # Pin the repo key locally so the test is independent of host state.
            policy_data = json.loads(policy.read_text(encoding="utf-8"))
            policy_data["transports"]["docker"][REPOSITORY][0]["keyPath"] = str(ROOT / "keys/update.pub")
            policy.write_text(json.dumps(policy_data), encoding="utf-8")
            signed = self.run_skopeo_copy(
                policy, registries, SIGNED_ROWYN_IMAGE, root / "rowyn-signed",
            )
            self.assert_runtime_fixture_available(signed, "signed Rowyn image")
            self.assertEqual(signed.returncode, 0, signed.stderr)

            # A different checked-in public key must not authorize that image.
            policy_data["transports"]["docker"][REPOSITORY][0]["keyPath"] = str(ROOT / "shared/retired-dev-key.pub")
            policy.write_text(json.dumps(policy_data), encoding="utf-8")
            wrong_key = self.run_skopeo_copy(
                policy, registries, SIGNED_ROWYN_IMAGE, root / "rowyn-wrong-key",
            )
            self.assertNotEqual(wrong_key.returncode, 0, wrong_key.stderr)
            self.assertRegex(wrong_key.stderr.lower(), "(rejected|signature|policy|key)")

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_actual_unscoped_registry_is_rejected_by_default_policy(self):
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            result = self.run_skopeo_copy(
                policy,
                registries,
                UNSCOPED_IMAGE,
                root / "untrusted",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr.lower(), "(rejected|policy)")


if __name__ == "__main__":
    unittest.main()
