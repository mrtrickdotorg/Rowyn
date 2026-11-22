"""The Updates page's restart action belongs to a staged digest.

The Hub performs exactly one restart: the allowlisted
`rowyn-finalize-staged reboot` the user asks for by clicking the action on a
staged deployment. These tests pin both halves — the fixed-argument launch and
the refusal that keeps it from restarting a machine with nothing staged.
"""
from pathlib import Path
import tempfile
import unittest

from test_hub_port import ROOT, hub, _pinned_repo

PORT = ROOT / "build/ubuntu-hub-source.py"
SUDOERS = ROOT / "shared/rowyn-upgrade.sudoers"
CONTAINER = ROOT / "Containerfile"
HUB = ROOT / "build/hub"


class RestartIsAllowlistedAndUserInitiated(unittest.TestCase):
    def test_the_finalizer_reboot_is_a_fixed_argv_with_no_wildcards(self):
        entries = [
            line.split("NOPASSWD:", 1)[1].strip()
            for line in SUDOERS.read_text(encoding="utf-8").splitlines()
            if line.startswith("%sudo") and "NOPASSWD:" in line
        ]
        self.assertIn("/usr/libexec/rowyn-finalize-staged reboot", entries)
        # Every granted command is a literal: no globs, no arguments built
        # from user input, nothing that a second argv could reach.
        for entry in entries:
            self.assertNotIn("*", entry, entry)
            self.assertNotIn("?", entry, entry)

    def test_the_helper_is_installed_root_owned_and_validated(self):
        text = CONTAINER.read_text(encoding="utf-8")
        self.assertIn(
            "install -Dm755 /ctx/shared/rowyn-finalize-staged.sh "
            "/usr/libexec/rowyn-finalize-staged", text)
        self.assertIn("install -Dm440 /ctx/shared/rowyn-upgrade.sudoers "
                      "/etc/sudoers.d/rowyn-upgrade", text)
        self.assertIn("visudo -cf /etc/sudoers.d/rowyn-upgrade", text)

    def test_no_rowyn_owned_hub_code_restarts_the_machine(self):
        """A Rowyn overlay must not add a second, unconfirmed reboot path."""
        for path in sorted((HUB / "src-tauri/src/commands").glob("rowyn_*.rs")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("systemctl\", \"reboot", "systemctl reboot",
                              "poweroff", "shutdown -r", "finalize-staged"):
                self.assertNotIn(forbidden, text,
                                 f"{path.name} must not restart the machine")
        for path in sorted((HUB / "src").rglob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("systemctl reboot", "poweroff"):
                self.assertNotIn(forbidden, text,
                                 f"{path.name} must not restart the machine")


class PortBindsTheRestartToAStagedDigest(unittest.TestCase):
    """Source-level pins on the transform, so a moved anchor fails loudly."""

    def test_port_requires_a_staged_digest_before_launching(self):
        text = PORT.read_text(encoding="utf-8")
        for needle in (
            "fn staged_digest_for_restart() -> Result<String, String>",
            'rowyn_shared::system::probe::read_section("bootc-status-data")',
            "rowyn_shared::system::bootc_query::fetch_status_data",
            'image_digest_from_status(&data, "staged")',
            "No update is staged, so nothing will restart. Check for updates first.",
            "Could not read the current deployment status, so nothing was restarted.",
            "let staged_digest = staged_digest_for_restart()?;",
        ):
            self.assertIn(needle, text)
        self.assertIn(
            '&format!("Restart to finish updating {staged_digest}")', text)

    def test_port_names_the_action_for_what_it_does(self):
        text = PORT.read_text(encoding="utf-8")
        self.assertIn('"Restart to finish updating"', text)
        self.assertIn("Choose “Restart to finish updating”", text)


class ExportedRestartAction(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_exported_action_refuses_without_a_staged_deployment(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-restart-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            updates = (web / "src-tauri/src/commands/updates.rs").read_text()

            self.assertIn("fn staged_digest_for_restart() -> Result<String, String>", updates)
            self.assertIn('image_digest_from_status(&data, "staged")', updates)
            self.assertIn("No update is staged, so nothing will restart.", updates)

            start = updates.index("pub(crate) fn apply_staged()")
            end = updates.index("#[tauri::command]", start + 1)
            body = updates[start:end]
            # The digest is read before the slot and before any job starts:
            # nothing is finalized or restarted on a blank click.
            self.assertLess(body.index("staged_digest_for_restart()?"),
                            body.index("take_mutating_slot()?"))
            self.assertLess(body.index("staged_digest_for_restart()?"),
                            body.index("start_update_job("))
            self.assertIn(
                'vec!["sudo", "-A", "/usr/libexec/rowyn-finalize-staged", "reboot"]',
                body)
            self.assertIn('&format!("Restart to finish updating {staged_digest}")', body)
            # The finalizer keeps exactly one launch site in the whole file.
            self.assertEqual(
                updates.count('"/usr/libexec/rowyn-finalize-staged", "reboot"'), 1)

            overview = (web / "src/components/UpdatesOverview.tsx").read_text()
            self.assertIn("Restart to finish updating", overview)
            self.assertNotIn("Restart to apply", overview)
            self.assertIn("Update ready — restart to finish", overview)

            contract = (web / "tests/command-contract.test.mjs").read_text()
            self.assertIn("Choose “Restart to finish updating”", contract)
            self.assertNotIn("Choose “Restart to apply”", contract)


if __name__ == "__main__":
    unittest.main()
