"""
test_oracle_coverage.py — coverage instrument (offline).

No network. Synthetic cards only.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import oracle_coverage as oc  # noqa: E402


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


def test_normalize_and_analyze():
    print("\noracle_coverage — normalize + analyze")
    eq("strip reminder",
       oc.normalize_line("Flying (This creature can't be blocked except by…)"),
       "flying")
    eq("collapse mana",
       oc.normalize_line("{T}: Add {G} or {W}."),
       "{M}: add {M} or {M}")

    cards = [
        {
            "name": "Silent Bear",
            "type_line": "Creature — Bear",
            "oracle_text": "Vigilance",
            "legalities": {"commander": "legal"},
        },
        {
            "name": "Blood Crypt",
            "type_line": "Land — Swamp Mountain",
            "oracle_text": (
                "As this land enters, you may pay 2 life. "
                "If you don't, it enters tapped.\n"
                "{T}: Add {B} or {R}."
            ),
            "legalities": {"commander": "legal"},
        },
        {
            "name": "Not Legal",
            "oracle_text": "Flying",
            "legalities": {"commander": "not_legal"},
        },
        {
            "name": "Unreadable",
            "oracle_text": None,
            "legalities": {"commander": "legal"},
        },
    ]
    report = oc.analyze(iter(cards))
    eq("universe counts legal only", report["universe_commander_legal"], 3)
    eq("unknown counted separately", report["unknown_unreadable"], 1)
    eq("graded excludes unknown", report["graded"], 2)
    # Blood Crypt fires pay_life_enter + enters_tapped.
    eq("signal fires on shock land",
       report["at_least_one_signal"]["count"], 1)
    # Vigilance Bear has no keywords field here → truly unread.
    eq("no-signal no-keyword card is unread",
       report["unread_no_signal_no_keyword"]["count"], 1)
    eq("unknown is not scored as signal-covered",
       report["no_signal"]["count"] + report["at_least_one_signal"]["count"],
       report["graded"])
    unread = {row["line"]: row["count"] for row in report["unread_lines_ranked"]}
    eq("vigilance appears on unread card", "vigilance" in unread, True)

    # Scryfall keywords catalog counts as readable without a signal.
    flyer = {
        "name": "Storm Crow",
        "type_line": "Creature — Bird",
        "oracle_text": "Flying",
        "keywords": ["Flying"],
        "legalities": {"commander": "legal"},
    }
    report2 = oc.analyze(iter([flyer]))
    eq("keyword-only card is signal-empty",
       report2["at_least_one_signal"]["count"], 0)
    eq("keyword-only card is signal∨keyword covered",
       report2["signal_or_keyword"]["count"], 1)
    eq("keyword-only is not in unread tail",
       report2["unread_no_signal_no_keyword"]["count"], 0)


if __name__ == "__main__":
    test_normalize_and_analyze()
    print(f"\n{passed} passed" + (f", {RED}{failed} failed{OFF}" if failed else ""))
    sys.exit(1 if failed else 0)
