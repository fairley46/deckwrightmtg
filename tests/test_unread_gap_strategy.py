"""CON-249 — Phase 4 strategy hooks shape."""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = json.loads((ROOT / "data" / "unread_gap_strategy.json").read_text())
PLAYSTYLES = json.loads((ROOT / "data" / "playstyles.json").read_text())["playstyles"]


class UnreadGapStrategy(unittest.TestCase):
    def test_meta(self):
        self.assertNotIn("issue", DOC)
        self.assertTrue(DOC["hooks"])
        self.assertIsNotNone(DOC["before"])

    def test_every_hook_has_action(self):
        for h in DOC["hooks"]:
            with self.subTest(h["id"]):
                a = h.get("playstyles_json_action") or {}
                self.assertIn(a.get("action"), {
                    "boost", "none", "none_yet", "catalog_only",
                    "synergy_object", "mapped",
                })
                self.assertTrue(a.get("reason"))

    def test_no_invented_playstyles(self):
        known = set(PLAYSTYLES)
        for h in DOC["hooks"]:
            ps = (h.get("playstyles_json_action") or {}).get("playstyle")
            if ps:
                self.assertIn(ps, known, f"{h['id']} invented playstyle {ps}")

    def test_voltron_landfall_boosts_landed(self):
        self.assertIn("cant_be_blocked", PLAYSTYLES["voltron"]["detect"]["boosts"])
        self.assertIn("firebreathing", PLAYSTYLES["voltron"]["detect"]["boosts"])
        self.assertIn("enchant_land", PLAYSTYLES["landfall"]["detect"]["boosts"])

    def test_after_reported(self):
        self.assertIsNotNone(DOC.get("after"))
        self.assertIn("empty_ways", DOC["after"])
        self.assertEqual(DOC["delta"]["empty_ways"], 0)
        self.assertTrue(DOC.get("empty_ways_finding", {}).get("reason"))

    def test_boosts_are_not_requires_any(self):
        """Generic evasion/pump must not become match gates."""
        self.assertNotIn(
            "cant_be_blocked",
            PLAYSTYLES["voltron"]["detect"]["requires_any"],
        )
        self.assertNotIn(
            "firebreathing",
            PLAYSTYLES["voltron"]["detect"]["requires_any"],
        )


if __name__ == "__main__":
    unittest.main()
