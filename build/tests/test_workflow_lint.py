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


if __name__ == "__main__":
    unittest.main()
