#!/usr/bin/env python3
"""thin CardFacts store status (offline; no bake, no model).

    python scripts/cardfacts_status.py
    python scripts/cardfacts_status.py --lookup "Sol Ring"
    python scripts/cardfacts_status.py --path /tmp/cardfacts.json

Prints store path, ``store_status``, card counts by extract status, and an
optional sample lookup. Missing / unreadable store → exit 2 (unknown ≠ zero).
Contestant C stays disabled; this does not touch the build path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--path", type=Path, default=None,
        help="store path (default: cardfacts.store_path())",
    )
    ap.add_argument(
        "--lookup", default=None,
        help="optional card name to look up after the summary",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="emit one JSON object on stdout",
    )
    args = ap.parse_args()

    out_path = (args.path or cf.store_path()).expanduser()
    cards = cf.read_store(out_path)
    summary = cf.summarize_cards(cards)

    payload: dict = {
        "path": str(out_path),
        "store_status": summary["status"],
        "card_count": summary["card_count"],
        "ok": summary["ok"],
        "partial": summary["partial"],
        "unread": summary["unread"],
        "other": summary["other"],
    }

    if args.lookup is not None:
        hit = None
        if cards is not None:
            name = args.lookup
            if name in cards:
                hit = cards[name]
            else:
                low = name.lower()
                for key, facts in cards.items():
                    if key.lower() == low:
                        hit = facts
                        break
        payload["lookup_name"] = args.lookup
        payload["lookup"] = hit  # None = unknown / miss

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"path={payload['path']}")
        if payload["store_status"] == "unknown":
            print("store_status=unknown card_count=None")
        else:
            print(
                f"store_status=ok card_count={payload['card_count']} "
                f"ok={payload['ok']} partial={payload['partial']} "
                f"unread={payload['unread']} other={payload['other']}"
            )
        if args.lookup is not None:
            hit = payload.get("lookup")
            if hit is None:
                print(f"lookup={args.lookup!r} → None (unknown / not in store)")
            else:
                st = hit.get("status") if isinstance(hit, dict) else "?"
                print(f"lookup={args.lookup!r} → status={st}")

    if summary["status"] == "unknown":
        print(
            f"UNKNOWN: CardFacts store missing or unreadable at {out_path}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
