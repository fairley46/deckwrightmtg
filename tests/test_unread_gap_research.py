"""CON-247 — Phase 2 research artifact shape.

No detectors. Assert every researched unread pattern carries method,
exemplar, near-miss, grammar candidate, and strategy candidate — and that
card names were not left blank (unknown is not inventing a name).
"""

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
DOC = json.loads((ROOT / "data" / "unread_gap_research.json").read_text())
METHODS = {"both", "community", "derived"}


class UnreadGapResearch(unittest.TestCase):
    def test_meta(self):
        self.assertNotIn("issue", DOC)
        self.assertNotIn("parent", DOC)
        self.assertTrue(DOC["patterns"])

    def test_every_pattern_complete(self):
        for p in DOC["patterns"]:
            with self.subTest(p["id"]):
                self.assertIn(p["method"], METHODS)
                self.assertTrue(p["exemplar"], "exemplar required")
                self.assertTrue(p["near_miss"], "near-miss required")
                self.assertNotEqual(p["exemplar"], p["near_miss"])
                g = p["grammar_candidate"]
                self.assertTrue(g.get("query"), "grammar candidate query required")
                self.assertIn("verified_count", g)
                # Phase 3 fills counts. Unknown failures are searchable=false,
                # never a silent fake zero.
                if g.get("searchable") is False:
                    self.assertIsNone(g["verified_count"])
                    self.assertTrue(g.get("unknown_reason") or g.get("note"))
                else:
                    self.assertIsInstance(g["verified_count"], int)
                    self.assertGreaterEqual(g["verified_count"], 0)
                s = p["strategy_candidate"]
                self.assertIn("kind", s)
                self.assertIn("note", s)
                self.assertIn(p["admit_via"], {
                    "scryfall_keyword_catalog",
                    "oracle_signal",
                })

    def test_keywords_do_not_recommend_invented_signals(self):
        for p in DOC["patterns"]:
            if p["admit_via"] == "scryfall_keyword_catalog":
                with self.subTest(p["id"]):
                    self.assertFalse(
                        p["oracle_signal_recommended"],
                        "catalog keywords must not invent oracle_signals",
                    )

    def test_draw_is_recommended_signal(self):
        draw = next(p for p in DOC["patterns"] if p["id"] == "draw_a_card")
        self.assertTrue(draw["oracle_signal_recommended"])
        self.assertEqual(draw["exemplar"], "Mind Stone")
        self.assertEqual(draw["near_miss"], "Path of Ancestry")

    def test_con245_closures_listed(self):
        closed = set(DOC["already_closed_in_con245"])
        for name in ("enters_tapped", "commander_matters", "enchant_creature"):
            self.assertIn(name, closed)


if __name__ == "__main__":
    unittest.main()
