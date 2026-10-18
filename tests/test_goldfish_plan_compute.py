"""
Goldfish can compute plan conditions on the 99 — any plan shape.

Wiring thesis_from_plan is not enough: the engine has to actually move the
keys the plan asks for. Offline. No model.
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

from deckwright import plan_graph  # noqa: E402
from deckwright import sim  # noqa: E402
from deckwright import synergy  # noqa: E402

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


def _basic(name: str, color: str, n: int) -> list[dict]:
    return [{
        "name": f"{name}{i}",
        "role": "basic",
        "type": f"Basic Land — {name}",
        "type_line": f"Basic Land — {name}",
        "cmc": 0,
        "cost": "",
        "text": "",
        "produced_mana": [color],
        "color_identity": [color],
    } for i in range(n)]


def _card(name, role, typ, cmc, cost, text, n=1) -> list[dict]:
    out = []
    for i in range(n):
        out.append({
            "name": name if i == 0 else f"{name}#{i}",
            "role": role,
            "type": typ,
            "type_line": typ,
            "cmc": cmc,
            "cost": cost,
            "text": text,
            "oracle_text": text,
            "keywords": [],
        })
    return out


def _curve_at(curves: list[dict], label: str, turn: int) -> float | None:
    for c in curves:
        if c.get("label") == label:
            pts = c.get("points") or []
            if 1 <= turn <= len(pts):
                return pts[turn - 1]
    return None


def test_attack_token_extraction():
    print("\ngoldfish compute — attack-trigger token proxies")
    synergy._SIGNALS = None
    adeline = {
        "name": "Adeline, Resplendent Cathar",
        "type": "Legendary Creature — Human Knight",
        "cmc": 3,
        "cost": "{1}{W}{W}",
        "text": (
            "Vigilance\nWhenever you attack, for each opponent, create a 1/1 "
            "white Human creature token that is tapped and attacking that "
            "player or a planeswalker they control."
        ),
    }
    hero = {
        "name": "Hero of Bladehold",
        "type": "Creature — Human Knight",
        "cmc": 4,
        "cost": "{2}{W}{W}",
        "text": (
            "Whenever Hero of Bladehold attacks, create two 1/1 white Soldier "
            "creature tokens that are tapped and attacking."
        ),
    }
    vanilla = {
        "name": "Knight of Meadowgrain",
        "type": "Creature — Kithkin Knight",
        "cmc": 2,
        "cost": "{W}{W}",
        "text": "Lifelink",
    }
    ma = sim.mechanics(adeline, set())
    mh = sim.mechanics(hero, set())
    mv = sim.mechanics(vanilla, set())
    true("Adeline proxy ≥ 1", (ma.get("creature_token_proxy_per_turn") or 0) >= 1,
         str(ma.get("creature_token_proxy_per_turn")))
    true("Hero proxy ≥ 2", (mh.get("creature_token_proxy_per_turn") or 0) >= 2,
         str(mh.get("creature_token_proxy_per_turn")))
    eq("vanilla has no token proxy", mv.get("creature_token_proxy_per_turn") or 0, 0)


def test_wide_plan_computes():
    print("\ngoldfish compute — Adeline plan conditions move on the 99")
    cmdr = {
        "name": "Adeline, Resplendent Cathar",
        "type": "Legendary Creature — Human Knight",
        "type_line": "Legendary Creature — Human Knight",
        "cmc": 3,
        "cost": "{1}{W}{W}",
        "keywords": ["Vigilance"],
        "text": (
            "Vigilance\nAdeline, Resplendent Cathar's power is equal to the "
            "number of creatures you control.\nWhenever you attack, for each "
            "opponent, create a 1/1 white Human creature token that is tapped "
            "and attacking that player or a planeswalker they control."
        ),
    }
    brief = {
        "theme": "horses and knights",
        "strategy": "attack with it",
        "mechanics": ["tokens", "go wide"],
        "playstyle": "aggro",
    }
    deck = (
        _basic("Plains", "W", 36)
        + _card("Sol Ring", "ramp", "Artifact", 1, "{1}",
                "{T}: Add {C}{C}.", 2)
        + _card("Mind Stone", "ramp", "Artifact", 2, "{2}",
                "{T}: Add {C}.", 2)
        + _card("Raise the Alarm", "plan", "Instant", 2, "{1}{W}",
                "Create two 1/1 white Soldier creature tokens.", 4)
        + _card("Intangible Virtue", "plan", "Enchantment", 2, "{W}{W}",
                "Creature tokens you control get +1/+1 and have vigilance.", 2)
        + _card("Knight Exemplar", "theme", "Creature — Human Knight", 3,
                "{1}{W}{W}",
                "First strike\nOther Knight creatures you control get +1/+1.", 3)
        + _card("Mentor of the Meek", "theme", "Creature — Human Soldier", 3,
                "{2}{W}",
                "Whenever another creature with power 2 or less enters the "
                "battlefield under your control, you may pay {1}. If you do, "
                "draw a card.", 2)
        + _card("Soldier", "theme", "Creature — Soldier", 2, "{1}{W}",
                "", 20)
    )
    plan = plan_graph.derive(brief, cmdr)
    spec = plan_graph.thesis_from_plan(plan, deck, cmdr)
    true("thesis from plan", spec is not None)
    assert spec is not None

    # by_turn survives into the goldfish payload
    with_bt = [c for c in spec["conditions"] if c.get("by_turn")]
    true("conditions carry by_turn", len(with_bt) == len(spec["conditions"]),
         str(spec["conditions"]))

    out = sim.run(
        deck, int(cmdr["cmc"]), spec["payoff_cards"], spec["conditions"],
        runs=500, seed=42, commander_card=cmdr,
    )
    eq("ran 500", out["runs"], 500)
    true("assumptions mention attack-trigger proxy",
         "attack-trigger" in (out.get("assumptions") or "").lower())

    for cond in spec["conditions"]:
        label = cond["label"]
        by = int(cond.get("by_turn") or 5)
        curve = next(c for c in out["curves"] if c["label"] == label)
        eq(f"{label} by_turn on curve", curve.get("by_turn"), by)
        t10 = _curve_at(out["curves"], label, 10)
        true(f"{label} reachable by T10", (t10 or 0) > 0, f"T10={t10}")

    board = next(c for c in out["curves"]
                 if "creatures" in (c.get("label") or "").lower()
                 or "board" in (c.get("label") or "").lower())
    # With Adeline's attack proxy + Raise the Alarm, board width should clear
    # 50% well before turn 10 on this shell.
    true("board width clears 50% sometime",
         board.get("median_turn") is not None and board["median_turn"] <= 6,
         f"median={board.get('median_turn')} pts={board.get('points')}")

    # Commander alone must contribute tokens once cast — otherwise the plan's
    # board-width condition is lying about how Adeline works.
    m = sim.mechanics(cmdr, set(spec["payoff_cards"]))
    true("commander contributes token proxy",
         (m.get("creature_token_proxy_per_turn") or 0) >= 1, str(m))


def test_lifegain_plan_computes():
    print("\ngoldfish compute — lifegain plan life_gained moves")
    cmdr = {
        "name": "Lyra Dawnbringer",
        "type": "Legendary Creature — Angel",
        "cmc": 5,
        "cost": "{3}{W}{W}",
        "keywords": ["Flying", "First strike", "Lifelink"],
        "text": (
            "Flying, first strike, lifelink\n"
            "Other Angels you control get +1/+1 and have lifelink."
        ),
    }
    brief = {
        "theme": "angels",
        "strategy": "gain life every turn",
        "mechanics": ["lifegain"],
        "playstyle": "proactive",
    }
    deck = (
        _basic("Plains", "W", 36)
        + _card("Sol Ring", "ramp", "Artifact", 1, "{1}",
                "{T}: Add {C}{C}.", 2)
        + _card("Soul Warden", "theme", "Creature — Human Cleric", 1, "{W}",
                "Whenever another creature enters, you gain 1 life.", 4)
        + _card("Ajani's Welcome", "plan", "Enchantment", 1, "{W}",
                "Whenever a creature enters under your control, you gain 1 life.",
                3)
        + _card("Angel", "theme", "Creature — Angel", 3, "{2}{W}",
                "Flying", 25)
    )
    plan = plan_graph.derive(brief, cmdr)
    spec = plan_graph.thesis_from_plan(plan, deck, cmdr)
    true("lifegain thesis", spec is not None)
    assert spec is not None
    keys = {k for c in spec["conditions"] for k in c["check"]}
    true("plan asks life_gained", "life_gained" in keys, str(keys))

    out = sim.run(
        deck, int(cmdr["cmc"]), spec["payoff_cards"], spec["conditions"],
        runs=400, seed=7, commander_card=cmdr,
    )
    life = next(c for c in out["curves"]
                if "life" in (c.get("label") or "").lower())
    t10 = life["points"][9]
    true("life_gained > 0% by T10", t10 > 0, f"T10={t10} pts={life['points']}")


def main() -> int:
    test_attack_token_extraction()
    test_wide_plan_computes()
    test_lifegain_plan_computes()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
