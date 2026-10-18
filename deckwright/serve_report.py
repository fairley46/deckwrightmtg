"""Candidate C (experimental) — ServeReport from CardFacts + CommanderStrategy.

Active only when ``DECKWRIGHT_CANDIDATE=C``. Bound math stays in
``strategy_audit.card_bound_ok`` (shared with B). Missing CardFacts lookup
→ unknown fields (``None``), never empty-as-zero.

ServeReport keys per card:
  feeds_input, consumes_output, bound_ok, concept_hits, weighted_serve,
  cardfacts_status
"""

from __future__ import annotations

from typing import Any

from deckwright import commander_strategy
from deckwright import strategy_audit

# Event → output kinds that can feed that strategy input (coarse; not a
# rules engine). Same map as scripts/compare_b_vs_cardfacts.py preview.
_INPUT_TO_OUTPUT_KINDS: dict[str, frozenset[str]] = {
    "gain_life": frozenset({"life"}),
    "draw": frozenset({"draw"}),
    "combat_damage": frozenset({"tokens"}),
    "dies": frozenset({"recur_to_battlefield", "recur_to_hand"}),
    "etb_creature": frozenset({"tokens"}),
}

_CONCEPT_KEYS = (
    "scaling",
    "resilience",
    "explosive",
    "soft_combo",
    "free_value",
    "hidden_power",
    "multi_engine",
    "value_to_turn",
)

# Concepts 4 and 6 stay computable:false, weight 0 (per the V2 design).
_UNSCOREABLE_CONCEPTS = frozenset({"soft_combo", "hidden_power"})


def cardfacts_path_enabled() -> bool:
    """True when green runs candidate C (CardFacts ServeReport + CF steer)."""
    try:
        from deckwright import instance
        return instance.candidate() == "C"
    except Exception:
        return False


def _cmc(card: dict) -> float | None:
    for key in ("cmc", "mana_value"):
        if key in card and card[key] is not None:
            try:
                return float(card[key])
            except (TypeError, ValueError):
                return None
    return None


def _type_line(card: dict) -> str:
    return (card.get("type_line") or card.get("type") or "").strip()


def _mv_subject_applies(card: dict, subject: str) -> bool:
    tl = _type_line(card).lower()
    if subject == "artifact_mv":
        return "artifact" in tl
    if subject == "creature_mv":
        return "creature" in tl
    if subject == "card_mv":
        return True
    return False


def _structural_floor_feeder(card: dict, strategy: dict) -> bool:
    """On-plan under a static geq MV floor (Bello-shaped). Not a rules engine."""
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


def feeds_from_facts(
    card: dict,
    strategy: dict,
    facts: dict[str, Any],
) -> bool:
    """True when CardFacts show the card feeding a strategy input.

    Also true for subject-matching cards that satisfy a static geq MV floor
    (structural floor satisfaction — what the bound says, not granted abilities).
    """
    cf_events = {
        (i.get("event") or "")
        for i in (facts.get("inputs") or [])
        if i.get("event")
    }
    cf_kinds = {
        (o.get("kind") or "")
        for o in (facts.get("outputs") or [])
        if o.get("kind")
    }
    for inp in strategy.get("inputs") or []:
        event = inp.get("event") or ""
        if not event:
            continue
        if event in cf_events:
            return True
        related = _INPUT_TO_OUTPUT_KINDS.get(event) or frozenset()
        if cf_kinds & related:
            return True
    if _structural_floor_feeder(card, strategy):
        return True
    return False


def consumes_from_facts(
    strategy: dict,
    facts: dict[str, Any],
) -> bool:
    """True when CardFacts show the card consuming a strategy output kind."""
    want = {
        (o.get("kind") or "")
        for o in (strategy.get("outputs") or [])
        if o.get("kind")
    }
    if not want:
        return False
    # A card "consumes" an output when its inputs/triggers need that resource,
    # or when its own outputs are the same kind (payoff overlap — coarse).
    cf_events = {
        (i.get("event") or "")
        for i in (facts.get("inputs") or [])
        if i.get("event")
    }
    event_to_kind = {
        "gain_life": "life",
        "draw": "draw",
        "dies": "recur_to_battlefield",
    }
    for event, kind in event_to_kind.items():
        if event in cf_events and kind in want:
            return True
    cf_kinds = {
        (o.get("kind") or "")
        for o in (facts.get("outputs") or [])
        if o.get("kind")
    }
    return bool(cf_kinds & want)


def concept_hits_from_facts(
    card: dict,
    strategy: dict,
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Per-concept hit map. Unscoreable concepts stay computable:false."""
    weights = strategy.get("weights") or commander_strategy._weights_from({
        "inputs": strategy.get("inputs") or [],
        "outputs": strategy.get("outputs") or [],
        "bounds": strategy.get("bounds") or [],
        "rate": strategy.get("rate") or {},
    })
    rate = (strategy.get("rate") or {}).get("supplier") or "self"
    cf_kinds = {
        (o.get("kind") or "")
        for o in (facts.get("outputs") or [])
        if o.get("kind")
    }
    feeds = feeds_from_facts(card, strategy, facts)
    free = bool(facts.get("free_value"))
    explosive_flag = bool(facts.get("explosive"))
    recur = bool(cf_kinds & {"recur_to_battlefield", "recur_to_hand"})
    tokens = "tokens" in cf_kinds
    mana = "mana" in cf_kinds

    hits: dict[str, Any] = {}
    for key in _CONCEPT_KEYS:
        if key in _UNSCOREABLE_CONCEPTS:
            hits[key] = {
                "hit": False,
                "computable": False,
                "weight": 0.0,
                "note": "we do not score this",
            }
            continue
        hit = False
        if key == "free_value":
            hit = free
        elif key == "scaling":
            hit = rate == "opponent" and feeds
        elif key == "resilience":
            hit = recur
        elif key == "explosive":
            hit = explosive_flag or (tokens and rate == "opponent")
        elif key == "multi_engine":
            hit = feeds and bool(cf_kinds)
        elif key == "value_to_turn":
            hit = free or mana or feeds
        hits[key] = {
            "hit": hit,
            "computable": True,
            "weight": float(weights.get(key) or 0.0),
        }
    return hits


def weighted_serve_from_hits(hits: dict[str, Any] | None) -> float | None:
    """Sum of weights for computable hits. None when hits unknown."""
    if hits is None:
        return None
    total = 0.0
    for row in hits.values():
        if not isinstance(row, dict):
            continue
        if row.get("computable") is False:
            continue
        if row.get("hit"):
            total += float(row.get("weight") or 0.0)
    return total


def for_card(
    card: dict,
    strategy: dict,
    deck_so_far: list[dict] | None = None,
) -> dict[str, Any]:
    """Build one ServeReport. bound_ok always via shared checker."""
    strategy = strategy or {}
    deck = list(deck_so_far or [])
    bound = strategy_audit.card_bound_ok(card, strategy, deck)

    name = (card.get("name") or "").strip()
    from deckwright import cardfacts as cf
    facts = cf.lookup(name) if name else None
    if facts is None:
        return {
            "feeds_input": None,
            "consumes_output": None,
            "bound_ok": bound,
            "concept_hits": None,
            "weighted_serve": None,
            "cardfacts_status": "unknown",
        }

    hits = concept_hits_from_facts(card, strategy, facts)
    return {
        "feeds_input": feeds_from_facts(card, strategy, facts),
        "consumes_output": consumes_from_facts(strategy, facts),
        "bound_ok": bound,
        "concept_hits": hits,
        "weighted_serve": weighted_serve_from_hits(hits),
        "cardfacts_status": str(facts.get("status") or "unread"),
    }
