"""
test_con167_empty_brief.py — CON-167 refusal of an unusable brief.

An empty brief must not produce commanders, a 99, or a plan that pretends it
decided something. The 1,044 existing tests all start from a good brief; this
is the class that was missing.

    python test_con167_empty_brief.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys

from deckwright import app
from deckwright import plan_graph
from deckwright import synergy
from fastapi.testclient import TestClient

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


CMDR = {
    "name": "Syr Konrad, the Grim",
    "cost": "{3}{B}{B}",
    "cmc": 5,
    "type": "Legendary Creature — Human Knight",
    "text": "Whenever another creature dies, or a creature card is put into a "
            "graveyard from anywhere other than the battlefield, or a creature "
            "card leaves your graveyard, Syr Konrad deals 1 damage to each opponent.",
}


def test_brief_theme_treats_none_as_absent():
    print("\nbrief_theme()  — None is absent, never the string 'None'")
    eq("missing key", app.brief_theme({}), "")
    eq("explicit None", app.brief_theme({"theme": None}), "")
    eq("empty string", app.brief_theme({"theme": ""}), "")
    eq("whitespace", app.brief_theme({"theme": "  "}), "")
    eq("real theme", app.brief_theme({"theme": "vampires"}), "vampires")
    eq("non-string int", app.brief_theme({"theme": 12}), "")


def test_unusable_brief_helpers():
    print("\nbrief_is_usable()  — themeless briefs are refused")
    ok_empty, reason_empty = app.brief_is_usable({})
    eq("empty dict unusable", ok_empty, False)
    (ok if reason_empty and "theme" in reason_empty.lower() else bad)(
        "reason names theme", reason_empty)

    ok_none, _ = app.brief_is_usable({"theme": None, "angle": "something"})
    eq("theme None unusable", ok_none, False)

    ok_theme, reason_theme = app.brief_is_usable({"theme": "vampires"})
    eq("theme usable", ok_theme, True)
    eq("no reason when usable", reason_theme, "")


def test_queries_never_contain_none_substring():
    """Guard at the query builder: Python None must not become Scryfall syntax."""
    print("\nrole_query / place_theme_word  — no 'None' in queries")
    from deckwright import resolve
    resolve.load_grammar()

    briefs = [
        {},
        {"theme": None, "bracket": 2},
        {"theme": "", "bracket": 2},
        {"theme": "vampires", "bracket": 2},
    ]
    roles = ("theme", "deepcut", "ramp", "draw", "spot", "wipe", "lands", "plan")
    for brief in briefs:
        for role in roles:
            try:
                ladder = app.role_query(role, "UB", brief, 5.0)
            except Exception as e:
                bad(f"role_query({role}) raised", f"{type(e).__name__}: {e}")
                continue
            for q in ladder:
                if "None" in q:
                    bad(f"{role} query contains None", q)
                    break
            else:
                ok(f"{role} clean for theme={brief.get('theme')!r}",
                   f"{len(ladder)} rungs")

        ladder, _ = app.commander_ladder(brief)
        for q in ladder:
            if "None" in q:
                bad("commander_ladder contains None", q)
                break
        else:
            ok(f"commander_ladder clean for theme={brief.get('theme')!r}",
               f"{len(ladder)} rungs")

    placed = app.place_theme_word(None)  # type: ignore[arg-type]
    eq("place_theme_word(None) unresolved", placed.get("resolved"), False)
    eq("place_theme_word(None) no operator", placed.get("operator"), None)
    blob = str(placed.get("query"))
    (ok if "None" not in blob or placed.get("query") is None else bad)(
        "place_theme_word query is not the string None", blob)


def test_decided_requires_label():
    print("\nplan_graph  — decided:true requires a label (CON-167)")
    empty = plan_graph.derive({}, CMDR)
    arch = empty["archetype"]
    eq("empty brief decided is false", arch.get("decided"), False)
    eq("empty brief has no label", arch.get("label"), None)

    # CON-178 changed what "no basis" means, and this assertion had to follow.
    # It used to read "empty brief has no commitments" while passing a real
    # CMDR. That encoded the old rule -- commitments came only from the brief --
    # so once commitments derive from the commander's demand profile it failed,
    # and it looked like a regression. It is not: a commander IS evidence.
    # Deriving "sacrifice fodder" from a commander that eats creatures is the
    # opposite of fabrication.
    #
    # What CON-167 actually forbids is a commitment with NO basis in either
    # input, so that is what gets asserted now.
    nothing = plan_graph.derive({}, None)
    (ok if not (nothing.get("commitments") or []) else bad)(
        "no brief AND no commander yields no commitments",
        str(nothing.get("commitments")))

    # With a commander, every commitment must trace back to a family that
    # commander's own profile implies. Anything else is invented.
    #
    # Compare through _COMMITMENT_TEMPLATES rather than by string shape: a
    # commitment id and its family are named differently (dies_triggers ->
    # sacrifice_density), so a prefix check cannot tell a naming mismatch from
    # a real fabrication. It has to be the actual mapping.
    basis = set(plan_graph._basis_families({}, CMDR))
    allowed = {tpl[0] for fam, tpl in plan_graph._COMMITMENT_TEMPLATES.items()
               if fam in basis}

    # Second-order needs are legitimate and must be allowed BY NAME, not by
    # loosening the rule. A commander that eats creatures needs creatures to
    # eat, so a sacrifice outlet implies fodder (_needs_sac_fodder) even though
    # "tokens" is not in its basis families. That is derivation, not invention.
    #
    # This assertion has now been wrong twice -- first "an empty brief emits
    # nothing" (ignored that a commander is evidence), then "every commitment's
    # family is in basis" (ignored derived needs). Each rewrite made the rule
    # sharper, and the rule is: every commitment traces to the commander by a
    # path we can NAME. An unnamed path is the fabrication.
    demand = synergy.demand_profile(CMDR)
    if plan_graph._needs_sac_fodder(demand):
        allowed.add(plan_graph._COMMITMENT_TEMPLATES["tokens"][0])

    for c in empty.get("commitments") or []:
        cid = c.get("id")
        (ok if cid in allowed else bad)(
            f"commitment {cid!r} traces to the commander",
            f"basis={sorted(basis)} allows={sorted(allowed)}")

    # Structural: no plan may claim decided without a label.
    for brief in ({}, {"theme": None}, {"theme": "dragons"},
                  {"theme": "pirates", "strategy": "steal their stuff",
                   "mechanics": ["theft", "combat"]},
                  {"theme": "lifegain", "strategy": "a lifegain deck",
                   "mechanics": ["lifegain"]}):
        plan = plan_graph.derive(brief, CMDR)
        a = plan["archetype"]
        if a.get("decided") and not a.get("label"):
            bad("decided without label", str(a))
        else:
            ok(f"honest decided for theme={brief.get('theme')!r}",
               f"decided={a.get('decided')} label={a.get('label')!r}")

    # Good brief that picks a line still decides.
    vito = {
        "name": "Vito, Thorn of the Dusk Rose",
        "cost": "{3}{B}", "cmc": 4,
        "type": "Legendary Creature — Vampire Cleric",
        "text": "Whenever you gain life, target opponent loses that much life.",
    }
    burst = plan_graph.derive(
        {"theme": "lifegain", "strategy": "win out of nowhere with one big combo turn",
         "mechanics": ["lifegain", "drain"]},
        vito,
    )
    eq("burst decided", burst["archetype"]["decided"], True)
    eq("burst has label", burst["archetype"]["label"], "one big turn")
    (ok if burst.get("commitments") else bad)(
        "burst has commitments", str(burst.get("commitments")))


def test_empty_commitments_not_a_success():
    """A plan with no commitments is an error state, never silent success."""
    print("\nplan_graph  — empty commitments is not success")
    plan = plan_graph.derive({}, CMDR)
    eq("plan_is_success false for empty", plan_graph.plan_is_success(plan), False)

    # decided:true with empty commitments must be structurally impossible
    # after normalize — even if a caller tampers.
    tampered = {
        "archetype": {"id": "burst", "label": "one big turn",
                      "candidates": ["burst"], "decided": True},
        "commitments": [],
    }
    eq("tampered empty commits not success",
       plan_graph.plan_is_success(tampered), False)


def test_chain_refuses_empty_brief():
    """Whole chain: empty brief refused at the first stage that can tell."""
    print("\n/api/commanders + /api/build  — refuse empty brief (CON-167)")
    client = TestClient(app.app)

    for payload, path in (
        ({}, "/api/commanders"),
        ({"theme": None}, "/api/commanders"),
        ({"theme": "", "angle": "vampires that drain"}, "/api/commanders"),
        ({"brief": {}, "commander": "Syr Konrad, the Grim"}, "/api/build"),
        ({"brief": {"theme": None}, "commander": "Syr Konrad, the Grim"},
         "/api/build"),
        ({"brief": {"theme": ""}, "commander": "Syr Konrad, the Grim"},
         "/api/build"),
    ):
        r = client.post(path, json=payload)
        eq(f"{path} themeless → 422", r.status_code, 422)
        body = r.json()
        detail = str(body.get("detail") or body)
        (ok if "theme" in detail.lower() else bad)(
            f"{path} reason names theme", detail)
        # Must not look like a deck or a picker.
        (ok if "picks" not in body and "deck" not in body else bad)(
            f"{path} no deck/picks payload", str(body.keys()))


def main() -> int:
    test_brief_theme_treats_none_as_absent()
    test_unusable_brief_helpers()
    test_queries_never_contain_none_substring()
    test_decided_requires_label()
    test_empty_commitments_not_a_success()
    test_chain_refuses_empty_brief()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
