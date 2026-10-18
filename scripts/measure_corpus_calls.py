#!/usr/bin/env python3
"""measure how many build-path queries stay local vs need live.

Uses the fixture corpus (or ~/.deckwright when present) and the real
role_query / commander ladders — no network. Counts:

  local         — answered from the index
  live_needed   — otag:/art:/unparsed (would hit Scryfall)
  unsupported   — same as live_needed, by query text

    python scripts/measure_corpus_calls.py
    python scripts/measure_corpus_calls.py --theme pirate --colors UB
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import corpus  # noqa: E402
from deckwright import app  # noqa: E402


async def measure(theme: str, colors: str) -> dict:
    fixture = REPO / "testdata" / "corpus" / "sample_cards.jsonl"
    if corpus.ORACLE_JSONL.is_file():
        corp = corpus.load_default_corpus(force=True)
        source = str(corpus.ORACLE_JSONL)
    else:
        corp = corpus.Corpus.from_jsonl(fixture, meta={"card_count": 15})
        source = str(fixture)
    corpus.set_corpus(corp)
    corpus.meters().reset()

    brief = {
        "theme": theme,
        "colors": colors,
        "bracket": 2,
        "budget_total": 150,
        "card_pool": "tournament",
        "strategy": f"{theme} aggro with treasures",
        "mechanics": ["treasure", "pirate"],
        "flavor_terms": [theme],
    }
    ci = colors
    budget_each = 4.5

    queries: list[str] = []
    for role, _, _ in [
        ("ramp", 10, ""), ("draw", 9, ""), ("spot", 7, ""),
        ("wipe", 3, ""), ("plan", 10, ""), ("theme", 15, ""),
        ("deepcut", 5, ""), ("lands", 0, ""),
    ]:
        if role == "lands":
            continue
        queries.extend(app.role_query(role, ci, brief, budget_each))

    # Commander ladder shape used at pick time.
    ladder, _ = app.commander_ladder(brief)
    queries.extend(ladder[:8])

    local = live = parse_fail = 0
    live_examples: list[str] = []
    for q in queries:
        if corpus.needs_live(q):
            live += 1
            if len(live_examples) < 6:
                live_examples.append(q)
            continue
        r = corp.serve_search(q)
        if r.status in (corpus.HIT, corpus.EMPTY):
            local += 1
        else:
            parse_fail += 1
            live += 1
            if len(live_examples) < 6:
                live_examples.append(f"[{r.status}] {q}")

    return {
        "source": source,
        "theme": theme,
        "colors": colors,
        "queries": len(queries),
        "local_ok": local,
        "live_needed": live,
        "live_examples": live_examples,
        "corpus": corp.status(),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--theme", default="pirate")
    p.add_argument("--colors", default="UB")
    args = p.parse_args()
    report = asyncio.run(measure(args.theme, args.colors))
    total = report["queries"] or 1
    print(f"corpus source : {report['source']}")
    print(f"brief         : {report['theme']} / {report['colors']}")
    print(f"queries       : {report['queries']}")
    print(f"local_ok      : {report['local_ok']}  ({100 * report['local_ok'] / total:.0f}%)")
    print(f"live_needed   : {report['live_needed']}  ({100 * report['live_needed'] / total:.0f}%)")
    print("live examples:")
    for q in report["live_examples"]:
        print(f"  - {q[:100]}")
    if report["live_needed"] == 0:
        print("\nAll role/commander queries answered locally on this brief.")
    else:
        print(
            "\nLive remainder is almost entirely otag:/art: (Tagger) — "
            "option 1. Materialise those tags (option 2) to cut further."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
