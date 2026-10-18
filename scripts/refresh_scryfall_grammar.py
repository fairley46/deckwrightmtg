#!/usr/bin/env python3
"""Fetch Scryfall catalogs + /sets into data/scryfall_grammar.json."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deckwright import app
from deckwright import resolve


async def main() -> None:
    path = resolve.GRAMMAR_PATH
    print(f"refreshing {path} …")
    payload = await resolve.refresh_grammar(app.scry, path)
    cats = payload.get("catalogs") or {}
    n_terms = sum(len(v) for v in cats.values())
    n_sets = len(payload.get("sets") or [])
    print(f"wrote version={payload.get('version')}  terms={n_terms}  sets={n_sets}")
    print(f"fetched_at={payload.get('fetched_at')}")
    # Smoke the fixtures that must work offline after this.
    resolve.load_grammar(force=True)
    for word, want in [
        ("spiderman", "set:spm"),
        ("hobbits", "set:hob"),
        ("fallouts", "set:pip"),
        ("pirates", "t:pirate"),
        ("lifelink", "keyword:lifelink"),
        ("treasure", "t:treasure"),
        ("heists", None),
    ]:
        got = resolve.expression(resolve.resolve_term(word))
        status = "ok" if got == want else "FAIL"
        print(f"  {status}  {word!r} -> {got!r} (want {want!r})")


if __name__ == "__main__":
    asyncio.run(main())
