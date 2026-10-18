"""
function_coverage.py — do we cover what his deck DOES, not what it contains?

Design note:

    "that person made a choice. Those are his choices. They may not be
    mathematically correct, but it's how he chose the deck. What we're looking
    for is mathematical correctness towards synergies. We're also playing with
    a much bigger set of cards than he had... maybe just theme those cards and
    say, what do they do? And do we have cards that do that or better? If you
    try to just focus in on those cards, that's a very myopic view."

So card-name overlap is the wrong measure. A tutorial author picks from his
collection and his memory; we pick from every card Scryfall knows. Matching his
list would cap us at his ceiling and score a better card as a miss.

This measures FUNCTION coverage instead. Every card decomposes into what it
produces and consumes (synergy.profile). His deck expresses a set of functions;
ours expresses a set of functions. The questions that matter:

    does our deck do everything his deck does?
    where we both cover a function, do our cards connect at least as well?
    what do we cover that he did not?

A function he covers and we do not is a real gap. A function we cover with
better-connected cards is a real win. A different card doing the same job is
neither.

    python scripts/function_coverage.py [--deck /tmp/hb_now.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import synergy  # noqa: E402

EXPERT = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-cards.json"
CACHE = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-oracle.json"
UA = "deckwright/1.0 (local; function coverage check)"


def fetch_oracle(names: list[str]) -> dict[str, dict]:
    """Oracle text for each name, via the app's own Scryfall client.

    Uses /cards/collection (75 names per request) through app.scry_post, so
    this inherits the repo's rate gate and User-Agent rather than reinventing
    a client. A hand-rolled urllib call to /cards/named returned HTTP 400 for
    every name including "Plains" -- the endpoint was fine, the request was
    not, and the repo already had a working one.

    Names Scryfall cannot resolve are reported and OMITTED, never faked. A card
    we could not fetch is an unknown, and an unknown must not become a zero.
    """
    if CACHE.exists():
        cached = json.loads(CACHE.read_text())
        if all(n in cached for n in names):
            return cached
        out = dict(cached)
    else:
        out = {}

    todo = [n for n in dict.fromkeys(names) if n not in out]
    if not todo:
        return out

    import asyncio
    from deckwright import app

    async def go():
        for i in range(0, len(todo), 75):
            chunk = todo[i:i + 75]
            payload = {"identifiers": [{"name": n} for n in chunk]}
            res = await app.scry_post("/cards/collection", payload)
            for card in res.get("data") or []:
                out[card["name"]] = {
                    "name": card.get("name"),
                    "text": card.get("oracle_text") or "",
                    "type_line": card.get("type_line") or "",
                    "card_faces": card.get("card_faces") or [],
                }
            for miss in res.get("not_found") or []:
                print(f"  UNRESOLVED {miss.get('name')}")

    asyncio.run(go())
    CACHE.write_text(json.dumps(out, indent=2) + "\n")
    return out


def functions(cards: list[dict]) -> dict[str, list[str]]:
    """signal -> card names expressing it. The 'what does it DO' view."""
    out: dict[str, list[str]] = {}
    for c in cards:
        p = synergy.profile(c)
        if p["status"] == synergy.UNKNOWN:
            continue
        for sig in p["signals"]:
            out.setdefault(sig, []).append(c.get("name") or "?")
    return out


# A function the expert expresses this few times is a coin-flip in the score,
# not a signal about deck quality. Measured: with his lifegain_trigger at 1,
# our 0-vs-2 flipped that function between "covered" and "gap" and moved the
# headline 6 points across three runs of identical code.
FRAGILE_AT = 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", action="append", default=None,
                    help="deck json; repeat for a band")
    args = ap.parse_args()
    decks = args.deck or ["/tmp/hb_now.json"]

    expert_payload = json.loads(EXPERT.read_text())
    # score functions against chosen, not the contaminated 50.
    chosen_rows = expert_payload.get("chosen") or []
    expert_names = [r["name"] for r in chosen_rows] or list(
        expert_payload.get("verified_multi_word") or [])

    all_our_names: list[str] = []
    per_deck: list[list[str]] = []
    for d in decks:
        names = json.loads(pathlib.Path(d).read_text())["deck"]
        per_deck.append(names)
        all_our_names.extend(names)

    # One shared cache, so it MUST be filtered back down to the names actually
    # asked for. Returning the whole cache put his 50 cards inside "ours" and
    # reported 125 of 99 profiled -- which would have scored 100% function
    # coverage partly because our set literally contained his.
    cache = fetch_oracle(expert_names + all_our_names)
    his_cards = [cache[n] for n in expert_names if n in cache]
    his = functions(his_cards)

    runs = []
    for d, names in zip(decks, per_deck):
        ours = functions([cache[n] for n in names if n in cache])
        covered = set(his) & set(ours)
        gaps = sorted(set(his) - set(ours))
        # Split the score by how much evidence his side carries. A function he
        # expresses once cannot be weighed the same as one he expresses 19
        # times; averaging them together is what made the headline swing.
        solid = {f for f in his if len(his[f]) > FRAGILE_AT}
        runs.append({
            "deck": d, "ours": ours,
            "pct": 100 * len(covered) // max(1, len(his)),
            "solid_pct": 100 * len(covered & solid) // max(1, len(solid)),
            "gaps": gaps,
            "wins": sorted(set(ours) - set(his)),
        })

    ref = runs[0]
    print(f"\nhis cards profiled:  {len(his_cards)} of {len(expert_names)}")
    print(f"decks compared:      {len(runs)}")

    print(f"\n{'function':<26} {'his':>5} {'ours':>5}   verdict")
    print("-" * 70)
    for fn in sorted(set(his) | set(ref["ours"])):
        h, o = len(his.get(fn, [])), len(ref["ours"].get(fn, []))
        if h and not o:
            verdict = "GAP — he does this, we do not"
        elif o and not h:
            verdict = "we cover, he did not"
        else:
            verdict = "both"
        if h and h <= FRAGILE_AT:
            verdict += f"   [fragile: his n={h}]"
        print(f"{fn:<26} {h:>5} {o:>5}   {verdict}")

    pcts = sorted(r["pct"] for r in runs)
    solids = sorted(r["solid_pct"] for r in runs)
    always = set(runs[0]["gaps"]).intersection(*[set(r["gaps"]) for r in runs])
    sometimes = set().union(*[set(r["gaps"]) for r in runs]) - always

    print("\n" + "=" * 70)
    print(f"  functions he expresses:      {len(his)}")
    if len(runs) == 1:
        print(f"  coverage:                    {pcts[0]}%   "
              f"(SINGLE RUN — not a band; pass --deck again to measure spread)")
    else:
        print(f"  coverage band:               {pcts[0]}–{pcts[-1]}%   "
              f"median {statistics.median(pcts):.0f}%  over {len(runs)} runs")
    print(f"  on well-evidenced functions: {solids[0]}–{solids[-1]}%   "
          f"(his n>{FRAGILE_AT}; excludes the coin-flips)")
    print(f"  GAPS every run:              {len(always)}  {sorted(always)}")
    if sometimes:
        print(f"  gaps SOME runs (fragile):    {len(sometimes)}  {sorted(sometimes)}")
    print(f"  ours beyond his:             {len(ref['wins'])}  {ref['wins'][:8]}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
