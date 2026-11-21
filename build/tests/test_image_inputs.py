"""Cross-commit reproducibility contracts for the image build.

Same-commit reproducibility (two builds of one commit producing one
digest) was proven 2026-11-21, but the first daily rebuild showed
its limit: the build epoch was the HEAD commit's timestamp and the
source-revision stamp was the HEAD commit, so any commit — even a
docs-only one — re-stamped every layer and clients re-downloaded
the whole image. These tests pin the fix:

- the epoch is the fixed project constant ROWYN_SOURCE_DATE_EPOCH
  in the workflow env, never derived from a commit;
- the revision stamp is the last commit that touched the
  image-input pathspec ROWYN_IMAGE_INPUT_PATHSPEC, so it moves
  exactly when the inputs move;
- every COPY source in the Containerfiles is covered by that
  pathspec (generated sources through their committed provenance),
  so a future input cannot silently escape the stamp.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"
ISO_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "installer-iso.yml"
IMAGE_JOB_NAME = "Build and publish image"
CONTAINERFILES = (
    ROOT / "Containerfile",
    ROOT / "build" / "destination-derivative.Containerfile",
)

EXPECTED_PATHSPEC = (
    "Containerfile",
    "os-release",
    "shared",
    "keys",
    "docs/assets",
    "build/hub",
    "build/fetch-assets.sh",
    "build/ubuntu-hub-source.py",
    "build/destination-derivative.Containerfile",
    "build/registry-migration-destination-v1",
    ".github/workflows/build.yml",
    ".github/actions/setup-compressed-storage",
)

# COPY sources that are generated during the Build job rather than
# checked out: each maps to the committed paths whose content fully
# determines the generated tree. Those covering paths must
# themselves be in the pathspec.
GENERATED_PROVENANCE = {
    "build-assets": ("build/fetch-assets.sh", "Containerfile"),
    "hub-source": (
        "build/ubuntu-hub-source.py",
        "build/hub",
        ".github/workflows/build.yml",
    ),
}

# Paths that must stay OUTSIDE the stamp: changing any of them must
# leave the image digest untouched.
NON_INPUTS = (
    "build/tests",
    "build/validate.sh",
    "docs/updates.md",
    "README.md",
    ".github/workflows/installer-iso.yml",
    ".github/workflows/ubuntu-pin.yml",
)


def load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def pathspec():
    env = load_workflow()["env"]
    return env["ROWYN_IMAGE_INPUT_PATHSPEC"].split()


def covers(entry, path):
    entry = entry.rstrip("/")
    return path == entry or path.startswith(entry + "/")


def copy_sources(containerfile):
    """Yield the source operands of every COPY/ADD in a Containerfile."""
    text = containerfile.read_text(encoding="utf-8")
    logical = []
    buffer = ""
    for line in text.splitlines():
        buffer += line.rstrip()
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            continue
        logical.append(buffer)
        buffer = ""
    for line in logical:
        stripped = line.strip()
        if not re.match(r"(?i)^(COPY|ADD)\s", stripped):
            continue
        tokens = stripped.split()[1:]
        if any(token.startswith("--from=") for token in tokens):
            continue  # stage output; its provenance is the stage's inputs
        operands = [token for token in tokens if not token.startswith("--")]
        for source in operands[:-1]:  # the last operand is the destination
            yield source


class FixedEpochTests(unittest.TestCase):
    def test_epoch_is_a_fixed_constant(self):
        workflow = load_workflow()
        epoch = str(workflow["env"]["ROWYN_SOURCE_DATE_EPOCH"])
        self.assertTrue(epoch.isdigit(), epoch)
        self.assertGreater(int(epoch), 0)

    def test_epoch_is_never_derived_from_a_commit(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("git log -1 --format=%ct", text)
        # Both the Buildah build and the derivative build take the
        # constant, through the same shell variable.
        self.assertEqual(
            text.count('source_date_epoch="${ROWYN_SOURCE_DATE_EPOCH}"'), 2)
        self.assertIn('--source-date-epoch "${source_date_epoch}"', text)
        self.assertIn('--timestamp "${source_date_epoch}"', text)


class InputScopedRevisionTests(unittest.TestCase):
    def test_revision_comes_from_the_input_pathspec(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "git log -1 --format=%H -- ${ROWYN_IMAGE_INPUT_PATHSPEC}", text)
        self.assertIn(
            '--build-arg "ROWYN_SOURCE_REVISION=${input_revision}"', text)
        self.assertNotIn(
            '--build-arg "ROWYN_SOURCE_REVISION=${GITHUB_SHA}"', text)

    def test_build_job_fetches_full_history(self):
        workflow = load_workflow()
        steps = workflow["jobs"]["build"]["steps"]
        checkout = next(
            step for step in steps if step["name"] == "Checkout repository")
        self.assertEqual(checkout["with"]["fetch-depth"], 0)

    def test_smoke_check_compares_against_the_derived_revision(self):
        workflow = load_workflow()
        steps = workflow["jobs"]["build"]["steps"]
        by_name = {step["name"]: step for step in steps}
        build = by_name["Build image (offline Containerfile)"]
        self.assertEqual(build["id"], "build_image")
        self.assertIn('echo "input_revision=${input_revision}"', build["run"])
        smoke = by_name["Inspect image and run image smoke checks"]
        self.assertIn("steps.build_image.outputs.input_revision", smoke["run"])
        self.assertIn(
            'test "${image_revision}" = "${expected_revision}"', smoke["run"])


class PathspecTests(unittest.TestCase):
    def test_pathspec_is_exactly_the_image_inputs(self):
        self.assertEqual(tuple(pathspec()), EXPECTED_PATHSPEC)

    def test_every_pathspec_entry_exists(self):
        for entry in pathspec():
            self.assertTrue((ROOT / entry).exists(), entry)

    def test_non_inputs_stay_outside_the_stamp(self):
        entries = pathspec()
        for path in NON_INPUTS:
            self.assertFalse(
                any(covers(entry, path) for entry in entries),
                f"{path} must not move the image stamp")

    def test_every_copy_source_is_covered(self):
        entries = pathspec()
        for containerfile in CONTAINERFILES:
            for source in copy_sources(containerfile):
                source = source.rstrip("/")
                generated = next(
                    (key for key in GENERATED_PROVENANCE
                     if source == key or source.startswith(key + "/")),
                    None)
                if generated is not None:
                    for covering in GENERATED_PROVENANCE[generated]:
                        self.assertIn(
                            covering, entries,
                            f"{containerfile.name}: generated source "
                            f"{source} is provenanced by {covering}, "
                            "which is missing from the pathspec")
                    continue
                self.assertTrue(
                    any(covers(entry, source) for entry in entries),
                    f"{containerfile.name}: COPY source {source} is not "
                    "covered by ROWYN_IMAGE_INPUT_PATHSPEC; the stamp "
                    "would not move when it changes")


class ImageInputGateTests(unittest.TestCase):
    """A push that touches no image input must not republish the image.

    build/tests/** is a deliberate push trigger so validation runs for
    test-only changes, but the payload image and its signature must only
    move when ROWYN_IMAGE_INPUT_PATHSPEC moves.
    """

    def detection_step(self):
        steps = load_workflow()["jobs"]["validate"]["steps"]
        return next(
            step for step in steps
            if step["name"] == "Detect image-input changes")

    def test_validate_job_publishes_the_decision(self):
        validate = load_workflow()["jobs"]["validate"]
        self.assertEqual(
            validate["outputs"]["image_inputs"],
            "${{ steps.image_inputs.outputs.changed }}")
        step = self.detection_step()
        self.assertEqual(step["id"], "image_inputs")
        self.assertEqual(step["env"]["BEFORE_SHA"], "${{ github.event.before }}")
        self.assertIn("${ROWYN_IMAGE_INPUT_PATHSPEC}", step["run"])

    def test_release_chain_follows_the_gate(self):
        jobs = load_workflow()["jobs"]
        self.assertIn(
            "needs.validate.outputs.image_inputs == 'true'",
            jobs["build"]["if"])
        # sign and promote only need build, so a skipped build skips them;
        # always() would resurrect them without an image to sign.
        for name, expected in (("sign", "build"), ("promote", ["build", "sign"])):
            job = jobs[name]
            self.assertEqual(job["needs"], expected)
            self.assertNotIn("always()", job["if"])

    def test_iso_runs_only_for_an_upstream_image_build(self):
        iso = yaml.safe_load(ISO_WORKFLOW_PATH.read_text(encoding="utf-8"))
        validate = iso["jobs"]["validate"]
        self.assertEqual(
            validate["outputs"]["image_build"],
            "${{ steps.upstream.outputs.image_build }}")
        self.assertIn("actions", validate["permissions"])
        step = next(s for s in validate["steps"]
                    if s["name"] == "Detect whether the upstream run published an image")
        # The probe must name the real image job and fail open.
        self.assertIn('select(.name == "' + IMAGE_JOB_NAME + '"', step["run"])
        self.assertIn('image_build=unknown', step["run"])
        self.assertIn("needs.validate.outputs.image_build == 'success'",
                      iso["jobs"]["iso"]["if"])
        self.assertEqual(
            load_workflow()["jobs"]["build"]["name"], IMAGE_JOB_NAME)

    def detect(self, repo, scratch, event="push", before=None, head=None):
        output = scratch / "github_output"
        summary = scratch / "github_summary"
        output.write_text("")
        summary.write_text("")
        env = dict(
            os.environ,
            GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary),
            EVENT_NAME=event, BEFORE_SHA=before if before is not None else "0" * 40,
            HEAD_SHA=head or "",
            ROWYN_IMAGE_INPUT_PATHSPEC=load_workflow()["env"]["ROWYN_IMAGE_INPUT_PATHSPEC"],
        )
        result = subprocess.run(
            ["/bin/bash", "-euo", "pipefail", "-c", self.detection_step()["run"]],
            cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        written = dict(
            line.split("=", 1)
            for line in output.read_text().splitlines() if "=" in line)
        return written["changed"], summary.read_text()

    def test_detection_against_a_real_push_range(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-gate-") as temp:
            scratch = Path(temp)
            repo = scratch / "repo"
            repo.mkdir()

            def run(*args):
                return subprocess.run(["git", "-C", str(repo), *args],
                                      check=True, capture_output=True, text=True).stdout

            def commit(message):
                run("add", "-A")
                run("commit", "-q", "-m", message)
                return run("rev-parse", "HEAD").strip()

            run("init", "-q", "-b", "main")
            run("config", "user.name", "rowyn-test")
            run("config", "user.email", "rowyn-test@example.invalid")
            (repo / "build" / "tests").mkdir(parents=True)
            (repo / "Containerfile").write_text("FROM scratch\n")
            base = commit("base")

            (repo / "build" / "tests" / "test_only.py").write_text("pass\n")
            tests_only = commit("test-only")
            self.assertEqual(
                self.detect(repo, scratch, before=base, head=tests_only)[0],
                "false")

            (repo / "Containerfile").write_text("FROM scratch\n# input\n")
            image_input = commit("image input")
            self.assertEqual(
                self.detect(repo, scratch, before=tests_only, head=image_input)[0],
                "true")
            # A multi-commit push that touched an image input anywhere in the
            # range still rebuilds.
            self.assertEqual(
                self.detect(repo, scratch, before=base, head=image_input)[0],
                "true")

            # Undiffable ranges and non-push events fall back to building.
            self.assertEqual(self.detect(repo, scratch, head=image_input)[0], "true")
            self.assertEqual(
                self.detect(repo, scratch, event="workflow_dispatch", head=image_input)[0],
                "true")
            changed, summary = self.detect(repo, scratch, before=base, head=tests_only)
            self.assertEqual(changed, "false")
            self.assertIn("false", summary)


if __name__ == "__main__":
    unittest.main()
