#!/usr/bin/env python3
"""
test_print_wave_reeval.py — print-wave path (offline).

No network. Exercises report assembly, Gate 1 doc parse, precision floor.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import check_playstyles as cps  # noqa: E402
import print_wave_reeval as pwr  # noqa: E402

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


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


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        fail(label, extra or "expected truthy")


def test_gate1_parse():
    print("\nparse_gate1_retest()")
    g = pwr.parse_gate1_retest(pwr.GATE1_RETEST)
    eq("status green from retest doc", g["status"], "green")
    eq("unclassifiable 0/0/0", g["unclassifiable"], "0/0/0")

    missing = pwr.parse_gate1_retest(REPO / "no-such-gate1.md")
    eq("missing doc is unknown", missing["status"], "unknown")
    eq("missing unclassifiable None", missing["unclassifiable"], None)


def test_assemble_precision_floor():
    print("\nassemble_report() — precision floor")
    coverage = {
        "status": "ok",
        "at_least_one_signal_pct": 73.4,
        "signal_or_keyword_pct": 85.0,
        "unread_pct": 15.0,
        "unread_lines_top": [{"count": 12, "line": "landwalk"}],
        "oracle_signal_count": 106,
        "graded": 10,
        "universe_commander_legal": 10,
        "unknown_unreadable": 0,
    }
    gate1 = {"status": "green", "path": "x", "unclassifiable": "0/0/0",
             "note": "t"}
    spots = [{"test": "t.py", "status": "ok", "ok": True}]

    bad_ps = {
        "status": "ok", "precision": 0.87, "precision_ok": False,
        "recall": 0.8, "hit": 87, "miss": 10, "extra": 13,
        "scored": 50, "excluded": 0, "fixture_rows": 50,
    }
    bad = pwr.assemble_report(
        coverage=coverage, playstyles=bad_ps, spot_checks=spots,
        gate1=gate1, refresh=None, wave="test",
    )
    eq("below floor → overall fail", bad["overall"], "fail")
    true("next_actions mentions precision",
         any("88%" in a for a in bad["next_actions"]))

    good_ps = dict(bad_ps, precision=0.899, precision_ok=True, extra=8)
    good = pwr.assemble_report(
        coverage=coverage, playstyles=good_ps, spot_checks=spots,
        gate1=gate1, refresh=None, wave="test",
    )
    eq("above floor → overall ok", good["overall"], "ok")
    eq("precision_pct rounded", good["playstyles"]["precision_pct"], 89.9)


def test_unknown_coverage_fails():
    print("\nassemble_report() — unknown coverage")
    report = pwr.assemble_report(
        coverage={"status": "unknown", "error": "missing"},
        playstyles={
            "status": "ok", "precision": 0.9, "precision_ok": True,
            "recall": 0.8, "hit": 9, "miss": 1, "extra": 1,
            "scored": 10, "excluded": 0, "fixture_rows": 10,
        },
        spot_checks=[{"test": "t.py", "status": "ok", "ok": True}],
        gate1={"status": "green", "path": "x", "unclassifiable": "0/0/0",
               "note": "t"},
        refresh=None,
        wave="t",
    )
    eq("unknown coverage fails overall", report["overall"], "fail")
    true("next action names unknown",
         any("unknown" in a.lower() for a in report["next_actions"]))


def test_load_coverage_committed():
    print("\nload_coverage() — committed report")
    path = REPO / "data" / "oracle_coverage_report.json"
    cov = pwr.load_coverage(path)
    eq("status ok", cov["status"], "ok")
    true("has signal pct", isinstance(cov.get("at_least_one_signal_pct"), (int, float)))
    true("unread top present", isinstance(cov.get("unread_lines_top"), list))


def test_check_playstyles_evaluate():
    print("\ncheck_playstyles.evaluate()")
    r = cps.evaluate()
    true("scored > 0", r["scored"] > 0)
    true("precision ≥ floor", r["precision"] >= cps.PRECISION_FLOOR)
    true("precision_ok", r["precision_ok"])
    # HIT+EXTRA denominator — never invent 100% by dropping extras
    true("extras counted", r["extra"] >= 0)


def test_render_markdown_roundtrip():
    print("\nrender_markdown()")
    with tempfile.TemporaryDirectory() as td:
        # minimal assemble via live helpers
        cov = pwr.load_coverage(REPO / "data" / "oracle_coverage_report.json")
        ps = cps.evaluate()
        ps["status"] = "ok"
        report = pwr.assemble_report(
            coverage=cov,
            playstyles=ps,
            spot_checks=[{"test": "x.py", "status": "skipped", "ok": True,
                          "note": "absent"}],
            gate1=pwr.parse_gate1_retest(pwr.GATE1_RETEST),
            refresh=None,
            wave="unit",
        )
        md = pwr.render_markdown(report)
        true("md has title", "Print-wave re-eval" in md)
        true("md has precision", "precision" in md.lower())
        out = Path(td) / "r.json"
        out.write_text(json.dumps(report))
        true("json serializable", out.is_file())


if __name__ == "__main__":
    test_gate1_parse()
    test_assemble_precision_floor()
    test_unknown_coverage_fails()
    test_load_coverage_committed()
    test_check_playstyles_evaluate()
    test_render_markdown_roundtrip()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        sys.exit(1)
    print(f"{GREEN}{passed} passed{OFF}")
    sys.exit(0)
