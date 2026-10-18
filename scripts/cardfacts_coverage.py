#!/usr/bin/env python3
"""CON-322: CardFacts extract coverage report (offline).

    python scripts/cardfacts_coverage.py
    python scripts/cardfacts_coverage.py --jsonl ~/.deckwright/oracle_cards.jsonl
    python scripts/cardfacts_coverage.py --reextract   # scan jsonl, ignore store

Prints ok / partial / unread tallies and steer-usable share (ok or partial
with at least one input or output). Missing jsonl → exit 2 (unknown ≠ zero).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402


def _steer_usable(facts: dict) -> bool:
    st = facts.get("status") or "unread"
    if st == "ok":
        return True
    if st == "partial" and (facts.get("inputs") or facts.get("outputs")):
        return True
    return False


def _scan_jsonl(path: Path) -> dict[str, int]:
    tallies: Counter[str] = Counter()
    steer = 0
    empty = 0
    nonempty_covered = 0
    partial_br: Counter[str] = Counter()
    n = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            card = json.loads(line)
            text = (card.get("oracle_text") or card.get("text") or "").strip()
            facts = cf.extract(card)
            st = facts.get("status") or "unread"
            tallies[st] += 1
            if _steer_usable(facts):
                steer += 1
            if not text:
                empty += 1
            elif st in ("ok", "partial"):
                nonempty_covered += 1
            if st == "partial":
                partial_br[
                    f"i={bool(facts.get('inputs'))} "
                    f"o={bool(facts.get('outputs'))} "
                    f"b={bool(facts.get('bounds'))}"
                ] += 1
            n += 1
    nonempty = n - empty
    return {
        "n": n,
        "ok": tallies["ok"],
        "partial": tallies["partial"],
        "unread": tallies["unread"],
        "steer_usable": steer,
        "empty": empty,
        "nonempty": nonempty,
        "nonempty_covered": nonempty_covered,
        "nonempty_coverage_pct": (
            round(100.0 * nonempty_covered / nonempty, 1) if nonempty else None
        ),
        "partial_breakdown": dict(partial_br),
    }


def _scan_store(path: Path) -> dict[str, int]:
    cards = cf.read_store(path)
    if cards is None:
        return {"n": 0, "status": "unknown"}
    summary = cf.summarize_cards(cards)
    steer = sum(1 for f in cards.values() if _steer_usable(f))
    return {
        "n": summary["card_count"] or 0,
        "ok": summary["ok"] or 0,
        "partial": summary["partial"] or 0,
        "unread": summary["unread"] or 0,
        "steer_usable": steer,
        "source": "store",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jsonl",
        type=Path,
        default=Path.home() / ".deckwright" / "oracle_cards.jsonl",
        help="corpus to re-extract (default: ~/.deckwright/oracle_cards.jsonl)",
    )
    ap.add_argument(
        "--store",
        type=Path,
        default=None,
        help="report baked store only (no re-extract)",
    )
    ap.add_argument(
        "--reextract",
        action="store_true",
        help="scan --jsonl with live extract() (default when store missing)",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    jsonl = args.jsonl.expanduser()
    store = (args.store or cf.store_path()).expanduser()

    if args.store is not None and not args.reextract:
        payload = _scan_store(store)
        payload["path"] = str(store)
    elif args.reextract or not store.is_file():
        if not jsonl.is_file():
            print(f"UNKNOWN: corpus missing at {jsonl}", file=sys.stderr)
            return 2
        payload = _scan_jsonl(jsonl)
        payload["path"] = str(jsonl)
        payload["source"] = "reextract"
    else:
        payload = _scan_store(store)
        payload["path"] = str(store)
        payload["source"] = "store"

    n = payload.get("n") or 0
    if n == 0 and payload.get("status") == "unknown":
        print(f"UNKNOWN: store unreadable at {store}", file=sys.stderr)
        return 2

    def pct(x: int) -> str:
        return f"{100.0 * x / n:.1f}%" if n else "n/a"

    if args.json:
        payload["ok_pct"] = pct(payload.get("ok", 0))
        payload["steer_usable_pct"] = pct(payload.get("steer_usable", 0))
        print(json.dumps(payload, indent=2))
    else:
        print(f"cardfacts_coverage — source: {payload.get('source')} path={payload.get('path')}")
        print(f"n={n}  ok={payload.get('ok')} ({pct(payload.get('ok', 0))})  "
              f"partial={payload.get('partial')} ({pct(payload.get('partial', 0))})  "
              f"unread={payload.get('unread')} ({pct(payload.get('unread', 0))})")
        print(f"steer_usable={payload.get('steer_usable')} ({pct(payload.get('steer_usable', 0))})")
        if payload.get("nonempty_coverage_pct") is not None:
            print(
                f"nonempty_coverage={payload['nonempty_covered']}/"
                f"{payload.get('nonempty')} "
                f"({payload['nonempty_coverage_pct']}%)  "
                f"empty={payload.get('empty')} (unread by design)"
            )
        br = payload.get("partial_breakdown")
        if br:
            print("partial_breakdown:", br)
        print()
        print(
            "After changing extract, re-bake green-c only:\n"
            "  DECKWRIGHT_HOME=~/.deckwright-green-c "
            "python scripts/bake_cardfacts.py --force"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
