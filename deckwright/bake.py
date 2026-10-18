"""
bake.py — stamps that mark a cached analysis valid or stale.

Shared by saved-deck simulation cache (CON-108) and precon bake (CON-102).
A hash mismatch means the card list changed; a version bump means our own
engine changed. Either one means recompute — never serve numbers from an
engine we no longer run.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def deck_list_hash(deck: list[dict] | None) -> str:
    """Stable fingerprint of the card list. Counts matter (basics repeat)."""
    counts: dict[str, int] = {}
    for c in deck or []:
        name = c.get("name") or ""
        counts[name] = counts.get(name, 0) + 1
    payload = json.dumps(sorted(counts.items()), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def stamp(payload: dict, deck: list[dict], version: int,
          *, version_key: str = "sim_version") -> dict:
    """Attach hash + version stamps to a cache payload. Returns a new dict."""
    out = dict(payload)
    out["deck_hash"] = deck_list_hash(deck)
    out[version_key] = int(version)
    return out


def fresh(cached: dict | None, deck: list[dict], version: int,
          *, version_key: str = "sim_version") -> bool:
    """True only when both stamps match the current list and engine."""
    if not isinstance(cached, dict):
        return False
    if cached.get(version_key) != int(version):
        return False
    if cached.get("deck_hash") != deck_list_hash(deck):
        return False
    return True


def strip_stale(data: dict, key: str, version: int,
                *, version_key: str = "sim_version",
                deck_key: str = "deck") -> dict[str, Any]:
    """Return a shallow copy with a stale cache key removed."""
    out = dict(data)
    if not fresh(out.get(key), out.get(deck_key) or [], version,
                 version_key=version_key):
        out.pop(key, None)
    return out
