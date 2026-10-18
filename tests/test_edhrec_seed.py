#!/usr/bin/env python3
"""EDHREC seed client + pool injection — offline tests."""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import os
import stat
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import edhrec_seed as es

REPO = ROOT

COMMANDER_FIXTURE = {
    "container": {
        "json_dict": {
            "card": {"num_decks": 43664, "name": "Krenko, Mob Boss"},
            "cardlists": [
                {
                    "header": "New Cards",
                    "cardviews": [
                        {"name": "Fresh Goblin", "num_decks": 200, "trend_zscore": 5.0},
                    ],
                },
                {
                    "header": "Top Cards",
                    "cardviews": [
                        {
                            "name": "Skirk Prospector",
                            "num_decks": 30000,
                            "synergy": 0.72,
                            "trend_zscore": 0.4,
                        },
                    ],
                },
            ],
        }
    }
}

AVERAGE_FIXTURE = {
    "deck": {
        "cards": {
            "Creature": [["Skirk Prospector", 1], ["Goblin Warchief", 1]],
            "Land": [["Mountain", 12], ["Cavern of Souls", 1]],
            "Basic": [["Mountain", 10]],
            "Instant": [["Lightning Bolt", 1]],
        }
    }
}


class EdhrecSeedTests(unittest.TestCase):
    def test_parse_num_decks(self):
        self.assertEqual(es.parse_num_decks(COMMANDER_FIXTURE), 43664)
        self.assertIsNone(es.parse_num_decks({}))

    def test_parse_average_skips_lands(self):
        names = es.parse_average_names(AVERAGE_FIXTURE)
        self.assertEqual(names, ["Skirk Prospector", "Goblin Warchief", "Lightning Bolt"])

    def test_parse_card_meta_inclusion_and_rising(self):
        meta = es.parse_card_meta(COMMANDER_FIXTURE, num_decks=43664)
        self.assertAlmostEqual(meta["Skirk Prospector"]["inclusion_pct"], 68.7, places=0)
        self.assertTrue(meta["Fresh Goblin"]["rising"])
        self.assertFalse(meta["Skirk Prospector"]["rising"])

    def test_classify_seed_threshold(self):
        self.assertEqual(
            es.classify_seed("ok", "ok", 1999, ["A"]),
            "thin_data",
        )
        self.assertEqual(
            es.classify_seed("ok", "ok", 2500, ["A"]),
            "seeded",
        )
        self.assertEqual(es.classify_seed("unknown", "skipped", None, []), "unreachable")
        self.assertEqual(es.classify_seed("missing", "skipped", None, []), "thin_data")

    def test_enabled_green_only(self):
        with patch.dict(os.environ, {"DECKWRIGHT_EDHREC_SEED": "1", "DECKWRIGHT_ENV": "green"}):
            self.assertTrue(es.enabled())
        with patch.dict(os.environ, {"DECKWRIGHT_EDHREC_SEED": "1", "DECKWRIGHT_ENV": "blue"}):
            self.assertFalse(es.enabled())

    def test_accepts_seed_card_filters(self):
        land = {"name": "Mountain", "type": "Basic Land — Mountain", "legal": "legal",
                "color_identity": ["R"], "usd": "0.25", "gc": False}
        self.assertFalse(es.accepts_seed_card(land, ci="R", card_pool="tournament", max_usd=5.0))
        spell = {"name": "Bolt", "type": "Instant", "legal": "legal",
                 "color_identity": ["R"], "usd": "1.00", "gc": False}
        self.assertTrue(es.accepts_seed_card(spell, ci="R", card_pool="tournament", max_usd=5.0))
        gc = {**spell, "gc": True}
        self.assertFalse(es.accepts_seed_card(gc, ci="R", card_pool="tournament", max_usd=5.0, bracket=2))

    def test_merge_and_prefer_order(self):
        pool = [{"name": "Zzz", "usd": "1"}, {"name": "Skirk Prospector", "usd": "1"}]
        seed = [{"name": "Skirk Prospector", "usd": "1"}, {"name": "New One", "usd": "1"}]
        merged = es.merge_pool_front(pool, seed)
        self.assertEqual([c["name"] for c in merged[:2]], ["New One", "Zzz"])
        meta = {"New One": {"inclusion_pct": 90.0}, "Zzz": {"inclusion_pct": 10.0}}
        ordered = es.prefer_seed_order(merged, meta)
        self.assertEqual(ordered[0]["name"], "New One")

    def test_provenance_copy(self):
        bundle = es.SeedBundle(status="seeded", provenance=es.PROVENANCE["seeded"])
        resp = bundle.as_response()
        self.assertIn("people play", resp["provenance"])

    def test_annotate_only_when_seeded(self):
        card = {"name": "Sol Ring", "role": "ramp"}
        meta = {"Sol Ring": {"inclusion_pct": 77.0, "rising": False, "synergy": 0.1}}
        thin = es.annotate_card(card, meta, status="thin_data")
        self.assertNotIn("edhrec_inclusion_pct", thin)
        seeded = es.annotate_card(card, meta, status="seeded")
        self.assertEqual(seeded["edhrec_inclusion_pct"], 77.0)
        unreachable = es.annotate_card(card, meta, status="unreachable")
        self.assertNotIn("edhrec_inclusion_pct", unreachable)

    def test_edhrec_base_override(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DECKWRIGHT_EDHREC_BASE", None)
            self.assertEqual(es.edhrec_base(), "https://json.edhrec.com/pages")
        with patch.dict(os.environ, {"DECKWRIGHT_EDHREC_BASE": "http://127.0.0.1:9/pages/"}):
            self.assertEqual(es.edhrec_base(), "http://127.0.0.1:9/pages")

    def test_thin_bundle_has_empty_meta(self):
        """Thin-data must not carry cardlists for paint."""
        cmd = {
            "container": {
                "json_dict": {
                    "card": {"num_decks": 139, "name": "Wilson"},
                    "cardlists": [
                        {
                            "header": "Top Cards",
                            "cardviews": [
                                {"name": "Sol Ring", "num_decks": 107, "synergy": 0.5},
                            ],
                        }
                    ],
                }
            }
        }
        status = es.classify_seed("ok", "skipped", 139, [])
        self.assertEqual(status, "thin_data")
        # Same rule as load_for_commander: meta only when seeded.
        meta = (
            es.parse_card_meta(cmd, num_decks=139)
            if status == "seeded"
            else {}
        )
        self.assertEqual(meta, {})
        annotated = es.annotate_card(
            {"name": "Sol Ring"},
            es.parse_card_meta(cmd, num_decks=139),
            status=status,
        )
        self.assertNotIn("edhrec_inclusion_pct", annotated)

    def test_green_baseline_has_no_seed_flag(self):
        plist = REPO / "config" / "com.deckwright.green.plist"
        if not plist.is_file():
            self.skipTest("personal launchd plist not in public tree")
        text = plist.read_text()
        self.assertNotIn("DECKWRIGHT_EDHREC_SEED", text)
        self.assertIn("8001", text)

    def test_green_d_plist_and_install_script(self):
        plist = REPO / "config" / "com.deckwright.green-d.plist"
        install = REPO / "config" / "install-green-d-launchd.sh"
        if not plist.is_file() or not install.is_file():
            self.skipTest("personal launchd templates not in public tree")
        text = plist.read_text()
        self.assertIn("com.deckwright.green-d", text)
        self.assertIn("8004", text)
        self.assertIn("DECKWRIGHT_EDHREC_SEED", text)
        self.assertIn(".deckwright-green-d", text)
        self.assertNotIn("<string>C</string>", text)
        root = ET.parse(plist).getroot()
        d = root.find("dict")
        children = list(d)
        env = None
        for i, child in enumerate(children):
            if child.tag == "key" and child.text == "EnvironmentVariables":
                env = children[i + 1]
        got = {}
        if env is not None:
            pairs = list(env)
            for i in range(0, len(pairs), 2):
                if pairs[i].tag == "key" and i + 1 < len(pairs):
                    got[pairs[i].text] = pairs[i + 1].text
        self.assertEqual(got.get("PORT"), "8004")
        self.assertEqual(got.get("DECKWRIGHT_EDHREC_SEED"), "1")
        self.assertEqual(got.get("DECKWRIGHT_CANDIDATE"), "off")
        self.assertEqual(got.get("DECKWRIGHT_LLM_TIMEOUT"), "600")
        sh = install.read_text()
        self.assertIn("8001", sh)
        self.assertIn("BLUE", sh)
        self.assertTrue(install.stat().st_mode & stat.S_IXUSR)

    def test_harness_contestant_d_registered(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import v2_benchmark as vb  # noqa: E402

        path = REPO / "data" / "benchmarks" / "contestants.json"
        reg = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(vb.validate_contestant_registry(reg), [])
        d_row = next(c for c in reg["contestants"] if c["id"] == "D")
        self.assertEqual(d_row["url_default"], "http://127.0.0.1:8004")
        self.assertEqual(d_row["url_env"], "DECKWRIGHT_GREEN_D_URL")
        self.assertEqual(d_row["kind"], "http_build")
        blue = next(c for c in reg["contestants"] if c["id"] == "BLUE")
        self.assertEqual(blue["url_default"], "http://127.0.0.1:8001")


if __name__ == "__main__":
    unittest.main()
