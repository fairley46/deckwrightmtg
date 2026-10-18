"""tier-1 otag membership lookups for the 99.

Reads an optional baked membership map (path). Missing file or
missing card → unknown, never zero tags.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STORE: dict[str, list[str]] | None = None
_LOADED = False
_MISS = False


def membership_path() -> Path:
    try:
        from deckwright import instance
        return instance.home_dir() / "otag_membership.json"
    except Exception:
        return Path.home() / ".deckwright" / "otag_membership.json"


def _load() -> dict[str, list[str]] | None:
    global _STORE, _LOADED, _MISS
    if _LOADED:
        return _STORE
    _LOADED = True
    path = membership_path()
    if not path.is_file():
        _MISS = True
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        _MISS = True
        return None
    cards = raw.get("cards") if isinstance(raw, dict) else raw
    if not isinstance(cards, dict):
        _MISS = True
        return None
    out: dict[str, list[str]] = {}
    for name, tags in cards.items():
        if not isinstance(name, str):
            continue
        if isinstance(tags, list):
            out[name] = [str(t) for t in tags]
        elif isinstance(tags, str):
            out[name] = [tags]
    _STORE = out
    return _STORE


def reset_cache() -> None:
    """Test hook — clear the in-process membership cache."""
    global _STORE, _LOADED, _MISS
    _STORE = None
    _LOADED = False
    _MISS = False


def store_status() -> str:
    """ok when a membership file loaded; unknown when absent or unreadable."""
    _load()
    return "unknown" if _MISS or _STORE is None else "ok"


def tags_for(card_name: str) -> list[str] | None:
    """Tags for one card name. None = unknown (not an empty list)."""
    store = _load()
    if store is None:
        return None
    if card_name in store:
        return list(store[card_name])
    low = card_name.lower()
    for key, tags in store.items():
        if key.lower() == low:
            return list(tags)
    return None


def has_tag(card: dict[str, Any], tag: str) -> bool | None:
    """True/False when membership is known; None when unknown."""
    name = (card.get("name") or "").strip()
    if not name:
        return None
    tags = tags_for(name)
    if tags is None:
        return None
    want = (tag or "").lower()
    return any(t.lower() == want for t in tags)


def rollup_counts(deck: list[dict], tags: list[str]) -> dict[str, Any]:
    """Count how many cards in deck carry each tag. Unknown cards tracked."""
    deck = list(deck or [])
    tags = [t.lower() for t in (tags or []) if t]
    counts: dict[str, int] = {t: 0 for t in tags}
    unknown = 0
    for card in deck:
        name = card.get("name") or ""
        card_tags = tags_for(name) if name else None
        if card_tags is None:
            unknown += 1
            continue
        lowered = {t.lower() for t in card_tags}
        for t in tags:
            if t in lowered:
                counts[t] += 1
    return {
        "status": store_status(),
        "counts": counts,
        "unknown_cards": unknown,
        "deck_size": len(deck),
    }
