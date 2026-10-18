"""
CON-268: goldfish applies commander oracle on cast.

Mister Negative's "draw that many" floors to 1 in extraction (CON-267).
Until this card, play_one only flipped commander_in_play — so cards_drawn
never moved from the commander. Life exchange with an opponent stays
unmodelled (no opponent invented).
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

MN_ORACLE = (
    "Vigilance, lifelink\n"
    "Darkforce Inversion — When Mister Negative enters, you may exchange "
    "life totals with target opponent. If you lost life this way, draw "
    "that many cards."
)


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


def _mn() -> dict:
    return {
        "name": "Mister Negative",
        "text": MN_ORACLE,
        "oracle_text": MN_ORACLE,
        "type": "Legendary Creature — Human Villain",
        "type_line": "Legendary Creature — Human Villain",
        "cmc": 7,
        "cost": "{5}{W}{B}",
        "keywords": ["Lifelink", "Vigilance", "Darkforce Inversion"],
        "color_identity": ["W", "B"],
    }


def _mana_shell(n_rocks=20, n_lands=36) -> list[dict]:
    """Enough rocks that a 7-drop resolves often in a 10-turn goldfish."""
    deck = []
    for i in range(n_lands):
        deck.append({
            "name": f"Plains {i}", "role": "basic",
            "text": "{T}: Add {W}.", "oracle_text": "{T}: Add {W}.",
            "type": "Basic Land — Plains", "type_line": "Basic Land — Plains",
            "cmc": 0, "cost": "", "keywords": [], "color_identity": ["W"],
        })
    for i in range(n_rocks):
        deck.append({
            "name": f"Rock {i}", "role": "ramp",
            "text": "{T}: Add {C}{C}.", "oracle_text": "{T}: Add {C}{C}.",
            "type": "Artifact", "type_line": "Artifact",
            "cmc": 2, "cost": "{2}", "keywords": [], "color_identity": [],
        })
    # Cheap fillers so the deck is ~99-ish without draw engines.
    for i in range(40):
        deck.append({
            "name": f"Body {i}", "role": "theme",
            "text": "Flying", "oracle_text": "Flying",
            "type": "Creature — Bird", "type_line": "Creature — Bird",
            "cmc": 2, "cost": "{1}{W}", "keywords": ["Flying"],
            "color_identity": ["W"],
        })
    return deck


def test_mn_draw_fires_on_commander_cast():
    print("\nCON-268 — Mister Negative draw that many fires on cast")
    deck = _mana_shell()
    cond = [{"label": "refuel", "note": "drew off the flip",
             "check": {"cards_drawn": 1}}]
    with_cmdr = sim.run(
        deck, 7, set(), cond, runs=800, seed=268,
        commander_card=_mn())
    without = sim.run(
        deck, 7, set(), cond, runs=800, seed=268,
        commander_card=None)
    a = with_cmdr["curves"][0]["points"][9]
    b = without["curves"][0]["points"][9]
    true("with commander oracle, cards_drawn fires", a > 20, f"T10={a}%")
    true("without commander oracle, cards_drawn stays near 0",
         b < 5, f"T10={b}%")
    true("commander oracle lifts the curve", a > b + 15, f"{a} vs {b}")


def test_life_exchange_not_invented():
    print("\nCON-268 — life exchange stays unmodelled (no opponent)")
    deck = _mana_shell()
    cond = [{"label": "pad", "note": "life exchange padded you",
             "check": {"life_gained": 1}}]
    out = sim.run(deck, 7, set(), cond, runs=400, seed=269,
                  commander_card=_mn())
    eq("life_gained from MN exchange is still 0%",
       out["curves"][0]["points"][9], 0.0)


def test_scryfall_shape_oracle_text_only():
    print("\nCON-268 — raw Scryfall shape (oracle_text/type_line) still draws")
    deck = _mana_shell()
    cond = [{"label": "refuel", "note": "drew",
             "check": {"cards_drawn": 1}}]
    raw = {
        "name": "Mister Negative",
        "oracle_text": MN_ORACLE,
        "type_line": "Legendary Creature — Human Villain",
        "cmc": 7, "mana_cost": "{5}{W}{B}",
        "keywords": ["Lifelink", "Vigilance"],
        "color_identity": ["W", "B"],
    }
    out = sim.run(deck, 7, set(), cond, runs=600, seed=270,
                  commander_card=raw)
    true("oracle_text-only commander still fires cards_drawn",
         out["curves"][0]["points"][9] > 20,
         f"T10={out['curves'][0]['points'][9]}%")


def test_assumptions_name_the_proxy():
    print("\nCON-268 — ASSUMPTIONS state commander on-cast + no exchange")
    true("assumptions mention commander on-cast effects",
         "on cast" in sim.ASSUMPTIONS.lower()
         or "commander" in sim.ASSUMPTIONS.lower())
    true("assumptions still deny inventing an opponent exchange",
         "exchange" in sim.ASSUMPTIONS.lower()
         or "no opponent" in sim.ASSUMPTIONS.lower(),
         sim.ASSUMPTIONS)


def test_refuel_story_ok_after_oracle_cast():
    print("\nCON-268 — refuel-off-the-flip is sound once MN draws on cast")
    deck = [{"name": "Consider", "role": "plan",
             "text": "Surveil 1, then draw a card.",
             "oracle_text": "Surveil 1, then draw a card.",
             "type": "Instant", "type_line": "Instant",
             "cmc": 1, "cost": "{U}", "keywords": ["Surveil"]}]
    spec = {
        "payoff_cards": ["Consider"],
        "conditions": [
            {"label": "Refuel off the flip",
             "note": "The life exchange paid off — you drew extra cards.",
             "check": {"cards_drawn": 1}},
            {"label": "Negative resolves",
             "note": "The commander is on board.",
             "check": {"commander_in_play": True}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 48.0]},
        {"points": [0, 0, 0, 0, 28.0]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn())
    eq("refuel story sound when commander contributes cards_drawn",
       r["sound"], True)


def test_exchange_life_gained_still_flagged():
    print("\nCON-268 — exchange→life_gained story still flagged")
    deck = [{"name": "Consider", "role": "plan",
             "text": "Draw a card.", "oracle_text": "Draw a card.",
             "type": "Instant", "type_line": "Instant",
             "cmc": 1, "cost": "{U}", "keywords": []}]
    spec = {
        "payoff_cards": ["Consider"],
        "conditions": [
            {"label": "Life padded early",
             "note": "Life exchange padded your life total.",
             "check": {"life_gained": 6}},
            {"label": "Negative resolves",
             "note": "commander on board",
             "check": {"commander_in_play": True}},
        ],
    }
    curves = [
        {"points": [0, 0, 0, 0, 5.0]},
        {"points": [0, 0, 0, 0, 28.0]},
    ]
    r = thesis_check.review(spec, curves, deck, commander_card=_mn())
    eq("exchange life_gained still unsound", r["sound"], False)


def main() -> int:
    test_mn_draw_fires_on_commander_cast()
    test_life_exchange_not_invented()
    test_scryfall_shape_oracle_text_only()
    test_assumptions_name_the_proxy()
    test_refuel_story_ok_after_oracle_cast()
    test_exchange_life_gained_still_flagged()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
