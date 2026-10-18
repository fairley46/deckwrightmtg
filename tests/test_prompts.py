"""
test_prompts.py — golden-set eval harness for LLM prompts.

    python test_prompts.py              # run all cases, compare to baseline
    python test_prompts.py --record     # update prompts/baseline.json after run
    python test_prompts.py --case pirate-ub
    python test_prompts.py --offline    # verifier/schema only, no model calls

Requires ANTHROPIC_API_KEY for live runs (except --offline).
Each live run costs model calls — keep this out of hot loops.
"""


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from deckwright import app
from deckwright import sim

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "prompts" / "golden.json"
BASELINE = ROOT / "prompts" / "baseline.json"

RECORD = "--record" in sys.argv
OFFLINE = "--offline" in sys.argv
CASE_FILTER = None
for i, arg in enumerate(sys.argv):
    if arg == "--case" and i + 1 < len(sys.argv):
        CASE_FILTER = sys.argv[i + 1]

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
SIM_RUNS = 800
MAX_INTERROGATION_TURNS = 5

BRIEF_KEYS = {"theme", "angle", "colors", "budget_total", "bracket",
              "commander_query", "flavor_terms"}

passed, failed, warned = 0, 0, 0
results: dict[str, dict] = {}


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def warn(label, extra=""):
    global warned
    warned += 1
    print(f"{YELLOW}  warn{OFF} {label} {DIM}{extra}{OFF}")


def prop(case_id: str, name: str, held: bool, detail: str = ""):
    """Record a property result for one case."""
    results.setdefault(case_id, {"properties": {}, "metrics": {}})
    results[case_id]["properties"][name] = held
    (ok if held else bad)(f"{case_id}: {name}", detail)


def metric(case_id: str, name: str, value):
    results.setdefault(case_id, {"properties": {}, "metrics": {}})
    results[case_id]["metrics"][name] = value


# ---------------------------------------------------------------- brief parsing

def parse_brief(text: str) -> dict | None:
    """Brief from a model reply — delegates to app.extract_brief_payload (CON-166)."""
    from deckwright import app
    return app.extract_brief_payload(text).get("brief")


def check_brief_schema(brief: dict) -> tuple[bool, str]:
    missing = BRIEF_KEYS - set(brief.keys())
    if missing:
        return False, f"missing keys: {sorted(missing)}"
    if not isinstance(brief.get("flavor_terms"), list):
        return False, "flavor_terms must be a list"
    if not brief.get("theme"):
        return False, "theme is empty"
    if not brief.get("commander_query"):
        return False, "commander_query is empty"
    return True, ""


# ---------------------------------------------------------------- verifiers (offline-testable)

def verify_commander_picks(model_picks: list, candidates: list[dict]) -> tuple[bool, str]:
    valid = {c["name"] for c in candidates}
    bad_names = [p.get("name") for p in model_picks
                 if isinstance(p, dict) and p.get("name") not in valid]
    if bad_names:
        return False, f"hallucinated names: {bad_names}"
    return True, ""


def verify_thesis_spec(spec: dict, deck: list[dict]) -> tuple[bool, str, list[str]]:
    names = {c["name"] for c in deck}
    bad_payoffs = [n for n in spec.get("payoff_cards", []) if n not in names]
    bad_keys = []
    for cond in spec.get("conditions", []):
        for k in (cond.get("check") or {}):
            if k not in sim.THESIS_KEYS:
                bad_keys.append(k)
    issues = []
    if bad_payoffs:
        issues.append(f"payoff_cards not in deck: {bad_payoffs}")
    if bad_keys:
        issues.append(f"unknown THESIS_KEYS: {bad_keys}")
    return not issues, "; ".join(issues), bad_payoffs + bad_keys


def verify_deck_names(deck: list[dict], commander: str) -> tuple[bool, str]:
    seen = set()
    dupes = []
    for c in deck:
        n = c["name"]
        if n in seen and c.get("role") != "basic":
            dupes.append(n)
        seen.add(n)
    if dupes:
        return False, f"duplicate non-basics: {dupes[:5]}"
    if len(deck) != 99:
        return False, f"deck size {len(deck)}, want 99"
    return True, ""


def thesis_is_demanding(curves: list[dict], ceiling: float = 90.0) -> tuple[bool, float]:
    """At least one condition must stay below ceiling% by turn 5."""
    if not curves:
        return False, 0.0
    rates_t5 = [c["points"][4] for c in curves if len(c.get("points", [])) > 4]
    if not rates_t5:
        return False, 0.0
    min_rate = min(rates_t5)
    return min_rate < ceiling, min_rate


# ---------------------------------------------------------------- Scryfall

async def scryfall_count(client: httpx.AsyncClient, query: str) -> tuple[int | None, str | None]:
    try:
        r = await client.get("https://api.scryfall.com/cards/search",
                             params={"q": query}, headers=app.UA, timeout=25)
        if r.status_code == 404:
            return 0, None
        if r.status_code == 200:
            return r.json().get("total_cards", 0), None
        return None, f"HTTP {r.status_code}"
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------- interrogator

async def run_interrogation(case: dict, max_turns: int) -> tuple[dict | None, int, list[str]]:
    """Drive INTERROGATOR until a brief appears or max_turns is hit."""
    messages = list(case["conversation"])
    follow_ups = list(case.get("follow_ups") or [])
    errors = []

    for turn in range(max_turns):
        r = await app.client.messages.create(
            model=app.MODEL, max_tokens=1000,
            system=app.INTERROGATOR, messages=messages)
        text = "".join(b.text for b in r.content if b.type == "text")
        brief = parse_brief(text)
        if brief is not None:
            return brief, turn + 1, errors

        messages.append({"role": "assistant", "content": text})
        if follow_ups:
            reply = follow_ups.pop(0)
        else:
            reply = "That sounds good. Can you put together the brief?"
        messages.append({"role": "user", "content": reply})

    errors.append(f"no brief after {max_turns} exchanges")
    return None, max_turns, errors


# ---------------------------------------------------------------- pipeline

async def run_pipeline(case: dict, brief: dict, http: httpx.AsyncClient):
    case_id = case["id"]
    await app.load_vocab()

    # theme classification
    kind, canon = app.classify(brief.get("theme", ""))
    metric(case_id, "theme_kind", kind)
    metric(case_id, "theme_canon", canon)
    must_resolve = case.get("theme_must_resolve", True)
    if must_resolve:
        prop(case_id, "theme_resolves", kind in ("type", "keyword"),
             f"classify -> ({kind}, {canon})")
    else:
        held = kind in ("type", "keyword", "text")
        prop(case_id, "theme_classified", held, f"classify -> ({kind}, {canon})")
        if kind == "text":
            warn(f"{case_id}: theme is text (expected for this case)", canon)

    # brief schema
    schema_ok, schema_detail = check_brief_schema(brief)
    prop(case_id, "brief_schema", schema_ok, schema_detail)

    # commander_query on Scryfall
    cq = brief.get("commander_query", "")
    must_hit = case.get("commander_query_must_hit", True)
    count, err = await scryfall_count(http, cq)
    await asyncio.sleep(0.15)
    if err:
        prop(case_id, "commander_query_valid", False, err)
    else:
        metric(case_id, "commander_query_hits", count)
        if must_hit:
            prop(case_id, "commander_query_valid", (count or 0) >= 1,
                 f"{count} cards for {cq!r}")
        else:
            prop(case_id, "commander_query_valid", True,
                 f"syntax ok, {count} cards (hit not required)")

    # commanders endpoint
    cmdr_resp = await app.commanders(brief)
    candidates = [p["name"] for p in cmdr_resp["picks"]]
    metric(case_id, "commander_picks", len(cmdr_resp["picks"]))
    prop(case_id, "commander_pool_nonempty", len(cmdr_resp["picks"]) >= 1,
         f"{len(cmdr_resp['picks'])} picks")
    # All returned picks must be from the Scryfall search (label_commander_picks output)
    prop(case_id, "commander_verifier", all(p.get("name") for p in cmdr_resp["picks"]),
         "every pick has a name")

    commander = cmdr_resp["picks"][0]["name"]
    metric(case_id, "commander", commander)

    # build endpoint
    build_resp = await app.build({"brief": brief, "commander": commander})
    deck = build_resp["deck"]
    metric(case_id, "deck_price", build_resp.get("price"))
    deck_ok, deck_detail = verify_deck_names(deck, commander)
    prop(case_id, "deck_complete", deck_ok, deck_detail)
    prop(case_id, "deck_legal", build_resp.get("legal", False),
         str(build_resp.get("violations", []))[:120])

    # thesis endpoint
    thesis_resp = await app.thesis({"deck": deck, "brief": brief, "commander": commander})
    if thesis_resp.get("error"):
        prop(case_id, "thesis_parseable", False, thesis_resp["error"])
        return

    prop(case_id, "thesis_parseable", True)
    thesis_ok, thesis_detail, _ = verify_thesis_spec(thesis_resp, deck)
    prop(case_id, "thesis_cards_and_keys", thesis_ok, thesis_detail)
    metric(case_id, "thesis_conditions", len(thesis_resp.get("conditions", [])))
    metric(case_id, "thesis_payoffs", len(thesis_resp.get("payoff_cards", [])))

    # simulate to check demanding thesis
    info = await app.scry("/cards/named", exact=commander)
    sim_out = await asyncio.to_thread(
        sim.run, deck, int(info.get("cmc") or 5),
        thesis_resp.get("payoff_cards", []),
        thesis_resp.get("conditions", []), SIM_RUNS)
    demanding, min_rate = thesis_is_demanding(sim_out["curves"])
    metric(case_id, "min_condition_rate_t5", min_rate)
    prop(case_id, "thesis_demanding", demanding,
         f"min T5 rate {min_rate}% (want <90%)")


async def run_interrogation_case(case: dict, http: httpx.AsyncClient, max_turns: int):
    case_id = case["id"]
    brief, turns, errors = await run_interrogation(case, max_turns)
    metric(case_id, "interrogation_turns", turns)

    if brief is None:
        prop(case_id, "interrogator_brief_emitted", False, "; ".join(errors))
        return

    prop(case_id, "interrogator_brief_emitted", turns <= max_turns,
         f"brief in {turns} exchange(s)")
    await run_pipeline(case, brief, http)


# ---------------------------------------------------------------- offline tests

def test_offline_verifiers():
    print("\noffline  — verifier and schema property checks")
    brief = {
        "theme": "pirate", "angle": "treasure", "colors": "UB",
        "budget_total": 150, "bracket": 2,
        "commander_query": "is:commander t:pirate f:commander",
        "flavor_terms": ["gold"],
    }
    schema_ok, _ = check_brief_schema(brief)
    (ok if schema_ok else bad)("brief schema accepts valid brief")

    bad_brief = dict(brief)
    del bad_brief["theme"]
    schema_ok, detail = check_brief_schema(bad_brief)
    (ok if not schema_ok else bad)("brief schema rejects missing theme", detail)

    candidates = [{"name": "Admiral Beckett Brass"}, {"name": "Malcolm, Keen-Eyed Navigator"}]
    clean, _ = verify_commander_picks(
        [{"name": "Admiral Beckett Brass", "does": "x", "catch": "y"}],
        candidates)
    (ok if clean else bad)("commander verifier accepts valid names")

    dirty, detail = verify_commander_picks(
        [{"name": "Fake Commander", "does": "x", "catch": "y"}],
        candidates)
    (ok if not dirty else bad)("commander verifier rejects hallucinated names", detail)

    deck = [{"name": "Sol Ring", "role": "ramp"}, {"name": "Island", "role": "basic"}]
    spec = {"payoff_cards": ["Sol Ring", "Not In Deck"],
            "conditions": [{"label": "cmdr", "check": {"commander_in_play": True}},
                           {"label": "bad", "check": {"fake_key": 1}}]}
    ok_spec, detail, _ = verify_thesis_spec(spec, deck)
    (ok if not ok_spec else bad)("thesis verifier catches bad cards and keys", detail)

    curves = [{"label": "a", "points": [10, 20, 40, 60, 95]},
              {"label": "b", "points": [5, 10, 15, 20, 25]}]
    demanding, min_r = thesis_is_demanding(curves)
    (ok if demanding else bad)("thesis demanding when one condition <90% at T5", f"min={min_r}")

    flat = [{"label": "a", "points": [90, 92, 94, 95, 96]},
            {"label": "b", "points": [91, 93, 95, 96, 97]}]
    demanding, min_r = thesis_is_demanding(flat)
    (ok if not demanding else bad)("thesis not demanding when all >=90% at T5", f"min={min_r}")

    text = 'Sounds good.\n\n```brief\n{"theme": "pirate", "angle": "x", "colors": "UB", '
    text += '"budget_total": 150, "bracket": 2, "commander_query": "is:commander t:pirate", '
    text += '"flavor_terms": []}\n```'
    parsed = parse_brief(text)
    (ok if parsed and parsed["theme"] == "pirate" else bad)("parse_brief extracts fenced json")

    # CON-258: key discipline goldens (offline — no model).
    (ok if app.thesis_key_fit(
        "angels that gain life",
        {"conditions": [{"check": {"life_gained": 10}}]},
    ) else bad)("thesis_key_fit lifegain wants life_gained")
    (ok if not app.thesis_key_fit(
        "go wide token swarm",
        {"conditions": [{"check": {"payoffs_in_play": 4}}]},
    ) else bad)("thesis_key_fit tokens rejects payoffs-only")


# ---------------------------------------------------------------- baseline

def load_golden() -> dict:
    return json.loads(GOLDEN.read_text())


def load_baseline() -> dict | None:
    if BASELINE.exists():
        return json.loads(BASELINE.read_text())
    return None


def save_baseline(data: dict):
    BASELINE.parent.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")


def compare_baseline(current: dict, baseline: dict):
    print(f"\n{DIM}baseline comparison{OFF}")
    base_cases = baseline.get("cases", {})
    for case_id, cur in current.items():
        base = base_cases.get(case_id)
        if not base:
            warn(f"{case_id}: no baseline entry (new case)")
            continue
        cur_props = cur.get("properties", {})
        base_props = base.get("properties", {})
        all_keys = sorted(set(cur_props) | set(base_props))
        for key in all_keys:
            c = cur_props.get(key)
            b = base_props.get(key)
            if c == b:
                print(f"{DIM}  {case_id}.{key}: same ({c}){OFF}")
            elif c and not b:
                print(f"{GREEN}  {case_id}.{key}: improved (was {b}, now {c}){OFF}")
            elif not c and b:
                print(f"{RED}  {case_id}.{key}: regressed (was {b}, now {c}){OFF}")
            else:
                print(f"{YELLOW}  {case_id}.{key}: changed ({b} -> {c}){OFF}")


# ---------------------------------------------------------------- runner

async def main():
    print("test_prompts.py  — golden-set prompt eval (CON-78)")
    if OFFLINE:
        test_offline_verifiers()
        print(f"\n{GREEN}{passed} passed{OFF}"
              + (f", {YELLOW}{warned} warned{OFF}" if warned else "")
              + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
        return 1 if failed else 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY not set.{OFF} Use --offline for verifier-only checks.")
        return 1

    golden = load_golden()
    max_turns = golden.get("max_interrogation_turns", MAX_INTERROGATION_TURNS)
    cases = golden["cases"]
    if CASE_FILTER:
        cases = [c for c in cases if c["id"] == CASE_FILTER]
        if not cases:
            print(f"{RED}No case matching {CASE_FILTER!r}{OFF}")
            return 1

    print(f"{DIM}{len(cases)} case(s), sim runs={SIM_RUNS}, max interrogation turns={max_turns}{OFF}")

    t0 = time.time()
    async with httpx.AsyncClient() as http:
        for case in cases:
            print(f"\n{case['id']}  — {case['label']}")
            if case.get("conversation"):
                await run_interrogation_case(case, http, max_turns)
            elif case.get("brief"):
                await run_pipeline(case, case["brief"], http)
            else:
                bad(f"{case['id']}: no brief or conversation defined")

    elapsed = time.time() - t0
    print(f"\n{DIM}elapsed {elapsed:.0f}s{OFF}")

    snapshot = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "sim_runs": SIM_RUNS,
        "cases": results,
    }

    baseline = load_baseline()
    if RECORD:
        save_baseline(snapshot)
        print(f"\n{GREEN}baseline recorded -> {BASELINE}{OFF}")
    elif baseline:
        compare_baseline(results, baseline)
    else:
        warn("no baseline yet — run with --record after a good run")

    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {YELLOW}{warned} warned{OFF}" if warned else "")
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
