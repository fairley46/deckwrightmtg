#!/usr/bin/env python3
"""
bake_precons.py — one-shot batch job for the precon bake.

Computes overview + signals + seeded simulation + resolved list for each
Archidekt precon and writes data/precons/{id}.json. Prices are stripped;
the app refreshes them at view time.

Usage:
  source .venv/bin/activate
  python scripts/bake_precons.py              # all catalog precons
  python scripts/bake_precons.py --limit 3    # first N (smoke)
  python scripts/bake_precons.py --id 12345   # one deck
  python scripts/bake_precons.py --force      # rewrite even when stamps are fresh
  python scripts/bake_precons.py --stale      # only entries whose stamps no longer match

Needs ANTHROPIC_API_KEY (or Claude via llm.py) for model overviews.
With --skip-overview, only Archidekt + Scryfall + the local simulator —
no Anthropic call; overview.from_counts() fills a serveable overview.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import argparse
import asyncio
import sys

from deckwright import app
from deckwright import card_score
from deckwright import overview
from deckwright import precon_bake
from deckwright import precons
from deckwright import sim


async def bake_one(precon_id: str, *, force: bool = False,
                   skip_overview: bool = False) -> str:
    """Bake one precon. Returns status: wrote | skipped | error:…"""
    existing = precon_bake.read_bake(precon_id)
    if existing and not force and precon_bake.analysis_fresh(existing):
        # Still verify the list hash against Archidekt when we can.
        try:
            text, explicit, meta = await precons.decklist_for_precon(precon_id)
        except Exception as e:
            return f"skipped (fresh, archidekt unreachable: {type(e).__name__})"
        result = await app.import_deck.resolve_decklist(
            text, app.scry_post, app.trim_card, explicit)
        if result["commander_pick"]["status"] == "found" and \
           precon_bake.analysis_fresh(existing, result["deck"]):
            return "skipped (fresh)"
        # Hash drifted — fall through and rewrite.

    try:
        text, explicit, meta = await precons.decklist_for_precon(precon_id)
    except Exception as e:
        return f"error: archidekt {type(e).__name__}: {e}"

    result = await app.import_deck.resolve_decklist(
        text, app.scry_post, app.trim_card, explicit)
    pick = result["commander_pick"]
    if pick["status"] != "found":
        return f"error: commander {pick.get('status')}: {pick.get('reason')}"

    deck = result["deck"]
    cmdr_card = result["commander_card"]
    cmdr_name = pick["commander"]
    signals = app.deck_signals(deck)
    cmc = int(cmdr_card.get("cmc") or 5)
    # Bootstrap sim feeds overview curves; the served sim re-runs against the
    # brief-derived thesis conditions once the overview exists.
    boot = await asyncio.to_thread(
        precon_bake.run_precon_sim, deck, cmc, overview.BUILD_CONDITIONS)

    if skip_overview:
        # Countable overview — no model. Serveable (complete); prose is thinner
        # than generate_overview but every claim traces to signals/sim.
        overview_data = overview.from_counts(cmdr_name, deck, signals, boot)
    else:
        try:
            overview_data = await overview.generate_overview(
                app.client, app.MODEL, cmdr_name, cmdr_card, deck, {},
                signals, boot, app.for_model, app.parse_json_list)
        except Exception as e:
            return f"error: overview {type(e).__name__}: {e}"

    brief = precon_bake.brief_from_analysis(
        overview_data, deck, cmdr_card, signals)
    conditions = card_score.provisional_conditions(brief)
    sim_out = await asyncio.to_thread(
        precon_bake.run_precon_sim, deck, cmc, conditions)
    if skip_overview:
        # Beats/how_it_works should cite the same conditions the chart shows.
        overview_data = overview.from_counts(cmdr_name, deck, signals, sim_out)
        brief = precon_bake.brief_from_analysis(
            overview_data, deck, cmdr_card, signals)
        new_conds = card_score.provisional_conditions(brief)
        if [c["label"] for c in new_conds] != [c["label"] for c in conditions]:
            conditions = new_conds
            sim_out = await asyncio.to_thread(
                precon_bake.run_precon_sim, deck, cmc, conditions)
            overview_data = overview.from_counts(
                cmdr_name, deck, signals, sim_out)
            brief = precon_bake.brief_from_analysis(
                overview_data, deck, cmdr_card, signals)

    payload = precon_bake.build_payload(
        precon_id=meta["id"],
        name=meta.get("name") or "",
        source=meta.get("source") or "",
        commander=cmdr_name,
        commander_card=cmdr_card,
        deck=deck,
        signals=signals,
        overview_data=overview_data,
        sim_out=sim_out,
        brief=brief,
        conditions=conditions,
    )
    path = precon_bake.write_bake(payload)
    note = " (from_counts)" if skip_overview else ""
    return f"wrote {path.name} hash={payload['deck_hash'][:12]}{note}"


def refresh_local(precon_id: str, *, force: bool = False) -> str:
    """rewrite brief + strategy-aware sim from an existing bake.

    Keeps the shipped overview and card list — no Archidekt, no model. Used to
    bump analysis_version across the 155 without a full remote rebake.
    """
    existing = precon_bake.read_bake(precon_id)
    if not existing:
        return "error: no bake on disk"
    if not force and precon_bake.analysis_fresh(existing):
        return "skipped (fresh)"

    deck = list(existing.get("deck") or [])
    cmdr_card = dict(existing.get("commander_card") or {})
    if not deck or not cmdr_card:
        return "error: bake missing deck or commander_card"
    overview_data = existing.get("overview") or {}
    if not (overview_data.get("strategy") or overview_data.get("how_it_works")):
        return "error: bake has empty overview"
    signals = existing.get("signals") or {}
    brief = precon_bake.brief_from_analysis(
        overview_data, deck, cmdr_card, signals)
    conditions = card_score.provisional_conditions(brief)
    sim_out = precon_bake.run_precon_sim(
        deck, int(cmdr_card.get("cmc") or 5), conditions)
    payload = precon_bake.build_payload(
        precon_id=str(existing.get("id") or precon_id),
        name=existing.get("name") or "",
        source=existing.get("source") or "",
        commander=existing.get("commander") or cmdr_card.get("name") or "",
        commander_card=cmdr_card,
        deck=deck,
        signals=signals,
        overview_data=overview_data,
        sim_out=sim_out,
        brief=brief,
        conditions=conditions,
    )
    # Preserve any extra keys (e.g. complete=False) only when still meaningful.
    if existing.get("complete") is False:
        payload["complete"] = False
    path = precon_bake.write_bake(payload)
    return (f"wrote {path.name} brief.theme={brief.get('theme')!r} "
            f"conditions={[c['label'] for c in conditions]}")


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", action="append", dest="ids",
                   help="Bake one Archidekt deck id (repeatable)")
    p.add_argument("--limit", type=int, default=0,
                   help="Bake only the first N catalog entries")
    p.add_argument("--force", action="store_true",
                   help="Rewrite even when stamps are fresh")
    p.add_argument("--stale", action="store_true",
                   help="Only rewrite entries with mismatched stamps")
    p.add_argument("--skip-overview", action="store_true",
                   help="Skip the model; write overview.from_counts() from "
                        "signals + sim (serveable, no Anthropic key needed)")
    p.add_argument("--refresh-local", action="store_true",
                   help="add brief + re-sim from on-disk bakes only "
                        "(no Archidekt, no model)")
    args = p.parse_args(argv)

    await app.load_vocab()

    if args.ids:
        targets = [str(i) for i in args.ids]
    elif args.refresh_local:
        targets = precon_bake.list_baked_ids()
        if args.limit:
            targets = targets[: args.limit]
    else:
        catalog = await precons.get_catalog()
        targets = [str(item["id"]) for item in catalog]
        if args.limit:
            targets = targets[: args.limit]

    if args.stale:
        kept = []
        for tid in targets:
            cached = precon_bake.read_bake(tid)
            if not cached or not precon_bake.analysis_fresh(cached):
                kept.append(tid)
        targets = kept

    if not targets:
        print("nothing to bake")
        return 0

    print(f"baking {len(targets)} precon(s) → {precon_bake.DATA_DIR}")
    print(f"analysis_version={precon_bake.ANALYSIS_VERSION} "
          f"sim_version={sim.SIM_VERSION} seed={precon_bake.PRECON_SIM_SEED}")
    if args.refresh_local:
        print("mode=refresh-local (brief + strategy-aware sim from disk)")

    wrote = skipped = errors = 0
    for i, tid in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {tid} …", flush=True)
        if args.refresh_local:
            status = refresh_local(tid, force=args.force or args.stale)
        else:
            status = await bake_one(
                tid, force=args.force or args.stale,
                skip_overview=args.skip_overview)
        print(f"  {status}", flush=True)
        if status.startswith("wrote"):
            wrote += 1
        elif status.startswith("skipped"):
            skipped += 1
        else:
            errors += 1

    print(f"done: wrote={wrote} skipped={skipped} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
