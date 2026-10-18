#!/usr/bin/env python3
"""CardFacts lookup + extract/bake — unknown ≠ zero.

    python test_cardfacts.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, detail=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def bad(label, detail=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r} want {want!r}")


def true(label, cond, detail=""):
    (ok if cond else bad)(label, detail)


# Pattern-text fixtures — not illustration-list name branches.
_SOL_RING_SHAPE = {
    "name": "Fixture Tap Mana Rock",
    "oracle_text": "{T}: Add {C}{C}.",
    "type_line": "Artifact",
    "cmc": 1,
}

_OPPONENT_CAST = {
    "name": "Fixture Opponent Cast Draw",
    "oracle_text": (
        "Whenever an opponent casts a spell, you may draw a card "
        "unless that player pays {1}."
    ),
    "type_line": "Enchantment",
    "cmc": 3,
}

_MV_FLOOR = {
    "name": "Fixture MV Floor Or Greater",
    "oracle_text": (
        "During your turn, each non-Equipment artifact you control with mana "
        "value 4 or greater is a 4/4 Elemental creature in addition to its "
        "other types and has haste."
    ),
    "type_line": "Legendary Creature — Bard",
    "cmc": 3,
}

_MV_CEILING = {
    "name": "Fixture MV Ceiling Or Less",
    "oracle_text": (
        "{3}: Return a permanent card with mana value 3 or less from your "
        "graveyard to the battlefield."
    ),
    "type_line": "Legendary Creature — Nightmare",
    "cmc": 3,
}

_REMINDER_ONLY = {
    "name": "Fixture Reminder Only",
    "oracle_text": "({T}: Add {U}.)",
    "type_line": "Basic Land — Island",
    "cmc": 0,
}

_EMPTY = {
    "name": "Fixture Empty",
    "oracle_text": "",
    "type_line": "Creature — Bear",
    "cmc": 2,
}


def _write_store(home: Path, payload: dict) -> Path:
    path = home / "cardfacts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_missing_store_is_unknown():
    print("\nCardFacts — missing store → unknown (not empty-as-zero)")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            eq("store_status missing", cf.store_status(), "unknown")
            got = cf.lookup("Sol Ring")
            eq("lookup missing store is None", got, None)
            true(
                "unknown is not empty dict",
                got is None,
                "caller must not treat {} as measured empty",
            )
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_unreadable_store_is_unknown():
    print("\nCardFacts — unreadable JSON → unknown")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / "cardfacts.json").write_text("{not-json", encoding="utf-8")
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            eq("store_status bad json", cf.store_status(), "unknown")
            eq("lookup bad json is None", cf.lookup("Sol Ring"), None)
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_missing_card_is_none():
    print("\nCardFacts — card absent from loaded store → None")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        _write_store(home, {
            "version": 1,
            "cards": {
                "Sol Ring": {
                    "name": "Sol Ring",
                    "status": "partial",
                    "inputs": [],
                    "outputs": [{
                        "kind": "mana",
                        "magnitude": {"mode": "static", "value": 2},
                        "once_each_turn": False,
                    }],
                    "bounds": [],
                    "rate": {"supplier": "self", "growth_2p_to_4p": 1},
                }
            },
        })
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            eq("store ok", cf.store_status(), "ok")
            eq("missing card None", cf.lookup("Lightning Bolt"), None)
            true(
                "missing card is not {}",
                cf.lookup("Lightning Bolt") is None,
            )
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_loaded_card_returns_facts():
    print("\nCardFacts — loaded card returns facts dict")
    facts = {
        "name": "Sol Ring",
        "status": "partial",
        "inputs": [],
        "outputs": [{
            "kind": "mana",
            "magnitude": {"mode": "static", "value": 2},
            "once_each_turn": False,
        }],
        "bounds": [],
        "rate": {"supplier": "self", "growth_2p_to_4p": 1},
        "free_value": True,
    }
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        _write_store(home, {"version": 1, "cards": {"Sol Ring": facts}})
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            eq("store ok", cf.store_status(), "ok")
            got = cf.lookup("Sol Ring")
            true("lookup is dict", isinstance(got, dict))
            eq("outputs", got.get("outputs"), facts["outputs"])
            eq("rate", got.get("rate"), facts["rate"])
            eq("optional rollup preserved", got.get("free_value"), True)
            # Case-insensitive name match
            got2 = cf.lookup("sol ring")
            true("case-insensitive hit", isinstance(got2, dict))
            eq("case-insensitive outputs", got2.get("outputs"), facts["outputs"])
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_known_empty_card_is_not_unknown():
    print("\nCardFacts — known empty entry ≠ missing store unknown")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        _write_store(home, {
            "version": 1,
            "cards": {
                "Vanilla Bear": {
                    "inputs": [],
                    "outputs": [],
                    "bounds": [],
                    "rate": {"supplier": "self", "growth_2p_to_4p": 1},
                }
            },
        })
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            got = cf.lookup("Vanilla Bear")
            true("known empty is dict", isinstance(got, dict))
            eq("known empty inputs", got.get("inputs"), [])
            eq("store still ok", cf.store_status(), "ok")
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_empty_name_is_unknown():
    print("\nCardFacts — blank name → None")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        _write_store(home, cf.empty_schema())
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            eq("empty name", cf.lookup(""), None)
            eq("whitespace name", cf.lookup("   "), None)
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_not_imported_by_build_path():
    print("\nCardFacts — app.py / card_score stay free; steer may gate on C")
    for path in (
        ROOT / "deckwright" / "app.py",
        ROOT / "deckwright" / "card_score.py",
    ):
        if not path.is_file():
            ok(f"{path.name} absent on this branch", "skip wire check")
            continue
        text = path.read_text(encoding="utf-8")
        true(
            f"{path.name} does not import cardfacts",
            "import cardfacts" not in text and "from cardfacts" not in text,
        )
    # Candidate C may import cardfacts inside strategy_steer / serve_report
    # behind DECKWRIGHT_CANDIDATE=C — never as a top-level always-on import
    # that would load on BLUE/B.
    steer = ROOT / "deckwright" / "strategy_steer.py"
    if steer.is_file():
        text = steer.read_text(encoding="utf-8")
        # No module-level import line at column 0.
        top_level = [
            ln for ln in text.splitlines()
            if ln.startswith("import cardfacts") or ln.startswith("from cardfacts")
        ]
        true("strategy_steer has no top-level cardfacts import", not top_level)
    serve = ROOT / "deckwright" / "serve_report.py"
    if serve.is_file():
        text = serve.read_text(encoding="utf-8")
        true(
            "serve_report exists for C path",
            "cardfacts" in text,
        )


def test_extract_tap_mana_rock():
    print("\nCardFacts extract — tap-mana rock (pattern text)")
    facts = cf.extract(_SOL_RING_SHAPE)
    eq("status ok (activate + mana)", facts["status"], "ok")
    kinds = [o["kind"] for o in facts["outputs"]]
    true("mana output", "mana" in kinds, str(kinds))
    mana = next(o for o in facts["outputs"] if o["kind"] == "mana")
    eq("mana magnitude static 2", mana["magnitude"], {"mode": "static", "value": 2})
    events = [i["event"] for i in facts["inputs"]]
    true("activate input", "activate" in events, str(events))
    eq("free_value", facts.get("free_value"), True)
    true("unscoreable listed", "soft_combo" in facts["unscoreable"])


def test_extract_when_this_enters():
    print("\nCardFacts extract — when this creature enters (non-greedy etb)")
    card = {
        "name": "Fixture ETB",
        "oracle_text": (
            "When this creature enters, exile target card from an opponent's graveyard."
        ),
        "type_line": "Creature — Zombie",
        "cmc": 3,
    }
    facts = cf.extract(card)
    eq("status ok", facts["status"], "ok")
    events = [i["event"] for i in facts["inputs"]]
    true("etb input", "etb_creature" in events, str(events))


def test_cardfacts_coverage_script():
    print("\nCardFacts — coverage script on tiny jsonl")
    import subprocess
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        path = fh.name
        for card in (_SOL_RING_SHAPE, _OPPONENT_CAST):
            fh.write(json.dumps(card) + "\n")
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "cardfacts_coverage.py"),
             "--reextract", "--jsonl", path],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        eq("coverage exit 0", r.returncode, 0)
        true("reports ok count", "ok=" in r.stdout, r.stdout[:200])
    finally:
        os.unlink(path)


def test_extract_destroy_and_search_spells():
    print("\nCardFacts extract — destroy / search / aura pump")
    destroy = cf.extract({
        "name": "Fixture Destroy",
        "oracle_text": "Destroy target creature.",
        "type_line": "Instant",
        "cmc": 2,
    })
    eq("destroy status ok", destroy["status"], "ok")
    true("destroy has cast input",
         any(i["event"] == "cast_spell" for i in destroy["inputs"]))
    true("destroy has life-ish output",
         any(o["kind"] == "life" for o in destroy["outputs"]))

    search = cf.extract({
        "name": "Fixture Tutor",
        "oracle_text": (
            "Search your library for a Forest or Plains card, reveal it, "
            "put it into your hand, then shuffle."
        ),
        "type_line": "Sorcery",
        "cmc": 1,
    })
    eq("search status ok", search["status"], "ok")
    true("search draw output", any(o["kind"] == "draw" for o in search["outputs"]))

    aura = cf.extract({
        "name": "Fixture Debuff Aura",
        "oracle_text": "Enchant creature\nEnchanted creature gets -3/-0.",
        "type_line": "Enchantment — Aura",
        "cmc": 1,
    })
    eq("aura status ok", aura["status"], "ok")
    true("aura counters output",
         any(o["kind"] == "counters" for o in aura["outputs"]))


def test_extract_flying_only_keyword_partial():
    print("\nCardFacts extract — keyword-only → partial (nonempty coverage)")
    facts = cf.extract({
        "name": "Fixture Flyer",
        "oracle_text": (
            "Flying (This creature can't be blocked except by creatures "
            "with flying or reach.)"
        ),
        "type_line": "Creature — Bird",
        "cmc": 2,
    })
    eq("flying-only partial", facts["status"], "partial")
    true("keyword output present",
         any(o["kind"] in ("keywords", "life") for o in facts["outputs"]))
    true("honesty keywords", "keyword" in (facts.get("honesty") or "").lower())


def test_extract_empty_stays_unread():
    print("\nCardFacts extract — empty oracle stays unread")
    facts = cf.extract({"name": "Fixture Blank", "oracle_text": "", "type_line": "Creature"})
    eq("empty unread", facts["status"], "unread")


def test_nonempty_corpus_coverage_is_complete():
    print("\nCardFacts — 100% nonempty coverage on sample corpus")
    sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
    if not sample.is_file():
        bad("sample corpus missing", str(sample))
        return
    nonempty = ok_or_partial = empty = 0
    for line in sample.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        card = json.loads(line)
        text = (card.get("oracle_text") or card.get("text") or "").strip()
        facts = cf.extract(card)
        if not text:
            empty += 1
            eq(f"empty {card.get('name')} unread", facts["status"], "unread")
            continue
        nonempty += 1
        if facts["status"] in ("ok", "partial"):
            ok_or_partial += 1
        else:
            bad(f"nonempty unread: {card.get('name')}", facts.get("status"))
    if nonempty:
        eq("100% nonempty coverage on sample", ok_or_partial, nonempty)
    true("sample had cards", nonempty + empty > 0)


def test_extract_opponent_cast():
    print("\nCardFacts extract — opponent cast → draw")
    facts = cf.extract(_OPPONENT_CAST)
    eq("status ok", facts["status"], "ok")
    events = [i["event"] for i in facts["inputs"]]
    true("cast_spell input", "cast_spell" in events, str(events))
    eq("opponent supplier rate", facts["rate"]["supplier"], "opponent")
    kinds = [o["kind"] for o in facts["outputs"]]
    true("draw output", "draw" in kinds, str(kinds))


def test_extract_mv_floor_geq():
    print("\nCardFacts extract — MV floor geq (pattern)")
    facts = cf.extract(_MV_FLOOR)
    floors = [b for b in facts["bounds"] if b.get("op") == "geq"]
    true("has geq floor", len(floors) >= 1, str(facts["bounds"]))
    if floors:
        eq("floor value 4", floors[0]["bound"]["value"], 4)
        eq("floor subject artifact_mv", floors[0]["subject"], "artifact_mv")
        eq("floor mode static", floors[0]["bound"]["mode"], "static")


def test_extract_mv_ceiling_leq():
    print("\nCardFacts extract — MV ceiling leq (pattern)")
    facts = cf.extract(_MV_CEILING)
    ceilings = [
        b for b in facts["bounds"]
        if b.get("op") == "leq" and (b.get("bound") or {}).get("mode") == "static"
    ]
    true("has static leq", len(ceilings) >= 1, str(facts["bounds"]))
    if ceilings:
        eq("ceiling value 3", ceilings[0]["bound"]["value"], 3)


def test_extract_unread_is_known_not_none():
    print("\nCardFacts extract — empty unread; reminder/parenthetical still a dict")
    facts = cf.extract(_EMPTY)
    true("empty returns dict", isinstance(facts, dict))
    eq("empty status unread", facts["status"], "unread")
    eq("empty inputs []", facts["inputs"], [])
    eq("empty outputs []", facts["outputs"], [])
    true("empty has honesty", bool(facts.get("honesty")))

    rem = cf.extract(_REMINDER_ONLY)
    true("reminder returns dict", isinstance(rem, dict))
    true(
        "reminder is ok or partial (nonempty coverage)",
        rem["status"] in ("ok", "partial"),
        rem["status"],
    )
    true("reminder not lookup-unknown", rem is not None)


def test_extract_none_card_unread():
    print("\nCardFacts extract — None / missing text → unread")
    facts = cf.extract(None)
    eq("None → unread", facts["status"], "unread")
    true("not None return", facts is not None)


def test_bake_dry_run_and_round_trip():
    print("\nCardFacts bake — dry-run + lookup round-trip")
    sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
    true("sample corpus present", sample.is_file(), str(sample))
    if not sample.is_file():
        return

    # Dry-run must not write.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        out = home / "cardfacts.json"
        env = {**os.environ, "DECKWRIGHT_HOME": str(home)}
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bake_cardfacts.py"),
                "--jsonl", str(sample),
                "--limit", "5",
                "--dry-run",
                "--path", str(out),
                "--progress", "0",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        eq("dry-run exit 0", r.returncode, 0)
        true("dry-run did not write", not out.is_file(), r.stdout + r.stderr)

    # Real bake → lookup round-trip; missing card stays None.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        out = home / "cardfacts.json"
        env = {**os.environ, "DECKWRIGHT_HOME": str(home)}
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bake_cardfacts.py"),
                "--jsonl", str(sample),
                "--limit", "20",
                "--path", str(out),
                "--progress", "0",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        eq("bake exit 0", r.returncode, 0)
        true("wrote store", out.is_file(), r.stdout + r.stderr)
        cf.reset_cache()
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            eq("store ok after bake", cf.store_status(), "ok")
            sol = cf.lookup("Sol Ring")
            true("Sol Ring hit", isinstance(sol, dict), str(sol))
            if sol:
                kinds = [o.get("kind") for o in (sol.get("outputs") or [])]
                true("Sol Ring has mana", "mana" in kinds, str(sol.get("outputs")))
                mag = next(
                    (o.get("magnitude") for o in sol["outputs"] if o.get("kind") == "mana"),
                    None,
                )
                eq("Sol Ring mana mag", mag, {"mode": "static", "value": 2})
            missing = cf.lookup("Card That Was Never Baked")
            eq("missing name is None", missing, None)
            true("missing ≠ {}", missing is None)
        finally:
            cf.reset_cache()
            os.environ.pop("DECKWRIGHT_HOME", None)


def test_bake_missing_corpus_is_unknown():
    print("\nCardFacts bake — missing corpus → exit 2 (unknown, not zero)")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        missing = home / "nope.jsonl"
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bake_cardfacts.py"),
                "--jsonl", str(missing),
                "--dry-run",
            ],
            cwd=str(ROOT),
            env={**os.environ, "DECKWRIGHT_HOME": str(home)},
            capture_output=True,
            text=True,
        )
        eq("missing jsonl exit 2", r.returncode, 2)
        true("stderr says UNKNOWN", "UNKNOWN" in r.stderr, r.stderr)


def test_read_store_and_summarize_unknown():
    print("\nCardFacts read_store — missing / bad → None (unknown ≠ zero)")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        missing = home / "cardfacts.json"
        eq("missing read_store None", cf.read_store(missing), None)
        summary = cf.summarize_cards(None)
        eq("summarize unknown status", summary["status"], "unknown")
        eq("summarize unknown count None", summary["card_count"], None)
        true("unknown count is not 0", summary["card_count"] is None)

        bad = home / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        eq("bad json read_store None", cf.read_store(bad), None)


def test_bake_resume_skips_existing():
    print("\nCardFacts bake — resume skips existing names")
    sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
    true("sample corpus present", sample.is_file(), str(sample))
    if not sample.is_file():
        return

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        out = home / "cardfacts.json"
        env = {**os.environ, "DECKWRIGHT_HOME": str(home)}
        bake = str(ROOT / "scripts" / "bake_cardfacts.py")

        r1 = subprocess.run(
            [
                sys.executable, bake,
                "--jsonl", str(sample),
                "--limit", "3",
                "--path", str(out),
                "--progress", "0",
                "--checkpoint", "0",
                "--replace",
            ],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        eq("first bake exit 0", r1.returncode, 0)
        true("first bake wrote", out.is_file(), r1.stdout + r1.stderr)
        first = json.loads(out.read_text(encoding="utf-8"))
        first_names = set(first["cards"].keys())
        eq("first bake 3 cards", len(first_names), 3)

        r2 = subprocess.run(
            [
                sys.executable, bake,
                "--jsonl", str(sample),
                "--limit", "3",
                "--path", str(out),
                "--progress", "0",
                "--checkpoint", "0",
                # default --skip-existing
            ],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        eq("resume bake exit 0", r2.returncode, 0)
        true(
            "resume reports skipped_existing",
            "skipped_existing=" in r2.stdout,
            r2.stdout,
        )
        # Parse extracted count from the done line.
        extracted = None
        skipped_ex = None
        for line in r2.stdout.splitlines():
            if "done considered=" in line or "[bake_cardfacts] done" in line:
                for part in line.split():
                    if part.startswith("extracted="):
                        extracted = int(part.split("=", 1)[1])
                    if part.startswith("skipped_existing="):
                        skipped_ex = int(part.split("=", 1)[1])
        eq("resume extracted 3 new", extracted, 3)
        eq("resume skipped the first 3", skipped_ex, 3)

        second = json.loads(out.read_text(encoding="utf-8"))
        second_names = set(second["cards"].keys())
        eq("store grew to 6", len(second_names), 6)
        true("first names preserved", first_names <= second_names)


def test_bake_skip_existing_all_done_is_success():
    print("\nCardFacts bake — all names already baked → exit 0, no wipe")
    sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
    if not sample.is_file():
        return
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        out = home / "cardfacts.json"
        env = {**os.environ, "DECKWRIGHT_HOME": str(home)}
        bake = str(ROOT / "scripts" / "bake_cardfacts.py")
        r1 = subprocess.run(
            [
                sys.executable, bake,
                "--jsonl", str(sample),
                "--path", str(out),
                "--progress", "0",
                "--checkpoint", "0",
                "--replace",
            ],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        eq("full sample bake exit 0", r1.returncode, 0)
        before = json.loads(out.read_text(encoding="utf-8"))
        n_before = len(before["cards"])

        r2 = subprocess.run(
            [
                sys.executable, bake,
                "--jsonl", str(sample),
                "--path", str(out),
                "--progress", "0",
                "--checkpoint", "0",
            ],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        eq("noop resume exit 0", r2.returncode, 0)
        after = json.loads(out.read_text(encoding="utf-8"))
        eq("noop resume kept count", len(after["cards"]), n_before)
        true("extracted=0 on noop", "extracted=0" in r2.stdout, r2.stdout)


def test_bake_unreadable_existing_is_unknown():
    print("\nCardFacts bake — unreadable existing store → exit 2")
    sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
    if not sample.is_file():
        return
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        out = home / "cardfacts.json"
        out.write_text("{not-json", encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bake_cardfacts.py"),
                "--jsonl", str(sample),
                "--limit", "1",
                "--path", str(out),
                "--progress", "0",
            ],
            cwd=str(ROOT),
            env={**os.environ, "DECKWRIGHT_HOME": str(home)},
            capture_output=True,
            text=True,
        )
        eq("unreadable store exit 2", r.returncode, 2)
        true("stderr UNKNOWN", "UNKNOWN" in r.stderr, r.stderr)
        # File must not have been silently replaced with a fake-empty success.
        true("still garbage on disk", out.read_text(encoding="utf-8") == "{not-json")


def test_cardfacts_status_script():
    print("\nCardFacts status script — count + lookup + unknown")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        missing = home / "missing.json"
        status_py = str(ROOT / "scripts" / "cardfacts_status.py")
        r0 = subprocess.run(
            [sys.executable, status_py, "--path", str(missing)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        eq("missing status exit 2", r0.returncode, 2)
        true("missing UNKNOWN", "UNKNOWN" in r0.stderr, r0.stderr)

        out = home / "cardfacts.json"
        sample = ROOT / "testdata" / "corpus" / "sample_cards.jsonl"
        if not sample.is_file():
            return
        r_bake = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bake_cardfacts.py"),
                "--jsonl", str(sample),
                "--limit", "5",
                "--path", str(out),
                "--progress", "0",
                "--checkpoint", "0",
                "--replace",
            ],
            cwd=str(ROOT),
            env={**os.environ, "DECKWRIGHT_HOME": str(home)},
            capture_output=True,
            text=True,
        )
        eq("status bake exit 0", r_bake.returncode, 0)
        r1 = subprocess.run(
            [
                sys.executable, status_py,
                "--path", str(out),
                "--lookup", "Sol Ring",
            ],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        eq("status exit 0", r1.returncode, 0)
        true("card_count present", "card_count=5" in r1.stdout, r1.stdout)
        true("lookup hit", "Sol Ring" in r1.stdout and "None" not in r1.stdout.split("lookup=")[-1], r1.stdout)
        r2 = subprocess.run(
            [
                sys.executable, status_py,
                "--path", str(out),
                "--lookup", "Card That Was Never Baked",
            ],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        eq("miss lookup exit 0 (store ok)", r2.returncode, 0)
        true("miss is None", "→ None" in r2.stdout, r2.stdout)


def main() -> int:
    test_missing_store_is_unknown()
    test_unreadable_store_is_unknown()
    test_missing_card_is_none()
    test_loaded_card_returns_facts()
    test_known_empty_card_is_not_unknown()
    test_empty_name_is_unknown()
    test_not_imported_by_build_path()
    test_extract_tap_mana_rock()
    test_extract_when_this_enters()
    test_extract_destroy_and_search_spells()
    test_extract_flying_only_keyword_partial()
    test_extract_empty_stays_unread()
    test_nonempty_corpus_coverage_is_complete()
    test_extract_opponent_cast()
    test_extract_mv_floor_geq()
    test_extract_mv_ceiling_leq()
    test_extract_unread_is_known_not_none()
    test_extract_none_card_unread()
    test_bake_dry_run_and_round_trip()
    test_bake_missing_corpus_is_unknown()
    test_read_store_and_summarize_unknown()
    test_bake_resume_skips_existing()
    test_bake_skip_existing_all_done_is_success()
    test_bake_unreadable_existing_is_unknown()
    test_cardfacts_status_script()
    test_cardfacts_coverage_script()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

