"""
CON-149 — resolve_term() fixtures.

Resolution is a lookup against Scryfall's catalogs and set list, not a guess.
These fixtures were verified live while the grammar was written
(docs/SCRYFALL-GRAMMAR.md). Offline against data/scryfall_grammar.json; the
live gate at the bottom checks every resolved expression returns ≥1 card.
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import sys

import httpx

from deckwright import resolve
from deckwright import app

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
PASSED = FAILED = 0


def ok(label, detail=""):
    global PASSED
    PASSED += 1
    print(f"  {GREEN}ok{OFF}  {label}" + (f"  {DIM}{detail}{OFF}" if detail else ""))


def bad(label, detail=""):
    global FAILED
    FAILED += 1
    print(f"  {RED}FAIL{OFF}  {label}" + (f"  {DIM}{detail}{OFF}" if detail else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def expr(r: dict) -> str | None:
    return resolve.expression(r)


# ---------------------------------------------------------------- fixtures

# (input, expected expression or None for unresolved)
FIXTURES = [
    ("spiderman", "set:spm"),
    ("spider-man", "set:spm"),
    ("Spider-Man", "set:spm"),
    ("lord of the rings", "set:ltr"),
    ("fallout", "set:pip"),
    ("fallouts", "set:pip"),             # CON-292: set rungs try singulars
    ("hobbit", "set:hob"),
    ("hobbits", "set:hob"),              # CON-292: plural of a UB set name
    ("doctor who", "set:who"),
    ("ninja turtles", "set:tmt"),
    ("pirates", "t:pirate"),
    ("lifelink", "keyword:lifelink"),
    ("treasure", "t:treasure"),          # artifact type, not keyword action
    ("heists", None),                    # unresolved — and says so
    # Behaviours (b629459) — checked before keyword catalogs.
    ("steal", "otag:theft"),             # NOT keyword:steal (1 card trap)
    ("ramp", "otag:ramp"),
    ("stax", None),                      # no such tag — and says so
]

# CON-212 — beginner phrases → verified operator, or unresolved (ask again).
# Never invent otag:; never revive dead tags; keywords stay strategy colour.
BEGINNER_FIXTURES = [
    ("steal", "otag:theft"),
    ("take their stuff", "otag:theft"),
    ("take stuff", "otag:theft"),
    ("gain life", "otag:lifegain"),
    ("stay alive", "otag:lifegain"),
    ("keep me alive", "otag:lifegain"),
    ("drain", "otag:lifedrain"),
    ("make them lose life", "otag:lifedrain"),
    ("bounce", "otag:bounce"),
    ("send back", "otag:bounce"),
    ("go fast", "keyword:haste"),
    ("haste", "keyword:haste"),
    ("trample", "keyword:trample"),
    ("first strike", 'keyword:"first strike"'),
    ("go wide", None),                   # no verified tokens otag — ask
    ("many creatures", None),
    ("help them grow", None),            # counters path not a fake otag
    ("grow", None),
    ("aggro", None),
    ("control", None),
    ("stax", None),
    ("make a bunch of stuff", None),
]


def test_normalize():
    print("\nnormalize()")
    eq("strips punctuation", resolve.normalize("Spider-Man"), "spiderman")
    eq("strips spaces", resolve.normalize("lord of the rings"), "lordoftherings")
    eq("lowercase", resolve.normalize("Fallout"), "fallout")


def test_resolve_fixtures():
    print("\nresolve_term()  — CON-149 fixtures")
    resolve.load_grammar()
    for word, want in FIXTURES:
        r = resolve.resolve_term(word)
        got = expr(r)
        if want is None:
            (ok if r.get("confidence") == "unresolved" and got is None else bad)(
                f"{word!r} unresolved", f"got {r}")
        else:
            eq(f"{word!r} -> {want}", got, want)


def test_collision_precedence():
    """The nine colliding terms — hand-written precedence, not a heuristic."""
    print("\nresolve_term()  — collision precedence")
    resolve.load_grammar()
    eq("treasure is artifact type", expr(resolve.resolve_term("treasure")), "t:treasure")
    eq("food is artifact type", expr(resolve.resolve_term("food")), "t:food")
    eq("phyrexian is creature type", expr(resolve.resolve_term("phyrexian")), "t:phyrexian")
    eq("foretell is keyword", expr(resolve.resolve_term("foretell")), "keyword:foretell")
    # set-name exact beats card-type / watermark
    for word in ("conspiracy", "transformers"):
        r = resolve.resolve_term(word)
        (ok if r.get("operator") == "set" else bad)(
            f"{word!r} resolves as set", f"got {r}")


def test_pirates_not_a_set():
    print("\nresolve_term()  — pirates is a type, not a set")
    resolve.load_grammar()
    r = resolve.resolve_term("pirates")
    eq("operator", r.get("operator"), "t")
    eq("value", (r.get("value") or "").lower(), "pirate")


def test_theme_clauses_uses_resolution():
    print("\ntheme_clauses()  — builds around resolve_term()")
    resolve.load_grammar()
    clauses = app.theme_clauses("spiderman")
    (ok if clauses and clauses[0] == "set:spm" else bad)(
        "spiderman theme leads with set:spm", str(clauses[:3]))
    pirate = app.theme_clauses("pirates")
    eq("pirates theme leads with t:pirate", pirate[0] if pirate else "", "t:pirate")
    heists = app.theme_clauses("heists")
    (ok if heists == [] else bad)(
        "heists unresolved yields no invented clauses", str(heists))
    nonsense = app.theme_clauses("qqzzxnothing")
    (ok if nonsense == [] else bad)(
        "unknown theme yields no clauses", str(nonsense))
    steal = app.theme_clauses("steal")
    eq("steal is otag:theft, not keyword:steal",
       steal[0] if steal else "", "otag:theft")
    (ok if not any("keyword:steal" in c for c in steal) else bad)(
        "steal ladder has no keyword:steal", str(steal))


def test_compose_pirates_that_steal():
    print("\nresolve_theme()  — pirates that steal composes")
    resolve.load_grammar()
    hit = resolve.resolve_theme("pirates that steal")
    eq("composed expression", hit.get("expression"), "t:pirate otag:theft")
    clauses = app.theme_clauses("pirates that steal")
    eq("theme_clauses leads with composition",
       clauses[0] if clauses else "", "t:pirate otag:theft")


def test_compose_angels_that_gain_life():
    """CON-157: multi-word behaviour ('gain life') composes with the type."""
    print("\nresolve_theme()  — angels that gain life composes")
    resolve.load_grammar()
    hit = resolve.resolve_theme("angels that gain life")
    eq("composed expression", hit.get("expression"), "t:angel otag:lifegain")
    clauses = app.theme_clauses("angels that gain life")
    eq("theme_clauses leads with composition",
       clauses[0] if clauses else "", "t:angel otag:lifegain")
    eq("type half follows",
       clauses[1] if len(clauses) > 1 else "", "t:angel")
    eq("behaviour half follows",
       clauses[2] if len(clauses) > 2 else "", "otag:lifegain")
    # Bare type invents no behaviour.
    bare = resolve.resolve_theme("pirates")
    eq("pirates alone is type only", bare.get("expression"), "t:pirate")
    (ok if "otag:" not in (bare.get("expression") or "") else bad)(
        "pirates alone invents no behaviour", str(bare))


def test_compose_sibling_types_or():
    """CON-209: sibling tribes OR — horses and knights is a real pool, not 0."""
    print("\nresolve_theme()  — sibling types OR together")
    resolve.load_grammar()

    horses = resolve.resolve_theme("horses and knights")
    eq("horses and knights ORs",
       horses.get("expression"), "(t:horse OR t:knight)")
    eq("both types in parts",
       sorted(str(p.get("value") or "").lower()
              for p in (horses.get("parts") or [])),
       ["horse", "knight"])

    slash = resolve.resolve_theme("horse/knight")
    eq("slash form ORs",
       slash.get("expression"), "(t:horse OR t:knight)")

    dogs = resolve.resolve_theme("dogs and bears")
    eq("dogs and bears ORs",
       dogs.get("expression"), "(t:dog OR t:bear)")

    # Plurals still morph to catalog singulars.
    clerics = resolve.resolve_theme("clerics")
    eq("clerics → t:cleric", clerics.get("expression"), "t:cleric")
    angels = resolve.resolve_theme("angels")
    eq("angels → t:angel", angels.get("expression"), "t:angel")

    # Keyword menu must not AND into the type pool.
    kw = resolve.resolve_theme(
        "knights with trample first strike haste")
    eq("knight + keyword menu stays type",
       kw.get("expression"), "t:knight")

    # Thing + behaviour still ANDs (composition payoff unchanged).
    steal = resolve.resolve_theme("horses and knights that steal")
    eq("dual tribe + behaviour",
       steal.get("expression"), "(t:horse OR t:knight) otag:theft")

    clauses = app.theme_clauses("horses and knights")
    eq("theme_clauses leads with OR",
       clauses[0] if clauses else "", "(t:horse OR t:knight)")

    # CON-308: echoed type terms must not duplicate OR arms.
    echoed = resolve.resolve_theme("pirates pirates and treasure")
    eq("duplicate pirate collapsed",
       echoed.get("expression"), "(t:pirate OR t:treasure)")
    discard = resolve.resolve_theme("pirate pirate treasure discard")
    eq("duplicate pirate collapsed with behaviour",
       discard.get("expression"),
       "(t:pirate OR t:treasure) otag:discard")


def test_cavalry_horse_alias():
    """CON-234: cavalry maps to t:horse so cavalry knights ORs both tribes."""
    print("\nresolve_theme()  — CON-234 cavalry → horse alias")
    resolve.load_grammar()

    eq("cavalry is t:horse", expr(resolve.resolve_term("cavalry")), "t:horse")
    eq("cavalry catalog is creature-types",
       resolve.resolve_term("cavalry").get("catalog"), "creature-types")

    cav = resolve.resolve_theme("cavalry knights")
    eq("cavalry knights ORs",
       cav.get("expression"), "(t:horse OR t:knight)")
    eq("both types in parts",
       sorted(str(p.get("value") or "").lower()
              for p in (cav.get("parts") or [])),
       ["horse", "knight"])

    horses = resolve.resolve_theme("horses and knights")
    eq("horses and knights still ORs",
       horses.get("expression"), "(t:horse OR t:knight)")

    # Honesty: word-bank weak hits dropped for a type pool surface in unresolved.
    ships = resolve.resolve_theme("knights and ships")
    eq("knights and ships type pool", ships.get("expression"), "t:knight")
    (ok if "ships" in (ships.get("unresolved") or []) else bad)(
        "dropped weak word in unresolved", str(ships.get("unresolved")))


def test_role_query_spiderman():
    print("\nrole_query()  — spiderman uses set:spm, not t:spiderman")
    resolve.load_grammar()
    brief = {"theme": "spiderman", "bracket": 2, "budget_total": 150}
    ladder = app.role_query("theme", "WB", brief, 5.0)
    (ok if any("set:spm" in q for q in ladder) else bad)(
        "theme ladder contains set:spm", str(ladder[:4]))
    (ok if not any("t:spiderman" in q for q in ladder) else bad)(
        "theme ladder has no t:spiderman", str(ladder[:4]))


def test_role_query_unresolved_no_silent_default():
    print("\nrole_query()  — unresolved theme does not silently become otag:draw")
    resolve.load_grammar()
    brief = {"theme": "heists", "bracket": 2, "budget_total": 150}
    ladder = app.role_query("theme", "UB", brief, 5.0)
    (ok if ladder == [] or not any("otag:draw" in q for q in ladder) else bad)(
        "no silent otag:draw for unresolved theme", str(ladder[:3]))
    r = resolve.resolve_term("heists")
    (ok if r.get("confidence") == "unresolved" else bad)(
        "resolve_term says unresolved", str(r))
    stax = resolve.resolve_term("stax")
    (ok if stax.get("confidence") == "unresolved" else bad)(
        "stax unresolved", str(stax))


def test_steal_not_keyword_trap():
    print("\nresolve_term()  — steal is behaviour, not keyword:steal")
    resolve.load_grammar()
    r = resolve.resolve_term("steal")
    eq("operator", r.get("operator"), "otag")
    eq("value", r.get("value"), "theft")
    (ok if expr(r) != "keyword:steal" else bad)("not the one-card trap", str(r))


def test_beginner_alias_pack():
    """CON-212: feelings → verified behaviours/keywords, or honest unresolved."""
    print("\nresolve_term()  — CON-212 beginner alias pack (≥20 phrases)")
    resolve.load_grammar(force=True)
    (ok if len(BEGINNER_FIXTURES) >= 20 else bad)(
        f"fixture has ≥20 phrases", f"got {len(BEGINNER_FIXTURES)}")
    for word, want in BEGINNER_FIXTURES:
        r = resolve.resolve_term(word)
        got = expr(r)
        if want is None:
            (ok if r.get("confidence") == "unresolved" and got is None else bad)(
                f"{word!r} unresolved (ask again)", f"got {r}")
        else:
            eq(f"{word!r} -> {want}", got, want)
    # Dead tags stay dead — never revived as invented otags.
    for dead in ("stax", "aggro", "control", "token-maker", "tokenmaker"):
        r = resolve.resolve_term(dead)
        (ok if r.get("confidence") == "unresolved" else bad)(
            f"dead {dead!r} stays unresolved", f"got {r}")
        (ok if (r.get("operator") or "") != "otag" else bad)(
            f"dead {dead!r} is not a fake otag", f"got {r}")


def test_strategy_keywords_stay_off_theme_and():
    """CON-212: horses + haste/trample are strategy, not AND'd onto the tribe."""
    print("\nresolve_theme()  — strategy keywords separate from tribe pool")
    resolve.load_grammar(force=True)
    hit = resolve.resolve_theme("horses haste trample")
    eq("theme expression is tribe only", hit.get("expression"), "t:horse")
    kws = {str(k.get("value") or "").lower() for k in (hit.get("keywords") or [])}
    (ok if "haste" in kws and "trample" in kws else bad)(
        "keywords collected separately", f"got {hit.get('keywords')}")
    (ok if "keyword:" not in (hit.get("expression") or "") else bad)(
        "keywords not AND'd into theme expression", str(hit))
    # Behaviour still composes with the tribe.
    steal = resolve.resolve_theme("pirates that steal")
    eq("thing+behaviour still ANDs", steal.get("expression"), "t:pirate otag:theft")


async def test_live_resolved_expressions():
    """CON-144 gate on term resolution: every resolved expression finds cards."""
    print("\nlive Scryfall  — resolved expressions return ≥1 card")
    resolve.load_grammar()
    resolved = [(w, expr(resolve.resolve_term(w))) for w, want in FIXTURES if want]
    # Composition + collision / design-doc rows.
    composed = resolve.resolve_theme("pirates that steal").get("expression")
    if composed:
        resolved.append(("pirates that steal", composed))
    for w in ("food", "phyrexian", "foretell", "final fantasy", "warhammer"):
        e = expr(resolve.resolve_term(w))
        if e:
            resolved.append((w, e))

    async with httpx.AsyncClient(
        headers=app.UA, timeout=25
    ) as client:
        for word, clause in resolved:
            await asyncio.sleep(0.25)
            try:
                r = await client.get(
                    "https://api.scryfall.com/cards/search",
                    params={"q": clause},
                )
            except Exception as e:
                bad(f"{word!r} {clause}", f"request failed: {e}")
                continue
            if r.status_code == 404:
                bad(f"{word!r} {clause}", "0 cards")
                continue
            if r.status_code != 200:
                bad(f"{word!r} {clause}", f"HTTP {r.status_code} (ungraded)")
                continue
            n = r.json().get("total_cards", 0)
            floor = 15 if word == "pirates that steal" else 1
            (ok if n >= floor else bad)(f"{word!r} {clause}", f"{n} cards")


def main():
    global PASSED, FAILED
    test_normalize()
    test_resolve_fixtures()
    test_collision_precedence()
    test_pirates_not_a_set()
    test_steal_not_keyword_trap()
    test_beginner_alias_pack()
    test_strategy_keywords_stay_off_theme_and()
    test_theme_clauses_uses_resolution()
    test_compose_pirates_that_steal()
    test_compose_angels_that_gain_life()
    test_compose_sibling_types_or()
    test_cavalry_horse_alias()
    test_role_query_spiderman()
    test_role_query_unresolved_no_silent_default()
    if "--live" in sys.argv:
        asyncio.run(test_live_resolved_expressions())
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
