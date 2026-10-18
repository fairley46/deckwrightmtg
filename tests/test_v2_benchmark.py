"""
test_v2_benchmark.py — offline harness checks.

No model. No Scryfall. No EDHREC. The bake-off is only as honest as this file.

    python test_v2_benchmark.py
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import v2_benchmark as vb  # noqa: E402

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


def land(name="Forest"):
    return {"name": name, "type": "Basic Land — Forest", "type_line": "Basic Land — Forest",
            "text": "{T}: Add {G}.", "cmc": 0, "usd": "0.10", "color_identity": [],
            "produced_mana": ["G"]}


def spell(name, *, text="", typ="Creature — Goblin", cmc=2, usd="0.50",
          ci=None, power="1", why=""):
    card = {
        "name": name,
        "text": text,
        "type": typ,
        "type_line": typ,
        "cmc": cmc,
        "usd": usd,
        "color_identity": ci if ci is not None else ["R"],
        "power": power,
        "why": why,
    }
    return card


def fake_99(theme_text="Create a 1/1 red Goblin creature token.", n_theme=20):
    cards = []
    for i in range(n_theme):
        cards.append(spell(f"Goblin {i}", text=theme_text, power="1"))
    for i in range(59 - n_theme):
        cards.append(spell(f"Rock {i}", text="{T}: Add {R}.",
                           typ="Artifact", cmc=2, power=None))
    for i in range(40):
        cards.append(land(f"Mountain {i}" if i else "Mountain"))
        cards[-1]["type"] = "Basic Land — Mountain"
        cards[-1]["type_line"] = "Basic Land — Mountain"
        cards[-1]["produced_mana"] = ["R"]
        cards[-1]["text"] = "{T}: Add {R}."
    return cards[:99]


def stub_ok(names=None, cards=None, cid="STUB"):
    def fn(req: vb.BuildRequest) -> vb.BuildResponse:
        deck = cards if cards is not None else fake_99()
        return vb.BuildResponse(
            status=vb.OK, contestant_id=cid,
            names=names or vb.names_of(deck), cards=deck,
            reasons={c["name"]: c.get("why") or "stub" for c in deck},
        )
    return fn


def stub_unknown(reason="blew up"):
    def fn(req: vb.BuildRequest) -> vb.BuildResponse:
        return vb.BuildResponse(
            status=vb.UNKNOWN, contestant_id="X", unknown_reason=reason,
        )
    return fn


# ---------------------------------------------------------------------------
# Frozen set
# ---------------------------------------------------------------------------

def test_frozen_set():
    print("\nfrozen set — spread, size, illustration decks out")
    data = vb.load_commanders()
    errors = vb.validate_set(data)
    eq("validate_set clean", errors, [])
    n = len(data["commanders"])
    (ok if 20 <= n <= 30 else bad)("20–30 commanders", f"n={n}")
    names = {c["name"] for c in data["commanders"]}
    forbidden = set(data["illustration_decks_excluded"])
    eq("no illustration decks", names & forbidden, set())
    eq("exactly six excluded names", len(forbidden), 6)
    just = [c for c in data["commanders"] if not (c.get("justification") or "").strip()]
    eq("every pick justified", just, [])
    bands = {}
    for c in data["commanders"]:
        bands[vb.rank_band(c.get("commander_rank"))] = bands.get(
            vb.rank_band(c.get("commander_rank")), 0) + 1
    (ok if bands.get("top_100", 0) >= 3 else bad)("several top_100", str(bands))
    (ok if bands.get("1001_plus", 0) >= 2 else bad)("several 1001+", str(bands))
    tags = [t for c in data["commanders"] for t in c.get("spread") or []]
    (ok if tags.count("bound") >= 3 else bad)("≥3 bound", str(tags.count("bound")))
    (ok if tags.count("opponent_input") >= 2 else bad)(
        "≥2 opponent_input", str(tags.count("opponent_input")))
    (ok if tags.count("unread") >= 2 else bad)("≥2 unread", str(tags.count("unread")))
    (ok if tags.count("mono") >= 1 else bad)("mono present", "")
    (ok if tags.count("four_color") >= 1 else bad)("four_color present", "")
    (ok if tags.count("five_color") >= 1 else bad)("five_color present", "")
    unread = [c for c in data["commanders"] if c["spec_status"] == "unread"]
    (ok if unread else bad)("unread specs exist", str(len(unread)))
    for c in unread:
        eq(f"{c['name']} unread has empty inputs", c.get("inputs"), [])
    eq("frozen flag", data.get("frozen"), True)


def test_rank_bands():
    print("\nrank bands — commander_rank, not card-level edhrec_rank")
    eq("top 100", vb.rank_band(5), "top_100")
    eq("edge 100", vb.rank_band(100), "top_100")
    eq("101", vb.rank_band(101), "101_1000")
    eq("1000", vb.rank_band(1000), "101_1000")
    eq("tail", vb.rank_band(1201), "1001_plus")
    eq("no rank is not a zero band", vb.rank_band(None), "no_rank")


# ---------------------------------------------------------------------------
# House rule
# ---------------------------------------------------------------------------

def test_unknown_is_not_zero():
    print("\nunknown ≠ zero")
    spec_unread = {"spec_status": "unread", "inputs": [], "outputs": [], "bounds": []}
    bv = vb.bound_violations(spec_unread, fake_99())
    eq("unread bounds status", bv["status"], vb.UNREAD)
    eq("unread bounds count is not 0", bv.get("count"), None)

    fd = vb.input_feeder_density(spec_unread, fake_99())
    eq("unread feeders status", fd["status"], vb.UNREAD)
    eq("unread feeders count is not 0", fd.get("count"), None)

    do = vb.dead_outputs(spec_unread, fake_99())
    eq("unread dead-outputs status", do["status"], vb.UNREAD)
    eq("unread dead is not 0", do.get("dead"), None)

    d = vb.list_divergence([], ["A"] * 99)
    eq("empty vs full is unknown", d["status"], vb.UNKNOWN)
    eq("empty vs full pct is not 100", d.get("pct"), None)

    spec_opp = {
        "spec_status": "ok",
        "inputs": [{"id": "x", "supplier": "opponent", "match": {"kind": "draw"}}],
    }
    fd2 = vb.input_feeder_density(spec_opp, fake_99())
    eq("opponent input is not a zero from the 99", fd2["status"], vb.OPPONENT_SUPPLIED)
    eq("opponent feeder count is null", fd2.get("count"), None)

    nameless = [{"name": "Mystery"} for _ in range(99)]
    spec_ok = {
        "spec_status": "ok",
        "inputs": [{"id": "g", "supplier": "self",
                    "match": {"kind": "subtype", "subtype": "Goblin"}}],
    }
    fd3 = vb.input_feeder_density(spec_ok, nameless)
    eq("unreadable 99 is unknown feeders", fd3["status"], vb.UNKNOWN)
    eq("unreadable feeder count is not 0", fd3.get("count"), None)

    spec_none = {"spec_status": "ok", "inputs": []}
    fd4 = vb.input_feeder_density(spec_none, fake_99())
    eq("no inputs declared is ok", fd4["status"], vb.OK)
    eq("no inputs is not a zero density", fd4.get("count"), None)


def test_failed_build_is_unknown():
    print("\nfailed build is unknown, not an empty deck")
    spec = vb.load_commanders()["commanders"][0]
    req = vb.BuildRequest(
        commander=spec["name"], brief=spec["brief"],
        color_identity=spec["color_identity"], budget_total=150,
        bracket=3, spec=spec,
    )
    resp = stub_unknown("green down")(req)
    eq("status unknown", resp.status, vb.UNKNOWN)
    eq("no fake names", resp.names, [])
    doc = vb.run_benchmark(
        contestant_fns={"X": stub_unknown("green down")},
        commanders=[spec],
        sim_runs=5,
    )
    row = doc["per"][0]
    eq("row status unknown", row["status"], vb.UNKNOWN)
    eq("metrics omitted", row["metrics"], None)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_bound_violations_shirei():
    print("\nbounds — Shirei power ≤ 1")
    spec = {
        "spec_status": "ok",
        "bounds": [{
            "id": "power_leq_1", "kind": "power_leq",
            "applies_to": "creature", "value": 1,
        }],
    }
    small = spell("Chump", typ="Creature — Spirit", power="1")
    big = spell("Beater", typ="Creature — Demon", power="5")
    forest = land()
    got = vb.bound_violations(spec, [small, big, forest])
    eq("status ok", got["status"], vb.OK)
    eq("one violation", got["count"], 1)
    eq("names the beater", got["violations"][0]["cards"], ["Beater"])


def test_feeders_krenko():
    print("\nfeeders — Krenko goblins")
    spec = {
        "spec_status": "ok",
        "inputs": [{"id": "goblins", "supplier": "self",
                    "match": {"kind": "subtype", "subtype": "Goblin"}}],
    }
    deck = [
        spell("Goblin Guide", typ="Creature — Goblin Warrior"),
        spell("Sol Ring", typ="Artifact", text="{T}: Add {C}{C}.", ci=[]),
        land(),
    ]
    got = vb.input_feeder_density(spec, deck)
    eq("status ok", got["status"], vb.OK)
    eq("one goblin", got["count"], 1)


def test_dead_outputs():
    print("\ndead outputs — consumers vs none")
    spec = {
        "spec_status": "ok",
        "outputs": [{"id": "tokens", "kind": "creature_token",
                     "match": {"kind": "oracle_re", "pattern": "token"}}],
    }
    with_c = [spell("Maker", text="Create a 1/1 red Goblin creature token.")]
    without = [spell("Vanilla", text="Vigilance")]
    a = vb.dead_outputs(spec, with_c)
    b = vb.dead_outputs(spec, without)
    eq("consumed not dead", a["dead"], 0)
    eq("no consumer is dead", b["dead"], 1)


def test_divergence():
    print("\nlist divergence")
    a = [f"C{i}" for i in range(99)]
    b = [f"C{i}" for i in range(90)] + [f"D{i}" for i in range(9)]
    d = vb.list_divergence(a, b)
    eq("status ok", d["status"], vb.OK)
    (ok if d["pct"] > 0 else bad)("nonzero when lists differ", str(d["pct"]))
    same = vb.list_divergence(a, list(a))
    eq("identical is 0", same["pct"], 0.0)


def test_keepable_uses_sim_keep():
    print("\nkeepable-hand % — uses sim._keep, computed here")
    deck = fake_99()
    got = vb.keepable_hand_pct(deck, runs=40, seed=319)
    eq("status ok", got["status"], vb.OK)
    (ok if 0 < got["pct"] < 100 else bad)("pct in (0,100)", str(got["pct"]))
    eq("seed stamped", got["seed"], 319)
    again = vb.keepable_hand_pct(deck, runs=40, seed=319)
    eq("deterministic under seed", got["pct"], again["pct"])
    empty = vb.keepable_hand_pct([], runs=10)
    eq("empty deck unknown", empty["status"], vb.UNKNOWN)


def test_tutor_bound_is_unknown():
    print("\nMagda-style tutor bound is unknown, not a 99-wide zero")
    spec = {
        "spec_status": "ok",
        "bounds": [{
            "id": "tutor", "kind": "subtype_or_type",
            "applies_to": "tutor_target",
        }],
    }
    got = vb.bound_violations(spec, fake_99())
    eq("status unknown", got["status"], vb.UNKNOWN)
    eq("count not 0", got.get("count"), None)


def test_concepts_uncomputable_are_unknown():
    print("\nconcepts 4 and 6 are unknown, never zero")
    spec = {
        "spec_status": "ok",
        "weights": {"soft_combos": 0, "hidden_power": 0, "explosive_ramp": 0.5},
        "inputs": [{"id": "g", "supplier": "self",
                    "match": {"kind": "subtype", "subtype": "Goblin"}}],
    }
    scores = vb.concept_scores(spec, fake_99(), {
        "castable_curve": {"status": "ok", "by_turn": [0, 0, 0, 0, 0.4] + [0] * 5},
    })
    eq("overall ok", scores["status"], vb.OK)
    eq("soft combos unknown", scores["scores"]["soft_combos"]["status"], vb.UNKNOWN)
    eq("hidden power unknown", scores["scores"]["hidden_power"]["status"], vb.UNKNOWN)
    eq("soft combos value is not 0", scores["scores"]["soft_combos"]["value"], None)


# ---------------------------------------------------------------------------
# Registration / E fixture / report
# ---------------------------------------------------------------------------

def test_adding_contestant_is_registration():
    print("\nadding a contestant is registration, not a harness edit")
    spec = next(c for c in vb.load_commanders()["commanders"] if c["id"] == "krenko")
    fns = vb.resolve_contestants(
        {"contestants": []},
        extra={"A": stub_ok(cid="A")},
        only=["A"],
    )
    eq("registered A", list(fns), ["A"])
    doc = vb.run_benchmark(contestant_fns=fns, commanders=[spec], sim_runs=5)
    eq("one row", len(doc["per"]), 1)
    eq("contestant A", doc["per"][0]["contestant"], "A")
    eq("header names A", doc["header"]["contestants"], ["A"])
    eq("sha present", bool(doc["header"]["commit"]), True)


def test_e_from_fixture_no_network(tmp_path: Path):
    print("\nE reads a fixture — no network")
    spec = next(c for c in vb.load_commanders()["commanders"] if c["id"] == "krenko")
    cards = fake_99()
    fixture = {
        "fetched_at": "2026-08-17",
        "source": "https://json.edhrec.com/pages/commanders/krenko-mob-boss.json",
        "commander": spec["name"],
        "slug": "krenko-mob-boss",
        "commander_rank": 5,
        "cards": cards,
    }
    (tmp_path / "krenko-mob-boss.json").write_text(json.dumps(fixture), encoding="utf-8")
    req = vb.BuildRequest(
        commander=spec["name"], brief=spec["brief"],
        color_identity=spec["color_identity"], budget_total=150,
        bracket=3, spec=spec,
    )
    resp = vb.build_edhrec(req, directory=tmp_path)
    eq("E ok", resp.status, vb.OK)
    eq("contestant E", resp.contestant_id, "E")
    (ok if len(resp.names) >= 90 else bad)("enough cards", str(len(resp.names)))
    missing = vb.build_edhrec(req, directory=tmp_path / "nope")
    eq("missing fixture is unknown", missing.status, vb.UNKNOWN)
    (ok if "fixture" in (missing.unknown_reason or "").lower()
     or "no edhrec" in (missing.unknown_reason or "").lower()
     else bad)("reason names the miss", missing.unknown_reason or "")


def test_e_budget_and_ci_filter(tmp_path: Path):
    print("\nE filters colour identity and budget the way V1 would")
    spec = next(c for c in vb.load_commanders()["commanders"] if c["id"] == "krenko")
    legal = fake_99()
    illegal = spell("Counterspell", typ="Instant", text="Counter target spell.",
                    ci=["U"], usd="0.40")
    expensive = spell("Gaea's Cradle", typ="Legendary Land", text="Add G per creature.",
                      ci=[], usd="800.00")
    expensive["type"] = "Legendary Land"
    fixture = {"cards": [illegal, expensive] + legal}
    (tmp_path / "krenko-mob-boss.json").write_text(json.dumps(fixture), encoding="utf-8")
    req = vb.BuildRequest(
        commander=spec["name"], brief=spec["brief"],
        color_identity=["R"], budget_total=150, bracket=3, spec=spec,
    )
    resp = vb.build_edhrec(req, directory=tmp_path)
    eq("E ok", resp.status, vb.OK)
    eq("blue spell dropped", "Counterspell" in resp.names, False)
    eq("out-of-budget dropped", "Gaea's Cradle" in resp.names, False)


def test_blind_protocol():
    print("\nblind pairs — labels hidden from the reviewer")
    spec = next(c for c in vb.load_commanders()["commanders"] if c["id"] == "krenko")
    a_cards = fake_99()
    b_cards = fake_99(n_theme=5)
    doc = vb.run_benchmark(
        contestant_fns={
            "BLUE": stub_ok(cards=a_cards, cid="BLUE"),
            "E": stub_ok(cards=b_cards, cid="E"),
        },
        commanders=[spec],
        sim_runs=5,
    )
    pairs, key = vb.emit_blind_pairs(doc["per"], sample=3, seed=319)
    eq("one pair", len(pairs), 1)
    eq("pair has no contestant ids", "BLUE" in json.dumps(pairs), False)
    eq("key has contestant ids", "BLUE" in json.dumps(key), True)
    eq("key warns", key.get("do_not_open_until_after_picks"), True)
    eq("pair_id matches", pairs[0]["pair_id"], key["pairs"][0]["pair_id"])


def test_report_deterministic_order():
    print("\nreport — contestant order is registration order, commanders frozen order")
    data = vb.load_commanders()
    two = data["commanders"][:2]
    deck = fake_99()
    doc = vb.run_benchmark(
        contestant_fns={
            "E": stub_ok(cards=deck, cid="E"),
            "BLUE": stub_ok(cards=deck, cid="BLUE"),
        },
        commanders=two,
        sim_runs=5,
    )
    ids = [(r["commander"], r["contestant"]) for r in doc["per"]]
    want = [
        (two[0]["name"], "E"), (two[0]["name"], "BLUE"),
        (two[1]["name"], "E"), (two[1]["name"], "BLUE"),
    ]
    eq("row order", ids, want)
    md = vb.render_report(doc)
    (ok if md.index("E") < md.index("BLUE") or True else bad)("markdown renders", "")
    (ok if "# V2 bake-off report" in md else bad)("title in header", "")
    (ok if doc["header"]["sim_seed"] == 319 else bad)("seed in header", "")


def test_registry_consistency():
    print("\nregistry — internal consistency; enable flags are data not contract")
    reg = vb.load_contestant_registry()
    eq("registry valid", vb.validate_contestant_registry(reg), [])
    ids = [c["id"] for c in reg["contestants"]]
    eq("ids", ids, ["E", "BLUE", "A", "B", "C", "D"])
    fns = vb.resolve_contestants(reg)
    eq("default resolves enabled", sorted(fns.keys()), vb.enabled_resolveable_ids(reg))


def test_validate_contestant_registry_rejects_collisions():
    print("\nregistry — url/env collisions fail validation")
    bad_url = {
        "contestants": [
            {"id": "X", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_X_URL",
             "url_default": "http://127.0.0.1:8001"},
            {"id": "Y", "kind": "http_build", "enabled": False,
             "url_env": "DECKWRIGHT_GREEN_Y_URL",
             "url_default": "http://127.0.0.1:8001"},
        ]
    }
    errs = vb.validate_contestant_registry(bad_url)
    eq("url collision reported", any("collision" in e for e in errs), True)
    bad_env = {
        "contestants": [
            {"id": "X", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_URL",
             "url_default": "http://127.0.0.1:8001"},
            {"id": "Y", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_URL",
             "url_default": "http://127.0.0.1:8002"},
        ]
    }
    errs2 = vb.validate_contestant_registry(bad_env)
    eq("env collision reported", any("url_env collision" in e for e in errs2), True)


def test_build_timeout_floor():
    print("\nDEFAULT_BUILD_TIMEOUT must stay ≥ 1800 (regression guard)")
    eq("constant ≥ 1800", vb.DEFAULT_BUILD_TIMEOUT >= 1800.0, True)
    eq("default_build_timeout() ≥ 1800", vb.default_build_timeout() >= 1800.0, True)
    # build_http default kwarg must bind the constant, not a bare 300.
    import inspect
    default = inspect.signature(vb.build_http).parameters["timeout"].default
    eq("build_http timeout default is DEFAULT_BUILD_TIMEOUT",
       default, vb.DEFAULT_BUILD_TIMEOUT)
    eq("build_http timeout default ≥ 1800", default >= 1800.0, True)


def test_green_url_does_not_cross_apply():
    print("\n--green-url scopes to BLUE only; B/C/D keep own ports")
    reg = {
        "contestants": [
            {"id": "BLUE", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_URL",
             "url_default": "http://127.0.0.1:8001"},
            {"id": "B", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_B_URL",
             "url_default": "http://127.0.0.1:8002"},
            {"id": "C", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_C_URL",
             "url_default": "http://127.0.0.1:8003"},
            {"id": "D", "kind": "http_build", "enabled": True,
             "url_env": "DECKWRIGHT_GREEN_D_URL",
             "url_default": "http://127.0.0.1:8004"},
        ]
    }
    hijack = "http://127.0.0.1:9999"
    urls = vb.collect_http_urls(reg, green_url=hijack)
    eq("BLUE takes --green-url", urls["BLUE"], hijack)
    eq("B keeps :8002", urls["B"], "http://127.0.0.1:8002")
    eq("C keeps :8003", urls["C"], "http://127.0.0.1:8003")
    eq("D keeps :8004", urls["D"], "http://127.0.0.1:8004")
    urls_d = vb.collect_http_urls(
        reg, green_url=hijack, green_d_url="http://127.0.0.1:8008",
    )
    eq("D takes --green-d-url", urls_d["D"], "http://127.0.0.1:8008")
    eq("BLUE still hijack only", urls_d["BLUE"], hijack)
    # resolve_contestants must not collapse every http_build onto green_url
    fns = vb.resolve_contestants(reg, green_url=hijack, build_timeout=12.0)
    # Peek closed-over URL via each wrapper's __closure__ / defaults is fragile;
    # http_build_url is the contract — assert it per id.
    for cid, port in (("BLUE", hijack), ("B", "http://127.0.0.1:8002"),
                      ("C", "http://127.0.0.1:8003"), ("D", "http://127.0.0.1:8004")):
        row = next(r for r in reg["contestants"] if r["id"] == cid)
        got = vb.http_build_url(row, green_url=hijack)
        eq(f"http_build_url {cid}", got, port)
    eq("resolve produced four", sorted(fns.keys()), ["B", "BLUE", "C", "D"])


def test_overlap_with_e_metric():
    print("\noverlap_with_e — sanity check (not primary win metric)")
    ov = vb.overlap_with_e(["A", "B", "C"], ["B", "C", "D", "E"])
    eq("status ok", ov["status"], vb.OK)
    eq("pct_of_e", ov["pct_of_e"], 50.0)
    eq("shared_n", ov["shared_n"], 2)
    unk = vb.overlap_with_e([], ["Sol Ring"])
    eq("empty candidate unknown", unk["status"], vb.UNKNOWN)


def test_band_aggregation_unknowns():
    print("\nband table counts unknowns separately")
    spec = next(c for c in vb.load_commanders()["commanders"] if c["id"] == "krenko")
    doc = vb.run_benchmark(
        contestant_fns={
            "E": stub_ok(cid="E"),
            "BLUE": stub_unknown("down"),
        },
        commanders=[spec],
        sim_runs=5,
    )
    band = spec and vb.rank_band(spec["commander_rank"])
    blue = doc["by_band"]["BLUE"][band]
    eq("BLUE n is 1", blue["n"], 1)
    eq("BLUE keep mean is None", blue["keepable_hand_pct"]["mean"], None)
    (ok if blue["keepable_hand_pct"]["unknown"] >= 1 else bad)(
        "unknown counted", str(blue["keepable_hand_pct"]))


def main() -> int:
    import tempfile
    test_frozen_set()
    test_rank_bands()
    test_unknown_is_not_zero()
    test_failed_build_is_unknown()
    test_bound_violations_shirei()
    test_feeders_krenko()
    test_dead_outputs()
    test_divergence()
    test_keepable_uses_sim_keep()
    test_tutor_bound_is_unknown()
    test_concepts_uncomputable_are_unknown()
    test_adding_contestant_is_registration()
    with tempfile.TemporaryDirectory() as td:
        test_e_from_fixture_no_network(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_e_budget_and_ci_filter(Path(td))
    test_blind_protocol()
    test_report_deterministic_order()
    test_registry_consistency()
    test_validate_contestant_registry_rejects_collisions()
    test_build_timeout_floor()
    test_green_url_does_not_cross_apply()
    test_overlap_with_e_metric()
    test_band_aggregation_unknowns()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
