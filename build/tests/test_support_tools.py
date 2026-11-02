#!/usr/bin/env python3
"""Coverage for rowyn-report (local scrubbed bug report) and
rowyn-controller-check (controller readiness), plus their wiring."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import py_compile
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "shared/rowyn-report"
CHECK = ROOT / "shared/rowyn-controller-check"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


report = load(REPORT, "rowyn_report")
check = load(CHECK, "rowyn_controller_check")
CTX = {"users": ["alice"], "hosts": ["fancybox"]}


def s(text):
    return report.scrub(text, CTX)


class ScrubTests(unittest.TestCase):
    def test_redaction_classes(self):
        cases = [
            ("git https://bob:hunter2@example.com/x.git", ["hunter2", "bob:"]),
            ("Authorization: Bearer abcDEF123456.xyz", ["abcDEF123456"]),
            ("curl -H 'bearer abcdefgh12345678'", ["abcdefgh12345678"]),
            ("password=hunter2 passwd: s3cret access_token=zzz111", ["hunter2", "s3cret", "zzz111"]),
            ("secret=abc apikey=KEY123 psk=wifipass", ["KEY123", "wifipass"]),
            ("root=UUID=123e4567-e89b-12d3-a456-426614174000 ro", ["123e4567"]),
            ('UUID="123E4567-E89B-12D3-A456-426614174000"', ["426614174000"]),
            ("link aa:bb:cc:dd:ee:ff brd", ["aa:bb:cc:dd:ee:ff"]),
            ("inet 192.168.1.23/24 gw 10.0.0.1", ["192.168.1.23", "10.0.0.1"]),
            ("inet6 fe80::1ff:fe23:4567:890a and 2001:db8::1", ["fe80::", "2001:db8"]),
            ("mail bob@example.com now", ["bob@example.com"]),
            ("ssid=MyHomeWifi", ["MyHomeWifi"]),
            ('wpa: SSID "Cafe Free WiFi" ok', ["Cafe Free"]),
            ("Serial Number: ABC123XYZ", ["ABC123XYZ"]),
            ("serial=5CG1234XYZ", ["5CG1234XYZ"]),
            ("user alice on fancybox", ["alice", "fancybox"]),
            ("/home/alice/docs /var/home/alice/x", ["alice"]),
            ("/home/carol/secret.txt", ["carol"]),
            ("sha " + "ab" * 64, ["ab" * 20]),
            ("machine " + "0123456789abcdef" * 2, ["0123456789abcdef0123"]),
            ("key Zm9vYmFyQmF6MTIzNDU2Nzg5MEFCQ0RFRkdISUo", ["Zm9vYmFyQmF6MTIz"]),
        ]
        for text, banned in cases:
            out = s(text)
            for b in banned:
                self.assertNotIn(b, out, (text, out))

    def test_home_paths_become_tilde(self):
        self.assertEqual(s("/home/alice/x"), "~/x")
        self.assertEqual(s("/var/home/alice/x"), "~/x")
        self.assertEqual(s("/home/carol/x"), "/home/<user>/x")

    def test_digest_abbreviated(self):
        out = s("digest " + "ab" * 64)
        self.assertIn("abababababab…", out)

    def test_loopback_kept(self):
        out = s("127.0.0.1 ::1 0.0.0.0")
        self.assertEqual(out, "127.0.0.1 ::1 0.0.0.0")

    def test_idempotent(self):
        text = "\n".join([
            "https://bob:pw@example.com root=UUID=123e4567-e89b-12d3-a456-426614174000",
            "Authorization: Bearer abcDEF123456", "password=x ssid=Net serial=1234",
            "10.1.2.3 fe80::1 aa:bb:cc:dd:ee:ff bob@example.com",
            "/home/alice/a /home/carol/b alice fancybox", "ab" * 64,
            "Zm9vYmFyQmF6MTIzNDU2Nzg5MEFCQ0RFRkdISUo",
        ])
        once = s(text)
        self.assertEqual(s(once), once)

    def test_ordinary_text_untouched(self):
        text = ("Linux version 6.18.44-generic (gcc 15.2.0) #1 SMP\n"
                "Oct 31 12:34:56 systemd[1]: Started Session 4 of User.\n"
                "/usr/lib/modules/6.18.0/kernel/drivers/gpu/drm/amd/amdgpu/amdgpu.ko\n"
                "PRETTY_NAME=Rowyn 26.10 VARIANT=kde 1.2.3 nvidia-open amdgpu\n"
                "a failed unit: foo.service loaded failed failed Foo")
        self.assertEqual(s(text), text)

    def test_root_and_short_names_not_replaced(self):
        out = report.scrub("root=/dev/sda1 and rt", {"users": ["root", "rt"], "hosts": ["localhost"]})
        self.assertEqual(out, "root=/dev/sda1 and rt")

    def test_make_context_tolerates_failure(self):
        ctx = report.make_context()
        self.assertIn("users", ctx)
        self.assertIn("hosts", ctx)


class ReportTests(unittest.TestCase):
    def test_url_cap(self):
        url = report.build_issue_url("t", "x y\n" * 20000)
        self.assertLessEqual(len(url), report.URL_CAP)
        self.assertTrue(url.startswith("https://github.com/mrtrickdotorg/Rowyn/issues/new?title="))

    def test_url_encoded_summary(self):
        url = report.build_issue_url("Rowyn: a b", report.summary_body({"os": "Rowyn 26.10"}))
        self.assertNotIn(" ", url)
        self.assertNotIn("\n", url)

    def test_image_from_bootc(self):
        js = json.dumps({"status": {"booted": {"image": {
            "image": {"image": "ghcr.io/mrtrickdotorg/rowyn:stable"}, "imageDigest": "sha256:abc"}}}})
        self.assertEqual(report.image_from_bootc(js), ("ghcr.io/mrtrickdotorg/rowyn:stable", "sha256:abc"))
        self.assertEqual(report.image_from_bootc("nope"), (None, None))

    def test_stdout_runs(self):
        p = subprocess.run([sys.executable, str(REPORT), "--stdout", "--no-sudo"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Rowyn", p.stdout)

    def test_writes_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": "/usr/bin:/bin", "XDG_STATE_HOME": d, "HOME": d}
            p = subprocess.run([sys.executable, str(REPORT), "--no-sudo"], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            files = list(Path(d, "rowyn").glob("rowyn-report-*.md"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertIn("issues/new?title=", p.stdout)

    def test_no_network_modules(self):
        src = REPORT.read_text()
        imports = "\n".join(l for l in src.splitlines() if re.match(r"\s*(import|from)\s", l))
        self.assertIsNone(re.search(
            r"urllib\.request|http\.client|requests|socket\.create_connection|ftplib|smtplib",
            src), "network use found")
        self.assertNotIn("http.client", imports)
        self.assertNotRegex(src, r"socket\.(socket|connect)\(")

    def test_py_compile(self):
        with tempfile.TemporaryDirectory() as d:
            for f in (REPORT, CHECK):
                py_compile.compile(str(f), cfile=str(Path(d) / (f.name + ".pyc")), doraise=True)


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def usb(sysfs: Path, n: str, vid: str, pid: str) -> None:
    put(sysfs / "bus/usb/devices" / n / "idVendor", vid + "\n")
    put(sysfs / "bus/usb/devices" / n / "idProduct", pid + "\n")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = Path(self.tmp.name) / "sys"
        self.dev = Path(self.tmp.name) / "dev"
        self.sys.mkdir()
        self.dev.mkdir()
        self.proc = Path(self.tmp.name) / "modules"
        self.proc.write_text("")
        self.rules = Path(self.tmp.name) / "rules"
        self.rules.mkdir()

    def run_check(self):
        return check.check(str(self.sys), str(self.dev), str(self.proc), (str(self.rules),))

    def find(self, result, name):
        return [f for f in result["findings"] if f["name"] == name]

    def test_no_devices(self):
        r = self.run_check()
        self.assertEqual(self.find(r, "Input devices")[0]["status"], "warn")
        self.assertEqual(r["usb"], [])
        self.assertEqual(self.find(r, "udev rules")[0]["status"], "warn")

    def test_dualsense_module_missing(self):
        usb(self.sys, "1-1", "054c", "0ce6")
        r = self.run_check()
        f = self.find(r, "Driver for DualSense")[0]
        self.assertEqual(f["status"], "fail")
        self.assertIn("hid_playstation", f["detail"])
        self.assertTrue(f["hint"])

    def test_dualsense_module_loaded(self):
        usb(self.sys, "1-1", "054c", "0ce6")
        (self.sys / "module/hid_playstation").mkdir(parents=True)
        put(self.sys / "class/input/input5/name", "Wireless Controller\n")
        (self.sys / "class/input/input5/js0").mkdir()
        r = self.run_check()
        self.assertEqual(self.find(r, "Driver for DualSense")[0]["status"], "ok")
        self.assertEqual(self.find(r, "Input devices")[0]["status"], "ok")

    def test_proc_modules_used(self):
        usb(self.sys, "1-1", "057e", "2009")
        self.proc.write_text("hid_nintendo 24576 0 - Live 0x0\n")
        r = self.run_check()
        self.assertEqual(self.find(r, "Driver for Switch Pro Controller")[0]["status"], "ok")

    def test_xbox_adapter(self):
        usb(self.sys, "1-2", "045e", "02fe")
        r = self.run_check()
        self.assertEqual(self.find(r, "USB controllers")[0]["status"], "ok")
        self.assertEqual(self.find(r, "Xbox wireless adapter")[0]["status"], "warn")

    def test_uinput_not_writable(self):
        put(self.dev / "uinput")
        orig = check.can_rw
        check.can_rw = lambda p: False
        self.addCleanup(setattr, check, "can_rw", orig)
        r = self.run_check()
        self.assertEqual(self.find(r, "uinput")[0]["status"], "warn")
        self.assertIn("cannot read and write", self.find(r, "uinput")[0]["detail"])
        check.can_rw = lambda p: True
        self.assertEqual(self.find(self.run_check(), "uinput")[0]["status"], "ok")

    def test_udev_rule_found(self):
        put(self.rules / "60-steam-input.rules")
        self.assertEqual(self.find(self.run_check(), "udev rules")[0]["status"], "ok")

    def test_json_cli(self):
        p = subprocess.run([sys.executable, str(CHECK), "--json", "--sysfs-root", str(self.sys),
                            "--dev-root", str(self.dev), "--proc-modules", str(self.proc)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("findings", json.loads(p.stdout))

    def test_text_cli_exit_zero(self):
        p = subprocess.run([sys.executable, str(CHECK), "--sysfs-root", str(self.sys),
                            "--dev-root", str(self.dev), "--proc-modules", str(self.proc)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("Controller Check", p.stdout)


class WiringTests(unittest.TestCase):
    def test_containerfile_installs_both(self):
        text = CONTAINERFILE.read_text()
        for name in ("rowyn-report", "rowyn-controller-check"):
            self.assertRegex(text, r"install -Dm755 /ctx/shared/%s /usr/bin/%s" % (name, name))
            self.assertIn("py_compile", text)

    def test_workflow_smoke(self):
        text = WORKFLOW.read_text()
        for needle in ("test -x /usr/bin/rowyn-report", "test -x /usr/bin/rowyn-controller-check",
                       "rowyn-report --stdout --no-sudo", "rowyn-controller-check --json"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
