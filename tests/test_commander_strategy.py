"""B clarity: CommanderStrategy + audit, no steer.

Offline fixtures only. Pattern texts, not named-commander product branches.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import copy
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


# ---------------------------------------------------------------------------
# Fixtures — synthetic oracle. Names are labels, not Scryfall product cases.
# ---------------------------------------------------------------------------

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

OPPONENT_CAST = {
    "name": "Fixture Opponent Cast",
    "text": (
        "Whenever an opponent casts a noncreature spell, create a 1/1 white "
        "Spirit creature token."
    ),
    "type": "Legendary Creature — Spirit",
    "cmc": 2,
}

BIRD_TYPE = {
    "name": "Fixture Bird Type",
    "text": (
        "Flying\n"
        "Whenever a Bird you control deals combat damage to a player, draw a card."
    ),
    "type": "Legendary Creature — Bird",
    "cmc": 4,
}

EMPTY_TEXT = {
    "name": "Fixture Unreadable",
    "text": "",
    "type": "Legendary Creature — Horror",
    "cmc": 5,
}

NOISE_ONLY = {
    "name": "Fixture Noise",
    "text": "This card has reminder text only. (Reminder.)",
    "type": "Legendary Creature — Illusion",
    "cmc": 2,
}

# board-count token engine (Krenko-shaped, pattern text only).
TOKEN_BOARD_COUNT = {
    "name": "Fixture Token Board Count",
    "text": (
        "{T}: Create X 1/1 red Goblin creature tokens, where X is the number "
        "of Goblins you control."
    ),
    "type": "Legendary Creature — Goblin Warrior",
    "cmc": 3,
}

# subtype-filter cast trigger (Edgar-shaped, pattern text only).
CAST_SUBTYPE = {
    "name": "Fixture Cast Subtype",
    "text": (
        "Whenever you cast another Vampire spell, create a 1/1 black Vampire "
        "creature token with lifelink."
    ),
    "type": "Legendary Creature — Vampire Knight",
    "cmc": 4,
}

# Outputs without a readable input → partial, not ok.
TOKENS_ONLY = {
    "name": "Fixture Tokens Only",
    "text": "{T}: Create three 1/1 Goblin creature tokens.",
    "type": "Legendary Creature — Goblin",
    "cmc": 2,
}

# static MV floor (Bello-shaped). Pattern text only.
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

# alternate floor phrasing.
MV_FLOOR_GEQ = {
    "name": "Fixture MV Floor GEQ",
    "text": (
        "Creatures you control with mana value greater than or equal to 3 "
        "have trample."
    ),
    "type": "Legendary Creature — Beast",
    "cmc": 4,
}

# static MV ceiling (Tayam-shaped numeric "or less", not life capacity).
MV_CEILING_OR_LESS = {
    "name": "Fixture MV Ceiling Or Less",
    "text": (
        "{3}: Return a permanent card with mana value 3 or less from your "
        "graveyard to the battlefield."
    ),
    "type": "Legendary Creature — Nightmare",
    "cmc": 3,
}

# remaining: power ceiling (Shirei-shaped). Pattern text only.
POWER_CEILING_OR_LESS = {
    "name": "Fixture Power Ceiling",
    "oracle_text": (
        "Whenever a creature with power 1 or less is put into your graveyard "
        "from the battlefield, you may return that card to the battlefield "
        "at the beginning of the next end step."
    ),
    "type_line": "Legendary Creature — Spirit",
    "cmc": 3,
}

# remaining: forbid draw (Maralen-shaped).
FORBID_DRAW = {
    "name": "Fixture Forbid Draw",
    "oracle_text": (
        "Players can't draw cards.\n"
        "At the beginning of each player's draw step, that player loses 3 life, "
        "searches their library for a card, puts it into their hand, then shuffles."
    ),
    "type_line": "Legendary Creature — Elf Wizard",
    "cmc": 3,
}

# remaining: subtype tutor / steal (Sliver Overlord-shaped).
SUBTYPE_TUTOR = {
    "name": "Fixture Subtype Tutor",
    "oracle_text": (
        "{3}: Search your library for a Sliver card, reveal that card, put it "
        "into your hand, then shuffle.\n"
        "{3}: Gain control of target Sliver."
    ),
    "type_line": "Legendary Creature — Specimen Mutant",
    "cmc": 5,
}

# remaining: experience-capacity MV ceiling (Meren-shaped).
MV_LEQ_EXPERIENCE = {
    "name": "Fixture Experience Capacity",
    "oracle_text": (
        "Whenever another creature you control dies, you get an experience counter.\n"
        "At the beginning of your end step, choose target creature card in your "
        "graveyard. If that card's mana value is less than or equal to the number "
        "of experience counters you have, return it to the battlefield. Otherwise, "
        "put it into your hand."
    ),
    "type_line": "Legendary Creature — Human Shaman",
    "cmc": 4,
}


def test_power_ceiling_extracts():
    print("\nGate 2 / power 'N or less' extracts creature_power leq")
    from deckwright import commander_strategy as cs
    s = cs.derive(POWER_CEILING_OR_LESS)
    powers = [
        b for b in s["bounds"]
        if b.get("subject") == "creature_power"
    ]
    true("has creature_power bound", len(powers) >= 1, str(s["bounds"]))
    if not powers:
        return
    b0 = powers[0]
    eq("op leq", b0["op"], "leq")
    eq("mode static", b0["bound"]["mode"], "static")
    eq("value 1", b0["bound"]["value"], 1)


def test_forbid_draw_extracts():
    print("\nGate 2 / 'can't draw cards' extracts draw forbid")
    from deckwright import commander_strategy as cs
    s = cs.derive(FORBID_DRAW)
    forbids = [
        b for b in s["bounds"]
        if b.get("subject") == "draw" and b.get("op") == "forbid"
    ]
    true("has draw forbid", len(forbids) >= 1, str(s["bounds"]))


def test_subtype_tutor_extracts():
    print("\nGate 2 / tutor/steal subtype extracts creature_subtype")
    from deckwright import commander_strategy as cs
    s = cs.derive(SUBTYPE_TUTOR)
    subs = [
        b for b in s["bounds"]
        if b.get("subject") == "creature_subtype"
    ]
    true("has creature_subtype", len(subs) >= 1, str(s["bounds"]))
    if not subs:
        return
    eq("op in_set", subs[0]["op"], "in_set")
    eq("value sliver", (subs[0]["bound"].get("value") or "").lower(), "sliver")


def test_experience_capacity_extracts():
    print("\nGate 2 / MV ≤ experience counters extracts event_capacity")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_LEQ_EXPERIENCE)
    caps = [
        b for b in s["bounds"]
        if (b.get("bound") or {}).get("mode") == "event_capacity"
        and ((b.get("bound") or {}).get("capacity_of") or {}).get("resource")
        == "experience"
    ]
    true("has experience capacity", len(caps) >= 1, str(s["bounds"]))
    if not caps:
        return
    eq("op leq", caps[0]["op"], "leq")
    eq("subject creature_mv", caps[0]["subject"], "creature_mv")


def test_audit_power_ceiling_and_subtype():
    print("\nGate 2 / audit honour power leq + subtype in_set")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa
    power_s = cs.derive(POWER_CEILING_OR_LESS)
    tiny = {"name": "Tiny", "type_line": "Creature — Spirit", "power": "1", "cmc": 1}
    big = {"name": "Big", "type_line": "Creature — Demon", "power": "5", "cmc": 5}
    eq("power 1 ok", sa.card_bound_ok(tiny, power_s, []), "ok")
    eq("power 5 false", sa.card_bound_ok(big, power_s, []), "false")

    sub_s = cs.derive(SUBTYPE_TUTOR)
    sliver = {"name": "Hive", "type_line": "Creature — Sliver", "cmc": 2}
    bear = {"name": "Bear", "type_line": "Creature — Bear", "cmc": 2}
    eq("sliver ok", sa.card_bound_ok(sliver, sub_s, []), "ok")
    eq("non-sliver creature false", sa.card_bound_ok(bear, sub_s, []), "false")


def test_single_non_equipment_floor_excludes_equipment():
    print("\nGate 2 / non-Equipment artifact floor carries excludes")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_FLOOR_OR_GREATER)
    floors = [b for b in s["bounds"] if b.get("op") == "geq"]
    true("has floor", len(floors) >= 1, str(floors))
    eq("excludes equipment", floors[0].get("excludes"), ["equipment"])


def test_mv_floor_or_greater_extracts():
    print("\nGate 2 / MV floor 'N or greater' extracts geq static")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_FLOOR_OR_GREATER)
    bounds = s["bounds"]
    true("has a bound", len(bounds) >= 1, str(bounds))
    floors = [b for b in bounds if b.get("op") == "geq"]
    true("has geq floor", len(floors) >= 1, str(bounds))
    if not floors:
        return
    b0 = floors[0]
    eq("bound subject artifact_mv", b0["subject"], "artifact_mv")
    eq("bound op geq", b0["op"], "geq")
    eq("bound mode static", b0["bound"]["mode"], "static")
    eq("bound value 4", b0["bound"]["value"], 4)


def test_mv_floor_geq_phrasing_extracts():
    print("\nGate 2 / MV floor 'greater than or equal to N'")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_FLOOR_GEQ)
    floors = [b for b in s["bounds"] if b.get("op") == "geq"]
    true("has geq floor", len(floors) >= 1, str(s["bounds"]))
    if not floors:
        return
    b0 = floors[0]
    eq("bound subject creature_mv", b0["subject"], "creature_mv")
    eq("bound value 3", b0["bound"]["value"], 3)
    eq("bound mode static", b0["bound"]["mode"], "static")


def test_mv_static_ceiling_or_less_extracts():
    print("\nGate 2 / static MV ceiling 'N or less' still leq")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_CEILING_OR_LESS)
    ceilings = [
        b for b in s["bounds"]
        if b.get("op") == "leq" and (b.get("bound") or {}).get("mode") == "static"
    ]
    true("has static leq ceiling", len(ceilings) >= 1, str(s["bounds"]))
    if not ceilings:
        return
    b0 = ceilings[0]
    eq("bound value 3", b0["bound"]["value"], 3)
    true(
        "subject is mv-typed",
        b0["subject"] in ("card_mv", "creature_mv", "artifact_mv"),
        b0["subject"],
    )



# Bello-shaped dual floor — non-Equipment artifacts AND non-Aura enchantments.
MV_FLOOR_BELLO_SHAPED = {
    "name": "Fixture Dual Animate Floor",
    "text": (
        "During your turn, each non-Equipment artifact and non-Aura enchantment "
        "you control with mana value 4 or greater is a 4/4 Elemental creature "
        "in addition to its other types and has haste."
    ),
    "type": "Legendary Creature — Bard",
    "cmc": 3,
}


def test_bello_shaped_floor_emits_artifact_and_enchantment():
    print("\nGate 2 / dual non-Equipment / non-Aura floor subjects")
    from deckwright import commander_strategy as cs
    s = cs.derive(MV_FLOOR_BELLO_SHAPED)
    floors = [b for b in s["bounds"] if b.get("op") == "geq"]
    subjects = sorted(b["subject"] for b in floors)
    true("has two geq floors", len(floors) >= 2, str(floors))
    true("artifact_mv present", "artifact_mv" in subjects, str(subjects))
    true("enchantment_mv present", "enchantment_mv" in subjects, str(subjects))
    art = next(b for b in floors if b["subject"] == "artifact_mv")
    ench = next(b for b in floors if b["subject"] == "enchantment_mv")
    eq("artifact excludes equipment", art.get("excludes"), ["equipment"])
    eq("enchantment excludes aura", ench.get("excludes"), ["aura"])
    eq("artifact value 4", art["bound"]["value"], 4)
    eq("enchantment value 4", ench["bound"]["value"], 4)


def test_audit_equipment_and_aura_exempt_from_bello_floor():
    print("\nGate 2 / Equipment/Aura exempt; cheap enchantment flagged")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(MV_FLOOR_BELLO_SHAPED)
    deck = [
        {"name": "Cheap Rock", "type": "Artifact", "cmc": 2},
        {"name": "Cheap Sword", "type": "Artifact — Equipment", "cmc": 1},
        {"name": "Cheap Aura", "type": "Enchantment — Aura", "cmc": 2},
        {"name": "Cheap Enchant", "type": "Enchantment", "cmc": 2},
        {"name": "Floor Enchant", "type": "Enchantment", "cmc": 4},
        {"name": "Enchant Creature", "type": "Enchantment Creature — Dryad", "cmc": 2},
    ]
    report = sa.audit(deck, strategy)
    viol = {v["name"] for v in report["bound_violations"]}
    true("cheap artifact violates", "Cheap Rock" in viol, str(viol))
    true("equipment exempt", "Cheap Sword" not in viol, str(viol))
    true("aura exempt", "Cheap Aura" not in viol, str(viol))
    true("cheap enchantment violates", "Cheap Enchant" in viol, str(viol))
    true("at-floor enchantment ok", "Floor Enchant" not in viol, str(viol))
    true("cheap enchantment creature violates", "Enchant Creature" in viol, str(viol))


def test_audit_floor_flags_below_not_above():
    print("\nGate 2 / audit flags cards below floor when subject matches")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(MV_FLOOR_OR_GREATER)
    deck = [
        {"name": "Cheap Rock", "text": "{T}: Add {C}.",
         "type": "Artifact", "cmc": 1},
        {"name": "Floor Rock", "text": "Indestructible",
         "type": "Artifact", "cmc": 4},
        {"name": "Expensive Rock", "text": "{T}: Add {C}{C}{C}.",
         "type": "Artifact", "cmc": 6},
        {"name": "Cheap Creature", "text": "Vigilance",
         "type": "Creature — Human", "cmc": 1},
    ]
    report = sa.audit(deck, strategy)
    viol_names = [v["name"] for v in report["bound_violations"]]
    true("cheap artifact violates floor", "Cheap Rock" in viol_names, str(viol_names))
    true("at-floor artifact ok", "Floor Rock" not in viol_names, str(viol_names))
    true("above-floor artifact ok", "Expensive Rock" not in viol_names, str(viol_names))
    true(
        "creature skipped for artifact_mv floor",
        "Cheap Creature" not in viol_names,
        str(viol_names),
    )


def test_lifegain_bound_strategy():
    print("\nGate 2 — lifegain + MV capacity bound")
    from deckwright import commander_strategy as cs
    s = cs.derive(LIFEGAIN_BOUND)
    eq("status ok or partial", s["status"] in ("ok", "partial"), True)
    eq("commander name", s["commander"], "Fixture Lifegain Bound")
    events = [i["event"] for i in s["inputs"]]
    true("gain_life input", "gain_life" in events, str(events))
    suppliers = {i.get("supplier") for i in s["inputs"]}
    true("self-supplied input", "self" in suppliers, str(suppliers))
    kinds = [o["kind"] for o in s["outputs"]]
    true("recur output", "recur_to_battlefield" in kinds, str(kinds))
    bounds = s["bounds"]
    true("has a bound", len(bounds) >= 1, str(bounds))
    b0 = bounds[0]
    eq("bound subject artifact_mv", b0["subject"], "artifact_mv")
    eq("bound op leq", b0["op"], "leq")
    eq("bound mode event_capacity", b0["bound"]["mode"], "event_capacity")
    eq("capacity resource life",
       b0["bound"]["capacity_of"]["resource"], "life")
    eq("rate supplier self", s["rate"]["supplier"], "self")
    eq("soft_combo weight 0", s["weights"]["soft_combo"], 0)
    eq("hidden_power weight 0", s["weights"]["hidden_power"], 0)


def test_opponent_trigger_strategy():
    print("\nGate 2 — opponent-supplied trigger")
    from deckwright import commander_strategy as cs
    s = cs.derive(OPPONENT_CAST)
    events = [i["event"] for i in s["inputs"]]
    true("cast_noncreature input", "cast_noncreature" in events, str(events))
    suppliers = {i.get("supplier") for i in s["inputs"] if i["event"] == "cast_noncreature"}
    eq("opponent supplier", suppliers, {"opponent"})
    eq("rate supplier opponent", s["rate"]["supplier"], "opponent")
    eq("growth 2p→4p is 3", s["rate"]["growth_2p_to_4p"], 3)
    kinds = [o["kind"] for o in s["outputs"]]
    true("tokens output", "tokens" in kinds, str(kinds))


def test_bird_type_strategy():
    print("\nGate 2 — creature subtype combat trigger")
    from deckwright import commander_strategy as cs
    s = cs.derive(BIRD_TYPE)
    events = [i["event"] for i in s["inputs"]]
    true("combat_damage input", "combat_damage" in events, str(events))
    filt = next(
        (i.get("filters") or {} for i in s["inputs"] if i["event"] == "combat_damage"),
        {},
    )
    eq("subtype Bird filter", (filt.get("subtype") or "").lower(), "bird")
    kinds = [o["kind"] for o in s["outputs"]]
    true("draw output", "draw" in kinds, str(kinds))


def test_token_board_count_strategy():
    print("\nGate 2 / board-count token engine")
    from deckwright import commander_strategy as cs
    s = cs.derive(TOKEN_BOARD_COUNT)
    eq("status ok", s["status"], "ok")
    events = [i["event"] for i in s["inputs"]]
    true("board_count input", "board_count" in events, str(events))
    filt = next(
        (i.get("filters") or {} for i in s["inputs"] if i["event"] == "board_count"),
        {},
    )
    eq("subtype Goblin filter", (filt.get("subtype") or "").lower(), "goblin")
    kinds = [o["kind"] for o in s["outputs"]]
    true("tokens output", "tokens" in kinds, str(kinds))
    tok = next(o for o in s["outputs"] if o["kind"] == "tokens")
    eq("token magnitude board_count", tok["magnitude"]["mode"], "board_count")
    eq("token count subtype",
       tok["magnitude"]["count_of"]["subtype"].lower(), "goblin")


def test_cast_subtype_strategy():
    print("\nGate 2 / cast another subtype spell")
    from deckwright import commander_strategy as cs
    s = cs.derive(CAST_SUBTYPE)
    eq("status ok", s["status"], "ok")
    events = [i["event"] for i in s["inputs"]]
    true("cast_spell input", "cast_spell" in events, str(events))
    filt = next(
        (i.get("filters") or {} for i in s["inputs"] if i["event"] == "cast_spell"),
        {},
    )
    eq("subtype Vampire filter", (filt.get("subtype") or "").lower(), "vampire")
    kinds = [o["kind"] for o in s["outputs"]]
    true("tokens output", "tokens" in kinds, str(kinds))


def test_outputs_without_inputs_is_partial():
    print("\nGate 2 / outputs without inputs is partial, not ok")
    from deckwright import commander_strategy as cs
    s = cs.derive(TOKENS_ONLY)
    eq("status partial", s["status"], "partial")
    eq("no inputs", s["inputs"], [])
    true("has outputs", len(s["outputs"]) >= 1)
    true("partial honesty",
         "produces" in (s.get("honesty") or "").lower()
         or "needs" in (s.get("honesty") or "").lower(),
         s.get("honesty") or "")


def test_audit_skips_feeder_line_when_no_inputs():
    print("\nGate 2 / no feeder audit when inputs empty")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(TOKENS_ONLY)
    deck = [
        {"name": "Goblin One", "text": "", "type": "Creature — Goblin", "cmc": 1},
    ]
    report = sa.audit(deck, strategy)
    eq("strategy partial", report["strategy_status"], "partial")
    eq("no feeders listed", report["feeders"], [])
    eq("feeder audit skipped flag", report.get("feeder_audit_skipped"), True)


def test_board_count_feeders():
    print("\nGate 2 / board-count feeders match subtype creatures")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(TOKEN_BOARD_COUNT)
    deck = [
        {"name": "Goblin Matron", "text": "Search your library for a Goblin.",
         "type": "Creature — Goblin", "cmc": 3},
        {"name": "Sol Ring", "text": "{T}: Add {C}{C}.",
         "type": "Artifact", "cmc": 1},
    ]
    report = sa.audit(deck, strategy)
    feeder_names = [c["name"] for c in report["feeders"]]
    true("goblin feeder", "Goblin Matron" in feeder_names, str(feeder_names))
    true("artifact not feeder", "Sol Ring" not in feeder_names, str(feeder_names))


def test_cast_subtype_feeders():
    print("\nGate 2 / subtype cast feeders match tribe on type line")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(CAST_SUBTYPE)
    deck = [
        {"name": "Vampire Nighthawk", "text": "Flying, deathtouch, lifelink",
         "type": "Creature — Vampire Shaman", "cmc": 3},
        {"name": "Lightning Bolt", "text": "Deal 3 damage.",
         "type": "Instant", "cmc": 1},
    ]
    report = sa.audit(deck, strategy)
    feeder_names = [c["name"] for c in report["feeders"]]
    true("vampire feeder", "Vampire Nighthawk" in feeder_names, str(feeder_names))
    true("bolt not feeder", "Lightning Bolt" not in feeder_names, str(feeder_names))


def test_unread_commander():
    print("\nGate 2 — unread commander is not invented")
    from deckwright import commander_strategy as cs
    for card, label in ((EMPTY_TEXT, "empty"), (NOISE_ONLY, "noise")):
        s = cs.derive(card)
        eq(f"{label} status unread", s["status"], "unread")
        eq(f"{label} no inputs", s["inputs"], [])
        eq(f"{label} no outputs", s["outputs"], [])
        eq(f"{label} no bounds", s["bounds"], [])
        true(f"{label} honesty note", bool(s.get("honesty")), s.get("honesty") or "")


def test_audit_feeders_bounds_unknown():
    print("\nGate 2 — audit feeders, bound break, unknown capacity")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(LIFEGAIN_BOUND)
    deck = [
        {"name": "Feeder Salve", "text": "You gain 3 life.",
         "type": "Instant", "cmc": 1},
        {"name": "Dead Mana Rock", "text": "{T}: Add {C}.",
         "type": "Artifact", "cmc": 2},
        {"name": "Expensive Artifact", "text": "Indestructible",
         "type": "Artifact", "cmc": 5},
        {"name": "Small Artifact", "text": "Equipped creature gets +1/+1.",
         "type": "Artifact — Equipment", "cmc": 1},
        {"name": "Land Drop", "text": "", "type": "Basic Land — Plains", "cmc": 0},
    ]
    # Capacity known from Feeder Salve static 3 → Expensive (5) violates, Small (1) ok.
    report = sa.audit(deck, strategy)
    feeder_names = [c["name"] for c in report["feeders"]]
    true("feeder listed", "Feeder Salve" in feeder_names, str(feeder_names))
    eq("feeder count", report["feeder_count"], len(report["feeders"]))
    viol = report["bound_violations"]
    viol_names = [c["name"] for c in viol]
    true("expensive artifact violates", "Expensive Artifact" in viol_names, str(viol_names))
    true("small artifact not a violation",
         "Small Artifact" not in viol_names, str(viol_names))
    eq("unknown bounds separate", isinstance(report["bound_unknown"], list), True)
    # Dead outputs: recur_to_battlefield — nothing in the 99 consumes returns
    # (Gate 2 coarse: no card mentions returning from graveyard as a need).
    true("dead_outputs is a list", isinstance(report["dead_outputs"], list))
    eq("status keyed", report["strategy_status"], strategy["status"])
    true("concepts 4/6 named unscoreable",
         report["unscoreable"] == ["soft_combo", "hidden_power"]
         or set(report["unscoreable"]) >= {"soft_combo", "hidden_power"},
         str(report.get("unscoreable")))


def test_audit_unknown_capacity_not_violation():
    print("\nGate 2 — unknown capacity is not a pass and not a violation")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(LIFEGAIN_BOUND)
    # Ref magnitude only — no static life number → capacity unknown.
    deck = [
        {"name": "Equal Life", "text": "You gain life equal to its toughness.",
         "type": "Instant", "cmc": 2},
        {"name": "Big Rock", "text": "{T}: Add {C}{C}.",
         "type": "Artifact", "cmc": 6},
    ]
    report = sa.audit(deck, strategy)
    eq("no hard violations when capacity unknown",
       report["bound_violations"], [])
    true("bound_unknown non-empty", len(report["bound_unknown"]) >= 1,
         str(report["bound_unknown"]))
    # unknown ≠ counted as zero violations
    eq("violation_count", report["violation_count"], 0)
    eq("unknown_count", report["unknown_count"], len(report["bound_unknown"]))


def test_attach_clarity_does_not_change_membership():
    print("\nGate 2 — attach clarity leaves the 99 membership alone")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    deck = [
        {"name": "A", "text": "You gain 2 life.", "type": "Instant", "cmc": 1},
        {"name": "B", "text": "{T}: Add {G}.", "type": "Artifact", "cmc": 2},
    ]
    out = {
        "commander": "Fixture Lifegain Bound",
        "commander_card": LIFEGAIN_BOUND,
        "deck": copy.deepcopy(deck),
    }
    names_before = [c["name"] for c in out["deck"]]
    sa.attach_clarity(out, enabled=True)
    names_after = [c["name"] for c in out["deck"]]
    eq("membership identical", names_after, names_before)
    true("strategy attached", isinstance(out.get("commander_strategy"), dict))
    true("audit attached", isinstance(out.get("strategy_audit"), dict))
    # Flag off: no keys, still same membership
    out2 = {
        "commander": "Fixture Lifegain Bound",
        "commander_card": LIFEGAIN_BOUND,
        "deck": copy.deepcopy(deck),
    }
    sa.attach_clarity(out2, enabled=False)
    eq("flag off no strategy key", "commander_strategy" in out2, False)
    eq("flag off no audit key", "strategy_audit" in out2, False)
    eq("flag off membership", [c["name"] for c in out2["deck"]], names_before)


def test_unread_attach_honesty():
    print("\nGate 2 — unread attach keeps V1 list and says so")
    from deckwright import strategy_audit as sa
    out = {
        "commander": "Fixture Unreadable",
        "commander_card": EMPTY_TEXT,
        "deck": [{"name": "X", "text": "Draw a card.", "type": "Sorcery", "cmc": 1}],
    }
    sa.attach_clarity(out, enabled=True)
    eq("unread status", out["commander_strategy"]["status"], "unread")
    true("honesty present",
         "could not read" in (out["commander_strategy"].get("honesty") or "").lower()
         or "could not read" in (out["strategy_audit"].get("honesty") or "").lower(),
         str(out["commander_strategy"]))
    eq("deck still one card", len(out["deck"]), 1)


def test_no_named_commander_branches():
    print("\nGate 2 — no illustration-list name branches in strategy modules")
    forbidden = (
        "Ratchet", "Kastral", "Rocco", "Ayli", "Ovika", "Jegantha",
    )
    for path in (
        REPO / "deckwright" / "commander_strategy.py",
        REPO / "deckwright" / "strategy_audit.py",
    ):
        text = path.read_text()
        for name in forbidden:
            true(f"{path.name} has no {name}", name not in text)


def test_sim_version_untouched():
    print("\nGate 2 — SIM_VERSION / THESIS_KEYS untouched")
    from deckwright import sim
    # Gate 2 must not bump the sim model. Pin current public contract size.
    true("THESIS_KEYS is a sequence", hasattr(sim, "THESIS_KEYS"))
    true("SIM_VERSION present", hasattr(sim, "SIM_VERSION"))
    # Life keys already exist; Gate 2 does not add new ones.
    for k in ("life_gained", "life_drained"):
        true(f"{k} already allowlisted", k in sim.THESIS_KEYS)


def test_ramp_violations_tagged_separately():
    print("\nmana rocks tagged ramp; payoff count excludes them")
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa

    strategy = cs.derive(MV_FLOOR_BELLO_SHAPED)
    deck = [
        {"name": "Sol Ring", "text": "{T}: Add {C}{C}.",
         "type": "Artifact", "cmc": 1},
        {"name": "Cheap Statue", "text": "Indestructible",
         "type": "Artifact", "cmc": 2},
    ]
    report = sa.audit(deck, strategy)
    ramp = {v["name"] for v in report["bound_violations_ramp"]}
    payoff = {v["name"] for v in report["bound_violations_payoff"]}
    true("Sol Ring is ramp violation", "Sol Ring" in ramp, str(ramp))
    true("Cheap Statue is payoff violation", "Cheap Statue" in payoff, str(payoff))
    eq("payoff count excludes ramp", report["violation_count_payoff"], 1)
    eq("ramp count", report["violation_count_ramp"], 1)


def main() -> int:
    test_bello_shaped_floor_emits_artifact_and_enchantment()
    test_audit_equipment_and_aura_exempt_from_bello_floor()
    test_single_non_equipment_floor_excludes_equipment()
    test_mv_floor_or_greater_extracts()
    test_mv_floor_geq_phrasing_extracts()
    test_mv_static_ceiling_or_less_extracts()
    test_power_ceiling_extracts()
    test_forbid_draw_extracts()
    test_subtype_tutor_extracts()
    test_experience_capacity_extracts()
    test_audit_power_ceiling_and_subtype()
    test_audit_floor_flags_below_not_above()
    test_lifegain_bound_strategy()
    test_opponent_trigger_strategy()
    test_bird_type_strategy()
    test_token_board_count_strategy()
    test_cast_subtype_strategy()
    test_outputs_without_inputs_is_partial()
    test_audit_skips_feeder_line_when_no_inputs()
    test_board_count_feeders()
    test_cast_subtype_feeders()
    test_unread_commander()
    test_audit_feeders_bounds_unknown()
    test_audit_unknown_capacity_not_violation()
    test_attach_clarity_does_not_change_membership()
    test_unread_attach_honesty()
    test_ramp_violations_tagged_separately()
    test_no_named_commander_branches()
    test_sim_version_untouched()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
