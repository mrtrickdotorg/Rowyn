import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "build" / "ubuntu-pin-bump.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ubuntu-pin.yml"
DIGEST = re.compile(r"^docker\.io/library/ubuntu@sha256:[0-9a-f]{64}$")


def load():
    spec = importlib.util.spec_from_file_location("ubuntu_pin_bump", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UbuntuPinBump(unittest.TestCase):
    def test_repository_pins_one_official_digest_everywhere(self):
        module = load()
        pinned = module.pinned(ROOT)
        self.assertRegex(pinned, DIGEST)
        # No tracked file may carry a second copy of the digest.
        self.assertEqual(module.stragglers(ROOT, pinned), [])

    def test_apply_moves_every_surface_once_and_rejects_mutable_refs(self):
        module = load()
        with tempfile.TemporaryDirectory(prefix="rowyn-pin-") as temp:
            root = Path(temp)
            for name in module.SURFACES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((ROOT / name).read_text(), encoding="utf-8")
            replacement = "docker.io/library/ubuntu@sha256:" + "1" * 64
            self.assertTrue(module.apply(root, replacement))
            self.assertEqual(module.pinned(root), replacement)
            self.assertFalse(module.apply(root, replacement), "second apply must be a no-op")
            with self.assertRaises(module.PinError):
                module.apply(root, "docker.io/library/ubuntu:26.10")

    def test_scheduled_workflow_moves_pins_and_commits_to_main(self):
        module = load()
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "41 5 * * *"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("python3 build/ubuntu-pin-bump.py --pinned", workflow)
        self.assertIn("python3 build/ubuntu-pin-bump.py --resolve", workflow)
        self.assertIn("python3 build/ubuntu-pin-bump.py --apply", workflow)
        self.assertIn("./build/validate.sh", workflow)
        self.assertIn("ROWYN_MOUNT_FIXTURE_PRIVILEGED", workflow)
        self.assertIn("git push origin main", workflow)
        for surface in ("build/hub/src-tauri/src/commands/rowyn_development.rs",
                        "build/hub/src/services/rowynDevelopmentModel.ts",
                        "docs/building.md"):
            self.assertIn(surface, workflow, f"bump must commit {surface}")
        # The repository takes no pull requests, so nothing may open one.
        self.assertNotIn("gh pr create", workflow)
        self.assertNotIn("pull_request", workflow)
        self.assertRegex(workflow, r"uses: actions/checkout@[0-9a-f]{40}")
        # The bump pushes with the default GITHUB_TOKEN, which GitHub refuses
        # for workflow-file changes regardless of the permissions block: no
        # pin surface may be a workflow file and the commit step must not
        # stage one, or every scheduled bump dies at the push.
        self.assertFalse(
            any(name.startswith(".github/workflows/") for name in module.SURFACES),
            "a workflow-file pin surface makes the scheduled bump unpushable")
        self.assertNotIn("installer-iso.yml", workflow)
        self.assertNotIn("workflows: write", workflow)

    def test_iso_workflow_derives_the_pin_instead_of_carrying_it(self):
        module = load()
        iso = (ROOT / ".github/workflows/installer-iso.yml").read_text(encoding="utf-8")
        self.assertNotIn(module.pinned(ROOT), iso)
        self.assertIn("UBUNTU_LIVE_IMAGE=$(python3 build/ubuntu-pin-bump.py --pinned)", iso)

    def test_no_other_file_hardcodes_the_digest(self):
        module = load()
        pinned = module.pinned(ROOT)
        for name in module.SURFACES:
            self.assertIn(pinned, (ROOT / name).read_text(encoding="utf-8"))
        self.assertEqual(module.stragglers(ROOT, pinned), [])


if __name__ == "__main__":
    unittest.main()
