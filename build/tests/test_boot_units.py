#!/usr/bin/env python3
"""The recorded boot-unit audit: this file IS the decision record.

Every payload-controlled unit decision for the boot path lives here as
an expected set; changing the Containerfile's enable/mask/disable lines
without updating this audit (and docs/performance.md) fails the suite,
so boot creep — a new always-on unit, a quietly unmasked timer — can
never land silently.

Audited 2026-11-14. Decisions:

MASKED (must never run on an image-based host):
- systemd-networkd{,-wait-online}, ufw: NM and firewalld own their
  roles; a second manager racing them is a defect, not redundancy.
- apt-daily{,-upgrade}.{service,timer}: periodic `apt update`/upgrade
  churns /var and the network for a package state the image model
  makes meaningless; host apt installs are guidance-blocked.
- unattended-upgrades.service: same reasoning — it would download and
  attempt deb upgrades against an immutable /usr. (Inert if the
  package is absent from the closure; masked regardless.)
- motd-news.{service,timer}: fetches Canonical marketing/news content
  for the login banner; pure noise on a derived image.

DISABLED (not masked):
- NetworkManager-wait-online: the boot path must not block on network.
  It is disabled, NOT masked, because rowyn-update-watcher and
  rowyn-flatpak-preinstall deliberately order After/Wants
  network-online.target; masking the wait-online service is one step
  further than the evidence justifies.

ENABLED AND UNTOUCHABLE (boot/update/VPN/boot-health paths):
- rowyn-update-watcher.timer, rowyn-boot-health.timer,
  rowyn-probe.timer, rowyn-performance.timer, systemd-oomd,
  sddm, NetworkManager, firewalld.

KEPT AFTER AUDIT (no change, with reasons in docs/performance.md):
fwupd-refresh (firmware metadata is a stability input), fstrim
(SSD health), man-db + plocate-updatedb (deprioritized instead — see
desktop-tuning.sh), package autostarts such as Akonadi (payload does
not own them; PIM is native by design and its agents are D-Bus
activated on demand).
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = (ROOT / "Containerfile").read_text(encoding="utf-8")

EXPECTED_MASKS = {
    "systemd-networkd.service",
    "systemd-networkd-wait-online.service",
    "ufw.service",
    "apt-daily.service",
    "apt-daily.timer",
    "apt-daily-upgrade.service",
    "apt-daily-upgrade.timer",
    "unattended-upgrades.service",
    "motd-news.service",
    "motd-news.timer",
}

PROTECTED_ENABLES = (
    "rowyn-update-watcher.timer",
    "rowyn-boot-health.timer",
    "rowyn-probe.timer",
    "rowyn-performance.timer",
    "systemd-oomd.service",
    "sddm.service",
    "NetworkManager.service",
    "firewalld.service",
)


def systemctl_args(verb: str) -> set[str]:
    """All unit names passed to `systemctl <verb>` in the Containerfile."""
    units: set[str] = set()
    text = CONTAINERFILE.replace("\\\n", " ")
    for match in re.finditer(rf"systemctl\s+{verb}\s+([^&|;#]+)", text):
        units.update(
            token for token in match.group(1).split()
            if token.endswith((".service", ".timer", ".socket", ".target")))
    return units


class BootUnitAudit(unittest.TestCase):
    def test_mask_set_is_exactly_the_audited_set(self):
        self.assertEqual(systemctl_args("mask"), EXPECTED_MASKS)

    def test_wait_online_is_disabled_never_masked(self):
        self.assertIn("NetworkManager-wait-online.service", systemctl_args("disable"))
        self.assertNotIn("NetworkManager-wait-online.service", systemctl_args("mask"))

    def test_protected_units_stay_enabled(self):
        enabled = systemctl_args("enable")
        for unit in PROTECTED_ENABLES:
            self.assertIn(unit, enabled)
            self.assertNotIn(unit, systemctl_args("mask"))
            self.assertNotIn(unit, systemctl_args("disable"))

    def test_global_user_enables_survive(self):
        text = CONTAINERFILE.replace("\\\n", " ")
        for unit in ("rowyn-probe-user.timer", "rowyn-update-notify.timer"):
            self.assertRegex(text, rf"systemctl --global enable [^&]*{re.escape(unit)}")

    def test_no_unit_is_both_masked_and_enabled(self):
        self.assertFalse(systemctl_args("mask") & systemctl_args("enable"))


if __name__ == "__main__":
    unittest.main()
