"""Workflow trust contracts plus executable data/invocation regression tests."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("iso_source", ROOT / "build/iso-source.py")
assert spec is not None and spec.loader is not None
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
WRAPPER = Path(os.environ.get("ROWYN_TEST_PODMAN_WRAPPER", ROOT / "build/rootful-podman.sh"))
IMAGE_WORKFLOW = Path(os.environ.get("ROWYN_TEST_IMAGE_WORKFLOW", ROOT / ".github/workflows/build.yml"))
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.upstream = dict(id=42, run_attempt=1, repository={"full_name": source.REPO},
                        head_repository={"full_name": source.REPO}, head_branch="main",
                        event="push", path=".github/workflows/build.yml", status="completed",
                        conclusion="success", head_sha=SHA)
        self.metadata = dict(run_id="42", source_sha=SHA, image=source.IMAGE + ":" + SHA, digest=DIGEST)
        self.artifact = dict(id=7, name="rowyn-image-metadata-" + SHA, expired=False, size_in_bytes=500)
        self.calls = []

    def resolve(self, run=None, metadata=None, artifact=None, entries=None):
        run = self.upstream if run is None else run
        metadata = self.metadata if metadata is None else metadata
        artifact = self.artifact if artifact is None else artifact
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name in entries or ["image-build-metadata.json"]:
                archive.writestr(name, json.dumps(metadata))
        def fetch(path):
            self.calls.append(path)
            if path == "/actions/runs/42":
                return run
            self.assertEqual(path, "/actions/runs/42/artifacts?per_page=100")
            return dict(total_count=1, artifacts=[artifact])
        def download(ident):
            self.assertEqual(ident, 7)
            return data.getvalue()
        return source.resolve("workflow_run", {"workflow_run": self.upstream}, source.REPO,
                              "refs/heads/main", fetch, download)

    def test_exact_run_artifact_and_canonical_digest(self):
        result = self.resolve()
        self.assertEqual(result, dict(image_ref=source.IMAGE + ":" + SHA, source_sha=SHA,
                                     image_digest=DIGEST, source_image=source.IMAGE + "@" + DIGEST))
        self.assertEqual(self.calls, ["/actions/runs/42", "/actions/runs/42/artifacts?per_page=100"])

    def test_untrusted_upstream_cannot_reach_artifact(self):
        for key, value in (("event", "pull_request"), ("head_branch", "feature"),
                           ("head_repository", {"full_name": "attacker/Rowyn"}),
                           ("repository", {"full_name": "attacker/Rowyn"}),
                           ("conclusion", "failure"), ("status", "in_progress"),
                           ("path", ".github/workflows/other.yml")):
            with self.subTest(key=key):
                old = copy.deepcopy(self.upstream)
                self.upstream[key] = value
                with self.assertRaises(ValueError):
                    self.resolve()
                self.upstream = old
                self.assertEqual(self.calls, [])

    def test_api_rechecks_run_and_attempt(self):
        for key, value in (("run_attempt", 2), ("head_sha", "c" * 40),
                           ("id", 43), ("event", "pull_request")):
            run = dict(self.upstream, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.resolve(run=run)

    def test_rejects_metadata_substitution_and_injection(self):
        for key, value in (("run_id", "43"), ("source_sha", "c" * 40),
                           ("image", source.IMAGE + ":spike"),
                           ("digest", DIGEST + "\nsource_image=evil"), ("digest", "sha256:bad")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.resolve(metadata=dict(self.metadata, **{key: value}))
        for entries in (["../evil"], ["image-build-metadata.json", "code.sh"]):
            with self.assertRaises(ValueError):
                self.resolve(entries=entries)
        for key, value in (("expired", True), ("name", "other"), ("size_in_bytes", 100000)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.resolve(artifact=dict(self.artifact, **{key: value}))

    def test_download_does_not_forward_bearer_token(self):
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(
            "https://api.github.com/", 302, "Found",
            {"Location": "https://blob.example.test/metadata.zip"}, None)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"zip bytes"
        with patch.dict(os.environ, GH_TOKEN="test-not-a-real-token"), \
                patch.object(source.urllib.request, "build_opener", return_value=opener), \
                patch.object(source.urllib.request, "urlopen", return_value=response) as download:
            self.assertEqual(source.download_artifact(7), b"zip bytes")
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.github.com/repos/mrtrickdotorg/Rowyn/actions/artifacts/7/zip")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-not-a-real-token")
            # Follow-up receives only a URL, not the authenticated Request object.
            self.assertEqual(download.call_args.args, ("https://blob.example.test/metadata.zip",))

    def test_manual_exact_digest_no_api_and_fail_closed(self):
        event = {"inputs": {"image_digest": DIGEST, "source_sha": SHA}}
        def no_api(path):
            self.fail("manual path must not select an image from API: " + path)
        result = source.resolve("workflow_dispatch", event, source.REPO, "refs/heads/main", no_api)
        self.assertEqual(result["source_image"], source.IMAGE + "@" + DIGEST)
        for name, repo, ref in (("push", source.REPO, "refs/heads/main"),
                                ("workflow_dispatch", "fork/Rowyn", "refs/heads/main"),
                                ("workflow_dispatch", source.REPO, "refs/heads/feature")):
            with self.assertRaises(ValueError):
                source.resolve(name, event, repo, ref, no_api)
        for field in ("image_digest", "source_sha"):
            bad = copy.deepcopy(event)
            bad["inputs"][field] += "\nmalicious=value"
            with self.assertRaises(ValueError):
                source.resolve("workflow_dispatch", bad, source.REPO, "refs/heads/main", no_api)


class PodmanInvocationTests(unittest.TestCase):
    def test_host_namespace_rootful_and_exact_argument_forwarding(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-invocation-") as temp:
            temp = Path(temp)
            log = temp / "calls.jsonl"
            mock = temp / "mock"
            mock.write_text('''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
if name == 'id':
    print(os.environ['TEST_UID'])
else:
    with open(os.environ['TEST_LOG'], 'a') as f:
        f.write(json.dumps([name] + sys.argv[1:]) + '\\n')
    sys.exit(int(os.environ.get('TEST_EXIT', '0')))
''')
            mock.chmod(0o755)
            for name in ("id", "sudo", "podman", "unshare"):
                (temp / name).symlink_to(mock)
            for uid in ("0", "1000"):
                for exit_code in (0, 19):
                    env = dict(os.environ, PATH=str(temp), TEST_UID=uid, TEST_LOG=str(log),
                               TEST_EXIT=str(exit_code), GITHUB_ACTIONS="true",
                               RUNNER_ENVIRONMENT="github-hosted")
                    env.pop("CONTAINER_ID", None)
                    args = ["build", "--cap-add", "SYS_ADMIN", "--network", "host", "space arg", ""]
                    result = subprocess.run(["/bin/bash", str(WRAPPER), *args], env=env)
                    self.assertEqual(result.returncode, exit_code)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    expected = ["podman"] if uid == "0" else ["sudo", "--preserve-env=MOK_KEY", "podman"]
                    self.assertEqual(calls[-1], expected + args)
            env["RUNNER_ENVIRONMENT"] = "self-hosted"
            before = log.read_bytes()
            result = subprocess.run(["/bin/bash", str(WRAPPER), "run"], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_bytes(), before)


class WorkflowTests(unittest.TestCase):
    def test_source_signature_invocation_exact_identity_digest_and_failure(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/installer-iso.yml").read_text())
        step = next(s for s in workflow["jobs"]["iso"]["steps"]
                    if s["name"] == "Verify fixed payload signer before privileged build")
        with tempfile.TemporaryDirectory(prefix="rowyn-cosign-") as temp:
            temp = Path(temp)
            binary = temp / "cosign"
            log = temp / "arguments.json"
            binary.write_text('#!/usr/bin/python3\nimport json, os, sys\n'
                              'from pathlib import Path\n'
                              'Path(os.environ["TEST_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
                              'sys.exit(int(os.environ["TEST_EXIT"]))\n')
            binary.chmod(0o755)
            for status in (0, 21):
                env = dict(os.environ, PATH=str(temp), TEST_LOG=str(log), TEST_EXIT=str(status),
                           SOURCE_IMAGE=source.IMAGE + "@" + DIGEST,
                           GITHUB_REPOSITORY="mrtrickdotorg/Rowyn")
                result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c", step["run"]], env=env)
                self.assertEqual(result.returncode, status)
                self.assertEqual(json.loads(log.read_text()), [
                    "verify", "--certificate-identity",
                    "https://github.com/mrtrickdotorg/Rowyn/.github/workflows/build.yml@refs/heads/main",
                    "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com",
                    source.IMAGE + "@" + DIGEST])

    def test_release_trust_order_and_identity(self):
        image = yaml.safe_load(IMAGE_WORKFLOW.read_text())
        iso = yaml.safe_load((ROOT / ".github/workflows/installer-iso.yml").read_text())
        self.assertTrue("iso" not in image["jobs"], "ISO must not remain in payload workflow")
        self.assertEqual(image["jobs"]["build"]["needs"], "validate")
        self.assertEqual(image["jobs"]["sign"]["needs"], "build")
        for job in ("build", "sign"):
            gate = image["jobs"][job]["if"]
            for token in ("github.event_name == 'push'", "github.event_name == 'workflow_dispatch'",
                          "github.ref == 'refs/heads/main'", "github.repository == 'mrtrickdotorg/Rowyn'"):
                self.assertIn(token, gate)
        self.assertEqual(image["permissions"], {"contents": "read"})
        self.assertNotIn("id-token", image["jobs"]["build"]["permissions"])
        self.assertEqual(iso["permissions"], {"contents": "read"})
        self.assertEqual(iso["jobs"]["iso"]["needs"], "validate")
        self.assertEqual(iso["jobs"]["iso"]["permissions"], {"contents": "read", "actions": "read", "id-token": "write"})
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", iso["jobs"]["validate"]["if"])
        steps = iso["jobs"]["iso"]["steps"]
        for job in iso["jobs"].values():
            checkout = job["steps"][0]
            self.assertEqual(checkout["with"], {"ref": "${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || 'refs/heads/main' }}",
                              "persist-credentials": False})
        names = [step["name"] for step in steps]
        self.assertLess(names.index("Resolve exact signed image"), names.index("Verify fixed payload signer before privileged build"))
        self.assertLess(names.index("Verify fixed payload signer before privileged build"), names.index("Provision compressed Podman storage"))
        text = (ROOT / ".github/workflows/installer-iso.yml").read_text()
        self.assertNotIn("needs.build", text)
        self.assertNotIn("podman build", text)
        self.assertNotIn("fetch-assets.sh", text)
        self.assertIn('--certificate-identity "https://github.com/${GITHUB_REPOSITORY}/.github/workflows/build.yml@refs/heads/main"', text)
        self.assertIn(r"workflows/installer-iso\.yml@refs/heads/main$", text)
        for step in steps:
            if step["name"] in ("Build custom installer ISO", "Preflight pinned custom installer adapter"):
                self.assertEqual(step["env"]["IMAGE_DIGEST"], "${{ steps.source.outputs.image_digest }}")
                self.assertEqual(step["env"]["SOURCE_TAG"], "${{ steps.source.outputs.source_sha }}")

    def test_path_filters_and_shell_syntax(self):
        image = yaml.safe_load(IMAGE_WORKFLOW.read_text())
        # PyYAML's YAML 1.1 loader interprets GitHub's 'on' as True.
        paths = image[True]["push"].get("paths", [])
        self.assertIn(".github/workflows/build.yml", paths)
        self.assertNotIn("build/**", paths)
        # build/tests/** is a deliberate trigger: validate must run
        # for test-only changes (aad3101), or a broken fixture can
        # land green-by-absence.
        self.assertIn("build/tests/**", paths)
        for path in ("build/custom-installer-iso.sh", ".github/workflows/installer-iso.yml"):
            self.assertNotIn(path, paths)
        iso_paths = yaml.safe_load((ROOT / ".github/workflows/installer-iso.yml").read_text())[True]["push"]["paths"]
        self.assertIn("shared/kde-dark-defaults.sh", iso_paths)
        for name in ("build.yml", "installer-iso.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            for job in workflow["jobs"].values():
                for step in job["steps"]:
                    if "run" in step:
                        result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_validation_caller_sets_explicit_mount_fixture_privilege(self):
        callers = []
        for workflow_path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            workflow = yaml.safe_load(workflow_path.read_text())
            for job_name, job in workflow["jobs"].items():
                for step in job["steps"]:
                    run = (step.get("run") or "").strip()
                    if run.split()[-1:] == ["./build/validate.sh"]:
                        callers.append((workflow_path.name, job_name, step))
        self.assertEqual(
            [(workflow, job) for workflow, job, _ in callers],
            [("build.yml", "validate"), ("installer-iso.yml", "validate"),
             ("ubuntu-pin.yml", "bump")],
        )
        for _, _, step in callers:
            self.assertEqual(step["env"].get("ROWYN_MOUNT_FIXTURE_PRIVILEGED"), "1")
