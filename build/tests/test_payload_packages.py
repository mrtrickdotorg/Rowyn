#!/usr/bin/env python3
"""Contracts for the payload's desktop package set.

The installed system carries the Kubuntu default applications and
integration — the set the live image gets from the Kubuntu manifest —
named explicitly in the Containerfile's KDE package layer rather than
pulled via the kubuntu-desktop metapackage, so the deliberate
exclusions stay excluded. Patrick's call (Oct 29, 2026): install the
essentials, skip LibreOffice. Firefox/Thunderbird stay out too (they
are snaps on Ubuntu and Rowyn is snap-free), as does plasma-welcome
(purged on purpose) and the legacy Oxygen/Air themes.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"

# The essentials set, grouped as in the Containerfile layer.
ESSENTIALS = [
    # Default KDE applications (kde-spectacle landed with the
    # Print Screen fix; it is part of the same set).
    "kde-spectacle",
    "gwenview", "okular", "okular-extra-backends", "ark", "kate",
    "kcalc", "kcharselect", "kdeconnect", "elisa", "haruna", "skanpage",
    # Archive backends for Ark.
    "unzip", "zip",
    # Printing, scanning, color, tablets.
    "cups", "cups-browsed", "cups-client", "cups-daemon", "cups-filters",
    "cups-ipp-utils", "cups-pk-helper", "print-manager",
    "system-config-printer-common", "system-config-printer-udev",
    "bluez-cups", "colord", "kde-config-tablet",
    # Driverless USB printing (ipp-usb) and the mDNS service cups-browsed
    # and network printer discovery rely on.
    "ipp-usb", "avahi-daemon",
    "sane-airscan", "sane-utils",
    # Desktop integration: KCMs, KIO workers, Plasma/KWin add-ons.
    "kio-admin", "kio-audiocd", "kde-config-updates", "kde-config-plymouth",
    "plasma-runners-addons", "plasma-widgets-addons",
    "plasma-wallpapers-addons", "plasma-calendar-addons",
    "plasma-dataengines-addons", "kwin-addons",
    # Media plumbing.
    "ffmpeg", "gstreamer1.0-pipewire", "phonon4qt6-backend-vlc",
    # Document fonts and accessibility.
    "fonts-liberation", "fonts-dejavu-core", "fonts-dejavu-mono",
    "orca",
]

# The command-line backbone: the stock Ubuntu metapackages plus the
# basics ubuntu-standard does not pull (curl is the famous one — it is
# in neither metapackage's Depends/Recommends). Patrick's report
# (Oct 29, 2026): curl and friends missing from the installed system.
TOOLS = [
    "ubuntu-minimal", "ubuntu-standard",
    "curl", "wget", "nano", "vim-tiny", "htop", "file", "lshw",
    "bind9-dnsutils", "iputils-ping", "iputils-tracepath", "mtr-tiny",
    "netcat-openbsd", "lsof", "strace", "tcpdump", "hdparm", "rfkill",
    "plocate", "manpages", "bash-completion", "command-not-found",
    "pastebinit",
]

# Packages that must never be installed in the payload. Matched against
# install tokens, not raw text: the Containerfile comments name some of
# these while explaining why they are excluded.
FORBIDDEN_PREFIXES = ("libreoffice",)
FORBIDDEN = {"firefox", "thunderbird"}


def installed_tokens(text: str) -> set[str]:
    """All package tokens named by any apt-get install in the file."""
    tokens: set[str] = set()
    for match in re.finditer(r"apt-get install -y[^\n]*\\\n(.*?)\s&& \\", text, re.S):
        body = match.group(1).replace("\\", " ")
        tokens.update(body.split())
    return tokens


class PayloadPackageTests(unittest.TestCase):
    def test_essentials_are_installed(self):
        tokens = installed_tokens(CONTAINERFILE.read_text(encoding="utf-8"))
        for package in ESSENTIALS:
            self.assertIn(package, tokens)

    def test_essentials_are_in_the_kde_layer_not_the_recommends_free_one(self):
        # The essentials ride the recommends-enabled desktop groups —
        # the same install mode as plasma-desktop — not the later
        # --no-install-recommends layer.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        kde_layer = text.split("apt-get install -y --no-install-recommends", 1)[0]
        for package in ESSENTIALS:
            self.assertIn(package, installed_tokens(kde_layer))

    def test_libreoffice_and_snap_browsers_are_not_installed(self):
        tokens = installed_tokens(CONTAINERFILE.read_text(encoding="utf-8"))
        for token in tokens:
            for prefix in FORBIDDEN_PREFIXES:
                self.assertFalse(token.startswith(prefix),
                                 f"forbidden package installed: {token}")
            self.assertNotIn(token, FORBIDDEN)

    def test_cups_units_are_enabled(self):
        # Installing CUPS is not enough: the daemon must actually be
        # enabled the way Ubuntu does it, or printing stays dead.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        enables = re.findall(r"systemctl enable ([^&]+)&&", text)
        desktop = [e for e in enables if "sddm.service" in e]
        self.assertTrue(desktop, "no systemctl enable line names sddm.service")
        units = desktop[0].replace("\\", " ").split()
        for unit in ("cups.service", "cups.socket", "cups-browsed.service",
                     "avahi-daemon.service", "avahi-daemon.socket"):
            self.assertIn(unit, units)

    def test_cli_tools_are_installed(self):
        tokens = installed_tokens(CONTAINERFILE.read_text(encoding="utf-8"))
        for package in TOOLS:
            self.assertIn(package, tokens)

    def test_ufw_is_masked_so_firewalld_owns_the_firewall(self):
        # ubuntu-standard recommends ufw; an enabled ufw would race
        # firewalld (the Hub's VPN lockdown drives firewall-cmd), so
        # the payload masks ufw.service like it masks systemd-networkd.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        mask = re.search(r"systemctl mask ([^&]+)&&", text)
        self.assertIsNotNone(mask)
        self.assertIn("ufw.service", mask.group(1).split())
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'test "$(readlink /etc/systemd/system/ufw.service)" = /dev/null',
            workflow)

    def test_image_smoke_checks_cover_the_essentials(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        for assertion in (
            "test -x /usr/bin/gwenview",
            "test -x /usr/bin/okular",
            "test -x /usr/bin/ark",
            "test -x /usr/bin/kate",
            "test -x /usr/bin/kcalc",
            "test -x /usr/bin/kdeconnect-app",
            "test -x /usr/sbin/cupsd",
            "test -e /etc/systemd/system/multi-user.target.wants/cups.service",
            "test -e /etc/systemd/system/sockets.target.wants/cups.socket",
            "test -e /etc/systemd/system/multi-user.target.wants/cups-browsed.service",
            "test -x /usr/sbin/avahi-daemon",
            "test -x /usr/sbin/ipp-usb",
            "test -s /usr/share/rowyn/print-test-page.txt",
        ):
            self.assertIn(assertion, workflow)


# The desktop set installs as five frequency groups (2026-11-27 split
# of the old single ~1.05 GB desktop layer): each group's anchor
# packages must be named in its own install block and in no other
# desktop group's, so a security update to one cluster re-downloads
# only that cluster's layer. The groups install in this order.
DESKTOP_GROUPS = [
    ("desktop-backbone", {"ubuntu-minimal", "ubuntu-standard",
                          "network-manager", "tzdata", "plocate"}),
    ("desktop-session", {"plasma-desktop", "kwin-wayland", "sddm",
                         "kde-spectacle"}),
    ("desktop-integration", {"kaccounts-integration", "kio-gdrive",
                             "kwin-addons", "fprintd"}),
    ("desktop-apps", {"gwenview", "okular", "elisa",
                      "plasma-systemmonitor"}),
    ("desktop-hub-vpn", {"libwebkit2gtk-4.1-0", "openconnect",
                         "firewalld"}),
]


def desktop_group_blocks(text: str) -> dict[str, str]:
    span = text.split("# --- KDE Plasma package layer", 1)[1]
    span = span.split("# Group: PIM", 1)[0]
    parts = re.split(r"# Group: (desktop-[a-z-]+)", span)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


class DesktopGroupSplitTests(unittest.TestCase):
    def test_desktop_installs_as_five_frequency_groups_in_order(self):
        blocks = desktop_group_blocks(CONTAINERFILE.read_text(encoding="utf-8"))
        self.assertEqual(list(blocks), [name for name, _ in DESKTOP_GROUPS])

    def test_each_groups_anchors_are_named_only_in_that_group(self):
        blocks = desktop_group_blocks(CONTAINERFILE.read_text(encoding="utf-8"))
        tokens = {name: installed_tokens(body) for name, body in blocks.items()}
        for name, anchors in DESKTOP_GROUPS:
            for anchor in anchors:
                self.assertIn(anchor, tokens[name],
                              f"{anchor} must be named in {name}")
                for other, other_tokens in tokens.items():
                    if other != name:
                        self.assertNotIn(
                            anchor, other_tokens,
                            f"{anchor} is named in both {name} and {other}; "
                            "the split only bounds downloads if each "
                            "cluster installs in exactly one group")


if __name__ == "__main__":
    unittest.main()
