"""
test_way_preference.py — brief way_preference → way_playstyle.

Offline. No model. No Scryfall.

    python test_way_preference.py
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

from deckwright import app  # noqa: E402
from deckwright import commanders as cmd_kb  # noqa: E402
from deckwright import playstyles  # noqa: E402
from deckwright import resolve  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


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


# Gate 1 golden briefs — stated expected maps (theft is honestly unmapped).
GOLDEN_BRIEFS = {
    "pirates": {
        "theme": "pirates that steal",
        "angle": "steal their stuff",
        "way_preference": "steal their stuff",
        "way_playstyle": None,  # otag:theft — no playstyle id
    },
    "cavalry": {
        "theme": "horses and knights",
        "angle": "go wide with the cavalry",
        "way_preference": "go wide",
        "way_playstyle": "tokens",
    },
    "angels": {
        "theme": "angels that gain life",
        "angle": "gain life and swing",
        "way_preference": "gain life",
        "way_playstyle": "lifegain",
    },
}


TOKENS_CMDR = {
    "name": "Token Boss",
    "type": "Legendary Creature — Human",
    "type_line": "Legendary Creature — Human",
    "text": "At the beginning of your end step, create a 1/1 white Soldier creature token.",
    "oracle_text": (
        "At the beginning of your end step, create a 1/1 white Soldier "
        "creature token."
    ),
    "color_identity": ["W"],
    "edhrec_rank": 100,
}

VOLTRON_CMDR = {
    "name": "Gear Boss",
    "type": "Legendary Creature — Human Knight",
    "type_line": "Legendary Creature — Human Knight",
    "text": (
        "Whenever you cast an Aura or Equipment spell, put a +1/+1 counter "
        "on Gear Boss.\nEquipped creatures you control get +1/+1."
    ),
    "oracle_text": (
        "Whenever you cast an Aura or Equipment spell, put a +1/+1 counter "
        "on Gear Boss.\nEquipped creatures you control get +1/+1."
    ),
    "color_identity": ["W"],
    "edhrec_rank": 50,  # more played than Token Boss
}


def test_alias_map():
    print("\nmap_way_playstyle() — alias / honest unmapped")
    resolve.load_grammar()
    vocab = set(playstyles.load_playstyles())

    eq("steal their stuff unmapped",
       playstyles.map_way_playstyle("steal their stuff"), None)
    eq("theft unmapped", playstyles.map_way_playstyle("theft"), None)
    eq("gain life → lifegain",
       playstyles.map_way_playstyle("gain life"), "lifegain")
    eq("keep me alive → lifegain",
       playstyles.map_way_playstyle("keep me alive"), "lifegain")
    eq("go wide → tokens", playstyles.map_way_playstyle("go wide"), "tokens")
    eq("sacrifice → aristocrats",
       playstyles.map_way_playstyle("sacrifice"), "aristocrats")
    eq("blink → blink", playstyles.map_way_playstyle("blink"), "blink")
    eq("empty → None", playstyles.map_way_playstyle(""), None)
    eq("exact id → tokens", playstyles.map_way_playstyle("tokens"), "tokens")
    eq("garbage → None", playstyles.map_way_playstyle("qqzzxnothing"), None)

    for pid in ("lifegain", "tokens", "aristocrats", "blink"):
        true(f"{pid} is allowlisted", pid in vocab)


def test_apply_way_fields():
    print("\napply_way_fields() — model freestyle overwritten")
    brief = {
        "theme": "angels",
        "way_preference": "gain life",
        "way_playstyle": "combo",  # not in the 13 — must not stick
    }
    out = app.apply_way_fields(brief)
    eq("preference preserved", out["way_preference"], "gain life")
    eq("freestyle dropped for map", out["way_playstyle"], "lifegain")

    unmapped = app.apply_way_fields({
        "way_preference": "steal their stuff",
        "way_playstyle": "tokens",  # model guessed — overwrite to null
    })
    eq("unmapped clears invent", unmapped["way_playstyle"], None)
    eq("said preserved", unmapped["way_preference"], "steal their stuff")


def test_schema_fields():
    print("\n_BRIEF_OBJECT_SCHEMA — way fields")
    props = app._BRIEF_OBJECT_SCHEMA["properties"]
    true("way_preference in schema", "way_preference" in props)
    true("way_playstyle in schema", "way_playstyle" in props)
    req = set(app._BRIEF_OBJECT_SCHEMA["required"])
    true("way_preference required", "way_preference" in req)
    true("way_playstyle required", "way_playstyle" in req)
    enum = props["way_playstyle"]["anyOf"][0]["enum"]
    vocab = set(playstyles.load_playstyles())
    eq("enum is the 13", set(enum), vocab)


def test_soft_prefer_order():
    print("\nprefer_way_playstyle() — soft prefer, no drops")
    # Patch ways so Token Boss seats tokens and Gear Boss seats voltron.
    real_ways = cmd_kb._ways_for_card

    def fake_ways(card):
        if card.get("name") == "Token Boss":
            return "ok", [{"playstyle": "tokens", "method": "derived",
                           "confidence": "high", "why": "tokens"}]
        if card.get("name") == "Gear Boss":
            return "ok", [{"playstyle": "voltron", "method": "derived",
                           "confidence": "high", "why": "voltron"}]
        return real_ways(card)

    cmd_kb._ways_for_card = fake_ways  # type: ignore[assignment]
    try:
        pool = [VOLTRON_CMDR, TOKENS_CMDR]  # voltron first (more played)
        ordered = app.prefer_way_playstyle(pool, "tokens")
        eq("tokens floats first", ordered[0]["name"], "Token Boss")
        eq("voltron still present", ordered[1]["name"], "Gear Boss")
        eq("length unchanged", len(ordered), 2)

        none_pref = app.prefer_way_playstyle(pool, None)
        eq("null preference keeps order", none_pref[0]["name"], "Gear Boss")
    finally:
        cmd_kb._ways_for_card = real_ways  # type: ignore[assignment]


def test_relational_copy_quotes_preference():
    print("\nrelational_copy() — quotes way_preference")
    real_ways = cmd_kb._ways_for_card

    def fake_ways(card):
        return "ok", [{"playstyle": "lifegain", "method": "derived",
                       "confidence": "high", "why": "lifegain"}]

    cmd_kb._ways_for_card = fake_ways  # type: ignore[assignment]
    try:
        brief = {
            "theme": "angels",
            "angle": "something else entirely",
            "way_preference": "gain life",
            "way_playstyle": "lifegain",
            "mechanics": ["lifegain"],
        }
        copy = cmd_kb.relational_copy(TOKENS_CMDR, brief)
        true("quotes her words", "gain life" in copy.lower(), copy)
    finally:
        cmd_kb._ways_for_card = real_ways  # type: ignore[assignment]


def test_readiness_unmapped_warning():
    print("\ncheck_readiness() — unmapped way is warning, not crash")
    import asyncio

    brief = {
        "theme": "pirates",
        "angle": "combat",
        "colors": "UB",
        "mechanics": ["combat"],
        "budget_total": 150,
        "bracket": 2,
        "commander_query": "is:commander t:pirate",
        "flavor_terms": [],
        "fantasy": "pirates",
        "playstyle": "proactive",
        "strategy_shape": "aggro",
        "commander_name": "",
        "table": "kitchen",
        "way_preference": "steal their stuff",
        "way_playstyle": None,
    }
    # Avoid live Scryfall: empty theme path still runs strategy checks.
    # Use a theme that may hit network — stub build_readback if needed.
    # Safer: no theme_expr path — use commander_name only? Strategy present.
    # Theme "pirates" will try pool. Patch build_readback.
    async def fake_rb(theme, colors=""):
        return {"composed": {"count": 50, "query": "t:pirate", "status": "ok"}}

    real_rb = app.build_readback
    app.build_readback = fake_rb  # type: ignore[assignment]
    try:
        readiness = asyncio.run(app.check_readiness(brief))
    finally:
        app.build_readback = real_rb  # type: ignore[assignment]

    warns = readiness.get("warnings") or []
    way_warns = [w for w in warns if w.get("signal") == "way_preference"]
    true("unmapped way warning present", bool(way_warns), str(warns))
    if way_warns:
        eq("said in detail", way_warns[0]["detail"].get("said"),
           "steal their stuff")
    true("not a blocker by default",
         not any(b.get("signal") == "way_preference"
                 for b in (readiness.get("blockers") or [])))


def test_golden_briefs():
    print("\nGate 1 golden briefs — stated maps")
    resolve.load_grammar()
    for name, g in GOLDEN_BRIEFS.items():
        mapped = playstyles.map_way_playstyle(g["way_preference"])
        eq(f"{name} map", mapped, g["way_playstyle"])
        applied = app.apply_way_fields({
            "way_preference": g["way_preference"],
            "way_playstyle": "artifacts",  # noise
        })
        eq(f"{name} apply", applied["way_playstyle"], g["way_playstyle"])


def main():
    test_alias_map()
    test_apply_way_fields()
    test_schema_fields()
    test_soft_prefer_order()
    test_relational_copy_quotes_preference()
    test_readiness_unmapped_warning()
    test_golden_briefs()
    print()
    if failed:
        print(f"{RED}FAILED{OFF} {failed}  {GREEN}passed{OFF} {passed}")
        return 1
    print(f"{GREEN}ALL OK{OFF}  {passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
