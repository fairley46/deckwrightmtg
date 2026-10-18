"""
hei_bai_agreement.py — Close and Better against the expert's chosen cards.

CON-177: name overlap against the contaminated 50 understates us. Score only
against `chosen`. Two numbers, both falsifiable:

  Close   of his chosen cards, how many did we independently pick — and, for
          those we did not, does synergy.edge_count() rate them well-connected?
          That second count separates recall (never found) from taste (found,
          ranked low).

  Better  a card we pick that he did not counts as better only if it has at
          least the median edge count of his chosen cards, and is legal and
          on-plan (has at least one edge to the commander). Without that floor,
          "or better" is unfalsifiable.

95% confidence means the score is stable. Require three consecutive runs
inside a stated band before claiming any number.

    python scripts/hei_bai_agreement.py --deck /tmp/hb_now.json
    python scripts/hei_bai_agreement.py --deck a.json --deck b.json --deck c.json --band 5
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import synergy  # noqa: E402

EXPERT = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-cards.json"
ORACLE = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-oracle.json"
COMMANDER = "Hei Bai, Spirit of Balance"


def _card(row: dict) -> dict:
    """Normalise cache/live shapes to what synergy.profile reads."""
    return {
        "name": row.get("name"),
        "oracle_text": row.get("oracle_text") or row.get("text") or "",
        "text": row.get("text") or row.get("oracle_text") or "",
        "type_line": row.get("type_line") or row.get("type") or "",
        "type": row.get("type") or row.get("type_line") or "",
        "card_faces": row.get("card_faces") or [],
        "color_identity": list(row.get("color_identity") or []),
    }


def load_oracle(names: list[str]) -> dict[str, dict]:
    """Oracle rows for the names we need. Unknowns stay omitted — not zeros."""
    cached = json.loads(ORACLE.read_text()) if ORACLE.exists() else {}
    out = {n: _card(cached[n]) for n in names if n in cached}
    missing = [n for n in dict.fromkeys(names) if n not in out]
    if not missing:
        return out

    import asyncio
    from deckwright import app

    async def go():
        for i in range(0, len(missing), 75):
            chunk = missing[i:i + 75]
            res = await app.scry_post(
                "/cards/collection",
                {"identifiers": [{"name": n} for n in chunk]},
            )
            for card in res.get("data") or []:
                out[card["name"]] = _card({
                    "name": card.get("name"),
                    "text": card.get("oracle_text") or "",
                    "type_line": card.get("type_line") or "",
                    "card_faces": card.get("card_faces") or [],
                    "color_identity": list(card.get("color_identity") or []),
                })
            for miss in res.get("not_found") or []:
                print(f"  UNRESOLVED {miss.get('name')}")

    asyncio.run(go())
    return out


def score(his_chosen: list[dict], our_deck: list[dict],
          commander: dict) -> dict:
    """Close + Better for one of our builds against his chosen cards."""
    his = [_card(c) for c in his_chosen]
    ours = [_card(c) for c in our_deck]
    our_names = {c["name"] for c in ours}
    his_names = [c["name"] for c in his]

    agreed = [n for n in his_names if n in our_names]
    missing = [n for n in his_names if n not in our_names]

    his_edges = {
        c["name"]: synergy.edge_count(c, commander, [])
        for c in his
        if synergy.profile(c)["status"] != synergy.UNKNOWN
    }
    # Median of HIS chosen edge counts. A missing card is "connected" when its
    # own edge_count clears that median — well-connected by his own standard.
    edge_vals = list(his_edges.values())
    median = statistics.median(edge_vals) if edge_vals else 0

    connected_not_picked = [
        n for n in missing
        if his_edges.get(n, 0) >= median and his_edges.get(n, 0) > 0
    ]
    unconnected_missing = [n for n in missing if n not in connected_not_picked]

    # Better: our cards he did not choose, on-plan (edge to commander), and at
    # least the median edge count of his chosen set.
    better = []
    for c in ours:
        if c["name"] in his_names:
            continue
        if synergy.profile(c)["status"] == synergy.UNKNOWN:
            continue
        n = synergy.edge_count(c, commander, [])
        if n >= median and n > 0:
            better.append({"name": c["name"], "edge_count": n})

    chosen_n = len(his_names)
    agreement_pct = (100.0 * len(agreed) / chosen_n) if chosen_n else 0.0
    return {
        "chosen_n": chosen_n,
        "agreement_count": len(agreed),
        "agreement_pct": round(agreement_pct, 1),
        "agreed": agreed,
        "missing": missing,
        "median_edge_count": median,
        "connected_not_picked": connected_not_picked,
        "connected_not_picked_n": len(connected_not_picked),
        "unconnected_missing": unconnected_missing,
        "better": better,
        "better_n": len(better),
    }


def within_band(pcts: list[float], band: float) -> bool:
    """Three consecutive runs inside `band` percentage points."""
    if len(pcts) < 3:
        return False
    return (max(pcts) - min(pcts)) <= band


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", action="append", default=[],
                    help="Our build JSON (hei_bai_baseline.py --out). "
                         "Pass three times to check reproducibility.")
    ap.add_argument("--band", type=float, default=5.0,
                    help="Max spread across runs before the claim is unstable.")
    args = ap.parse_args()
    if not args.deck:
        ap.error("pass at least one --deck")

    expert = json.loads(EXPERT.read_text())
    chosen_names = [r["name"] for r in expert.get("chosen") or []]
    if not chosen_names:
        print("fixture has no chosen[] — run scripts/extract_expert_deck.py")
        return 1

    reports = []
    for path in args.deck:
        payload = json.loads(pathlib.Path(path).read_text())
        our_names = payload.get("deck") or []
        oracle = load_oracle(chosen_names + our_names + [COMMANDER])
        if COMMANDER not in oracle:
            print(f"UNRESOLVED commander {COMMANDER}")
            return 1
        his = [oracle[n] for n in chosen_names if n in oracle]
        ours = [oracle[n] for n in our_names if n in oracle]
        unknown_ours = len(our_names) - len(ours)
        if unknown_ours:
            print(f"  ({path}) {unknown_ours} of our cards unresolved — "
                  f"held out, not scored as zero")
        rep = score(his, ours, oracle[COMMANDER])
        rep["deck_path"] = path
        reports.append(rep)

        print(f"\n{path}")
        print(f"  chosen denominator:     {rep['chosen_n']}")
        print(f"  we also picked:         {rep['agreement_count']}  "
              f"({rep['agreement_pct']}%)")
        print(f"  connected not picked:   {rep['connected_not_picked_n']}  "
              f"(median edge_count={rep['median_edge_count']})")
        print(f"  unconnected missing:    {len(rep['unconnected_missing'])}")
        print(f"  better (our extras):    {rep['better_n']}")
        if rep["agreed"]:
            print(f"  agreed: {', '.join(rep['agreed'][:12])}"
                  + ("…" if len(rep['agreed']) > 12 else ""))
        if rep["connected_not_picked"]:
            print(f"  connected-not-picked: "
                  f"{', '.join(rep['connected_not_picked'][:8])}")

    pcts = [r["agreement_pct"] for r in reports]
    if len(reports) >= 3:
        stable = within_band(pcts, args.band)
        print("\n" + "=" * 60)
        print(f"  three-run agreement %:  {pcts}")
        print(f"  band:                   {args.band} pp")
        print(f"  stable:                 {stable}")
        print("=" * 60)
        return 0 if stable else 1
    if len(reports) == 1:
        print("\n(pass two more --deck files to test the reproducibility band)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
