"""drop unlabelled pool size; narrowing turn toward commanders.

    python test_narrowing.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import unittest
from unittest import mock

from deckwright import app


def _run(coro):
    return asyncio.run(coro)


def _card(name: str, *, text: str = "", typeline: str = "Legendary Creature — Angel") -> dict:
    return {
        "name": name,
        "text": text or f"{name} makes Angel tokens.",
        "type": typeline,
        "color_identity": ["W"],
        "edhrec_rank": 100,
    }


class ClosingPoolMagnitude(unittest.TestCase):
    """Healthy pool sizes are builder diagnostics — do not force them on her."""

    def test_ok_pool_count_not_appended_at_handoff(self):
        inst = {
            "spans": [{"said": "angels", "count": 278, "status": "ok",
                       "operator": "t:angel"}],
            "composed": None,
            "unknowns": [],
            "empty": [],
        }
        text = "Angels that swing as a choir — got it."
        out = app.ensure_closing_instrument_readback(
            text, inst, still_asking=False)
        self.assertEqual(out, text)
        self.assertNotIn("278", out)
        self.assertNotIn("Together that is about", out)

    def test_empty_and_unknown_honesty_still_appended(self):
        empty_inst = {
            "spans": [{"said": "heists", "count": 0, "status": "empty",
                       "operator": "otag:heists"}],
            "composed": None,
            "unknowns": [],
            "empty": ["heists"],
        }
        out = app.ensure_closing_instrument_readback(
            "Ready.", empty_inst, still_asking=False)
        self.assertIn("could not find", out.lower())
        self.assertNotIn("0 cards", out.lower())

        unk_inst = {
            "spans": [{"said": "angels", "count": None, "status": "unknown",
                       "operator": "t:angel"}],
            "composed": None,
            "unknowns": ["angels"],
            "empty": [],
        }
        out2 = app.ensure_closing_instrument_readback(
            "Ready.", unk_inst, still_asking=False)
        self.assertIn("could not look", out2.lower())
        self.assertNotRegex(out2.lower(), r"\b0\b")

    def test_strip_ok_magnitudes_for_handoff(self):
        inst = {
            "spans": [
                {"said": "angels", "count": 278, "status": "ok",
                 "operator": "t:angel"},
                {"said": "heists", "count": 0, "status": "empty",
                 "operator": "x"},
                {"said": "wobbly", "count": None, "status": "unknown",
                 "operator": "y"},
            ],
            "composed": {"said": "angels", "count": 278, "status": "ok",
                         "query": "t:angel"},
            "unknowns": ["wobbly"],
            "empty": ["heists"],
            "thin": False,
        }
        out = app.strip_pool_magnitudes_for_handoff(inst)
        ok_spans = [s for s in out["spans"] if s["status"] == "ok"]
        self.assertTrue(ok_spans)
        for s in ok_spans:
            self.assertIsNone(s.get("count"))
            self.assertTrue(s.get("count_suppressed"))
        self.assertIsNone(out["composed"]["count"])
        self.assertTrue(out["composed"].get("count_suppressed"))
        # empty / unknown stay graded
        by = {s["said"]: s for s in out["spans"]}
        self.assertEqual(by["heists"]["count"], 0)
        self.assertEqual(by["heists"]["status"], "empty")
        self.assertIsNone(by["wobbly"]["count"])
        self.assertEqual(by["wobbly"]["status"], "unknown")


class NarrowingTurn(unittest.TestCase):
    """Machinery a narrowing beat consumes — KB ways, not pool size."""

    def test_pick_diversifies_by_lead_way(self):
        brief = {
            "theme": "angels",
            "angle": "wide angel board",
            "way_preference": "go wide",
            "way_playstyle": "tokens",
        }
        cards = [
            _card("Giada, Font of Hope"),
            _card("Lyra Dawnbringer"),
            _card("Avacyn, Angel of Hope"),
            _card("Reya Dawnbringer"),
        ]

        def fake_ways(card):
            name = card["name"]
            if "Giada" in name:
                return "ok", [{"playstyle": "tokens", "method": "both",
                               "why": "makes angels", "confidence": "high"}]
            if "Lyra" in name:
                return "ok", [{"playstyle": "voltron", "method": "both",
                               "why": "angel lord", "confidence": "high"}]
            if "Avacyn" in name:
                return "ok", [{"playstyle": "tokens", "method": "community",
                               "why": "also tokens", "confidence": "medium"}]
            if "Reya" in name:
                return "ok", [{"playstyle": "reanimator", "method": "both",
                               "why": "returns creatures", "confidence": "high"}]
            return "ok", []

        with mock.patch.object(app.cmd_kb, "_ways_for_card", side_effect=fake_ways):
            with mock.patch.object(
                app.cmd_kb, "theme_fit",
                return_value={"status": "ok", "score": 1, "matched": ["angel"]},
            ):
                with mock.patch.object(
                    app.playstyles, "map_way_playstyle", return_value="tokens"
                ):
                    # strategy_fit uses _ways_for_card + playstyles vocab
                    picked = app.pick_narrowing_candidates(cards, brief, n=3)

        names = [p["name"] for p in picked]
        self.assertEqual(len(names), 3)
        self.assertIn("Giada, Font of Hope", names)
        # Avacyn shares tokens with Giada — must not take the second tokens seat
        # while a distinct way (reanimator / voltron) is still available.
        self.assertNotIn("Avacyn, Angel of Hope", names)
        ways = {p["lead_way"] for p in picked}
        self.assertEqual(len(ways), 3)
        for p in picked:
            self.assertTrue(p.get("says"))
            self.assertNotIn("count", p)  # not a pool size

    def test_unknown_ways_never_become_zero(self):
        brief = {"theme": "angels", "angle": "angels"}
        cards = [_card("Mystery Angel")]

        with mock.patch.object(
            app.cmd_kb, "_ways_for_card", return_value=("unknown", [])
        ):
            with mock.patch.object(
                app.cmd_kb, "theme_fit",
                return_value={"status": "ok", "score": 1, "matched": ["angel"]},
            ):
                picked = app.pick_narrowing_candidates(cards, brief, n=3)
        # Card may still appear, but way status must stay unknown — never empty-zero.
        if picked:
            self.assertEqual(picked[0].get("ways_status"), "unknown")
            self.assertIsNone(picked[0].get("lead_way"))

    def test_build_narrowing_turn_unknown_vs_empty(self):
        brief = {"theme": "angels", "angle": "angels", "commander_query": "t:angel"}

        async def fake_unknown(*_a, **_k):
            return [], "t:angel", "unknown", None

        with mock.patch.object(app, "search_primary_rung", side_effect=fake_unknown):
            with mock.patch.object(
                app, "commander_ladder",
                return_value=(["is:commander t:angel"], set()),
            ):
                out = _run(app.build_narrowing_turn(brief))
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["candidates"], [])
        self.assertIsNone(out.get("commander_count"))
        self.assertIn("could not look", (out.get("note") or "").lower())

        async def fake_empty(*_a, **_k):
            return [], "t:angel", "ok", 0

        with mock.patch.object(app, "search_primary_rung", side_effect=fake_empty):
            with mock.patch.object(
                app, "commander_ladder",
                return_value=(["is:commander t:angel"], set()),
            ):
                out2 = _run(app.build_narrowing_turn(brief))
        self.assertEqual(out2["status"], "empty")
        self.assertEqual(out2["candidates"], [])
        self.assertEqual(out2.get("commander_count"), 0)

    def test_closing_narrowing_prose_names_real_candidates(self):
        narrowing = {
            "status": "ok",
            "candidates": [
                {"name": "Giada, Font of Hope", "lead_way": "tokens",
                 "says": "Make a wide board of creature tokens."},
                {"name": "Lyra Dawnbringer", "lead_way": "voltron",
                 "says": "Pile equipment on one creature."},
            ],
            "commander_count": 40,
            "note": None,
        }
        text = "Angels that swing as a choir."
        out = app.ensure_closing_narrowing(text, narrowing)
        self.assertIn("Giada, Font of Hope", out)
        self.assertIn("Lyra Dawnbringer", out)
        self.assertIn("tokens", out.lower())
        # Must not reintroduce the old pool-size sentence.
        self.assertNotIn("Together that is about", out)
        # Commander magnitude, if shown, is labelled.
        if "40" in out:
            self.assertRegex(out.lower(), r"40\s+angel|40\s+commander")

    def test_closing_narrowing_unknown_is_not_zero(self):
        narrowing = {
            "status": "unknown",
            "candidates": [],
            "commander_count": None,
            "note": "I could not look up commanders just now.",
        }
        out = app.ensure_closing_narrowing("Got it.", narrowing)
        self.assertIn("could not look", out.lower())
        self.assertNotRegex(out.lower(), r"\b0\b")


class ReadbackHidesHealthyPool(unittest.TestCase):
    def test_format_readback_count_suppresses_healthy(self):
        # Helper used by the UI contract / API shaping.
        self.assertIsNone(app.readback_count_display(278, thin=False))
        self.assertEqual(
            app.readback_count_display(0, thin=False),
            {"kind": "empty", "label": "none in Commander"},
        )
        self.assertEqual(
            app.readback_count_display(None, thin=False),
            {"kind": "unknown", "label": "couldn't reach Scryfall"},
        )
        # Thin pools stay visible — actionable, and labelled as cards.
        self.assertEqual(
            app.readback_count_display(4, thin=True),
            {"kind": "thin", "label": "4 cards in your colours", "count": 4},
        )


if __name__ == "__main__":
    unittest.main()
