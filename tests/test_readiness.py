"""
test_readiness.py — readiness gate before readback/commanders.

Confidence is countable facts, never a model percentage.
An unknown is not a zero; a blocked brief does not reach commanders.

    python test_readiness.py           # unit + live Scryfall pool depth
    python test_readiness.py --unit    # offline only (skips live counts)
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import sys

from deckwright import app

UNIT_ONLY = "--unit" in sys.argv
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


def has_no_pct(payload: dict):
    blob = str(payload).lower()
    for needle in ("%", "percent", "confidence:", "85%", "0.85"):
        if needle in blob:
            return False
    return True


COMPLETE = {
    "theme": "pirates that steal",
    "angle": "steal their stuff and sail away",
    "mechanics": ["theft", "combat"],
    "budget_total": 150,
    "bracket": 2,
    "commander_query": "is:commander t:pirate",
}


async def test_unresolved_term_blocks():
    print("\ncheck_readiness()  — unresolved load-bearing term blocks")
    await app.load_vocab()
    brief = {
        **COMPLETE,
        "theme": "heists",
    }
    r = await app.check_readiness(brief)
    eq("not ready", r.get("ready"), False)
    signals = {b["signal"] for b in (r.get("blockers") or [])}
    (ok if "terms" in signals else bad)("terms blocker present", str(r.get("blockers")))
    q = (r.get("question") or "").lower()
    (ok if "heist" in q else bad)("question names the missing term", r.get("question"))
    (ok if has_no_pct(r) else bad)("no confidence percentage", str(r))


async def test_complete_brief_proceeds():
    print("\ncheck_readiness()  — complete brief adds no friction")
    await app.load_vocab()
    # No colour filter — composed pirates+theft is healthy without id<=.
    r = await app.check_readiness(COMPLETE)
    if UNIT_ONLY:
        # Offline: terms + strategy still decide; pool depth skipped without live.
        eq("ready offline shape", r.get("ready") in (True, False), True)
        eq("no terms blocker", "terms" in {b["signal"] for b in r.get("blockers") or []}, False)
        eq("no strategy blocker",
           "strategy" in {b["signal"] for b in r.get("blockers") or []}, False)
    else:
        eq("ready", r.get("ready"), True)
        eq("no blockers", r.get("blockers") or [], [])
        eq("no question", r.get("question"), None)
    (ok if has_no_pct(r) else bad)("no confidence percentage", str(r))


async def test_missing_strategy_blocks():
    print("\ncheck_readiness()  — strategy/angle missing blocks")
    await app.load_vocab()
    brief = {**COMPLETE, "angle": "", "strategy": "", "plan": ""}
    r = await app.check_readiness(brief)
    eq("not ready", r.get("ready"), False)
    signals = {b["signal"] for b in (r.get("blockers") or [])}
    (ok if "strategy" in signals else bad)("strategy blocker", str(r.get("blockers")))
    q = (r.get("question") or "").lower()
    (ok if q and ("turn" in q or "do" in q or "feel" in q or "when" in q)
     else bad)("question asks what the deck does", r.get("question"))


async def test_mechanics_warn_not_block():
    print("\ncheck_readiness()  — missing mechanics warns, does not block")
    await app.load_vocab()
    brief = {**COMPLETE, "mechanics": []}
    r = await app.check_readiness(brief)
    warns = {w["signal"] for w in (r.get("warnings") or [])}
    (ok if "mechanics" in warns else bad)("mechanics warning", str(r.get("warnings")))
    if not UNIT_ONLY:
        eq("still ready without mechanics", r.get("ready"), True)


async def test_budget_warn_not_block():
    print("\ncheck_readiness()  — missing budget/bracket warns, does not block")
    await app.load_vocab()
    brief = {k: v for k, v in COMPLETE.items() if k not in ("budget_total", "bracket")}
    r = await app.check_readiness(brief)
    warns = {w["signal"] for w in (r.get("warnings") or [])}
    (ok if "budget" in warns or "bracket" in warns else bad)(
        "budget/bracket warning", str(r.get("warnings")))
    if not UNIT_ONLY:
        eq("still ready with defaults", r.get("ready"), True)


async def test_colors_do_not_block():
    """The interrogator reply-length work / INTERROGATOR: colours come from the commander. Missing colours
    must not force a question — that would fight the sacred prompt."""
    print("\ncheck_readiness()  — missing colours do not block")
    await app.load_vocab()
    brief = {**COMPLETE}
    brief.pop("colors", None)
    r = await app.check_readiness(brief)
    signals = {b["signal"] for b in (r.get("blockers") or [])}
    eq("no colours blocker", "colors" in signals or "colours" in signals, False)
    if not UNIT_ONLY:
        eq("ready without colours", r.get("ready"), True)


async def test_question_is_specific_not_generic():
    print("\ncheck_readiness()  — question names the specific missing thing")
    await app.load_vocab()
    r = await app.check_readiness({**COMPLETE, "theme": "stax pirates"})
    q = r.get("question") or ""
    (ok if "stax" in q.lower() else bad)("names stax, not a generic re-prompt", q)
    (ok if "what else" not in q.lower() and "tell me more" not in q.lower()
     else bad)("not a vague re-prompt", q)


async def test_stuck_degrades_honestly():
    print("\ncheck_readiness()  — same blocker twice degrades honestly")
    await app.load_vocab()
    brief = {**COMPLETE, "theme": "heists"}
    first = await app.check_readiness(brief)
    eq("first pass blocked", first.get("ready"), False)
    asked = first.get("asked_about")
    (ok if asked else bad)("asked_about set", str(first))

    second = await app.check_readiness(brief, previously_asked=asked)
    eq("still blocked", second.get("ready"), False)
    eq("stuck flagged", second.get("stuck"), True)
    q = (second.get("question") or "").lower()
    (ok if ("could not" in q or "couldn't" in q or "still" in q)
     else bad)("honest degrade copy", second.get("question"))
    (ok if "heist" in q else bad)("still names the term", second.get("question"))
    (ok if "different" in q or "instead" in q or "widen" in q or "move on" in q
     else bad)("offers widen or move on", second.get("question"))


async def test_thin_pool_discloses_live():
    print("\ncheck_readiness()  — composed pool < 10 discloses, does not refuse (live)")
    if UNIT_ONLY:
        print(f"{DIM}  skip (needs live Scryfall){OFF}")
        return
    await app.load_vocab()
    # UB pirates+theft is thin (readback fixture: under 10).
    brief = {**COMPLETE, "colors": "UB"}
    r = await app.check_readiness(brief)
    eq("ready when thin but non-zero", r.get("ready"), True)
    signals = {b["signal"] for b in (r.get("blockers") or [])}
    eq("no pool blocker", "pool" in signals, False)
    warns = [w for w in (r.get("warnings") or []) if w.get("signal") == "pool"]
    (ok if warns else bad)("pool warning present", str(r.get("warnings")))
    if warns:
        disc = ((warns[0].get("detail") or {}).get("disclosure") or "").lower()
        (ok if "only" in disc or "serv" in disc or "starting" in disc
         else bad)("disclosure names the thin pool", disc)
    # Countable — never invent a percentage.
    (ok if has_no_pct(r) else bad)("no confidence percentage", str(r))


async def test_thin_pool_discloses_unit():
    """5 on-theme cards → warn + disclose, not block."""
    print("\ncheck_readiness()  — thin non-zero is disclosure (unit)")
    await app.load_vocab()

    async def fake_readback(theme, colors):
        return {
            "composed": {"count": 5, "query": 't:pirate o:"gain control"'},
            "parts": [],
        }

    real = app.build_readback
    app.build_readback = fake_readback
    try:
        brief = {**COMPLETE, "colors": "UB", "theme": "pirates"}
        r = await app.check_readiness(brief)
        eq("ready", r.get("ready"), True)
        signals = {b["signal"] for b in (r.get("blockers") or [])}
        eq("no pool blocker", "pool" in signals, False)
        warns = [w for w in (r.get("warnings") or []) if w.get("signal") == "pool"]
        eq("one pool warning", len(warns), 1)
        eq("count is 5", (warns[0].get("detail") or {}).get("count"), 5)
        disc = (warns[0].get("detail") or {}).get("disclosure") or ""
        true("disclosure present", bool(disc), disc)
    finally:
        app.build_readback = real


async def test_zero_pool_still_blocks_unit():
    """Zero composed cards still blocks — cannot proceed on theme."""
    print("\ncheck_readiness()  — zero pool still blocks (unit)")
    await app.load_vocab()

    async def fake_readback(theme, colors):
        return {"composed": {"count": 0, "query": "t:heist"}, "parts": []}

    real = app.build_readback
    app.build_readback = fake_readback
    try:
        brief = {**COMPLETE, "theme": "pirates"}
        r = await app.check_readiness(brief)
        # Theme "pirates" resolves; composed forced to 0.
        signals = {b["signal"] for b in (r.get("blockers") or [])}
        (ok if "pool" in signals else bad)("pool blocks on zero", str(r.get("blockers")))
        eq("not ready", r.get("ready"), False)
    finally:
        app.build_readback = real


async def test_unreachable_pool_is_not_a_zero():
    """House rule: unknown ≠ zero. A failed count must not manufacture thin."""
    print("\ncheck_readiness()  — unreachable count is unknown, not thin")
    await app.load_vocab()

    real = app.count_matching

    async def boom(_q):
        return None

    app.count_matching = boom
    try:
        brief = {**COMPLETE, "colors": "UB"}
        r = await app.check_readiness(brief)
        signals = {b["signal"] for b in (r.get("blockers") or [])}
        eq("pool does not block on unknown", "pool" in signals, False)
        # Terms and strategy still clear for this brief.
        eq("terms clear", "terms" in signals, False)
        eq("strategy clear", "strategy" in signals, False)
        warns = {w["signal"] for w in (r.get("warnings") or [])}
        (ok if "pool" in warns or r.get("pool_unknown") else bad)(
            "unknown pool is visible", str(r))
    finally:
        app.count_matching = real


async def test_commander_named_without_theme():
    """She named a commander — no theme terms to resolve. Gate must not invent work."""
    print("\ncheck_readiness()  — named commander, empty theme proceeds")
    await app.load_vocab()
    brief = {
        "theme": "",
        "angle": "I want Mister Negative to lead",
        "commander_name": "Mister Negative",
        "commander_query": 'is:commander !"Mister Negative"',
        "mechanics": ["theft"],
        "budget_total": 150,
        "bracket": 2,
    }
    r = await app.check_readiness(brief)
    eq("ready", r.get("ready"), True)
    eq("no question", r.get("question"), None)


async def test_composed_and_behaviour_phrases_ready():
    """model theme phrasing must not non-deterministically block on terms.

    resolve_theme places these; readiness used to demand each display segment
    resolve as a single catalog term, so 'vampire lifegain' and 'drain the
    table' blocked while 'vampires that drain' passed — same wish, different
    brief wording.
    """
    print("\ncheck_readiness()  — composed / behaviour phrases proceed")
    await app.load_vocab()
    cases = [
        "vampire lifegain",
        "angels lifegain",
        "vampires that drain the table",
        "vampires that gain me life and drain the table",
        "angels that gain life and keep me alive",
        "vampires that steal permanents",
    ]
    for theme in cases:
        r = await app.check_readiness({**COMPLETE, "theme": theme})
        signals = {b["signal"] for b in (r.get("blockers") or [])}
        eq(f"no terms blocker for {theme!r}", "terms" in signals, False)
        if UNIT_ONLY:
            eq(f"strategy clear for {theme!r}", "strategy" in signals, False)
        else:
            eq(f"ready for {theme!r}", r.get("ready"), True)


async def test_dead_behaviour_still_blocks():
    """stax is a known-dead behaviour — still load-bearing, still blocks."""
    print("\ncheck_readiness()  — dead behaviour still blocks")
    await app.load_vocab()
    r = await app.check_readiness({**COMPLETE, "theme": "stax pirates"})
    eq("not ready", r.get("ready"), False)
    terms = [
        (b.get("detail") or {}).get("term")
        for b in (r.get("blockers") or [])
        if b.get("signal") == "terms"
    ]
    (ok if any(t and "stax" in str(t).lower() for t in terms) else bad)(
        "blocker names stax", str(terms))


async def test_blocker_detail_names_term():
    """Harness (and UI) need the term, not just signal='terms'."""
    print("\ncheck_readiness()  — terms blocker carries the word")
    await app.load_vocab()
    r = await app.check_readiness({**COMPLETE, "theme": "heists"})
    blockers = r.get("blockers") or []
    terms_b = [b for b in blockers if b.get("signal") == "terms"]
    (ok if terms_b else bad)("terms blocker present", str(blockers))
    if terms_b:
        term = (terms_b[0].get("detail") or {}).get("term")
        (ok if term and "heist" in str(term).lower() else bad)(
            "detail.term names heists", str(terms_b[0]))


async def main():
    await test_unresolved_term_blocks()
    await test_missing_strategy_blocks()
    await test_mechanics_warn_not_block()
    await test_budget_warn_not_block()
    await test_colors_do_not_block()
    await test_question_is_specific_not_generic()
    await test_stuck_degrades_honestly()
    await test_unreachable_pool_is_not_a_zero()
    await test_commander_named_without_theme()
    await test_composed_and_behaviour_phrases_ready()
    await test_dead_behaviour_still_blocks()
    await test_blocker_detail_names_term()
    await test_complete_brief_proceeds()
    await test_thin_pool_discloses_unit()
    await test_zero_pool_still_blocks_unit()
    await test_thin_pool_discloses_live()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
