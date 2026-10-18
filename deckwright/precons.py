"""
precons.py — Commander precon catalog from Archidekt's public JSON API.

CON-98: curated entry into the CON-97 resolve pipeline. Archidekt's
Archidekt_Precons account publishes official precon lists; we cache the catalog
and fetch one deck on demand.

Terms (checked 2026-08-04): Archidekt's ToS prohibits automated queries that
burden their servers (archidekt.com/terms, section 3). Their founder has stated
the read JSON API is intentionally open for third parties (archidekt.com/forum/
thread/40353), EDHREC already uses it, and they ask for attribution when
publishing deck data. We identify as Deckwright, stay under ~8 req/s, and cache
the catalog for 30 days — precons only change when a set releases.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from typing import Any

import httpx

from deckwright import instance

ARCHIDEKT = "https://archidekt.com/api"
PRECON_OWNER = "Archidekt_Precons"
DECK_FORMAT_COMMANDER = 3
PAGE_SIZE = 100

SAVE_DIR = instance.home_dir()
CACHE_PATH = SAVE_DIR / "precons.json"
CACHE_MAX_AGE = 30 * 86400  # sets ship quarterly; 30d is plenty

UA = {
    "User-Agent": "Deckwright/1.0 (local personal deckbuilder)",
    "Accept": "application/json",
}

_gate = asyncio.Semaphore(4)
_last = [0.0]

PASTE_FALLBACK = (
    "Precon picker is unavailable right now. Paste your deck list instead — "
    "the import screen accepts Archidekt, Moxfield, or plain text."
)


class PreconsUnavailable(Exception):
    """Catalog cannot be loaded and there is no usable cache."""


async def archidekt_get(url: str) -> dict:
    """GET Archidekt JSON with the same pacing as scry()."""
    async with _gate:
        gap = time.time() - _last[0]
        if gap < 0.12:
            await asyncio.sleep(0.12 - gap)
        _last[0] = time.time()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, headers=UA)
    r.raise_for_status()
    return r.json()


def colors_from_counts(counts: dict) -> str:
    return "".join(letter for letter in "WUBRG" if counts.get(letter))


def summarize_list_item(deck: dict) -> dict:
    return {
        "id": str(deck["id"]),
        "name": deck["name"],
        "colors": colors_from_counts(deck.get("colors") or {}),
        "image": deck.get("featured") or "",
        "updated_at": deck.get("updatedAt"),
        "source": f"https://archidekt.com/decks/{deck['id']}",
    }


def _normalize_next(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


async def fetch_catalog_remote() -> list[dict]:
    """Paginate Archidekt_Precons Commander decks."""
    items: list[dict] = []
    url = (
        f"{ARCHIDEKT}/decks/v3/?ownerUsername={PRECON_OWNER}"
        f"&deckFormat={DECK_FORMAT_COMMANDER}&pageSize={PAGE_SIZE}"
    )
    while url:
        data = await archidekt_get(url)
        for deck in data.get("results") or []:
            if deck.get("size") == 100 and not deck.get("private"):
                items.append(summarize_list_item(deck))
        url = _normalize_next(data.get("next"))
    items.sort(key=lambda x: x["name"].lower())
    return items


def read_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(catalog: list[dict]) -> None:
    SAVE_DIR.mkdir(exist_ok=True)
    payload = {"fetched_at": time.time(), "precons": catalog}
    CACHE_PATH.write_text(json.dumps(payload, indent=1))


async def get_catalog(force: bool = False) -> list[dict]:
    """Return the cached precon list, refreshing from Archidekt when stale."""
    cached = read_cache()
    if not force and cached:
        age = time.time() - cached.get("fetched_at", 0)
        if age < CACHE_MAX_AGE and cached.get("precons"):
            return cached["precons"]

    try:
        catalog = await fetch_catalog_remote()
        if catalog:
            write_cache(catalog)
            return catalog
    except Exception:
        pass

    if cached and cached.get("precons"):
        return cached["precons"]
    raise PreconsUnavailable(PASTE_FALLBACK)


def card_name(oracle_card: dict) -> str:
    """Oracle card name, including // for double-faced cards."""
    name = oracle_card.get("name") or ""
    faces = oracle_card.get("faces") or []
    if faces and "//" not in name:
        parts = [f.get("name") for f in faces if f.get("name")]
        if parts:
            return " // ".join(parts)
    return name


def archidekt_to_decklist(deck: dict) -> tuple[str, str | None]:
    """Turn an Archidekt deck payload into pasted-list text."""
    commander_lines: list[str] = []
    body_lines: list[str] = []
    commander_name: str | None = None

    for entry in deck.get("cards") or []:
        oc = (entry.get("card") or {}).get("oracleCard") or {}
        name = card_name(oc)
        if not name:
            continue
        qty = int(entry.get("quantity") or 1)
        cats = entry.get("categories") or []
        line = f"{qty} {name}"
        if "Commander" in cats:
            commander_name = name
            commander_lines.append(line)
        else:
            body_lines.append(line)

    parts: list[str] = []
    if commander_lines:
        parts.append("// Commander")
        parts.extend(commander_lines)
    parts.extend(body_lines)
    return "\n".join(parts), commander_name


async def fetch_deck(deck_id: str) -> dict:
    return await archidekt_get(f"{ARCHIDEKT}/decks/{deck_id}/")


async def decklist_for_precon(deck_id: str) -> tuple[str, str | None, dict[str, Any]]:
    """Fetch one precon and return (text, explicit_commander, metadata)."""
    deck = await fetch_deck(deck_id)
    text, commander = archidekt_to_decklist(deck)
    meta = {
        "id": str(deck.get("id") or deck_id),
        "name": deck.get("name") or "",
        "source": f"https://archidekt.com/decks/{deck_id}",
    }
    return text, commander, meta
