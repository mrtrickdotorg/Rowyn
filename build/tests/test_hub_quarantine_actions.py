"""Clearing a boot-health quarantine from the Hub.

The panel that shows a quarantine is the place to fix it, but the write stays
one literal, allowlisted argv with the digest on stdin, behind an explicit
confirmation, and the read-only panel keeps no write path of its own.
"""
from pathlib import Path
import tempfile
import unittest

from test_hub_port import ROOT, hub, _pinned_repo

PORT = ROOT / "build/ubuntu-hub-source.py"
SUDOERS = ROOT / "shared/rowyn-upgrade.sudoers"
HELPER = ROOT / "shared/rowyn-boot-health"
HUB = ROOT / "build/hub"
READ_BRIDGE = HUB / "src-tauri/src/commands/rowyn_boot_health.rs"
BRIDGE = HUB / "src-tauri/src/commands/rowyn_boot_health_actions.rs"


class TheGrantIsOneLiteralArgv(unittest.TestCase):
    def test_the_helper_is_allowlisted_with_the_digest_on_stdin(self):
        entries = [
            line.split("NOPASSWD:", 1)[1].strip()
            for line in SUDOERS.read_text(encoding="utf-8").splitlines()
            if line.startswith("%sudo") and "NOPASSWD:" in line
        ]
        clear = [entry for entry in entries if "clear-quarantine" in entry]
        self.assertEqual(clear, ["/usr/libexec/rowyn-boot-health clear-quarantine -"])
        # A wildcard would let any argument through; the digest must never be
        # part of the granted command line.
        for entry in entries:
            self.assertNotIn("*", entry, entry)
            self.assertNotIn("?", entry, entry)

    def test_the_helper_accepts_the_stdin_form_and_still_validates(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("def read_digest_argument(digest: str) -> str:", text)
        self.assertIn('if digest != "-":', text)
        self.assertIn("sys.stdin.readline(4096).strip()", text)
        self.assertIn("cmd_clear_quarantine(cfg, read_digest_argument(args.digest))", text)
        # One digest, and the same format check the positional form uses.
        self.assertIn("if not DIGEST_RE.fullmatch(digest):", text)


class TheBridgeIsFixedArgumentAndFailsClosed(unittest.TestCase):
    def test_command_shape_and_refusals(self):
        text = BRIDGE.read_text(encoding="utf-8")
        for needle in (
            'const SUDO: &str = "/usr/bin/sudo";',
            'const HELPER: &str = "/usr/libexec/rowyn-boot-health";',
            'SUDO, "-n", HELPER, "clear-quarantine", "-"',
            "run_bounded_with_input(&argv(), input.as_bytes()",
            "require_confirmation(confirmed)?",
            "super::rowyn_boot_health::collect()",
            "is not quarantined now",
            "nothing was downloaded, installed, or restarted",
        ):
            self.assertIn(needle, text)
        for forbidden in ("pkexec", "bash", "sh -c", "Command::new(program)", "Stdio::inherit"):
            self.assertNotIn(forbidden, text)

    def test_the_digest_is_never_an_argument(self):
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertNotIn("argv.push", text)
        self.assertNotIn('"-", digest', text)
        # The only argv constructor is the constant list in `fn argv`.
        self.assertEqual(text.count("fn argv()"), 1)

    def test_the_record_check_happens_before_the_helper_runs(self):
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertLess(
            text.index("if !quarantined(&record)?"),
            text.index("run_bounded_with_input("),
            "the digest must be checked against the record before anything runs")

    def test_a_debug_fixture_path_override_exists_and_is_gated(self):
        text = READ_BRIDGE.read_text(encoding="utf-8")
        self.assertIn("ROWYN_BOOT_HEALTH_PATH", text)
        self.assertIn("#[cfg(debug_assertions)]", text)
        self.assertIn("#[cfg(not(debug_assertions))]", text)
        # The fixed path keeps its owner/mode rule: only a debug fixture, which
        # the developer owns, skips it.
        self.assertIn("let unsafe_owner = require_root &&", text)
        self.assertIn("read_record(Path::new(PATH), true)", text)
        self.assertIn("read_record(&path, false)", text)

    def test_docs_say_how_to_use_the_override(self):
        docs = (ROOT / "docs/boot-health.md").read_text(encoding="utf-8")
        self.assertIn("Exercising the panel without root", docs)
        self.assertIn("ROWYN_BOOT_HEALTH_PATH=/tmp/boot-health.json", docs)
        self.assertIn("compiled out of release builds", docs)

    def test_tests_exist_for_the_refusals(self):
        tests = (HUB / "src-tauri/src/commands/rowyn_boot_health_actions_tests.rs").read_text(
            encoding="utf-8")
        for needle in ("fn the_command_is_one_literal_allowlisted_argv",
                       "fn only_a_lowercase_sha256_digest_is_accepted",
                       "fn clearing_requires_the_explicit_confirmation",
                       "fn the_helper_reads_the_digest_from_stdin_not_argv"):
            self.assertIn(needle, tests)


class ThePanelStaysReadOnlyAndConfirms(unittest.TestCase):
    def test_the_panel_keeps_no_write_path(self):
        panel = (HUB / "src/components/BootHealthPanel.tsx").read_text(encoding="utf-8")
        for forbidden in ("invoke(", "sudo", "pkexec", "systemctl", "writeFile", "clearBootQuarantine"):
            self.assertNotIn(forbidden, panel)
        self.assertIn("BootQuarantineClear", panel)
        self.assertIn("asks for confirmation first", panel)
        self.assertNotIn("terminal-only action", panel)

    def test_the_confirmation_is_an_in_page_dialog(self):
        control = (HUB / "src/components/BootQuarantineClear.tsx").read_text(encoding="utf-8")
        for needle in ('role="alertdialog"', "aria-modal=\"false\"",
                       "Clear the quarantine for this deployment?",
                       "Nothing is downloaded, installed,", "onCleared()",
                       "clearBootQuarantine(digest, true)"):
            self.assertIn(needle, control)
        for forbidden in ("window.confirm", "setInterval", "setTimeout", "sudo"):
            self.assertNotIn(forbidden, control)

    def test_the_service_is_typed_and_native_only(self):
        service = (HUB / "src/services/rowynQuarantine.ts").read_text(encoding="utf-8")
        self.assertIn("invoke<string>('rowyn_clear_boot_quarantine', { digest, confirmed })", service)
        self.assertIn("inTauriShell()", service)


class ThePortWiresIt(unittest.TestCase):
    def test_module_and_command_are_registered_by_the_port(self):
        text = PORT.read_text(encoding="utf-8")
        self.assertIn("pub(crate) mod rowyn_boot_health_actions;", text)
        self.assertIn(
            "commands::rowyn_boot_health_actions::rowyn_clear_boot_quarantine,", text)


class ExportedQuarantineAction(unittest.TestCase):
    @unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO")
    def test_exported_hub_registers_the_writer_next_to_the_reader(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-quarantine-") as temp:
            root = Path(temp) / "export"
            hub.export_source(_pinned_repo(), root)
            hub.port_source(root)
            web = root / "src/rowyn-hub-web"
            commands = (web / "src-tauri/src/commands/mod.rs").read_text()
            handlers = (web / "src-tauri/src/main.rs").read_text()
            self.assertIn("pub(crate) mod rowyn_boot_health_actions;", commands)
            self.assertIn("commands::rowyn_boot_health_actions::rowyn_clear_boot_quarantine,", handlers)
            self.assertIn("commands::rowyn_boot_health::rowyn_boot_health_snapshot,", handlers)
            bridge = (web / "src-tauri/src/commands/rowyn_boot_health_actions.rs").read_text()
            self.assertIn('SUDO, "-n", HELPER, "clear-quarantine", "-"', bridge)
            service = (web / "src/services/rowynQuarantine.ts").read_text()
            self.assertIn("rowyn_clear_boot_quarantine", service)


if __name__ == "__main__":
    unittest.main()
