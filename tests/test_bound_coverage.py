"""bound_coverage helpers, offline pattern fixtures.

No live corpus required. Does not edit commander_strategy.
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

import bound_coverage as bc  # noqa: E402

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


# Lifegain capacity — extractable on Gate 4 steer tip (ceiling, not floor).
LIFEGAIN_BOUND = {
    "name": "Fixture Lifegain Bound",
    "oracle_text": (
        "Whenever you gain life, return target artifact card with mana value "
        "less than or equal to the amount of life you gained this turn from "
        "your graveyard to the battlefield."
    ),
    "type_line": "Legendary Creature — Vampire Cleric",
}

# MV floor — floors express this as static geq (expect SEEN).
MV_FLOOR = {
    "name": "Fixture MV Floor",
    "oracle_text": (
        "During your turn, artifacts you control with mana value 4 or greater "
        "are 4/4 creatures in addition to their other types."
    ),
    "type_line": "Legendary Creature — Raccoon Bard",
}


def test_load_from_frozen_filters_empty_bounds():
    print("\nbound_coverage — load frozen set, only declared bounds")
    payload = {
        "commanders": [
            {"name": "No Bounds", "bounds": []},
            {
                "name": "Has Bound",
                "bounds": [{"id": "mv_geq_4", "kind": "mv_geq", "value": 4}],
            },
            {"name": "Missing Bounds Key"},
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "commanders.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        entries, source = bc.load_bound_commanders(path)
    eq("one entry", len(entries), 1)
    eq("name", entries[0]["name"], "Has Bound")
    eq("harness bound count", len(entries[0]["harness_bounds"]), 1)
    true("source mentions path", "commanders" in source or str(path) in source, source)


def test_embedded_fallback_six_names():
    print("\nbound_coverage — embedded Gate 5 names when no frozen path")
    # load_bound_commanders(None) uses DEFAULT_FROZEN; on steer that file is
    # absent, so we get the six. If a future merge adds the file, still assert
    # GATE5_BOUND_NAMES content.
    eq("six names", len(bc.GATE5_BOUND_NAMES), 6)
    true(
        "Bello in fallback",
        "Bello, Bard of the Brambles" in bc.GATE5_BOUND_NAMES,
    )


def test_coverage_row_seen_none_unknown():
    print("\nbound_coverage — SEEN / NONE / UNKNOWN from fixtures")
    seen = bc.coverage_row("Fixture Lifegain Bound", card=LIFEGAIN_BOUND)
    eq("lifegain status SEEN", seen["status"], "SEEN")
    true("lifegain extracted_n >= 1", (seen["extracted_n"] or 0) >= 1)

    floor = bc.coverage_row("Fixture MV Floor", card=MV_FLOOR)
    eq("floor status SEEN (post the MV floor bounds floors)", floor["status"], "SEEN")
    eq("floor extracted_n 1", floor["extracted_n"], 1)

    unk = bc.coverage_row(
        "Missing Card",
        card=None,
        miss_reason="corpus not loaded",
    )
    eq("miss status UNKNOWN", unk["status"], "UNKNOWN")
    eq("miss extracted_n None", unk["extracted_n"], None)
    true("detail kept", "corpus" in unk["detail"])


def test_summarize_and_table():
    print("\nbound_coverage — summarize + table formatting")
    s = bc.summarize_bounds([
        {"subject": "artifact_mv", "op": "leq",
         "bound": {"mode": "event_capacity",
                   "capacity_of": {"resource": "life"}}},
    ])
    true("summary has subject", "artifact_mv" in s, s)
    table = bc.format_table([
        {
            "commander": "A",
            "harness_n": 1,
            "harness_summary": "mv_geq=4",
            "extracted_n": 0,
            "extracted_summary": "—",
            "status": "NONE",
            "detail": "",
        },
    ])
    true("table has header", "commander" in table)
    true("table has NONE", "NONE" in table)


def main():
    test_load_from_frozen_filters_empty_bounds()
    test_embedded_fallback_six_names()
    test_coverage_row_seen_none_unknown()
    test_summarize_and_table()
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
