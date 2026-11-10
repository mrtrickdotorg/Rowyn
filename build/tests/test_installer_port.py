#!/usr/bin/env python3
"""Contracts for the Rowyn installer port's legacy junk cut
(build/ubuntu-installer-source.py).

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_UPSTREAM_REPO to a local upstream checkout
that contains the pinned commit; the port integration tests skip otherwise.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
DIGEST = "sha256:" + "b" * 64
# The upstream executor fixtures pin the all-zero digest as the ISO
# release digest; the cargo-suite export must use it so the ported
# ROWYN_RELEASE_SOURCE constant matches the fixtures' references.
CARGO_TEST_DIGEST = "sha256:" + "0" * 64


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_module("ubuntu_installer_source", "build/ubuntu-installer-source.py")
hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")
source_probes = load_module("ubuntu_source_probes", "build/ubuntu-source-probes.py")
live_preflight = load_module("ubuntu_live_preflight", "build/ubuntu-live-preflight.py")


class InstallerJunkCutContractTests(unittest.TestCase):
    def test_shared_crate_cut_matches_hub_port(self):
        # Both ports carry the shared-crate cut; the constants must stay
        # in step or the two exports of the same crate would diverge.
        self.assertEqual(installer.SHARED_CRATE_BIN_FILES, hub.SHARED_CRATE_BIN_FILES)
        self.assertEqual(installer.SHARED_CRATE_BIN_SECTIONS, hub.SHARED_CRATE_BIN_SECTIONS)
        self.assertEqual(installer.SHARED_CRATE_KEEP_MODULES, hub.SHARED_CRATE_KEEP_MODULES)
        self.assertEqual(len(installer.SHARED_CRATE_KEEP_MODULES), 58)

    def test_port_script_carries_junk_cut(self):
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        for anchor in ("strip_shared_crate_bins", "prune_shared_crate",
                       "sync_cargo_lock", '"cargo", "fetch"',
                       "kyth-installer-native", "native_main.rs",
                       'slint-build = "1.16.0"', 'slint = "1.16.0"',
                       "slint_build::compile"):
            self.assertIn(anchor, script)
        # Item 8: the dead installer_source rewrite is gone from the port.
        self.assertNotIn("install_images", script)
        self.assertNotIn("installer_source.rs", script)

    def test_port_script_carries_test_debt_fix(self):
        # The ported tree's upstream tests are adapted to Rowyn's
        # deliberate behavior (MOK rejection, the /api/disk/* gate, the
        # Prepare release gate) so `cargo test` on the export is a real
        # signal; the one live-ISO-only success path is gated visibly.
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        for anchor in (
            "rejects_import_plans_without_handling_the_password",
            "disk_api_gate_refuses_create_and_keeps_pending_empty",
            "disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests",
            "disk_api_gate_refuses_pending_remove_and_rollback",
            "preparation_refuses_a_non_release_source_before_preparing",
            '#[ignore = "requires the live ISO environment',
            '#[ignore = "load-sensitive socket-deadline timing',
            '#[ignore = "load-sensitive cross-thread timing',
            'contains("source is not this Rowyn release")',
            "Rowyn does not support custom kernels or MOK enrollment",
        ):
            self.assertIn(anchor, script)


class InstallerAlongsideQualificationTests(unittest.TestCase):
    """Install-alongside-Windows (resize_ntfs) is qualified; every other
    non-wipe mode and every encryption mode stays gated off."""

    def test_port_script_restores_only_wipe_and_resize_modes_in_ui(self):
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        # The replacement dropdown offers exactly wipe + resize_ntfs.
        self.assertIn(
            "'<option value=\"wipe\">Erase full disk</option>"
            "<option value=\"resize_ntfs\">Keep Windows and install alongside</option>'",
            script)
        # Encryption stays none-only in the UI transform: upstream's
        # only encrypted mode is TPM2-bound LUKS with no passphrase
        # plumbing, so there is nothing else honest to offer.
        self.assertIn(
            "'<option value=\"none\">None</option>'", script)

    def test_port_script_narrows_executor_gate_to_wipe_and_resize(self):
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        self.assertIn(
            '!matches!(self.storage_plan.mode.as_str(), "wipe" | "resize_ntfs")',
            script)
        self.assertIn(
            "whole-disk and install-alongside-Windows (NTFS shrink) plaintext installs only; "
            "manual, replace-partition, free-space and TPM encryption are not qualified",
            script)
        # The old wipe-only gate is gone.
        self.assertNotIn("whole-disk plaintext installs only", script)
        self.assertNotIn(
            'self.storage_plan.mode != "wipe"', script)

    def test_port_script_restores_honest_alongside_copy(self):
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        # Welcome copy names the alongside choice and the shrink, and
        # no longer claims the installer only erases.
        self.assertIn('choose "Keep Windows and install alongside"', script)
        self.assertIn("shrink your Windows partition to make room", script)
        self.assertNotIn(
            "This installer erases the disk you select and installs Rowyn on it.",
            script)

    def test_port_script_repoints_moved_ubuntu_runtime_tools(self):
        # Ubuntu 26.10 (stonking) packaging moved ntfsfix and the btrfs
        # CLI from /usr/sbin to /usr/bin — verified against the archive
        # debs (ntfs-3g 1:2026.7.7-2: ./usr/bin/ntfsfix, ntfsresize
        # stays in /usr/sbin; btrfs-progs 7.1-1: ./usr/bin/btrfs, only
        # mkfs.btrfs/fsck.btrfs in /usr/sbin). The port must repoint
        # upstream's absolute-path invocations of both, count-asserted,
        # or the generated daemon/disk helper call paths that do not
        # exist on a real live system.
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        self.assertIn(
            "replace_count(text, '\"/usr/sbin/ntfsfix\"', "
            "'\"/usr/bin/ntfsfix\"', 1)", script)
        self.assertIn(
            "replace_count(text, '\"/usr/sbin/btrfs\"', "
            "'\"/usr/bin/btrfs\"', 6)", script)

    def test_port_script_carries_prepare_btrfs_target_fallback(self):
        # Upstream prepare_btrfs_target re-reads plan.target_partition,
        # which resize_ntfs plans never carry; the port falls back to
        # the caller-passed created partition, keeping the fresh
        # snapshot re-validation.
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        self.assertIn("unwrap_or_else(|| _target.to_string())", script)
        self.assertIn("requested_owned", script)

    def test_live_preflight_requires_resize_path_tools(self):
        # The resize path shells out to these by absolute path; the
        # preflight (run in Prepare, before any mutation) must require
        # them, and the live image must install their packages. Paths
        # are the ones the Ubuntu 26.10 (stonking) archive debs ship —
        # ntfs-3g 1:2026.7.7-2 puts ntfsfix in /usr/bin and keeps
        # ntfsresize in /usr/sbin; btrfs-progs 7.1-1 puts the btrfs CLI
        # in /usr/bin (only mkfs.btrfs/fsck.btrfs stay in /usr/sbin).
        # The /usr/sbin forms of ntfsfix/btrfs do not exist there; the
        # old tuple failed the ISO's configure-live preflight on them.
        for binary in ("/usr/sbin/ntfsresize", "/usr/bin/ntfsfix",
                       "/usr/bin/btrfs"):
            self.assertIn(binary, live_preflight.REQUIRED)
        for stale in ("/usr/sbin/ntfsfix", "/usr/sbin/btrfs"):
            self.assertNotIn(stale, live_preflight.REQUIRED)
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        live_stage = containerfile.split(
            "FROM ${UBUNTU_IMAGE} AS live", 1)[1]
        package_line = live_stage.split(
            "apt-get install -y --no-install-recommends", 1)[1].split("&&", 1)[0]
        for package in ("ntfs-3g", "btrfs-progs", "cryptsetup-bin"):
            self.assertRegex(package_line, rf"\b{re.escape(package)}\b")


class SourceProbesContractTests(unittest.TestCase):
    def test_probes_read_the_executor_release_gate_not_the_deleted_module(self):
        # The junk cut deleted the shared crate's image-source selection
        # module, but the ISO build's source probes kept reading it and
        # crashed. The probes now extract the release contract from the
        # installer crate: the executor's pinned constants and digest
        # check, the bootc plan's reference normalization, and the
        # assurance kernel check. They must never point at the deleted
        # module (or the shared crate) again.
        script = (ROOT / "build/ubuntu-source-probes.py").read_text()
        self.assertNotIn("installer_source", script)
        self.assertNotIn("rowyn-shared-rs", script)
        for anchor in ("installer_job_executor.rs", "installer_bootc.rs",
                       "installer_assurance.rs", "ROWYN_RELEASE_SOURCE",
                       "ROWYN_UPDATE_TARGET", "is_sha256_digest",
                       "safe_reference", "source_reference", "kernel_detail"):
            self.assertIn(anchor, script)


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


@unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit")
class PinnedInstallerJunkCutTests(unittest.TestCase):
    def test_junk_cut_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-junk-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            crate = root / "src/rowyn-installer-web/src-tauri"
            shared = root / "src/rowyn-shared-rs"

            # Item 3: the Slint stack is out — bin target, source,
            # dependencies, and the build.rs compile step — while the
            # three shipped bins stay declared.
            self.assertFalse((crate / "src/native_main.rs").exists())
            manifest = (crate / "Cargo.toml").read_text()
            self.assertNotIn("kyth-installer-native", manifest)
            self.assertNotIn("slint", manifest)
            self.assertNotIn("slint", (crate / "build.rs").read_text())
            for kept_bin in ("rowyn-installer-shell", "rowyn-installerd",
                             "rowyn-installer-exec"):
                self.assertIn(f'name = "{kept_bin}"', manifest)

            # Items 1 + 7 + 8: shared crate pruned to the keep set, no
            # bin sources/sections, no pgp, and the dead
            # installer_source module is gone rather than rewritten.
            self.assertEqual(list((shared / "src").glob("*_bin.rs")), [])
            shared_manifest = (shared / "Cargo.toml").read_text()
            self.assertNotIn("[[bin]]", shared_manifest)
            self.assertNotIn("pgp", shared_manifest)
            self.assertFalse((shared / "src/system/installer_source.rs").exists())
            remaining = sorted(
                str(p.relative_to(shared / "src"))
                for p in (shared / "src").rglob("*.rs"))
            keep = set(installer.SHARED_CRATE_KEEP_MODULES)
            expected = sorted(
                ["lib.rs", "system/mod.rs"]
                + [f"{name}.rs" for name in keep if "::" not in name and name != "system"]
                + [f"system/{name.rsplit('::', 1)[-1]}.rs"
                   for name in keep if name.startswith("system::")])
            self.assertEqual(remaining, expected)
            self.assertTrue((shared / "src/system/installer_validation.rs").is_file())
            self.assertTrue((shared / "src/transfer.rs").is_file())

            # The lock was re-synced by cargo: none of the removed
            # packages remain.
            lock = (crate / "Cargo.lock").read_text()
            lock_names = {block.split('"')[1] for block in lock.split("[[package]]")[1:]
                          if 'name = "' in block}
            for gone in ("pgp", "slint", "slint-build"):
                self.assertNotIn(gone, lock_names)

    def test_test_debt_adaptations_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-tests-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            src = root / "src/rowyn-installer-web/src-tauri/src"

            secure = (src / "installer_secure_boot.rs").read_text()
            self.assertIn("rejects_import_plans_without_handling_the_password", secure)
            self.assertNotIn("plans_import_without_handling_the_password", secure)
            self.assertNotIn("MOK plan should validate", secure)
            self.assertNotIn("fixture input should validate", secure)

            acceptance = (src / "installer_acceptance.rs").read_text()
            self.assertIn("Rowyn must reject MOK import plans", acceptance)

            daemon = (src / "installer_daemon.rs").read_text()
            for adapted in ("disk_api_gate_refuses_create_and_keeps_pending_empty",
                            "disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests",
                            "disk_api_gate_refuses_pending_remove_and_rollback"):
                self.assertIn(adapted, daemon)
            for upstream in ("native_journal_routes_preserve_pending_metadata_without_python",
                             "native_journal_routes_fail_closed_for_unsafe_and_unsupported_requests",
                             "native_journal_pending_remove_and_rollback_clear_state"):
                self.assertNotIn(upstream, daemon)

            executor = (src / "installer_job_executor.rs").read_text()
            self.assertIn("preparation_refuses_a_non_release_source_before_preparing", executor)
            self.assertNotIn("preparation_persists_a_recoverable_native_transaction", executor)
            self.assertIn('#[ignore = "requires the live ISO environment', executor)
            self.assertIn('contains("source is not this Rowyn release")', executor)
            self.assertIn('#[ignore = "load-sensitive socket-deadline timing', daemon)
            job = (src / "installer_job.rs").read_text()
            self.assertIn('#[ignore = "load-sensitive cross-thread timing', job)


    def test_alongside_qualification_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-alongside-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            crate = root / "src/rowyn-installer-web/src-tauri"
            src = crate / "src"

            # UI: exactly wipe + resize_ntfs are offered; the dormant
            # upstream DiskStep resize flow (NTFS picker, GiB input,
            # Review shrink line) becomes reachable, and the welcome
            # copy describes the shrink honestly.
            app = (root / "src/rowyn-installer-web/src/InstallerApp.tsx").read_text()
            self.assertIn(
                '<option value="wipe">Erase full disk</option>'
                '<option value="resize_ntfs">Keep Windows and install alongside</option>',
                app)
            for unqualified in ('<option value="alongside">',
                                '<option value="free_space">',
                                '<option value="manual">'):
                self.assertNotIn(unqualified, app)
            self.assertIn("Windows partition to shrink", app)
            self.assertIn("Shrink Windows (", app)
            self.assertIn("shrink your Windows partition to make room", app)
            # Encryption stays none-only: no TPM2 (or any other) option.
            self.assertIn('<option value="none">None</option>', app)
            self.assertNotIn('value="tpm2"', app)

            # Executor: the Prepare gate admits wipe + resize_ntfs and
            # still rejects everything else, and it runs before the
            # live preflight (itself before any disk mutation).
            executor = (src / "installer_job_executor.rs").read_text()
            self.assertIn(
                '!matches!(self.storage_plan.mode.as_str(), "wipe" | "resize_ntfs")',
                executor)
            self.assertIn(
                "manual, replace-partition, free-space and TPM encryption are not qualified",
                executor)
            self.assertNotIn("whole-disk plaintext installs only", executor)
            prepare = executor[executor.index("            Phase::Prepare => {"):
                               executor.index("            Phase::Storage =>")]
            self.assertLess(prepare.index('"resize_ntfs"'),
                            prepare.index("rowyn-live-preflight"))
            self.assertLess(prepare.index("verify_install_source"),
                            prepare.index("rowyn-live-preflight"))

            # The bounded prepare_btrfs_target fix is in: a resize plan
            # (no target_partition) formats the partition the storage
            # phase just created, still re-validated from a fresh
            # snapshot.
            self.assertIn("unwrap_or_else(|| _target.to_string())", executor)
            self.assertIn("validate_replace_target", executor)

            # NTFS attestation/shrink anchors survive the port
            # untouched and fail closed: daemon commit-time dirty-bit
            # re-verification via ntfsfix, journal shrink
            # preconditions, and the executor's ntfsresize stages.
            daemon = (src / "installer_daemon.rs").read_text()
            self.assertIn("fn stamp_ntfs_attestation", daemon)
            self.assertIn("fn ntfs_volume_is_clean", daemon)
            # Ubuntu 26.10 ships ntfsfix in /usr/bin, not /usr/sbin
            # (ntfs-3g 1:2026.7.7-2 deb listing); the port repoints the
            # daemon's attestation probe.
            self.assertIn('"/usr/bin/ntfsfix"', daemon)
            self.assertNotIn('"/usr/sbin/ntfsfix"', daemon)
            journal = (src / "installer_journal.rs").read_text()
            self.assertIn("fn validate_shrink_preconditions", journal)
            self.assertIn("ntfs_verified_clean", journal)
            self.assertIn("dirty bit must be re-verified clear", journal)
            disk = (src / "installer_disk.rs").read_text()
            self.assertIn('"/usr/sbin/ntfsresize"', disk)
            # The btrfs CLI likewise moved to /usr/bin in Ubuntu 26.10
            # (btrfs-progs 7.1-1 deb listing); the port repoints every
            # subvolume invocation in the disk helper.
            self.assertIn('"/usr/bin/btrfs"', disk)
            self.assertNotIn('"/usr/sbin/btrfs"', disk)
            self.assertIn("fn resize_ntfs_target", executor)
            for stage in ('"check"', '"info"', '"dry_run"', '"resize"'):
                self.assertIn(stage, executor)

    def test_rename_contract_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-rename-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            crate = root / "src/rowyn-installer-web/src-tauri"
            shared = root / "src/rowyn-shared-rs"

            # The shared crate and every reference to it carry the
            # Rowyn name; no KYTH_ token survives in the shipped code.
            shared_manifest = (shared / "Cargo.toml").read_text()
            self.assertIn('name = "rowyn-shared"', shared_manifest)
            self.assertIn("path = \"../../rowyn-shared-rs\"",
                          (crate / "Cargo.toml").read_text())
            executor = (crate / "src/installer_job_executor.rs").read_text()
            self.assertIn('"/usr/bin/rowyn-cosign"', executor)
            self.assertIn("ROWYN_SOURCE_IMAGE", executor)
            self.assertIn("ghcr.io/mrtrickdotorg/rowyn@sha256:", executor)
            self.assertIn("https://github.com/mrtrickdotorg/Rowyn/.github/workflows/build.yml@refs/heads/main", executor)
            self.assertNotIn("ghcr.io/kyth-os/rowyn@", executor)
            self.assertNotIn("https://github.com/kyth-os/Rowyn", executor)
            self.assertNotIn("KYTH_", executor)
            daemon = (crate / "src/installer_daemon.rs").read_text()
            self.assertIn("rowyn_shared::", daemon)
            self.assertIn("X-Rowyn-Session-Token", daemon)
            self.assertNotIn("KYTH_", daemon)

            # ISO runtime names: launcher, daemon unit, env file.
            service = (root / "build_files/rowyn-installerd.service").read_text()
            self.assertIn("ExecStart=/usr/bin/rowyn-installerd", service)
            self.assertIn("EnvironmentFile=/etc/rowyn-installer.env", service)
            self.assertIn("/run/rowyn-installer", service)
            launcher = (root / "build_files/rowyn-launch-installer").read_text()
            self.assertIn("ghcr.io/mrtrickdotorg/rowyn:stable", launcher)

            # No unmasked kyth reference survives anywhere in the
            # export. Deliberate exceptions: the GitHub org in
            # registry/identity strings, the upstream cert-name
            # matcher, one negative regression needle, and the
            # upstream unit whose absence the ported config tests
            # assert.
            protected = ("KythOS Secure Boot", "kyth-vpnc-script",
                         "kyth-system-accounts.service")
            offenders = []
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    masked = path.read_text()
                except (UnicodeDecodeError, ValueError):
                    continue
                for literal in protected:
                    masked = masked.replace(literal, "")
                masked = masked.replace("kyth-os", "")
                if "kyth" in masked.lower():
                    offenders.append(str(path.relative_to(root)))
            self.assertEqual(offenders, [])


@unittest.skipUnless(_pinned_repo() and shutil.which("rustc"),
                     "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit and provide rustc")
class PinnedSourceProbesTests(unittest.TestCase):
    def test_probe_targets_exist_and_probes_pass(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-source-probes-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            crate_src = root / "src/rowyn-installer-web/src-tauri/src"

            # Every file the probes open must exist in a fresh export,
            # and the module they used to read must stay deleted.
            for name in ("installer_job_executor.rs", "installer_bootc.rs",
                         "installer_assurance.rs"):
                self.assertTrue((crate_src / name).is_file(), name)
            self.assertFalse(
                (root / "src/rowyn-shared-rs/src/system/installer_source.rs").exists())

            # The generated probe source is extracted from the real
            # exported code: the port-baked release constants and the
            # gate/normalization/kernel functions, by name.
            generated = source_probes.probes(root)
            for marker in (
                f'const ROWYN_RELEASE_SOURCE: &str = "ghcr.io/mrtrickdotorg/rowyn@{DIGEST}";',
                'const ROWYN_UPDATE_TARGET: &str = "ghcr.io/mrtrickdotorg/rowyn:stable";',
                "fn is_sha256_digest(", "fn safe_reference(",
                "fn source_reference(", "fn kernel_detail(",
            ):
                self.assertIn(marker, generated)

            # End to end, exactly as the ISO compiler stage runs it.
            env = dict(os.environ)
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = cargo_bin + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                [sys.executable, str(ROOT / "build/ubuntu-source-probes.py"),
                 str(root), str(Path(temp) / "out")],
                env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("5 passed; 0 failed", result.stdout)


@unittest.skipUnless(_pinned_repo() and shutil.which("cargo"),
                     "set ROWYN_TEST_UPSTREAM_REPO to an upstream checkout containing the pinned commit and provide cargo")
class PinnedInstallerCargoTests(unittest.TestCase):
    def test_installer_cargo_suite_is_green(self):
        repo = _pinned_repo()
        # The debug target dir is ~3G; /tmp is a small tmpfs on the dev
        # VM, so the scratch tree lives on the home filesystem.
        scratch = Path.home() / ".cache" / "rowyn-test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-cargo-", dir=scratch) as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, CARGO_TEST_DIGEST)
            crate = root / "src/rowyn-installer-web/src-tauri"
            # The shell binary's tauri::generate_context! requires the
            # frontend dist to exist even for unit tests; the ISO build
            # produces the real one with npm, the suite uses a stub.
            dist = root / "src/rowyn-installer-web/dist"
            dist.mkdir(exist_ok=True)
            (dist / "index.html").write_text("<html></html>\n")
            env = dict(os.environ)
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = cargo_bin + os.pathsep + env.get("PATH", "")
            # Serialize Rust test functions: the external-writer flock
            # assertion is flaky under parallel load on the shared dev
            # filesystem. Tests that exercise internal concurrency still
            # create their own threads.
            env["RUST_TEST_THREADS"] = "1"
            result = subprocess.run(
                ["cargo", "test", "--locked", "--no-fail-fast"],
                cwd=crate, env=env, text=True, capture_output=True,
                timeout=3600)
            output = result.stdout + result.stderr
            # Keep the full log outside the temp tree for diagnosis.
            log_path = scratch / "last-installer-cargo-test.log"
            log_path.write_text(output)
            failed_names = re.findall(r"^test (\S+) \.\.\. FAILED$", output, re.M)
            summary = (
                f"cargo log: {log_path}\nfailed tests: {failed_names}\n"
                + "\n".join(
                    line for line in output.splitlines()
                    if line.startswith("test result:")))
            totals = re.findall(
                r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored",
                output)
            self.assertTrue(totals, summary)
            self.assertEqual(sum(int(t[1]) for t in totals), 0, summary)
            self.assertEqual(result.returncode, 0, summary)
            # The adaptations actually ran (per target binary), and the
            # live-ISO cosign success path is visibly gated, not deleted.
            for marker in (
                "installer_secure_boot::tests::rejects_import_plans_without_handling_the_password ... ok",
                "installer_secure_boot::tests::classifies_existing_states_and_non_custom_kernel ... ok",
                "installer_secure_boot::tests::matches_shared_decision_fixture ... ok",
                "installer_acceptance::tests::bootc_and_secure_boot_staging_are_explicit_in_acceptance ... ok",
                "installer_daemon::tests::disk_api_gate_refuses_create_and_keeps_pending_empty ... ok",
                "installer_daemon::tests::disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests ... ok",
            "installer_daemon::tests::disk_api_gate_refuses_pending_remove_and_rollback ... ok",
                "installer_job_executor::tests::image_phase_refuses_unpinned_docker_source ... ok",
                "installer_job_executor::tests::preparation_refuses_a_non_release_source_before_preparing ... ok",
                "installer_job_executor::tests::image_phase_verifies_digest_pinned_docker_source ... ignored",
                "installer_daemon::tests::slow_trickled_request_cannot_extend_the_absolute_read_deadline ... ignored",
                "installer_job::tests::cancellation_during_phase_waits_for_executor_cleanup ... ignored",
            ):
                self.assertIn(marker, output, marker)


if __name__ == "__main__":
    unittest.main()
