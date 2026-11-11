#!/usr/bin/env python3
"""Automatic-update safety: AC/metered gates, policy parser, unit files."""
from __future__ import annotations

import os
import json
import signal
import time
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SAFE_UPGRADE = ROOT / "shared/rowyn-safe-upgrade.sh"
WATCHER = ROOT / "shared/rowyn-update-watcher"
SERVICE = ROOT / "shared/rowyn-update-watcher.service"
TIMER = ROOT / "shared/rowyn-update-watcher.timer"
POLICY = ROOT / "shared/update-policy.conf"

FAKE_BOOTC = """#!/bin/sh
case "$1" in
switch) echo "$@" >> "$BOOTC_CALLS"; if [ -n "${BOOTC_FAIL_MESSAGE:-}" ]; then echo "$BOOTC_FAIL_MESSAGE" >&2; exit 1; fi; echo "Staged update" ;;
upgrade) echo "$@" >> "$BOOTC_CALLS"; echo "Staged update" ;;
status) if [ "${BOOTC_NO_UPDATE:-0}" = 1 ]; then printf '{"status":{"booted":{"image":{"image":{"image":"%s"}}},"staged":null}}\\n' "$BOOTED_IMAGE_REF"; else printf '{"status":{"booted":{"image":{"image":{"image":"%s"}}},"staged":{"image":{}}}}\\n' "$BOOTED_IMAGE_REF"; fi ;;
esac
"""


class WatcherStatusTests(unittest.TestCase):
    def test_status_helper_rejects_boolean_timestamp_and_extra_args(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "status"
            env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(d))
            helper = ROOT / "shared/rowyn-update-watcher-status"
            subprocess.run([str(helper), "start", "100"], env=env, check=True)
            doc = {"schema_version": 1, "started_at": True, "finished_at": None, "outcome": "running",
                   "reason_code": "attempt_started", "last_success_at": None, "last_staged_at": None}
            (d / "status.json").write_text(json.dumps(doc))
            self.assertNotEqual(subprocess.run([str(helper), "start", "110"], env=env).returncode, 0)
            self.assertNotEqual(subprocess.run([str(helper), "start", "110", "extra"], env=env).returncode, 0)

    def test_watcher_fixture_records_authoritative_outcomes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            helper = ROOT / "shared/rowyn-update-watcher-status"
            policy = root / "policy"
            policy.write_text("AutoStage=yes\n")
            fake = root / "safe"
            fake.write_text("#!/bin/bash\necho 'ROWYN_UPDATE_RESULT outcome=deferred reason=battery'\nexit 0\n")
            fake.chmod(0o755)
            env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(root / "status"), ROWYN_UPDATE_STATUS_HELPER=str(helper),
                       ROWYN_POLICY_ETC=str(policy), ROWYN_POLICY_DEFAULT=str(root / "missing"), ROWYN_SAFE_UPGRADE=str(fake))
            result = subprocess.run([str(WATCHER)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            doc = json.loads((root / "status/status.json").read_text())
            self.assertEqual((doc["outcome"], doc["reason_code"]), ("deferred", "battery"))

    def test_watcher_fixture_persists_success_failure_and_malformed_marker_outcomes(self):
        cases = [
            ("echo 'ROWYN_UPDATE_RESULT outcome=current reason=no_update'; exit 0", ("current", "no_update"), 0),
            ("echo 'ROWYN_UPDATE_RESULT outcome=staged reason=update_staged'; exit 0", ("staged", "update_staged"), 0),
            ("echo 'ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io'; exit 1", ("failed", "runtime_auth_io"), 1),
            ("echo 'ROWYN_UPDATE_RESULT outcome=staged reason=update_staged'; exit 1", ("failed", "upgrade_failed"), 1),
            ("echo 'ROWYN_UPDATE_RESULT outcome=current reason=no_update'; echo 'ROWYN_UPDATE_RESULT malformed'; exit 0", ("failed", "unknown_failure"), 1),
        ]
        for script, expected, expected_rc in cases:
            with self.subTest(expected=expected, script=script):
                with tempfile.TemporaryDirectory() as t:
                    root = Path(t)
                    helper = ROOT / "shared/rowyn-update-watcher-status"
                    policy = root / "policy"
                    policy.write_text("AutoStage=yes\n")
                    fake = root / "safe"
                    fake.write_text("#!/bin/bash\n" + script + "\n")
                    fake.chmod(0o755)
                    env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(root / "status"), ROWYN_UPDATE_STATUS_HELPER=str(helper),
                               ROWYN_POLICY_ETC=str(policy), ROWYN_POLICY_DEFAULT=str(root / "missing"), ROWYN_SAFE_UPGRADE=str(fake))
                    result = subprocess.run([str(WATCHER)], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected_rc, result.stdout + result.stderr)
                    doc = json.loads((root / "status/status.json").read_text())
                    self.assertEqual((doc["outcome"], doc["reason_code"]), expected)

    def test_signal_keeps_interrupted_status_across_normal_result_handling(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            policy = root / "policy"
            policy.write_text("AutoStage=yes\n")
            fake = root / "safe"
            fake.write_text("#!/bin/bash\nsleep 30\n")
            fake.chmod(0o755)
            env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(root / "status"),
                       ROWYN_UPDATE_STATUS_HELPER=str(ROOT / "shared/rowyn-update-watcher-status"),
                       ROWYN_POLICY_ETC=str(policy), ROWYN_POLICY_DEFAULT=str(root / "missing"),
                       ROWYN_SAFE_UPGRADE=str(fake))
            proc = subprocess.Popen([str(WATCHER)], env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True)
            try:
                time.sleep(0.5)
                os.killpg(proc.pid, signal.SIGTERM)
                output, _ = proc.communicate(timeout=12)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=3)
            doc = json.loads((root / "status/status.json").read_text())
            self.assertEqual((doc["outcome"], doc["reason_code"]), ("interrupted", "cancelled"), output)

    def test_status_helper_writes_bounded_attempt_and_preserves_last_success(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "status"
            env = dict(os.environ, ROWYN_UPDATE_STATUS_DIR=str(d))
            helper = ROOT / "shared/rowyn-update-watcher-status"
            first = subprocess.run([str(helper), "start", "100"], env=env, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            finish = subprocess.run([str(helper), "finish", "current", "no_update", "110"], env=env, capture_output=True)
            self.assertEqual(finish.returncode, 0, finish.stderr)
            import json
            doc = json.loads((d / "status.json").read_text())
            self.assertEqual(doc, {"schema_version": 1, "started_at": 100, "finished_at": 110,
                                   "outcome": "current", "reason_code": "no_update",
                                   "last_success_at": 110, "last_staged_at": None})
            subprocess.run([str(helper), "start", "120"], env=env, check=True)
            subprocess.run([str(helper), "finish", "failed", "runtime_auth_io", "130"], env=env, check=True)
            doc = json.loads((d / "status.json").read_text())
            self.assertEqual(doc["last_success_at"], 110)
            self.assertEqual(doc["outcome"], "failed")
            self.assertEqual((d / "status.json").stat().st_mode & 0o777, 0o644)

    def test_watcher_calls_status_helper_with_classified_result(self):
        text = WATCHER.read_text()
        self.assertIn('"$STATUS_HELPER" start', text)
        self.assertIn('"$STATUS_HELPER" finish', text)


class Sandbox:
    def __init__(self, test: unittest.TestCase):
        self.tmp = tempfile.TemporaryDirectory(prefix="rowyn-safety-")
        test.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.calls = self.dir / "bootc-calls"
        self.power = self.dir / "power"
        self.power.mkdir()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        bootc = self.dir / "bootc"
        bootc.write_text(FAKE_BOOTC)
        bootc.chmod(0o755)
        fin = self.dir / "finalize"
        fin.write_text("#!/bin/sh\nexit 0\n")
        fin.chmod(0o755)
        # Digest resolution is unavailable in the sandbox (the fake
        # status carries no imageDigest, and this skopeo stub refuses),
        # so the digest-equal switch path can never trigger here.
        skopeo = self.dir / "skopeo"
        skopeo.write_text("#!/bin/sh\nexit 1\n")
        skopeo.chmod(0o755)
        text = SAFE_UPGRADE.read_text()
        text = text.replace("if (( EUID != 0 )); then", "if false; then")
        text = text.replace("/usr/bin/bootc", str(bootc))
        text = text.replace("/usr/bin/skopeo", str(skopeo))
        text = text.replace("/run/rowyn-bootc.lock", str(self.dir / "lock"))
        text = text.replace("/usr/libexec/rowyn-finalize-staged", str(fin))
        self.script = self.dir / "rowyn-safe-upgrade"
        self.script.write_text(text)

    def supply(self, name, type_, **files):
        d = self.power / name
        d.mkdir()
        (d / "type").write_text(type_ + "\n")
        for k, v in files.items():
            (d / k).write_text(v + "\n")

    def nmcli(self, output):
        f = self.bin / "nmcli"
        f.write_text(f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\n")
        f.chmod(0o755)

    def run(self, auto=True, **env):
        e = dict(os.environ)
        e["PATH"] = f"{self.bin}:{e['PATH']}"
        e["ROWYN_POWER_SUPPLY_DIR"] = str(self.power)
        e["BOOTC_CALLS"] = str(self.calls)
        e["BOOTED_IMAGE_REF"] = "ghcr.io/kyth-os/rowyn:stable"
        for k in ("ROWYN_UPDATE_AUTO", "ROWYN_ALLOW_BATTERY", "ROWYN_ALLOW_METERED"):
            e.pop(k, None)
        if auto:
            e["ROWYN_UPDATE_AUTO"] = "1"
        e.update(env)
        return subprocess.run(["bash", str(self.script)], capture_output=True,
                              text=True, timeout=60, env=e)

    def upgraded(self):
        return self.calls.exists()


class AutoGateTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(self)

    def battery(self, ac_online):
        self.sb.supply("BAT0", "Battery", status="Discharging" if not ac_online else "Charging")
        self.sb.supply("AC", "Mains", online="1" if ac_online else "0")

    def test_battery_defers_with_exit_zero(self):
        self.battery(False)
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Deferred: running on battery power; will retry later.", r.stdout)
        self.assertFalse(self.sb.upgraded())
        self.assertIn("ROWYN_UPDATE_RESULT outcome=deferred reason=battery", r.stdout)

    def test_ac_proceeds(self):
        self.battery(True)
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Deferred", r.stdout)
        self.assertTrue(self.sb.upgraded())
        self.assertIn("ghcr.io/mrtrickdotorg/rowyn:stable", self.sb.calls.read_text())
        self.assertIn("ROWYN_UPDATE_RESULT outcome=staged reason=update_staged", r.stdout)

    def test_no_update_is_marked_current(self):
        r = self.sb.run(BOOTC_NO_UPDATE="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ROWYN_UPDATE_RESULT outcome=current reason=no_update", r.stdout)
        self.assertNotIn("outcome=staged", r.stdout)

    def test_runtime_auth_failure_uses_fixed_reason_without_exposing_details(self):
        r = self.sb.run(BOOTC_FAIL_MESSAGE="runtime auth file access failed")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ROWYN_UPDATE_RESULT outcome=failed reason=runtime_auth_io", r.stdout)
        self.assertNotIn("credential", r.stdout.lower())

    def test_destination_booted_image_uses_upgrade_without_switching(self):
        r = self.sb.run(BOOTED_IMAGE_REF="ghcr.io/mrtrickdotorg/rowyn:stable")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.sb.calls.read_text()
        self.assertTrue(calls.startswith("upgrade "), calls)
        self.assertNotIn("switch", calls)

    def test_unrecognized_booted_image_fails_closed(self):
        r = self.sb.run(BOOTED_IMAGE_REF="ghcr.io/attacker/rowyn:stable")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.sb.upgraded())

    def test_desktop_without_battery_proceeds(self):
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sb.upgraded())

    def test_missing_power_dir_proceeds(self):
        r = self.sb.run(ROWYN_POWER_SUPPLY_DIR=str(self.sb.dir / "nope"))
        self.assertTrue(self.sb.upgraded(), r.stdout + r.stderr)

    def test_battery_override(self):
        self.battery(False)
        r = self.sb.run(ROWYN_ALLOW_BATTERY="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sb.upgraded())

    def test_manual_run_is_never_gated(self):
        self.battery(False)
        self.sb.nmcli("yes")
        r = self.sb.run(auto=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sb.upgraded())

    def test_metered_defers(self):
        for value in ("yes", "guess-yes", "no\nyes", "3"):
            with self.subTest(value=value):
                sb = Sandbox(self)
                sb.nmcli(value)
                r = sb.run()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("Deferred: metered connection", r.stdout)
                self.assertFalse(sb.upgraded())

    def test_not_metered_proceeds(self):
        for value in ("no", "guess-no", "unknown", ""):
            with self.subTest(value=value):
                sb = Sandbox(self)
                sb.nmcli(value)
                r = sb.run()
                self.assertTrue(sb.upgraded(), r.stdout + r.stderr)

    def test_metered_override(self):
        self.sb.nmcli("yes")
        r = self.sb.run(ROWYN_ALLOW_METERED="1")
        self.assertTrue(self.sb.upgraded(), r.stdout + r.stderr)

    def test_failing_nmcli_allows(self):
        f = self.sb.bin / "nmcli"
        f.write_text("#!/bin/sh\nexit 3\n")
        f.chmod(0o755)
        r = self.sb.run()
        self.assertTrue(self.sb.upgraded(), r.stdout + r.stderr)

    def test_lock_held_defers_in_auto_mode_only(self):
        lock = self.sb.dir / "lock"
        holder = subprocess.Popen(["flock", str(lock), "sleep", "5"])
        self.addCleanup(holder.wait)
        import time
        time.sleep(0.5)
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Deferred: another bootc upgrade", r.stdout)
        r = self.sb.run(auto=False)
        self.assertEqual(r.returncode, 1)

    def test_arguments_still_a_usage_error(self):
        r = subprocess.run(["bash", str(self.sb.script), "x"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 64)


class WatcherPolicyTests(unittest.TestCase):
    def run_watcher(self, policy_text=None, default_text=None):
        with tempfile.TemporaryDirectory(prefix="rowyn-watcher-") as t:
            t = Path(t)
            etc = t / "etc.conf"
            dflt = t / "default.conf"
            if policy_text is not None:
                etc.write_text(policy_text)
            if default_text is not None:
                dflt.write_text(default_text)
            stub = t / "safe-upgrade"
            stub.write_text('#!/bin/sh\necho "ran AUTO=$ROWYN_UPDATE_AUTO '
                            'BAT=$ROWYN_ALLOW_BATTERY MET=$ROWYN_ALLOW_METERED"'
                            ' >> "$STUB_LOG"\necho ROWYN_UPDATE_RESULT outcome=current reason=no_update\n')
            stub.chmod(0o755)
            log = t / "log"
            env = dict(os.environ, ROWYN_POLICY_ETC=str(etc),
                       ROWYN_POLICY_DEFAULT=str(dflt),
                       ROWYN_SAFE_UPGRADE=str(stub), STUB_LOG=str(log))
            r = subprocess.run(["bash", str(WATCHER)], capture_output=True,
                               text=True, timeout=30, env=env, cwd=t)
            ran = log.read_text() if log.exists() else ""
            return r, ran, t

    def test_default_policy_runs_with_gates(self):
        r, ran, _ = self.run_watcher(default_text=POLICY.read_text())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AUTO=1 BAT= MET=", ran)

    def test_autostage_no_skips(self):
        r, ran, _ = self.run_watcher(policy_text="AutoStage=no\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(ran, "")

    def test_overrides_exported(self):
        r, ran, _ = self.run_watcher(
            policy_text="# c\nAllowOnBattery=yes\n AllowMetered = yes \n")
        self.assertIn("BAT=1 MET=1", ran)

    def test_etc_wins_over_default(self):
        r, ran, _ = self.run_watcher(policy_text="AutoStage=no\n",
                                     default_text="AutoStage=yes\n")
        self.assertEqual(ran, "")

    def test_fail_closed(self):
        bad = ["AutoStagee=yes\n", "AutoStage=maybe\n", "AutoStage yes\n",
               "AutoStage=yes extra\n", "AutoStage=yes\nAllowMetered=1\n",
               "garbage\n"]
        for text in bad:
            with self.subTest(text=text):
                r, ran, _ = self.run_watcher(policy_text=text)
                self.assertEqual(r.returncode, 0)
                self.assertEqual(ran, "")
                self.assertIn("refusing to auto-stage", r.stdout)

    def test_injection_is_not_executed(self):
        with tempfile.TemporaryDirectory() as t:
            marker = Path(t) / "pwned"
            for text in (f"AutoStage=yes; touch {marker}\n",
                         f"AutoStage=$(touch {marker})\n",
                         f"AutoStage=yes\n`touch {marker}`\n"):
                r, ran, _ = self.run_watcher(policy_text=text)
                self.assertEqual(ran, "")
                self.assertFalse(marker.exists())


class StaticTests(unittest.TestCase):
    def test_shell_syntax(self):
        for f in (SAFE_UPGRADE, WATCHER):
            subprocess.run(["bash", "-n", str(f)], check=True)

    def test_service_unit(self):
        text = SERVICE.read_text()
        for needed in ("Type=oneshot", "ExecStart=/usr/libexec/rowyn-update-watcher",
                       "Nice=10", "IOSchedulingClass=idle", "CPUQuota=50%",
                       "MemoryHigh=512M", "MemoryMax=1G", "TimeoutStartSec=2400",
                       "LockPersonality=yes", "RestrictRealtime=yes"):
            self.assertIn(needed, text.splitlines())
        # systemd's SUID/SGID filter unconditionally denies openat2; bootc's
        # capability-safe runtime auth reader cannot fall back to openat.
        self.assertNotIn("RestrictSUIDSGID=yes", text.splitlines())
        self.assertIn("openat2", text)
        directives = [l.split("=")[0] for l in text.splitlines()
                      if l and not l.startswith("#") and "=" in l]
        for banned in ("ProtectSystem", "ProtectHome", "PrivateTmp",
                       "ConditionACPower", "PrivateMounts", "ReadOnlyPaths"):
            self.assertNotIn(banned, directives)

    def test_timer_unit(self):
        lines = TIMER.read_text().splitlines()
        for needed in ("OnBootSec=20min", "OnUnitActiveSec=6h",
                       "RandomizedDelaySec=30min", "Persistent=true",
                       "WantedBy=timers.target"):
            self.assertIn(needed, lines)

    def test_default_policy_is_safe_and_executable_bit(self):
        text = POLICY.read_text()
        self.assertIn("AutoStage=yes", text.splitlines())
        self.assertIn("AllowOnBattery=no", text.splitlines())
        self.assertIn("AllowMetered=no", text.splitlines())
        self.assertTrue(os.access(WATCHER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
