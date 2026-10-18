"""CON-321 Gate 4 — soft steer from CommanderStrategy. Audit always; steer on B/C.

Deprioritise bound_ok == false, prefer cards feeding strategy.inputs.
bound_ok == unknown is neither a violation nor a steer pass (unknown ≠ zero).

CON-322 Candidate C: when ``DECKWRIGHT_CANDIDATE=C``, CardFacts may enrich
feeder detection and stamp ServeReport fields. Bound math stays shared via
``strategy_audit.card_bound_ok``. Missing CardFacts → fall back to B oracle
feeders (C includes B); ServeReport marks CF fields unknown, never zero.
"""

from __future__ import annotations

from typing import Any

from deckwright import strategy_audit


def steer_enabled() -> bool:
    """True when green runs candidate B or C with selection steer."""
    try:
        from deckwright import instance
        return instance.candidate() in ("B", "C")
    except Exception:
        return False


def cardfacts_steer_enabled() -> bool:
    """True when Candidate C may consult CardFacts for feeder / ServeReport."""
    try:
        from deckwright import serve_report
        return serve_report.cardfacts_path_enabled()
    except Exception:
        return False


def should_steer(strategy: dict | None) -> bool:
    if not steer_enabled() or not strategy:
        return False
    return strategy.get("status") != "unread"


def bound_ok(card: dict, strategy: dict, deck_so_far: list[dict]) -> str:
    """Per-card bound check against deck built so far.

    Returns 'ok', 'false', or 'unknown'. Delegates to strategy_audit so
    type guards and op (leq/geq) cannot drift (CON-329 / CON-331).
    """
    return strategy_audit.card_bound_ok(card, strategy or {}, list(deck_so_far or []))


def feeds_any_input(card: dict, strategy: dict) -> bool:
    """True when the card feeds any strategy input.

    Candidate C: CardFacts hit → CF feeder OR oracle feeder (union; C includes
    B). CardFacts miss → oracle only (unknown ≠ \"does not feed\").
    """
    strategy = strategy or {}
    oracle_hit = False
    for inp in strategy.get("inputs") or []:
        if strategy_audit._feeds_input(card, inp):
            oracle_hit = True
            break

    if not cardfacts_steer_enabled():
        return oracle_hit

    from deckwright import cardfacts as cf
    facts = cf.lookup((card.get("name") or "").strip())
    if facts is None:
        return oracle_hit
    from deckwright import serve_report
    return serve_report.feeds_from_facts(card, strategy, facts) or oracle_hit


def steer_vote(
    card: dict,
    strategy: dict,
    deck_so_far: list[dict],
) -> int | None:
    """+1 feeder, -1 known violation, 0 neutral, None when bound unknown."""
    if not should_steer(strategy):
        return None
    b = bound_ok(card, strategy, deck_so_far)
    if b == "false":
        return -1
    if b == "unknown":
        return None
    if feeds_any_input(card, strategy):
        return 1
    return 0


def cmp_votes(va: int | None, vb: int | None) -> int:
    """Compare strategy steer votes for ranking (CON-334 caveat 2).

    None (bound unknown) sorts as neutral 0 — not as CON-222 edge 'no vote'.
    Hard violators (-1) demote; among non-violators, feeder (+1) beats
    neutral and unknown. Bound demotion must not outrank feeder prefer
    except when bound_ok is actually false.
    """
    a = 0 if va is None else int(va)
    b = 0 if vb is None else int(vb)
    if a == b:
        return 0
    return 1 if a > b else -1


def prefer_strategy(
    cards: list[dict],
    strategy: dict | None,
    deck_so_far: list[dict] | None = None,
) -> list[dict]:
    """Stable reorder: feeders first, known violations last. Never drops."""
    if not should_steer(strategy):
        return list(cards)
    scaf = list(deck_so_far or [])

    def sort_key(ic: tuple[int, dict]) -> tuple:
        i, c = ic
        vote = steer_vote(c, strategy, scaf)
        # Map None → 0 so unknown ties neutrals and loses to feeders.
        bucket = 0 if vote is None else int(vote)
        return (bucket, -i)

    ranked = sorted(enumerate(cards), key=sort_key, reverse=True)
    return [c for _, c in ranked]


def stamp_votes(out: dict) -> dict:
    """Stamp per-card steer observability on a finalized build. Never reorders.

    Candidate B/C + readable commander only. off / unread → leave cards untouched
    (omit steer_vote / bound_ok / feeds_input). Uses commander_strategy already
    on out when present (post attach_clarity); otherwise derives once.

    Candidate C also stamps ``serve_report`` (CardFacts ServeReport). Missing
    facts → unknown fields inside that dict; never invent empty hits as zero.
    """
    if not steer_enabled():
        return out
    strategy = out.get("commander_strategy")
    if strategy is None:
        from deckwright import commander_strategy
        strategy = commander_strategy.derive(out.get("commander_card") or {})
    if not should_steer(strategy):
        return out
    deck = out.get("deck") or []
    # Finalized deck as capacity context — same basis as strategy_audit.
    use_cf = cardfacts_steer_enabled()
    if use_cf:
        from deckwright import serve_report
    for card in deck:
        card["steer_vote"] = steer_vote(card, strategy, deck)
        card["bound_ok"] = bound_ok(card, strategy, deck)
        card["feeds_input"] = feeds_any_input(card, strategy)
        if use_cf:
            card["serve_report"] = serve_report.for_card(card, strategy, deck)
    return out
