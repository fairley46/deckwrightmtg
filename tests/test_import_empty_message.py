#!/usr/bin/env python3
"""CON-306: empty import deck must say the true cause, not invent a zero.

finishImport used to treat every empty `deck` as "nothing resolved". The
response already distinguishes unresolved lines, commander-pick failure, and
a genuine empty resolve. Assert the *message*, not merely that a failure card
rendered.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

NOTHING = "Nothing in that list resolved to a real card."
PARTNER_REASON = (
    "Legendary creatures found but none cover the full color identity"
)


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        bad(label, extra or "expected truthy")


def _extract_helpers(html: str) -> str:
    """Pull the CON-306 pure helpers out of index.html for node evaluation."""
    start = html.find("const IMPORT_NOTHING_RESOLVED")
    if start < 0:
        raise AssertionError("IMPORT_NOTHING_RESOLVED missing from index.html")
    end = html.find("function showCommanderDisambiguation", start)
    if end < 0:
        raise AssertionError("showCommanderDisambiguation missing after helpers")
    return html[start:end]


def _run_cases(cases: list[dict]) -> list[dict]:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    helpers = _extract_helpers(html)
    harness = helpers + """
const cases = JSON.parse(process.argv[2]);
const out = cases.map((c) => {
  const d = c.payload;
  return {
    id: c.id,
    should: importShouldDisambiguate(d),
    verdict: importEmptyFailure(d),
  };
});
process.stdout.write(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run(
            ["node", path, json.dumps(cases)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"node helpers failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout)


def test_message_branches():
    print("\nimportEmptyFailure  — message matches the real cause")
    cases = [
        {
            "id": "commander_ambiguous_no_candidates",
            "payload": {
                "entries": [{"line": 1, "name": "Sol Ring"}],
                "unresolved": [],
                "deck": [],
                "notes": [PARTNER_REASON],
                "commander_pick": {
                    "status": "missing",
                    "commander": None,
                    "candidates": [],
                    "reason": PARTNER_REASON,
                },
            },
        },
        {
            "id": "commander_ambiguous_with_candidates",
            "payload": {
                "entries": [{"line": 1, "name": "Sol Ring"}],
                "unresolved": [],
                "deck": [],
                "notes": [PARTNER_REASON],
                "commander_pick": {
                    "status": "ambiguous",
                    "commander": None,
                    "candidates": [
                        "Malcolm, Keen-Eyed Navigator",
                        "Breeches, Brazen Plunderer",
                    ],
                    "reason": PARTNER_REASON,
                },
            },
        },
        {
            "id": "some_unresolved",
            "payload": {
                "entries": [
                    {"line": 1, "name": "Sol Ring"},
                    {"line": 2, "name": "Not A Real Card"},
                    {"line": 3, "name": "Also Fake"},
                ],
                "unresolved": [
                    {"line": 2, "text": "Not A Real Card"},
                    {"line": 3, "text": "Also Fake"},
                ],
                "deck": [],
                "notes": ["No commander section and no legendary creature in the list"],
                "commander_pick": {
                    "status": "missing",
                    "commander": None,
                    "candidates": [],
                    "reason": "No commander section and no legendary creature in the list",
                },
            },
        },
        {
            "id": "zero_resolved",
            "payload": {
                "entries": [
                    {"line": 1, "name": "Fake One"},
                    {"line": 2, "name": "Fake Two"},
                ],
                "unresolved": [
                    {"line": 1, "text": "Fake One"},
                    {"line": 2, "text": "Fake Two"},
                ],
                "deck": [],
                "notes": [],
                "commander_pick": {
                    "status": "missing",
                    "commander": None,
                    "candidates": [],
                    "reason": "No commander section and no legendary creature in the list",
                },
            },
        },
        {
            "id": "empty_paste",
            "payload": {
                "entries": [],
                "unresolved": [],
                "deck": [],
                "notes": ["empty list"],
                "commander_pick": {
                    "status": "missing",
                    "commander": None,
                    "candidates": [],
                    "reason": "empty list",
                },
            },
        },
    ]
    results = {row["id"]: row for row in _run_cases(cases)}

    amb = results["commander_ambiguous_no_candidates"]
    eq("ambiguous without candidates → fail", amb["verdict"]["action"], "fail")
    true(
        "ambiguous message contains pick reason",
        PARTNER_REASON in amb["verdict"]["message"],
        amb["verdict"]["message"],
    )
    true(
        "ambiguous message is not 'nothing resolved'",
        NOTHING not in amb["verdict"]["message"],
        amb["verdict"]["message"],
    )
    true(
        "ambiguous what is not 'nothing resolved'",
        NOTHING not in amb["verdict"]["what"],
        amb["verdict"]["what"],
    )

    with_cands = results["commander_ambiguous_with_candidates"]
    eq(
        "ambiguous with candidates → disambiguate",
        with_cands["verdict"]["action"],
        "disambiguate",
    )
    true(
        "ambiguous with candidates shouldDisambiguate",
        with_cands["should"] is True,
    )

    some = results["some_unresolved"]
    eq("some unresolved → fail", some["verdict"]["action"], "fail")
    true(
        "unresolved message names Not A Real Card",
        "Not A Real Card" in some["verdict"]["message"],
        some["verdict"]["message"],
    )
    true(
        "unresolved message names Also Fake",
        "Also Fake" in some["verdict"]["message"],
        some["verdict"]["message"],
    )
    true(
        "unresolved message is not 'nothing resolved'",
        NOTHING not in some["verdict"]["message"],
        some["verdict"]["message"],
    )

    zero = results["zero_resolved"]
    eq("zero resolved → fail", zero["verdict"]["action"], "fail")
    eq("zero resolved keeps current sentence", zero["verdict"]["message"], NOTHING)

    empty = results["empty_paste"]
    eq("empty paste → fail", empty["verdict"]["action"], "fail")
    eq("empty paste keeps current sentence", empty["verdict"]["message"], NOTHING)


def test_wiring_in_index_html():
    print("\nindex.html  — finishImport / afterImport wire CON-306 helpers")
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    true("importEmptyFailure defined", "function importEmptyFailure" in html)
    true(
        "importShouldDisambiguate defined",
        "function importShouldDisambiguate" in html,
    )
    true(
        "showCommanderDisambiguation defined",
        "function showCommanderDisambiguation" in html,
    )

    fin = html.find("async function finishImport")
    true("finishImport present", fin >= 0)
    fin_body = html[fin:fin + 900]
    true(
        "finishImport calls importEmptyFailure",
        "importEmptyFailure(d)" in fin_body,
        fin_body[:200],
    )
    true(
        "finishImport no longer hardcodes only the nothing-resolved sentence",
        not re.search(
            r"fail\(\s*'import'\s*,\s*\{\s*message:\s*'Nothing in that list resolved",
            fin_body,
        ),
    )
    true(
        "finishImport routes disambiguate to showCommanderDisambiguation",
        "showCommanderDisambiguation(d)" in fin_body,
    )

    after = html.find("async function afterImport")
    after_body = html[after:after + 400]
    true(
        "afterImport uses importShouldDisambiguate",
        "importShouldDisambiguate(d)" in after_body,
    )

    paste = html.find("async function resolvePasted")
    paste_body = html[paste:paste + 700]
    true(
        "resolvePasted keeps the paste text for re-resolve",
        "d.text = text" in paste_body,
        paste_body,
    )


def main() -> int:
    test_message_branches()
    test_wiring_in_index_html()
    print(
        f"\n{GREEN}{passed} passed{OFF}"
        + (f", {RED}{failed} FAILED{OFF}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
