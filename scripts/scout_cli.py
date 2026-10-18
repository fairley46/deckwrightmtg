#!/usr/bin/env python3
"""Thin card-scout CLI over DeckWright resolve + corpus.

Usage (from repo root):
  python3 scripts/scout_cli.py resolve "pirates"
  python3 scripts/scout_cli.py search "goblin tokens" [--limit N]
  python3 scripts/scout_cli.py archidekt "pirates" [--limit N]

Sacred: resolve writes Scryfall expressions, not card names.
Leave UI / FastAPI / goldfish alone — this is the Archidekt-first path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deckwright import corpus  # noqa: E402
from deckwright import resolve  # noqa: E402


def _theme_to_query(theme: str) -> dict:
    """Resolve natural language → Scryfall expression. Never invent card names."""
    out = resolve.resolve_theme(theme)
    return out


def _search(q: str, *, order: str = "edhrec", page: int = 1) -> dict:
    c = corpus.get_corpus()
    r = c.serve_search(q, order=order, page=page)
    detail = r.detail or None
    # EMPTY is a real zero. INCOMPLETE / UNSUPPORTED / MISSING are unknowns —
    # never report them as total=0 (house rule: unknown ≠ zero).
    if r.status == corpus.EMPTY:
        return {
            "ok": True,
            "status": r.status,
            "query": q,
            "cards": [],
            "total": 0,
            "note": detail,
        }
    if r.status != corpus.HIT or not isinstance(r.payload, dict):
        return {
            "ok": False,
            "status": r.status,
            "query": q,
            "cards": [],
            "total": None,
            "note": detail,
        }
    data = r.payload.get("data") or []
    cards = []
    for card in data:
        cards.append(
            {
                "name": card.get("name"),
                "set": card.get("set"),
                "collector_number": card.get("collector_number"),
                "mana_cost": card.get("mana_cost"),
                "type_line": card.get("type_line"),
            }
        )
    return {
        "ok": True,
        "status": r.status,
        "query": q,
        "cards": cards,
        "total": r.payload.get("total_cards", len(cards)),
        "has_more": bool(r.payload.get("has_more")),
        "note": detail,
    }


def cmd_resolve(args: argparse.Namespace) -> int:
    out = _theme_to_query(args.theme)
    print(json.dumps(out, indent=2))
    print(f"\nexpression: {out.get('expression')}", file=sys.stderr)
    if out.get("unresolved"):
        print(f"unresolved: {out['unresolved']}", file=sys.stderr)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    theme = args.query
    if args.raw or ":" in theme:
        q = theme
        resolved = None
    else:
        # resolve_theme already reports unresolved; do not invent dropped_words
        # by token-diffing the expression (plurals/aliases are not drops).
        resolved = _theme_to_query(theme)
        q = resolved.get("expression") or theme

    result = _search(q, order=args.order)
    limit = args.limit
    cards = result["cards"][:limit]
    print(
        json.dumps(
            {
                "resolved": resolved,
                "query": result["query"],
                "ok": result["ok"],
                "status": result.get("status"),
                "total": result["total"],
                "showing": len(cards),
                "cards": cards,
                "note": result.get("note"),
            },
            indent=2,
        )
    )
    return 0 if result["ok"] else 1


def cmd_archidekt(args: argparse.Namespace) -> int:
    resolved = _theme_to_query(args.theme)
    q = resolved.get("expression") or args.theme
    result = _search(q, order=args.order)
    if not result["ok"]:
        print(
            json.dumps(
                {
                    "ok": False,
                    "resolved": resolved,
                    "status": result.get("status"),
                    "note": result.get("note"),
                },
                indent=2,
            )
        )
        return 1
    cards = result["cards"][: args.limit]
    # Archidekt bulk import: "1 Card Name" per line
    lines = [f"1 {c['name']}" for c in cards if c.get("name")]
    paste = "\n".join(lines)
    meta = {
        "ok": True,
        "theme": args.theme,
        "query": q,
        "resolved": resolved,
        "total_available": result["total"],
        "exported": len(lines),
    }
    print(json.dumps(meta, indent=2), file=sys.stderr)
    print(paste)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Archidekt-first card scout over DeckWright brain")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("resolve", help="theme → Scryfall expression")
    pr.add_argument("theme")
    pr.set_defaults(func=cmd_resolve)

    ps = sub.add_parser("search", help="theme or Scryfall query → ranked cards")
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=12)
    ps.add_argument("--order", default="edhrec")
    ps.add_argument("--raw", action="store_true", help="treat query as raw Scryfall (skip resolve)")
    ps.set_defaults(func=cmd_search)

    pa = sub.add_parser("archidekt", help="theme → Archidekt paste block")
    pa.add_argument("theme")
    pa.add_argument("--limit", type=int, default=20)
    pa.add_argument("--order", default="edhrec")
    pa.set_defaults(func=cmd_archidekt)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
