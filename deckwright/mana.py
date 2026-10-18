"""
mana.py — land count and colored-source analysis from the deck's curve and costs.

Land total follows Karsten's 99-card Commander regression (free mulligan + T1 draw).
Colored sources follow Karsten's 2022 conditional tables for 99-card decks at the
assumed land count, scaled when the deck runs fewer or more lands.

Sources: Frank Karsten, "How many colored sources do you need to consistently cast
your spells? A 2022 update" (Peasant Magic / ChannelFireball), and Karsten's land-
count regression in the same article series.
"""

import math
import re

from deckwright import sim

WUBRG = sim.WUBRG

# Karsten 2022 — 99-card Commander, 41 lands in deck, ~90–94% on-curve consistency.
# Keys: turn -> {colored_pips_in_cost -> min sources of that color}.
# Population is singleton 99, not 60-card Constructed — do not copy 60-card rows.
KARSTEN_SOURCES_99_41: dict[int, dict[int, int]] = {
    1: {1: 9},
    2: {1: 10, 2: 12},
    3: {1: 11, 2: 13, 3: 16},
    4: {1: 10, 2: 12, 3: 15},
    5: {1: 9, 2: 11, 3: 14},
    6: {1: 9, 2: 10, 3: 13},
    7: {1: 8, 2: 10, 3: 12},
}

KARSTEN_ASSUMED_LANDS = 41
MIN_TOTAL_LANDS = 30
MAX_TOTAL_LANDS = 44
ROCK_SOURCE_WEIGHT = 0.75  # Karsten 2022: mana rocks ≈ ¾ source for MV≥3 spells


def _is_land(card: dict) -> bool:
    return "land" in (card.get("type") or "").lower()


def _cheap_ramp_draw(deck: list) -> int:
    """Non-land MV≤2 dedicated ramp or cantrips — Karsten's −0.28 lands each."""
    prepped = sim.prep(deck, set())
    n = 0
    for c in prepped:
        if c["land"] or c["cmc"] > 2:
            continue
        if c["produces"] or c["fetch"]:
            n += 1
            continue
        tl = (c.get("type") or "").lower()
        if "instant" in tl or "sorcery" in tl:
            text = (c.get("text") or "").lower()
            if re.search(r"draw (?:a |one |two |three )card", text):
                n += 1
    return n


def _commander_cards(cmdr_card) -> list[dict]:
    """Accept one commander dict or a list (partner pairs)."""
    if not cmdr_card:
        return []
    if isinstance(cmdr_card, (list, tuple)):
        return [c for c in cmdr_card if c]
    return [cmdr_card]


def average_spell_mv(deck: list, cmdr_card=None) -> float:
    """Average mana value of non-land spells plus commander(s)."""
    spells = [c for c in deck if not _is_land(c)]
    for c in _commander_cards(cmdr_card):
        if not _is_land(c):
            spells.append(c)
    if not spells:
        return 3.0
    return sum(int(c.get("cmc") or 0) for c in spells) / len(spells)


def total_lands_target(deck: list, cmdr_card=None) -> tuple[int, str]:
    """Karsten 99-card: 31.42 + 3.13×avgMV − 0.28×cheap ramp/draw."""
    avg = average_spell_mv(deck, cmdr_card)
    cheap = _cheap_ramp_draw(deck)
    raw = 31.42 + 3.13 * avg - 0.28 * cheap
    total = max(MIN_TOTAL_LANDS, min(MAX_TOTAL_LANDS, round(raw)))
    basis = (f"{total} lands — Karsten 99-card regression on avg MV {avg:.2f} "
             f"(31.42 + 3.13×{avg:.2f} − 0.28×{cheap} cheap ramp/draw)")
    return total, basis


def sources_required(turn: int, colored_pips: int, total_lands: int) -> int:
    """Min sources of one color for that pip count on that turn (99-card, scaled)."""
    turn = max(1, min(7, turn))
    pips = max(1, min(4, colored_pips))
    row = KARSTEN_SOURCES_99_41.get(turn) or KARSTEN_SOURCES_99_41[7]
    base = row.get(pips) or row[max(row)]
    return max(1, math.ceil(base * total_lands / KARSTEN_ASSUMED_LANDS))


def _card_source_colors(card: dict, identity: list) -> set[str]:
    """Colors this card can produce, intersected with the commander's identity."""
    ci = set(identity) or set(WUBRG)
    sc = sim.source_colors(card)
    if "ANY" in sc:
        return set(ci)
    return {c for c in sc if c in ci and c in WUBRG}


def colored_sources(deck: list, identity: list) -> dict[str, float]:
    """Lands and rocks counted honestly — Command Tower only counts CI colors."""
    counts = {c: 0.0 for c in WUBRG}
    prepped = sim.prep(deck, set())
    for c in prepped:
        cols = _card_source_colors(c, identity)
        if not cols:
            continue
        weight = 1.0
        if c["land"]:
            weight = 1.0
        elif c["produces"]:
            weight = ROCK_SOURCE_WEIGHT
        elif c["fetch"]:
            weight = ROCK_SOURCE_WEIGHT
        else:
            continue
        for col in cols:
            counts[col] += weight
    return counts


def _parse_cost_pips(cost: str) -> dict[str, int]:
    pips = sim.parse_cost(cost or "")
    return {c: pips[c] for c in WUBRG}


def _casting_demands(deck: list, cmdr_card) -> list[dict]:
    """Per-color on-curve demands: turn, pips, card name (gold +1 per Karsten)."""
    demands = []
    cards = list(_commander_cards(cmdr_card))
    cards.extend(c for c in deck if not _is_land(c))
    for c in cards:
        turn = int(c.get("cmc") or 0)
        if turn <= 0:
            continue
        pips = _parse_cost_pips(c.get("cost") or "")
        colored = [col for col in WUBRG if pips[col] > 0]
        if not colored:
            continue
        gold = len(colored) > 1
        for col in colored:
            demands.append({
                "card": c["name"],
                "color": col,
                "pips": pips[col] + (1 if gold else 0),
                "turn": turn,
            })
    return demands


def analyze_sources(deck: list, cmdr_card, identity: list,
                    total_lands: int) -> dict:
    """Does this mana base meet Karsten's on-curve colored-source targets?"""
    have = colored_sources(deck, identity)
    shortages = []
    required_by_color: dict[str, int] = {c: 0 for c in WUBRG}

    for d in _casting_demands(deck, cmdr_card):
        need = sources_required(d["turn"], d["pips"], total_lands)
        required_by_color[d["color"]] = max(required_by_color[d["color"]], need)
        got = have[d["color"]]
        if got + 0.01 < need:
            short = round(need - got)
            color_name = {"W": "white", "U": "blue", "B": "black",
                          "R": "red", "G": "green"}[d["color"]]
            shortages.append({
                "color": d["color"],
                "card": d["card"],
                "turn": d["turn"],
                "pips": d["pips"],
                "have": round(got, 1),
                "need": need,
                "short": short,
                "text": (f"{short} {color_name} source{'s' if short != 1 else ''} "
                         f"short of casting {d['card']} on turn {d['turn']} reliably "
                         f"({round(got, 1)} have, {need} need for {d['pips']} "
                         f"{color_name} pip{'s' if d['pips'] != 1 else ''})"),
            })

    # dedupe by color — keep the worst shortfall per color for summary
    seen_color: set[str] = set()
    unique_shortages = []
    for s in sorted(shortages, key=lambda x: -x["short"]):
        if s["color"] in seen_color:
            continue
        seen_color.add(s["color"])
        unique_shortages.append(s)

    return {
        "have": {c: round(have[c], 1) for c in WUBRG if have[c] > 0},
        "need": {c: required_by_color[c] for c in WUBRG if required_by_color[c] > 0},
        "shortages": unique_shortages,
        "supported": len(shortages) == 0,
    }


def nonbasic_target(total_lands: int, min_basics: int = 2) -> int:
    return max(1, total_lands - min_basics)


def plan(spell_deck: list, cmdr_card, identity: list,
         min_basics: int = 2) -> dict:
    """Size the mana base before nonbasic lands are filled.

    spell_deck: cards placed so far (no lands/basic). cmdr_card: trimmed
    commander, or a list when the deck has partners.
    Source verdict runs on the finished deck in /api/build — not here.
    """
    total, land_basis = total_lands_target(spell_deck, cmdr_card)
    nonbasic = nonbasic_target(total, min_basics)
    avg = average_spell_mv(spell_deck, cmdr_card)
    return {
        "total_lands": total,
        "nonbasic_lands": nonbasic,
        "avg_mv": round(avg, 2),
        "basis": land_basis,
    }
