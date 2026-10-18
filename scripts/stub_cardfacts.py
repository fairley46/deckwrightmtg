#!/usr/bin/env python3
"""CON-322: write an empty-but-valid CardFacts store (no model, no scrape).

    python scripts/stub_cardfacts.py
    python scripts/stub_cardfacts.py --path /tmp/cardfacts.json

Creates ``cardfacts.json`` under DECKWRIGHT_HOME (or ~/.deckwright) with the
version-1 schema documented in ``cardfacts.py``. Does not call the model and
does not fetch Scryfall. Optional ``--fixture`` writes a tiny offline sample
card so tests/docs can show a loaded hit without a harvest pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402

_FIXTURE_CARD = {
    "Sol Ring": {
        "name": "Sol Ring",
        "status": "partial",
        "inputs": [],
        "outputs": [{
            "kind": "mana",
            "magnitude": {"mode": "static", "value": 2},
            "once_each_turn": False,
        }],
        "bounds": [],
        "rate": {"supplier": "self", "growth_2p_to_4p": 1},
        "honesty": "I read what it produces, not what it needs.",
        "unscoreable": ["soft_combo", "hidden_power"],
        "free_value": True,
        "provenance": {"tier": "fixture", "note": "offline stub only"},
    }
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--path",
        type=Path,
        default=None,
        help="output path (default: cardfacts.store_path())",
    )
    ap.add_argument(
        "--fixture",
        action="store_true",
        help="include one offline Sol Ring sample (no network)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing file",
    )
    args = ap.parse_args()

    path = args.path or cf.store_path()
    path = path.expanduser()
    if path.exists() and not args.force:
        print(f"refusing to overwrite {path} (pass --force)", file=sys.stderr)
        return 1

    payload = cf.empty_schema()
    if args.fixture:
        payload["cards"] = dict(_FIXTURE_CARD)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(payload['cards'])} cards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
