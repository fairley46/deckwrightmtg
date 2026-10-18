"""
swap.py — replace one card in a finished 99 with a real pool alternate.

the player rejects a card and picks a replacement from the same role's
candidate pool. The replacement must already have been fetched from Scryfall
(role_pools / the substitution ladder) — never invented. Singleton holds. The
caller re-runs the simulation; this module only mutates the list.
"""

from __future__ import annotations

import copy

from deckwright import substitution


class SwapError(ValueError):
    """Raised when a swap cannot legally proceed. str(e) is the client message."""


def pool_for_role(role_pools: dict[str, list[dict]] | None, role: str) -> list[dict]:
    return list((role_pools or {}).get(role) or [])


def find_in_pool(name: str, pool: list[dict]) -> dict | None:
    """Exact-name lookup in a verified Scryfall pool."""
    if not name:
        return None
    for c in pool:
        if c.get("name") == name:
            return c
    return None


def find_in_deck(deck: list[dict], name: str) -> dict | None:
    for c in deck:
        if c.get("name") == name:
            return c
    return None


def alternatives(card: dict, role_pools: dict[str, list[dict]], deck: list[dict],
                 ci: str, *, count: int = substitution.MAX_ALTERNATES) -> list[dict]:
    """Full pool cards the player may swap this one for.

    Names are filtered the same way the substitution ladder filters shopping alternates, then
    the matching full records are returned from the pool (so the sim gets text,
    type, produced_mana — not just a shopping stub).
    """
    role = card.get("role") or "theme"
    if substitution.REPLACEABILITY.get(role, "flexible") == "core":
        return []
    pool = pool_for_role(role_pools, role)
    stubs = substitution.pick_alternates(
        card, pool, {c["name"] for c in deck}, ci, count=count)
    stubs = substitution.verify_alternates(stubs, pool)
    out = []
    for s in stubs:
        full = find_in_pool(s["name"], pool)
        if full:
            out.append(full)
    return out


def _slot_fields(old: dict, replacement: dict) -> dict:
    """Build the card that occupies the old slot — keep role, take Scryfall fields."""
    card = copy.deepcopy(replacement)
    card["role"] = old.get("role") or card.get("role") or "theme"
    # Drop rationale / scoring stamps that describe the card being removed.
    card.pop("why", None)
    card.pop("sim_pick", None)
    return card


def apply_swap(deck: list[dict], remove: str, add: str,
               role_pools: dict[str, list[dict]],
               *, ci: str = "") -> dict:
    """Replace `remove` with `add` from the same role's verified pool.

    Returns {"deck", "removed", "added", "role"}. Raises SwapError on refusal.
    """
    if not remove or not add:
        raise SwapError("remove and add are required")
    if remove == add:
        raise SwapError(f"{remove} is already in that slot")

    old = find_in_deck(deck, remove)
    if not old:
        raise SwapError(f"{remove} is not in the deck")

    role = old.get("role") or "theme"
    if substitution.REPLACEABILITY.get(role, "flexible") == "core":
        raise SwapError(
            f"{remove} is a core strategy piece — come back for it rather than swapping")

    names = {c["name"] for c in deck}
    if add in names:
        raise SwapError(f"{add} is already in the deck (singleton)")

    pool = pool_for_role(role_pools, role)
    if not pool:
        raise SwapError(f"no candidate pool for role '{role}' — rebuild the deck")

    replacement = find_in_pool(add, pool)
    if not replacement:
        # Verifier: inventing a name outside the Scryfall pool is refused.
        raise SwapError(f"{add} is not in the {role} pool")

    if ci:
        allowed = set(ci)
        card_ci = set(replacement.get("color_identity") or [])
        if not card_ci <= allowed:
            raise SwapError(f"{add} is outside this commander's colour identity")

    new_card = _slot_fields(old, replacement)
    out = []
    replaced = False
    for c in deck:
        if not replaced and c.get("name") == remove:
            out.append(new_card)
            replaced = True
        else:
            out.append(dict(c))
    if not replaced:
        raise SwapError(f"{remove} is not in the deck")

    # Singleton after the fact — belt and braces.
    seen = set()
    for c in out:
        n = c.get("name")
        if n in seen and c.get("role") != "basic":
            raise SwapError(f"swap would duplicate {n}")
        if c.get("role") != "basic":
            seen.add(n)

    return {
        "deck": out,
        "removed": remove,
        "added": new_card,
        "role": role,
    }


def apply_swap_card(deck: list[dict], remove: str, replacement: dict,
                    *, ci: str = "") -> dict:
    """Replace `remove` with a Scryfall-fetched card object (editor path).

    Same singleton / colour-identity / core-slot rules as apply_swap, but the
    add does not have to already sit in role_pools — the caller verified it
    against a live Scryfall response before staging.
    """
    if not remove or not isinstance(replacement, dict) or not replacement.get("name"):
        raise SwapError("remove and a real replacement card are required")
    add = replacement["name"]
    if remove == add:
        raise SwapError(f"{remove} is already in that slot")

    old = find_in_deck(deck, remove)
    if not old:
        raise SwapError(f"{remove} is not in the deck")

    role = old.get("role") or "theme"
    if substitution.REPLACEABILITY.get(role, "flexible") == "core":
        raise SwapError(
            f"{remove} is a core strategy piece — come back for it rather than swapping")

    names = {c["name"] for c in deck}
    if add in names:
        raise SwapError(f"{add} is already in the deck (singleton)")

    if ci:
        allowed = set(ci)
        card_ci = set(replacement.get("color_identity") or [])
        if not card_ci <= allowed:
            raise SwapError(f"{add} is outside this commander's colour identity")

    new_card = _slot_fields(old, replacement)
    out = []
    replaced = False
    for c in deck:
        if not replaced and c.get("name") == remove:
            out.append(new_card)
            replaced = True
        else:
            out.append(dict(c))
    if not replaced:
        raise SwapError(f"{remove} is not in the deck")

    seen = set()
    for c in out:
        n = c.get("name")
        if n in seen and c.get("role") != "basic":
            raise SwapError(f"swap would duplicate {n}")
        if c.get("role") != "basic":
            seen.add(n)

    return {
        "deck": out,
        "removed": remove,
        "added": new_card,
        "role": role,
    }
