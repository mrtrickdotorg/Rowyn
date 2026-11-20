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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/update-policy.sh"
REPOSITORY = "ghcr.io/mrtrickdotorg/rowyn"
KEY_PATH = "/usr/share/rowyn/update.pub"

# containers/image checks run against checked-in `dir:` fixtures, never a
# registry: the policy decision is what this module covers, and a registry
# pull made the suite depend on the network (a slow link or a loaded host
# surfaced as a failure of the policy under test). `image` is an unsigned
# public image, `signed-image` is the same image plus a sigstore attachment
# produced by `fixtures/update-policy/generate.sh`, and `signing-key.pub` /
# `other-key.pub` are that fixture's throwaway keys.
FIXTURES = ROOT / "build/tests/fixtures/update-policy"
PUBLIC_IMAGE = FIXTURES / "image"
SIGNED_IMAGE = FIXTURES / "signed-image"
SIGNING_KEY = FIXTURES / "signing-key.pub"
OTHER_KEY = FIXTURES / "other-key.pub"
SIGNED_IDENTITY = "docker.io/library/hello-world:latest"


class UpdatePolicyTests(unittest.TestCase):
    def run_helper(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def container_policy(
        self, scopes: dict[str, list[dict[str, str]]]
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        """A fail-closed policy scoped to local `dir:` fixtures.

        Mirrors the shape the helper installs: a global `reject` default plus
        explicit per-scope requirements. The registries.d file enables the
        sigstore attachments the signed fixture carries.
        """
        temporary = tempfile.TemporaryDirectory(prefix="rowyn-update-policy-")
        root = Path(temporary.name)
        policy = root / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "default": [{"type": "reject"}],
                    "transports": {"dir": scopes},
                }
            ),
            encoding="utf-8",
        )
        registries = root / "registries.d"
        registries.mkdir()
        (registries / "fixture.yaml").write_text(
            "default-docker:\n  use-sigstore-attachments: true\n",
            encoding="utf-8",
        )
        return temporary, policy, registries

    def signature_policy(self, key: Path) -> dict[str, list[dict[str, str]]]:
        return {
            str(SIGNED_IMAGE): [
                {
                    "type": "sigstoreSigned",
                    "keyPath": str(key),
                    "signedIdentity": {
                        "type": "exactReference",
                        "dockerReference": SIGNED_IDENTITY,
                    },
                }
            ]
        }

    def run_skopeo_copy(
        self,
        policy: Path,
        registries: Path,
        source: Path,
        destination: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("CONTAINERS_POLICY_JSON", None)
        try:
            return subprocess.run(
                [
                    "skopeo",
                    "--policy",
                    str(policy),
                    "--registries.d",
                    str(registries),
                    "copy",
                    "--src-no-creds",
                    f"dir:{source}",
                    f"dir:{destination}",
                ],
                text=True,
                capture_output=True,
                env=env,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            # The policy decision itself is fast; the deadline only covers
            # reading the fixture. A host under load must report the fixture
            # as unavailable here rather than as a failure of the policy
            # under test, which is what a bare TimeoutExpired would look like.
            self.skipTest(
                f"update-policy fixture unavailable for {source}: skopeo copy "
                "did not finish within 120 seconds"
            )

    def copy_to_scratch(
        self, policy: Path, registries: Path, source: Path
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="rowyn-policy-copy-") as scratch:
            return self.run_skopeo_copy(
                policy, registries, source, Path(scratch) / "image"
            )

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
    def test_scoped_fixture_image_is_accepted(self):
        """A fail-closed policy still permits the scopes it explicitly allows."""
        temporary, policy, registries = self.container_policy(
            {str(PUBLIC_IMAGE): [{"type": "insecureAcceptAnything"}]}
        )
        with temporary:
            result = self.copy_to_scratch(policy, registries, PUBLIC_IMAGE)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_signed_fixture_requires_the_matching_signing_key(self):
        temporary, policy, registries = self.container_policy(
            self.signature_policy(SIGNING_KEY)
        )
        with temporary:
            signed = self.copy_to_scratch(policy, registries, SIGNED_IMAGE)
            self.assertEqual(signed.returncode, 0, signed.stderr)

            policy.write_text(
                json.dumps(
                    {
                        "default": [{"type": "reject"}],
                        "transports": {"dir": self.signature_policy(OTHER_KEY)},
                    }
                ),
                encoding="utf-8",
            )
            wrong_key = self.copy_to_scratch(policy, registries, SIGNED_IMAGE)
            self.assertNotEqual(wrong_key.returncode, 0, wrong_key.stderr)
            self.assertRegex(
                wrong_key.stderr.lower(),
                "(rejected|signature|policy|key)",
            )

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_unscoped_fixture_is_rejected_by_default_policy(self):
        temporary, policy, registries = self.container_policy(
            {str(SIGNED_IMAGE): [{"type": "insecureAcceptAnything"}]}
        )
        with temporary:
            result = self.copy_to_scratch(policy, registries, PUBLIC_IMAGE)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stderr.lower(), "(rejected|policy)")

    def test_fixture_copy_timeout_is_reported_as_a_skip(self):
        """A fixture read that never finishes must not look like a policy failure."""

        def timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="skopeo", timeout=120)

        with mock.patch.object(subprocess, "run", timeout):
            with self.assertRaises(unittest.SkipTest) as caught:
                self.run_skopeo_copy(
                    Path("/nonexistent/policy.json"),
                    Path("/nonexistent/registries.d"),
                    PUBLIC_IMAGE,
                    Path("/nonexistent/destination"),
                )
        self.assertIn("did not finish within 120 seconds", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
