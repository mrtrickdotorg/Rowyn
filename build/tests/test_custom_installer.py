#!/usr/bin/env python3
"""Safe behavioral regressions; no sudo, mounts, Podman, or network are used.

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_UPSTREAM_REPO to a local upstream checkout.
All writes, including disposable worktrees, live below TMPDIR.
ROWYN_TEST_ACTION / ROWYN_TEST_ADAPTER allow testing saved pre-fix files.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = Path(os.environ.get("ROWYN_TEST_ACTION", ROOT / ".github/actions/setup-compressed-storage/action.yml"))
ADAPTER = Path(os.environ.get("ROWYN_TEST_ADAPTER", ROOT / "build/custom-installer-iso.sh"))
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
EXECUTOR = "src/rowyn-installer-web/src-tauri/src/installer_job_executor.rs"

# This executable NEVER delegates to the host, even for an unexpected command.
# Its virtual directory/mount table models reset deleting storage directories.
STORAGE_MOCK = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
state_path = Path(os.environ['MOCK_STATE'])
s = json.loads(state_path.read_text())
a = sys.argv[1:]
command = Path(sys.argv[0]).name
s['calls'].append([command] + a)
result = ''
if command == 'sudo':
    if a[:2] == ['mkdir', '-p']:
        s['dirs'] = sorted(set(s['dirs'] + a[2:]))
    elif a[:3] == ['podman', 'system', 'reset']:
        s['dirs'] = [p for p in s['dirs'] if p not in [os.environ['MOUNT_POINT'], os.environ['RUNROOT']]]
    elif a[:2] == ['mount', '--bind']:
        source, target = a[2:]
        assert source in s['dirs'], 'bind source missing'
        assert target in s['dirs'], 'post-reset bind target missing'
        s['bind_source'] = source
        s['mounts'][target] = source
    elif a[:2] == ['mount', '-o']:
        assert a[-1] in s['dirs'], 'post-reset graphroot missing'
        s['mounts'][a[-1]] = 'loop'
    elif a[:2] == ['podman', 'info']:
        result = os.environ['MOUNT_POINT'] + ' ' + os.environ['RUNROOT']
        if s['fault'] == 'podman_graphroot': result = '/wrong ' + os.environ['RUNROOT']
        if s['fault'] == 'podman_runroot': result = os.environ['MOUNT_POINT'] + ' /wrong'
    elif a and a[0] in ['truncate', 'mkfs.btrfs', 'systemctl']:
        pass
    else:
        raise AssertionError('unsafe/unexpected mocked sudo command: ' + repr(a))
elif command == 'findmnt':
    assert a[:2] == ['-n', '-o'] and a[3] == '--target', a
    column, path = a[2], a[4]
    assert column in ['FSTYPE', 'MAJ:MIN'], column
    s['queries'].append([path, column])
    backing = s['mounts'].get(path, path)
    fs, device = ('tmpfs', '0:25') if backing.startswith('/run/') else ('ext4', '8:1')
    if backing == 'loop': fs, device = 'btrfs', '7:0'
    if s['fault'] == 'graphroot_fs' and path == os.environ['MOUNT_POINT']: fs = 'ext4'
    if s['fault'] == 'bind_tmpfs' and path == os.environ['RUNROOT']: fs, device = 'tmpfs', '0:25'
    if s['fault'] == 'bind_device' and path == os.environ['RUNROOT']: device = '8:2'
    if s['fault'] == 'source_device' and path == s.get('bind_source'): device = '8:2'
    if s['fault'] == 'image_tmpfs': fs, device = 'tmpfs', '0:25'
    if s['fault'] == 'image_ramfs': fs, device = 'ramfs', '0:26'
    if s['fault'] == 'empty_device': device = ''
    if s['fault'] == 'findmnt_error':
        state_path.write_text(json.dumps(s))
        sys.exit(1)
    result = fs if column == 'FSTYPE' else device
else:
    raise AssertionError('unexpected mock executable: ' + command)
state_path.write_text(json.dumps(s))
if result: print(result)
'''


class StorageActionTests(unittest.TestCase):
    def run_action(self, fault="", image="/disk/custom image.img", runroot="/run/custom/storage"):
        script = yaml.safe_load(ACTION.read_text())["runs"]["steps"][0]["run"]
        with tempfile.TemporaryDirectory(prefix="rowyn-storage-") as temp:
            temp = Path(temp)
            mock = temp / "mock"
            mock.write_text(STORAGE_MOCK)
            mock.chmod(0o755)
            for name in ("sudo", "findmnt"):
                (temp / name).symlink_to(mock)
            state_path = temp / "state.json"
            state_path.write_text(json.dumps(dict(dirs=[], mounts={}, calls=[], queries=[], fault=fault)))
            env = dict(os.environ, PATH=str(temp), MOCK_STATE=str(state_path),
                       IMAGE_PATH=image, MOUNT_POINT="/var/lib/custom/storage", RUNROOT=runroot, SIZE="65G")
            result = subprocess.run(["/bin/bash", "-c", script], env=env, text=True, capture_output=True)
            return result, json.loads(state_path.read_text())

    def test_disk_source_and_post_reset_targets(self):
        for image, runroot in (("/disk/custom image.img", "/run/custom/storage"),
                               ("/mnt/rowyn-podman-storage.img", "/run/containers/storage")):
            with self.subTest(image=image):
                result, state = self.run_action(image=image, runroot=runroot)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["bind_source"], image + ".runroot")
                for path in (image, image + ".runroot", runroot):
                    for column in ("FSTYPE", "MAJ:MIN"):
                        self.assertIn([path, column], state["queries"])
                self.assertIn(["/var/lib/custom/storage", "FSTYPE"], state["queries"])

    def test_rejects_wrong_backing_filesystems_and_devices(self):
        for fault in ("graphroot_fs", "bind_tmpfs", "bind_device", "source_device",
                      "image_tmpfs", "image_ramfs", "empty_device", "findmnt_error"):
            with self.subTest(fault=fault):
                result, state = self.run_action(fault)
                self.assertNotEqual(result.returncode, 0, "accepted " + fault)
                self.assertNotIn("Traceback", result.stderr, "mock setup failed before assertion")
                self.assertTrue(state["queries"], "filesystem queries were never reached")

    def test_preserves_podman_path_assertions(self):
        for fault in ("podman_graphroot", "podman_runroot"):
            with self.subTest(fault=fault):
                result, state = self.run_action(fault)
                self.assertNotEqual(result.returncode, 0, "accepted " + fault)
                self.assertNotIn("Traceback", result.stderr, "mock setup failed before assertion")
                self.assertTrue(state["queries"], "filesystem queries were never reached")

    def test_rejects_image_on_run_tmpfs(self):
        result, _ = self.run_action(image="/run/storage.img")
        self.assertNotEqual(result.returncode, 0)


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


port = load_module("ubuntu_source", "build/ubuntu-installer-source.py")
live = load_module("ubuntu_live", "build/ubuntu-live-preflight.py")
guard = load_module("ubuntu_guard", "build/ubuntu-only-check.py")
network = load_module("ubuntu_live_network", "build/ubuntu-live-network.py")


class KubuntuDesktopLivePackageParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = load_module("ubuntu_live_packages", "build/ubuntu-live-packages.py")
        cls.manifest = ROOT / "build/kubuntu-desktop-live-26.10-amd64.manifest"

    def test_pinned_official_manifest_contains_the_complete_deb_and_snap_set(self):
        metadata = json.loads((ROOT / "build/kubuntu-desktop-live-26.10-amd64.manifest.json").read_text())
        self.assertEqual(metadata["sha256"], self.packages.REFERENCE_SHA256)
        package_set = self.packages.verify_reference_manifest(self.manifest)
        self.assertEqual(len(package_set.debs), 2175)
        self.assertEqual(len(package_set.snaps), 8)
        self.assertIn("iputils-ping", package_set.debs)
        self.assertIn("ubuntu-standard", package_set.debs)
        # The live desktop is KDE Plasma, not GNOME: the Kubuntu reference
        # set must carry the Plasma stack and none of the GNOME desktop.
        self.assertIn("plasma-desktop", package_set.debs)
        self.assertIn("kubuntu-desktop", package_set.debs)
        self.assertIn("sddm", package_set.debs)
        self.assertNotIn("gnome-shell", package_set.debs)
        self.assertNotIn("gdm3", package_set.debs)
        self.assertNotIn("ubuntu-desktop", package_set.debs)

    def test_install_passes_every_official_deb_except_substituted_installer_packages_to_apt(self):
        package_set = self.packages.verify_reference_manifest(self.manifest)
        calls = []

        def fake_runner(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[0] == "/usr/bin/apt-get":
                return ""
            if arguments[0] == "/usr/bin/dpkg-query":
                return "".join(f"{name}\n" for name in package_set.debs)
            raise AssertionError("unexpected command: " + str(arguments))

        self.packages.install_deb_packages(self.manifest, runner=fake_runner)
        self.assertEqual(set(calls[0][4:]), set(self.packages.expected_debs(package_set)))
        self.assertEqual(len(calls[0][4:]), 2166)
        # Calamares is Kubuntu's installer; Rowyn ships its own bootc
        # installer, so the substituted packages never reach apt.
        self.assertNotIn("calamares", calls[0][4:])
        self.assertNotIn("kubuntu-installer-prompt", calls[0][4:])
        self.assertIn("calamares", package_set.debs)
        # KDE Welcome (plasma-welcome) is the first-login popup Rowyn
        # suppresses on live and installed desktops alike; it is part of
        # the official set but must never reach apt either.
        self.assertNotIn("plasma-welcome", calls[0][4:])
        self.assertIn("plasma-welcome", package_set.debs)

    def test_run_checked_error_includes_stdout_tail_where_dpkg_names_the_failing_package(self):
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/apt-get"],
            returncode=100,
            stdout="Setting up example (1.0) ...\ndpkg: error processing package example (--configure)\n",
            stderr="E: Sub-process /usr/bin/dpkg returned an error code (1)",
        )
        with unittest.mock.patch.object(self.packages.subprocess, "run", return_value=completed):
            with self.assertRaises(RuntimeError) as raised:
                self.packages.run_checked(["/usr/bin/apt-get", "install", "-y", "example"])
        message = str(raised.exception)
        self.assertIn("dpkg: error processing package example", message)
        self.assertIn("E: Sub-process /usr/bin/dpkg returned an error code (1)", message)

    def test_parity_fails_closed_when_a_reference_deb_is_missing(self):
        package_set = self.packages.verify_reference_manifest(self.manifest)
        with tempfile.TemporaryDirectory(prefix="rowyn-live-package-missing-deb-") as temporary:
            installed = Path(temporary) / "filesystem.manifest"
            installed.write_text("bash 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "iputils-ping"):
                self.packages.verify_package_parity(self.manifest, installed, root=temporary)

    def test_parity_fails_closed_when_the_snap_seed_is_missing(self):
        package_set = self.packages.verify_reference_manifest(self.manifest)
        with tempfile.TemporaryDirectory(prefix="rowyn-live-package-missing-snap-") as temporary:
            installed = Path(temporary) / "filesystem.manifest"
            installed.write_text("".join(f"{name} 1\n" for name in self.packages.expected_debs(package_set)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snap seed is missing"):
                self.packages.verify_package_parity(self.manifest, installed, root=temporary)

    def test_parity_fails_closed_when_a_substituted_installer_package_is_present(self):
        package_set = self.packages.verify_reference_manifest(self.manifest)
        with tempfile.TemporaryDirectory(prefix="rowyn-live-package-substituted-") as temporary:
            installed = Path(temporary) / "filesystem.manifest"
            names = self.packages.expected_debs(package_set) + ["calamares"]
            installed.write_text("".join(f"{name} 1\n" for name in names), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Substituted Kubuntu installer packages present"):
                self.packages.verify_package_parity(self.manifest, installed, root=temporary)

    def test_parity_fails_closed_when_plasma_welcome_is_present(self):
        # Regression: KDE Welcome must not reach the live image. If any
        # package in the set ever pulls plasma-welcome back in, parity
        # refuses the image instead of shipping the first-login popup.
        package_set = self.packages.verify_reference_manifest(self.manifest)
        with tempfile.TemporaryDirectory(prefix="rowyn-live-package-welcome-") as temporary:
            installed = Path(temporary) / "filesystem.manifest"
            names = self.packages.expected_debs(package_set) + ["plasma-welcome"]
            installed.write_text("".join(f"{name} 1\n" for name in names), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "plasma-welcome"):
                self.packages.verify_package_parity(self.manifest, installed, root=temporary)

    def test_snap_seed_is_complete_and_parity_verification_records_it(self):
        package_set = self.packages.verify_reference_manifest(self.manifest)

        def fake_runner(arguments, **_kwargs):
            self.assertEqual(arguments[:2], ["/usr/bin/snap", "download"])
            name = arguments[2]
            target = Path(next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("--target-directory=")))
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{name}_777.snap").write_bytes(b"test-snap")
            (target / f"{name}_777.assert").write_text("type: snap-revision\n", encoding="utf-8")
            return ""

        with tempfile.TemporaryDirectory(prefix="rowyn-live-package-test-") as temporary:
            root = Path(temporary)
            self.packages.seed_snaps(
                self.manifest,
                root=root,
                runner=fake_runner,
                metadata_reader=lambda path, runner: (
                    "classic" if Path(path).name.startswith("firefox_") else "strict"
                ),
            )
            installed = root / "filesystem.manifest"
            installed.write_text("".join(f"{name} 1\n" for name in self.packages.expected_debs(package_set)), encoding="utf-8")
            marker = root / "var/lib/snapd/seed/.snapd-explicit-install-stamp"
            marker.unlink()
            with self.assertRaisesRegex(ValueError, "explicit-install stamp is missing"):
                self.packages.verify_package_parity(self.manifest, installed, root=root)
            marker.touch()
            deb_count, snap_count = self.packages.verify_package_parity(
                self.manifest, installed, root=root,
            )
            seed = json.loads((root / "var/lib/snapd/seed/seed.yaml").read_text(encoding="utf-8"))
            self.assertEqual((deb_count, snap_count), (2166, 8))
            self.assertEqual(len(seed["snaps"]), 8)
            self.assertTrue(next(item for item in seed["snaps"] if item["name"] == "firefox")["classic"])
            self.assertTrue((root / "var/lib/snapd/seed/.snapd-explicit-install-stamp").is_file())
            self.assertEqual(sum(line.startswith("snap:") for line in installed.read_text().splitlines()), 8)


class UbuntuGuardTests(unittest.TestCase):
    def test_current_executable_path_is_ubuntu(self):
        self.assertTrue(guard.validate(ROOT).startswith("docker.io/library/ubuntu@sha256:"))

    def test_guard_rejects_non_ubuntu_stages_and_executables(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-guard-") as temp:
            root = Path(temp)
            files = ["Containerfile", ".github/workflows/installer-iso.yml", "build/custom-installer-iso.sh",
                     "build/configure-live.sh", "build/assemble-iso.sh", "build/ubuntu-live.Containerfile",
                     "build/ubuntu-live-packages.py"]
            for file in files:
                target = root / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / file).read_bytes())
            for file, old, new in [
                ("build/ubuntu-live.Containerfile", "FROM ${UBUNTU_IMAGE}", "FROM registry.fedoraproject.org/fedora:45"),
                ("build/configure-live.sh", "set -euo pipefail", "set -euo pipefail\ndnf install -y rust"),
                ("build/configure-live.sh", "set -euo pipefail", "set -euo pipefail\n    sudo rpm -q bootc"),
                ("build/assemble-iso.sh", "set -euo pipefail", "set -euo pipefail\nbash build_files/build-live-iso.sh"),
                (".github/workflows/installer-iso.yml",
                 "UBUNTU_LIVE_IMAGE=$(python3 build/ubuntu-pin-bump.py --pinned)",
                 "UBUNTU_LIVE_IMAGE=docker.io/library/ubuntu@sha256:" + "0" * 64),
                ("Containerfile", "FROM docker.io/library/ubuntu@sha256:", "FROM docker.io/library/ubuntu:26.10 # "),
                ("build/ubuntu-live.Containerfile", "FROM ${UBUNTU_IMAGE} AS compiler",
                 "FROM docker.io/library/ubuntu@sha256:" + "0" * 64 + " AS compiler"),
            ]:
                path = root / file
                text = path.read_text()
                self.assertIn(old, text)
                path.write_text(text.replace(old, new))
                with self.assertRaises(ValueError, msg=file):
                    guard.validate(root)
                path.write_text(text)
            guard.validate(root)

    def test_port_rejects_ambiguous_anchors_and_bad_digest(self):
        for text in ("", "anchor anchor"):
            with self.assertRaises(ValueError):
                port.replace_once(text, "anchor", "replacement")
        with self.assertRaises(ValueError):
            port.port_source(Path("/does/not/exist"), "sha256:bad")

    def test_legacy_base_rejected_before_container_execution(self):
        env = dict(os.environ, IMAGE_REF="ghcr.io/mrtrickdotorg/rowyn:" + "a"*40,
                   SOURCE_TAG="a"*40, IMAGE_DIGEST="sha256:" + "b"*64,
                   UPSTREAM_SOURCE_DIR="/never/read", UPSTREAM_INSTALLER_COMMIT=PIN,
                   UBUNTU_LIVE_IMAGE="ghcr.io/kyth-os/kyth@sha256:" + "c"*64)
        result = subprocess.run(["bash", str(ADAPTER)], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("official Ubuntu", result.stderr)

    def test_installer_declares_distinct_immutable_source_and_stable_target(self):
        adapter = (ROOT / "build/custom-installer-iso.sh").read_text()
        live = (ROOT / "build/configure-live.sh").read_text()
        workflow = (ROOT / ".github/workflows/installer-iso.yml").read_text()
        self.assertIn('source_image="ghcr.io/mrtrickdotorg/rowyn@${IMAGE_DIGEST}"', adapter)
        self.assertIn('INSTALL_SOURCE_IMAGE" =~ ^ghcr\\.io/mrtrickdotorg/rowyn@sha256:', live)
        self.assertIn('--certificate-identity https://github.com/mrtrickdotorg/Rowyn/.github/workflows/build.yml@refs/heads/main', live)
        self.assertIn('target_image="ghcr.io/mrtrickdotorg/rowyn:stable"', adapter)
        self.assertIn('INSTALL_TARGET_IMAGE', adapter)
        self.assertIn('target_image="ghcr.io/mrtrickdotorg/rowyn:stable"', live)
        self.assertIn('ROWYN_TARGET_IMAGE=', live)
        self.assertNotIn('target_image:$source', live)
        self.assertIn("IMAGE_REPOSITORY: ghcr.io/mrtrickdotorg/rowyn", workflow)
        self.assertIn('skopeo inspect --no-creds', workflow)
        self.assertNotIn("podman login ghcr.io", workflow)

    def test_iso_metadata_records_source_digest_and_stable_target_separately(self):
        adapter = (ROOT / "build/custom-installer-iso.sh").read_text()
        metadata = adapter.split("iso.with_suffix('.json').write_text", 1)[1]
        self.assertIn("source_image=os.environ['INSTALL_SOURCE_IMAGE']", metadata)
        self.assertIn("target_image=os.environ['INSTALL_TARGET_IMAGE']", metadata)
        self.assertIn("source_digest=os.environ['IMAGE_DIGEST']", metadata)


class LiveBootcConfigurationTests(unittest.TestCase):
    def test_live_stage_installs_the_regular_ubuntu_desktop_package_set(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        live_stage = containerfile.split("FROM ${UBUNTU_IMAGE} AS live", 1)[1]
        self.assertIn("kubuntu-desktop-live-26.10-amd64.manifest", live_stage)
        configure = (ROOT / "build/configure-live.sh").read_text()
        self.assertIn("install-deb-packages", configure)
        self.assertIn("seed-snaps", configure)
        assembly = (ROOT / "build/assemble-iso.sh").read_text()
        self.assertIn("verify-package-parity", assembly)

    def test_live_stage_explicitly_installs_podman_and_crun_for_bootc_storage(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        live_stage = containerfile.split("FROM ${UBUNTU_IMAGE} AS live", 1)[1]
        package_line = live_stage.split("apt-get install -y --no-install-recommends", 1)[1].split("&&", 1)[0]
        self.assertRegex(package_line, r"\bpodman\b")
        self.assertRegex(package_line, r"\bcrun\b")

    def test_runtime_preflight_rejects_missing_podman_with_hermetic_external_discovery(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-runtime-closure-") as temp:
            root = Path(temp)
            for binary in live.REQUIRED:
                path = root / binary.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\n")
                path.chmod(0o755)
            (root / "usr/lib/ostree/prepare-root.conf").parent.mkdir(parents=True, exist_ok=True)
            (root / "usr/lib/ostree/prepare-root.conf").touch()
            (root / "usr/lib/systemd/boot/efi/systemd-bootx64.efi").parent.mkdir(parents=True, exist_ok=True)
            (root / "usr/lib/systemd/boot/efi/systemd-bootx64.efi").touch()
            for binary in ("rowyn-installerd", "rowyn-installer-shell"):
                path = root / "usr/bin" / binary
                path.write_text("#!/bin/sh\n")
                path.chmod(0o755)

            podman = root / "usr/bin/podman"
            podman.parent.mkdir(parents=True, exist_ok=True)
            podman.write_text("#!/bin/sh\n")
            podman.chmod(0o755)
            (root / "usr/bin/crun").write_text("#!/bin/sh\n")
            (root / "usr/bin/crun").chmod(0o755)
            podman.unlink()
            calls = []

            def runner(*arguments):
                calls.append(arguments)
                if arguments[0] == str(root / "usr/bin/bootc"):
                    return "--source-imgref --target-imgref --bootloader --composefs-backend --sysroot --latest"
                if arguments[0] == str(root / "usr/bin/podman"):
                    return "podman version 5.8.6"
                raise AssertionError("unexpected external command: " + repr(arguments))

            def process_runner(arguments, **kwargs):
                self.assertEqual(arguments[0], str(root / "usr/bin/ldd"))
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

            with self.assertRaisesRegex(ValueError, r"missing required Ubuntu runtime binary: /usr/bin/podman"):
                live.runtime_closure(root=root, runner=runner, process_runner=process_runner)
            self.assertEqual(calls, [], "missing podman must fail before storage/version probes")

    def test_runtime_preflight_uses_bounded_podman_version_probe_not_storage_info(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-runtime-closure-ok-") as temp:
            root = Path(temp)
            for binary in live.REQUIRED:
                path = root / binary.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\n")
                path.chmod(0o755)
            (root / "usr/lib/ostree/prepare-root.conf").parent.mkdir(parents=True, exist_ok=True)
            (root / "usr/lib/ostree/prepare-root.conf").touch()
            (root / "usr/lib/systemd/boot/efi/systemd-bootx64.efi").parent.mkdir(parents=True, exist_ok=True)
            (root / "usr/lib/systemd/boot/efi/systemd-bootx64.efi").touch()
            for binary in ("rowyn-installerd", "rowyn-installer-shell"):
                path = root / "usr/bin" / binary
                path.write_text("#!/bin/sh\n")
                path.chmod(0o755)
            calls = []

            def runner(*arguments):
                calls.append(arguments)
                if arguments[0] == str(root / "usr/bin/bootc"):
                    return "--source-imgref --target-imgref --bootloader --composefs-backend --sysroot --latest"
                if arguments[0] == str(root / "usr/bin/podman") and arguments[1:] == ("--version",):
                    return "podman version 5.8.6"
                raise AssertionError("unexpected external command: " + repr(arguments))

            def process_runner(arguments, **kwargs):
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

            live.runtime_closure(root=root, runner=runner, process_runner=process_runner)
            self.assertIn((str(root / "usr/bin/podman"), "--version"), calls)
            self.assertNotIn((str(root / "usr/bin/podman"), "info"), calls)
            # A copied bootc executable is insufficient if its backend/mount
            # capabilities differ from the pinned installer's contract.
            def missing_backend(*arguments):
                return runner(*arguments).replace("--composefs-backend", "")
            with self.assertRaisesRegex(ValueError, "lacks --composefs-backend"):
                live.runtime_closure(root=root, runner=missing_backend, process_runner=process_runner)
            def missing_mount(*arguments):
                return runner(*arguments).replace("--latest", "")
            with self.assertRaisesRegex(ValueError, "mount lacks --latest"):
                live.runtime_closure(root=root, runner=missing_mount, process_runner=process_runner)

    def test_live_install_diverts_bootctl_so_systemd_boot_trigger_postinst_survives(self):
        # Regression: the manifest install lands shim-signed/grub packages,
        # which fire systemd-boot's dpkg file triggers. Its postinst's
        # remove_shim assigns esp_path="$(bootctl --print-esp-path)" under
        # set -e and dies when bootctl finds no ESP in the build container.
        # configure-live must divert bootctl around the install step only.
        configure = (ROOT / "build/configure-live.sh").read_text()
        divert = configure.index("dpkg-divert --rename --add /usr/bin/bootctl")
        install = configure.index("install-deb-packages")
        undivert = configure.index("dpkg-divert --rename --remove /usr/bin/bootctl")
        self.assertLess(divert, install)
        self.assertLess(install, undivert)
        self.assertIn('"--print-esp-path"', configure)
        self.assertIn('exec /usr/bin/bootctl.distrib "$@"', configure)

    def test_iso_assembly_allows_files_over_4gib(self):
        # Regression: the full Kubuntu live squashfs exceeds the ISO 9660
        # level-2 single-file limit (4 GiB - 1); xorriso aborted the
        # grub-mkrescue run until -iso-level 3 was forwarded to it.
        assemble = (ROOT / "build/assemble-iso.sh").read_text()
        mkrescue = next(
            line for line in assemble.splitlines() if line.startswith("grub-mkrescue")
        )
        self.assertIn("-iso-level 3", mkrescue)
        # The flag must be on the mkisofs side of the -- separator; after
        # it, xorriso is in native command mode and rejects the option.
        self.assertLess(mkrescue.index("-iso-level 3"), mkrescue.index(" -- "))

    def test_live_stage_uses_prepare_root_policy_from_pinned_payload(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        live_stage = containerfile.split("FROM ${UBUNTU_IMAGE} AS live", 1)[1]
        self.assertIn(
            "COPY --from=payload /usr/lib/ostree/prepare-root.conf /usr/lib/ostree/prepare-root.conf",
            live_stage,
        )
        preflight = (ROOT / "build/ubuntu-live-preflight.py").read_text()
        self.assertIn("require_prepare_root_config(root)", preflight)

    def test_runtime_preflight_requires_bootc_prepare_root_config(self):
        validator = getattr(live, "require_prepare_root_config", None)
        self.assertTrue(callable(validator), "live preflight must validate bootc prepare-root policy")
        assert callable(validator)
        with tempfile.TemporaryDirectory(prefix="rowyn-prepare-root-") as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "prepare-root.conf"):
                validator(root)

            for relative in (
                "usr/lib/ostree/prepare-root.conf",
                "etc/ostree/prepare-root.conf",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                self.assertEqual(validator(root), path)
                path.unlink()

    def test_live_dns_uses_persistent_tmpfiles_policy_and_casper_final_check(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        live_stage = containerfile.split("FROM ${UBUNTU_IMAGE} AS live", 1)[1]
        self.assertIn("systemd-resolved", live_stage)

        configure = (ROOT / "build/configure-live.sh").read_text()
        self.assertIn("systemctl enable systemd-resolved", configure)
        self.assertIn("/etc/tmpfiles.d/systemd-resolve.conf", configure)
        self.assertIn(
            "L!+ /etc/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf",
            configure,
        )
        hook_name = "scripts/casper-bottom/99rowyn-resolver"
        self.assertIn(f"{hook_name} <<'EOF'", configure)
        hook = configure.split(f"{hook_name} <<'EOF'", 1)[1].split("\nEOF", 1)[0]
        self.assertIn("systemd-tmpfiles --create --boot /etc/tmpfiles.d/systemd-resolve.conf", hook)
        self.assertIn("/root/etc/resolv.conf", hook)
        self.assertIn("stub-resolv.conf", hook)
        self.assertIn("grep -q 'scripts/casper-bottom/99rowyn-resolver'", configure)

    def test_tmpfiles_repairs_empty_and_wrong_resolver_files_idempotently(self):
        tmpfiles = shutil.which("systemd-tmpfiles")
        self.assertIsNotNone(tmpfiles, "systemd-tmpfiles is required for the live resolver regression")
        assert tmpfiles is not None
        with tempfile.TemporaryDirectory(prefix="rowyn-resolv-tmpfiles-") as temp:
            root = Path(temp)
            rule = root / "etc/tmpfiles.d/systemd-resolve.conf"
            rule.parent.mkdir(parents=True)
            configure = (ROOT / "build/configure-live.sh").read_text()
            policy = configure.split("cat > /etc/tmpfiles.d/systemd-resolve.conf <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
            rule.write_text(policy + "\n", encoding="utf-8")
            resolv = root / "etc/resolv.conf"
            resolv.touch()

            def apply_rule():
                result = subprocess.run(
                    [tmpfiles, f"--root={root}", "--create", "--boot"],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            apply_rule()
            self.assertTrue(resolv.is_symlink())
            self.assertEqual(resolv.readlink(), Path("/run/systemd/resolve/stub-resolv.conf"))
            apply_rule()
            self.assertTrue(resolv.is_symlink())

            resolv.unlink()
            resolv.write_text("wrong\\n", encoding="utf-8")
            apply_rule()
            self.assertTrue(resolv.is_symlink())
            self.assertEqual(resolv.readlink(), Path("/run/systemd/resolve/stub-resolv.conf"))


class LiveNetworkConfigurationTests(unittest.TestCase):
    def test_live_image_installs_netplan_and_generates_policy(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        configure = (ROOT / "build/configure-live.sh").read_text()
        self.assertIn("network-manager netplan.io plasma-nm", containerfile)
        self.assertIn("/usr/bin/python3 /rowyn/ubuntu-live-network.py --root /", configure)
        self.assertIn("netplan generate --root-dir /", configure)

    def test_generated_policy_manages_wired_and_wifi_but_not_other_devices(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-network-") as temp:
            nm_path, netplan_path = network.configure(Path(temp))
            nm_text = nm_path.read_text()
            config = yaml.safe_load(netplan_path.read_text())
            netplan_mode = netplan_path.stat().st_mode & 0o777

        self.assertEqual(nm_path.name, "10-globally-managed-devices.conf")
        self.assertEqual(netplan_mode, 0o600)
        self.assertIn("unmanaged-devices=except:type:ethernet,except:type:wifi,except:type:gsm,except:type:cdma", nm_text)
        self.assertIn("[ifupdown]\nmanaged=true", nm_text)
        self.assertEqual(config["network"]["renderer"], "NetworkManager")
        wired = config["network"]["ethernets"]["rowyn-wired"]
        self.assertEqual(wired["match"]["name"], "en*")
        self.assertTrue(wired["dhcp4"])
        self.assertTrue(wired["dhcp6"])
        self.assertTrue(wired["optional"])

        # Exercise NetworkManager's policy shape: only the named device types
        # are exceptions to the global unmanaged wildcard.
        tokens = nm_text.split("unmanaged-devices=", 1)[1].splitlines()[0].split(",")
        def is_unmanaged(device_type):
            positive = "*" in tokens or all(token.startswith("except:") for token in tokens)
            return positive and f"except:type:{device_type}" not in tokens

        for device_type in ("ethernet", "wifi", "gsm", "cdma"):
            self.assertFalse(is_unmanaged(device_type))
        self.assertTrue(is_unmanaged("bridge"))


class DarkDefaultsTests(unittest.TestCase):
    HELPER = ROOT / "shared/kde-dark-defaults.sh"

    def run_helper(self, root, user=None, home=None):
        command = ["bash", str(self.HELPER), "--root", str(root)]
        if user:
            command += ["--user", user]
        if home:
            command += ["--home", home]
        return subprocess.run(command, text=True, capture_output=True)

    def test_helper_generates_breeze_dark_system_and_user_defaults(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-dark-") as temp:
            root = Path(temp)
            home = root / "home/liveuser"
            home.mkdir(parents=True)
            result = self.run_helper(root, "liveuser", "/home/liveuser")
            self.assertEqual(result.returncode, 0, result.stderr)

            expected = {
                "ColorScheme=BreezeDark",
                "LookAndFeelPackage=org.kde.breezedark.desktop",
                "Theme=breeze-dark",
                "gtk-theme-name=Breeze-Dark",
                "gtk-icon-theme-name=breeze-dark",
            }
            files = [
                root / "etc/xdg/kdeglobals",
                root / "etc/skel/.config/kdeglobals",
                home / ".config/kdeglobals",
                root / "etc/gtk-3.0/settings.ini",
                home / ".config/gtk-3.0/settings.ini",
            ]
            for path in files:
                self.assertTrue(path.is_file(), path)
                text = path.read_text()
                for value in expected:
                    if value.startswith("gtk-") and "gtk-3.0" not in str(path):
                        continue
                    if value.startswith("gtk-"):
                        self.assertIn(value, text, path)
                if path.name == "kdeglobals":
                    self.assertIn("ColorScheme=BreezeDark", text)
                    self.assertIn("LookAndFeelPackage=org.kde.breezedark.desktop", text)

            self.assertIn("[Theme]\nname=default", (root / "etc/xdg/plasmarc").read_text())
            self.assertIn("library=org.kde.breeze", (root / "etc/xdg/kwinrc").read_text())
            self.assertIn("theme=Breeze", (root / "etc/xdg/kwinrc").read_text())
            self.assertIn("cursorTheme=breeze_cursors", (root / "etc/xdg/kcminputrc").read_text())
            self.assertIn("Theme=org.kde.breeze.desktop", (root / "etc/xdg/ksplashrc").read_text())

            self.assertTrue((root / "usr/share/rowyn/kde-dark-defaults/kdeglobals").is_file())

    def test_helper_does_not_replace_existing_user_preferences_and_handles_var_home(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-dark-") as temp:
            root = Path(temp)
            result = self.run_helper(root, "rowyn", "/var/home/rowyn")
            self.assertEqual(result.returncode, 0, result.stderr)
            tmpfiles = (root / "usr/lib/tmpfiles.d/zz-rowyn-kde-dark.conf").read_text()
            self.assertIn("/var/home/rowyn/.config/kdeglobals", tmpfiles)
            self.assertIn("/usr/share/rowyn/kde-dark-defaults/kdeglobals", tmpfiles)
            self.assertIn("d /var/home/rowyn/.config/gtk-3.0 0700 rowyn rowyn -", tmpfiles)
            self.assertIn("d /var/home/rowyn/.config/gtk-4.0 0700 rowyn rowyn -", tmpfiles)

        with tempfile.TemporaryDirectory(prefix="rowyn-dark-") as temp:
            root = Path(temp)
            existing = root / "var/home/rowyn/.config/kdeglobals"
            existing.parent.mkdir(parents=True)
            existing.write_text("[General]\nColorScheme=MyUserChoice\n")
            result = self.run_helper(root, "rowyn", "/var/home/rowyn")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(existing.read_text(), "[General]\nColorScheme=MyUserChoice\n")

    def test_both_image_paths_install_assets_and_run_shared_helper_after_rootfs(self):
        payload = (ROOT / "Containerfile").read_text()
        live = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        adapter = (ROOT / "build/custom-installer-iso.sh").read_text()
        configure = (ROOT / "build/configure-live.sh").read_text()
        for text in (payload, live):
            for package in ("breeze", "breeze-gtk-theme", "breeze-icon-theme", "kde-style-breeze", "kde-cli-tools"):
                self.assertIn(package, text)
        self.assertLess(payload.index("/ctx/shared/bootc-rootfs.sh"),
                        payload.index("/ctx/shared/kde-dark-defaults.sh"))
        self.assertIn("kde-dark-defaults.sh", adapter)
        self.assertIn("kde-dark-defaults.sh", configure)
        self.assertIn("--user liveuser", configure)


class InstalledPayloadDesktopTests(unittest.TestCase):
    def test_payload_excludes_the_first_login_welcome_launcher(self):
        # Plasma 6.7 launches Welcome from a KDED plugin, not XDG autostart.
        # Exclude the optional package instead of writing an ineffective Hidden=true file.
        payload = (ROOT / "Containerfile").read_text()
        purge = re.search(r"apt-get purge -y ([^\n]+)", payload)
        assert purge is not None
        self.assertIn("plasma-welcome", purge.group(1).split())
        self.assertLess(purge.start(), payload.index("/ctx/shared/bootc-rootfs.sh"))
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("! command -v plasma-welcome", workflow)
        self.assertIn("test ! -e /usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kded/kded_plasma_welcome.so", workflow)

    def test_payload_netplan_selects_the_enabled_desktop_network_manager(self):
        # Without renderer, Netplan denies Ethernet to NM although networkd is masked.
        payload = (ROOT / "Containerfile").read_text()
        match = re.search(r"printf '([^']+)' > /etc/netplan/90-default.yaml", payload)
        assert match is not None
        generated = subprocess.run(["bash", "-c", "printf '" + match.group(1) + "'"],
                                   check=True, text=True, capture_output=True).stdout
        config = yaml.safe_load(generated)["network"]
        self.assertEqual(config.get("renderer"), "NetworkManager")
        # Renderer only: wired DHCP comes from the single rowyn-wired NM
        # profile. A netplan ethernets block would add two more wired
        # profiles for the same NIC (netplan-all-en / netplan-all-eth).
        self.assertNotIn("ethernets", config)
        self.assertIn("id=rowyn-wired", payload)
        self.assertIn("chmod 0600 /etc/netplan/90-default.yaml", payload)
        self.assertIn("netplan generate --root-dir /", payload)

    def test_installed_payload_has_bootc_container_storage_runtime(self):
        # A working live installer did not provide Podman to installed bootc upgrade.
        payload = (ROOT / "Containerfile").read_text()
        system = payload.split("FROM base AS system", 1)[1]
        packages = re.search(r"apt install -y (.*?) &&", system, re.DOTALL)
        assert packages is not None
        for package in ("podman", "crun"):
            self.assertIn(package, packages.group(1).split())
        self.assertLess(system.index("apt install -y"), system.index("/ctx/shared/bootc-rootfs.sh"))

    def test_payload_smoke_checks_bootc_runtime_and_effective_netplan_renderer(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
        smoke = next(step["run"] for job in workflow["jobs"].values()
                     for step in job.get("steps", [])
                     if step.get("name") == "Inspect image and run image smoke checks")
        for check in ("podman --version", "crun --version", "netplan get network.renderer",
                      "netplan generate --root-dir /", "netplan-all-en.nmconnection",
                      "netplan-all-eth.nmconnection", "NM_UNMANAGED"):
            self.assertIn(check, smoke)

    @unittest.skipUnless(os.environ.get("ROWYN_TEST_UBUNTU_NETWORK_POLICY") == "1",
                         "opt-in real Ubuntu Netplan generation fixture")
    def test_real_ubuntu_netplan_no_longer_denies_desktop_ethernet(self):
        payload = (ROOT / "Containerfile").read_text()
        match = re.search(r"printf '([^']+)' > /etc/netplan/90-default.yaml", payload)
        assert match is not None
        fixed = subprocess.run(["bash", "-c", "printf '" + match.group(1) + "'"],
                               check=True, text=True, capture_output=True).stdout
        broken = fixed.replace("  renderer: NetworkManager\n", "")
        self.assertNotEqual(broken, fixed)
        with tempfile.TemporaryDirectory(prefix="rowyn-netplan-") as temp:
            base = Path(temp)
            for state, text in (("broken", broken), ("fixed", fixed)):
                config = base / state / "etc/netplan/90-default.yaml"
                config.parent.mkdir(parents=True)
                config.write_text(text)
                config.chmod(0o600)
            image = os.environ.get("ROWYN_TEST_UBUNTU_COMPONENT_IMAGE",
                                   "localhost/rowyn-ubuntu-components:verified")
            result = subprocess.run([
                "podman", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
                "-v", f"{base}:/fixture:Z", image, "-ec",
                "netplan generate --root-dir /fixture/broken; netplan generate --root-dir /fixture/fixed",
            ], text=True, capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rules = "run/udev/rules.d/90-netplan.rules"
            self.assertIn('NM_UNMANAGED}=\"1\"', (base / "broken" / rules).read_text())
            fixed_rules = base / "fixed" / rules
            if fixed_rules.exists():
                self.assertNotIn('NM_UNMANAGED}=\"1\"', fixed_rules.read_text())
            for group in ("all-en", "all-eth"):
                profile = base / "fixed/run/NetworkManager/system-connections" / f"netplan-{group}.nmconnection"
                self.assertTrue(profile.is_file(), profile)
                self.assertIn("method=auto", profile.read_text())

    HELPER = ROOT / "shared/installed-desktop-defaults.sh"

    def run_helper(self, root):
        return subprocess.run(
            ["bash", str(self.HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
        )

    def seed_units(self, root):
        for name in (
            "sddm-backgrounds-cache.service",
            "sddm-backgrounds-cache.path",
            "sddm-backgrounds-cache.timer",
        ):
            path = root / "usr/lib/systemd/system" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[Unit]\nDescription=fixture\n")

    def test_helper_writes_kde_x11_sddm_defaults_and_masks_only_unused_budgie_cache(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-desktop-") as temp:
            root = Path(temp)
            self.seed_units(root)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)

            config = (root / "etc/sddm.conf.d/20-rowyn-desktop.conf").read_text()
            self.assertIn("DisplayServer=x11", config)
            self.assertIn("Current=breeze", config)
            self.assertIn("DisplayCommand=/usr/share/sddm/scripts/Xsetup", config)
            self.assertIn("GreeterEnvironment=QT_IM_MODULE=qtvirtualkeyboard", config)
            self.assertNotIn("ubuntu-budgie", config)
            self.assertNotIn("budgie-sddm-display-setup", config)

            for name in (
                "sddm-backgrounds-cache.service",
                "sddm-backgrounds-cache.path",
                "sddm-backgrounds-cache.timer",
            ):
                masked = root / "etc/systemd/system" / name
                self.assertTrue(masked.is_symlink(), masked)
                self.assertEqual(masked.resolve(), Path("/dev/null"))
            self.assertFalse((root / "etc/systemd/system/sddm.service").exists())

    def test_absent_budgie_cache_units_do_not_block_breeze_defaults(self):
        # Explicit Breeze selection can leave Budgie's optional package absent.
        with tempfile.TemporaryDirectory(prefix="rowyn-no-budgie-") as temp:
            root = Path(temp)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Current=breeze", (root / "etc/sddm.conf.d/20-rowyn-desktop.conf").read_text())
            for name in ("sddm-backgrounds-cache.service", "sddm-backgrounds-cache.path", "sddm-backgrounds-cache.timer"):
                self.assertFalse((root / "etc/systemd/system" / name).is_symlink())

    def test_helper_installs_late_resolver_override_without_writing_usr(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-resolver-") as temp:
            root = Path(temp)
            self.seed_units(root)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = (root / "etc/tmpfiles.d/systemd-resolve.conf").read_text()
            self.assertIn("L!+ /etc/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf", policy)
            self.assertFalse((root / "usr/lib/tmpfiles.d/resolv-conf.conf").exists())

    def test_tmpfiles_repairs_empty_and_regular_resolver_in_installed_fixture(self):
        tmpfiles = shutil.which("systemd-tmpfiles")
        self.assertIsNotNone(tmpfiles, "systemd-tmpfiles is required for resolver regression")
        assert tmpfiles is not None
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-resolv-tmpfiles-") as temp:
            root = Path(temp)
            self.seed_units(root)
            vendor = root / "usr/lib/tmpfiles.d/systemd-resolve.conf"
            vendor.parent.mkdir(parents=True, exist_ok=True)
            vendor.write_text("L! /etc/resolv.conf - - - - /run/systemd/resolve/stub-resolv.conf\n")
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            resolv = root / "etc/resolv.conf"
            resolv.touch()

            def apply_rule():
                applied = subprocess.run(
                    [tmpfiles, f"--root={root}", "--create", "--boot"],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(applied.returncode, 0, applied.stderr)

            apply_rule()
            self.assertTrue(resolv.is_symlink())
            self.assertEqual(resolv.readlink(), Path("/run/systemd/resolve/stub-resolv.conf"))
            resolv.unlink()
            resolv.write_text("stale\\n", encoding="utf-8")
            apply_rule()
            self.assertTrue(resolv.is_symlink())
            self.assertEqual(resolv.readlink(), Path("/run/systemd/resolve/stub-resolv.conf"))

    def test_containerfile_installs_xorg_and_breeze_theme_before_normalization(self):
        payload = (ROOT / "Containerfile").read_text()
        package_start = payload.index("apt-get install -y", payload.index("# --- KDE Plasma package layer"))
        package_end = payload.index("rm -rf /var/lib/apt/lists", package_start)
        package_block = payload[package_start:package_end]
        for package in ("xserver-xorg", "sddm-theme-breeze"):
            self.assertRegex(package_block, rf"\b{re.escape(package)}\b")
        self.assertLess(payload.index("installed-desktop-defaults.sh"), payload.index("# --- Rowyn identity"))
        self.assertLess(payload.index("/ctx/shared/bootc-rootfs.sh"), payload.index("installed-desktop-defaults.sh"))

    @unittest.skipUnless(
        os.environ.get("ROWYN_TEST_UBUNTU_PACKAGE_CLOSURE"),
        "set ROWYN_TEST_UBUNTU_PACKAGE_CLOSURE=1 for the isolated Ubuntu package closure",
    )
    def test_pinned_ubuntu_fixture_has_xorg_breeze_theme_and_qml_closure(self):
        script = r'''set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
apt-get install -y --no-install-recommends sddm xserver-xorg sddm-theme-breeze
test -x /usr/bin/Xorg
test -x /usr/lib/xorg/Xorg
test -f /usr/share/sddm/themes/breeze/Main.qml
test -f /usr/share/sddm/themes/breeze/metadata.desktop
test -f /usr/share/sddm/themes/breeze/theme.conf
test -f /usr/share/sddm/scripts/Xsetup
test -f /usr/share/wayland-sessions/plasma.desktop
'''
        result = subprocess.run(
            ["podman", "run", "--rm", "--network=host", "docker.io/library/ubuntu:26.10", "bash", "-c", script],
            text=True,
            capture_output=True,
            timeout=900,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LiveScreenLockTests(unittest.TestCase):
    def test_liveuser_screen_lock_override_is_after_user_creation_and_casper_reasserted(self):
        configure = (ROOT / "build/configure-live.sh").read_text()
        user_creation = configure.index(
            "useradd --uid 1000 --create-home --shell /bin/bash --groups audio,video,render liveuser"
        )
        live_config = configure.index("/home/liveuser/.config/kscreenlockerrc")
        casper_config = configure.index("cat > /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-screenlock")
        self.assertLess(user_creation, live_config)
        self.assertLess(live_config, casper_config)

        self.assertIn(
            "[Daemon]\nAutolock=false\nLockOnResume=false",
            configure,
        )
        self.assertIn("chmod 755 /usr/share/initramfs-tools/scripts/casper-bottom/99rowyn-screenlock", configure)
        self.assertIn("grep -q 'scripts/casper-bottom/99rowyn-screenlock'", configure)
        hook = configure.split("scripts/casper-bottom/99rowyn-screenlock <<'EOF'", 1)[1].split("\nEOF", 1)[0]
        self.assertNotIn("install -d", hook, "initramfs must not require the coreutils install binary")
        self.assertIn("chroot /root chown liveuser:liveuser", hook)
        self.assertNotIn("/etc/xdg/kscreenlockerrc", configure)

    def test_screen_lock_override_is_not_in_installed_payload_or_shared_defaults(self):
        payload = (ROOT / "Containerfile").read_text()
        helper = (ROOT / "shared/kde-dark-defaults.sh").read_text()
        self.assertNotIn("kscreenlockerrc", payload)
        self.assertNotIn("Autolock=false", payload)
        self.assertNotIn("kscreenlockerrc", helper)
        self.assertNotIn("Autolock=false", helper)

    def test_live_screen_lock_change_does_not_expand_sudo_or_password_policy(self):
        configure = (ROOT / "build/configure-live.sh").read_text()
        block_start = configure.index("# The live installer session must not lock itself")
        block_end = configure.index("cat > /etc/casper.conf", block_start)
        screen_lock_block = configure[block_start:block_end]
        self.assertNotIn("sudo", screen_lock_block)
        self.assertNotIn("passwd", screen_lock_block)
        self.assertNotIn("chpasswd", screen_lock_block)


class CasperProtectionTests(unittest.TestCase):
    def runner(self, mounts, devices, loops=None):
        calls = []
        def run(*args):
            calls.append(args)
            if args[0] == "/usr/bin/findmnt":
                path = args[-1]
                if path not in mounts:
                    raise subprocess.CalledProcessError(1, args)
                return mounts[path]
            if args[0] == "/usr/bin/lsblk":
                return json.dumps(dict(blockdevices=devices[args[-1]]))
            if args[0] == "/usr/sbin/losetup":
                assert loops is not None
                return json.dumps(dict(loopdevices=[dict(name=args[-1], **{"back-file": loops[args[-1]]})]))
            raise AssertionError(args)
        return run, calls

    def test_live_usb_whole_disk_and_partition_are_protected(self):
        devices = {"/dev/sda1": [dict(name="/dev/sda1", type="part", children=[dict(name="/dev/sda", type="disk")])],
                   "/dev/sda": [dict(name="/dev/sda", type="disk")],
                   "/dev/nvme0n1": [dict(name="/dev/nvme0n1", type="disk")]}
        run, calls = self.runner({"/cdrom": "/dev/sda1"}, devices)
        live.assert_safe_target("/dev/nvme0n1", run)
        for target in ("/dev/sda", "/dev/sda1"):
            with self.assertRaisesRegex(ValueError, "overlaps"):
                live.assert_safe_target(target, run)
        self.assertTrue(all(a[0] in ("/usr/bin/findmnt", "/usr/bin/lsblk") for a in calls))

    def test_loop_iso_backing_file_resolves_physical_media(self):
        devices = {"/dev/loop0": [dict(name="/dev/loop0", type="loop")],
                   "/dev/sda2": [dict(name="/dev/sda2", type="part", children=[dict(name="/dev/sda", type="disk")])],
                   "/dev/sda": [dict(name="/dev/sda", type="disk")]}
        run, _ = self.runner({"/cdrom": "/dev/loop0", "/isodevice": "/dev/sda2", "/isodevice/rowyn.iso": "/dev/sda2"},
                             devices, {"/dev/loop0": "/isodevice/rowyn.iso"})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            live.assert_safe_target("/dev/sda", run)

    def test_unresolved_and_missing_live_media_fail_closed(self):
        for mounts, devices, loops in (({}, {}, {}), ({"/cdrom": "overlay"}, {}, {}),
            ({"/cdrom": "/dev/loop0", "/bad.iso": "overlay"},
             {"/dev/loop0": [dict(name="/dev/loop0", type="loop")]}, {"/dev/loop0": "/bad.iso"}),
            ({"/cdrom": "/dev/loop0", "/cycle.iso": "/dev/loop0"},
             {"/dev/loop0": [dict(name="/dev/loop0", type="loop")]}, {"/dev/loop0": "/cycle.iso"})):
            run, _ = self.runner(mounts, devices, loops)
            with self.assertRaises(ValueError):
                live.assert_safe_target("/dev/sda", run)


class InstallerServiceSandboxContractTests(unittest.TestCase):
    """Model systemd's path provisioning without touching host services.

    This is deliberately not presented as a mount-namespace test: the
    contract model covers ProtectSystem/StateDirectory/RuntimeDirectory path
    semantics, while systemd-analyze verification covers unit syntax.
    """

    ORIGINAL_SERVICE = (
        "[Service]\n"
        "ProtectSystem=strict\n"
        "ReadWritePaths=/run/kyth-installer /var/tmp /var/log\n"
        "EnvironmentFile=-/etc/kyth-installer.env\n"
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        "UMask=0077\n"
    )

    def _paths(self, service):
        values = {}
        for line in service.splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key, []).append(value.split())
        return values

    def _model_mkdir(self, service, root, absolute_path):
        paths = self._paths(service)
        requested = Path(absolute_path)
        writable = {Path(path) for group in paths.get("ReadWritePaths", []) for path in group}
        writable.update(
            Path("/var/lib") / name
            for group in paths.get("StateDirectory", [])
            for name in group
        )
        writable.update(
            Path("/run") / name
            for group in paths.get("RuntimeDirectory", [])
            for name in group
        )
        allowed = any(requested == path or path in requested.parents for path in writable)
        if paths.get("ProtectSystem") == [["strict"]] and not allowed:
            raise PermissionError(f"modeled ProtectSystem=strict denial: {absolute_path}")
        translated = root / requested.relative_to("/")
        translated.mkdir(parents=True, exist_ok=True)

    def test_original_denies_bootc_required_mkdir_and_fix_provisions_exact_paths(self):
        fixed = port.adapt_installer_service(self.ORIGINAL_SERVICE)
        with tempfile.TemporaryDirectory(prefix="rowyn-service-sandbox-") as temp:
            root = Path(temp)
            with self.assertRaises(PermissionError):
                self._model_mkdir(self.ORIGINAL_SERVICE, root, "/var/lib/containers")
            with self.assertRaises(PermissionError):
                self._model_mkdir(self.ORIGINAL_SERVICE, root, "/run/bootc")

            self._model_mkdir(fixed, root, "/var/lib/containers")
            self._model_mkdir(fixed, root, "/run/bootc")
            self.assertTrue((root / "var/lib/containers").is_dir())
            self.assertTrue((root / "run/bootc").is_dir())
            with self.assertRaises(PermissionError):
                self._model_mkdir(fixed, root, "/var/lib/unrelated")

    def test_fixed_service_preserves_runtime_and_mode_boundaries(self):
        fixed = port.adapt_installer_service(self.ORIGINAL_SERVICE)
        paths = self._paths(fixed)
        self.assertEqual(paths["ProtectSystem"], [["strict"]])
        self.assertEqual(paths["RuntimeDirectory"], [["bootc"]])
        self.assertEqual(paths["RuntimeDirectoryMode"], [["0750"]])
        self.assertEqual(paths["StateDirectory"], [["containers"]])
        self.assertEqual(paths["StateDirectoryMode"], [["0700"]])
        self.assertEqual(
            paths["ReadWritePaths"],
            [["/run/kyth-installer", "/var/tmp", "/var/log", "/var/lib/containers", "/run/bootc"]],
        )
        self.assertNotIn("/run", paths["ReadWritePaths"][0])
        self.assertNotIn("/var", paths["ReadWritePaths"][0])
        self.assertNotIn("ExecStartPre=", fixed)
        self.assertNotIn("ProtectSystem=off", fixed)

    def test_fixed_unit_passes_systemd_effective_unit_verification(self):
        if shutil.which("systemd-analyze") is None:
            self.skipTest("systemd-analyze is unavailable")
        fixed = port.adapt_installer_service(self.ORIGINAL_SERVICE)
        unit = "[Unit]\nDescription=Rowyn service fixture\n\n" + fixed
        unit += "ExecStart=/bin/true\n\n[Install]\nWantedBy=multi-user.target\n"
        with tempfile.TemporaryDirectory(prefix="rowyn-systemd-unit-") as temp:
            path = Path(temp) / "kyth-installerd.service"
            path.write_text(unit)
            result = subprocess.run(
                ["systemd-analyze", "verify", str(path)],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_adapter_fails_closed_when_pinned_anchor_changes(self):
        for anchor in (
            "EnvironmentFile=-/etc/kyth-installer.env",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "UMask=0077",
            "ReadWritePaths=/run/kyth-installer /var/tmp /var/log",
        ):
            mutated = self.ORIGINAL_SERVICE.replace(anchor, anchor + "-changed")
            with self.assertRaises(ValueError, msg=anchor):
                port.adapt_installer_service(mutated)


@unittest.skipUnless(os.environ.get("ROWYN_TEST_UPSTREAM_REPO"), "set ROWYN_TEST_UPSTREAM_REPO for pinned-source integration")
class PinnedSourceTests(unittest.TestCase):
    def test_exact_object_export_is_repeatable_and_ports_actual_source(self):
        repo = Path(os.environ["ROWYN_TEST_UPSTREAM_REPO"])
        original = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
        with tempfile.TemporaryDirectory(prefix="rowyn-pinned-") as temp:
            snapshots = []
            for name in ("first", "second"):
                root = Path(temp) / name
                port.export_source(repo, root)
                port.port_source(root, "sha256:" + "b"*64)
                snapshots.append({str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})
                self.assertFalse((root / "installer/Containerfile").exists())
                self.assertFalse((root / "build_files/build-live-iso.sh").exists())
                self.assertFalse((root / "src/rowyn-installer-web/node_modules").exists())
                executor = (root / EXECUTOR).read_text()
                prepare = executor[executor.index("            Phase::Prepare => {"):executor.index("            Phase::Storage =>")]
                self.assertLess(prepare.index("self.verify_install_source(phase)?;"), prepare.index("rowyn-live-preflight"))
                self.assertIn('Command::new("/usr/bin/rowyn-cosign")', executor)
                self.assertIn('"--certificate-identity"', executor)
                self.assertIn(port.IDENTITY, executor)
                image = executor[executor.index('    fn execute_image('):]
                self.assertLess(image.index('self.verify_install_source(phase)?;'), image.index('self.check_storage_preflight(phase, cancellation)?;'))
                crate = root / "src/rowyn-installer-web/src-tauri"
                accounts = (crate / "src/installer_accounts.rs").read_text()
                self.assertNotIn("wheel", accounts)
                self.assertIn('"sudo,video,audio,render"', accounts)
                self.assertNotIn('apply_boot_ordering_fix(&plan.target_root)?;', (crate / "src/installer_configuration.rs").read_text())
                self.assertIn('"/cdrom"', (crate / "src/installer_storage.rs").read_text())
                daemon = (crate / "src/installer_daemon.rs").read_text()
                dispatch = daemon[daemon.index('    fn dispatch('):]
                self.assertLess(dispatch.index('return (403'), dispatch.index('let disk ='))
                service = (root / 'build_files/rowyn-installerd.service').read_text()
                self.assertIn('EnvironmentFile=/etc/rowyn-installer.env', service)
                self.assertIn('RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6', service)
                self.assertIn('RuntimeDirectory=bootc\n', service)
                # The launcher owns the session-token tree. systemd must not
                # re-own it as root:root and cut off the live UI's API access.
                self.assertNotIn('rowyn-installer', next(
                    line for line in service.splitlines() if line.startswith('RuntimeDirectory=')
                ))
                self.assertIn('RuntimeDirectoryMode=0750', service)
                self.assertIn('StateDirectory=containers', service)
                self.assertIn('StateDirectoryMode=0700', service)
                self.assertIn(
                    'ReadWritePaths=/run/rowyn-installer /var/tmp /var/log /var/lib/containers /run/bootc',
                    service,
                )
                self.assertNotIn('ReadWritePaths=/run\n', service)
                self.assertNotIn('ReadWritePaths=/var\n', service)
                if shutil.which("systemd-analyze"):
                    verify = service.replace(
                        "ExecStart=/usr/bin/rowyn-installerd",
                        "ExecStart=/bin/true",
                        1,
                    )
                    unit = root / "build_files/rowyn-installerd-verify.service"
                    unit.write_text(verify)
                    result = subprocess.run(
                        ["systemd-analyze", "verify", str(unit)],
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                # Legacy junk cut: system::installer_source is pruned
                # from the export — no shipped frontend references it,
                # and the executor carries its own release constants.
                self.assertFalse(
                    (root / "src/rowyn-shared-rs/src/system/installer_source.rs").exists())
                with self.assertRaises(ValueError):
                    port.export_source(repo, root)

            self.assertEqual(*snapshots)
        self.assertEqual(original, subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]))

    def test_adapted_pinned_source_uses_ubuntu_utility_paths(self):
        repo = Path(os.environ["ROWYN_TEST_UPSTREAM_REPO"])
        expected = {
            Path(path).name: path
            for path in live.REQUIRED
            if Path(path).name in {"blockdev", "blkid", "findmnt", "lsblk", "mount", "umount"}
        }
        with tempfile.TemporaryDirectory(prefix="rowyn-paths-") as temp:
            root = Path(temp) / "source"
            port.export_source(repo, root)
            port.port_source(root, "sha256:" + "b"*64)
            source = root / "src/rowyn-installer-web/src-tauri/src"
            for path in sorted(source.glob("*.rs")):
                text = path.read_text()
                for utility, expected_path in expected.items():
                    actual = set(re.findall(rf"/usr/(?:bin|sbin)/{re.escape(utility)}", text))
                    self.assertTrue(
                        actual <= {expected_path},
                        f"{path.relative_to(root)} uses non-Ubuntu {utility}: {sorted(actual)}",
                    )
            self.assertTrue(
                any("/usr/sbin/blockdev" in path.read_text() for path in source.glob("*.rs")),
                "pinned production source did not retain a blockdev sector-size probe",
            )

    def test_adapted_stream_merges_stderr_into_failure_output(self):
        repo = Path(os.environ["ROWYN_TEST_UPSTREAM_REPO"])
        with tempfile.TemporaryDirectory(prefix="rowyn-stream-") as temp:
            exported = Path(temp) / "source"
            port.export_source(repo, exported)
            port.port_source(exported, "sha256:" + "b" * 64)
            stream = (exported / "src/rowyn-installer-web/src-tauri/src/installer_stream.rs").read_text()
            self.assertIn("fn merge_stderr_into_stdout", stream)
            for function in ("pub(crate) fn run_command(", "pub(crate) fn run_command_timeout("):
                start = stream.index(function)
                body = stream[start:stream.index("\n}\n", start) + 2]
                self.assertIn("merge_stderr_into_stdout(command);", body)
            self.assertIn("libc::dup2(libc::STDOUT_FILENO, libc::STDERR_FILENO)", stream)

    def test_adapted_sector_probe_runs_with_safe_command_double(self):
        repo = Path(os.environ["ROWYN_TEST_UPSTREAM_REPO"])
        if shutil.which("rustc") is None:
            self.skipTest("rustc is required for the source-extracted probe")
        with tempfile.TemporaryDirectory(prefix="rowyn-probe-") as temp:
            root = Path(temp)
            exported = root / "source"
            port.export_source(repo, exported)
            port.port_source(exported, "sha256:" + "b"*64)
            source = (exported / "src/rowyn-installer-web/src-tauri/src/installer_disk.rs").read_text()
            start = source.index("pub(crate) fn logical_sector_size")
            function = source[start:source.index("\n}\n", start) + 2]
            self.assertIn('Command::new("/usr/sbin/blockdev")', function)

            double = root / "blockdev-double"
            log = root / "blockdev.args"
            double.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > {log}\n"
                "printf '4096\\n'\n"
            )
            double.chmod(0o755)
            probe = root / "probe.rs"
            command = function.replace(
                'Command::new("/usr/sbin/blockdev")',
                f"Command::new({json.dumps(str(double))})",
            )
            probe.write_text(
                "use std::process::Command;\n"
                + command
                + "\nfn main() { println!(\"{}\", logical_sector_size(\"/dev/vda\").unwrap()); }\n"
            )
            binary = root / "probe"
            compiled = subprocess.run(
                ["rustc", str(probe), "-o", str(binary)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "4096")
            self.assertEqual(log.read_text().splitlines(), ["--getss", "/dev/vda"])


if __name__ == "__main__":
    unittest.main()
