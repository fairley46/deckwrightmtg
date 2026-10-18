#!/usr/bin/env python3
"""The CardFacts work Candidate C — CardFacts wired into audit/steer behind flag only.

    python test_candidate_c.py

Default / BLUE / B paths must stay unchanged. Missing CardFacts → unknown,
never empty-as-zero. Bound math stays on strategy_audit.card_bound_ok.
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import os
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, detail=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def bad(label, detail=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r} want {want!r}")


def true(label, cond, detail=""):
    (ok if cond else bad)(label, detail)


BELLO = {
    "name": "Bello, Bard of the Brambles",
    "text": (
        "During your turn, each non-Equipment artifact you control with mana "
        "value 4 or greater is a 4/4 Elemental creature in addition to its "
        "other types and has haste."
    ),
    "type": "Legendary Creature — Raccoon Bard",
    "type_line": "Legendary Creature — Raccoon Bard",
    "cmc": 3,
}

SOL_RING = {
    "name": "Sol Ring",
    "text": "{T}: Add {C}{C}.",
    "oracle_text": "{T}: Add {C}{C}.",
    "type": "Artifact",
    "type_line": "Artifact",
    "cmc": 1,
}

CHARIOT = {
    "name": "Esika's Chariot",
    "text": (
        "When Esika's Chariot enters the battlefield, create two 2/2 green "
        "Cat creature tokens.\nWhenever Esika's Chariot attacks, create a "
        "token that's a copy of target creature token you control."
    ),
    "oracle_text": (
        "When Esika's Chariot enters the battlefield, create two 2/2 green "
        "Cat creature tokens.\nWhenever Esika's Chariot attacks, create a "
        "token that's a copy of target creature token you control."
    ),
    "type": "Legendary Artifact — Vehicle",
    "type_line": "Legendary Artifact — Vehicle",
    "cmc": 4,
}


def _set_candidate(val: str | None):
    if val is None:
        os.environ.pop("DECKWRIGHT_CANDIDATE", None)
    else:
        os.environ["DECKWRIGHT_CANDIDATE"] = val


def _with_candidate(val: str | None):
    """Context-manager style restore."""
    old = os.environ.get("DECKWRIGHT_CANDIDATE")
    _set_candidate(val)
    return old


def _restore(old: str | None):
    _set_candidate(old)


def test_c_enables_clarity_and_steer_b_unchanged():
    print("\nC — clarity/steer on C; off stays inert; B still on")
    from deckwright import strategy_audit as sa
    from deckwright import strategy_steer as ss

    old = _with_candidate("off")
    try:
        eq("off clarity", sa.clarity_enabled(), False)
        eq("off steer", ss.steer_enabled(), False)

        _set_candidate("B")
        eq("B clarity", sa.clarity_enabled(), True)
        eq("B steer", ss.steer_enabled(), True)

        _set_candidate("C")
        eq("C clarity", sa.clarity_enabled(), True)
        eq("C steer", ss.steer_enabled(), True)
        eq("C cardfacts path", ss.cardfacts_steer_enabled(), True)

        _set_candidate("B")
        eq("B cardfacts path off", ss.cardfacts_steer_enabled(), False)
    finally:
        _restore(old)


def test_c_serve_report_unknown_when_store_missing():
    print("\nC — missing CardFacts store → unknown, not zero")
    from deckwright import cardfacts as cf
    from deckwright import commander_strategy as cs
    from deckwright import serve_report as sr

    old = _with_candidate("C")
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = tmp
        try:
            cf.reset_cache()
            strategy = cs.derive(BELLO)
            report = sr.for_card(SOL_RING, strategy, [SOL_RING])
            eq("facts_status unknown", report["cardfacts_status"], "unknown")
            eq("feeds_input None", report["feeds_input"], None)
            eq("consumes_output None", report["consumes_output"], None)
            eq("concept_hits None", report["concept_hits"], None)
            eq("weighted_serve None", report["weighted_serve"], None)
            # Bound math still shared — Sol Ring fails Bello MV floor.
            eq("bound_ok false (shared)", report["bound_ok"], "false")
            true("unknown ≠ empty hits", report["concept_hits"] is None)
        finally:
            if prev_home is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev_home
            cf.reset_cache()
            _restore(old)


def test_c_serve_report_from_baked_facts():
    print("\nC — ServeReport from baked CardFacts + strategy")
    from deckwright import cardfacts as cf
    from deckwright import commander_strategy as cs
    from deckwright import serve_report as sr

    old = _with_candidate("C")
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = tmp
        try:
            store = {
                SOL_RING["name"]: cf.extract(SOL_RING),
                CHARIOT["name"]: cf.extract(CHARIOT),
            }
            cf.write_store(store, path=Path(tmp) / "cardfacts.json")
            cf.reset_cache()
            strategy = cs.derive(BELLO)
            sol = sr.for_card(SOL_RING, strategy, [SOL_RING, CHARIOT])
            cha = sr.for_card(CHARIOT, strategy, [SOL_RING, CHARIOT])

            eq("sol cf status", sol["cardfacts_status"], store["Sol Ring"]["status"])
            eq("sol bound false", sol["bound_ok"], "false")
            eq("chariot bound ok", cha["bound_ok"], "ok")
            true("chariot feeds under floor", cha["feeds_input"] is True)
            true("concept_hits is dict", isinstance(cha["concept_hits"], dict))
            true(
                "soft_combo surfaced unscoreable",
                cha["concept_hits"].get("soft_combo", {}).get("computable") is False,
            )
            true(
                "weighted_serve is number",
                isinstance(cha["weighted_serve"], (int, float)),
            )
            # Missing name → unknown
            miss = sr.for_card(
                {"name": "Not In Store", "type": "Artifact", "cmc": 5},
                strategy,
                [],
            )
            eq("missing card unknown", miss["cardfacts_status"], "unknown")
            eq("missing feeds None", miss["feeds_input"], None)
        finally:
            if prev_home is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev_home
            cf.reset_cache()
            _restore(old)


def test_c_stamp_votes_adds_serve_report_b_does_not():
    print("\nC — stamp_votes ServeReport on C only")
    from deckwright import cardfacts as cf
    from deckwright import commander_strategy as cs
    from deckwright import strategy_audit as sa
    from deckwright import strategy_steer as ss

    deck = [dict(SOL_RING), dict(CHARIOT)]
    out_b = {
        "commander_card": dict(BELLO),
        "deck": [dict(c) for c in deck],
    }
    out_c = {
        "commander_card": dict(BELLO),
        "deck": [dict(c) for c in deck],
    }

    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = tmp
        try:
            cf.write_store(
                {
                    SOL_RING["name"]: cf.extract(SOL_RING),
                    CHARIOT["name"]: cf.extract(CHARIOT),
                },
                path=Path(tmp) / "cardfacts.json",
            )
            cf.reset_cache()

            old = _with_candidate("B")
            try:
                sa.attach_clarity(out_b, enabled=sa.clarity_enabled())
                ss.stamp_votes(out_b)
                true("B has steer_vote", "steer_vote" in out_b["deck"][0])
                true(
                    "B has no serve_report",
                    "serve_report" not in out_b["deck"][0],
                )
                true(
                    "B has no concept_hits",
                    "concept_hits" not in out_b["deck"][0],
                )
            finally:
                _restore(old)

            old = _with_candidate("C")
            try:
                sa.attach_clarity(out_c, enabled=sa.clarity_enabled())
                ss.stamp_votes(out_c)
                card0 = out_c["deck"][0]
                true("C has steer_vote", "steer_vote" in card0)
                true("C has serve_report", isinstance(card0.get("serve_report"), dict))
                true("C serve has concept_hits", "concept_hits" in card0["serve_report"])
                true(
                    "C bound shared with B shape",
                    card0["bound_ok"] in ("ok", "false", "unknown"),
                )
            finally:
                _restore(old)
        finally:
            if prev_home is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev_home
            cf.reset_cache()


def test_c_cf_feeder_steers_when_oracle_silent():
    print("\nC — CF-informed feeder vote when oracle text is silent")
    from deckwright import cardfacts as cf
    from deckwright import commander_strategy as cs
    from deckwright import strategy_steer as ss

    # Lifegain commander; card with no "gain life" oracle but CF outputs life.
    cmdr = {
        "name": "Fixture Lifegain",
        "text": "Whenever you gain life, draw a card.",
        "type": "Legendary Creature — Vampire",
        "cmc": 3,
    }
    silent = {
        "name": "Silent Life Rock",
        "text": "Indestructible",
        "oracle_text": "Indestructible",
        "type": "Artifact",
        "type_line": "Artifact",
        "cmc": 2,
    }
    old = _with_candidate("C")
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = tmp
        try:
            # Hand-bake CF that claims a life output (deterministic store, not model).
            facts = cf.extract(silent)
            facts["outputs"] = [{
                "kind": "life",
                "magnitude": {"mode": "static", "value": 2},
                "once_each_turn": False,
            }]
            facts["status"] = "partial"
            cf.write_store({silent["name"]: facts}, path=Path(tmp) / "cardfacts.json")
            cf.reset_cache()
            strategy = cs.derive(cmdr)
            true("should_steer", ss.should_steer(strategy))
            # Oracle-only path would not see a feeder; CF path should.
            eq("CF feeder vote +1", ss.steer_vote(silent, strategy, []), 1)
            true("feeds_any_input True", ss.feeds_any_input(silent, strategy))
        finally:
            if prev_home is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev_home
            cf.reset_cache()
            _restore(old)


def test_green_c_plist_and_install_script():
    print("\ngreen-c launchd template — optional in public tree")
    plist = REPO / "config" / "com.deckwright.green-c.plist"
    install = REPO / "config" / "install-green-c-launchd.sh"
    if not plist.is_file() or not install.is_file():
        print("  skip — personal launchd templates not in public tree")
        return
    true("green-c plist exists", plist.is_file())
    true("install-green-c-launchd.sh exists", install.is_file())
    text = plist.read_text()
    true("green-c label", "com.deckwright.green-c" in text)
    true("port 8003", "8003" in text)
    true("candidate C", "<string>C</string>" in text)
    true("green-c home dir", ".deckwright-green-c" in text)
    true("green-c log path", "/tmp/deckwright-green-c.log" in text)
    sh = install.read_text()
    true("install-c label", "com.deckwright.green-c" in sh)
    true("install-c refuses primary clone", "worktree" in sh.lower())
    mode = install.stat().st_mode
    true("install-c executable", bool(mode & stat.S_IXUSR))

    root = ET.parse(plist).getroot()
    d = root.find("dict")
    children = list(d)
    env = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "EnvironmentVariables":
            env = children[i + 1]
    true("env dict present", env is not None and env.tag == "dict")
    if env is not None:
        pairs = list(env)
        got = {}
        for i in range(0, len(pairs), 2):
            if pairs[i].tag == "key" and i + 1 < len(pairs):
                got[pairs[i].text] = pairs[i + 1].text
        eq("PORT", got.get("PORT"), "8003")
        eq("CANDIDATE", got.get("DECKWRIGHT_CANDIDATE"), "C")
        eq("ENV", got.get("DECKWRIGHT_ENV"), "green")
        eq("LLM timeout", got.get("DECKWRIGHT_LLM_TIMEOUT"), "600")
        true("PORT is not 8002", got.get("PORT") != "8002")
    true("install-c warns on missing timeout", "DECKWRIGHT_LLM_TIMEOUT" in sh)


def test_harness_contestant_c_registered():
    print("\nC — harness contestants.json registers C as http_build")
    sys.path.insert(0, str(REPO / "scripts"))
    import v2_benchmark as vb  # noqa: E402

    path = REPO / "data" / "benchmarks" / "contestants.json"
    true("contestants.json exists", path.is_file())
    if not path.is_file():
        return
    reg = json.loads(path.read_text(encoding="utf-8"))
    eq("registry consistent", vb.validate_contestant_registry(reg), [])
    ids = [c["id"] for c in reg["contestants"]]
    eq("ids", ids, ["E", "BLUE", "A", "B", "C", "D"])
    c_row = next(c for c in reg["contestants"] if c["id"] == "C")
    eq("C kind", c_row["kind"], "http_build")
    eq("C url_env", c_row.get("url_env"), "DECKWRIGHT_GREEN_C_URL")
    eq("C url_default", c_row.get("url_default"), "http://127.0.0.1:8003")
    b_row = next(c for c in reg["contestants"] if c["id"] == "B")
    eq("B still :8002", b_row.get("url_default"), "http://127.0.0.1:8002")
    blue = next(c for c in reg["contestants"] if c["id"] == "BLUE")
    eq("BLUE still :8001", blue.get("url_default"), "http://127.0.0.1:8001")
    fns = vb.resolve_contestants(reg)
    eq("default resolves enabled", sorted(fns.keys()), vb.enabled_resolveable_ids(reg))


def main() -> int:
    test_c_enables_clarity_and_steer_b_unchanged()
    test_c_serve_report_unknown_when_store_missing()
    test_c_serve_report_from_baked_facts()
    test_c_stamp_votes_adds_serve_report_b_does_not()
    test_c_cf_feeder_steers_when_oracle_silent()
    test_green_c_plist_and_install_script()
    test_harness_contestant_c_registered()
    print()
    if failed:
        print(f"{RED}FAILED{OFF} {failed}  passed {passed}")
        return 1
    print(f"{GREEN}ok{OFF} {passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
