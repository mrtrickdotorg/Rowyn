#!/usr/bin/env python3
"""Rowyn's update helpers must match its composefs + systemd-boot layout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SAFE_UPGRADE = ROOT / "shared/rowyn-safe-upgrade.sh"
FINALIZE = ROOT / "shared/rowyn-finalize-staged.sh"


class SafeUpgradeTests(unittest.TestCase):
    def test_staging_targets_the_policy_channel_of_the_destination_image(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn("readonly IMAGE_NAME=ghcr.io/mrtrickdotorg/rowyn", text)
        self.assertIn('update_ref="$IMAGE_NAME:$policy_channel"', text)
        self.assertIn('switch) args=(switch "$update_ref")', text)
        self.assertIn('upgrade) args=(upgrade)', text)
        self.assertIn("stable|testing", text)
        self.assertIn("mrtrickdotorg/rowyn", text)
        self.assertNotIn("/usr/bin/bootc upgrade --progress-fd", text)

    def test_manual_switch_targets_the_destination_channels(self):
        text = (ROOT / "shared/rowyn-bootc-guard.sh").read_text(encoding="utf-8")
        self.assertIn("readonly STABLE_REF=ghcr.io/mrtrickdotorg/rowyn:stable", text)
        self.assertIn("readonly TESTING_REF=ghcr.io/mrtrickdotorg/rowyn:testing", text)
        self.assertIn('bootc switch "$TARGET_REF"', text)
        self.assertIn('/usr/bin/bootc upgrade', text)

    def test_digest_equal_switch_is_handled_in_both_switch_paths(self):
        # The guard (manual channel switch) and the stager (watcher /
        # Hub staging) share the verified digest comparison: the
        # booted digest from bootc status, the target tag's digest
        # from skopeo inspect, never bootc's error wording.
        for script in ("rowyn-bootc-guard.sh", "rowyn-safe-upgrade.sh"):
            text = (ROOT / "shared" / script).read_text(encoding="utf-8")
            self.assertIn("same_image_as_booted", text)
            self.assertIn('"imageDigest"', text)
            self.assertIn("/usr/bin/skopeo inspect", text)
            # The decision never pattern-matches bootc's refusal text.
            self.assertNotIn("same fs-verity digest as the existing", text)

    def test_only_the_physical_root_space_is_checked(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn('check_free /sysroot "$SYSROOT_FREE_MIN_BYTES"', text)
        # /boot is an empty directory on the read-only composefs root, so a
        # /boot free-space check always failed with "0 MiB free".
        self.assertNotIn("check_free /boot", text)
        self.assertNotIn("BOOT_FREE_MIN_BYTES", text)


class BootcGuardOperationTests(unittest.TestCase):
    BOOTED_DIGEST = "sha256:" + "a" * 64
    OTHER_DIGEST = "sha256:" + "b" * 64

    FAKE_BOOTC = """#!/bin/sh
case "$1" in
status)
    printf '%s' "$BOOTED_STATUS_JSON"
    ;;
switch|upgrade)
    printf '%s' "$*" >> "$BOOTC_CALLS"
    if [ "${BOOTC_SWITCH_FAILS:-0}" = 1 ]; then
        echo "error: Switching: Composefs Switching: Target image has the same fs-verity digest as the existing Some(Booted) deployment." >&2
        exit 1
    fi
    ;;
*) exit 9 ;;
esac
"""

    FAKE_SKOPEO = """#!/bin/sh
if [ "${SKOPEO_FAIL_FIRST:-0}" = 1 ]; then
    count=0
    if [ -f "$SKOPEO_COUNTER" ]; then count=$(cat "$SKOPEO_COUNTER"); fi
    count=$((count + 1))
    printf '%s' "$count" > "$SKOPEO_COUNTER"
    if [ "$count" = 1 ]; then exit 1; fi
fi
printf '%s' "$TARGET_IMAGE_DIGEST"
"""

    def run_guard(self, image_ref, operation="switch-latest", *,
                  target_digest=OTHER_DIGEST, switch_fails=False,
                  skopeo_fail_first=False):
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-guard-") as temp:
            base = Path(temp)
            bootc = base / "bootc"
            bootc.write_text(self.FAKE_BOOTC, encoding="utf-8")
            bootc.chmod(0o755)
            skopeo = base / "skopeo"
            skopeo.write_text(self.FAKE_SKOPEO, encoding="utf-8")
            skopeo.chmod(0o755)
            finalize = base / "finalize"
            finalize.write_text("#!/bin/sh\necho \"$*\" >> \"$FINALIZE_CALLS\"\nexit 0\n",
                                encoding="utf-8")
            finalize.chmod(0o755)
            calls = base / "bootc-calls"
            finalizer_calls = base / "finalizer-calls"
            policy_etc = base / "etc/update-policy.conf"
            policy_default = base / "default/update-policy.conf"
            policy_default.parent.mkdir(parents=True)
            policy_default.write_text("AutoStage=yes\nChannel=stable\n",
                                      encoding="utf-8")
            text = (ROOT / "shared/rowyn-bootc-guard.sh").read_text(encoding="utf-8")
            text = text.replace("/usr/bin/bootc", str(bootc))
            text = text.replace("/usr/bin/skopeo", str(skopeo))
            text = text.replace("/run/rowyn-bootc.lock", str(base / "lock"))
            text = text.replace("/usr/libexec/rowyn-finalize-staged", str(finalize))
            script = base / "guard"
            script.write_text(text, encoding="utf-8")
            status_json = json.dumps({"status": {"booted": {"image": {
                "image": {"image": image_ref},
                "imageDigest": self.BOOTED_DIGEST}}}})
            env = dict(os.environ, BOOTED_STATUS_JSON=status_json,
                       TARGET_IMAGE_DIGEST=target_digest,
                       SKOPEO_COUNTER=str(base / "skopeo-counter"),
                       BOOTC_CALLS=str(calls), FINALIZE_CALLS=str(finalizer_calls),
                       ROWYN_POLICY_ETC=str(policy_etc),
                       ROWYN_POLICY_DEFAULT=str(policy_default))
            if switch_fails:
                env["BOOTC_SWITCH_FAILS"] = "1"
            if skopeo_fail_first:
                env["SKOPEO_FAIL_FIRST"] = "1"
            result = subprocess.run(["bash", str(script), operation],
                                    capture_output=True, text=True, timeout=30, env=env)
            recorded = (policy_etc.read_text(encoding="utf-8")
                        if policy_etc.exists() else "")
            return result, calls.read_text(encoding="utf-8") if calls.exists() else "", recorded

    def test_old_registry_booted_image_switches_to_destination(self):
        result, calls, _ = self.run_guard("ghcr.io/kyth-os/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:stable", calls)
        self.assertNotIn("upgrade", calls)

    def test_destination_booted_image_uses_upgrade_to_avoid_switch_collision(self):
        result, calls, _ = self.run_guard("ghcr.io/mrtrickdotorg/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.strip(), "upgrade")
        self.assertNotIn("switch", calls)

    def test_unrecognized_booted_image_fails_closed(self):
        result, calls, _ = self.run_guard("ghcr.io/attacker/rowyn:stable")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "")

    def test_switch_testing_from_stable_switches_and_records_the_channel(self):
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "switch-testing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:testing", calls)
        self.assertIn("Channel=testing", recorded)
        self.assertIn("AutoStage=yes", recorded)

    def test_switch_testing_from_the_old_registry(self):
        result, calls, _ = self.run_guard(
            "ghcr.io/kyth-os/rowyn:stable", "switch-testing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:testing", calls)

    def test_switch_testing_on_testing_upgrades_in_place(self):
        result, calls, _ = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:testing", "switch-testing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.strip(), "upgrade")

    def test_switch_latest_from_testing_returns_to_stable(self):
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:testing", "switch-latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:stable", calls)
        self.assertIn("Channel=stable", recorded)

    def test_switch_testing_with_equal_digests_records_channel_without_bootc(self):
        # Both channels are promoted from one verified digest, so the
        # testing tag resolves to the booted image itself: bootc would
        # refuse the switch, and the guard must complete it as a
        # channel change without invoking bootc at all.
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "switch-testing",
            target_digest=self.BOOTED_DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("Channel=testing", recorded)
        self.assertIn("AutoStage=yes", recorded)
        self.assertIn("Already running this build; channel set to testing",
                      result.stdout)

    def test_switch_latest_with_equal_digests_records_stable(self):
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:testing", "switch-latest",
            target_digest=self.BOOTED_DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("Channel=stable", recorded)
        self.assertIn("Already running this build; channel set to stable",
                      result.stdout)

    def test_switch_refused_for_equal_digest_is_salvaged_by_recheck(self):
        # The up-front digest resolution fails (skopeo's first call),
        # bootc refuses the switch with the same-digest error, and the
        # guard's independent re-check confirms the equality: the
        # switch's goal was already met, so the channel is recorded.
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "switch-testing",
            target_digest=self.BOOTED_DIGEST, switch_fails=True,
            skopeo_fail_first=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:testing", calls)
        self.assertIn("Channel=testing", recorded)

    def test_failed_switch_with_different_digests_still_fails(self):
        result, calls, recorded = self.run_guard(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "switch-testing",
            switch_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:testing", calls)
        self.assertNotIn("Channel=testing", recorded)


class SafeUpgradeProgressPipeTests(unittest.TestCase):
    """bootc's --progress-fd must be a real pipe ("not a pipe" otherwise).

    Runs the real script, with its fixed paths rewritten into a temp dir,
    against a fake bootc that enforces what the real one does.
    """

    FAKE_BOOTC = """#!/bin/sh
case "$1" in
switch|upgrade)
    if [ "$1" = switch ]; then
        [ "$BOOTED_IMAGE_REF" = "ghcr.io/kyth-os/rowyn:stable" ] || exit 9
        found=0
        for arg in "$@"; do
            [ "$arg" = "ghcr.io/mrtrickdotorg/rowyn:stable" ] && found=1
        done
        [ "$found" = 1 ] || exit 9
    else
        [ "$BOOTED_IMAGE_REF" = "ghcr.io/mrtrickdotorg/rowyn:stable" ] || exit 9
    fi
    for arg in "$@"; do
        if [ "$arg" = "--progress-fd" ]; then
            if [ ! -p /dev/fd/3 ]; then
                echo "error: Upgrading composefs: not a pipe" >&2
                exit 1
            fi
            echo '{"type":"ProgressBytes","task":"pulling","bytes":50,"bytesTotal":100,"steps":0,"stepsTotal":2}' >&3
        fi
    done
    echo "Staged update"
    ;;
status)
    printf '{"status":{"booted":{"image":{"image":{"image":"%s"}}},"staged":{"image":{}}}}\n' "$BOOTED_IMAGE_REF"
    ;;
esac
"""

    def run_script(self, fake_bootc: str, booted_image_ref="ghcr.io/kyth-os/rowyn:stable"):
        with tempfile.TemporaryDirectory(prefix="rowyn-safe-upgrade-") as temp:
            temp_path = Path(temp)
            bootc = temp_path / "bootc"
            bootc.write_text(fake_bootc, encoding="utf-8")
            bootc.chmod(0o755)
            finalize = temp_path / "finalize"
            finalize.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            finalize.chmod(0o755)
            # Digest resolution stays unavailable in this class: the
            # fake status carries no imageDigest, and the skopeo stub
            # refuses, so the digest-equal path can never trigger here.
            skopeo = temp_path / "skopeo"
            skopeo.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            skopeo.chmod(0o755)
            text = SAFE_UPGRADE.read_text(encoding="utf-8")
            text = text.replace("if (( EUID != 0 )); then", "if false; then")
            text = text.replace("/usr/bin/bootc", str(bootc))
            text = text.replace("/usr/bin/skopeo", str(skopeo))
            text = text.replace("/run/rowyn-bootc.lock", str(temp_path / "lock"))
            text = text.replace("/usr/libexec/rowyn-finalize-staged", str(finalize))
            script = temp_path / "rowyn-safe-upgrade"
            script.write_text(text, encoding="utf-8")
            env = dict(os.environ, BOOTED_IMAGE_REF=booted_image_ref)
            return subprocess.run(["bash", str(script)], capture_output=True,
                                  text=True, timeout=60, env=env)

    def test_progress_fd_is_a_pipe_and_progress_is_streamed(self):
        result = self.run_script(self.FAKE_BOOTC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("not a pipe", result.stderr)
        # 85 * 50 / 100 = 42: the byte progress written to the pipe reached
        # the Hub's marker stream.
        self.assertIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_destination_image_uses_upgrade_not_switch(self):
        result = self.run_script(self.FAKE_BOOTC,
                                 "ghcr.io/mrtrickdotorg/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_falls_back_without_progress_when_the_fd_is_rejected(self):
        always_rejects = self.FAKE_BOOTC.replace(
            'if [ ! -p /dev/fd/3 ]; then',
            'if true; then',
        )
        result = self.run_script(always_rejects)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_script_never_redirects_fd3_to_a_regular_file(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertNotIn('3>"$workdir/bootc.progress"', text)
        self.assertIn('3> >(cat >>"$workdir/bootc.progress")', text)


class SafeUpgradeChannelTests(unittest.TestCase):
    """The policy channel selects the staged ref: a machine on testing
    stages testing; a channel change stages a switch; an unknown
    channel fails closed before bootc runs. Because both channels are
    promoted from one verified digest, a channel change whose target
    digest is the booted image's own digest is already current —
    bootc switch refuses that identical target outright."""

    BOOTED_DIGEST = "sha256:" + "a" * 64
    OTHER_DIGEST = "sha256:" + "b" * 64

    FAKE_BOOTC = """#!/bin/sh
printf '%s\\n' "$*" >> "$BOOTC_CALLS"
case "$1" in
status)
    printf '%s\n' "$BOOTED_STATUS_JSON"
    ;;
switch|upgrade)
    if [ "${BOOTC_SWITCH_FAILS:-0}" = 1 ]; then
        echo "error: Switching: Composefs Switching: Target image has the same fs-verity digest as the existing Some(Booted) deployment." >&2
        exit 1
    fi
    for arg in "$@"; do
        if [ "$arg" = "--progress-fd" ] && [ -p /dev/fd/3 ]; then
            echo '{"type":"ProgressBytes","task":"pulling","bytes":10,"bytesTotal":100,"steps":0,"stepsTotal":1}' >&3
        fi
    done
    echo "Staged update"
    ;;
esac
"""

    FAKE_SKOPEO = """#!/bin/sh
if [ "${SKOPEO_FAIL_FIRST:-0}" = 1 ]; then
    count=0
    if [ -f "$SKOPEO_COUNTER" ]; then count=$(cat "$SKOPEO_COUNTER"); fi
    count=$((count + 1))
    printf '%s' "$count" > "$SKOPEO_COUNTER"
    if [ "$count" = 1 ]; then exit 1; fi
fi
printf '%s' "$TARGET_IMAGE_DIGEST"
"""

    def run_script(self, booted_image_ref, policy_text, *,
                   target_digest=OTHER_DIGEST, switch_fails=False,
                   skopeo_fail_first=False):
        with tempfile.TemporaryDirectory(prefix="rowyn-safe-upgrade-channel-") as temp:
            temp_path = Path(temp)
            bootc = temp_path / "bootc"
            bootc.write_text(self.FAKE_BOOTC, encoding="utf-8")
            bootc.chmod(0o755)
            skopeo = temp_path / "skopeo"
            skopeo.write_text(self.FAKE_SKOPEO, encoding="utf-8")
            skopeo.chmod(0o755)
            finalize = temp_path / "finalize"
            finalize.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            finalize.chmod(0o755)
            calls = temp_path / "bootc-calls"
            policy = temp_path / "update-policy.conf"
            policy.write_text(policy_text, encoding="utf-8")
            text = SAFE_UPGRADE.read_text(encoding="utf-8")
            text = text.replace("if (( EUID != 0 )); then", "if false; then")
            text = text.replace("/usr/bin/bootc", str(bootc))
            text = text.replace("/usr/bin/skopeo", str(skopeo))
            text = text.replace("/run/rowyn-bootc.lock", str(temp_path / "lock"))
            text = text.replace("/usr/libexec/rowyn-finalize-staged", str(finalize))
            script = temp_path / "rowyn-safe-upgrade"
            script.write_text(text, encoding="utf-8")
            status_json = json.dumps({"status": {
                "booted": {"image": {"image": {"image": booted_image_ref},
                                     "imageDigest": self.BOOTED_DIGEST}},
                "staged": {"image": {}}}})
            env = dict(os.environ, BOOTED_STATUS_JSON=status_json,
                       TARGET_IMAGE_DIGEST=target_digest,
                       SKOPEO_COUNTER=str(temp_path / "skopeo-counter"),
                       BOOTC_CALLS=str(calls),
                       ROWYN_POLICY_ETC=str(policy),
                       ROWYN_POLICY_DEFAULT=str(temp_path / "absent-default"))
            if switch_fails:
                env["BOOTC_SWITCH_FAILS"] = "1"
            if skopeo_fail_first:
                env["SKOPEO_FAIL_FIRST"] = "1"
            result = subprocess.run(["bash", str(script)], capture_output=True,
                                    text=True, timeout=60, env=env)
            recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
            return result, recorded

    def test_testing_machine_stages_by_upgrade(self):
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:testing", "Channel=testing\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("upgrade", calls)
        self.assertNotIn("switch", calls)

    def test_channel_change_stages_a_switch_to_the_channel_ref(self):
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "Channel=testing\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch", calls)
        self.assertIn("ghcr.io/mrtrickdotorg/rowyn:testing", calls)

    def test_channel_change_with_equal_digests_is_already_current(self):
        # The testing tag resolves to the booted image's own digest:
        # bootc switch would refuse it, so staging completes as
        # already current and bootc is never asked to switch.
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "Channel=testing\n",
            target_digest=self.BOOTED_DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROWYN_UPDATE_RESULT outcome=current reason=no_update",
                      result.stdout)
        self.assertNotIn("switch", calls)
        self.assertNotIn("upgrade", calls)

    def test_switch_refused_for_equal_digest_is_salvaged_by_recheck(self):
        # The up-front digest resolution fails (skopeo's first call),
        # bootc refuses the switch with the same-digest error, and the
        # independent re-check confirms the equality: already current,
        # not a failure the watcher would retry forever.
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "Channel=testing\n",
            target_digest=self.BOOTED_DIGEST, switch_fails=True,
            skopeo_fail_first=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch", calls)
        self.assertIn("ROWYN_UPDATE_RESULT outcome=current reason=no_update",
                      result.stdout)

    def test_failed_switch_with_different_digests_still_fails(self):
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "Channel=testing\n",
            switch_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("switch", calls)
        self.assertIn("ROWYN_UPDATE_RESULT outcome=failed", result.stdout)

    def test_unknown_channel_fails_closed(self):
        result, calls = self.run_script(
            "ghcr.io/mrtrickdotorg/rowyn:stable", "Channel=cachy\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("switch", calls)
        self.assertNotIn("upgrade", calls)


class FinalizeStagedTests(unittest.TestCase):
    def run_finalize(self, cmdline: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="rowyn-finalize-") as temp:
            path = Path(temp) / "cmdline"
            path.write_text(cmdline + "\n", encoding="utf-8")
            env = dict(os.environ, ROWYN_PROC_CMDLINE=str(path))
            return subprocess.run(["bash", str(FINALIZE), *args], env=env,
                                  capture_output=True, text=True, timeout=30)

    # bootc 1.17.3 defaults to EROFS format V1, whose karg is composefs.digest=.
    COMPOSEFS = ("BOOT_IMAGE=/vmlinuz root=UUID=0000 rw rootflags=noatime,compress=zstd:1 "
                 "composefs.digest=v1-sha512-12:" + "f" * 128 + " quiet splash")
    COMPOSEFS_V2 = "root=UUID=0000 rw composefs=abc123 quiet"

    def test_v2_composefs_karg_is_detected_too(self):
        result = self.run_finalize(self.COMPOSEFS_V2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootc finalizes the staged deployment at shutdown", result.stdout)

    def test_similar_kargs_are_not_mistaken_for_composefs(self):
        text = FINALIZE.read_text(encoding="utf-8")
        self.assertIn("(^| )composefs(\\.digest)?=", text)

    def test_composefs_finalize_defers_to_bootc_at_shutdown(self):
        result = self.run_finalize(self.COMPOSEFS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootc finalizes the staged deployment at shutdown", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_composefs_prepare_boot_is_a_no_op(self):
        result = self.run_finalize(self.COMPOSEFS, "prepare-boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_composefs_must_be_a_whole_argument(self):
        text = FINALIZE.read_text(encoding="utf-8")
        # The ostree path is kept for non-composefs boots.
        self.assertIn("/usr/bin/ostree admin finalize-staged", text)

    def test_usage_errors_are_unchanged(self):
        result = self.run_finalize(self.COMPOSEFS, "bogus")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
