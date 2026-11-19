#!/usr/bin/env python3
"""Coverage for Rowyn's per-boot GPU driver selection and NVIDIA packaging.

One image serves every machine: Canonical's prebuilt open nvidia modules and
nouveau are both installed, and shared/gpu-select.sh picks one at boot from
the PCI bus. These tests run the helper against fake sysfs trees and pin the
Containerfile and workflow wiring that keeps the choice honest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SELECT = ROOT / "shared/gpu-select.sh"
DEFAULTS = ROOT / "shared/nvidia-defaults.sh"
DEVIDS = ROOT / "shared/nvidia-open-devids.py"
UNIT = ROOT / "shared/rowyn-gpu-select.service"
INITRAMFS = ROOT / "shared/initramfs.sh"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

NVIDIA, INTEL, AMD = "0x10de", "0x8086", "0x1002"
VGA, DISPLAY_3D, AUDIO = "0x030000", "0x030200", "0x040300"
RTX_2080_TI = "0x1e04"   # Turing: open modules
RTX_4090 = "0x2684"      # Ada: open modules
GTX_1080 = "0x1b80"      # Pascal: legacy, nouveau
GTX_830M = "0x1340"      # Maxwell: legacy, nouveau
SUPPORTED_IDS = "# open-module devices\n1e04\n2684\n"

PACKAGES = [
    "linux-modules-nvidia-615-open-generic", "nvidia-kernel-common-615",
    "libnvidia-gl-615", "libnvidia-compute-615", "libnvidia-extra-615",
    "libnvidia-cfg1-615", "libnvidia-decode-615", "libnvidia-encode-615",
    "libnvidia-fbc1-615", "nvidia-utils-615", "nvidia-compute-utils-615",
    "xserver-xorg-video-nvidia-615",
]
SLEEP_UNITS = [
    "nvidia-suspend", "nvidia-resume", "nvidia-hibernate",
    "nvidia-suspend-then-hibernate", "nvidia-powerd",
]
OMITTED = ["nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm"]


def add_pci(sysfs: Path, slot: str, vendor: str, device: str, cls: str) -> None:
    dev = sysfs / "bus/pci/devices" / slot
    dev.mkdir(parents=True)
    (dev / "vendor").write_text(vendor + "\n")
    (dev / "device").write_text(device + "\n")
    (dev / "class").write_text(cls + "\n")


def active_lines(path: Path) -> list[str]:
    return [l for l in path.read_text().splitlines() if l and not l.startswith("#")]


class GpuSelectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sysfs = self.tmp / "sys"
        (self.sysfs / "bus/pci/devices").mkdir(parents=True)
        self.ids = self.tmp / "ids"
        self.ids.write_text(SUPPORTED_IDS)
        self.out = self.tmp / "run/modprobe.d/rowyn-gpu.conf"

    def run_select(self, *extra: str, ids: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(SELECT), "--sysfs-root", str(self.sysfs),
             "--output", str(self.out), "--supported-ids", str(ids or self.ids), *extra],
            text=True, capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def select(self, **kw) -> list[str]:
        result = self.run_select(**kw)
        self.assertEqual(result.returncode, 0, result.stderr)
        return active_lines(self.out) if self.out.exists() else []

    def test_script_is_executable_and_strict(self):
        self.assertTrue(os.access(SELECT, os.X_OK))
        self.assertIn("set -euo pipefail", SELECT.read_text(encoding="utf-8"))

    def test_rejects_unknown_arguments(self):
        for args in (["--bogus"], ["--output"]):
            result = subprocess.run(["/bin/bash", str(SELECT), *args],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, args)

    def test_supported_nvidia_gets_nvidia_and_loses_nouveau(self):
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_2080_TI, VGA)
        lines = self.select()
        self.assertIn("blacklist nouveau", lines)
        self.assertIn("options nouveau modeset=0", lines)
        self.assertFalse([l for l in lines if re.match(r"blacklist nvidia", l)])
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o644)

    def test_3d_controller_class_counts_as_a_gpu(self):
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_4090, DISPLAY_3D)
        self.assertIn("blacklist nouveau", self.select())

    def test_unsupported_nvidia_keeps_nouveau(self):
        for dev in (GTX_1080, GTX_830M):
            with self.subTest(device=dev):
                shutil_rm(self.sysfs / "bus/pci/devices")
                add_pci(self.sysfs, "0000:01:00.0", NVIDIA, dev, VGA)
                lines = self.select()
                for module in ("nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm"):
                    self.assertIn(f"blacklist {module}", lines)
                self.assertNotIn("blacklist nouveau", lines)
                self.assertFalse([l for l in lines if "nouveau" in l])

    def test_no_nvidia_writes_nothing(self):
        add_pci(self.sysfs, "0000:00:02.0", INTEL, "0x9a49", VGA)
        add_pci(self.sysfs, "0000:03:00.0", AMD, "0x73bf", VGA)
        self.assertEqual(self.select(), [])
        self.assertFalse(self.out.exists())

    def test_no_nvidia_removes_a_stale_file(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_text("blacklist nouveau\n")
        add_pci(self.sysfs, "0000:00:02.0", INTEL, "0x9a49", VGA)
        self.select()
        self.assertFalse(self.out.exists())

    def test_empty_bus_and_missing_output_directory_are_fine(self):
        self.assertEqual(self.select(), [])
        self.assertFalse(self.out.exists())

    def test_nvidia_audio_function_is_not_a_gpu(self):
        # The HDMI audio function of a GPU is vendor 10de but class 0x0403.
        add_pci(self.sysfs, "0000:01:00.1", NVIDIA, "0x10f8", AUDIO)
        self.assertEqual(self.select(), [])

    def test_hybrid_igpu_plus_supported_dgpu(self):
        for igpu, slot in ((INTEL, "0000:00:02.0"), (AMD, "0000:00:08.1")):
            with self.subTest(igpu=igpu):
                shutil_rm(self.sysfs / "bus/pci/devices")
                add_pci(self.sysfs, slot, igpu, "0x1234", VGA)
                add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_4090, DISPLAY_3D)
                lines = self.select()
                self.assertIn("blacklist nouveau", lines)
                # The iGPU's driver is never touched.
                self.assertFalse([l for l in lines if re.search(r"i915|xe|amdgpu|radeon", l)])

    def test_hybrid_igpu_plus_unsupported_dgpu(self):
        add_pci(self.sysfs, "0000:00:02.0", INTEL, "0x9a49", VGA)
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, GTX_1080, DISPLAY_3D)
        lines = self.select()
        self.assertIn("blacklist nvidia", lines)
        self.assertNotIn("blacklist nouveau", lines)

    def test_any_supported_nvidia_wins_over_an_unsupported_one(self):
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, GTX_1080, VGA)
        add_pci(self.sysfs, "0000:02:00.0", NVIDIA, RTX_2080_TI, VGA)
        lines = self.select()
        self.assertIn("blacklist nouveau", lines)
        self.assertNotIn("blacklist nvidia", lines)

    def test_hex_case_and_list_formatting_are_tolerated(self):
        self.ids.write_text("# comment\n\n0x1E04  # Turing\n  2684\n")
        add_pci(self.sysfs, "0000:01:00.0", "0x10DE", "0x1E04", "0x030000")
        self.assertIn("blacklist nouveau", self.select())

    def test_missing_id_list_falls_back_to_nouveau(self):
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_2080_TI, VGA)
        result = self.run_select(ids=self.tmp / "absent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("missing", result.stderr)
        lines = active_lines(self.out)
        self.assertIn("blacklist nvidia", lines)
        self.assertNotIn("blacklist nouveau", lines)

    def test_unreadable_device_attributes_do_not_fail_the_boot(self):
        (self.sysfs / "bus/pci/devices/0000:09:00.0").mkdir()
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_2080_TI, VGA)
        self.assertIn("blacklist nouveau", self.select())

    def test_rerun_after_hardware_change_replaces_the_file(self):
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, RTX_2080_TI, VGA)
        self.assertIn("blacklist nouveau", self.select())
        shutil_rm(self.sysfs / "bus/pci/devices")
        add_pci(self.sysfs, "0000:01:00.0", NVIDIA, GTX_1080, VGA)
        lines = self.select()
        self.assertIn("blacklist nvidia", lines)
        self.assertNotIn("blacklist nouveau", lines)
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["rowyn-gpu.conf"])


def shutil_rm(path: Path) -> None:
    import shutil
    shutil.rmtree(path)
    path.mkdir(parents=True)


class DeviceIdListTests(unittest.TestCase):
    CHIPS = {"chips": [
        {"devid": "0x1E04", "name": "RTX 2080 Ti", "features": ["kernelopen", "runtimepm"]},
        {"devid": "0x2684", "subdevid": "0x1", "subvendorid": "0x2", "name": "RTX 4090",
         "features": ["kernelopen"]},
        {"devid": "0x2684", "name": "RTX 4090", "features": ["kernelopen"]},
        {"devid": "0x1340", "name": "GeForce 830M", "legacybranch": "580.xx",
         "features": ["kernelopen"]},
        {"devid": "0x1B80", "name": "GTX 1080", "legacybranch": "580.xx", "features": []},
        {"devid": "0x0020", "name": "RIVA TNT", "legacybranch": "71.86.xx", "features": []},
        {"devid": "0x1D01", "name": "GT 1030", "features": ["hdmi4k"]},
    ]}

    def run_gen(self, data: dict, out: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        out.parent.mkdir(parents=True, exist_ok=True)
        src = out.parent / "supported-gpus.json"
        src.write_text(json.dumps(data))
        return subprocess.run(["python3", "-I", str(DEVIDS), str(src), str(out), *argv],
                              text=True, capture_output=True)

    def test_only_current_branch_kernelopen_chips_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "lib/rowyn/nvidia-open-devids"
            result = self.run_gen(self.CHIPS, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(active_lines(out), ["1e04", "2684"])
            self.assertEqual(out.stat().st_mode & 0o777, 0o644)

    def test_empty_result_fails_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ids"
            result = self.run_gen({"chips": self.CHIPS["chips"][3:]}, out)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(out.exists())

    def test_usage_error(self):
        result = subprocess.run(["python3", "-I", str(DEVIDS)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_generated_list_drives_the_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ids = tmp / "ids"
            self.assertEqual(self.run_gen(self.CHIPS, ids).returncode, 0)
            sysfs = tmp / "sys"
            for dev, expected in (("0x1e04", "blacklist nouveau"), ("0x1b80", "blacklist nvidia")):
                (sysfs / "bus/pci/devices").mkdir(parents=True, exist_ok=True)
                shutil_rm(sysfs / "bus/pci/devices")
                add_pci(sysfs, "0000:01:00.0", NVIDIA, dev, VGA)
                out = tmp / "out.conf"
                result = subprocess.run(
                    ["/bin/bash", str(SELECT), "--sysfs-root", str(sysfs),
                     "--output", str(out), "--supported-ids", str(ids)],
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, active_lines(out))


class NvidiaDefaultsTests(unittest.TestCase):
    def run_defaults(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(DEFAULTS), "--root", str(root), *args],
            text=True, capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def snapshot(self, root: Path) -> dict[str, str]:
        return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}

    def test_rejects_unknown_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_defaults(Path(tmp), "--bogus").returncode, 2)

    def test_module_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_defaults(root).returncode, 0)
            lines = active_lines(root / "usr/lib/modprobe.d/rowyn-nvidia.conf")
            self.assertEqual(lines, [
                "options nvidia-drm modeset=1 fbdev=1",
                "options nvidia NVreg_PreserveVideoMemoryAllocations=1 "
                "NVreg_TemporaryFilePath=/var/tmp NVreg_DynamicPowerManagement=0x02",
            ])

    def test_ships_no_static_nouveau_blacklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # What nvidia-kernel-common-615 installs.
            pkg = root / "usr/lib/modprobe.d/nvidia-graphics-drivers.conf"
            pkg.parent.mkdir(parents=True)
            pkg.write_text("blacklist nouveau\nalias nouveau off\nblacklist nova_core\n")
            kms = root / "etc/modprobe.d/nvidia-graphics-drivers-kms.conf"
            kms.parent.mkdir(parents=True)
            kms.write_text("options nvidia_drm modeset=1\noptions nvidia NVreg_TemporaryFilePath=/var\n")
            keep = root / "etc/modprobe.d/local.conf"
            keep.write_text("options snd-hda-intel power_save=1\n")
            self.assertEqual(self.run_defaults(root).returncode, 0)
            self.assertFalse(pkg.exists())
            self.assertFalse(kms.exists())
            self.assertTrue(keep.exists())
            for conf in (root / "usr/lib/modprobe.d").glob("*.conf"):
                self.assertNotRegex(conf.read_text(), r"(?m)^\s*(blacklist|alias)\s+nouveau")

    def test_sleep_units_are_gated_on_the_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_defaults(root).returncode, 0)
            for unit in SLEEP_UNITS:
                drop_in = root / f"usr/lib/systemd/system/{unit}.service.d/10-rowyn-nvidia-only.conf"
                text = drop_in.read_text()
                self.assertIn("[Unit]", text, unit)
                self.assertIn("ConditionPathExists=/proc/driver/nvidia", text.splitlines(), unit)

    def test_idempotent_and_writes_only_vendor_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_defaults(root).returncode, 0)
            first = self.snapshot(root)
            self.assertEqual(self.run_defaults(root).returncode, 0)
            self.assertEqual(self.snapshot(root), first)
            self.assertTrue(all(p.startswith("usr/lib/") for p in first), list(first))


class UnitTests(unittest.TestCase):
    def test_unit_orders_before_driver_loading(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("DefaultDependencies=no", text)
        self.assertIn("Type=oneshot", text)
        self.assertIn("ExecStart=/usr/libexec/rowyn-gpu-select", text.splitlines())
        self.assertIn("WantedBy=sysinit.target", text.splitlines())
        before = " ".join(l.split("=", 1)[1] for l in text.splitlines() if l.startswith("Before="))
        for unit in ("systemd-udev-trigger.service", "systemd-modules-load.service",
                     "systemd-udevd.service", "sysinit.target"):
            self.assertIn(unit, before.split())
        self.assertNotIn("After=", text)


def containerfile_runs(text: str) -> list[str]:
    return re.split(r"(?m)^(?=RUN |COPY |FROM |LABEL )", text)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.text = CONTAINERFILE.read_text(encoding="utf-8")

    def kde_layer(self) -> str:
        # The payload package layers (docs/updates.md): from the
        # desktop group through the NVIDIA group, the last install.
        # The span covers every frequency group, so the package
        # presence checks see the whole installed set.
        start = self.text.index("# --- KDE Plasma package layer")
        nvidia = self.text.index("linux-modules-nvidia-615-open-generic", start)
        end = self.text.index("rm -rf /var/lib/apt/lists/*", nvidia)
        return self.text[start:end]

    def test_packages_are_in_the_kde_layer_without_dkms(self):
        layer = self.kde_layer()
        group = layer[layer.index("linux-modules-nvidia-615-open-generic") - 200:]
        self.assertIn("apt-get install -y --no-install-recommends", group)
        tokens = set(layer.replace("\\", " ").split())
        for package in PACKAGES:
            self.assertIn(package, tokens)
        installs = re.sub(r"(?m)^\s*#.*$", "", self.text)
        self.assertNotRegex(installs, r"(?m)^\s+\S*dkms\S*\s*\\?$")
        self.assertNotIn("nvidia-driver-615", set(re.sub(r"(?m)^\s*#.*$", "", self.text).replace("\\", " ").split()))
        self.assertNotIn("nvidia-kernel-source", set(re.sub(r"(?m)^\s*#.*$", "", self.text).replace("\\", " ").split()))

    def test_supported_gpu_list_survives_dpkg_doc_excludes(self):
        self.assertIn("path-include=/usr/share/doc/nvidia-kernel-common-*/supported-gpus.json", self.text)
        # The include is written at the top of the NVIDIA group,
        # immediately before the install that extracts the list (the
        # package is named in prose earlier; measure from the printf).
        printf = self.text.index("path-include=/usr/share/doc/nvidia-kernel-common")
        self.assertLess(printf,
                        self.text.index("linux-modules-nvidia-615-open-generic", printf))

    def test_build_gate_checks_modules_for_the_single_kernel(self):
        gate_start = self.text.index("NVIDIA build gate")
        gate = self.text[gate_start:self.text.index("# Canonical timezone state")]
        for needle in (
            'wc -l)" = 1',
            "/usr/lib/modules/${kver}/kernel/nvidia-615-open/${m}.ko",
            'depmod -a "${kver}"',
            'modinfo -k "${kver}"',
            "nvidia-open-devids.py",
            "/usr/lib/rowyn/nvidia-open-devids",
        ):
            self.assertIn(needle, gate)
        # The module package's stray empty /lib/modules/kernel tree must be
        # removed before the single-kernel count runs.
        self.assertLess(gate.index("find /usr/lib/modules/kernel -depth -type d -empty -delete"),
                        gate.index('wc -l)" = 1'))
        self.assertGreater(gate_start, self.text.index("apt-get purge -y plasma-discover-backend-snap"))

    def test_initramfs_is_built_after_the_nvidia_packages_and_gate(self):
        initramfs = self.text.index("/ctx/shared/initramfs.sh")
        self.assertLess(self.text.index("linux-modules-nvidia-615-open-generic"), initramfs)
        self.assertLess(self.text.index("NVIDIA build gate"), initramfs)

    def test_initramfs_omits_gpu_drivers_and_verifies(self):
        text = INITRAMFS.read_text(encoding="utf-8")
        self.assertIn("omit_drivers+=", text)
        line = next(l for l in text.splitlines() if "omit_drivers+=" in l)
        for module in OMITTED:
            self.assertIn(f" {module} ", line)
        self.assertIn("/usr/lib/dracut/dracut.conf.d/", line)
        self.assertLess(text.index("omit_drivers+="), text.index("dracut --force"))
        self.assertRegex(text, r"nouveau\|nvidia")
        self.assertLess(text.index("dracut --force"), text.rindex("nouveau|nvidia"))

    def test_helpers_installed_after_rootfs_normalization(self):
        for call in ("/ctx/shared/nvidia-defaults.sh --root /",
                     "/ctx/shared/gpu-select.sh /usr/libexec/rowyn-gpu-select",
                     "/ctx/shared/rowyn-gpu-select.service /usr/lib/systemd/system/rowyn-gpu-select.service"):
            self.assertIn(call, self.text)
            self.assertGreater(self.text.index(call), self.text.index("/ctx/shared/bootc-rootfs.sh"))
        enable = re.search(r"systemctl enable (rowyn-gpu-select\.service[^&]*?)(?:\n\n|$)", self.text, re.S)
        self.assertIsNotNone(enable)
        enabled = set(enable.group(1).replace("\\", " ").split())
        self.assertTrue({"rowyn-gpu-select.service", "nvidia-suspend.service",
                         "nvidia-resume.service", "nvidia-hibernate.service"} <= enabled)

    def test_shell_scripts_parse(self):
        for script in (SELECT, DEFAULTS, INITRAMFS):
            result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")

    def smoke_block(self) -> str:
        text = WORKFLOW.read_text(encoding="utf-8")
        start = text.index("-euxc '") + len("-euxc '")
        return text[start:text.index("\n          '\n", start)]

    def test_workflow_smoke_checks(self):
        block = self.smoke_block()
        self.assertNotIn("'", block)
        for needle in (
            "kernel/nvidia-615-open/nvidia.ko",
            "/usr/libexec/rowyn-gpu-select",
            "sysinit.target.wants/rowyn-gpu-select.service",
            "/usr/lib/modprobe.d/rowyn-nvidia.conf",
            "/usr/lib/rowyn/nvidia-open-devids",
            "lsinitrd",
            "nouveau",
            "/etc/modprobe.d",
            "/usr/lib/modprobe.d",
            "10-rowyn-nvidia-only.conf",
            "! test -e /usr/sbin/dkms",
        ):
            self.assertIn(needle, block)


if __name__ == "__main__":
    unittest.main()
