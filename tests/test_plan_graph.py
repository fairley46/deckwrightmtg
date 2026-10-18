"""
test_plan_graph.py — deterministic plan graph from brief + commander.

Offline. No model calls. Run:

    python test_plan_graph.py
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import ast
import json
import os
import sys
from pathlib import Path

from deckwright import sim

from deckwright import plan_graph

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "data" / "plan_graph_fixtures.json"
EXPECTED_PATH = Path(__file__).resolve().parents[1] / "data" / "plan_graph_expected.json"

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


def _load_cases():
    data = json.loads(FIXTURES_PATH.read_text())
    return data["cases"]


def _canonical(plan: dict) -> str:
    return json.dumps(plan, sort_keys=True, separators=(",", ":"))


def test_no_model_imports():
    """plan_graph must not import or call the model layer."""
    print("\nplan_graph imports  — no anthropic / llm")
    src = Path(plan_graph.__file__).read_text()
    tree = ast.parse(src)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    banned = {"anthropic", "llm", "openai"}
    hits = [m for m in imports if m.split(".")[0] in banned]
    eq("no model imports", hits, [])


def test_thesis_keys_only():
    """Every condition check key must be in sim.THESIS_KEYS."""
    print("\nplan_graph conditions  — THESIS_KEYS allowlist")
    for case in _load_cases():
        cmdr = case.get("commander")
        if cmdr:
            plan = plan_graph.derive(case["brief"], cmdr)
        else:
            plan = plan_graph.derive_provisional(case["brief"])
        for cond in plan.get("conditions") or []:
            for key in (cond.get("check") or {}):
                (ok if key in sim.THESIS_KEYS else bad)(
                    f"{case['id']} key {key!r} allowed", str(sim.THESIS_KEYS))


def test_schema_shape():
    """Output matches PLAN-GRAPH-SCHEMA.md top-level fields."""
    print("\nplan_graph shape  — schema contract")
    case = next(c for c in _load_cases() if c["id"] == "edgar_vampires_wide")
    plan = plan_graph.derive(case["brief"], case["commander"])
    for key in ("version", "derived_from", "conditions", "commitments",
                "load_bearing", "theme", "activation_multiplier"):
        (ok if key in plan else bad)(f"has {key}", "")
    eq("version", plan["version"], plan_graph.PLAN_VERSION)
    eq("sim_version", plan["derived_from"]["sim_version"], sim.SIM_VERSION)
    eq("theme.binding", plan["theme"]["binding"], False)
    eq("load_bearing includes commander",
       case["commander"]["name"] in plan["load_bearing"], True)
    eq("activation_multiplier has relevant",
       "relevant" in (plan.get("activation_multiplier") or {}), True)
    for cond in plan["conditions"]:
        for field in ("id", "label", "why", "check", "by_turn", "weight"):
            (ok if field in cond else bad)(f"condition has {field}", cond.get("id"))
        (ok if cond["weight"] in ("core", "supporting", "nice") else bad)(
            f"weight valid for {cond['id']}", cond["weight"])


def test_commitments_vs_conditions():
    """Lifegain gets a floor commitment; mill does not invent a wish condition."""
    print("\nplan_graph commitments  — floors without wish conditions")
    oloro = next(c for c in _load_cases() if c["id"] == "oloro_lifegain")
    plan = plan_graph.derive(oloro["brief"], oloro["commander"])
    commit_ids = {c["id"] for c in plan.get("commitments") or []}
    (ok if "lifegain_density" in commit_ids else bad)(
        "oloro lifegain commitment", str(commit_ids))
    keys = {k for c in plan["conditions"] for k in (c.get("check") or {})}
    (ok if "life" not in "".join(keys) else bad)(
        "no invented life total key", str(keys))

    mill = next(c for c in _load_cases() if c["id"] == "provisional_mill_wish")
    prov = plan_graph.derive_provisional(mill["brief"])
    prov_keys = {k for c in prov["conditions"] for k in (c.get("check") or {})}
    (ok if "mill" not in prov_keys and "graveyard" not in prov_keys else bad)(
        "mill/graveyard not in conditions", str(prov_keys))
    (ok if any(c["id"] == "graveyard_density" for c in prov.get("commitments") or [])
     else bad)("graveyard commitment present", str(prov.get("commitments")))


def test_hei_bai_commitments_from_demand_profile():
    """commitments come from the commander, not theme-word expansion.

    Hei Bai's oracle is sacrifice → +1/+1 counters → move them when he leaves.
    A brief that never says lifegain used to emit lifegain_density because
    dies_triggers expands to life_math. Counter synergies and re-entry were
    absent entirely. demand_profile already names what he needs — use it.
    """
    print("\nplan_graph Hei Bai commitments from demand_profile")
    # Brief mirrored from the measured baseline (no lifegain words).
    brief = {
        "theme": "Hei Bai, Spirit of Balance sacrifice deck",
        "angle": ("Sacrifice creatures and artifacts to load +1/+1 counters "
                  "onto Hei Bai, then relocate those counters to another "
                  "permanent when he leaves the battlefield"),
        "mechanics": [
            "sacrifice fodder", "+1/+1 counters",
            "counter movement/proliferate",
            "cheap creature and artifact generation",
            "aristocrats value",
        ],
        "playstyle": "proactive",
    }
    cmdr = {
        "name": "Hei Bai, Spirit of Balance",
        "type": "Legendary Creature — Bear Spirit",
        "cmc": 4,
        "cost": "{2}{W}{B}",
        "text": (
            "Whenever Hei Bai enters or attacks, you may sacrifice another "
            "creature or artifact. If you do, put two +1/+1 counters on "
            "Hei Bai.\nWhen Hei Bai leaves the battlefield, put its counters "
            "on target creature you control."
        ),
    }
    plan = plan_graph.derive(brief, cmdr)
    ids = {c["id"] for c in plan.get("commitments") or []}
    (ok if "lifegain_density" not in ids else bad)(
        "no fabricated lifegain commitment", str(ids))
    (ok if "counter_payoff_density" in ids else bad)(
        "counter payoff from produces +1/+1 counter", str(ids))
    (ok if "reentry_density" in ids else bad)(
        "re-entry from enters/leaves_battlefield", str(ids))
    (ok if "sacrifice_density" in ids else bad)(
        "sacrifice loop from outlet + consumes", str(ids))
    # Relation expand used to invent go-wide payoffs from tokens←go_tall.
    (ok if "wide_payoff_density" not in ids else bad)(
        "no fabricated go-wide payoff", str(ids))
    # Payoff searches must actually chase counters and blink.
    queries = " ".join(s["query"] for s in plan_graph.payoff_searches(plan))
    (ok if "counters-matter" in queries or "+1/+1" in queries else bad)(
        "payoff search covers counters", queries)
    (ok if "blink" in queries else bad)(
        "payoff search covers blink/re-entry", queries)
    # relation neighbors (life_math via dies_triggers) may enter the
    # recall pool, but must not appear as a plan commitment.
    from deckwright import synergy
    neigh_fams = {n["family"] for n in synergy.synergy_neighbors(cmdr)}
    (ok if "life_math" in neigh_fams else bad)(
        "recall neighbors see life_math hop", str(neigh_fams))
    (ok if "lifegain_density" not in ids else bad)(
        "life_math neighbor did not become a commitment", str(ids))


def test_commander_changes_plan():
    """Second derivation with commander differs from provisional."""
    print("\nplan_graph commander  — second derivation is the real one")
    case = next(c for c in _load_cases() if c["id"] == "tatyova_landfall")
    prov = plan_graph.derive_provisional(case["brief"])
    full = plan_graph.derive(case["brief"], case["commander"])
    prov_turn = next(c["by_turn"] for c in prov["conditions"] if c["id"] == "commander_out")
    full_turn = next(c["by_turn"] for c in full["conditions"] if c["id"] == "commander_out")
    (ok if full_turn != prov_turn or prov != full else bad)(
        "commander tunes plan", f"prov={prov_turn} full={full_turn}")
    labels = [c["label"] for c in full["conditions"]]
    (ok if any("land" in l.lower() for l in labels) else bad)(
        "landfall brief surfaces land condition", str(labels))


def test_draw_threshold_floor():
    """Draw conditions use >= 4 per SCORING-CONTRACT, not a lazy threshold."""
    print("\nplan_graph draw  — threshold floor")
    oloro = next(c for c in _load_cases() if c["id"] == "oloro_lifegain")
    plan = plan_graph.derive(oloro["brief"], oloro["commander"])
    draw_conds = [c for c in plan["conditions"]
                  if "cards_drawn" in (c.get("check") or {})]
    for cond in draw_conds:
        val = cond["check"]["cards_drawn"]
        (ok if isinstance(val, int) and val >= 4 else bad)(
            f"draw threshold {cond['id']}", str(val))


def test_stable_json():
    """Fixture briefs produce stable canonical JSON."""
    print("\nplan_graph stability  — fixture golden JSON")
    expected = {}
    if EXPECTED_PATH.is_file():
        expected = json.loads(EXPECTED_PATH.read_text())
    for case in _load_cases():
        cid = case["id"]
        cmdr = case.get("commander")
        if cmdr:
            plan = plan_graph.derive(case["brief"], cmdr)
        else:
            plan = plan_graph.derive_provisional(case["brief"])
        again = (plan_graph.derive(case["brief"], cmdr) if cmdr
                 else plan_graph.derive_provisional(case["brief"]))
        eq(f"{cid} deterministic", _canonical(plan), _canonical(again))
        canon = _canonical(plan)
        if cid in expected:
            # sim_version is deliberately excluded from the golden. It bumps
            # every time perception improves (2 -> 6 over one session), and a
            # golden that pins it goes stale on work that did not change a
            # single plan node. Assert it separately against the live value.
            import re as _re
            _strip = lambda t: _re.sub(r'"sim_version": ?\d+', '"sim_version": *', t)
            eq(f"{cid} matches golden", _strip(canon), _strip(expected[cid]))
            eq(f"{cid} stamps live SIM_VERSION",
               (plan.get("derived_from") or {}).get("sim_version"), sim.SIM_VERSION)
        else:
            bad(f"{cid} missing golden — run with RECORD=1", "no expected entry")


def test_derive_provisional_no_commander():
    """Provisional plan has null commander in derived_from."""
    print("\nplan_graph provisional  — pre-commander pass")
    case = next(c for c in _load_cases() if c["id"] == "provisional_mill_wish")
    plan = plan_graph.derive_provisional(case["brief"])
    eq("commander null", plan["derived_from"]["commander"], None)
    (ok if plan["conditions"] else bad)("has conditions", "")



def test_archetype_changes_the_pool():
    """the archetype must change WHICH CARDS are searched.

    The commander-aware plan work's test passes on a threshold-only change — that is exactly the
    shallowness Brad called out. This one asserts the payoff SEARCHES differ,
    so moving a number cannot satisfy it.
    """
    print("\narchetype changes the pool")
    vito = {"name": "Vito, Thorn of the Dusk Rose", "cost": "{3}{B}", "cmc": 4,
            "type": "Legendary Creature — Vampire Cleric",
            "text": "Whenever you gain life, target opponent loses that much life."}
    base = {"theme": "lifegain", "colors": "WB", "mechanics": ["lifegain", "drain"]}
    sets = {}
    for label, strategy in (("incremental", "gain life every turn and turn it into cards"),
                            ("burst", "win out of nowhere with one big combo turn"),
                            ("aggro", "attack with big lifelink creatures")):
        plan = plan_graph.derive({**base, "strategy": strategy}, vito)
        eq(f"{label} archetype", plan["archetype"]["id"], label)
        (ok if plan["archetype"]["decided"] else bad)(
            f"{label} decided by her words", str(plan["archetype"]))
        sets[label] = {s["query"] for s in plan_graph.payoff_searches(plan)}
    (ok if len(set(map(frozenset, sets.values()))) == 3 else bad)(
        "three archetypes search three different card pools",
        str({k: len(v) for k, v in sets.items()}))
    (ok if sets["burst"] - sets["incremental"] else bad)(
        "burst searches cards incremental does not", str(sets["burst"] - sets["incremental"]))
    # Both halves of the Vito loop — gain→lose AND lose→gain. the
    # lose→gain half (Exquisite Blood) was missing, so the combo piece never
    # entered the payoff pool.
    burst_q = " ".join(sets["burst"])
    (ok if "opponent loses life" in burst_q else bad)(
        "burst searches the Exquisite Blood half of the loop", burst_q)
    # A brief that does not distinguish must NOT pretend she chose.
    quiet = plan_graph.derive({**base, "strategy": "a lifegain deck"}, vito)
    (ok if not quiet["archetype"]["decided"] else bad)(
        "undecided brief is reported, not guessed", str(quiet["archetype"]))
    (ok if len(quiet["archetype"]["candidates"]) > 1 else bad)(
        "candidates offered when undecided", str(quiet["archetype"]["candidates"]))


def test_commander_shapes_the_plan():
    """different commanders on one brief must derive different plans.

    Five WB lifegain commanders used to produce a byte-identical plan — the
    derivation read the brief and barely the commander. The goldens above
    passed the whole time, because none of them covered a commander that is
    itself a lifegain payoff. A golden can only certify what it looks at.
    """
    print("\ncommander shapes the plan")
    brief = {"theme": "lifegain", "colors": "WB",
             "strategy": "gain life every turn and turn that life into cards",
             "mechanics": ["lifegain", "drain"]}
    cmdrs = [
        # is the payoff AND repeats -> wants the most gain events
        {"name": "Vito, Thorn of the Dusk Rose", "cost": "{3}{B}", "cmc": 4,
         "type": "Legendary Creature — Vampire Cleric",
         "text": "Whenever you gain life, target opponent loses that much life."},
        # is a payoff, does not repeat per turn
        {"name": "Heliod, Sun-Crowned", "cost": "{2}{W}", "cmc": 3,
         "type": "Legendary Enchantment Creature — God",
         "text": "Whenever you gain life, put a +1/+1 counter on target creature."},
        # NOT a payoff — an outlet. Needs payoffs from the 99 instead.
        {"name": "Ayli, Eternal Pilgrim", "cost": "{1}{W}{B}", "cmc": 3,
         "type": "Legendary Creature — Kor Cleric",
         "text": "Deathtouch. {1}, Sacrifice another creature: You gain 2 life."},
    ]
    plans = [plan_graph.derive(brief, c) for c in cmdrs]
    keys = [tuple(sorted(f"{k}={v}" for c in p["conditions"]
                         for k, v in c["check"].items())) for p in plans]
    (ok if len(set(keys)) >= 2 else bad)(
        "commanders derive different plans", f"{len(set(keys))} distinct of {len(keys)}")
    # The commander that converts every gain must ask for MORE gain events than
    # the one that merely gains life — that is the whole point of reading it.
    def life_target(p):
        for c in p["conditions"]:
            if "life_gained" in c["check"]:
                return c["check"]["life_gained"]
        return 0
    (ok if life_target(plans[0]) > life_target(plans[2]) else bad)(
        "repeating payoff commander wants more gain events",
        f"Vito {life_target(plans[0])} vs Ayli {life_target(plans[2])}")
    for p in plans:
        for c in p["conditions"]:
            for k in c["check"]:
                (ok if k in sim.THESIS_KEYS else bad)(
                    f"{k} is measurable", k)


def test_fixture_briefs_discriminate_archetype():
    """the incremental/burst axis must hear the preserved briefs.

    The differential fixture is the measured failure: drain-burst's angle says
    "one explosive finishing turn" and mechanics name a "burst finisher", yet
    pick_archetype returned incremental/undecided. If this test fails, the
    strategy label is lying about a wish that already changed the deck.
    """
    print("\nfixture briefs discriminate archetype")
    path = (Path(__file__).resolve().parents[1] / "testdata" / "review"
            / "differential-run-2026-08-06.json")
    data = json.loads(path.read_text())
    vito = {
        "name": "Vito, Thorn of the Dusk Rose",
        "cost": "{3}{B}", "cmc": 4,
        "type": "Legendary Creature — Vampire Cleric",
        "text": "Whenever you gain life, target opponent loses that much life.",
    }
    plans = {}
    for wid, want in (("drain-incremental", "incremental"),
                      ("drain-burst", "burst")):
        brief = data["runs"][wid]["brief"]
        plan = plan_graph.derive(brief, vito)
        arch = plan["archetype"]
        plans[wid] = arch
        eq(f"{wid} id", arch.get("id"), want)
        (ok if arch.get("decided") else bad)(
            f"{wid} decided", str(arch))
        # Fixtures predate strategy_shape — phrase path must still decide.
        eq(f"{wid} source phrases", arch.get("source"), "phrases")
    (ok if plans["drain-incremental"]["id"] != plans["drain-burst"]["id"]
     else bad)("pair labels differ",
               f"{plans['drain-incremental']['id']} vs {plans['drain-burst']['id']}")

    # Contrast prose must not let the rejected line win. The incremental brief
    # says "not a single big swing or combo" — those words are burst/aggro
    # phrases under negation, not a vote for them.
    text = " ".join(str((data["runs"]["drain-incremental"]["brief"] or {}).get(k) or "")
                    for k in ("strategy", "theme", "playstyle", "plan", "angle")).lower()
    (ok if "not a single big swing or combo" in text else bad)(
        "fixture still carries the contrast clause", text)


def test_strategy_shape_beats_prose():
    """archetype is stated, not inferred from free-form angle prose.

    Run 4 collapsed both sides onto aggro because the model wrote 'board' and
    'combat' (and hyphenated 'one-turn'). Phrase matching cannot win that race.
    A structured strategy_shape field must decide, and the plan must report
    which path was used.
    """
    print("\nstrategy_shape beats prose")
    # Picker allowlist and chat schema must name the same enum.
    from deckwright import app as _app
    schema_enum = set(
        _app._BRIEF_OBJECT_SCHEMA["properties"]["strategy_shape"]["anyOf"][0]["enum"])
    eq("schema matches picker allowlist",
       schema_enum, set(plan_graph._STRATEGY_SHAPES))
    vito = {
        "name": "Vito, Thorn of the Dusk Rose",
        "cost": "{3}{B}", "cmc": 4,
        "type": "Legendary Creature — Vampire Cleric",
        "text": "Whenever you gain life, target opponent loses that much life.",
    }
    # Character-for-character the run-4 burst angle that elected aggro.
    burst_prose = (
        "a board of vampires that sets up quietly then converts "
        "a big chunk of power into a one-turn life swing"
    )
    incr_prose = (
        "a wide vampire board where nearly every creature, trigger, "
        "and combat step chips away at the table"
    )
    base = {"theme": "vampires", "colors": "WB",
            "mechanics": ["lifegain", "drain", "vampires"]}

    # Without the field, board/combat prose elects aggro — the measured bug.
    naked = plan_graph.derive({**base, "angle": burst_prose}, vito)
    eq("run-4 prose alone elects aggro", naked["archetype"]["id"], "aggro")
    eq("run-4 prose source is phrases", naked["archetype"]["source"], "phrases")

    burst = plan_graph.derive(
        {**base, "angle": burst_prose, "strategy_shape": "burst"}, vito)
    eq("strategy_shape burst wins", burst["archetype"]["id"], "burst")
    eq("burst decided", burst["archetype"]["decided"], True)
    eq("burst source", burst["archetype"]["source"], "strategy_shape")

    incr = plan_graph.derive(
        {**base, "angle": incr_prose, "strategy_shape": "incremental"}, vito)
    eq("strategy_shape incremental wins", incr["archetype"]["id"], "incremental")
    eq("incremental decided", incr["archetype"]["decided"], True)
    eq("incremental source", incr["archetype"]["source"], "strategy_shape")

    # Stated shape outranks disagreeing phrases — no silent phrase override.
    override = plan_graph.derive(
        {**base,
         "angle": "gain life every turn and grind them down steadily",
         "strategy_shape": "burst"},
        vito)
    eq("stated burst beats incremental prose",
       override["archetype"]["id"], "burst")
    eq("override source", override["archetype"]["source"], "strategy_shape")

    # null / missing → phrase fallback only. Do not invent a fourth path.
    fallback = plan_graph.derive(
        {**base, "angle": "win out of nowhere with one big combo turn",
         "strategy_shape": None},
        vito)
    eq("null falls back to phrases", fallback["archetype"]["id"], "burst")
    eq("null source is phrases", fallback["archetype"]["source"], "phrases")

    # Stated but unsupported for this commander — unknown, not a phrase guess.
    unsupported = plan_graph.derive(
        {**base, "angle": "win out of nowhere with one big combo turn",
         "strategy_shape": "control"},
        vito)
    eq("unsupported shape not decided",
       unsupported["archetype"]["decided"], False)
    eq("unsupported source still strategy_shape",
       unsupported["archetype"]["source"], "strategy_shape")


def test_commitment_labels_distinct_and_ui_uses_them():
    """plan rows show each commitment's own label, not role collapse."""
    print("\ncommitment labels stay distinct; UI reads c.label")
    # Same role ("theme") with different ids must keep different labels —
    # the bug rendered both as the brief theme string.
    commits = plan_graph._build_commitments(
        ["tokens", "attacking_triggers", "dies_triggers"])
    by_id = {c["id"]: c for c in commits}
    (ok if "token_density" in by_id and "combat_piece_density" in by_id
     else bad)("multi-floor plan has distinct commitment ids",
               str(sorted(by_id)))
    labels = [c["label"] for c in commits]
    eq("labels unique when ids unique", len(labels), len(set(labels)))
    theme_roles = [c for c in commits if c["counts"]["role"] == "theme"]
    (ok if len(theme_roles) >= 2 else bad)(
        "two theme-role floors (collapse would collide)", str(theme_roles))
    (ok if len({c["label"] for c in theme_roles}) == len(theme_roles)
     else bad)("theme-role floors keep distinct labels",
               str([c["label"] for c in theme_roles]))

    html = (Path(__file__).resolve().parents[1] / "index.html").read_text()
    # The renderer must read the commitment label; the old role→themeLabel /
    # role→"payoffs" collapse is the bug.
    (ok if "c.label" in html and "renderPlanBlock" in html else bad)(
        "renderPlanBlock uses c.label", "")
    (ok if "(role === 'theme' && themeLabel) ? themeLabel" not in html
     else bad)("themeLabel collapse gone", "")
    (ok if "role === 'plan' ? 'payoffs'\n" not in html else bad)(
        "plan→payoffs rename no longer drives the row name", "")


def test_activation_multiplier_links_combat_commander_to_timing():
    """C: Saheeli-like combat trigger ↔ Full Throttle is plan-relevant.

    No combat/turn synergy objects. Thin note on the plan graph only.
    """
    print("\nplan_graph activation_multiplier")
    from deckwright import synergy

    saheeli = {
        "name": "Saheeli, Radiant Creator",
        "type_line": "Legendary Creature — Human Artificer",
        "text": (
            "Whenever you cast an Artificer or artifact spell, you get {E}.\n"
            "At the beginning of combat on your turn, you may pay {E}{E}{E}. "
            "When you do, create a token that's a copy of target permanent "
            "you control, except it's a 5/5 artifact creature in addition to "
            "its other types and has haste. Sacrifice it at the beginning of "
            "the next end step."
        ),
    }
    throttle = {
        "name": "Full Throttle",
        "type_line": "Sorcery",
        "oracle_text": (
            "After this main phase, there are two additional combat phases "
            "followed by an additional main phase."
        ),
    }
    shock = {
        "name": "Shock",
        "type_line": "Instant",
        "oracle_text": "Shock deals 2 damage to any target.",
    }
    oloro = {
        "name": "Oloro, Ageless Ascetic",
        "type_line": "Legendary Creature — Giant Soldier",
        "text": (
            "At the beginning of your upkeep, you gain 2 life.\n"
            "Whenever you gain life, you may pay {1}. If you do, draw a card "
            "and create a 1/1 white Soldier creature token."
        ),
    }

    eq("Saheeli wants activation multiplier",
       plan_graph.wants_activation_multiplier(saheeli), True)
    eq("Full Throttle serves Saheeli plan",
       plan_graph.serves_activation_multiplier(throttle, saheeli), True)
    eq("Shock does not serve activation multiplier",
       plan_graph.serves_activation_multiplier(shock, saheeli), False)
    eq("Oloro does not want combat activation multiplier",
       plan_graph.wants_activation_multiplier(oloro), False)
    eq("Full Throttle not plan-relevant for Oloro",
       plan_graph.serves_activation_multiplier(throttle, oloro), False)

    # Still no material edge — combat is not a synergy object.
    eq("no combat synergy edge Saheeli→Throttle",
       synergy.edges(saheeli, throttle), [])
    eq("no combat synergy edge Throttle→Saheeli",
       synergy.edges(throttle, saheeli), [])
    eq("combat not a produce object on Throttle",
       "combat" in synergy.profile(throttle)["produces"], False)

    brief = {"theme": "energy", "angle": "copy permanents each combat"}
    plan = plan_graph.derive(brief, saheeli)
    am = plan.get("activation_multiplier") or {}
    eq("derive stamps activation_multiplier.relevant",
       am.get("relevant"), True)


def test_payoff_queries_honest():
    """payoff patterns must not alias the wrong family."""
    print("\npayoff queries  — damage_mod ≠ life_math, go_wide needs +")
    life = plan_graph._PAYOFF_QUERIES["life_math"]
    dmg = plan_graph._PAYOFF_QUERIES["damage_modification"]
    eq("damage_mod distinct from life_math", dmg != life, True)
    eq("damage_mod targets instead", 'instead' in dmg.lower(), True)
    wide = plan_graph._PAYOFF_QUERIES["go_wide_payoffs"]
    eq("go_wide_payoffs requires +", 'get +"' in wide, True)


def test_thesis_from_plan_any_shape():
    """Goldfish thesis comes from the plan + the 99 — any plan shape."""
    print("\nthesis_from_plan  — plan conditions, real 99 payoffs")
    from deckwright import synergy
    synergy._SIGNALS = None  # pick up attacks pattern edit in this process

    shapes = [
        {
            "id": "wide_tokens",
            "brief": {
                "theme": "horses and knights",
                "strategy": "attack with it",
                "mechanics": ["tokens", "go wide"],
                "playstyle": "aggro",
            },
            "commander": {
                "name": "Adeline, Resplendent Cathar",
                "type": "Legendary Creature — Human Knight",
                "type_line": "Legendary Creature — Human Knight",
                "cmc": 3,
                "cost": "{1}{W}{W}",
                "text": (
                    "Vigilance\nAdeline, Resplendent Cathar's power is equal "
                    "to the number of creatures you control.\nWhenever you "
                    "attack, for each opponent, create a 1/1 white Human "
                    "creature token that is tapped and attacking that player "
                    "or a planeswalker they control."
                ),
            },
            "deck": [
                {"name": "Knight Exemplar", "role": "theme",
                 "type": "Creature — Human Knight",
                 "text": "Other Knights you control get +1/+1."},
                {"name": "Secure the Wastes", "role": "plan",
                 "type": "Instant",
                 "text": "Create X 1/1 white Warrior creature tokens."},
                {"name": "Sol Ring", "role": "ramp",
                 "type": "Artifact", "text": "{T}: Add {C}{C}."},
            ],
        },
        {
            "id": "lifegain",
            "brief": {
                "theme": "angels",
                "strategy": "gain life every turn",
                "mechanics": ["lifegain"],
                "playstyle": "proactive",
            },
            "commander": {
                "name": "Lyra Dawnbringer",
                "type": "Legendary Creature — Angel",
                "cmc": 5,
                "cost": "{3}{W}{W}",
                "text": (
                    "Flying, first strike, lifelink\n"
                    "Other Angels you control get +1/+1 and have lifelink."
                ),
            },
            "deck": [
                {"name": "Soul Warden", "role": "theme",
                 "type": "Creature — Human Cleric",
                 "text": "Whenever another creature enters, you gain 1 life."},
                {"name": "Authority of the Consuls", "role": "plan",
                 "type": "Enchantment",
                 "text": (
                     "Creatures your opponents control enter tapped.\n"
                     "Whenever a creature an opponent controls enters, "
                     "you gain 1 life."
                 )},
            ],
        },
        {
            "id": "draw_engine",
            "brief": {
                "theme": "artifacts",
                "strategy": "draw cards and accumulate",
                "mechanics": ["card draw"],
                "playstyle": "proactive",
            },
            "commander": {
                "name": "Urza, Lord High Artificer",
                "type": "Legendary Creature — Human Artificer",
                "cmc": 4,
                "cost": "{2}{U}{U}",
                "text": (
                    "When Urza, Lord High Artificer enters, create a "
                    "0/0 colorless Construct artifact creature token with "
                    "'This creature gets +1/+1 for each artifact you "
                    "control.'\n{T}: Add {U} for each artifact you control.\n"
                    "{5}: Shuffle your library, then exile the top card. "
                    "Until end of turn, you may play that card without "
                    "paying its mana cost."
                ),
            },
            "deck": [
                {"name": "Thought Monitor", "role": "theme",
                 "type": "Artifact Creature — Construct",
                 "text": "When Thought Monitor enters, draw two cards."},
                {"name": "The One Ring", "role": "plan",
                 "type": "Legendary Artifact",
                 "text": (
                     "At the beginning of your upkeep, you gain 1 life "
                     "for each burden counter on The One Ring.\n"
                     "{1}, {T}: Put a burden counter on The One Ring, then "
                     "draw a card for each burden counter."
                 )},
            ],
        },
    ]

    for case in shapes:
        plan = plan_graph.derive(case["brief"], case["commander"])
        spec = plan_graph.thesis_from_plan(
            plan, case["deck"], case["commander"])
        (ok if spec is not None else bad)(f"{case['id']} thesis built", "")
        if spec is None:
            continue
        eq(f"{case['id']} source is plan", spec.get("source"), "plan")
        # Whatever this plan decided to measure — goldfish that, not a
        # second invented question. Shape-specific keys vary by commander.
        plan_keys = {k for c in (plan.get("conditions") or [])
                     for k in (c.get("check") or {})}
        thesis_keys = {k for c in spec["conditions"] for k in c["check"]}
        eq(f"{case['id']} thesis keys == plan keys", thesis_keys, plan_keys)
        (ok if plan_keys else bad)(
            f"{case['id']} plan had measurable keys", str(plan_keys))
        deck_names = {c["name"] for c in case["deck"]}
        invented = [n for n in spec["payoff_cards"] if n not in deck_names]
        eq(f"{case['id']} payoffs ⊆ 99", invented, [])
        (ok if spec["payoff_cards"] else bad)(
            f"{case['id']} named payoffs from the 99",
            str(spec["payoff_cards"]))
        scoring = plan_graph.conditions_for_scoring(plan)
        eq(f"{case['id']} thesis conditions == scoring conditions",
           spec["conditions"], scoring)

    # Empty / unmeasurable plan → None (caller may use model thesis).
    eq("no plan → None", plan_graph.thesis_from_plan(None, []), None)
    eq("empty conditions → None",
       plan_graph.thesis_from_plan({"conditions": []}, []), None)

    # Collective "whenever you attack" counts for activation multipliers.
    adeline = shapes[0]["commander"]
    eq("Adeline wants activation multiplier",
       plan_graph.wants_activation_multiplier(adeline), True)
    demand = synergy.demand_profile(adeline)
    (ok if "attacks" in (demand.get("signals") or []) else bad)(
        "Adeline attacks signal", str(demand.get("signals")))


def main() -> int:
    if os.environ.get("RECORD") == "1":
        out = {}
        for case in _load_cases():
            cmdr = case.get("commander")
            if cmdr:
                plan = plan_graph.derive(case["brief"], cmdr)
            else:
                plan = plan_graph.derive_provisional(case["brief"])
            out[case["id"]] = _canonical(plan)
        EXPECTED_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {EXPECTED_PATH}")
        return 0

    test_no_model_imports()
    test_thesis_keys_only()
    test_schema_shape()
    test_commitments_vs_conditions()
    test_hei_bai_commitments_from_demand_profile()
    test_commander_changes_plan()
    test_draw_threshold_floor()
    test_stable_json()
    test_derive_provisional_no_commander()
    test_commander_shapes_the_plan()
    test_archetype_changes_the_pool()
    test_fixture_briefs_discriminate_archetype()
    test_strategy_shape_beats_prose()
    test_commitment_labels_distinct_and_ui_uses_them()
    test_activation_multiplier_links_combat_commander_to_timing()
    test_payoff_queries_honest()
    test_thesis_from_plan_any_shape()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
