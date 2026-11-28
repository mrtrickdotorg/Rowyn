#!/usr/bin/env python3
"""Contracts for the payload's screenshot tool.

KDE's PrintScreen handling belongs to Spectacle: the key is dead on a
system without it. Spectacle is in the Kubuntu live image but has to be
named explicitly in the payload's KDE package layer, or the installed
system ships with no screenshot tool at all.
"""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"


class PayloadSpectacleTests(unittest.TestCase):
    def test_kde_layer_installs_spectacle(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        # The desktop set installs as several frequency groups; the
        # span is all of them (up to the PIM group), not one layer.
        kde_layer = text.split("# --- KDE Plasma package layer", 1)[1]
        kde_layer = kde_layer.split("# Group: PIM", 1)[0]
        self.assertIn("plasma-desktop", kde_layer)
        self.assertIn("kde-spectacle", kde_layer)

    def test_image_smoke_checks_assert_spectacle_binary(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test -x /usr/bin/spectacle", workflow)


if __name__ == "__main__":
    unittest.main()
