#!/usr/bin/env python3
"""
check_playstyles.py — falsification instrument for the playstyle vocabulary.

For each fixture row, run playstyles.detect() against the commander's oracle
and compare with the community's classification. Report hit / miss / extra
separately. Never collapse them into one accuracy number.

    python scripts/check_playstyles.py
    python scripts/check_playstyles.py --json

Honesty rules:
  * match-nothing → unknown, never a default playstyle
  * unreadable / unverified commander → excluded and counted, not scored as miss
  * publish measured numbers; do not invent a pass bar

floor: precision ≥ 88% on this validated set → exit 1 when below.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import playstyles  # noqa: E402

FIXTURE = REPO / "testdata" / "review" / "commander-playstyles.json"
GRAMMAR = REPO / "data" / "scryfall_grammar.json"
VOCAB = REPO / "data" / "playstyles.json"
PRECISION_FLOOR = 0.88


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_catalog() -> dict[str, str]:
    grammar = json.loads(GRAMMAR.read_text())
    names = (grammar.get("catalogs") or {}).get("card-names") or []
    out: dict[str, str] = {}
    for raw in names:
        n = str(raw).strip()
        if n:
            out.setdefault(squash(n), n)
    return out


def evaluate() -> dict[str, Any]:
    """Score fixtures. Unknown/excluded are counted separately — never as miss."""
    fixture = json.loads(FIXTURE.read_text())
    vocab = json.loads(VOCAB.read_text()).get("playstyles") or {}
    catalog = load_catalog()
    known_styles = set(vocab)

    rows = fixture.get("commanders") or []
    hits = misses = extras = 0
    excluded = 0
    unknown_detect = 0
    empty_detect = 0
    per_style = {s: {"hit": 0, "miss": 0, "extra": 0, "community": 0}
                 for s in known_styles}
    details: list[tuple[str, str, str]] = []

    for row in rows:
        name = row.get("commander") or ""
        community = set(row.get("playstyles") or [])
        oracle = row.get("oracle_text")
        type_line = row.get("type_line") or ""

        # Verify name against Scryfall catalog — the one rule for fixtures.
        if squash(name) not in catalog:
            excluded += 1
            details.append((name, "EXCLUDED", "not in Scryfall card-names catalog"))
            continue
        if not isinstance(oracle, str) or not oracle.strip():
            excluded += 1
            details.append((name, "EXCLUDED", "no oracle_text in fixture"))
            continue

        bad_styles = community - known_styles
        if bad_styles:
            excluded += 1
            details.append((name, "EXCLUDED",
                            f"fixture names unknown playstyles {sorted(bad_styles)}"))
            continue

        card = {"name": name, "oracle_text": oracle, "type_line": type_line}
        result = playstyles.detect(card)
        if result["status"] == "unknown":
            excluded += 1
            unknown_detect += 1
            details.append((name, "EXCLUDED", "demand_profile unreadable"))
            continue

        detected = set(result["matched"])
        if not detected:
            empty_detect += 1

        hit = community & detected
        miss = community - detected
        extra = detected - community

        hits += len(hit)
        misses += len(miss)
        extras += len(extra)

        for s in community:
            per_style[s]["community"] += 1
        for s in hit:
            per_style[s]["hit"] += 1
        for s in miss:
            per_style[s]["miss"] += 1
        for s in extra:
            per_style[s]["extra"] += 1

        tag = "OK" if not miss and not extra else "GAP"
        if not detected and community:
            tag = "NONE"
        details.append((
            name, tag,
            f"community={sorted(community)} detected={sorted(detected)} "
            f"signals={result['signals']}"
            + (f" hit={sorted(hit)}" if hit else "")
            + (f" miss={sorted(miss)}" if miss else "")
            + (f" extra={sorted(extra)}" if extra else ""),
        ))

    scored = len(rows) - excluded
    prec = (hits / (hits + extras)) if (hits + extras) else 0.0
    recall = (hits / (hits + misses)) if (hits + misses) else 0.0
    return {
        "fixture_rows": len(rows),
        "scored": scored,
        "excluded": excluded,
        "unknown_detect": unknown_detect,
        "empty_detect": empty_detect,
        "hit": hits,
        "miss": misses,
        "extra": extras,
        "precision": prec,
        "recall": recall,
        "precision_floor": PRECISION_FLOOR,
        "precision_ok": (not scored) or (prec >= PRECISION_FLOOR),
        "per_style": per_style,
        "details": details,
        "vocab": vocab,
        "vocab_size": len(known_styles),
    }


def print_report(result: dict[str, Any]) -> None:
    vocab = result["vocab"]
    details = result["details"]
    per_style = result["per_style"]

    print(f"playstyles vocabulary: {result['vocab_size']} entries in {VOCAB.name}")
    print(f"fixture rows: {result['fixture_rows']} in {FIXTURE.relative_to(REPO)}")
    print()

    print("=== per commander ===")
    for name, tag, note in details:
        print(f"  [{tag}] {name}: {note}")

    print()
    print("=== per playstyle (community rows) ===")
    for s in sorted(per_style):
        c = per_style[s]
        if c["community"] == 0 and c["extra"] == 0:
            continue
        print(f"  {s}: community={c['community']} hit={c['hit']} "
              f"miss={c['miss']} extra={c['extra']}")

    print()
    print("=== totals ===")
    print(f"  fixture rows:     {result['fixture_rows']}")
    print(f"  scored:           {result['scored']}")
    print(f"  excluded:         {result['excluded']}  (unknown ≠ miss)")
    print(f"  empty detect:     {result['empty_detect']}  (looked, matched nothing)")
    print(f"  HIT:              {result['hit']}")
    print(f"  MISS:             {result['miss']}")
    print(f"  EXTRA:            {result['extra']}")
    # Precision = of the playstyles we name, how many the community also names.
    # floor is 88% on this validated set. Recall is reported separately;
    # never collapse them.
    print(f"  precision:         {result['precision']:.1%}  (HIT / HIT+EXTRA)  "
          f"floor {int(PRECISION_FLOOR * 100)}%")
    print(f"  recall:           {result['recall']:.1%}  (HIT / HIT+MISS)")
    if result["scored"] and not result["precision_ok"]:
        print("  PRECISION BELOW 88% — bar failed")

    print()
    print("=== vocabulary missing_signals (findings) ===")
    for pid, entry in sorted(vocab.items()):
        missing = entry.get("missing_signals") or []
        det = entry.get("detect") or {}
        # requires_any lists alternative shapes of one mechanic, and a
        # playstyle detected solely through it has an empty `requires`. Reading
        # only `requires` reported nine working playstyles as NOT DETECTABLE —
        # a false negative in our own reporting, which is the same class of
        # wrong signal this card exists to remove.
        req = (det.get("requires") or []) + (det.get("requires_any") or [])
        if not req:
            print(f"  {pid}: NOT DETECTABLE yet — {len(missing)} missing signal note(s)")
        elif missing:
            print(f"  {pid}: detectable, {len(missing)} known gap(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--json", action="store_true",
        help="emit machine-readable totals (no per-commander dump)",
    )
    args = ap.parse_args()
    result = evaluate()
    if args.json:
        payload = {
            k: result[k]
            for k in (
                "fixture_rows", "scored", "excluded", "unknown_detect",
                "empty_detect", "hit", "miss", "extra", "precision", "recall",
                "precision_floor", "precision_ok", "vocab_size",
            )
        }
        print(json.dumps(payload, indent=2))
    else:
        print_report(result)
    if result["scored"] and not result["precision_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
