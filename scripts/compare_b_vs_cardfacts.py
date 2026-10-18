#!/usr/bin/env python3
"""compare a Candidate-B stamped deck against CardFacts extract.

Offline observability for the bake-off — what would change when contestant C
is fully implemented. Does **not** enable contestant C, and does **not** wire
CardFacts into ``/api/build``, steer, or score.

Inputs
------
* A saved B build JSON (or this repo's fixture) with optional per-card stamps
  ``steer_vote`` / ``bound_ok`` / ``feeds_input``, plus ``commander_card`` /
  ``commander_strategy``.
* CardFacts store (``cardfacts.lookup``). Missing store or missing card →
  **unknown**, never empty-as-zero.

    source .venv/bin/activate
    python scripts/compare_b_vs_cardfacts.py \\
        --deck testdata/benchmarks/bello_b_fixture.json \\
        --bake-deck --jsonl testdata/benchmarks/bello_b_fixture_cards.jsonl

    # Demo host, against a real Bello B build:
    python scripts/compare_b_vs_cardfacts.py \\
        --deck /path/to/bello_b_build.json --bake-deck

Exit 0 on a completed report. Exit 2 when the deck file is missing/unreadable
(unknown), or when ``--bake-deck`` cannot produce facts (corpus/jsonl miss).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import cardfacts as cf  # noqa: E402

# Event → output kinds that can feed that strategy input (coarse ServeReport
# preview — not a rules engine, not wired into build).
_INPUT_TO_OUTPUT_KINDS: dict[str, frozenset[str]] = {
    "gain_life": frozenset({"life"}),
    "draw": frozenset({"draw"}),
    "combat_damage": frozenset({"tokens"}),  # token makers may become attackers
    "dies": frozenset({"recur_to_battlefield", "recur_to_hand"}),
    "etb_creature": frozenset({"tokens"}),
}


def _load_deck(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deck JSON must be an object")
    return raw


def _deck_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept B build shape (deck:[{name,...}]) or a bare names list."""
    deck = payload.get("deck")
    if isinstance(deck, list) and deck:
        out: list[dict[str, Any]] = []
        for item in deck:
            if isinstance(item, str):
                out.append({"name": item})
            elif isinstance(item, dict) and (item.get("name") or "").strip():
                out.append(dict(item))
        return out
    names = payload.get("names") or payload.get("cards")
    if isinstance(names, list) and names:
        return [
            {"name": n} if isinstance(n, str) else dict(n)
            for n in names
            if (isinstance(n, str) and n.strip())
            or (isinstance(n, dict) and (n.get("name") or "").strip())
        ]
    raise ValueError("deck JSON needs a non-empty 'deck' or 'names' list")


def _commander_card(payload: dict[str, Any]) -> dict[str, Any]:
    cc = payload.get("commander_card")
    if isinstance(cc, dict) and (cc.get("name") or "").strip():
        return dict(cc)
    name = (payload.get("commander") or "").strip()
    if name:
        return {"name": name}
    return {}


def _strategy(payload: dict[str, Any], commander: dict[str, Any]) -> dict[str, Any]:
    """Prefer stamped commander_strategy; else CardFacts extract of commander."""
    strat = payload.get("commander_strategy")
    if isinstance(strat, dict) and strat.get("status") != "unread":
        return dict(strat)
    if not commander:
        return {"status": "unread", "inputs": [], "outputs": [], "bounds": []}
    # Bake-path extract is Layer-1; shell fields match Layer-2 enough for preview.
    facts = cf.extract(commander)
    return {
        "status": facts.get("status") or "unread",
        "inputs": list(facts.get("inputs") or []),
        "outputs": list(facts.get("outputs") or []),
        "bounds": list(facts.get("bounds") or []),
        "honesty": facts.get("honesty"),
        "source": "cardfacts.extract",
    }


def _cmc(card: dict[str, Any]) -> float | None:
    for key in ("cmc", "mana_value"):
        if key in card and card[key] is not None:
            try:
                return float(card[key])
            except (TypeError, ValueError):
                return None
    return None


def _type_line(card: dict[str, Any]) -> str:
    return (card.get("type_line") or card.get("type") or "").strip()


def _is_artifact(card: dict[str, Any]) -> bool:
    return "artifact" in _type_line(card).lower()


def _is_creature(card: dict[str, Any]) -> bool:
    return "creature" in _type_line(card).lower()


def _mv_subject_applies(card: dict[str, Any], subject: str) -> bool:
    if subject == "artifact_mv":
        return _is_artifact(card)
    if subject == "creature_mv":
        return _is_creature(card)
    if subject == "card_mv":
        return True
    return False


def _preview_bound_ok(card: dict[str, Any], strategy: dict[str, Any]) -> str:
    """Static MV bound preview (ServeReport-shaped). Unknown ≠ pass."""
    bounds = strategy.get("bounds") or []
    if not bounds:
        return "ok"
    saw_unknown = False
    cmc = _cmc(card)
    for b in bounds:
        bound = b.get("bound") or {}
        subject = b.get("subject") or ""
        op = (b.get("op") or "leq").lower()
        mode = bound.get("mode")
        if mode != "static" or subject not in (
            "artifact_mv", "creature_mv", "card_mv",
        ):
            if mode == "event_capacity":
                saw_unknown = True
            continue
        if not _mv_subject_applies(card, subject):
            continue
        try:
            limit = float(bound["value"])
        except (KeyError, TypeError, ValueError):
            saw_unknown = True
            continue
        if cmc is None:
            saw_unknown = True
            continue
        if op == "geq":
            if cmc < limit:
                return "false"
        else:
            if cmc > limit:
                return "false"
    if saw_unknown:
        return "unknown"
    return "ok"


def _preview_feeds(card: dict[str, Any], strategy: dict[str, Any],
                   facts: dict[str, Any] | None) -> bool | None:
    """True/False when CF present; None when CF missing (unknown)."""
    if facts is None:
        return None
    inputs = strategy.get("inputs") or []
    if not inputs:
        return False
    cf_events = {
        (i.get("event") or "") for i in (facts.get("inputs") or []) if i.get("event")
    }
    cf_kinds = {
        (o.get("kind") or "") for o in (facts.get("outputs") or []) if o.get("kind")
    }
    for inp in inputs:
        event = inp.get("event") or ""
        if not event:
            continue
        if event in cf_events:
            return True
        related = _INPUT_TO_OUTPUT_KINDS.get(event) or frozenset()
        if cf_kinds & related:
            return True
        # Structural feeder under a geq MV floor: subject-matching cards that
        # satisfy the floor are on-plan for that commander (Bello-shaped).
        for b in strategy.get("bounds") or []:
            bound = b.get("bound") or {}
            if bound.get("mode") != "static":
                continue
            if (b.get("op") or "").lower() != "geq":
                continue
            subject = b.get("subject") or ""
            if not _mv_subject_applies(card, subject):
                continue
            try:
                limit = float(bound["value"])
            except (KeyError, TypeError, ValueError):
                continue
            cmc = _cmc(card)
            if cmc is not None and cmc >= limit:
                return True
    return False


def _bake_deck_names(
    names: list[str],
    *,
    jsonl: Path | None,
    store_path: Path,
) -> None:
    """Bake CardFacts for the named cards into store_path.

    Prefer --jsonl (offline fixture). Else local corpus. Missing source →
    raise FileNotFoundError (caller exits 2 — unknown, not empty bake).
    """
    from deckwright import corpus

    name_set = {n for n in names if n}
    name_low = {n.lower() for n in name_set}
    cards_out: dict[str, dict[str, Any]] = {}

    if jsonl is not None:
        path = jsonl.expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"jsonl not found: {path}")
        for card in corpus.iter_jsonl(path):
            n = (card.get("name") or "").strip()
            if n.lower() not in name_low:
                continue
            cards_out[n] = cf.extract(card)
    else:
        corp = corpus.load_default_corpus()
        if not corp.loaded or not corp.cards:
            raise FileNotFoundError(
                "local corpus missing or empty — "
                f"expected {corpus.ORACLE_JSONL} (or pass --jsonl)"
            )
        for card in corp.cards:
            n = (card.get("name") or "").strip()
            if n.lower() not in name_low:
                continue
            cards_out[n] = cf.extract(card)

    # Also extract from inline oracle on the deck cards when corpus missed.
    # (Handled by caller via --bake-from-deck-fields.)

    if not cards_out:
        raise FileNotFoundError(
            f"no corpus/jsonl rows matched deck names ({len(name_set)} asked)"
        )

    # Merge into existing store when present (unknown≠wipe).
    existing = cf.read_store(store_path)
    if existing is not None:
        merged = dict(existing)
        merged.update(cards_out)
        cards_out = merged

    cf.write_store(cards_out, path=store_path)


def _bake_from_deck_fields(
    cards: list[dict[str, Any]],
    commander: dict[str, Any],
    store_path: Path,
) -> int:
    """Extract from oracle text already on the deck JSON. Returns cards baked."""
    existing = cf.read_store(store_path) or {}
    n = 0
    for card in ([commander] if commander else []) + cards:
        name = (card.get("name") or "").strip()
        text = (card.get("oracle_text") or card.get("text") or "").strip()
        if not name or not text:
            continue
        existing[name] = cf.extract(card)
        n += 1
    if n:
        cf.write_store(existing, path=store_path)
    return n


def compare(
    payload: dict[str, Any],
    *,
    require_cf: bool = False,
) -> dict[str, Any]:
    """Build the B-vs-CardFacts report. Pure — no bake, no I/O beyond lookup."""
    cards = _deck_cards(payload)
    commander = _commander_card(payload)
    strategy = _strategy(payload, commander)

    store = cf.store_status()
    rows: list[dict[str, Any]] = []
    b_violations: list[str] = []
    cf_violations: list[str] = []
    agree: list[str] = []
    disagree: list[str] = []
    cf_only: list[str] = []
    cf_unknown_names: list[str] = []
    b_feeders: list[str] = []
    cf_feeders: list[str] = []
    feeder_both: list[str] = []
    feeder_b_only: list[str] = []
    feeder_cf_only: list[str] = []

    for card in cards:
        name = (card.get("name") or "").strip()
        b_vote = card.get("steer_vote") if "steer_vote" in card else None
        b_bound = card.get("bound_ok") if "bound_ok" in card else None
        b_feed = card.get("feeds_input") if "feeds_input" in card else None
        has_b_stamps = "bound_ok" in card

        facts = cf.lookup(name)
        if facts is None:
            cf_unknown_names.append(name)
            cf_bound: str | None = None
            cf_feed: bool | None = None
            cf_status = "unknown"
        else:
            cf_status = str(facts.get("status") or "unread")
            cf_bound = _preview_bound_ok(card, strategy)
            cf_feed = _preview_feeds(card, strategy, facts)

        if has_b_stamps and b_bound == "false":
            b_violations.append(name)
        if cf_bound == "false":
            cf_violations.append(name)

        if has_b_stamps and b_bound == "false":
            if facts is None or cf_bound is None or cf_bound == "unknown":
                pass  # unknown — do not score as agree or disagree
            elif cf_bound == "false":
                agree.append(name)
            else:
                disagree.append(name)

        if cf_bound == "false" and (not has_b_stamps or b_bound != "false"):
            cf_only.append(name)

        if b_feed is True:
            b_feeders.append(name)
        if cf_feed is True:
            cf_feeders.append(name)
        if b_feed is True and cf_feed is True:
            feeder_both.append(name)
        elif b_feed is True and cf_feed is False:
            feeder_b_only.append(name)
        elif b_feed is not True and cf_feed is True:
            feeder_cf_only.append(name)

        row = {
            "name": name,
            "cmc": _cmc(card),
            "type_line": _type_line(card),
            "b": {
                "present": has_b_stamps,
                "steer_vote": b_vote if has_b_stamps else None,
                "bound_ok": b_bound if has_b_stamps else None,
                "feeds_input": b_feed if has_b_stamps else None,
            },
            "cardfacts": {
                "status": cf_status,
                "inputs": None if facts is None else list(facts.get("inputs") or []),
                "outputs": None if facts is None else list(facts.get("outputs") or []),
                "bounds": None if facts is None else list(facts.get("bounds") or []),
                "free_value": None if facts is None else facts.get("free_value"),
                "preview_bound_ok": cf_bound,
                "preview_feeds_input": cf_feed,
            },
        }
        rows.append(row)

    summary = {
        "deck_size": len(cards),
        "commander": (commander.get("name") or payload.get("commander") or ""),
        "strategy_status": strategy.get("status"),
        "cardfacts_store": store,
        "b_violations": b_violations,
        "b_violation_count": len(b_violations),
        "cf_preview_violations": cf_violations,
        "cf_preview_violation_count": len(cf_violations),
        "b_violations_cf_agrees": agree,
        "b_violations_cf_disagrees": disagree,
        "b_violations_cf_unknown": [
            n for n in b_violations
            if n in cf_unknown_names or any(
                r["name"] == n and r["cardfacts"]["preview_bound_ok"] in (None, "unknown")
                for r in rows
            )
        ],
        "cf_flags_b_did_not": cf_only,
        "cardfacts_unknown_cards": cf_unknown_names,
        "cardfacts_unknown_count": len(cf_unknown_names),
        "feeders": {
            "b": b_feeders,
            "cf_preview": cf_feeders,
            "both": feeder_both,
            "b_only": feeder_b_only,
            "cf_only": feeder_cf_only,
        },
        "note": (
            "preview_bound_ok / preview_feeds_input are ServeReport-shaped "
            "reads of strategy + CardFacts — not contestant C selection. "
            "C is still unwired from /api/build."
        ),
    }

    if require_cf and cf_unknown_names:
        summary["incomplete"] = True

    return {
        "summary": summary,
        "strategy": {
            "status": strategy.get("status"),
            "inputs": strategy.get("inputs") or [],
            "outputs": strategy.get("outputs") or [],
            "bounds": strategy.get("bounds") or [],
        },
        "cards": rows,
    }


def _print_human(report: dict[str, Any]) -> None:
    s = report["summary"]
    print(
        f"commander={s['commander']!r} deck={s['deck_size']} "
        f"cf_store={s['cardfacts_store']} strategy={s['strategy_status']}"
    )
    print(
        f"B violations: {s['b_violation_count']}  "
        f"CF preview violations: {s['cf_preview_violation_count']}  "
        f"CF unknown cards: {s['cardfacts_unknown_count']}"
    )
    print(
        f"  B∩CF agree: {len(s['b_violations_cf_agrees'])}  "
        f"B vs CF disagree: {len(s['b_violations_cf_disagrees'])}  "
        f"CF-only flags: {len(s['cf_flags_b_did_not'])}"
    )
    feed = s["feeders"]
    print(
        f"feeders B={len(feed['b'])} CF_preview={len(feed['cf_preview'])} "
        f"both={len(feed['both'])} b_only={len(feed['b_only'])} "
        f"cf_only={len(feed['cf_only'])}"
    )
    print("--- per card ---")
    for row in report["cards"]:
        b = row["b"]
        c = row["cardfacts"]
        b_part = (
            f"B vote={b['steer_vote']!s} bound={b['bound_ok']!s} feed={b['feeds_input']!s}"
            if b["present"] else "B (no stamps)"
        )
        outs = c["outputs"]
        out_kinds = (
            ",".join(sorted({o.get("kind") or "?" for o in outs}))
            if isinstance(outs, list) else "unknown"
        )
        print(
            f"{row['name']}: {b_part} | "
            f"CF status={c['status']} preview_bound={c['preview_bound_ok']!s} "
            f"preview_feed={c['preview_feeds_input']!s} outputs=[{out_kinds}]"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--deck", type=Path, required=True,
        help="B build JSON (or fixture) with deck + optional stamps",
    )
    ap.add_argument(
        "--bake-deck", action="store_true",
        help="bake CardFacts for deck names before compare",
    )
    ap.add_argument(
        "--jsonl", type=Path, default=None,
        help="oracle jsonl for --bake-deck (offline fixture path)",
    )
    ap.add_argument(
        "--bake-from-deck-fields", action="store_true",
        help="also extract from oracle_text/text already on the deck JSON",
    )
    ap.add_argument(
        "--store", type=Path, default=None,
        help="CardFacts store path (default: cardfacts.store_path())",
    )
    ap.add_argument(
        "--json-out", type=Path, default=None,
        help="write full report JSON here",
    )
    ap.add_argument(
        "--quiet", action="store_true",
        help="suppress human table (JSON only when --json-out or stdout)",
    )
    args = ap.parse_args()

    deck_path = args.deck.expanduser()
    if not deck_path.is_file():
        print(f"UNKNOWN: deck not found: {deck_path}", file=sys.stderr)
        return 2
    try:
        payload = _load_deck(deck_path)
        cards = _deck_cards(payload)
        commander = _commander_card(payload)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"UNKNOWN: cannot read deck: {e}", file=sys.stderr)
        return 2

    store_path = (args.store or cf.store_path()).expanduser()
    if args.store:
        import os
        # Point lookup at the chosen store via DECKWRIGHT_HOME parent.
        os.environ["DECKWRIGHT_HOME"] = str(store_path.parent)
        cf.reset_cache()

    if args.bake_deck or args.bake_from_deck_fields:
        names = [(c.get("name") or "") for c in cards]
        if commander.get("name"):
            names.append(commander["name"])
        try:
            if args.bake_deck:
                _bake_deck_names(names, jsonl=args.jsonl, store_path=store_path)
            if args.bake_from_deck_fields:
                n = _bake_from_deck_fields(cards, commander, store_path)
                if n == 0 and not args.bake_deck:
                    print(
                        "UNKNOWN: --bake-from-deck-fields found no oracle text "
                        "on deck cards",
                        file=sys.stderr,
                    )
                    return 2
        except FileNotFoundError as e:
            print(f"UNKNOWN: {e}", file=sys.stderr)
            return 2
        cf.reset_cache()

    report = compare(payload)
    if not args.quiet:
        _print_human(report)
    if args.json_out:
        args.json_out.expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.json_out.expanduser().write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )
        print(f"wrote {args.json_out}")
    elif args.quiet:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
