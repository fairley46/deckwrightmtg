"""
substitution.py — per-card shopping guidance for the build response.

CON-100: every card in the 99 carries its deck role, how replaceable that slot
is, and two or three validated alternates from the same role_query() ladder.
Alternates are never recalled — they are other cards the ladder already fetched.
"""

from __future__ import annotations

# Mirrors ROLES descriptions in app.py — the builder's slot labels.
ROLE_PURPOSE = {
    "lands": "Nonbasic lands and utility lands",
    "ramp": "Mana rocks and land ramp",
    "draw": "Card advantage",
    "spot": "Targeted removal",
    "wipe": "Board wipes",
    "plan": "Cards the chosen strategy specifically calls for",
    "theme": "On-theme synergy pieces",
    "deepcut": "Flavor cards almost nobody plays",
    "basic": "Basic lands",
}

# core: strategy pieces — skip it or come back. swappable: any card in the role.
# flexible: on-theme slots — alternates exist but the pick matters more.
REPLACEABILITY = {
    "plan": "core",
    "lands": "swappable",
    "ramp": "swappable",
    "draw": "swappable",
    "spot": "swappable",
    "wipe": "swappable",
    "basic": "swappable",
    "theme": "flexible",
    "deepcut": "flexible",
}

MAX_ALTERNATES = 3
COST_TOLERANCE = 1

# An alternate has to be affordable, not just castable. The first version
# matched on mana value alone, so Sol Ring ($1.43) was offered Mana Vault
# ($118) and Chrome Mox ($165) — all cheap to cast, all ruinous to buy. This
# feature exists for someone standing at a counter with a budget; telling her
# to replace a dollar card with a hundred-dollar one is worse than saying
# nothing. Allow a real step up in price, not a different league.
PRICE_MULTIPLE = 2.5
PRICE_FLOOR = 3.0


def _in_color_identity(card: dict, ci: str) -> bool:
    """Card's color identity must fit the commander's."""
    if not ci or ci == "C":
        return True
    allowed = set(ci)
    card_ci = set(card.get("color_identity") or [])
    return card_ci <= allowed


def _similar_cost(a: float | int, b: float | int, tolerance: int = COST_TOLERANCE) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def _price(card: dict) -> float | None:
    """Card price as a number, or None when Scryfall has no price for it."""
    try:
        return float(card["usd"])
    except (KeyError, TypeError, ValueError):
        return None


def _affordable(alt: dict, original: dict) -> bool:
    """Is this a swap someone on a budget would actually make?

    An unpriced alternate is allowed through — a missing price is not evidence
    of an expensive card — but it sorts last, so priced options come first.
    """
    a, o = _price(alt), _price(original)
    if a is None or o is None:
        return True
    return a <= max(o * PRICE_MULTIPLE, o + PRICE_FLOOR)


def alternate_entry(card: dict) -> dict:
    """Minimal alternate for a shopping list — name must match a Scryfall card.

    Carries the image because the deck page lets you hover any card name to see
    the card, and a substitute is a card name like any other. Without it the
    frontend has nothing to show: an alternate is by definition NOT in the
    deck, so the usual lookup through `deckData.deck` finds nothing and the
    name silently renders as plain text. The page must not fetch art itself —
    the User-Agent and rate-limit rules live in `scry()` — so it has to arrive
    here or not at all.
    """
    return {
        "name": card["name"],
        "cost": card.get("cost") or "",
        "cmc": card.get("cmc", 0),
        "usd": card.get("usd"),
        "image": card.get("image") or card.get("art") or "",
    }


def pick_alternates(card: dict, pool: list[dict], deck_names: set[str], ci: str,
                    *, count: int = MAX_ALTERNATES,
                    tolerance: int = COST_TOLERANCE) -> list[dict]:
    """Other ladder cards in the same role, similar cost, not already in the deck."""
    if not pool:
        return []
    pool_names = {c["name"] for c in pool}
    cmc = card.get("cmc", 0)
    candidates = []
    for c in pool:
        name = c["name"]
        if name == card["name"] or name in deck_names:
            continue
        if name not in pool_names:
            continue
        if not _in_color_identity(c, ci):
            continue
        if not _similar_cost(c.get("cmc", 0), cmc, tolerance):
            continue
        if not _affordable(c, card):
            continue
        candidates.append(c)
    # Cards shops are likelier to stock, first; unpriced ones last, since we
    # cannot promise what they cost.
    candidates.sort(key=lambda c: (_price(c) is None,
                                   c.get("edhrec_rank") or 999_999))
    return [alternate_entry(c) for c in candidates[:count]]


def verify_alternates(alternates: list[dict], pool: list[dict]) -> list[dict]:
    """Drop any alternate not present in the validated role pool."""
    allowed = {c["name"] for c in pool}
    return [a for a in alternates if a.get("name") in allowed]


def card_substitution(card: dict, role_pools: dict[str, list[dict]], deck_names: set[str],
                      ci: str) -> dict:
    """Substitution block for one deck card."""
    role = card.get("role") or "theme"
    replaceability = REPLACEABILITY.get(role, "flexible")
    purpose = ROLE_PURPOSE.get(role, ROLE_PURPOSE["theme"])
    pool = list(role_pools.get(role) or [])
    alternates: list[dict] = []
    if replaceability != "core":
        alternates = pick_alternates(card, pool, deck_names, ci)
        alternates = verify_alternates(alternates, pool)
    return {
        "role": role,
        "purpose": purpose,
        "replaceability": replaceability,
        "alternates": alternates,
    }


def build_substitutions(deck: list[dict], role_pools: dict[str, list[dict]],
                        ci: str) -> dict[str, dict]:
    """Map card name -> substitution guidance for the full 99."""
    deck_names = {c["name"] for c in deck}
    return {
        c["name"]: card_substitution(c, role_pools, deck_names, ci)
        for c in deck
    }
