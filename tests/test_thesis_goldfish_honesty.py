"""
CON-267: thesis conditions the goldfish cannot see must not look like success.

Mister Negative shaped: drain/gain/draw stories that narrate the commander's
oracle while sim.mechanics contributes zeros — and cards_drawn grading other
cantrips as "refuel off the flip."
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import sim  # noqa: E402
from deckwright import thesis_check  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        bad(label, extra)


MN_ORACLE = (
    "Vigilance, lifelink\n"
    "Darkforce Inversion — When Mister Negative enters, you may exchange "
    "life totals with target opponent. If you lost life this way, draw "
    "that many cards."
)


def _mn_commander() -> dict:
    return {
        "name": "Mister Negative",
        "text": MN_ORACLE,
        "oracle_text": MN_ORACLE,
        "type": "Legendary Creature — Human Villain",
        "type_line": "Legendary Creature — Human Villain",
        "cmc": 7,
        "cost": "{5}{W}{B}",
        "keywords": ["Lifelink", "Vigilance", "Darkforce Inversion"],
    }


def _cantrip() -> dict:
    return {
        "name": "Consider",
        "role": "plan",
        "text": "Surveil 1, then draw a card.",
        "oracle_text": "Surveil 1, then draw a card.",
        "type": "Instant",
        "type_line": "Instant",
        "cmc": 1,
        "cost": "{U}",
        "keywords": ["Surveil"],
    }


def _artist() -> dict:
    return {
        "name": "Blood Artist",
        "role": "plan",
        "text": ("Whenever Blood Artist or another creature dies, target player "
                 "loses 1 life and you gain 1 life."),
        "oracle_text": ("Whenever Blood Artist or another creature dies, "
                        "target player loses 1 life and you gain 1 life."),
        "type": "Creature — Vampire",
        "type_line": "Creature — Vampire",
        "cmc": 2,
        "cost": "{1}{B}",
        "keywords": [],
    }


def test_mn_mechanics_baseline():
    print("\nCON-267 — Mister Negative extraction baseline")
    m = sim.mechanics(_mn_commander(), {"Mister Negative"})
    eq("MN draws after that-many floor", m.get("draws"), 1)
    eq("lifelink is not life_gain", m.get("life_gain"), 0)
    eq("lifelink is not drain", m.get("drain_amount") or 0, 0)
    eq("no drain proxy from lifelink", m.get("drain_proxy_per_turn") or 0, 0)


def test_draw_that_many_extraction():
    print("\nCON-267 — draw that many cards floors to 1")
    card = {
        "name": "That Many Drawer",
        "text": "When this creature enters, draw that many cards.",
        "oracle_text": "When this creature enters, draw that many cards.",
        "type": "Creature — Illusion",
        "type_line": "Creature — Illusion",
        "cmc": 3,
        "cost": "{2}{U}",
        "keywords": [],
    }
    m = sim.mechanics(card, set())
    eq("draw that many → draws floor 1", m.get("draws"), 1)


def test_key_support_drain_empty():
    print("\nCON-267 — life_drained with no drainers is unsound")
    deck = [_cantrip()] + [
        {"name": f"Filler {i}", "role": "theme", "text": "Flying",
         "oracle_text": "Flying", "type": "Creature — Bird",
         "type_line": "Creature — Bird", "cmc": 2, "cost": "{1}{U}",
         "keywords": ["Flying"]}
        for i in range(5)
    ]
    spec = {
        "payoff_cards": ["Consider"],
        "conditions": [
            {"label": "Close via drain", "note": "drain finishers bite",
             "check": {"life_drained": 4}},
            {"label": "Commander out", "note": "commander resolved",
             "check": {"commander_in_play": True}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 0.0]},
        {"points": [0, 0, 0, 0, 28.0]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn_commander())
    eq("unsound when drain has zero support", r["sound"], False)
    true("caveat mentions drain or goldfish",
         "drain" in (r.get("caveat") or "").lower()
         or "goldfish" in (r.get("caveat") or "").lower()
         or "cannot" in (r.get("caveat") or "").lower(),
         r.get("caveat") or "")


def test_key_support_drain_present():
    print("\nCON-267 — life_drained with Blood Artist is supported")
    deck = [_artist(), _cantrip()]
    spec = {
        "payoff_cards": ["Blood Artist"],
        "conditions": [
            {"label": "Close via drain", "note": "Artist drains",
             "check": {"life_drained": 4}},
            {"label": "Commander out", "note": "commander resolved",
             "check": {"commander_in_play": True}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 40.0]},
        {"points": [0, 0, 0, 0, 28.0]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn_commander())
    # May still fail commander-story on other conditions — only drain support here.
    support = thesis_check.key_support(spec["conditions"], deck,
                                       commander_card=_mn_commander())
    eq("drain support ok", support["ok"], True)


def test_commander_story_exchange_life_lie():
    print("\nCON-267/268 — exchange→life_gained is still a lie (no opponent)")
    deck = [_cantrip()]
    spec = {
        "payoff_cards": ["Consider"],
        "conditions": [
            {"label": "Life padded early",
             "note": ("Turns 1-3: life exchange has padded your life total "
                      "before you tap Negative."),
             "check": {"life_gained": 6}},
            {"label": "Negative resolves",
             "note": "The commander is on board.",
             "check": {"commander_in_play": True}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 5.4]},
        {"points": [0, 0, 0, 0, 28.3]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn_commander())
    eq("exchange life_gained is unsound", r["sound"], False)
    true("caveat names opponent/exchange",
         "exchange" in (r.get("caveat") or "").lower()
         or "opponent" in (r.get("caveat") or "").lower()
         or "life_gained" in (r.get("caveat") or "").lower(),
         r.get("caveat") or "")


def test_honest_commander_in_play_ok():
    print("\nCON-267 — commander_in_play story stays sound")
    deck = [_cantrip(), _artist()]
    spec = {
        "payoff_cards": ["Blood Artist", "Consider"],
        "conditions": [
            {"label": "Negative resolves",
             "note": "The commander is on board and survived.",
             "check": {"commander_in_play": True}},
            {"label": "Close via drain",
             "note": "drain finishers bite",
             "check": {"life_drained": 4}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 28.0]},
        {"points": [0, 0, 0, 0, 40.0]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn_commander())
    eq("honest commander + drain thesis is sound", r["sound"], True)


def main() -> int:
    test_mn_mechanics_baseline()
    test_draw_that_many_extraction()
    test_key_support_drain_empty()
    test_key_support_drain_present()
    test_commander_story_exchange_life_lie()
    test_honest_commander_in_play_ok()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
