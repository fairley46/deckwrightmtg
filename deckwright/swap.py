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


# Shown when a refusal is found only after the list is rebuilt (a duplicate).
REFUSED = "That swap would leave the deck invalid."


class SwapError(ValueError):
    """Raised when a swap cannot legally proceed.

    The message is the same sentence swap_problem / swap_card_problem return;
    API code shows those return values rather than reading the exception.
    """


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


def _common_problem(deck: list[dict], remove: str,
                    add: str) -> tuple[str | None, dict | None]:
    """Checks shared by both swap paths. Returns (problem, card being removed)."""
    if remove == add:
        return f"{remove} is already in that slot", None
    old = find_in_deck(deck, remove)
    if not old:
        return f"{remove} is not in the deck", None
    role = old.get("role") or "theme"
    if substitution.REPLACEABILITY.get(role, "flexible") == "core":
        return (f"{remove} is a core strategy piece — come back for it "
                "rather than swapping"), old
    if add in {c["name"] for c in deck}:
        return f"{add} is already in the deck (singleton)", old
    return None, old


def _outside_identity(add: str, card_ci: list | None, ci: str) -> str | None:
    if ci and not set(card_ci or []) <= set(ci):
        return f"{add} is outside this commander's colour identity"
    return None


def swap_problem(deck: list[dict], remove: str, add: str,
                 role_pools: dict[str, list[dict]] | None,
                 *, ci: str = "") -> str | None:
    """Why apply_swap would refuse, as the sentence shown to the player.

    Returns None when the swap can go ahead. Callers show this text directly,
    so it never has to be read back out of an exception.
    """
    if not remove or not add:
        return "remove and add are required"
    problem, old = _common_problem(deck, remove, add)
    if problem:
        return problem
    role = old.get("role") or "theme"
    pool = pool_for_role(role_pools, role)
    if not pool:
        return f"no candidate pool for role '{role}' — rebuild the deck"
    replacement = find_in_pool(add, pool)
    if not replacement:
        # Verifier: inventing a name outside the Scryfall pool is refused.
        return f"{add} is not in the {role} pool"
    return _outside_identity(add, replacement.get("color_identity"), ci)


def swap_card_problem(deck: list[dict], remove: str, replacement: dict,
                      *, ci: str = "") -> str | None:
    """Why apply_swap_card would refuse, or None when the swap can go ahead."""
    if not remove or not isinstance(replacement, dict) or not replacement.get("name"):
        return "remove and a real replacement card are required"
    add = replacement["name"]
    problem, _old = _common_problem(deck, remove, add)
    if problem:
        return problem
    return _outside_identity(add, replacement.get("color_identity"), ci)


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
    problem = swap_problem(deck, remove, add, role_pools, ci=ci)
    if problem:
        raise SwapError(problem)
    old = find_in_deck(deck, remove)
    role = old.get("role") or "theme"
    replacement = find_in_pool(add, pool_for_role(role_pools, role))

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
    problem = swap_card_problem(deck, remove, replacement, ci=ci)
    if problem:
        raise SwapError(problem)
    old = find_in_deck(deck, remove)
    role = old.get("role") or "theme"

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
