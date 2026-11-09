#!/usr/bin/env python3
"""Coverage for the rowyn-probe producer: the Hub's probe reader expects
cache files written through the shared crate's probe library; the shell
crate's second binary drives it, and the payload ships the binary plus
the system and per-user timers that run it."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "build/hub/src-tauri/src/bin/rowyn-probe.rs"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"


class ProbeProducer(unittest.TestCase):
    def test_driver_follows_the_library_contract(self):
        source = BIN.read_text(encoding="utf-8")
        for token in (
            "rowyn_shared::system::probe",
            "deferred_system_sections()",
            "collect_snapshot_streaming",
            "update_sections(batch, None, true)",
            "update_sections(batch, None, false)",
            "fresh_system_sections()",
            "cacheable_sections_to_skip",
            "merge_fresh_sections",
            '"--system"',
        ):
            self.assertIn(token, source)

    def test_units_match_the_library_timing(self):
        system_service = (ROOT / "shared/rowyn-probe.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/rowyn-probe --system", system_service)
        # The library's streaming design assumes the 120 s service timeout.
        self.assertIn("TimeoutStartSec=120", system_service)
        self.assertIn("UMask=0077", system_service)
        system_timer = (ROOT / "shared/rowyn-probe.timer").read_text(encoding="utf-8")
        self.assertIn("OnBootSec=1min", system_timer)
        user_timer = (ROOT / "shared/rowyn-probe-user.timer").read_text(encoding="utf-8")
        # USER_REFRESH_INTERVAL_SECS in the library is 600.
        self.assertIn("OnUnitActiveSec=10min", user_timer)
        user_service = (ROOT / "shared/rowyn-probe-user.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/rowyn-probe", user_service)
        self.assertNotIn("--system", user_service)

    def test_payload_ships_binary_and_units(self):
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("cp target/release/rowyn-probe /rowyn-probe", containerfile)
        self.assertIn("COPY --from=hub-builder /rowyn-probe /usr/bin/rowyn-probe",
                      containerfile)
        for unit in ("rowyn-probe.service", "rowyn-probe.timer",
                     "rowyn-probe-user.service", "rowyn-probe-user.timer"):
            self.assertIn(unit, containerfile)
        self.assertIn("systemctl enable rowyn-probe.timer", containerfile)
        self.assertIn("systemctl --global enable rowyn-probe-user.timer", containerfile)

    def test_workflow_smoke_covers_the_producer(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/bin/rowyn-probe", workflow)
        self.assertIn("timers.target.wants/rowyn-probe.timer", workflow)
        self.assertIn("timers.target.wants/rowyn-probe-user.timer", workflow)


if __name__ == "__main__":
    unittest.main()
