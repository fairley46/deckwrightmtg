"""
precon_delta.py — what changes if you add/cut cards on a precon.

No model. Compares mechanic density + sim diagnostics before/after. Each added
card gets help/hurt from whether it lands on families/signals the precon
already leans into (or related families via the taxonomy graph).
"""

from __future__ import annotations

from typing import Any

from deckwright import mechanics
from deckwright import sim


def _density_map(tagged: dict, key: str) -> dict[str, float]:
    out = {}
    for row in tagged.get(key) or []:
        name = row.get("name") or row.get("family")
        if name:
            out[str(name)] = float(row.get("density") or 0)
    return out


def _lean_families(tagged: dict, *, min_density: float = 0.05) -> set[str]:
    return {f["family"] for f in (tagged.get("families") or [])
            if float(f.get("density") or 0) >= min_density and f.get("family")}


def _related_set(families: set[str], tax: dict) -> set[str]:
    out = set(families)
    for f in list(families):
        out.update(mechanics.expand_goal_families([f], tax, depth=1))
    return out


def card_help_hurt(card: dict, lean: set[str], tax: dict) -> dict[str, Any]:
    hits = mechanics.card_plan_hits(card, tax)
    overlap = set(hits["families"]) & lean
    if overlap:
        return {
            "name": card.get("name"),
            "verdict": "helps",
            "overlap": sorted(overlap),
            "hits": hits,
            "reason": "Touches families the precon already leans on: "
                      + ", ".join(sorted(overlap)) + ".",
        }
    # Related one hop still helps.
    related = _related_set(set(hits["families"]), tax)
    soft = related & lean
    if soft:
        return {
            "name": card.get("name"),
            "verdict": "helps",
            "overlap": sorted(soft),
            "hits": hits,
            "reason": "Related to the precon's plan via "
                      + ", ".join(sorted(soft)) + ".",
        }
    if hits["families"] or hits["signals"]:
        return {
            "name": card.get("name"),
            "verdict": "hurts",
            "overlap": [],
            "hits": hits,
            "reason": "Pulls toward "
                      + ", ".join(hits["families"] or hits["signals"])
                      + " — off the precon's current lean.",
        }
    return {
        "name": card.get("name"),
        "verdict": "neutral",
        "overlap": [],
        "hits": hits,
        "reason": "No counted mechanic signal either way.",
    }


def strategy_delta(base_deck: list[dict],
                   *,
                   adds: list[dict] | None = None,
                   cuts: list[str] | None = None,
                   commander_cmc: int = 5) -> dict[str, Any]:
    """Compare mechanic + diagnose before/after a card list edit."""
    tax = mechanics.load_families()
    adds = list(adds or [])
    cuts = set(cuts or [])

    before_deck = list(base_deck)
    after_names = []
    after_deck = []
    for c in before_deck:
        if c.get("name") in cuts:
            continue
        after_deck.append(c)
        after_names.append(c.get("name"))
    for c in adds:
        if c.get("name") in after_names:
            continue
        after_deck.append(c)

    before_tag = mechanics.tag_deck(before_deck, tax)
    after_tag = mechanics.tag_deck(after_deck, tax)
    lean = _related_set(_lean_families(before_tag), tax)

    before_cards = sim.prep(before_deck, set())
    after_cards = sim.prep(after_deck, set())
    before_diag = sim.diagnose(before_cards)
    after_diag = sim.diagnose(after_cards)

    per_card = [card_help_hurt(c, lean, tax) for c in adds]

    fam_before = _density_map(before_tag, "families")
    fam_after = _density_map(after_tag, "families")
    fam_delta = []
    for name in sorted(set(fam_before) | set(fam_after)):
        b, a = fam_before.get(name, 0.0), fam_after.get(name, 0.0)
        if abs(a - b) >= 0.01:
            fam_delta.append({"family": name, "before": b, "after": a,
                              "delta": round(a - b, 4)})
    fam_delta.sort(key=lambda r: -abs(r["delta"]))

    return {
        "ok": True,
        "before": {
            "size": len(before_deck),
            "mechanics": before_tag,
            "diagnostics": before_diag,
        },
        "after": {
            "size": len(after_deck),
            "mechanics": after_tag,
            "diagnostics": after_diag,
        },
        "family_delta": fam_delta[:12],
        "adds": per_card,
        "cuts": sorted(cuts),
        "lean_families": sorted(lean),
    }
