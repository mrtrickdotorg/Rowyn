#!/usr/bin/env python3
"""Contract for the Hub's clear-quarantine terminal instruction.

The shared crate's recovery status builds the command a user is handed
to clear a boot-health quarantine (RecoveryStatus.clear_quarantine_cmd,
surfaced through the Hub's recovery_status command). Upstream's form —
``sudo kyth-boot-health clear-quarantine --digest <digest>`` — cannot
run on a Rowyn system in either respect: Rowyn's helper ships at
/usr/libexec/rowyn-boot-health (never on PATH), and its clear-quarantine
takes the digest as a positional argument (there is no --digest flag).
The branding rename alone produced ``sudo rowyn-boot-health ...``,
which is still not a runnable command; the port therefore repoints the
instruction at the shipped helper's real interface
(apply step in build/ubuntu-hub-source.py).

These tests pin the two ends together:
- the real helper's CLI (clear-quarantine exists, digest positional,
  --digest rejected by argparse);
- the image layout (the Containerfile installs the helper at the path
  the instruction names);
- the generator's repointing, in source and (gated) after a real port.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/rowyn-boot-health"
GENERATOR = ROOT / "build/ubuntu-hub-source.py"
CONTAINERFILE = ROOT / "Containerfile"
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
DIGEST = "sha256:" + "a" * 64
CORRECT_COMMAND = '"sudo /usr/libexec/rowyn-boot-health clear-quarantine {}"'


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HelperCliTests(unittest.TestCase):
    """The shipped helper's clear-quarantine interface keeps its shape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rowyn-boot-health-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, ROWYN_BOOT_HEALTH_DIR=self.tmp.name)

    def run_helper(self, *args):
        return subprocess.run(
            [str(HELPER), *args], env=self.env,
            capture_output=True, text=True)

    def test_digest_is_a_positional_argument(self):
        # Parses, then stops at the root check (non-root) or the
        # not-quarantined check (root): either way exit 1, and never
        # an argparse "unrecognized arguments" failure.
        result = self.run_helper("clear-quarantine", DIGEST)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)

    def test_digest_flag_is_rejected(self):
        # The upstream instruction's form: argparse refuses --digest
        # before any root or state check runs.
        result = self.run_helper("clear-quarantine", "--digest", DIGEST)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_digest_argument_is_required(self):
        result = self.run_helper("clear-quarantine")
        self.assertEqual(result.returncode, 2, result.stderr)


class GeneratorSourceTests(unittest.TestCase):
    """The port repoints the instruction at the real helper."""

    @classmethod
    def setUpClass(cls):
        cls.script = GENERATOR.read_text()

    def test_generator_repoints_the_command(self):
        self.assertIn("recovery_status.rs", self.script)
        self.assertIn(
            '"sudo kyth-boot-health clear-quarantine --digest {}"',
            self.script)
        self.assertIn(CORRECT_COMMAND, self.script)

    def test_containerfile_installs_the_helper_at_the_named_path(self):
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-boot-health "
            "/usr/libexec/rowyn-boot-health",
            CONTAINERFILE.read_text())


def _pinned_repo():
    repo = os.environ.get("ROWYN_TEST_UPSTREAM_REPO")
    if not repo:
        return None
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", PIN + "^{commit}"],
        text=True, capture_output=True)
    if result.returncode != 0 or result.stdout.strip() != PIN:
        return None
    return Path(repo)


@unittest.skipUnless(
    _pinned_repo(),
    "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit")
class PinnedGeneratedTests(unittest.TestCase):
    """After a real port, the generated instruction is the runnable one."""

    def test_generated_clear_quarantine_command(self):
        hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-hub-recovery-") as temp:
            root = Path(temp) / "tree"
            hub.export_source(repo, root)
            hub.port_source(root)
            status = (root / "src/rowyn-shared-rs/src/system"
                      / "recovery_status.rs").read_text()
            self.assertIn(CORRECT_COMMAND, status)
            self.assertNotIn("kyth-boot-health", status)
            self.assertNotIn("clear-quarantine --digest", status)
            self.assertNotIn('"sudo rowyn-boot-health', status)


if __name__ == "__main__":
    unittest.main()
