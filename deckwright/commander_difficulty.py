"""
commander_difficulty.py — one plain sentence for how hard a commander is to pilot.

derive from countable oracle/keyword facts (flash, hold-up, resource
tracking). Not a 1–5 rating. Not a label chip. Deterministic, no model call.
"""

from __future__ import annotations

import re

_DEFAULT = (
    "You cast it and play a normal curve — no special hold-up or tracking."
)


def _norm_keywords(card: dict) -> list[str]:
    return [str(k).lower() for k in (card.get("keywords") or []) if k]


def _text(card: dict) -> str:
    return (card.get("text") or "").lower()


def difficulty_sentence(card: dict) -> str:
    """Return one plain sentence describing what this commander asks you to do."""
    if not card:
        return _DEFAULT

    text = _text(card)
    kws = _norm_keywords(card)

    # Ordered rules — first match wins. Tests lock exact strings.
    if "flash" in kws or re.search(
        r"as though it had flash|any time you could cast an instant", text, re.I
    ):
        return "You can hold it up and cast at instant speed when the board is right."

    if "proliferate" in text:
        return "You track counters across the board and choose when to proliferate."

    if "tap a noncreature permanent for mana" in text:
        return (
            "You track which permanents to tap and when to spend extra mana "
            "on creatures."
        )

    if re.search(r"attacks.*cast it without paying", text, re.S) or re.search(
        r"whenever .* attacks.*you may cast", text, re.S
    ):
        return (
            "You attack — big cheat-in creatures land when this commander connects."
        )

    if "ninja" in text and "combat damage to a player" in text:
        return (
            "You keep attacking — ninja combat damage is how the deck stacks value."
        )

    if re.search(r"\{4\}, \{t\}:\s*draw a card", text, re.I):
        return (
            "You hold mana open to sink into drawing and extra lands instead of "
            "tapping out on curve."
        )

    if "cost {1} more to cast" in text and "unless that player pays" in text:
        return "You hold mana for interaction while their spells cost more."

    if re.search(
        r"whenever you cast an instant or sorcery.*create a", text, re.S
    ):
        return (
            "You cast an instant or sorcery every turn to keep djinn production "
            "going."
        )

    if (
        "instant and sorcery spells you cast cost" in text
        and "deals damage to an opponent" in text
        and "draw a card" in text
    ):
        return (
            "You chain instants and sorceries — drawing cards depends on spell "
            "order, not combat."
        )

    if "graveyard" in text and "command zone this game" in text:
        return (
            "You track what's in your graveyard and how many times you've recast "
            "this commander."
        )

    if re.search(r"whenever .* attacks", text, re.I):
        return (
            "You attack to trigger it — combat is the main decision, not spell "
            "timing."
        )

    return _DEFAULT
