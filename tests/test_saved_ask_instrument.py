"""
test_saved_ask_instrument.py — CON-261 saved-deck ask consumes instrument.

Offline where possible. No model.

    python test_saved_ask_instrument.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import app  # noqa: E402
from deckwright import synergy  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0
FIXTURES = ROOT / "data" / "synergy_fixtures.json"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


def _card(name: str, role: str = "theme") -> dict:
    c = json.loads(FIXTURES.read_text())["cards"][name]
    return {**c, "name": name, "role": role}


def test_deck_context_projects_way_fields():
    print("\ndeck_context() — way_preference / way_playstyle")
    data = {
        "commander": "Hei Bai, Spirit of Balance",
        "deck": [_card("Angel of Indemnity")],
        "brief": {
            "theme": "angels",
            "angle": "gain life",
            "way_preference": "gain life",
            "way_playstyle": "combo",  # freestyle — overwritten
            "mechanics": ["lifegain"],
        },
    }
    ctx = app.deck_context(data)
    eq("preference projected", ctx["brief"].get("way_preference"), "gain life")
    eq("playstyle remapped", ctx["brief"].get("way_playstyle"), "lifegain")
    facts = app.ask_prompt_facts(ctx)
    true("facts have WAY PREFERENCE line",
         "WAY PREFERENCE:" in facts and "gain life" in facts, facts[:500])
    true("facts name mapped playstyle",
         "mapped playstyle `lifegain`" in facts, facts[:500])


def test_improve_candidates_from_pools():
    print("\nimprove_candidates_from_pools() — glued pool leftovers")
    hei = _card("Hei Bai, Spirit of Balance", "commander")
    deck = [_card("Angel of Indemnity"), _card("Sol Ring", "ramp")]
    pools = {
        "theme": [
            _card("Pitiless Plunderer"),
            _card("Shock", "spot"),  # 0 edges — sorts after
            _card("Angel of Indemnity"),  # already in 99 — skip
        ],
        "plan": [_card("Deadly Dispute", "plan")],
    }
    out = app.improve_candidates_from_pools(
        deck, hei, pools, cap=10)
    names = [c["name"] for c in out]
    true("Plunderer offered", "Pitiless Plunderer" in names, str(names))
    true("Dispute offered", "Deadly Dispute" in names, str(names))
    true("already-in skipped", "Angel of Indemnity" not in names, str(names))
    # Shock may appear but after edged cards when both present
    if "Shock" in names and "Pitiless Plunderer" in names:
        true("edged before orphan",
             names.index("Pitiless Plunderer") < names.index("Shock"),
             str(names))


def test_ask_prompt_facts_instrument_block():
    print("\nask_prompt_facts() — instrument + improve blocks")
    ctx = {
        "commander": "X",
        "brief": {
            "theme": "pirates",
            "angle": "steal",
            "way_preference": "steal their stuff",
            "way_playstyle": None,
            "mechanics": [],
        },
        "deck": [{"name": "Sol Ring", "role": "ramp"}],
        "instrument": {
            "theme": "pirates",
            "spans": [{"said": "pirates", "count": 181, "status": "ok",
                       "query": "t:pirate"}],
            "composed": {"said": "pirates", "count": 181, "status": "ok",
                         "query": "t:pirate"},
            "unknowns": [],
            "empty": [],
        },
        "improve_candidates": [
            {"name": "Pitiless Plunderer", "edge_count": 3, "role": "theme"},
        ],
        "glued_payoffs": ["Angel of Indemnity"],
        "missing": [],
    }
    facts = app.ask_prompt_facts(ctx)
    true("instrument section", "INSTRUMENT" in facts, facts)
    true("ok count cited", "181" in facts, facts)
    true("improve candidates named", "Pitiless Plunderer" in facts, facts)
    true("glued payoffs named", "Angel of Indemnity" in facts, facts)
    true("unmapped way disclosed",
         "steal their stuff" in facts and "not mapped" in facts.lower(),
         facts)

    # CON-272: unread/unknown improve rows must not print edges=0.
    ctx2 = {
        **ctx,
        "improve_candidates": [
            {"name": "Mystery Bulk", "edge_count": 0, "role": "theme",
             "readable": False, "read_status": "unknown",
             "synergy_note": "could not read — not the same as no synergy"},
            {"name": "Shock", "edge_count": 0, "role": "theme",
             "readable": False, "read_status": "unread"},
            {"name": "Negate", "edge_count": 0, "role": "theme",
             "readable": True, "read_status": "ok"},
        ],
    }
    facts2 = app.ask_prompt_facts(ctx2)
    true("unknown improve says could not read",
         "Mystery Bulk" in facts2 and "could not read" in facts2.lower(),
         facts2)
    true("unknown improve does not say edges=0",
         "Mystery Bulk [edges=0" not in facts2, facts2)
    true("unread improve says could not read",
         "Shock" in facts2 and "could not read" in facts2.lower(),
         facts2)
    true("readable none may still show edges=0",
         "Negate [edges=0" in facts2, facts2)


def test_enrich_ask_context_async():
    print("\nenrich_ask_context() — rebuilds instrument, no freeze")
    import asyncio

    hei = _card("Hei Bai, Spirit of Balance")
    data = {
        "commander": "Hei Bai, Spirit of Balance",
        "commander_card": hei,
        "deck": [_card("Angel of Indemnity"), _card("Sol Ring", "ramp")],
        "brief": {
            "theme": "spirits",
            "angle": "sacrifice",
            "way_preference": "sacrifice",
            "colors": "WB",
            "mechanics": ["sacrifice"],
        },
        "role_pools": {
            "theme": [_card("Pitiless Plunderer"), _card("Deadly Dispute")],
        },
    }
    fake_inst = {
        "theme": "spirits",
        "spans": [{"said": "spirits", "count": 40, "status": "ok",
                   "query": "t:spirit"}],
        "composed": {"said": "spirits", "count": 40, "status": "ok",
                     "query": "t:spirit"},
        "unknowns": [],
        "empty": [],
    }

    async def run():
        with patch.object(app, "build_instrument",
                          new=AsyncMock(return_value=fake_inst)):
            return await app.enrich_ask_context(data)

    ctx = asyncio.run(run())
    eq("instrument attached", ctx.get("instrument"), fake_inst)
    eq("way mapped", ctx["brief"].get("way_playstyle"), "aristocrats")
    true("improve nonempty",
         bool(ctx.get("improve_candidates")),
         str(ctx.get("improve_candidates")))
    true("glued payoffs from deck",
         "Angel of Indemnity" in (ctx.get("glued_payoffs") or []),
         str(ctx.get("glued_payoffs")))


def test_decktalk_mentions_improve():
    print("\nDECKTALK — improve / instrument guidance")
    true("mentions instrument or improve",
         "instrument" in app.DECKTALK.lower()
         or "improve" in app.DECKTALK.lower()
         or "glued" in app.DECKTALK.lower(),
         app.DECKTALK[:200])


def main():
    test_deck_context_projects_way_fields()
    test_improve_candidates_from_pools()
    test_ask_prompt_facts_instrument_block()
    test_enrich_ask_context_async()
    test_decktalk_mentions_improve()
    print()
    if failed:
        print(f"{RED}FAILED{OFF} {failed}  {GREEN}passed{OFF} {passed}")
        return 1
    print(f"{GREEN}ALL OK{OFF}  {passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
