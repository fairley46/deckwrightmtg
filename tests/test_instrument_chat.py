"""CON-256 — instrumented chat readback (unit / offline)."""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import json
import unittest
from unittest import mock

from deckwright import app


def _run(coro):
    return asyncio.run(coro)


class InstrumentChat(unittest.TestCase):
    def test_empty_theme_yields_empty_instrument(self):
        inst = _run(app.build_instrument(""))
        self.assertEqual(inst["spans"], [])
        self.assertIsNone(inst["composed"])
        self.assertEqual(inst["unknowns"], [])
        self.assertEqual(inst["empty"], [])

    def test_span_status_ok_empty_unknown(self):
        async def fake_readback(theme, colors=""):
            return {
                "theme": theme,
                "colors": colors,
                "terms": [
                    {
                        "said": "pirates",
                        "resolved": True,
                        "operator": "t:pirate",
                        "query": "t:pirate",
                        "count": 180,
                        "catalog": "type",
                        "note": None,
                    },
                    {
                        "said": "that steal",
                        "resolved": True,
                        "operator": "otag:theft",
                        "query": "otag:theft",
                        "count": 0,
                        "catalog": "otag",
                        "note": None,
                    },
                    {
                        "said": "heists",
                        "resolved": True,
                        "operator": "o:heist",
                        "query": "o:heist",
                        "count": None,
                        "catalog": "oracle",
                        "note": None,
                    },
                    {
                        "said": "zzzz",
                        "resolved": False,
                        "operator": None,
                        "query": None,
                        "count": None,
                        "catalog": None,
                        "note": "no match in Scryfall",
                    },
                ],
                "composed": {
                    "said": "pirates that steal",
                    "query": "t:pirate otag:theft",
                    "count": 12,
                },
                "thin": False,
                "thin_threshold": 10,
            }

        with mock.patch.object(app, "build_readback", side_effect=fake_readback):
            inst = _run(app.build_instrument("pirates that steal, heists"))

        by_said = {s["said"]: s for s in inst["spans"]}
        self.assertEqual(by_said["pirates"]["status"], "ok")
        self.assertEqual(by_said["pirates"]["count"], 180)
        self.assertEqual(by_said["that steal"]["status"], "empty")
        self.assertEqual(by_said["that steal"]["count"], 0)
        self.assertEqual(by_said["heists"]["status"], "unknown")
        self.assertIsNone(by_said["heists"]["count"])
        self.assertNotIn("zzzz", by_said)  # unresolved ≠ span with fake zero
        self.assertEqual(inst["empty"], ["that steal"])
        self.assertEqual(inst["unknowns"], ["heists"])
        self.assertEqual(inst["composed"]["status"], "ok")
        self.assertEqual(inst["composed"]["count"], 12)

    def test_composed_unknown_not_zero(self):
        async def fake_readback(theme, colors=""):
            return {
                "theme": theme,
                "colors": "",
                "terms": [
                    {
                        "said": "pirates",
                        "resolved": True,
                        "operator": "t:pirate",
                        "query": "t:pirate",
                        "count": 180,
                        "catalog": "type",
                        "note": None,
                    },
                    {
                        "said": "that steal",
                        "resolved": True,
                        "operator": "otag:theft",
                        "query": "otag:theft",
                        "count": 12,
                        "catalog": "otag",
                        "note": None,
                    },
                ],
                "composed": {
                    "said": "pirates that steal",
                    "query": "t:pirate otag:theft",
                    "count": None,
                },
                "thin": False,
                "thin_threshold": 10,
            }

        with mock.patch.object(app, "build_readback", side_effect=fake_readback):
            inst = _run(app.build_instrument("pirates that steal"))
        self.assertEqual(inst["composed"]["status"], "unknown")
        self.assertIsNone(inst["composed"]["count"])

    def test_theme_hint_prefers_brief(self):
        brief = {"theme": "pirates that steal", "angle": "theft"}
        messages = [
            {"role": "user", "content": "I like dragons"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "actually pirates"},
        ]
        self.assertEqual(
            app.theme_hint_for_instrument(messages, brief),
            "pirates that steal",
        )

    def test_theme_hint_falls_back_to_last_user(self):
        messages = [
            {"role": "user", "content": "pirates that steal"},
        ]
        self.assertEqual(
            app.theme_hint_for_instrument(messages, None),
            "pirates that steal",
        )

    def test_inject_instrument_strips_for_display(self):
        messages = [{"role": "user", "content": "pirates"}]
        inst = {
            "spans": [{"said": "pirates", "operator": "t:pirate",
                       "count": 180, "status": "ok"}],
            "composed": None,
            "unknowns": [],
            "empty": [],
            "readiness": None,
        }
        injected = app.messages_with_instrument(messages, inst)
        self.assertEqual(len(injected), 2)
        self.assertTrue(injected[-1]["content"].startswith(app.INSTRUMENT_FENCE))
        # Original messages untouched for UI
        self.assertEqual(messages[0]["content"], "pirates")
        self.assertFalse(
            any(app.INSTRUMENT_FENCE in (m.get("content") or "")
                for m in messages)
        )

    def test_closing_readback_does_not_force_ok_pool_count(self):
        # CON-287: healthy pool size is a builder diagnostic — not her handoff.
        inst = {
            "spans": [{"said": "pirates", "count": 180, "status": "ok",
                       "operator": "t:pirate"}],
            "composed": {"said": "pirates that steal", "count": 15,
                         "status": "ok", "query": "x"},
            "unknowns": [],
            "empty": [],
        }
        text = "Got it — thieves on the high seas."
        out = app.ensure_closing_instrument_readback(text, inst, still_asking=False)
        self.assertEqual(out, text)
        self.assertNotIn("15", out)
        self.assertNotIn("Together that is about", out)
        # Still asking — do not force close
        self.assertEqual(
            app.ensure_closing_instrument_readback(
                text, inst, still_asking=True),
            text,
        )

    def test_interrogator_mentions_instrument(self):
        self.assertIn("instrument", app.INTERROGATOR.lower())
        self.assertIn("unknown", app.INTERROGATOR.lower())


if __name__ == "__main__":
    unittest.main()
