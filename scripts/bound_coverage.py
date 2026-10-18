#!/usr/bin/env python3
"""bound coverage — does `commander_strategy.derive` see harness bounds?

Sign-off before the next E,BLUE,B sweep. Prints whether derive extracts any
bounds for every harness commander that *declares* bounds.

Run after MV floor bounds are in place. Expect NONE → nonempty for floor
commanders (Bello, Tayam, Shirei, Maralen, …). This script only *reads*
derive — it does not edit the extractor.

    source .venv/bin/activate
    python scripts/bound_coverage.py
    python scripts/bound_coverage.py --commanders data/benchmarks/commanders.json

Exit 0 on UNKNOWN corpus misses (unknown ≠ zero). Exit 1 only on crash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DEFAULT_FROZEN = REPO / "data" / "benchmarks" / "commanders.json"

# Gate 5 / six bound-declaring names when frozen set is absent.
GATE5_BOUND_NAMES: tuple[str, ...] = (
    "Bello, Bard of the Brambles",
    "Shirei, Shizo's Caretaker",
    "Maralen of the Mornsong",
    "Tayam, Luminous Enigma",
    "Sliver Overlord",
    "Meren of Clan Nel Toth",
)


def load_bound_commanders(
    path: Path | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (entries, source_label).

    Each entry: {"name": str, "harness_bounds": list | None}.
    Prefer frozen `data/benchmarks/commanders.json` (or --commanders).
    Else embed the six Gate 5 names (harness_bounds unknown).
    """
    if path is not None:
        return _from_frozen(path), f"--commanders {path}"
    if DEFAULT_FROZEN.is_file():
        return _from_frozen(DEFAULT_FROZEN), str(DEFAULT_FROZEN.relative_to(REPO))
    return (
        [{"name": n, "harness_bounds": None} for n in GATE5_BOUND_NAMES],
        "embedded Gate 5 bound names (frozen set absent)",
    )


def _from_frozen(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for c in data.get("commanders") or []:
        bounds = c.get("bounds") or []
        if not bounds:
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "harness_bounds": bounds})
    return out


def summarize_bounds(bounds: list[dict[str, Any]] | None) -> str:
    """Short human summary of derive().bounds (or harness bounds)."""
    if not bounds:
        return "—"
    parts: list[str] = []
    for b in bounds:
        if "kind" in b:  # harness frozen shape
            kind = b.get("kind") or "?"
            val = b.get("value")
            parts.append(f"{kind}={val}" if val is not None else str(kind))
            continue
        # derive() shape: subject / op / bound.mode
        subj = b.get("subject") or "?"
        op = b.get("op") or "?"
        mode = (b.get("bound") or {}).get("mode") or "?"
        val = (b.get("bound") or {}).get("value")
        if val is not None:
            parts.append(f"{subj}:{op}:{mode}:{val}")
        else:
            parts.append(f"{subj}:{op}:{mode}")
    return "; ".join(parts)


def fetch_oracle_card(name: str) -> tuple[dict[str, Any] | None, str]:
    """Local corpus only. Miss → (None, reason). Never invent oracle."""
    from deckwright import corpus

    c = corpus.get_corpus()
    if not c.loaded:
        return None, "corpus not loaded"
    r = c.serve_named(exact=name)
    if r.status == corpus.HIT and isinstance(r.payload, dict) and r.payload.get("name"):
        return r.payload, "hit"
    if r.status == corpus.EMPTY:
        return None, "not in corpus"
    if r.status == corpus.MISSING:
        return None, "corpus not loaded"
    return None, r.detail or r.status


def coverage_row(
    name: str,
    *,
    card: dict[str, Any] | None,
    miss_reason: str | None = None,
    harness_bounds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One table row. UNKNOWN when card missing; never fake bounds."""
    harness_n = len(harness_bounds) if harness_bounds is not None else None
    harness_sum = summarize_bounds(harness_bounds) if harness_bounds else "—"
    if card is None:
        return {
            "commander": name,
            "harness_n": harness_n,
            "harness_summary": harness_sum,
            "extracted_n": None,
            "extracted_summary": "—",
            "status": "UNKNOWN",
            "detail": miss_reason or "could not look",
        }
    from deckwright import commander_strategy as cs

    strategy = cs.derive(card)
    bounds = list(strategy.get("bounds") or [])
    status = "SEEN" if bounds else "NONE"
    return {
        "commander": name,
        "harness_n": harness_n,
        "harness_summary": harness_sum,
        "extracted_n": len(bounds),
        "extracted_summary": summarize_bounds(bounds),
        "status": status,
        "detail": strategy.get("status") or "",
    }


def format_table(rows: list[dict[str, Any]]) -> str:
    headers = (
        "commander",
        "harness",
        "extracted",
        "summary",
        "status",
    )
    cells: list[tuple[str, str, str, str, str]] = []
    for r in rows:
        hn = "—" if r["harness_n"] is None else str(r["harness_n"])
        en = "—" if r["extracted_n"] is None else str(r["extracted_n"])
        cells.append((
            r["commander"],
            hn,
            en,
            r["extracted_summary"] if r["status"] != "UNKNOWN" else r["detail"],
            r["status"],
        ))
    widths = [len(h) for h in headers]
    for row in cells:
        for i, col in enumerate(row):
            widths[i] = max(widths[i], len(col))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(col.ljust(widths[i]) for i, col in enumerate(row))

    lines = [fmt(headers), fmt(tuple("-" * w for w in widths))]
    lines.extend(fmt(row) for row in cells)
    return "\n".join(lines)


def run(path: Path | None = None) -> list[dict[str, Any]]:
    entries, source = load_bound_commanders(path)
    print(f"bound_coverage — source: {source}")
    print(f"commanders declaring bounds: {len(entries)}")
    print()
    rows: list[dict[str, Any]] = []
    for e in entries:
        name = e["name"]
        card, reason = fetch_oracle_card(name)
        rows.append(coverage_row(
            name,
            card=card,
            miss_reason=None if card else reason,
            harness_bounds=e.get("harness_bounds"),
        ))
    print(format_table(rows))
    print()
    seen = sum(1 for r in rows if r["status"] == "SEEN")
    none = sum(1 for r in rows if r["status"] == "NONE")
    unknown = sum(1 for r in rows if r["status"] == "UNKNOWN")
    print(f"SEEN={seen}  NONE={none}  UNKNOWN={unknown}")
    if none:
        print(
            "NONE = derive ran and returned empty bounds "
            "(coverage gap, not a steer failure)."
        )
    if unknown:
        print(
            "UNKNOWN = local corpus miss — not graded. "
            "Load ~/.deckwright/oracle_cards.jsonl and re-run."
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--commanders",
        type=Path,
        default=None,
        help="Path to frozen benchmarks commanders.json "
             "(default: data/benchmarks/commanders.json if present)",
    )
    args = p.parse_args(argv)
    try:
        run(args.commanders)
    except Exception as e:
        print(f"bound_coverage CRASH: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
