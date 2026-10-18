"""
test_readback.py — CON-150 readback before commander selection.

    python test_readback.py           # unit + live Scryfall counts
    python test_readback.py --unit    # offline only

Counts come from live Scryfall. Unresolved terms never surface as 0.
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


def test_theme_segments():
    print("\ntheme_segments()  — her words stay intact")
    eq("pirates that steal",
       app.theme_segments("pirates that steal"),
       ["pirates", "that steal"])
    eq("comma keeps unresolved word",
       app.theme_segments("pirates that steal, heists"),
       ["pirates", "that steal", "heists"])
    eq("single word", app.theme_segments("heists"), ["heists"])
    eq("empty", app.theme_segments(""), [])
    eq("whitespace", app.theme_segments("  "), [])


def test_compose_pool_query_unit():
    """CON-209: composed pool ORs sibling types; keywords do not AND in."""
    print("\ncompose_pool_query()  — translate her words into a real pool")
    eq("dual tribe ORs",
       app.compose_pool_query([
           {"query": "t:horse", "catalog": "creature-types"},
           {"query": "t:knight", "catalog": "creature-types"},
       ]),
       "(t:horse OR t:knight)")
    eq("thing + behaviour ANDs",
       app.compose_pool_query([
           {"query": "t:pirate", "catalog": "creature-types"},
           {"query": "otag:theft", "catalog": "behaviour"},
       ]),
       "t:pirate otag:theft")
    eq("dual tribe + behaviour",
       app.compose_pool_query([
           {"query": "t:horse", "catalog": "creature-types"},
           {"query": "t:knight", "catalog": "creature-types"},
           {"query": "otag:theft", "catalog": "behaviour"},
       ]),
       "(t:horse OR t:knight) otag:theft")
    eq("keyword menu does not AND onto type",
       app.compose_pool_query([
           {"query": "t:knight", "catalog": "creature-types"},
           {"query": "keyword:trample", "catalog": "keyword"},
           {"query": 'keyword:"first strike"', "catalog": "keyword"},
           {"query": "keyword:haste", "catalog": "keyword"},
       ]),
       "t:knight")
    eq("empty", app.compose_pool_query([]), None)


async def test_place_theme_word_unit():
    """Offline placement shape — operators only, no card names."""
    print("\nplace_theme_word()  — operators, never card names")
    await app.load_vocab()

    pirate = app.place_theme_word("pirates")
    eq("pirates operator", pirate and pirate.get("operator"), "t:pirate")
    eq("pirates resolved", pirate and pirate.get("resolved"), True)

    steal = app.place_theme_word("steal")
    eq("steal is otag:theft", steal and steal.get("operator"), "otag:theft")
    eq("steal resolved", steal and steal.get("resolved"), True)

    # CON-149/150 fixture: heists is unresolved — never keyword:heist, never 0.
    heists = app.place_theme_word("heists")
    eq("heists unresolved", heists and heists.get("resolved"), False)
    eq("heists has no operator", heists and heists.get("operator"), None)
    eq("heists count is None not 0", heists and heists.get("count"), None)

    stax = app.place_theme_word("stax")
    eq("stax unresolved", stax and stax.get("resolved"), False)

    ramp = app.place_theme_word("ramp")
    eq("ramp is otag:ramp", ramp and ramp.get("operator"), "otag:ramp")

    # Her words with a connector stay for display; lookup strips it.
    that_steal = app.place_theme_word("that steal")
    eq("that steal operator", that_steal and that_steal.get("operator"), "otag:theft")
    eq("that steal said preserved", that_steal and that_steal.get("said"), "that steal")

    # CON-170: multi-word spans compose the same way resolve_theme does.
    composed = app.place_theme_word("vampire lifegain")
    eq("vampire lifegain resolved", composed.get("resolved"), True)
    eq("vampire lifegain query", composed.get("query"), "t:vampire otag:lifegain")

    drain = app.place_theme_word("drain the table")
    eq("drain the table resolved", drain.get("resolved"), True)
    eq("drain the table is lifedrain", drain.get("query"), "otag:lifedrain")

    gain_me = app.place_theme_word("gain me life")
    eq("gain me life resolved", gain_me.get("resolved"), True)
    eq("gain me life is lifegain", gain_me.get("query"), "otag:lifegain")

    # Dead behaviour inside a mixed span stays unresolved (stax is load-bearing).
    stax_pirates = app.place_theme_word("stax pirates")
    eq("stax pirates unresolved", stax_pirates.get("resolved"), False)

    # CON-212: "keep me alive" now resolves, and that is the alias pack working
    # rather than a regression. This assertion was written when the only way a
    # prose span could resolve was a word-bank o: guess — a fallback wearing
    # success. It now returns otag:lifegain at confidence "exact" from the
    # verified otag catalog, which is a real behaviour match, so the old
    # expectation encoded a limitation instead of the rule. Same shape as the
    # CON-178 correction: the code got better and the test had to say so.
    keep = app.place_theme_word("keep me alive")
    eq("keep me alive resolves via alias", keep.get("resolved"), True)
    eq("keep me alive is lifegain", keep.get("query"), "otag:lifegain")

    # The invariant the assertion above was really protecting, kept intact:
    # a span with no alias and no catalog hit must still refuse rather than
    # fall back to an o: guess. Unknown is not a match.
    nonsense = app.place_theme_word("wobbly grandiloquent nonsense")
    eq("unaliased prose still unresolved", nonsense.get("resolved"), False)


async def test_build_readback_live():
    print("\nbuild_readback()  — live Scryfall counts")
    await app.load_vocab()

    rb = await app.build_readback("pirates that steal", "UB")
    terms = rb.get("terms") or []
    eq("two resolved + no silent drop", len(terms), 2)
    by_said = {t["said"]: t for t in terms}

    p = by_said.get("pirates") or {}
    eq("pirates op", p.get("operator"), "t:pirate")
    (ok if isinstance(p.get("count"), int) and p["count"] >= 100 else bad)(
        "pirates count is live", str(p.get("count")))

    s = by_said.get("that steal") or {}
    eq("steal op", s.get("operator"), "otag:theft")
    (ok if isinstance(s.get("count"), int) and s["count"] >= 100 else bad)(
        "theft count is live", str(s.get("count")))

    composed = rb.get("composed") or {}
    (ok if isinstance(composed.get("count"), int) and composed["count"] >= 1 else bad)(
        "composed count is live", str(composed.get("count")))
    (ok if "pirates" in (composed.get("said") or "") and "steal" in (composed.get("said") or "")
     else bad)("composed keeps her words", composed.get("said"))
    (ok if "blue" in (composed.get("said") or "") and "black" in (composed.get("said") or "")
     else bad)("composed names colours", composed.get("said"))

    # Unresolved term — never 0, never dropped. Comma keeps her word as its own row.
    rb2 = await app.build_readback("pirates that steal, heists", "")
    terms2 = rb2.get("terms") or []
    eq("three rows including unresolved", len(terms2), 3)
    heists = next((t for t in terms2 if t["said"] == "heists"), None)
    (ok if heists is not None else bad)("heists appears in readback", str(terms2))
    if heists:
        eq("heists unresolved in readback", heists.get("resolved"), False)
        eq("heists note", heists.get("note"), "no match in Scryfall")
        eq("heists count is not 0", heists.get("count"), None)

    # Thin callout when composed pool is under ~10 in her colours.
    # UB pirates+theft is 4 live — must be thin before she picks.
    (ok if rb.get("thin") is True else bad)(
        "UB pirates+theft called thin",
        f"count={composed.get('count')} thin={rb.get('thin')}")

    # Healthy without colour filter — not thin.
    rb3 = await app.build_readback("pirates that steal", "")
    c3 = (rb3.get("composed") or {}).get("count")
    (ok if rb3.get("thin") is False and isinstance(c3, int) and c3 >= 10 else bad)(
        "uncoloured compose is healthy", f"count={c3} thin={rb3.get('thin')}")

    # Never invents a card name — payload is operators and counts only.
    blob = str(rb) + str(rb2)
    for name in ("Breeches", "Admiral Beckett Brass", "Sol Ring"):
        (ok if name not in blob else bad)(f"no invented name {name}", "")


async def test_sibling_types_live():
    """CON-209: horses and knights is a real pool — never AND to 0."""
    print("\nbuild_readback()  — sibling tribes OR (live)")
    await app.load_vocab()

    rb = await app.build_readback("horses and knights", "")
    composed = rb.get("composed") or {}
    eq("composed query ORs",
       composed.get("query"), "(t:horse OR t:knight)")
    n = composed.get("count")
    (ok if isinstance(n, int) and n >= 10 else bad)(
        "horses and knights pool is real", f"count={n}")
    eq("not thin", rb.get("thin"), False)

    # Keyword menu on a real tribe must not zero the composed pool.
    rb2 = await app.build_readback(
        "knights with trample, first strike, and haste", "")
    c2 = rb2.get("composed") or {}
    eq("keyword menu stays on type", c2.get("query"), "t:knight")
    n2 = c2.get("count")
    (ok if isinstance(n2, int) and n2 >= 10 else bad)(
        "knight + keywords pool is real", f"count={n2}")


async def main():
    test_theme_segments()
    test_compose_pool_query_unit()
    await test_place_theme_word_unit()
    if UNIT_ONLY:
        print(f"\n{DIM}--unit: skipping live Scryfall counts{OFF}")
    else:
        await test_build_readback_live()
        await test_sibling_types_live()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
