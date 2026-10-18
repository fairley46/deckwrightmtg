"""
tweak.py — composition sliders for /api/simulate (CON-75).

Swap real cards from the build's role_pools by card type. Never invent a
stand-in, never duplicate a singleton name. If the pool cannot cover a step,
stop and say so — the curves then describe a deck that could actually be built.

Axes match the deck rail: lands, creatures, instants & sorceries, artifacts,
enchantments. The post-tweak composition dict is the rebuild target for
CON-77 / CON-95.
"""

from __future__ import annotations

from deckwright import import_deck

# Card-type axes shown in the rail — not deck roles.
COMPOSITION_AXES = (
    "lands",
    "creatures",
    "instants_sorceries",
    "artifacts",
    "enchantments",
)

# Prefer these as donors when adding to an axis (flexible filler slots).
DONOR_ROLES = ("theme", "deepcut", "wipe")


def card_axis(card: dict) -> str | None:
    """Which composition axis a card counts toward. Mutually exclusive.

    Land wins over everything (artifact lands are lands). Creature wins over
    artifact/enchantment (the rail counts artifact creatures as creatures;
    non-creature artifacts are the artifacts row).
    """
    t = (card.get("type") or "").lower()
    if "land" in t:
        return "lands"
    if "creature" in t:
        return "creatures"
    if "instant" in t or "sorcery" in t:
        return "instants_sorceries"
    if "artifact" in t:
        return "artifacts"
    if "enchantment" in t:
        return "enchantments"
    return None


def composition_of(deck: list) -> dict[str, int]:
    """Count the 99 by composition axis. Rebuild target for CON-77 / CON-95."""
    counts = {a: 0 for a in COMPOSITION_AXES}
    for c in deck:
        ax = card_axis(c)
        if ax:
            counts[ax] += 1
    return counts


def _copy_card(card: dict, role: str | None = None) -> dict:
    """Deck entry from a pool card. Drops internal bookkeeping keys."""
    out = {k: v for k, v in card.items() if not str(k).startswith("_")}
    if role is not None:
        out["role"] = role
    elif "role" not in out:
        out["role"] = import_deck.infer_role(out)
    return out


def _role_of_pool_card(card: dict, pool_role: str | None) -> str:
    """Role stamp for a card entering the deck from a named role pool."""
    if pool_role == "lands" or card_axis(card) == "lands":
        tl = (card.get("type") or "").lower()
        return "basic" if "basic" in tl else "lands"
    if pool_role and pool_role != "basic":
        return pool_role
    return import_deck.infer_role(card)


def _available(role_pools: dict | None, deck_names: set[str]) -> list[tuple[str, dict]]:
    """Unused pool cards as (pool_role, card), first-seen order, singleton."""
    seen: set[str] = set(deck_names)
    out: list[tuple[str, dict]] = []
    for role, pool in (role_pools or {}).items():
        for c in pool or []:
            name = c.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append((role, c))
    return out


def _donor_rank(card: dict) -> tuple[int, int]:
    """Lower is preferred when giving up a slot to grow another axis."""
    role = card.get("role") or ""
    # Prefer flexible filler, then anything non-land, then nonbasics, then basics.
    if role in DONOR_ROLES:
        flex = 0
    elif role in ("plan", "spot", "draw", "ramp"):
        flex = 1
    elif role == "lands":
        flex = 2
    elif role == "basic":
        flex = 3
    else:
        flex = 1
    # Within a band, prefer higher CMC as less foundational — weak heuristic.
    return (flex, -int(card.get("cmc") or 0))


def _victim_rank(card: dict) -> tuple[int, int]:
    """Lower is preferred when shrinking an axis."""
    role = card.get("role") or ""
    if role in DONOR_ROLES:
        flex = 0
    elif role == "basic":
        flex = 2  # keep basics if other lands exist
    else:
        flex = 1
    return (flex, -int(card.get("cmc") or 0))


def _pick_donor(deck: list, axis: str) -> dict | None:
    """A card in the deck that is not on `axis`, preferred from filler roles."""
    candidates = [c for c in deck if card_axis(c) != axis]
    if not candidates:
        return None
    candidates.sort(key=_donor_rank)
    return candidates[0]


def _pick_victim(deck: list, axis: str) -> dict | None:
    """A card in the deck that is on `axis`."""
    candidates = [c for c in deck if card_axis(c) == axis]
    if not candidates:
        return None
    candidates.sort(key=_victim_rank)
    return candidates[0]


def _take_candidate(
    available: list[tuple[str, dict]], axis: str, want_match: bool
) -> tuple[str, dict] | None:
    """Pop the first available card that matches (or doesn't match) `axis`."""
    for i, (role, card) in enumerate(available):
        matches = card_axis(card) == axis
        if matches if want_match else not matches:
            return available.pop(i)
    return None


# The five basics plus Wastes. A deck may run any number of these; every other
# card is a singleton. Snow-covered basics carry different names and are not
# included — they are not what the builder pads with.
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def apply_tweak(
    deck: list,
    tweak: dict | None,
    role_pools: dict | None = None,
) -> dict:
    """Apply composition deltas by swapping real pool cards.

    Returns:
        deck:        copy of the 99 after swaps (same length)
        applied:     axis → actual delta applied (may be short of requested)
        blocked:     [{axis, wanted, applied, reason}] for incomplete moves
        composition: post-tweak axis counts — rebuild target seam
        ok:          True iff every requested delta fully applied
    """
    d = [dict(c) for c in deck]
    requested = {k: int(v) for k, v in (tweak or {}).items() if int(v)}
    applied: dict[str, int] = {}
    blocked: list[dict] = []

    if not requested:
        return {
            "deck": d,
            "applied": {},
            "blocked": [],
            "composition": composition_of(d),
            "ok": True,
        }

    deck_names = {c["name"] for c in d if c.get("name")}
    available = _available(role_pools, deck_names)

    # Basics are an unlimited legal source. Every other card is a singleton, so
    # the pool is the only place a swap-in can come from — but a deck can always
    # run one more Mountain. Without this the lands slider is dead on any deck
    # whose pools were not carried along (every deck saved before pools were
    # persisted), and "add a land" is the move a player reaches for first.
    # Copies are taken from basics already in the deck, so they are real cards
    # in the right colours rather than anything invented.
    wants_lands = int((tweak or {}).get("lands") or 0)
    if wants_lands > 0:
        seen_basic: dict[str, dict] = {}
        for c in d:
            if c.get("name") in BASICS and c["name"] not in seen_basic:
                seen_basic[c["name"]] = c
        if seen_basic:
            ring = list(seen_basic.values())
            for i in range(wants_lands):
                available.append(("lands", dict(ring[i % len(ring)])))

    for axis, delta in requested.items():
        if axis not in COMPOSITION_AXES:
            blocked.append({
                "axis": axis,
                "wanted": delta,
                "applied": 0,
                "reason": f"unknown axis '{axis}' — use {', '.join(COMPOSITION_AXES)}",
            })
            applied[axis] = 0
            continue

        steps = abs(delta)
        did = 0
        growing = delta > 0

        for _ in range(steps):
            if growing:
                if not available:
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": did if delta > 0 else -did,
                        "reason": "candidate pool exhausted — slider cannot go further",
                    })
                    break
                taken = _take_candidate(available, axis, want_match=True)
                if not taken:
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": did if delta > 0 else -did,
                        "reason": f"no unused cards on the {axis} axis left in the pool — slider cannot go further",
                    })
                    break
                pool_role, src = taken
                donor = _pick_donor(d, axis)
                if not donor:
                    # Put the candidate back; nothing to displace.
                    available.insert(0, (pool_role, src))
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": did if delta > 0 else -did,
                        "reason": f"no non-{axis} card left to swap out — slider cannot go further",
                    })
                    break
                # Return the donor to the available pool for later opposite moves.
                available.append((donor.get("role") or "theme", dict(donor)))
                idx = d.index(donor)
                d[idx] = _copy_card(src, _role_of_pool_card(src, pool_role))
                did += 1
            else:
                victim = _pick_victim(d, axis)
                if not victim:
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": -did,
                        "reason": f"no {axis} left in the deck — slider cannot go further",
                    })
                    break
                if not available:
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": -did,
                        "reason": "candidate pool exhausted — slider cannot go further",
                    })
                    break
                taken = _take_candidate(available, axis, want_match=False)
                if not taken:
                    blocked.append({
                        "axis": axis,
                        "wanted": delta,
                        "applied": -did,
                        "reason": f"no non-{axis} cards left in the pool to replace with — slider cannot go further",
                    })
                    break
                pool_role, src = taken
                available.append((victim.get("role") or "theme", dict(victim)))
                idx = d.index(victim)
                d[idx] = _copy_card(src, _role_of_pool_card(src, pool_role))
                did += 1

        applied[axis] = did if growing else -did
        # If we broke early we already recorded a blocked entry; if we completed
        # all steps, applied matches wanted.

    # Deduplicate blocked entries per axis (one reason for the stop).
    # (We append at most once per axis on break, so fine as-is.)

    # Singleton is the invariant for every card EXCEPT basic lands, which a
    # deck may hold in any number — that is the format's own rule, not a
    # loophole. Checking raw name counts flagged all fifteen saved decks as
    # corrupt (one had 31 Mountains) and raised on the first slider drag, which
    # took the whole simulation panel down with it.
    names = [c["name"] for c in d if c.get("name") and c["name"] not in BASICS]
    if len(names) != len(set(names)):
        # Should be unreachable — singleton is the invariant. Surface loudly.
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise RuntimeError(
            "apply_tweak produced duplicate card names: " + ", ".join(dupes))

    return {
        "deck": d,
        "applied": applied,
        "blocked": blocked,
        "composition": composition_of(d),
        "ok": not blocked and all(
            applied.get(a, 0) == requested[a] for a in requested
        ),
    }
