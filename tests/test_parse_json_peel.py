"""
CON-187: parse_json_list peels trailing prose after a complete JSON value.

The write-up path was burning both retries on
`Extra data: line 13 column 1` — valid JSON, then more text. A retry that
reproduces the identical failure is a wasted call.
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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import app  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


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
        bad(label, extra)


def _resp(text: str, stop_reason: str = "end_turn"):
    block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(output_tokens=len(text))
    return SimpleNamespace(content=[block], stop_reason=stop_reason, usage=usage)


def test_peels_trailing_prose_object():
    print("\nCON-187 — Extra data after object is peeled, not empty")
    payload = {
        "name": "Etali, Primal Storm",
        "does": "Cast free spells off the top.",
        "strategy": "Big red ramp into Etali, then cascade value.",
        "fits": "You wanted stompy chaos.",
        "mechanics": ["cascade", "impulse draw"],
        "pros": ["Explosive turns"],
        "cons": ["Dies to removal"],
        "catch": "Everyone knows the threat.",
    }
    raw = json.dumps(payload, indent=2) + "\n\nHope this helps you choose!"
    # Prove the failure mode before the peel.
    try:
        json.loads(raw)
        bad("fixture should Extra-data against json.loads", "loads succeeded")
    except json.JSONDecodeError as e:
        true("fixture is Extra data", "Extra data" in str(e), str(e))

    got = app.parse_json_list(_resp(raw), "write-up: Etali", expect="object")
    eq("peeled name", got.get("name"), "Etali, Primal Storm")
    eq("peeled does", got.get("does"), "Cast free spells off the top.")
    true("full object kept", len(got.get("mechanics") or []) == 2)


def test_peels_trailing_prose_list():
    print("\nCON-187 — Extra data after array is peeled")
    raw = '["a", "b"]\nThanks!'
    got = app.parse_json_list(_resp(raw), "list-test", expect="list")
    eq("peeled list", got, ["a", "b"])


def test_clean_json_still_works():
    print("\nCON-187 — clean JSON unchanged")
    got = app.parse_json_list(
        _resp('{"name": "Nazar, the Velvet Fang", "does": "drain"}'),
        "clean", expect="object")
    eq("clean name", got.get("name"), "Nazar, the Velvet Fang")


def test_truncated_still_empty():
    print("\nCON-187 — truncated mid-object stays empty (retry may help)")
    raw = '{"name": "Half", "does": "still writ'
    got = app.parse_json_list(
        _resp(raw, stop_reason="max_tokens"), "trunc", expect="object")
    eq("truncated → empty object", got, {})


def test_fenced_json_with_trailer():
    print("\nCON-187 — fenced JSON + trailer peels")
    body = '{"name": "Yargle", "does": "big frog"}'
    raw = f"```json\n{body}\n```\nAnd that is why."
    got = app.parse_json_list(_resp(raw), "fence", expect="object")
    eq("fenced+trailer name", got.get("name"), "Yargle")


def main() -> int:
    test_peels_trailing_prose_object()
    test_peels_trailing_prose_list()
    test_clean_json_still_works()
    test_truncated_still_empty()
    test_fenced_json_with_trailer()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
