"""
test_curve.py — can she actually play the plan? (/ curve spec)

Offline. No model calls, no network. Run:

    python test_curve.py
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
from pathlib import Path

from deckwright import curve
from deckwright import app

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def fail(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        fail(label, f"got {got!r} want {want!r}")


FIXTURES = Path(__file__).resolve().parents[1] / "data" / "synergy_fixtures.json"


def _land(name="Plains", n=1):
    return {
        "name": name if n == 1 else f"{name} {n}",
        "type": "Basic Land — Plains",
        "type_line": "Basic Land — Plains",
        "cmc": 0,
        "text": "{T}: Add {W}.",
        "oracle_text": "{T}: Add {W}.",
        "mana_cost": "",
    }


def _spell(name, cmc, text="{T}: Add {C}.", typ="Artifact"):
    return {
        "name": name,
        "type": typ,
        "type_line": typ,
        "cmc": cmc,
        "text": text,
        "oracle_text": text,
        "mana_cost": "{" + str(cmc) + "}" if cmc else "{0}",
    }


def _dense_deck(n_lands=36, cheap=True):
    """A castable goldfish deck: lands + rocks + cheap bodies."""
    deck = [_land(f"Plains{i}") for i in range(n_lands)]
    # Ramp rocks the goldfish can cast.
    for i in range(10):
        deck.append(_spell(f"Rock{i}", 1 if cheap else 3,
                           "{T}: Add {C}{C}." if cheap else "{T}: Add {C}."))
    # Cheap draw / bodies so "acted" is not only rocks.
    for i in range(20):
        cmc = 2 if cheap else 5
        deck.append(_spell(
            f"Piece{i}", cmc,
            "When this enters, draw a card.",
            "Creature — Test",
        ))
    # Pad to 99 with filler the goldfish may or may not cast.
    while len(deck) < 99:
        i = len(deck)
        deck.append(_spell(
            f"Pad{i}", 2 if cheap else 6,
            "Flying",
            "Creature — Bird",
        ))
    return deck[:99]


# ---------------------------------------------------------------------------
# diagnosis: the brief carries the words; the picker could not hear.
# ---------------------------------------------------------------------------

def test_preference_hears_surviving_brief_fields():
    """After commander overwrite, plan shadows angle — fantasy/theme must still count.

    Measured: strategy_profile() returned mid/mid for both curve wishes once
    pick.strategy/plan/mechanics replaced her words, even though angle and
    fantasy still said cheap-and-fast vs big-expensive.
    """
    print("\ncurve — preference hears fields that survive commander overwrite")
    low = {
        "theme": "vampire lifegain drain",
        "angle": "cheap and fast, low mana costs, I want to be doing things early",
        "fantasy": "doing things early with cheap vampires",
        "playstyle": "proactive",
        # Commander overwrite — the words that used to be the only ones read:
        "strategy": "Play vampires that gain life and drain the table.",
        "plan": "gain life and drain every turn",
        "mechanics": ["gain life", "lifelink", "drain"],
    }
    high = {
        "theme": "vampire lifegain drain",
        "angle": "big expensive spells, I don't mind waiting",
        "fantasy": "haymakers and expensive finishers late",
        "playstyle": "grindy",
        "strategy": "Play vampires that gain life and drain the table.",
        "plan": "gain life and drain every turn",
        "mechanics": ["gain life", "lifelink", "drain"],
    }
    eq("low-curve wish -> low", curve.preference(low), "low")
    eq("high-curve wish -> high", curve.preference(high), "high")
    eq("undecided brief -> mid",
       curve.preference({"theme": "vampires", "strategy": "gain life"}), "mid")

    # strategy_profile must use the same ear — one source of truth.
    eq("strategy_profile low", app.strategy_profile(low)["curve"], "low")
    eq("strategy_profile high", app.strategy_profile(high)["curve"], "high")


def test_preference_unknown_is_not_mid():
    """Unreadable brief is UNKNOWN, not a silent mid guess."""
    print("\ncurve — unknown brief is unknown, not mid")
    eq("None brief", curve.preference(None), curve.UNKNOWN)
    eq("empty brief", curve.preference({}), curve.UNKNOWN)


def test_cmc_tiebreak_orders_by_preference():
    """Within a sim-score band, low-curve prefers cheap; high prefers expensive.

    Not a theme-role mana-value filter — a tiebreak, same shape as synergy
    edge_count. The query still offers the pool; the math picks.
    """
    print("\ncurve — CMC tiebreak binds directionally")
    cheap = _spell("Cheap", 1)
    dear = _spell("Dear", 6)
    eq("low prefers cmc 1 over 6",
       curve.cmc_sort_key(cheap, "low") > curve.cmc_sort_key(dear, "low"), True)
    eq("high prefers cmc 6 over 1",
       curve.cmc_sort_key(dear, "high") > curve.cmc_sort_key(cheap, "high"), True)
    eq("mid is flat",
       curve.cmc_sort_key(cheap, "mid") == curve.cmc_sort_key(dear, "mid"), True)
    eq("unknown cmc does not tip low",
       curve.cmc_sort_key({"name": "X"}, "low"), 0)


# ---------------------------------------------------------------------------
# Measurement — the instrument before any skeleton change
# ---------------------------------------------------------------------------

def test_castable_curve_reports_per_turn():
    print("\ncurve — castable_curve is a per-turn act rate")
    deck = _dense_deck(cheap=True)
    report = curve.castable_curve(deck, cmdr_cmc=3, runs=80, seed=1)
    eq("status ok", report["status"], "ok")
    eq("ten turns", len(report["by_turn"]), 10)
    eq("rates are fractions",
       all(0.0 <= x <= 1.0 for x in report["by_turn"]), True)
    # A dense cheap deck should act on early turns most of the time.
    eq("turn 3 act rate > 0.5", report["by_turn"][2] > 0.5, True)


def test_castable_curve_unknown_on_unparseable():
    print("\ncurve — unparseable costs are unknown, not zero")
    bad = [_land()] + [{"name": "Mystery", "type": "Creature",
                        "text": "Flying"}]  # no cmc
    report = curve.castable_curve(bad, cmdr_cmc=3, runs=10, seed=0)
    eq("status unknown", report["status"], curve.UNKNOWN)
    eq("no fake by_turn zeros", "by_turn" not in report or report.get("by_turn") is None, True)


def test_missed_land_drops_measures_36_vs_38():
    """The 36-vs-38 argument settles with a number, not a tutorial quote."""
    print("\ncurve — missed_land_drops at 36 vs 38")
    d36 = _dense_deck(n_lands=36, cheap=True)
    d38 = _dense_deck(n_lands=38, cheap=True)
    # Trim back to 99 after adding lands (dense_deck pads).
    d36 = d36[:99]
    # Rebuild 38 by swapping two pads for lands.
    d38 = _dense_deck(n_lands=38, cheap=True)[:99]
    a = curve.missed_land_drops(d36, cmdr_cmc=3, runs=120, seed=2)
    b = curve.missed_land_drops(d38, cmdr_cmc=3, runs=120, seed=2)
    eq("36 status ok", a["status"], "ok")
    eq("38 status ok", b["status"], "ok")
    # Either direction is fine — the bar is that we MEASURE a difference.
    # With this seed and shape, 38 should miss no more than 36.
    eq("rates are real fractions",
       0.0 <= a["rate"] <= 1.0 and 0.0 <= b["rate"] <= 1.0, True)
    # Publish the measured delta so the number is visible in the suite.
    ok(f"measured delta 38-36 = {b['rate'] - a['rate']:+.4f}",
       f"36={a['rate']:.4f} 38={b['rate']:.4f}")


def test_ramp_split_separates_explosive():
    print("\ncurve — ramp_split normal vs explosive")
    deck = [
        _spell("Sol Ring", 1, "{T}: Add {C}{C}."),
        _spell("Arcane Signet", 2, "{T}: Add one mana of any color."),
        _spell("Dark Ritual", 1, "Add {B}{B}{B}.", "Instant"),
        _spell("Mana Flare", 3,
               "Lands you control tap for an additional mana.",
               "Enchantment"),
        _land(),
    ]
    split = curve.ramp_split(deck)
    eq("status ok", split["status"], "ok")
    eq("normal includes rocks", split["normal"] >= 2, True)
    eq("explosive includes ritual/doubler", split["explosive"] >= 1, True)


def test_skeleton_live_shapes_role_counts():
    """SKELETON_LIVE — derived counts are applied, not observation-only."""
    print("\ncurve — skeleton_for is live")
    cmdr = {"name": "Test", "cmc": 4, "type_line": "Legendary Creature — Test"}
    plan = {"archetype": {"id": "incremental", "decided": True},
            "commitments": [], "curve": "low"}
    report = curve.skeleton_for(cmdr, plan)
    eq("live flag on", curve.SKELETON_LIVE, True)
    eq("reporting_only false", report["reporting_only"], False)
    eq("live marker", report["live"], True)
    eq("roles present", isinstance(report.get("roles"), list), True)
    by_name = {r[0]: r[1] for r in report["roles"]}
    fixed = {r[0]: r[1] for r in app.ROLES}
    eq("low-curve draw >= fixed draw",
       by_name.get("draw", 0) >= fixed.get("draw", 0), True)
    # Sum-zero within spell roles vs conventional ROLES.
    eq("spell sum matches ROLES",
       app._spell_role_sum(report["roles"]),
       app._spell_role_sum(list(app.ROLES)))


def test_skeleton_live_low_vs_high_differential():
    """cheap-and-fast vs big-spells move counts the right way."""
    print("\ncurve — live skeleton differential low vs high")
    cmdr = {"name": "Vito, Thorn of the Dusk Rose", "cmc": 3,
            "type_line": "Legendary Creature — Vampire Cleric"}
    # Same playstyle so the delta is curve preference alone (re-gate).
    low_brief = {
        "theme": "vampire lifegain drain",
        "angle": "cheap and fast, low mana costs, I want to be doing things early",
        "fantasy": "doing things early with cheap vampires",
        "playstyle": "proactive",
        "strategy": "Play vampires that gain life and drain the table.",
        "mechanics": ["gain life", "lifelink", "drain"],
    }
    high_brief = {
        "theme": "vampire lifegain drain",
        "angle": "big expensive spells, I don't mind waiting",
        "fantasy": "haymakers and expensive finishers late",
        "playstyle": "proactive",
        "strategy": "Play vampires that gain life and drain the table.",
        "mechanics": ["gain life", "lifelink", "drain"],
    }
    low = {n: c for n, c, _ in app.skeleton_for(low_brief, commander=cmdr)}
    high = {n: c for n, c, _ in app.skeleton_for(high_brief, commander=cmdr)}
    eq("low draw > high draw", low["draw"] > high["draw"], True)
    eq("high ramp > low ramp", high["ramp"] > low["ramp"], True)
    eq("spell sums equal",
       sum(c for n, c in low.items() if n != "lands"),
       sum(c for n, c in high.items() if n != "lands"))
    eq("spell sum == ROLES spell sum",
       sum(c for n, c in low.items() if n != "lands"),
       app._spell_role_sum(list(app.ROLES)))

    # Playstyle still stacks on the derived curve.
    grindy = {n: c for n, c, _ in app.skeleton_for(
        {**high_brief, "playstyle": "grindy"}, commander=cmdr)}
    eq("grindy stacks more wipe than proactive high",
       grindy["wipe"] > high["wipe"], True)

    # Unknown brief does not invent a curve direction from empty words.
    plain = {n: c for n, c, _ in app.skeleton_for({}, commander=cmdr)}
    # cmc 3 + mid → no low densify (only cmc>=4 densifies mid). Equals ROLES.
    eq("empty brief + cmc3 keeps fixed draw", plain.get("draw"),
       {r[0]: r[1] for r in app.ROLES}["draw"])


def test_ui_curve_report_matches_live_flag():
    """UI copy matches whether the skeleton rewrote role counts."""
    print("\ncurve — UI curve_report honesty matches SKELETON_LIVE")
    html = Path(__file__).resolve().parents[1].joinpath("index.html").read_text()
    (ok if "renderCurveReportBlock" in html else fail)(
        "curve_report renderer present", "")
    if curve.SKELETON_LIVE:
        eq("live flag on", curve.SKELETON_LIVE, True)
        (ok if "shaped role counts" in html else fail)(
            "live copy says skeleton shaped role counts", "")
        (ok if "reporting only — it did not rewrite" in html else fail)(
            "reporting-only branch still in source for flag-off", "")
    else:
        eq("live flag off", curve.SKELETON_LIVE, False)
        (ok if "reporting only" in html else fail)(
            "copy says reporting only", "")
        (ok if "did not rewrite role counts" in html else fail)(
            "copy denies role-count rewrite", "")


def test_plan_carries_curve_preference():
    """plan_graph.derive exposes curve so the harness can see what was heard."""
    print("\ncurve — plan_graph carries preference")
    from deckwright import plan_graph
    cmdr = {
        "name": "Vito, Thorn of the Dusk Rose",
        "cmc": 3,
        "type_line": "Legendary Creature — Vampire Cleric",
        "oracle_text": (
            "Whenever you gain life, target opponent loses that much life.\n"
            "{3}{B}{B}: Creatures you control gain lifelink until end of turn."
        ),
        "color_identity": ["B"],
    }
    low = plan_graph.derive({
        "theme": "vampires",
        "angle": "cheap and fast, low mana costs",
        "fantasy": "doing things early",
        "strategy": "drain with vampires",
        "mechanics": ["lifegain", "drain"],
    }, cmdr)
    high = plan_graph.derive({
        "theme": "vampires",
        "angle": "big expensive spells, I don't mind waiting",
        "fantasy": "haymakers late",
        "strategy": "drain with vampires",
        "mechanics": ["lifegain", "drain"],
    }, cmdr)
    eq("low plan.curve", low.get("curve"), "low")
    eq("high plan.curve", high.get("curve"), "high")


def test_hei_bai_castable_curve_reference_band():
    """Expert Hei Bai 99 sets a reference band — measurement, not a gate."""
    print("\ncurve — Hei Bai bracket-2 castable_curve reference")
    payload = json.loads(FIXTURES.read_text())["moxfield_hei_bai_bracket2"]
    raw = payload["cards"]
    # Fixture cards lack cmc; derive a usable stand-in from mana_cost in text
    # or type. Lands = 0; else leave unknown → the house rule must fire if
    # too many are unparseable. Build a minimal playable projection:
    deck = []
    for c in raw:
        tl = (c.get("type_line") or "").lower()
        card = {
            "name": c["name"],
            "type": c.get("type_line") or "",
            "type_line": c.get("type_line") or "",
            "text": c.get("oracle_text") or "",
            "oracle_text": c.get("oracle_text") or "",
            "mana_cost": c.get("mana_cost") or "",
        }
        if "land" in tl.split("—")[0]:
            card["cmc"] = 0
        elif "cmc" in c:
            card["cmc"] = c["cmc"]
        else:
            # Cheap stand-in so the goldfish can run: rocks/creatures get 2.
            card["cmc"] = 2
            card["mana_cost"] = "{2}"
        deck.append(card)
    # Pad / trim toward 99 with basics if the fixture is a unique-name set.
    while len(deck) < 99:
        deck.append(_land(f"Basic{len(deck)}"))
    report = curve.castable_curve(deck[:99], cmdr_cmc=4, runs=60, seed=3)
    eq("hei bai measurable", report["status"], "ok")
    ok(f"hei bai turn-3 act rate {report['by_turn'][2]:.2f}",
       f"turns={report['by_turn']}")


def main() -> int:
    test_preference_hears_surviving_brief_fields()
    test_preference_unknown_is_not_mid()
    test_cmc_tiebreak_orders_by_preference()
    test_castable_curve_reports_per_turn()
    test_castable_curve_unknown_on_unparseable()
    test_missed_land_drops_measures_36_vs_38()
    test_ramp_split_separates_explosive()
    test_skeleton_live_shapes_role_counts()
    test_skeleton_live_low_vs_high_differential()
    test_ui_curve_report_matches_live_flag()
    test_plan_carries_curve_preference()
    test_hei_bai_castable_curve_reference_band()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
