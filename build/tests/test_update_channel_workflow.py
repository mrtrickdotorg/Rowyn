"""Contract and execution tests for the verified stable update channel."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build.yml"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/mrtrickdotorg/rowyn"


class UpdateChannelWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)

    def test_release_publishes_only_to_the_canonical_owner(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(
            self.workflow["env"]["IMAGE_REPOSITORY"], "ghcr.io/mrtrickdotorg/rowyn"
        )
        for job_name in ("build", "sign", "promote"):
            self.assertIn("github.repository == 'mrtrickdotorg/Rowyn'", jobs[job_name]["if"])
        self.assertNotIn("kyth-os/Rowyn", self.workflow_text)
        self.assertNotIn("ghcr.io/kyth-os", self.workflow_text)
        self.assertIn(
            '[[ "${IMAGE_NAME}" == "ghcr.io/mrtrickdotorg/rowyn" ]]',
            "\\n".join(step.get("run", "") for step in jobs["sign"]["steps"]),
        )

    def test_stable_promotion_is_gated_by_key_signature_and_digest_verification(self):
        jobs = self.workflow["jobs"]
        self.assertIn("promote", jobs)
        promote = jobs["promote"]
        self.assertEqual(set(promote["needs"]), {"build", "sign"})
        self.assertEqual(
            promote["permissions"],
            {"contents": "read", "packages": "write", "id-token": "write"},
        )

        names = [step["name"] for step in promote["steps"]]
        self.assertLess(
            names.index("Sign exact destination image with update-channel key"),
            names.index("Verify destination update-channel signature"),
        )
        self.assertLess(
            names.index("Verify destination update-channel signature"),
            names.index("Reject stale main before destination stable promotion"),
        )
        self.assertEqual(
            names.index("Reject stale main before destination stable promotion") + 1,
            names.index("Promote exact digest to destination stable"),
        )
        self.assertLess(
            names.index("Promote exact digest to destination stable"),
            names.index("Verify destination stable tag digest"),
        )

        self.assertIn('test -s "keys/update.pub"', self.workflow_text)
        self.assertIn("ROWYN_UPDATE_SIGNING_KEY", self.workflow_text)
        self.assertIn("ROWYN_UPDATE_SIGNING_PASSWORD", self.workflow_text)
        self.assertIn("cosign sign --yes --key env://ROWYN_UPDATE_SIGNING_KEY", self.workflow_text)
        self.assertIn("cosign verify --key keys/update.pub", self.workflow_text)
        self.assertIn("skopeo copy --preserve-digests", self.workflow_text)
        self.assertIn("DEST_IMAGE}@${DESTINATION_DIGEST}", self.workflow_text)
        self.assertIn('DEST_IMAGE}:stable', self.workflow_text)
        # Pass 2 channels: the same verified digest is also tagged
        # :testing inside the promote step (no extra registry login —
        # the workflow-wide ghcr.io login count is pinned elsewhere).
        self.assertIn('DEST_IMAGE}:testing', self.workflow_text)
        promote_step = next(
            step for step in promote["steps"]
            if step["name"] == "Promote exact digest to destination stable")
        self.assertIn("docker://${DEST_IMAGE}:testing", promote_step["run"])
        self.assertIn("testing_digest", promote_step["run"])
        verify_step = next(
            step for step in promote["steps"]
            if step["name"] == "Verify destination stable tag digest")
        self.assertIn("docker://${DEST_IMAGE}:testing", verify_step["run"])

    def test_promotion_is_signed_and_verified_before_stable_advances(self):
        jobs = self.workflow["jobs"]
        self.assertIn("promote", jobs)
        stage = jobs["promote"]
        self.assertEqual(set(stage["needs"]), {"build", "sign"})
        self.assertEqual(
            stage["permissions"],
            {"contents": "read", "packages": "write", "id-token": "write"},
        )
        self.assertEqual(stage["if"], jobs["build"]["if"])
        build_paths = self.workflow[True]["push"]["paths"]
        self.assertIn("build/destination-derivative.Containerfile", build_paths)
        self.assertIn("build/registry-migration-destination-v1", build_paths)
        self.assertNotIn("ROWYN_DEST_GHCR_TOKEN", self.workflow_text)
        publishing_jobs = ("build", "sign", "promote")
        auth_steps = 0
        for job_name in publishing_jobs:
            self.assertEqual(jobs[job_name]["permissions"]["packages"], "write")
            for step in jobs[job_name]["steps"]:
                if "login" in step.get("run", "") and "ghcr.io" in step.get("run", ""):
                    auth_steps += 1
                    self.assertEqual(
                        step["env"]["GHCR_TOKEN"], "${{ secrets.GITHUB_TOKEN }}"
                    )
                    self.assertEqual(
                        step["env"]["GHCR_USERNAME"], "${{ github.actor }}"
                    )
        self.assertEqual(auth_steps, 7)
        self.assertEqual(
            stage["outputs"]["destination_digest"],
            "${{ steps.build_destination.outputs.destination_digest }}",
        )

        stage_names = [step["name"] for step in stage["steps"]]
        self.assertLess(
            stage_names.index("Build and publish unique destination image"),
            stage_names.index("Sign exact destination image with update-channel key"),
        )
        self.assertLess(
            stage_names.index("Sign exact destination image with update-channel key"),
            stage_names.index("Verify destination update-channel signature"),
        )
        self.assertLess(
            stage_names.index("Verify destination update-channel signature"),
            stage_names.index("Promote exact digest to destination stable"),
        )
        self.assertLess(
            stage_names.index("Promote exact digest to destination stable"),
            stage_names.index("Verify destination stable tag digest"),
        )
        stage_text = "\n".join(step.get("run", "") for step in stage["steps"])
        self.assertIn("ghcr.io/mrtrickdotorg/rowyn", stage_text)
        self.assertIn("destination-derivative.Containerfile", stage_text)
        self.assertIn("registry-migration-destination-v1", stage_text)
        self.assertIn("podman run --rm --entrypoint /bin/sh", stage_text)
        self.assertIn("skopeo inspect --no-creds", stage_text)
        self.assertIn("skopeo copy --preserve-digests", stage_text)
        self.assertIn("cosign sign --yes --key env://ROWYN_UPDATE_SIGNING_KEY", stage_text)
        self.assertIn("cosign sign -y", stage_text)
        self.assertIn("cosign verify --key keys/update.pub", stage_text)
        self.assertIn("--certificate-identity-regexp", stage_text)
        self.assertIn('[[ "${destination_digest}" != "${IMAGE_DIGEST}" ]]', stage_text)
        self.assertIn("test ! -e \"$1\"", stage_text)
        self.assertIn("test -s \"$1\"", stage_text)
        self.assertIn("--entrypoint /usr/bin/bootc", stage_text)
        self.assertIn(" container lint", stage_text)
        marker_file = ROOT / "build/registry-migration-destination-v1"
        self.assertEqual(marker_file.read_text(encoding="utf-8"),
                         "rowyn-registry-migration-destination-v1\n")

    def test_container_image_declares_its_destination_source_repository(self):
        containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")
        self.assertIn(
            'LABEL org.opencontainers.image.source="https://github.com/mrtrickdotorg/Rowyn"',
            containerfile,
        )

    def test_destination_stable_verification_is_anonymous_and_uses_derived_digest(self):
        stage = self.workflow["jobs"]["promote"]
        verify = next(
            step for step in stage["steps"]
            if step["name"] == "Verify destination stable tag digest"
        )
        self.assertEqual(
            verify["env"]["DESTINATION_DIGEST"],
            "${{ steps.build_destination.outputs.destination_digest }}",
        )
        self.assertNotIn("ROWYN_DEST_GHCR_TOKEN", verify["env"])
        self.assertIn("skopeo inspect --no-creds", verify["run"])
        self.assertIn('"${stable_digest}" == "${DESTINATION_DIGEST}"', verify["run"])

    def test_destination_keyless_verifier_passes_exact_github_identity_regexp(self):
        stage = self.workflow["jobs"]["promote"]
        verify = next(
            step for step in stage["steps"]
            if step["name"] == "Verify destination update-channel signature"
        )
        identity_line = next(
            line.strip() for line in verify["run"].splitlines()
            if "--certificate-identity-regexp" in line
        )
        identity_source = identity_line.split(
            "--certificate-identity-regexp ", 1
        )[1].split(" ", 1)[0]
        self.assertEqual(
            identity_source,
            r'"^https://github.com/${GITHUB_REPOSITORY}/\.github/workflows/build\.yml@refs/heads/main$"',
        )
        with tempfile.TemporaryDirectory(prefix="rowyn-destination-cosign-") as temp:
            temp = Path(temp)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            log = temp / "cosign-args.bin"
            cosign = fake_bin / "cosign"
            cosign.write_text(
                "#!/bin/sh\n"
                "printf 'CALL\\\\0' >> \\\"$COSIGN_ARG_LOG\\\"\\n"
                "printf '%s\\\\0' \\\"$@\\\" >> \\\"$COSIGN_ARG_LOG\\\"\\n",
                encoding="utf-8",
            )
            cosign.chmod(0o755)
            cosign.unlink()
            cosign.symlink_to("/bin/echo")
            env = {
                "PATH": "/usr/bin:/bin",
                "COSIGN_ARG_LOG": str(log),
                "DESTINATION_DIGEST": DIGEST,
                "GHCR_TOKEN": "test-token",
                "GHCR_USERNAME": "test-user",
                "GITHUB_REPOSITORY": "mrtrickdotorg/Rowyn",
            }
            capture_cosign = r'''cosign() { printf '%s\n' CALL "$@"; }
'''
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", capture_cosign + verify["run"]],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = []
            for token in result.stdout.splitlines():
                if token == "CALL":
                    calls.append([])
                else:
                    calls[-1].append(token)
            self.assertEqual(len(calls), 3)
            identity_index = calls[2].index("--certificate-identity-regexp") + 1
            self.assertEqual(
                calls[2][identity_index],
                r"^https://github.com/mrtrickdotorg/Rowyn/\.github/workflows/build\.yml@refs/heads/main$",
            )

    def test_stale_main_guard_rejects_without_copy_and_current_main_allows_copy(self):
        promote = self.workflow["jobs"]["promote"]
        guard_step = next(
            step for step in promote["steps"]
            if step["name"] == "Reject stale main before destination stable promotion"
        )
        copy_step = next(
            step for step in promote["steps"]
            if step["name"] == "Promote exact digest to destination stable"
        )
        guard_script = guard_step["run"]
        copy_script = copy_step["run"]
        self.assertIn("GH_TOKEN", guard_step["env"])
        self.assertIn("gh api", guard_script)
        self.assertIn("git/ref/heads/main", guard_script)
        self.assertIn("GITHUB_REPOSITORY", guard_script)

        with tempfile.TemporaryDirectory(prefix="rowyn-update-channel-guard-") as temp:
            temp = Path(temp)
            log = temp / "calls.log"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "[ \"${GH_TOKEN-}\" = authenticated-token ]\n"
                "case \"$*\" in\n"
                "  *\"repos/${GITHUB_REPOSITORY}/git/ref/heads/main\"*) ;;\n"
                "  *) exit 42 ;;\n"
                "esac\n"
                "printf 'gh api\\n' >> \"$TEST_LOG\"\n"
                "printf '%s\\n' \"${GH_API_HEAD_SHA}\"\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            skopeo = fake_bin / "skopeo"
            skopeo.write_text(
                "#!/bin/sh\n"
                "printf 'skopeo %s\\n' \"$*\" >> \"$TEST_LOG\"\n"
                "case \"$*\" in\n"
                "  *inspect*\":stable\"*) printf '%s\\n' \"$DESTINATION_DIGEST\" ;;\n"
                "  *inspect*\":testing\"*) printf '%s\\n' \"$DESTINATION_DIGEST\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            skopeo.chmod(0o755)

            expected_sha = "b" * 40
            env = {
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TEST_LOG": str(log),
                "EXPECTED_SHA": expected_sha,
                "GH_TOKEN": "authenticated-token",
                "GITHUB_REPOSITORY": "mrtrickdotorg/Rowyn",
                "DESTINATION_DIGEST": DIGEST,
                "GHCR_TOKEN": "token",
                "GHCR_USERNAME": "actor",
                "RUNNER_TEMP": str(temp),
            }
            combined_script = f"{guard_script}\n{copy_script}"

            for head_sha, should_copy in (
                ("a" * 40, False),
                ("not-a-sha", False),
                (expected_sha, True),
            ):
                with self.subTest(head_sha=head_sha, should_copy=should_copy):
                    log.unlink(missing_ok=True)
                    case_env = {**env, "GH_API_HEAD_SHA": head_sha}
                    result = subprocess.run(
                        ["bash", "-euo", "pipefail", "-c", combined_script],
                        cwd=temp,
                        env=case_env,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode == 0, should_copy, result.stderr)
                    calls = log.read_text(encoding="utf-8") if log.exists() else ""
                    self.assertIn("gh api", calls)
                    self.assertEqual("skopeo copy" in calls, should_copy)

    def test_keyless_signing_remains_identity_bound_and_uses_oidc(self):
        sign = self.workflow["jobs"]["sign"]
        self.assertEqual(sign["permissions"]["id-token"], "write")
        sign_text = "\n".join(step.get("run", "") for step in sign["steps"])
        self.assertIn("cosign sign -y", sign_text)
        self.assertIn("cosign verify", sign_text)
        self.assertIn("--certificate-identity-regexp", sign_text)
        self.assertIn("--certificate-oidc-issuer", sign_text)
        self.assertNotIn("ROWYN_UPDATE_SIGNING_KEY", sign_text)

    def test_promotion_script_fails_closed_before_copy_when_public_key_is_missing(self):
        promote = self.workflow["jobs"]["promote"]
        sign_step = next(
            step for step in promote["steps"]
            if step["name"] == "Sign exact destination image with update-channel key"
        )
        copy_step = next(
            step for step in promote["steps"]
            if step["name"] == "Promote exact digest to destination stable"
        )
        verify_step = next(
            step for step in promote["steps"]
            if step["name"] == "Verify destination update-channel signature"
        )

        with tempfile.TemporaryDirectory(prefix="rowyn-update-channel-") as temp:
            temp = Path(temp)
            log = temp / "calls.log"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            cosign = fake_bin / "cosign"
            cosign.write_text(
                "#!/bin/sh\n"
                "printf 'cosign %s\\n' \"$*\" >> \"$TEST_LOG\"\n",
                encoding="utf-8",
            )
            cosign.chmod(0o755)
            skopeo = fake_bin / "skopeo"
            skopeo.write_text(
                "#!/bin/sh\n"
                "printf 'skopeo %s\\n' \"$*\" >> \"$TEST_LOG\"\n",
                encoding="utf-8",
            )
            skopeo.chmod(0o755)
            env = {
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TEST_LOG": str(log),
                "DESTINATION_DIGEST": DIGEST,
                "GHCR_TOKEN": "token",
                "GHCR_USERNAME": "actor",
                "ROWYN_UPDATE_SIGNING_KEY": "private-key-material",
                "COSIGN_PASSWORD": "password",
            }
            # The key check belongs to the signature step, not the copy step.
            public_key_check = "test -s \"keys/update.pub\""
            self.assertIn(public_key_check, verify_step["run"])
            self.assertIn(public_key_check, sign_step["run"])
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", sign_step["run"]],
                cwd=temp,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists(), "signing must not run without the checked-in public key")
            self.assertIn("skopeo copy --preserve-digests", copy_step["run"])

    def test_stable_digest_verification_compares_registry_tag_to_source_digest(self):
        promote = self.workflow["jobs"]["promote"]
        verify_step = next(
            step for step in promote["steps"]
            if step["name"] == "Verify destination stable tag digest"
        )
        script = verify_step["run"]
        self.assertIn('stable_digest="$(skopeo inspect', script)
        self.assertIn('"${DESTINATION_DIGEST}"', script)
        self.assertRegex(script, r"\[\[ .*stable_digest.*==.*DESTINATION_DIGEST")


if __name__ == "__main__":
    unittest.main()
