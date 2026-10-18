#!/usr/bin/env python3
"""CON-322: bake CardFacts from the local corpus (no model, no live scrape).

Reads oracle rows from the local Scryfall bulk corpus (``corpus.py`` /
``oracle_cards.jsonl``), runs ``cardfacts.extract`` on each, and writes
``{DECKWRIGHT_HOME}/cardfacts.json`` in the format ``cardfacts.lookup`` expects.

Full-corpus bake on the demo host (resume-safe):

    source .venv/bin/activate
    python -m corpus                    # once if oracle_cards.jsonl missing
    python scripts/bake_cardfacts.py    # all cards; skip names already baked
    python scripts/cardfacts_status.py

Flags for scale:

    --limit N          cap newly extracted cards (0 = no cap)
    --skip-existing    default on — resume / skip names already in the store
    --force            re-extract even when the name is already baked
    --replace          ignore any existing store; write only this run
    --checkpoint N     flush the store every N newly extracted cards (0 = end only)
    --progress N       log every N newly considered cards (default 500)
    --status           print store summary and exit (no bake)

Contestant C stays disabled. This script does not wire CardFacts into
``/api/build``, strategy_steer, or card_score.

Exit 0 on success. Exit 2 when the corpus (or an unreadable resume store
without ``--replace``) is unknown — never report that as a successful empty bake.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402
from deckwright import corpus  # noqa: E402


def _iter_cards(
    *,
    jsonl: Path | None,
    names: set[str] | None,
) -> Iterator[dict[str, Any]]:
    """Yield corpus card dicts. Prefer --jsonl; else stream the local corpus.

    Raises ``FileNotFoundError`` eagerly (before the first yield) when the
    source is missing — callers treat that as unknown, not an empty bake.
    """
    name_low = {n.lower() for n in names} if names else None

    def _filter(card: dict[str, Any]) -> bool:
        if name_low is None:
            return True
        n = (card.get("name") or "").strip()
        return n.lower() in name_low

    if jsonl is not None:
        path = jsonl.expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"jsonl not found: {path}")
        for card in corpus.iter_jsonl(path):
            if _filter(card):
                yield card
        return

    # Stream oracle jsonl when present so a full bake does not require
    # materializing the entire Corpus.cards list first.
    oracle = corpus.ORACLE_JSONL
    if oracle.is_file():
        for card in corpus.iter_jsonl(oracle):
            if _filter(card):
                yield card
        return

    corp = corpus.load_default_corpus()
    if not corp.loaded or not corp.cards:
        raise FileNotFoundError(
            "local corpus missing or empty — "
            f"expected {corpus.ORACLE_JSONL} (or pass --jsonl)"
        )
    for card in corp.cards:
        if _filter(card):
            yield card


def _open_cards(
    *,
    jsonl: Path | None,
    names: set[str] | None,
) -> Iterator[dict[str, Any]]:
    """Return a card iterator after proving the source exists.

    Generators defer body execution until the first ``next``; this wrapper
    forces the missing-source check before bake counts look like zeros.
    """
    it = _iter_cards(jsonl=jsonl, names=names)
    try:
        first = next(it)
    except StopIteration:
        return iter(())
    except FileNotFoundError:
        raise

    def _chain() -> Iterator[dict[str, Any]]:
        yield first
        yield from it

    return _chain()

def _parse_names(raw: list[str] | None) -> set[str] | None:
    if not raw:
        return None
    out: set[str] = set()
    for item in raw:
        for part in item.split(","):
            name = part.strip()
            if name:
                out.add(name)
    return out or None


def _print_status(out_path: Path) -> int:
    cards = cf.read_store(out_path)
    summary = cf.summarize_cards(cards)
    if summary["status"] == "unknown":
        print(
            f"UNKNOWN: CardFacts store missing or unreadable at {out_path}",
            file=sys.stderr,
        )
        print(f"path={out_path}")
        print("store_status=unknown card_count=None")
        return 2
    print(f"path={out_path}")
    print(
        f"store_status=ok card_count={summary['card_count']} "
        f"ok={summary['ok']} partial={summary['partial']} "
        f"unread={summary['unread']} other={summary['other']}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--limit", type=int, default=0,
        help="max cards to newly extract this run (0 = all matching)",
    )
    ap.add_argument(
        "--names", action="append", default=None,
        help="only these card names (repeat or comma-separated)",
    )
    ap.add_argument(
        "--jsonl", type=Path, default=None,
        help="read cards from this jsonl instead of the default corpus",
    )
    ap.add_argument(
        "--path", type=Path, default=None,
        help="output path (default: cardfacts.store_path())",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="extract and log; do not write the store",
    )
    ap.add_argument(
        "--progress", type=int, default=500,
        help="log every N cards considered (0 = quiet except summary)",
    )
    ap.add_argument(
        "--checkpoint", type=int, default=1000,
        help="write the store every N newly extracted cards (0 = only at end)",
    )
    ap.add_argument(
        "--skip-existing", action=argparse.BooleanOptionalAction, default=True,
        help="skip names already in the store (default: true; resume-safe)",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="re-extract even when the name is already baked (implies no skip)",
    )
    ap.add_argument(
        "--replace", action="store_true",
        help="ignore any existing store; write only cards from this run",
    )
    ap.add_argument(
        "--status", action="store_true",
        help="print store summary and exit (no bake)",
    )
    args = ap.parse_args()

    out_path = (args.path or cf.store_path()).expanduser()

    if args.status:
        return _print_status(out_path)

    skip_existing = bool(args.skip_existing) and not args.force
    cards: dict[str, dict[str, Any]] = {}
    existing_names: set[str] = set()

    if not args.replace:
        existing = cf.read_store(out_path)
        if out_path.is_file() and existing is None:
            # File present but unreadable — unknown, not "start from zero".
            print(
                f"UNKNOWN: existing CardFacts store unreadable at {out_path} "
                "(pass --replace to discard, or fix the file)",
                file=sys.stderr,
            )
            return 2
        if existing is not None:
            cards = dict(existing)
            existing_names = set(cards.keys())
            print(
                f"[bake_cardfacts] resume loaded {len(cards)} existing cards "
                f"from {out_path}",
                flush=True,
            )

    names = _parse_names(args.names)
    try:
        cards_iter = _open_cards(jsonl=args.jsonl, names=names)
    except FileNotFoundError as e:
        # House rule: missing corpus is unknown, not "0 cards baked".
        print(f"UNKNOWN: {e}", file=sys.stderr)
        return 2

    counts = {
        "ok": 0, "partial": 0, "unread": 0,
        "skipped": 0, "skipped_existing": 0, "extracted": 0,
    }
    t0 = time.monotonic()
    considered = 0
    since_checkpoint = 0

    for card in cards_iter:
        name = (card.get("name") or "").strip()
        if not name:
            counts["skipped"] += 1
            continue
        considered += 1
        if skip_existing and name in existing_names:
            counts["skipped_existing"] += 1
            if args.progress and considered % args.progress == 0:
                elapsed = time.monotonic() - t0
                print(
                    f"[bake_cardfacts] considered={considered} "
                    f"extracted={counts['extracted']} "
                    f"skipped_existing={counts['skipped_existing']} "
                    f"store={len(cards)} ({elapsed:.1f}s)",
                    flush=True,
                )
            continue

        facts = cf.extract(card)
        cards[name] = facts
        existing_names.add(name)
        status = facts.get("status") or "unread"
        counts[status] = counts.get(status, 0) + 1
        counts["extracted"] += 1
        since_checkpoint += 1

        if args.progress and considered % args.progress == 0:
            elapsed = time.monotonic() - t0
            print(
                f"[bake_cardfacts] considered={considered} "
                f"extracted={counts['extracted']} "
                f"ok={counts['ok']} partial={counts['partial']} "
                f"unread={counts['unread']} "
                f"skipped_existing={counts['skipped_existing']} "
                f"store={len(cards)} ({elapsed:.1f}s)",
                flush=True,
            )

        if (
            not args.dry_run
            and args.checkpoint
            and since_checkpoint >= args.checkpoint
        ):
            written = cf.write_store(cards, path=out_path)
            print(
                f"[bake_cardfacts] checkpoint wrote {written} "
                f"({len(cards)} cards)",
                flush=True,
            )
            since_checkpoint = 0

        if args.limit and counts["extracted"] >= args.limit:
            break

    elapsed = time.monotonic() - t0
    print(
        f"[bake_cardfacts] done considered={considered} "
        f"extracted={counts['extracted']} "
        f"ok={counts['ok']} partial={counts['partial']} "
        f"unread={counts['unread']} skipped={counts['skipped']} "
        f"skipped_existing={counts['skipped_existing']} "
        f"store={len(cards)} ({elapsed:.1f}s)",
        flush=True,
    )

    if args.dry_run:
        print(
            f"[bake_cardfacts] dry-run — would write {len(cards)} cards → {out_path}"
        )
        return 0

    if counts["extracted"] == 0 and names and counts["skipped_existing"] == 0:
        # Named filter matched nothing new and nothing already present —
        # unknown, not an empty successful bake.
        print(
            "UNKNOWN: no corpus rows matched --names "
            f"{sorted(names)!r}",
            file=sys.stderr,
        )
        return 2

    written = cf.write_store(cards, path=out_path)
    print(f"[bake_cardfacts] wrote {written} ({len(cards)} cards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
