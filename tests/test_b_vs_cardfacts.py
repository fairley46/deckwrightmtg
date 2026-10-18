#!/usr/bin/env python3
"""B-stamped deck vs CardFacts compare — offline.

    python test_b_vs_cardfacts.py
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

import importlib.util

from deckwright import cardfacts as cf  # noqa: E402

COMPARE = ROOT / "scripts" / "compare_b_vs_cardfacts.py"
_spec = importlib.util.spec_from_file_location("compare_b_vs_cardfacts", COMPARE)
cmp = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cmp)
FIXTURE = ROOT / "testdata" / "benchmarks" / "bello_b_fixture.json"
FIXTURE_JSONL = ROOT / "testdata" / "benchmarks" / "bello_b_fixture_cards.jsonl"

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


def test_fixture_present():
    print("\nB-vs-C — fixture present")
    true("fixture json", FIXTURE.is_file(), str(FIXTURE))
    true("fixture jsonl", FIXTURE_JSONL.is_file(), str(FIXTURE_JSONL))
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    eq("deck size ~15", len(raw["deck"]), 15)
    true("has commander_strategy", isinstance(raw.get("commander_strategy"), dict))
    stamped = [c for c in raw["deck"] if "bound_ok" in c]
    eq("all cards stamped", len(stamped), 15)
    viol = [c["name"] for c in raw["deck"] if c.get("bound_ok") == "false"]
    true("cheap rocks among B violations", "Sol Ring" in viol)
    true("Esika not B violation", "Esika's Chariot" not in viol)


def test_missing_cf_is_unknown_not_disagree():
    print("\nB-vs-C — missing CardFacts → unknown (not disagree)")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
            report = cmp.compare(payload)
            s = report["summary"]
            eq("store unknown", s["cardfacts_store"], "unknown")
            true(
                "all cards CF-unknown",
                s["cardfacts_unknown_count"] == s["deck_size"],
            )
            eq("no agree without CF", s["b_violations_cf_agrees"], [])
            eq("no disagree without CF", s["b_violations_cf_disagrees"], [])
            true(
                "B violations still listed",
                "Sol Ring" in s["b_violations"],
            )
            true(
                "unknown bucket covers B violations",
                "Sol Ring" in s["b_violations_cf_unknown"],
            )
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_bake_and_agree_on_cheap_rocks():
    print("\nB-vs-C — bake fixture → CF agrees on cheap-rock violations")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        store = home / "cardfacts.json"
        prev = os.environ.get("DECKWRIGHT_HOME")
        os.environ["DECKWRIGHT_HOME"] = str(home)
        try:
            cf.reset_cache()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--deck", str(FIXTURE),
                    "--bake-deck",
                    "--jsonl", str(FIXTURE_JSONL),
                    "--store", str(store),
                    "--json-out", str(home / "report.json"),
                    "--quiet",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            eq("compare exit 0", proc.returncode, 0)
            if proc.returncode != 0:
                bad("stderr", proc.stderr[:500])
                return
            report = json.loads((home / "report.json").read_text(encoding="utf-8"))
            s = report["summary"]
            eq("cf store ok", s["cardfacts_store"], "ok")
            true("Sol Ring agree", "Sol Ring" in s["b_violations_cf_agrees"])
            true(
                "Arcane Signet agree",
                "Arcane Signet" in s["b_violations_cf_agrees"],
            )
            true(
                "Esika not CF violation",
                "Esika's Chariot" not in s["cf_preview_violations"],
            )
            by = {r["name"]: r for r in report["cards"]}
            sol = by["Sol Ring"]["cardfacts"]
            eq("Sol Ring CF status not unknown", sol["status"] != "unknown", True)
            true(
                "Sol Ring has mana output or free_value",
                any(
                    (o.get("kind") == "mana")
                    for o in (sol["outputs"] or [])
                ) or sol.get("free_value") is True,
            )
            # Structural feeder: on-plan MV≥4 artifact
            true(
                "Esika CF preview feeds (structural geq)",
                by["Esika's Chariot"]["cardfacts"]["preview_feeds_input"] is True,
            )
        finally:
            cf.reset_cache()
            if prev is None:
                os.environ.pop("DECKWRIGHT_HOME", None)
            else:
                os.environ["DECKWRIGHT_HOME"] = prev


def test_missing_deck_exits_unknown():
    print("\nB-vs-C — missing deck file → exit 2 (unknown)")
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope.json"
        proc = subprocess.run(
            [sys.executable, str(COMPARE), "--deck", str(missing), "--quiet"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        eq("exit 2", proc.returncode, 2)
        true("stderr says UNKNOWN", "UNKNOWN" in proc.stderr)


def test_bake_missing_jsonl_unknown():
    print("\nB-vs-C — --bake-deck with missing jsonl → exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        proc = subprocess.run(
            [
                sys.executable, str(COMPARE),
                "--deck", str(FIXTURE),
                "--bake-deck",
                "--jsonl", str(home / "missing.jsonl"),
                "--store", str(home / "cardfacts.json"),
                "--quiet",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        eq("exit 2", proc.returncode, 2)
        true("stderr UNKNOWN", "UNKNOWN" in proc.stderr)


def main() -> int:
    print("test_b_vs_cardfacts.py — B vs CardFacts compare")
    test_fixture_present()
    test_missing_cf_is_unknown_not_disagree()
    test_bake_and_agree_on_cheap_rocks()
    test_missing_deck_exits_unknown()
    test_bake_missing_jsonl_unknown()
    print()
    if failed:
        print(f"{RED}FAILED{OFF} {failed}  passed {passed}")
        return 1
    print(f"{GREEN}OK{OFF} {passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
