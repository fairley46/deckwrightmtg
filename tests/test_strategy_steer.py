"""soft steer + otag rollup tests. Pattern fixtures only."""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


LIFEGAIN_BOUND = {
    "name": "Fixture Lifegain Bound",
    "text": (
        "Whenever you gain life, return target artifact card with mana value "
        "less than or equal to the amount of life you gained this turn from "
        "your graveyard to the battlefield."
    ),
    "type": "Legendary Creature — Vampire Cleric",
    "cmc": 3,
}

UNREAD = {
    "name": "Fixture Unreadable",
    "text": "",
    "type": "Legendary Creature — Horror",
    "cmc": 5,
}


def test_bound_unknown_not_violation_or_steer_pass():
    print("\nGate 4 / G4-T1 — bound_ok unknown is not violation or steer pass")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    strategy = cs.derive(LIFEGAIN_BOUND)
    deck_so_far = [
        {"name": "Equal Life", "text": "You gain life equal to its toughness.",
         "type": "Instant", "cmc": 2},
    ]
    violator = {"name": "Big Rock", "text": "{T}: Add {C}{C}.",
                "type": "Artifact", "cmc": 6}
    eq("capacity unknown", strategy_audit_capacity_unknown(strategy, deck_so_far), True)
    eq("bound_ok unknown", ss.bound_ok(violator, strategy, deck_so_far), "unknown")
    eq("steer_vote none", ss.steer_vote(violator, strategy, deck_so_far), None)


def strategy_audit_capacity_unknown(strategy, deck):
    from deckwright import strategy_audit as sa
    return sa._capacity(strategy, deck).get("status") == "unknown"


def test_unread_commander_no_steer():
    print("\nGate 4 / G4-T2 — unread commander keeps V1 path (no steer)")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    strategy = cs.derive(UNREAD)
    eq("unread", strategy["status"], "unread")
    eq("should_steer false", ss.should_steer(strategy), False)
    card = {"name": "Shock", "text": "Deal 3 damage.", "type": "Instant", "cmc": 1}
    eq("steer_vote none", ss.steer_vote(card, strategy, []), None)


def test_no_named_commander_branches():
    print("\nGate 4 / G4-T3 — no illustration-list branches in steer modules")
    forbidden = ("Ratchet", "Kastral", "Rocco", "Ayli", "Ovika", "Jegantha")
    for path in (REPO / "deckwright" / "strategy_steer.py", REPO / "deckwright" / "otag_rollup.py"):
        text = path.read_text()
        for name in forbidden:
            true(f"{path.name} has no {name}", name not in text)


def test_artifact_mv_bound_skips_non_artifacts():
    """steer bound_ok must match audit type guards on artifact_mv."""
    print("\nGate 4 / artifact_mv bound skips creatures and sorceries")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(LIFEGAIN_BOUND)
        deck_so_far = [
            {"name": "Gain 4", "text": "You gain 4 life.", "type": "Instant", "cmc": 1},
        ]
        cap = sa._capacity(strategy, deck_so_far)
        eq("capacity ok", cap.get("status"), "ok")
        eq("capacity limit 4", cap.get("value"), 4)

        big_creature = {
            "name": "Big Creature", "type": "Creature — Beast", "cmc": 7,
        }
        big_sorcery = {
            "name": "Big Sorcery", "type": "Sorcery", "cmc": 6,
        }
        big_artifact = {
            "name": "Big Artifact", "type": "Artifact", "cmc": 7,
        }
        cheap_creature = {
            "name": "Cheap Creature", "type": "Creature — Human", "cmc": 2,
        }

        eq("steer creature ok", ss.bound_ok(big_creature, strategy, deck_so_far), "ok")
        eq("steer sorcery ok", ss.bound_ok(big_sorcery, strategy, deck_so_far), "ok")
        eq("steer artifact false", ss.bound_ok(big_artifact, strategy, deck_so_far), "false")

        audit = sa.audit(deck_so_far + [big_creature, big_artifact], strategy)
        eq(
            "audit violations",
            [v["name"] for v in audit["bound_violations"]],
            ["Big Artifact"],
        )

        pool = [big_creature, big_sorcery, big_artifact, cheap_creature]
        ordered = ss.prefer_strategy(pool, strategy, deck_so_far)
        eq("artifact demoted last", ordered[-1]["name"], "Big Artifact")
        true(
            "creature not demoted for artifact bound",
            ordered.index(big_creature) < ordered.index(big_artifact),
        )
        true(
            "sorcery not demoted for artifact bound",
            ordered.index(big_sorcery) < ordered.index(big_artifact),
        )
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def test_prefer_strategy_orders_feeder_before_violator():
    print("\nGate 4 / G4-T4 — prefer_strategy ranks feeders over violations")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(LIFEGAIN_BOUND)
        deck_so_far = [
            {"name": "Feeder", "text": "You gain 3 life.", "type": "Instant", "cmc": 1},
        ]
        feeder = {"name": "Salve", "text": "You gain 2 life.", "type": "Instant", "cmc": 1}
        violator = {"name": "Rock", "text": "Indestructible", "type": "Artifact", "cmc": 9}
        true("should_steer", ss.should_steer(strategy))
        ordered = ss.prefer_strategy([violator, feeder], strategy, deck_so_far)
        eq("feeder first", ordered[0]["name"], "Salve")
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


MV_FLOOR_OR_GREATER = {
    "name": "Fixture MV Floor Or Greater",
    "text": (
        "During your turn, each non-Equipment artifact you control with mana "
        "value 4 or greater is a 4/4 Elemental creature in addition to its "
        "other types and has haste."
    ),
    "type": "Legendary Creature — Bard",
    "cmc": 3,
}


def test_steer_floor_demotes_below_not_above():
    """floor bound demotes cheap subjects; expensive ones stay ok."""
    print("\nGate 4 / steer demotes below-floor, not above-floor")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(MV_FLOOR_OR_GREATER)
        floors = [b for b in strategy["bounds"] if b.get("op") == "geq"]
        true("strategy has floor", len(floors) >= 1, str(strategy["bounds"]))

        below = {"name": "Cheap Rock", "type": "Artifact", "cmc": 2}
        at_floor = {"name": "Floor Rock", "type": "Artifact", "cmc": 4}
        above = {"name": "Big Rock", "type": "Artifact", "cmc": 7}
        cheap_creature = {"name": "Peasant", "type": "Creature — Human", "cmc": 1}

        eq("below floor false", ss.bound_ok(below, strategy, []), "false")
        eq("at floor ok", ss.bound_ok(at_floor, strategy, []), "ok")
        eq("above floor ok", ss.bound_ok(above, strategy, []), "ok")
        eq("creature type-guard ok", ss.bound_ok(cheap_creature, strategy, []), "ok")

        audit = sa.audit([below, at_floor, above, cheap_creature], strategy)
        eq(
            "audit violations match steer",
            [v["name"] for v in audit["bound_violations"]],
            ["Cheap Rock"],
        )

        ordered = ss.prefer_strategy(
            [above, below, at_floor, cheap_creature], strategy, [],
        )
        eq("below demoted last", ordered[-1]["name"], "Cheap Rock")
        true(
            "above not demoted vs below",
            ordered.index(above) < ordered.index(below),
        )
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


BELLO_SHAPED_FLOOR = {
    "name": "Fixture Dual Animate Floor",
    "text": (
        "During your turn, each non-Equipment artifact and non-Aura enchantment "
        "you control with mana value 4 or greater is a 4/4 Elemental creature "
        "in addition to its other types and has haste."
    ),
    "type": "Legendary Creature — Bard",
    "cmc": 3,
}


def test_steer_equipment_ok_under_bello_floor():
    print("\nGate 4 / Equipment not demoted on Bello-shaped floor")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(BELLO_SHAPED_FLOOR)
        sword = {"name": "Cheap Sword", "type": "Artifact — Equipment", "cmc": 1}
        rock = {"name": "Cheap Rock", "type": "Artifact", "cmc": 2}
        ench = {"name": "Cheap Enchant", "type": "Enchantment", "cmc": 2}
        eq("equipment ok", ss.bound_ok(sword, strategy, []), "ok")
        eq("cheap rock false", ss.bound_ok(rock, strategy, []), "false")
        eq("cheap enchant false", ss.bound_ok(ench, strategy, []), "false")
        eq("equipment vote neutral", ss.steer_vote(sword, strategy, []), 0)
        ordered = ss.prefer_strategy([rock, sword, ench], strategy, [])
        true(
            "equipment before cheap rock",
            ordered.index(sword) < ordered.index(rock),
        )
        true(
            "equipment before cheap enchant",
            ordered.index(sword) < ordered.index(ench),
        )
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def test_cmp_votes_feeder_beats_unknown():
    """caveat 2: unknown bound is neutral; must not tie with feeder."""
    print("\nGate 4 / cmp_votes: feeder beats unknown/neutral")
    from deckwright import strategy_steer as ss

    eq("feeder vs unknown", ss.cmp_votes(1, None), 1)
    eq("unknown vs feeder", ss.cmp_votes(None, 1), -1)
    eq("feeder vs neutral", ss.cmp_votes(1, 0), 1)
    eq("unknown vs neutral tie", ss.cmp_votes(None, 0), 0)
    eq("violator loses to feeder", ss.cmp_votes(-1, 1), -1)
    eq("violator loses to unknown", ss.cmp_votes(-1, None), -1)
    eq("violator loses to neutral", ss.cmp_votes(-1, 0), -1)


def test_prefer_strategy_feeder_before_unknown_bound():
    print("\nGate 4 / prefer_strategy: feeder before unknown-bound card")
    import os
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        # Capacity bound with empty deck → capacity unknown → bound_ok unknown
        strategy = {
            "status": "ok",
            "inputs": [{"event": "gain_life", "supplier": "self"}],
            "outputs": [],
            "bounds": [{
                "subject": "artifact_mv",
                "op": "leq",
                "bound": {
                    "mode": "event_capacity",
                    "capacity_of": {"resource": "life", "aggregator": "max_static"},
                },
            }],
            "rate": {"supplier": "self", "growth_2p_to_4p": 1},
        }
        feeder = {"name": "Salve", "text": "You gain 2 life.", "type": "Instant", "cmc": 1}
        # Artifact with unknown capacity check → vote None
        unknown_art = {"name": "Mystery Rock", "text": "Indestructible", "type": "Artifact", "cmc": 3}
        eq("unknown bound vote", ss.steer_vote(unknown_art, strategy, []), None)
        eq("feeder vote", ss.steer_vote(feeder, strategy, []), 1)
        ordered = ss.prefer_strategy([unknown_art, feeder], strategy, [])
        eq("feeder first over unknown", ordered[0]["name"], "Salve")
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def test_otag_miss_is_unknown():
    print("\nGate 4 / G4-T5 — otag harvest miss → unknown, not zero tags")
    from deckwright import otag_rollup as oroll

    oroll.reset_cache()
    eq("store unknown when missing", oroll.store_status(), "unknown")
    eq("has_tag unknown", oroll.has_tag({"name": "Sol Ring"}, "ramp"), None)
    counts = oroll.rollup_counts([{"name": "Sol Ring"}], ["ramp"])
    eq("rollup status unknown", counts["status"], "unknown")
    eq("unknown_cards counted", counts["unknown_cards"], 1)
    eq("ramp not zero", counts["counts"].get("ramp"), 0)


def test_otag_file_loads(tmp_path: Path):
    print("\nGate 4 — otag membership file resolves tags")
    from deckwright import otag_rollup as oroll

    path = tmp_path / "otag_membership.json"
    path.write_text(json.dumps({
        "cards": {"Sol Ring": ["ramp", "mana-rock"]},
    }), encoding="utf-8")
    oroll.reset_cache()
    old = oroll.membership_path
    oroll.membership_path = lambda: path  # type: ignore[method-assign]
    try:
        eq("store ok", oroll.store_status(), "ok")
        eq("has ramp", oroll.has_tag({"name": "Sol Ring"}, "ramp"), True)
        eq("missing card unknown", oroll.has_tag({"name": "Missing"}, "ramp"), None)
    finally:
        oroll.membership_path = old  # type: ignore[method-assign]
        oroll.reset_cache()


_STAMP_KEYS = ("steer_vote", "bound_ok", "feeds_input")


def _stamp_out(deck, commander_card, strategy=None):
    import copy
    out = {
        "commander_card": commander_card,
        "deck": copy.deepcopy(deck),
    }
    if strategy is not None:
        out["commander_strategy"] = strategy
    return out


def test_stamp_votes_under_b():
    """Candidate B + readable commander stamps steer_vote / bound_ok / feeds_input."""
    print("\nGate 4 — stamp_votes present under B")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(LIFEGAIN_BOUND)
        deck = [
            {"name": "Gain 3", "text": "You gain 3 life.", "type": "Instant", "cmc": 1},
            {"name": "Salve", "text": "You gain 2 life.", "type": "Instant", "cmc": 1},
            {"name": "Rock", "text": "Indestructible", "type": "Artifact", "cmc": 9},
            {"name": "Bolt", "text": "Deal 3 damage.", "type": "Instant", "cmc": 1},
        ]
        out = _stamp_out(deck, LIFEGAIN_BOUND, strategy)
        names_before = [c["name"] for c in out["deck"]]
        ss.stamp_votes(out)
        eq("membership unchanged", [c["name"] for c in out["deck"]], names_before)
        for c in out["deck"]:
            true(f"{c['name']} has steer_vote", "steer_vote" in c)
            true(f"{c['name']} has bound_ok", "bound_ok" in c)
            true(f"{c['name']} has feeds_input", "feeds_input" in c)
            true(
                f"{c['name']} bound_ok value",
                c["bound_ok"] in ("ok", "false", "unknown"),
            )
            true(
                f"{c['name']} steer_vote int|None",
                c["steer_vote"] is None or c["steer_vote"] in (-1, 0, 1),
            )
            true(f"{c['name']} feeds_input bool", isinstance(c["feeds_input"], bool))
        by_name = {c["name"]: c for c in out["deck"]}
        eq("feeder vote +1", by_name["Salve"]["steer_vote"], 1)
        eq("feeder feeds_input", by_name["Salve"]["feeds_input"], True)
        eq("violator vote -1", by_name["Rock"]["steer_vote"], -1)
        eq("violator bound_ok false", by_name["Rock"]["bound_ok"], "false")
        eq("neutral vote 0", by_name["Bolt"]["steer_vote"], 0)
        eq("neutral feeds_input", by_name["Bolt"]["feeds_input"], False)
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def test_stamp_votes_absent_under_off():
    print("\nGate 4 — stamp_votes absent under off")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "off"
    try:
        strategy = cs.derive(LIFEGAIN_BOUND)
        deck = [
            {"name": "Salve", "text": "You gain 2 life.", "type": "Instant", "cmc": 1},
        ]
        out = _stamp_out(deck, LIFEGAIN_BOUND, strategy)
        names_before = [c["name"] for c in out["deck"]]
        ss.stamp_votes(out)
        eq("membership unchanged", [c["name"] for c in out["deck"]], names_before)
        for c in out["deck"]:
            for k in _STAMP_KEYS:
                eq(f"off omits {k}", k in c, False)
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def test_stamp_votes_absent_when_unread():
    print("\nGate 4 — stamp_votes absent when commander unread")
    import os
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    os.environ["DECKWRIGHT_CANDIDATE"] = "B"
    try:
        strategy = cs.derive(UNREAD)
        eq("unread", strategy["status"], "unread")
        deck = [
            {"name": "Shock", "text": "Deal 3 damage.", "type": "Instant", "cmc": 1},
        ]
        out = _stamp_out(deck, UNREAD, strategy)
        names_before = [c["name"] for c in out["deck"]]
        ss.stamp_votes(out)
        eq("membership unchanged", [c["name"] for c in out["deck"]], names_before)
        for c in out["deck"]:
            for k in _STAMP_KEYS:
                eq(f"unread omits {k}", k in c, False)
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_CANDIDATE", None)
        else:
            os.environ["DECKWRIGHT_CANDIDATE"] = old


def main() -> int:
    test_bound_unknown_not_violation_or_steer_pass()
    test_artifact_mv_bound_skips_non_artifacts()
    test_steer_floor_demotes_below_not_above()
    test_unread_commander_no_steer()
    test_no_named_commander_branches()
    test_prefer_strategy_orders_feeder_before_violator()
    test_steer_equipment_ok_under_bello_floor()
    test_cmp_votes_feeder_beats_unknown()
    test_prefer_strategy_feeder_before_unknown_bound()
    test_otag_miss_is_unknown()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_otag_file_loads(Path(td))
    test_stamp_votes_under_b()
    test_stamp_votes_absent_under_off()
    test_stamp_votes_absent_when_unread()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
