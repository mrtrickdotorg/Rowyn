"""actionlint runs in validation, pinned identically in every workflow.

A YAML parse and string assertions cannot see expression or needs-context
mistakes: a job that reads `needs.validate.outputs` without depending on
`validate` evaluates the expression to an empty string, so its release gate
silently stops gating. actionlint resolves those contexts, so it is part of
`build/validate.sh` and installed from a checksum-pinned release wherever
validation runs.
"""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATE = ROOT / "build/validate.sh"
INSTALL_STEP = "Install validation tools"
WORKFLOWS = ("build.yml", "installer-iso.yml", "ubuntu-pin.yml")
VERSION = "v1.7.12"
TARBALL = "actionlint_1.7.12_linux_amd64.tar.gz"
SHA256 = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
SHELLCHECK_VERSION = "v0.11.0"
SHELLCHECK_TARBALL = "shellcheck-v0.11.0.linux.x86_64.tar.gz"
SHELLCHECK_SHA256 = (
    "b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6")


class ActionlintContractTests(unittest.TestCase):
    def install_steps(self):
        for name in WORKFLOWS:
            document = yaml.safe_load(
                (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            for job in document["jobs"].values():
                for step in job.get("steps") or []:
                    if step.get("name") == INSTALL_STEP:
                        yield name, step

    def test_validation_requires_and_runs_actionlint(self):
        text = VALIDATE.read_text(encoding="utf-8")
        self.assertIn("command -v actionlint", text)
        self.assertIn("actionlint is required", text)
        self.assertRegex(text, r"(?m)^actionlint$")
        # The workflow checks must come after the binary is required, or a
        # host without it would skip the lint instead of failing.
        self.assertLess(
            text.index("command -v actionlint"), text.index("\nactionlint\n"))

    def test_every_validating_workflow_installs_the_same_pinned_binary(self):
        steps = list(self.install_steps())
        self.assertEqual(
            sorted(name for name, _ in steps), sorted(WORKFLOWS),
            "every workflow that runs validate.sh must install actionlint")
        self.assertRegex(SHA256, r"^[0-9a-f]{64}$")
        for name, step in steps:
            env = step["env"]
            with self.subTest(workflow=name):
                self.assertEqual(env["ACTIONLINT_VERSION"], VERSION)
                self.assertEqual(env["ACTIONLINT_TARBALL"], TARBALL)
                self.assertEqual(env["ACTIONLINT_TARBALL_SHA256"], SHA256)
                run = step["run"]
                self.assertIn(
                    "releases/download/${ACTIONLINT_VERSION}/${ACTIONLINT_TARBALL}",
                    run)
                # An unverified download would let the lint result change
                # underneath us.
                self.assertIn('| sha256sum -c -', run)
                self.assertIn("sudo install -m 0755", run)
                self.assertIn("actionlint -version", run)


class ShellCheckPinContractTests(unittest.TestCase):
    """Inline workflow run scripts are linted by one pinned ShellCheck.

    actionlint lints every `run:` block with the ShellCheck on PATH, and the
    runner image's copy moves with the ubuntu-24.04 label (SC2002 appeared on
    the image's 0.9.0 and not on a newer local ShellCheck). Pinning the release
    in the same install step as actionlint makes the result identical on every
    machine running the workflow instead of depending on the image.
    """

    def install_steps(self):
        for name in WORKFLOWS:
            document = yaml.safe_load(
                (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            for job in document["jobs"].values():
                for step in job.get("steps") or []:
                    if step.get("name") == INSTALL_STEP:
                        yield name, step

    def test_every_validating_workflow_installs_the_same_pinned_shellcheck(self):
        steps = list(self.install_steps())
        self.assertEqual(
            sorted(name for name, _ in steps), sorted(WORKFLOWS),
            "every workflow that runs validate.sh must install ShellCheck")
        self.assertRegex(SHELLCHECK_SHA256, r"^[0-9a-f]{64}$")
        for name, step in steps:
            env = step["env"]
            run = step["run"]
            with self.subTest(workflow=name):
                self.assertEqual(env["SHELLCHECK_VERSION"], SHELLCHECK_VERSION)
                self.assertEqual(env["SHELLCHECK_TARBALL"], SHELLCHECK_TARBALL)
                self.assertEqual(
                    env["SHELLCHECK_TARBALL_SHA256"], SHELLCHECK_SHA256)
                self.assertIn(
                    "releases/download/${SHELLCHECK_VERSION}/${SHELLCHECK_TARBALL}",
                    run)
                self.assertIn('| sha256sum -c -', run)
                self.assertIn("/usr/local/bin/shellcheck", run)
                # The image's own ShellCheck must not be installed too: the
                # pinned binary is the only one that may answer.
                self.assertNotIn("python3-yaml shellcheck skopeo", run)
                # Installing is not enough; the effective binary has to be the
                # pinned release, not an older preinstalled copy that still
                # wins on some PATH.
                self.assertIn("shellcheck --version", run)
                self.assertIn('"${SHELLCHECK_VERSION#v}"', run)

    def test_docs_name_the_pinned_shellcheck_release(self):
        for relative in ("README.md", "docs/building.md"):
            with self.subTest(document=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("0.11.0", text)
                self.assertIn("ShellCheck", text)


if __name__ == "__main__":
    unittest.main()
