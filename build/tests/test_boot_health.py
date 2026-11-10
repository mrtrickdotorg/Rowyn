#!/usr/bin/env python3
"""Boot-health gating: state machine, checks, downgrade guard, wiring."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/rowyn-boot-health"
SERVICE = ROOT / "shared/rowyn-boot-health.service"
TIMER = ROOT / "shared/rowyn-boot-health.timer"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"
POLICY = ROOT / "shared/update-policy.conf"
WATCHER = ROOT / "shared/rowyn-update-watcher"
SAFE_UPGRADE = ROOT / "shared/rowyn-safe-upgrade.sh"

DIGEST = "sha256:" + "a" * 64
STAGED = "sha256:" + "b" * 64

FAKE_BOOTC = """#!/bin/sh
case "$1" in
  status) cat "$BOOTC_STATUS" ;;
  rollback) echo rollback >> "$BOOTC_LOG"; exit "${BOOTC_ROLLBACK_RC:-0}" ;;
  *) exit 1 ;;
esac
"""

FAKE_SYSTEMCTL = """#!/bin/sh
if [ "$1" = "is-active" ]; then
  case ",${SYSTEMCTL_ACTIVE:-}," in
    *",${3:-},"*) exit 0 ;;
  esac
  exit 3
fi
if [ "$1" = "reboot" ]; then echo reboot >> "$REBOOT_LOG"; exit 0; fi
exit 0
"""

ACTIVE = "graphical.target,display-manager.service"


def deployment(digest, version=None, timestamp=None, tag="stable"):
    image = {"image": {"image": f"ghcr.io/mrtrickdotorg/rowyn:{tag}"}, "digest": digest}
    if version is not None:
        image["version"] = version
    if timestamp is not None:
        image["timestamp"] = timestamp
    return {"image": image}


def status_document(staged=None, booted_version="0.2.0",
                    booted_timestamp="2026-11-01T00:00:00Z"):
    document = {"status": {"booted": deployment(
        DIGEST, booted_version, booted_timestamp)}}
    if staged is not None:
        document["status"]["staged"] = staged
    return document


class Sandbox:
    """A temporary image-side environment: fake bootc, fake systemctl, state."""

    def __init__(self, test, active=ACTIVE, status=None, rollback_rc=0):
        temporary = tempfile.TemporaryDirectory(prefix="rowyn-boot-health-")
        test.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.state = self.dir / "state"
        self.bootc_log = self.dir / "bootc.log"
        self.reboot_log = self.dir / "reboot.log"
        self.status_file = self.dir / "status.json"
        self.status_file.write_text(json.dumps(status or status_document()))
        self.os_release = self.dir / "os-release"
        self.os_release.write_text(
            'NAME="Rowyn"\nID=rowyn\nID_LIKE="ubuntu debian"\n', encoding="utf-8")
        modules = self.dir / "modules" / "test-kernel"
        modules.mkdir(parents=True)
        (modules / "modules.dep").write_text("", encoding="utf-8")
        self.bootc = self._script("bootc", FAKE_BOOTC)
        self.systemctl = self._script("systemctl", FAKE_SYSTEMCTL)
        text = HELPER.read_text(encoding="utf-8").replace(
            "os.geteuid() != 0", "False")
        self.helper = self.dir / "rowyn-boot-health"
        self.helper.write_text(text, encoding="utf-8")
        self.active = active
        self.rollback_rc = rollback_rc

    def _script(self, name, body):
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run(self, *args, active=None, rollback_rc=None, extra=None):
        env = dict(os.environ)
        env.update({
            "ROWYN_BOOT_HEALTH_DIR": str(self.state),
            "ROWYN_BOOTC": str(self.bootc),
            "ROWYN_SYSTEMCTL": str(self.systemctl),
            "ROWYN_BOOT_HEALTH_OS_RELEASE": str(self.os_release),
            "ROWYN_BOOT_HEALTH_MODULES_DIR": str(self.dir / "modules"),
            "ROWYN_BOOT_HEALTH_KERNEL_RELEASE": "test-kernel",
            "ROWYN_BOOT_HEALTH_DESKTOP_GRACE": "0",
            "ROWYN_BOOT_HEALTH_REBOOT_CMD": f"{self.systemctl} reboot",
            "ROWYN_BOOT_HEALTH_NO_REBOOT": "1",
            "BOOTC_STATUS": str(self.status_file),
            "BOOTC_LOG": str(self.bootc_log),
            "REBOOT_LOG": str(self.reboot_log),
            "BOOTC_ROLLBACK_RC": str(
                self.rollback_rc if rollback_rc is None else rollback_rc),
            "SYSTEMCTL_ACTIVE": self.active if active is None else active,
        })
        if extra:
            env.update(extra)
        return subprocess.run([sys.executable, str(self.helper), *args],
                              capture_output=True, text=True, timeout=120, env=env)

    def load(self):
        return json.loads((self.state / "boot-health.json").read_text(encoding="utf-8"))

    def rebooted(self):
        return self.reboot_log.exists()

    def rollbacks(self):
        if not self.bootc_log.exists():
            return 0
        return self.bootc_log.read_text(encoding="utf-8").count("rollback")


class BootRecordingTests(unittest.TestCase):
    def test_healthy_boot_is_recorded_known_good(self):
        sb = Sandbox(self)
        result = sb.run("boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertIn(DIGEST, state["known_good"])
        self.assertEqual(state["failures"], {})
        self.assertTrue(state["last_boot"]["healthy"])
        self.assertEqual(state["last_boot"]["digest"], DIGEST)

    def test_known_good_image_is_never_judged_again(self):
        sb = Sandbox(self)
        sb.run("boot")
        for _ in range(4):
            result = sb.run("boot", active="")
            self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertNotIn(DIGEST, state["failures"])
        self.assertNotIn(DIGEST, state["quarantined"])
        self.assertTrue(state["last_boot"]["healthy"])
        self.assertEqual(sb.rollbacks(), 0)

    def test_three_unhealthy_boots_quarantine_and_roll_back(self):
        sb = Sandbox(self, active="")
        for _ in range(2):
            sb.run("boot")
        state = sb.load()
        self.assertNotIn(DIGEST, state["quarantined"])
        self.assertEqual(state["failures"][DIGEST], 2)
        self.assertEqual(sb.rollbacks(), 0)
        result = sb.run("boot", extra={"ROWYN_BOOT_HEALTH_NO_REBOOT": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertEqual(state["quarantined"][DIGEST]["reason"], "unhealthy-boot")
        self.assertEqual(sb.rollbacks(), 1)
        self.assertTrue(sb.rebooted())

    def test_rollback_is_attempted_once_even_after_reboot(self):
        sb = Sandbox(self, active="")
        for _ in range(4):
            sb.run("boot")
        self.assertEqual(sb.rollbacks(), 1)

    def test_failed_rollback_is_recorded_without_reboot(self):
        sb = Sandbox(self, active="", rollback_rc=1)
        for _ in range(3):
            sb.run("boot", extra={"ROWYN_BOOT_HEALTH_NO_REBOOT": ""})
        state = sb.load()
        self.assertIn("exited 1", state["rollback"][DIGEST]["last_error"])
        self.assertFalse(sb.rebooted())

    def test_missing_bootc_status_does_not_count_as_failure(self):
        sb = Sandbox(self)
        sb.status_file.write_text("not json", encoding="utf-8")
        for _ in range(4):
            result = sb.run("boot")
            self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertEqual(state["failures"], {})
        self.assertNotIn(DIGEST, state["quarantined"])
        self.assertIsNone(state["last_boot"]["healthy"])

    def test_boot_without_digest_is_unjudged(self):
        sb = Sandbox(self, status={"status": {"booted": {"image": {}}}})
        result = sb.run("boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertEqual(state["failures"], {})
        self.assertIsNone(state["last_boot"]["healthy"])

    def test_corrupt_state_is_preserved(self):
        sb = Sandbox(self)
        sb.state.mkdir(parents=True)
        (sb.state / "boot-health.json").write_text("{not json", encoding="utf-8")
        result = sb.run("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("boot-health.json.corrupt", result.stderr)
        preserved = sb.state / "boot-health.json.corrupt"
        self.assertEqual(preserved.read_text(encoding="utf-8"), "{not json")

    def test_state_file_is_world_readable_and_leaves_no_temporary_files(self):
        sb = Sandbox(self)
        sb.run("boot")
        path = sb.state / "boot-health.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        leftovers = [p.name for p in sb.state.iterdir()
                     if p.name.startswith(".boot-health-")]
        self.assertEqual(leftovers, [])


class CheckTests(unittest.TestCase):
    def test_check_is_read_only_and_reports_every_check(self):
        sb = Sandbox(self, active="")
        result = sb.run("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertFalse(document["healthy"])
        self.assertEqual([check["name"] for check in document["checks"]],
                         ["identity", "kernel-modules", "desktop"])
        self.assertFalse((sb.state / "boot-health.json").exists())

    def test_identity_mismatch_fails(self):
        sb = Sandbox(self)
        sb.os_release.write_text('NAME="Other"\nID=other\n', encoding="utf-8")
        result = sb.run("check")
        checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
        self.assertFalse(checks["identity"]["ok"])

    def test_missing_kernel_tree_fails(self):
        sb = Sandbox(self)
        (sb.dir / "modules" / "test-kernel" / "modules.dep").unlink()
        (sb.dir / "modules" / "test-kernel").rmdir()
        result = sb.run("check")
        checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
        self.assertFalse(checks["kernel-modules"]["ok"])
        self.assertTrue(checks["identity"]["ok"])


class QuarantineCommandTests(unittest.TestCase):
    def test_quarantine_then_clear(self):
        sb = Sandbox(self)
        result = sb.run("quarantine", DIGEST, "manual")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(DIGEST, sb.load()["quarantined"])
        result = sb.run("clear-quarantine", DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = sb.load()
        self.assertNotIn(DIGEST, state["quarantined"])
        self.assertNotIn(DIGEST, state["failures"])

    def test_rejects_bad_digest_and_unknown_reason(self):
        sb = Sandbox(self)
        self.assertEqual(sb.run("quarantine", "sha256:zz", "manual").returncode, 2)
        self.assertEqual(sb.run("quarantine", DIGEST, "because").returncode, 2)
        self.assertEqual(sb.run("clear-quarantine", "nope").returncode, 2)

    def test_clearing_an_unquarantined_digest_fails(self):
        sb = Sandbox(self)
        self.assertEqual(sb.run("clear-quarantine", DIGEST).returncode, 1)


class DowngradeTests(unittest.TestCase):
    def staged(self, version=None, timestamp=None, booted_version="0.2.0",
               booted_timestamp="2026-11-01T00:00:00Z"):
        return status_document(deployment(STAGED, version, timestamp),
                               booted_version, booted_timestamp)

    def test_older_staged_version_is_refused_and_quarantined(self):
        sb = Sandbox(self, status=self.staged(version="0.1.0"))
        result = sb.run("refuse-downgrade")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(sb.load()["quarantined"][STAGED]["reason"], "downgrade")

    def test_newer_and_equal_versions_pass(self):
        for version in ("0.3.0", "0.2.0", "0.2"):
            with self.subTest(version=version):
                sb = Sandbox(self, status=self.staged(version=version))
                self.assertEqual(sb.run("refuse-downgrade").returncode, 0)

    def test_allow_flag_warns_without_recording(self):
        sb = Sandbox(self, status=self.staged(version="0.1.0"))
        result = sb.run("refuse-downgrade", "--allow")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((sb.state / "boot-health.json").exists())

    def test_timestamps_order_when_versions_are_missing(self):
        sb = Sandbox(self, status=self.staged(
            timestamp="2026-10-01T00:00:00Z", booted_version=None))
        self.assertEqual(sb.run("refuse-downgrade").returncode, 3)

    def test_unknown_ordering_passes(self):
        sb = Sandbox(self, status=self.staged(
            booted_version=None, booted_timestamp=None))
        self.assertEqual(sb.run("refuse-downgrade").returncode, 0)

    def test_no_staged_deployment_passes(self):
        sb = Sandbox(self, status=status_document())
        self.assertEqual(sb.run("refuse-downgrade").returncode, 0)


class WiringTests(unittest.TestCase):
    def test_helper_is_executable_python(self):
        self.assertTrue(os.access(HELPER, os.X_OK))
        self.assertTrue(HELPER.read_text(encoding="utf-8").startswith(
            "#!/usr/bin/env python3"))

    def test_service_and_timer_contract(self):
        service = SERVICE.read_text(encoding="utf-8").splitlines()
        for needed in ("Type=oneshot",
                       "ExecStart=/usr/libexec/rowyn-boot-health boot",
                       "StateDirectory=rowyn", "StateDirectoryMode=0755",
                       "TimeoutStartSec=300", "Nice=10",
                       "IOSchedulingClass=idle"):
            self.assertIn(needed, service)
        directives = [line.split("=")[0] for line in service
                      if line and not line.startswith("#") and "=" in line]
        for banned in ("ProtectSystem", "ProtectHome", "PrivateTmp",
                       "PrivateMounts", "ReadOnlyPaths"):
            self.assertNotIn(banned, directives)
        timer = TIMER.read_text(encoding="utf-8").splitlines()
        for needed in ("OnBootSec=90s", "Unit=rowyn-boot-health.service",
                       "WantedBy=timers.target"):
            self.assertIn(needed, timer)
        self.assertNotIn("Persistent=true", timer)

    def test_containerfile_installs_and_enables_boot_health(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        for needed in (
            "install -Dm755 /ctx/shared/rowyn-boot-health /usr/libexec/rowyn-boot-health",
            "install -Dm644 /ctx/shared/rowyn-boot-health.service "
            "/usr/lib/systemd/system/rowyn-boot-health.service",
            "install -Dm644 /ctx/shared/rowyn-boot-health.timer "
            "/usr/lib/systemd/system/rowyn-boot-health.timer",
            "systemctl enable rowyn-boot-health.timer",
        ):
            self.assertIn(needed, text)

    def test_image_smoke_checks_boot_health(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for needed in ("test -x /usr/libexec/rowyn-boot-health",
                       "test -s /usr/lib/systemd/system/rowyn-boot-health.timer",
                       "test -e /etc/systemd/system/timers.target.wants/"
                       "rowyn-boot-health.timer"):
            self.assertIn(needed, text)

    def test_policy_declares_channel_and_downgrade(self):
        lines = POLICY.read_text(encoding="utf-8").splitlines()
        self.assertIn("Channel=stable", lines)
        self.assertIn("AllowDowngrade=no", lines)

    def test_safe_upgrade_refuses_downgrades_through_boot_health(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn("/usr/libexec/rowyn-boot-health", text)
        self.assertIn("refuse-downgrade", text)
        self.assertIn("AllowDowngrade", text)


class WatcherPolicyTests(unittest.TestCase):
    def run_watcher(self, policy_text):
        with tempfile.TemporaryDirectory(prefix="rowyn-watcher-") as temp:
            directory = Path(temp)
            etc = directory / "etc.conf"
            etc.write_text(policy_text, encoding="utf-8")
            stub = directory / "safe-upgrade"
            stub.write_text(
                '#!/bin/sh\necho "DGR=${ROWYN_ALLOW_DOWNGRADE:-}" >> "$STUB_LOG"\necho ROWYN_UPDATE_RESULT outcome=current reason=no_update\n',
                encoding="utf-8")
            stub.chmod(0o755)
            log = directory / "log"
            env = dict(os.environ, ROWYN_POLICY_ETC=str(etc),
                       ROWYN_POLICY_DEFAULT=str(directory / "absent.conf"),
                       ROWYN_SAFE_UPGRADE=str(stub), STUB_LOG=str(log))
            result = subprocess.run(["bash", str(WATCHER)], capture_output=True,
                                    text=True, timeout=30, env=env, cwd=directory)
            ran = log.read_text(encoding="utf-8") if log.exists() else ""
            return result, ran

    def test_unknown_channel_is_refused(self):
        result, ran = self.run_watcher("Channel=cachy\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("refusing to auto-stage", result.stdout)
        self.assertEqual(ran, "")

    def test_testing_channel_is_accepted(self):
        result, ran = self.run_watcher("Channel=testing\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("refusing to auto-stage", result.stdout)
        self.assertIn("DGR=", ran)

    def test_downgrade_permission_is_exported(self):
        result, ran = self.run_watcher("AllowDowngrade=yes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DGR=1", ran)

    def test_new_keys_stay_fail_closed(self):
        for text in ("Channel=stable extra\n", "Channel=Stable\n",
                     "AllowDowngrade=maybe\n", "Channel=\n"):
            with self.subTest(text=text):
                result, ran = self.run_watcher(text)
                self.assertEqual(ran, "")
                self.assertIn("refusing to auto-stage", result.stdout)


if __name__ == "__main__":
    unittest.main()
