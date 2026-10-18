"""
test_differential_harness.py — CON-168 offline checks for the differential harness.

No model calls. No Scryfall. Pure comparison logic and report shape.

    python test_differential_harness.py
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
sys.path.insert(0, str(ROOT / "scripts"))

import differential_harness as dh  # noqa: E402

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


def _run(wish_id, *, status="ok", picks=None, note="", commander=None,
         names=None, arch_id=None, arch_label=None, commits=None,
         price=None, budget_total=None, unknown_reason=None):
    commits = commits if commits is not None else []
    plan = None
    if status == "ok":
        plan = {
            "archetype": {"id": arch_id, "label": arch_label, "decided": bool(arch_id)},
            "commitments": commits,
        }
    return dh.WishRun(
        wish_id=wish_id,
        wish=f"wish for {wish_id}",
        status=status,
        unknown_reason=unknown_reason,
        brief={"theme": wish_id, "budget_total": budget_total} if status == "ok" else None,
        commander_picks=list(picks or []),
        commander_note=note,
        commander_query="is:commander",
        commander=commander or ((picks or [None])[0]),
        deck_names=set(names or []),
        deck=[{"name": n, "role": "theme"} for n in (names or [])],
        plan=plan,
        price=price,
        budget={"total": price, "budget": budget_total} if price is not None else None,
    )


def test_jaccard():
    print("\njaccard  — overlap both directions")
    eq("identical", dh.jaccard({"a", "b"}, {"a", "b"}), 1.0)
    eq("disjoint", dh.jaccard({"a", "b"}, {"c", "d"}), 0.0)
    eq("half", dh.jaccard({"a", "b"}, {"b", "c"}), 1 / 3)
    eq("empty is unknown", dh.jaccard(set(), {"a"}), None)
    eq("both empty unknown", dh.jaccard(set(), set()), None)


def test_identical_decks_fail_legibly():
    print("\nidentical decks  — fail with a human sentence")
    shared = {f"Card {i}" for i in range(99)}
    a = _run("a", picks=["Vito"], commander="Vito", names=shared,
             arch_id="incremental", commits=[
                 {"id": "x", "label": "payoffs", "counts": {"role": "theme", "min": 8}}])
    b = _run("b", picks=["Vito"], commander="Vito", names=set(shared),
             arch_id="burst", commits=[
                 {"id": "y", "label": "combo", "counts": {"role": "theme", "min": 6}}])
    checks = dh.compare_pair(
        a, b,
        {"jaccard": True, "commanders_differ": True},
        membership_hits={},
    )
    j = next(c for c in checks if c.name.startswith("jaccard"))
    eq("identical is FAIL", j.status, "FAIL")
    (ok if "same deck" in j.detail.lower() else bad)(
        "failure names the sameness", j.detail)


def test_unknown_excluded_not_different():
    print("\nUNKNOWN  — refused/missing side is not 'different'")
    a = _run("a", status="unknown", unknown_reason="no brief")
    b = _run("b", picks=["Vito"], commander="Vito",
             names={f"C{i}" for i in range(99)}, arch_id="incremental")
    checks = dh.compare_pair(a, b, {"jaccard": True, "commanders_differ": True},
                             membership_hits={})
    eq("pair is UNKNOWN", {c.status for c in checks}, {"UNKNOWN"})
    (ok if all("excluded" in c.detail.lower() or "unknown" in c.detail.lower()
               for c in checks) else bad)(
        "detail says excluded/unknown", "; ".join(c.detail for c in checks))


def test_vacuous_not_pass():
    print("\nvacuous  — nothing to check is UNKNOWN, not PASS")
    a = _run("a", picks=["A"], commander="A", names=set(), arch_id="incremental")
    b = _run("b", picks=["B"], commander="B", names=set(), arch_id="burst")
    checks = dh.compare_pair(a, b, {"jaccard": True}, membership_hits={})
    j = next(c for c in checks if c.name.startswith("jaccard"))
    eq("empty decks → UNKNOWN", j.status, "UNKNOWN")


def test_archetype_differs():
    print("\narchetype  — incremental vs burst")
    a = _run("inc", picks=["Vito"], commander="Vito",
             names={f"A{i}" for i in range(80)} | {f"S{i}" for i in range(19)},
             arch_id="incremental", arch_label="grind it out")
    b = _run("burst", picks=["Vito"], commander="Vito",
             names={f"A{i}" for i in range(80)} | {f"B{i}" for i in range(19)},
             arch_id="burst", arch_label="one big turn")
    checks = dh.compare_pair(
        a, b,
        {"archetypes": ("incremental", "burst"), "jaccard": True},
        membership_hits={},
    )
    arch = next(c for c in checks if c.name == "archetype differs")
    eq("archetype PASS", arch.status, "PASS")

    same = _run("burst2", picks=["Vito"], commander="Vito",
                names={f"A{i}" for i in range(80)} | {f"B{i}" for i in range(19)},
                arch_id="incremental", arch_label="grind it out")
    checks2 = dh.compare_pair(
        a, same,
        {"archetypes": ("incremental", "burst")},
        membership_hits={},
    )
    arch2 = next(c for c in checks2 if c.name == "archetype differs")
    eq("same archetype FAIL", arch2.status, "FAIL")
    (ok if "incremental" in arch2.detail and "burst" in arch2.detail else bad)(
        "names both sides", arch2.detail)


def test_commitments_meet_min():
    print("\ncommitments  — differ and each side meets its own min")
    deck_a = ([{"name": f"Pay{i}", "role": "theme"} for i in range(12)]
              + [{"name": f"Ramp{i}", "role": "ramp"} for i in range(10)])
    deck_b = ([{"name": f"Combo{i}", "role": "theme"} for i in range(4)]
              + [{"name": f"Ramp{i}", "role": "ramp"} for i in range(10)])
    a = dh.WishRun(
        wish_id="a", wish="a", status="ok", unknown_reason=None,
        brief={"theme": "vampires"}, commander_picks=["Vito"], commander_note="",
        commander_query="", commander="Vito",
        deck_names={c["name"] for c in deck_a}, deck=deck_a,
        plan={"archetype": {"id": "incremental"}, "commitments": [
            {"id": "lifegain_density", "label": "lifegain payoffs",
             "counts": {"role": "theme", "min": 10}},
        ]},
        price=100.0, budget=None,
    )
    b = dh.WishRun(
        wish_id="b", wish="b", status="ok", unknown_reason=None,
        brief={"theme": "vampires"}, commander_picks=["Vito"], commander_note="",
        commander_query="", commander="Vito",
        deck_names={c["name"] for c in deck_b}, deck=deck_b,
        plan={"archetype": {"id": "burst"}, "commitments": [
            {"id": "combo_pieces", "label": "combo halves",
             "counts": {"role": "theme", "min": 6}},
        ]},
        price=100.0, budget=None,
    )
    checks = dh.compare_pair(
        a, b,
        {"commitments_differ": True, "commitments_meet_min": True},
        membership_hits={},
    )
    differ = next(c for c in checks if c.name == "commitments differ")
    meet = next(c for c in checks if c.name == "commitments meet min")
    eq("commitments differ PASS", differ.status, "PASS")
    eq("b misses its min → FAIL", meet.status, "FAIL")
    (ok if "combo" in meet.detail.lower() or "min 6" in meet.detail else bad)(
        "names the short commitment", meet.detail)


def test_membership_against_query():
    print("\nmembership  — against query hits, not a hand-written list")
    a_names = {f"Angel {i}" for i in range(10)} | {f"Other {i}" for i in range(89)}
    b_names = {f"Vampire {i}" for i in range(10)} | {f"Other {i}" for i in range(89)}
    a = _run("angels", picks=["Giada"], commander="Giada", names=a_names)
    b = _run("vampires", picks=["Vito"], commander="Vito", names=b_names)
    hits = {
        "t:angel": {f"Angel {i}" for i in range(10)},
        "t:vampire": {f"Vampire {i}" for i in range(10)},
    }
    checks = dh.compare_pair(
        a, b,
        {"membership": {
            "a": {"query": "t:angel", "min": 5, "label": "angels"},
            "b": {"query": "t:vampire", "min": 5, "label": "vampires"},
        }},
        membership_hits=hits,
    )
    mem = [c for c in checks if c.name.startswith("membership")]
    eq("both membership checks PASS", {c.status for c in mem}, {"PASS"})

    # Hits empty for a query → UNKNOWN (could not look), not FAIL-as-zero.
    checks_u = dh.compare_pair(
        a, b,
        {"membership": {
            "a": {"query": "t:angel", "min": 5, "label": "angels"},
        }},
        membership_hits={},  # query never resolved
    )
    mem_u = next(c for c in checks_u if c.name.startswith("membership"))
    eq("unresolved query → UNKNOWN", mem_u.status, "UNKNOWN")


def test_commander_picks_differ():
    print("\ncommanders  — same five is a generic-rung failure")
    same = ["A", "B", "C", "D", "E"]
    a = _run("angels", picks=same, commander="A",
             names={f"X{i}" for i in range(99)},
             note="Scryfall had nothing for “None” as a card type")
    b = _run("vampires", picks=list(same), commander="A",
             names={f"Y{i}" for i in range(99)},
             note="Scryfall had nothing for “None” as a card type")
    checks = dh.compare_pair(a, b, {"commanders_differ": True}, membership_hits={})
    c = next(x for x in checks if x.name == "commander picks differ")
    eq("identical picks FAIL", c.status, "FAIL")
    (ok if "same" in c.detail.lower() and "commander" in c.detail.lower() else bad)(
        "readable commander failure", c.detail)
    (ok if "note" in c.detail.lower() or "generic" in c.detail.lower()
           or "scryfall" in c.detail.lower() else bad)(
        "surfaces the note field", c.detail)


def _deck_with_cmc(prefix: str, cmcs: list[float], role: str = "theme"):
    return [{"name": f"{prefix}{i}", "role": role, "cmc": cmc,
             "type": "Creature — Vampire"}
            for i, cmc in enumerate(cmcs)]


def test_curve_binds():
    """CON-172: 'cheap' means mana value, not dollars — assert on the curve."""
    print("\ncurve  — low-MV wish must actually shape the curve")
    low = _deck_with_cmc("L", [1] * 40 + [2] * 30 + [3] * 10)  # avg ~1.6, ≤2: 70
    high = _deck_with_cmc("H", [4] * 40 + [5] * 30 + [6] * 10)  # avg ~4.6, ≤2: 0
    # Shared lands so Jaccard is not 0 under the same commander.
    lands = [{"name": f"Land{i}", "role": "lands", "cmc": 0, "type": "Land"}
             for i in range(19)]
    a = dh.WishRun(
        wish_id="vampires-lowcurve", wish="low", status="ok", unknown_reason=None,
        brief={"theme": "vampires"}, commander_picks=["Vito"], commander_note="",
        commander_query="", commander="Vito",
        deck_names={c["name"] for c in low + lands}, deck=low + lands,
        plan={"archetype": {"id": "aggro"}, "commitments": []},
        price=200.0, budget=None,  # dollars must not matter
    )
    b = dh.WishRun(
        wish_id="vampires-highcurve", wish="high", status="ok", unknown_reason=None,
        brief={"theme": "vampires"}, commander_picks=["Vito"], commander_note="",
        commander_query="", commander="Vito",
        deck_names={c["name"] for c in high + lands}, deck=high + lands,
        plan={"archetype": {"id": "incremental"}, "commitments": []},
        price=40.0, budget=None,
    )
    checks = dh.compare_pair(a, b, {"curve_binds": True}, membership_hits={})
    cur = next(c for c in checks if c.name == "curve binds")
    eq("lower avg MV + more MV≤2 PASS", cur.status, "PASS")
    (ok if "avg" in cur.detail.lower() or "mv" in cur.detail.lower() else bad)(
        "detail names mana value", cur.detail)

    # Inverted curves fail even when the "cheap" side is cheaper in dollars.
    checks2 = dh.compare_pair(b, a, {"curve_binds": True}, membership_hits={})
    cur2 = next(c for c in checks2 if c.name == "curve binds")
    eq("inverted curve FAIL", cur2.status, "FAIL")

    # Missing CMC is UNKNOWN, not a zero average.
    bare = dh.WishRun(
        wish_id="bare", wish="bare", status="ok", unknown_reason=None,
        brief={"theme": "vampires"}, commander_picks=["Vito"], commander_note="",
        commander_query="", commander="Vito",
        deck_names={f"X{i}" for i in range(80)},
        deck=[{"name": f"X{i}", "role": "theme"} for i in range(80)],
        plan={}, price=None, budget=None,
    )
    checks3 = dh.compare_pair(a, bare, {"curve_binds": True}, membership_hits={})
    cur3 = next(c for c in checks3 if c.name == "curve binds")
    eq("missing cmc → UNKNOWN", cur3.status, "UNKNOWN")


def test_no_price_assertions():
    """CON-172: harness must not assert on dollars anywhere."""
    print("\nno price assertions  — dollars are ballparks, never a gate")
    eq("budget_binds expect gone from PAIRS",
       any((p.get("expect") or {}).get("budget_binds") for p in dh.PAIRS), False)
    eq("no budget-cheap pair id",
       any(p["id"] == "budget-cheap-vs-open" for p in dh.PAIRS), False)
    eq("curve pair present",
       any(p["id"] == "curve-low-vs-high" for p in dh.PAIRS), True)
    eq("_check_budget removed", hasattr(dh, "_check_budget"), False)


def test_report_table():
    print("\nreport  — human-readable table columns")
    a = _run("angels", picks=["Giada"], commander="Giada",
             names={f"A{i}" for i in range(90)} | {f"S{i}" for i in range(9)},
             arch_id="incremental", arch_label="grind it out",
             commits=[{"id": "x", "label": "angel density",
                       "counts": {"role": "theme", "min": 10}}],
             price=120.0, budget_total=150)
    b = _run("vampires", picks=["Vito"], commander="Vito",
             names={f"V{i}" for i in range(90)} | {f"S{i}" for i in range(9)},
             arch_id="burst", arch_label="one big turn",
             commits=[{"id": "y", "label": "drain payoffs",
                       "counts": {"role": "theme", "min": 8}}],
             price=140.0, budget_total=150)
    pair_checks = dh.compare_pair(a, b, {"jaccard": True, "commanders_differ": True},
                                  membership_hits={})
    table = dh.format_report(
        [a, b],
        [("tribe", a, b, pair_checks)],
    )
    for col in ("wish", "commander", "archetype", "commitment", "overlap"):
        (ok if col.lower() in table.lower() else bad)(f"table has {col}", table[:200])
    (ok if "Giada" in table and "Vito" in table else bad)("names commanders", table)


def test_exit_semantics():
    print("\nexit  — vacuous all-UNKNOWN is not a green run")
    eq("all unknown → nonzero", dh.exit_code(["UNKNOWN", "UNKNOWN"]), 2)
    eq("any fail → 1", dh.exit_code(["PASS", "FAIL", "UNKNOWN"]), 1)
    eq("pass with unknown ok → 0", dh.exit_code(["PASS", "UNKNOWN"]), 0)
    eq("all pass → 0", dh.exit_code(["PASS", "PASS"]), 0)
    eq("empty → nonzero", dh.exit_code([]), 2)


def test_scryfall_headers():
    print("\nscryfall  — User-Agent and Accept both required")
    h = dh._scryfall_headers()
    (ok if h.get("User-Agent") else bad)("User-Agent set", str(h))
    eq("Accept is application/json", h.get("Accept"), "application/json")


def test_format_readiness_blockers():
    """CON-170: UNKNOWN reason must name the term, not just 'terms'."""
    print("\nformat_readiness_blockers  — names the cause")
    got = dh.format_readiness_blockers([
        {"signal": "terms", "detail": {"term": "drain the table"}},
        {"signal": "pool", "detail": {"count": 4}},
        {"signal": "strategy", "detail": {}},
    ])
    eq("terms carries the word", got[0], "terms:drain the table")
    eq("pool carries count", got[1], "pool:4")
    eq("strategy stays signal", got[2], "strategy")
    eq("empty terms still says terms",
       dh.format_readiness_blockers([{"signal": "terms", "detail": {}}]),
       ["terms"])


def test_load_runs_from_dump():
    print("\nfrom-dump  — rebuild WishRun without model calls")
    import json
    from pathlib import Path
    import tempfile
    dump = {
        "runs": {
            "angels-lifegain": {
                "wish_id": "angels-lifegain", "status": "ok",
                "brief": {"theme": "angel"}, "commander": "Giada",
                "commander_picks": ["Giada"], "deck_names": ["Serra Angel"],
                "deck": [{"name": "Serra Angel", "role": "theme"}],
                "plan": {"archetype": {"id": "incremental"}, "commitments": []},
                "price": 10.0,
            }
        }
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "d.json"
        path.write_text(json.dumps(dump))
        runs = dh.load_runs_from_dump(path, ["angels-lifegain", "vampires-lifegain"])
    eq("loaded ok side", runs["angels-lifegain"].status, "ok")
    eq("loaded commander", runs["angels-lifegain"].commander, "Giada")
    eq("missing side is unknown", runs["vampires-lifegain"].status, "unknown")
    (ok if "missing from dump" in (runs["vampires-lifegain"].unknown_reason or "")
     else bad)("missing reason", runs["vampires-lifegain"].unknown_reason)


def main():
    test_jaccard()
    test_identical_decks_fail_legibly()
    test_unknown_excluded_not_different()
    test_vacuous_not_pass()
    test_archetype_differs()
    test_commitments_meet_min()
    test_membership_against_query()
    test_commander_picks_differ()
    test_curve_binds()
    test_no_price_assertions()
    test_report_table()
    test_exit_semantics()
    test_scryfall_headers()
    test_format_readiness_blockers()
    test_load_runs_from_dump()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
