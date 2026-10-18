"""
release_calendar.py — release calendar data: Scryfall /sets + hand overlay (CON-137)
and **debut mechanics** per released set (CON-218).

No model. Paced Scryfall via the caller's scry() — same gate and User-Agent as
app.py. Failed requests are unknowns, never zeros. Future sets are never counted
and never invent mechanics.

CON-218: What's coming answers what a set *introduces*, not what it does for
saved decks. First paint returns the set list; mechanics are `pending` until
cache/enrich. Enrich loads keywords from cards in the set, then keeps those
whose earliest print is this set or the same release day (companion products).
Returning evergreen keywords are omitted. Empty debut after a successful look
is a real zero; a failed look is unknown — never collapse those.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlparse

from deckwright import coverage
from deckwright import instance
from deckwright import mechanics

SAVE_DIR = instance.home_dir()
OVERLAY_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "release_overlay.json"
SETS_CACHE_PATH = SAVE_DIR / "calendar_sets.json"
COUNTS_CACHE_PATH = SAVE_DIR / "calendar_counts.json"  # legacy CON-147; unused by CON-218 path
MECHANICS_CACHE_PATH = SAVE_DIR / "calendar_mechanics.json"

SETS_MAX_AGE = 7 * 86400          # set list changes on release, not daily
COUNTS_MAX_AGE = 7 * 86400
MECHANICS_MAX_AGE = 7 * 86400
ART_MAX_AGE = 30 * 86400
CALENDAR_PAST_DAYS = 548            # ~18 months of released sets eligible
RELEASED_PAGE_SIZE = 6              # first paint; older sets behind offset
# Single-page OR-query is cheap; above this, fall back to per-set total_cards.
_BATCH_PAGE_CARD_CAP = 175
DEBUT_CACHE_MAX_AGE = 30 * 86400    # keyword first-print rarely moves

_WUBRG = "WUBRG"
_MISSING = object()


class CalendarUnavailable(Exception):
    """Set list cannot be loaded and there is no usable cache."""


ScryFn = Callable[..., Awaitable[dict]]


def _today() -> date:
    return datetime.now(timezone.utc).date()


def load_overlay() -> dict[str, dict]:
    """Hand overlay keyed by set code. Missing file → empty overlay."""
    if not OVERLAY_PATH.exists():
        return {}
    try:
        raw = json.loads(OVERLAY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    sets = raw.get("sets") or {}
    return {str(k).lower(): dict(v) for k, v in sets.items() if isinstance(v, dict)}


def merge_set_record(scry_set: dict, overlay: dict[str, dict]) -> dict:
    """Scryfall /sets row merged with overlay fields. Overlay wins on conflict."""
    code = str(scry_set.get("code") or "").lower()
    hand = overlay.get(code) or {}
    out = {
        "name": hand.get("name") or scry_set.get("name") or "",
        "code": code,
        "set_type": hand.get("set_type") or scry_set.get("set_type") or "",
        "released_at": hand.get("released_at") or scry_set.get("released_at") or "",
        "card_count": hand.get("card_count") if hand.get("card_count") is not None
                      else scry_set.get("card_count"),
    }
    if hand.get("hidden"):
        out["hidden"] = True
    # Announced debuts for unspoiled / hand-curated sets (e.g. Hobbit → Storied).
    raw_mechs = hand.get("mechanics")
    if isinstance(raw_mechs, list):
        names = [str(m).strip() for m in raw_mechs if str(m).strip()]
        if names:
            out["overlay_mechanics"] = names
    return out


def parse_released_at(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_future_set(row: dict, today: date | None = None) -> bool:
    """True when the set has not released yet (or has no parseable date)."""
    today = today or _today()
    rd = parse_released_at(row.get("released_at"))
    if rd is None:
        return True
    return rd > today


def calendar_eligible(row: dict, today: date | None = None) -> bool:
    """Playable sets in the calendar window — future or recently released."""
    if row.get("hidden"):
        return False
    st = row.get("set_type") or ""
    if st not in coverage.PLAYABLE_SET_TYPES:
        return False
    today = today or _today()
    rd = parse_released_at(row.get("released_at"))
    if rd is None:
        return True
    if rd > today:
        return True
    return (today - rd).days <= CALENDAR_PAST_DAYS


def saved_deck_identity(data: dict) -> str:
    """Scryfall id<= string for a saved deck — mirrors app._ci_from_saved."""
    brief = data.get("brief") or {}
    colors = brief.get("colors") or ""
    if colors:
        return "".join(c for c in colors.upper() if c in _WUBRG) or "C"
    if data.get("color_identity"):
        return _identity_string(list(data["color_identity"]))
    zone = data.get("commander_cards") or []
    if not zone:
        cmdr = data.get("commander_card") or {}
        partner = data.get("partner_card")
        zone = [cmdr]
        if partner and partner.get("name") and partner.get("name") != cmdr.get("name"):
            zone.append(partner)
    if zone:
        return _identity_string(_union_ci(*zone))
    cmdr = data.get("commander_card") or {}
    return "".join(cmdr.get("color_identity") or []) or "C"


def saved_deck_colors(identity: str) -> str:
    """WUBRG letters for pip colour — colourless uses empty string."""
    return "".join(c for c in identity if c in _WUBRG)


def _union_ci(*cards: dict) -> list[str]:
    found: set[str] = set()
    for c in cards:
        if not c:
            continue
        found.update(c.get("color_identity") or [])
    return [c for c in _WUBRG if c in found]


def _identity_string(ci_list: list[str] | None) -> str:
    s = "".join(ci_list or [])
    return s or "C"


def count_cache_key(set_code: str, identity: str) -> str:
    return f"{set_code.lower()}:{identity or 'C'}"


def read_sets_cache() -> dict | None:
    if not SETS_CACHE_PATH.exists():
        return None
    try:
        return json.loads(SETS_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_sets_cache(payload: dict) -> None:
    SAVE_DIR.mkdir(exist_ok=True)
    SETS_CACHE_PATH.write_text(json.dumps(payload, indent=1))


def read_counts_cache() -> dict:
    if not COUNTS_CACHE_PATH.exists():
        return {"fetched_at": 0, "counts": {}}
    try:
        data = json.loads(COUNTS_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"fetched_at": 0, "counts": {}}
    data.setdefault("counts", {})
    return data


def write_counts_cache(data: dict) -> None:
    SAVE_DIR.mkdir(exist_ok=True)
    data["fetched_at"] = time.time()
    COUNTS_CACHE_PATH.write_text(json.dumps(data, indent=1))


def read_mechanics_cache() -> dict:
    if not MECHANICS_CACHE_PATH.exists():
        return {"fetched_at": 0, "sets": {}, "debut": {}}
    try:
        data = json.loads(MECHANICS_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"fetched_at": 0, "sets": {}, "debut": {}}
    data.setdefault("sets", {})
    data.setdefault("debut", {})
    return data


def write_mechanics_cache(data: dict) -> None:
    SAVE_DIR.mkdir(exist_ok=True)
    data["fetched_at"] = time.time()
    MECHANICS_CACHE_PATH.write_text(json.dumps(data, indent=1))


# Sentence openers that look like ability words but aren't mechanics.
_ORACLE_AW_SKIP = frozenset({
    "When", "Whenever", "At", "If", "As", "For", "This", "That", "Those",
    "Target", "Each", "Every", "Create", "Exile", "Destroy", "Return", "Put",
    "Draw", "Discard", "Sacrifice", "Attach", "Equip", "Enchant", "Tap",
    "Untap", "Search", "Shuffle", "Reveal", "Look", "Choose", "You", "Your",
    "They", "Their", "Then", "Otherwise", "Instead", "Until", "During",
    "Before", "After", "Unless", "While", "Where", "With", "Without",
    "Add", "Remove", "Gain", "Lose", "Pay", "Spend", "Cast", "Play",
    "Activate", "Counter", "Copy", "Deal", "Prevent", "Regenerate",
})

# "Storied (" / "Landfall —" at a line start. Captures ability words Scryfall
# has not yet promoted into the keywords array (Hobbit's Storied is the case).
_ORACLE_AW_RE = re.compile(
    r"(?m)^(?:\{[^}]+\}\s*)*"
    r"([A-Z][A-Za-z'’]*(?:\s+[A-Z][A-Za-z'’]*){0,2})"
    r"\s*(?:\(|—)"
)


def keywords_from_cards(cards: list[dict]) -> list[str]:
    """Unique Scryfall keyword strings from card payloads — never invented."""
    seen: set[str] = set()
    out: list[str] = []
    for card in cards:
        for kw in card.get("keywords") or []:
            if not isinstance(kw, str):
                continue
            name = kw.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
    out.sort(key=str.lower)
    return out


def _card_oracle_blobs(card: dict) -> list[str]:
    blobs: list[str] = []
    top = card.get("oracle_text")
    if isinstance(top, str) and top.strip():
        blobs.append(top)
    for face in card.get("card_faces") or []:
        text = face.get("oracle_text")
        if isinstance(text, str) and text.strip():
            blobs.append(text)
    return blobs


def oracle_ability_words(cards: list[dict]) -> list[str]:
    """Ability-word shapes from oracle text (Storied, Landfall, …).

    Only names that appear on the cards — never invented. Used when Scryfall
    has not indexed a new mechanic into `keywords` yet.
    """
    seen: set[str] = set()
    out: list[str] = []
    for card in cards:
        for blob in _card_oracle_blobs(card):
            for m in _ORACLE_AW_RE.finditer(blob):
                name = m.group(1).strip()
                if not name or name in _ORACLE_AW_SKIP or name in seen:
                    continue
                seen.add(name)
                out.append(name)
    out.sort(key=str.lower)
    return out


def mechanic_candidates_from_cards(cards: list[dict]) -> list[str]:
    """Keywords ∪ oracle ability words — the pool we debut-filter."""
    seen: set[str] = set()
    out: list[str] = []
    for name in keywords_from_cards(cards) + oracle_ability_words(cards):
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    out.sort(key=str.lower)
    return out


def mechanic_href(set_code: str, name: str) -> str:
    """Scryfall search for this mechanic inside the set — real cards only."""
    code = (set_code or "").lower()
    q = f'set:{code} (keyword:"{name}" or o:"{name}")'
    return "https://scryfall.com/search?q=" + quote(q, safe="")


def is_debut_for_set(
    set_code: str,
    set_released_at: str | None,
    first_set: str | None,
    first_released_at: str | None,
) -> bool:
    """True when the keyword's earliest print is this set or its release day.

    Same-day companion products (e.g. EOE + EOC) share a debut wave. A keyword
    whose first print is years earlier is a reprint, not new here.
    """
    code = (set_code or "").lower()
    first = (first_set or "").lower()
    if code and first and code == first:
        return True
    set_day = (set_released_at or "")[:10]
    first_day = (first_released_at or "")[:10]
    if set_day and first_day and set_day == first_day:
        return True
    return False


def filter_debut_names(
    set_code: str,
    set_released_at: str | None,
    keywords: list[str],
    debut_index: dict[str, dict],
) -> tuple[list[str] | None, int]:
    """Return (sorted debut names, unchecked_count).

    None names = could not decide whether anything debuted (partial or total
    unknown with zero confirmed debuts). Empty list with unchecked=0 = looked;
    none debuted. Confirmed debuts still return when some peers are unchecked —
    we know those names; we do not invent emptiness from an unread sibling.
    """
    debuts: list[str] = []
    unchecked = 0
    for kw in keywords:
        entry = debut_index.get(kw)
        if not isinstance(entry, dict) or entry.get("status") == "unknown":
            unchecked += 1
            continue
        if entry.get("status") != "known":
            unchecked += 1
            continue
        if is_debut_for_set(
            set_code,
            set_released_at,
            entry.get("first_set"),
            entry.get("first_released_at"),
        ):
            debuts.append(kw)
    # House rule: unknown is not a zero. Flying known-reprint + Warp unread
    # must not paint "No new keywords."
    if not debuts and unchecked:
        return None, unchecked
    debuts.sort(key=str.lower)
    return debuts, unchecked


def merge_overlay_mechanics(
    payload: dict,
    announced: list[str] | None,
    *,
    set_code: str = "",
) -> dict:
    """Union hand-announced names into a mechanics payload.

    Failed enrich / warm unknown cache must not bury overlay debuts (Hobbit
    → Storied). Announced names are real Magic words from the hand file —
    never model-invented.
    """
    names = [n for n in (announced or []) if isinstance(n, str) and n.strip()]
    if not names:
        return payload
    code = set_code or (payload.get("set_code") if isinstance(payload, dict) else "") or ""
    if not isinstance(payload, dict):
        return mechanics_payload(status="known", names=names, set_code=code)
    status = payload.get("status") or "pending"
    if status == "known":
        have = list(payload.get("names") or [])
        seen = {n.lower() for n in have}
        for n in names:
            if n.lower() not in seen:
                have.append(n)
                seen.add(n.lower())
        return mechanics_payload(status="known", names=have, set_code=code)
    # pending / unknown — announced names win over silence or failed look
    return mechanics_payload(status="known", names=names, set_code=code)


def mechanics_payload(
    *,
    status: str,
    names: list[str] | None = None,
    set_code: str = "",
) -> dict:
    """Panel payload. known may carry an empty names list (real zero).

    Each name gets a Scryfall href so the tile can link to the cards.
    """
    row: dict[str, Any] = {"status": status}
    if status == "known":
        clean = list(names or [])
        row["names"] = clean
        row["items"] = [
            {"name": n, "href": mechanic_href(set_code, n)} for n in clean
        ]
    return row


def cached_set_mechanics(cache: dict, set_code: str) -> dict | object:
    """Return mechanics payload dict, or _MISSING when absent/stale."""
    code = (set_code or "").lower()
    entry = (cache.get("sets") or {}).get(code)
    if not isinstance(entry, dict) or not _entry_fresh(entry, MECHANICS_MAX_AGE):
        return _MISSING
    status = entry.get("status")
    if status == "unknown":
        return mechanics_payload(status="unknown")
    if status == "known":
        return mechanics_payload(
            status="known", names=list(entry.get("names") or []), set_code=code)
    return _MISSING


def _entry_fresh(entry: dict | None, max_age: float) -> bool:
    if not isinstance(entry, dict):
        return False
    return (time.time() - float(entry.get("fetched_at") or 0)) < max_age


def cached_hit_entry(counts_cache: dict, set_code: str, identity: str):
    """Return a hit cache dict, None (cached unknown), or _MISSING.

    CON-147 entries carry `roles`. Legacy colour-only entries (bare int or
    dict without `roles`) are treated as missing so enrich reclassifies.
    """
    key = count_cache_key(set_code, identity)
    entry = (counts_cache.get("counts") or {}).get(key)
    if entry is None:
        return _MISSING
    if isinstance(entry, dict):
        if not _entry_fresh(entry, COUNTS_MAX_AGE):
            return _MISSING
        if entry.get("status") == "unknown":
            return None
        # Pre-CON-147 cache: known colour count without roles — not reusable.
        if "roles" not in entry:
            return _MISSING
        return entry
    # Legacy bare int — never has roles.
    return _MISSING


def cached_count_value(counts_cache: dict, set_code: str, identity: str):
    """Return int, None (cached unknown), or _MISSING (not cached / stale)."""
    entry = cached_hit_entry(counts_cache, set_code, identity)
    if entry is _MISSING:
        return _MISSING
    if entry is None:
        return None
    return int(entry.get("count") or 0)


def store_count(counts_cache: dict, set_code: str, identity: str,
                count: int | None,
                roles: dict[str, int] | None = None) -> None:
    key = count_cache_key(set_code, identity)
    if count is None and roles is None:
        counts_cache.setdefault("counts", {})[key] = {
            "status": "unknown", "count": None, "roles": None,
            "fetched_at": time.time(),
        }
    else:
        role_map = {
            r: int((roles or {}).get(r) or 0)
            for r in mechanics.ROLE_DISPLAY_ORDER
        }
        # Drop zeros so the payload stays small; absence means 0.
        role_map = {r: n for r, n in role_map.items() if n > 0}
        total = int(count) if count is not None else sum(role_map.values())
        counts_cache.setdefault("counts", {})[key] = {
            "status": "known",
            "count": total,
            "roles": role_map,
            "fetched_at": time.time(),
        }


def art_crop_from_card(card: dict) -> str | None:
    uris = card.get("image_uris") or {}
    if uris.get("art_crop"):
        return uris["art_crop"]
    for face in card.get("card_faces") or []:
        fu = face.get("image_uris") or {}
        if fu.get("art_crop"):
            return fu["art_crop"]
    return None


async def fetch_sets_remote(scry: ScryFn) -> list[dict]:
    j = await scry("/sets")
    return list(j.get("data") or [])


async def get_merged_sets(scry: ScryFn, *, force: bool = False) -> list[dict]:
    """Cached Scryfall /sets merged with hand overlay."""
    overlay = load_overlay()
    if not force:
        cached = read_sets_cache()
        if cached and time.time() - cached.get("fetched_at", 0) < SETS_MAX_AGE:
            return list(cached.get("sets") or [])

    try:
        raw = await fetch_sets_remote(scry)
    except Exception:
        cached = read_sets_cache()
        if cached and cached.get("sets"):
            return list(cached["sets"])
        raise CalendarUnavailable("Scryfall /sets unreachable and no cache on disk")

    merged = [merge_set_record(s, overlay) for s in raw]
    prev = read_sets_cache() or {}
    write_sets_cache({
        "fetched_at": time.time(),
        "sets": merged,
        "art": prev.get("art") or {},
    })
    return merged


def _empty_role_tallies() -> dict[str, set[str]]:
    return {r: set() for r in mechanics.ROLE_DISPLAY_ORDER}


def _roles_from_tallies(tallies: dict[str, set[str]]) -> dict[str, int]:
    return {r: len(ids) for r, ids in tallies.items() if ids}


def _card_uid(card: dict) -> str:
    return str(card.get("oracle_id") or card.get("id") or card.get("name") or "")


def _accumulate_roles(
    tallies_by_set: dict[str, dict[str, set[str]]],
    card: dict,
    role_queries: dict[str, list[str]],
) -> None:
    code = (card.get("set") or "").lower()
    if code not in tallies_by_set:
        return
    uid = _card_uid(card)
    if not uid:
        return
    for role in mechanics.classify_card_roles(card, role_queries):
        tallies_by_set[code][role].add(uid)


async def _page_search(scry: ScryFn, q: str) -> list[dict] | None:
    """All cards for a search. None = failed request (unknown), [] = genuine zero."""
    try:
        j = await scry("/cards/search", q=q, unique="cards")
    except Exception:
        return None
    cards = list(j.get("data") or [])
    # 404 → empty data, no total_cards — genuine zero.
    while j.get("has_more") and j.get("next_page"):
        parsed = urlparse(str(j["next_page"]))
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            j = await scry(parsed.path, **params)
        except Exception:
            return None
        cards.extend(j.get("data") or [])
    return cards


async def count_legal_in_set(scry: ScryFn, set_code: str, identity: str,
                             counts_cache: dict | None = None,
                             *, persist: bool = True) -> int | None:
    """Classified role-card total in a set. None = unknown, 0 = none classified."""
    cache = counts_cache if counts_cache is not None else read_counts_cache()
    cached = cached_count_value(cache, set_code, identity)
    if cached is not _MISSING:
        return cached  # type: ignore[return-value]

    roles = await count_roles_in_set(scry, set_code, identity, cache, persist=persist)
    if roles is None:
        return None
    return sum(roles.values())


async def count_roles_in_set(
    scry: ScryFn,
    set_code: str,
    identity: str,
    counts_cache: dict | None = None,
    *,
    persist: bool = True,
    role_queries: dict[str, list[str]] | None = None,
) -> dict[str, int] | None:
    """Per-plan_role card counts for one set×identity. None = unknown."""
    cache = counts_cache if counts_cache is not None else read_counts_cache()
    entry = cached_hit_entry(cache, set_code, identity)
    if entry is not _MISSING:
        if entry is None:
            return None
        return dict(entry.get("roles") or {})

    identity = identity or "C"
    role_queries = role_queries if role_queries is not None else mechanics.queries_by_plan_role()
    cards = await _page_search(scry, f"set:{set_code} id<={identity}")
    if cards is None:
        store_count(cache, set_code, identity, None, roles=None)
        if persist:
            write_counts_cache(cache)
        return None

    tallies = _empty_role_tallies()
    bucket = {set_code.lower(): tallies}
    for card in cards:
        _accumulate_roles(bucket, card, role_queries)
    roles = _roles_from_tallies(tallies)
    store_count(cache, set_code, identity, sum(roles.values()), roles=roles)
    if persist:
        write_counts_cache(cache)
    return roles


async def _count_identity_across_sets(
    scry: ScryFn,
    set_codes: list[str],
    identity: str,
    counts_cache: dict,
    role_queries: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, int] | None]:
    """Classify colour-legal cards into plan_roles for each set.

    One search per identity across the page's sets when the result fits a
    short page; otherwise per-set. Failures return None for the affected
    codes — never an empty role map pretending to be a real zero.
    """
    identity = identity or "C"
    role_queries = role_queries if role_queries is not None else mechanics.queries_by_plan_role()
    codes = [c.lower() for c in set_codes]
    out: dict[str, dict[str, int] | None] = {}
    needed: list[str] = []
    for code in codes:
        entry = cached_hit_entry(counts_cache, code, identity)
        if entry is _MISSING:
            needed.append(code)
        elif entry is None:
            out[code] = None
        else:
            out[code] = dict(entry.get("roles") or {})

    if not needed:
        return out

    set_or = " OR ".join(f"set:{c}" for c in needed)
    q = f"({set_or}) id<={identity}"
    try:
        first = await scry("/cards/search", q=q, unique="cards")
    except Exception:
        for code in needed:
            out[code] = None
            store_count(counts_cache, code, identity, None, roles=None)
        return out

    total = int(first.get("total_cards") or 0)
    if total > _BATCH_PAGE_CARD_CAP:
        async def one(code: str) -> tuple[str, dict[str, int] | None]:
            roles = await count_roles_in_set(
                scry, code, identity, counts_cache,
                persist=False, role_queries=role_queries,
            )
            return code, roles

        pairs = await asyncio.gather(*[one(c) for c in needed])
        for code, roles in pairs:
            out[code] = roles
        return out

    # Page the OR-query fully, then classify into per-set role tallies.
    cards = list(first.get("data") or [])
    j = first
    while j.get("has_more") and j.get("next_page"):
        parsed = urlparse(str(j["next_page"]))
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            j = await scry(parsed.path, **params)
        except Exception:
            for code in needed:
                if code not in out:
                    out[code] = None
                    store_count(counts_cache, code, identity, None, roles=None)
            return out
        cards.extend(j.get("data") or [])

    tallies_by_set = {c: _empty_role_tallies() for c in needed}
    for card in cards:
        _accumulate_roles(tallies_by_set, card, role_queries)
    for code in needed:
        roles = _roles_from_tallies(tallies_by_set[code])
        out[code] = roles
        store_count(counts_cache, code, identity, sum(roles.values()), roles=roles)
    return out


async def fetch_set_art(scry: ScryFn, set_code: str,
                        sets_cache: dict | None = None,
                        *, persist: bool = True,
                        fetch: bool = True) -> str | None:
    """Top EDHREC card art_crop for a released set. None if unavailable."""
    sets_cache = sets_cache if sets_cache is not None else (read_sets_cache() or {})
    art_map = sets_cache.setdefault("art", {})
    code = set_code.lower()
    entry = art_map.get(code)
    if _entry_fresh(entry, ART_MAX_AGE):
        if entry.get("url"):
            return entry["url"]
        if entry.get("status") == "unknown":
            return None

    if not fetch:
        return None

    q = f"set:{code} -is:token"
    try:
        j = await scry("/cards/search", q=q, order="edhrec", unique="cards")
        cards = j.get("data") or []
        url = art_crop_from_card(cards[0]) if cards else None
    except Exception:
        art_map[code] = {"status": "unknown", "fetched_at": time.time()}
        if persist:
            write_sets_cache(sets_cache)
        return None

    art_map[code] = {"url": url, "status": "known", "fetched_at": time.time()}
    if persist:
        write_sets_cache(sets_cache)
    return url


def deck_hit_row(deck: dict, count: int | None, identity: str,
                 *, status: str | None = None,
                 roles: dict[str, int] | None = None) -> dict:
    if status is None:
        status = "unknown" if count is None else "known"
    row = {
        "id": deck.get("id") or "",
        "name": deck.get("name") or deck.get("commander") or "",
        "colors": saved_deck_colors(identity),
        "count": count,
        "status": status,
    }
    # pending/unknown: roles omitted (not an empty map — empty would mean
    # "classified none"). known: always include roles (possibly {}).
    if status == "known":
        row["roles"] = {
            r: int((roles or {}).get(r) or 0)
            for r in mechanics.ROLE_DISPLAY_ORDER
            if int((roles or {}).get(r) or 0) > 0
        }
    elif roles is not None:
        row["roles"] = roles
    return row


def hit_has_classified(hit: dict) -> bool:
    """True when a known hit has at least one classified role card."""
    if hit.get("status") != "known":
        return False
    roles = hit.get("roles")
    if isinstance(roles, dict):
        return any(int(v or 0) > 0 for v in roles.values())
    return int(hit.get("count") or 0) > 0


def _slice_released(released_rows: list[dict], limit: int, offset: int) -> tuple[list[dict], int, bool]:
    total = len(released_rows)
    limit = max(0, int(limit))
    offset = max(0, int(offset))
    page = released_rows[offset:offset + limit] if limit else []
    has_more = (offset + len(page)) < total
    return page, total, has_more


def _set_shell(row: dict, *, future: bool, art_url: str | None = None,
               mechanics: dict | None = None,
               decks: list[dict] | None = None) -> dict:
    # decks kept empty for back-compat with old clients; CON-218 content is mechanics.
    return {
        "name": row.get("name"),
        "code": row.get("code") or "",
        "set_type": row.get("set_type"),
        "released_at": row.get("released_at"),
        "card_count": row.get("card_count"),
        "future": future,
        "art_url": art_url,
        "mechanics": mechanics if mechanics is not None else mechanics_payload(status="pending"),
        "decks": decks if decks is not None else [],
    }


def _hits_for_set(
    code: str,
    deck_rows: list[dict],
    counts_cache: dict,
    *,
    allow_pending: bool,
) -> list[dict]:
    hits: list[dict] = []
    for dr in deck_rows:
        identity = dr["identity"]
        entry = cached_hit_entry(counts_cache, code, identity)
        if entry is _MISSING:
            if allow_pending:
                hits.append(deck_hit_row(dr["data"], None, identity, status="pending"))
            else:
                hits.append(deck_hit_row(dr["data"], None, identity, status="unknown"))
        elif entry is None:
            hits.append(deck_hit_row(dr["data"], None, identity, status="unknown"))
        else:
            roles = dict(entry.get("roles") or {})
            count = int(entry.get("count") or sum(roles.values()))
            hits.append(deck_hit_row(dr["data"], count, identity, roles=roles))
    hits.sort(key=lambda h: (
        0 if h["status"] == "known" else 1,
        -(h["count"] or -1) if h["status"] == "known" else 0,
        h["name"].lower(),
    ))
    return hits


async def _keyword_debut_entry(scry: ScryFn, keyword: str) -> dict:
    """Earliest print for a mechanic name. status unknown when the look failed.

    Prefer `keyword:` (indexed abilities). Fall back to oracle text for names
    Scryfall has not promoted into keywords yet (Hobbit's Storied).
    """
    queries = [f'keyword:"{keyword}"', f'o:"{keyword}"']
    saw_success = False
    for q in queries:
        try:
            j = await scry("/cards/search", q=q, unique="prints",
                           order="released", dir="asc")
        except Exception:
            continue
        saw_success = True
        cards = list(j.get("data") or [])
        if not cards:
            continue
        card = cards[0]
        return {
            "status": "known",
            "first_set": str(card.get("set") or "").lower(),
            "first_released_at": str(card.get("released_at") or "")[:10],
            "fetched_at": time.time(),
        }
    if not saw_success:
        return {"status": "unknown", "fetched_at": time.time()}
    return {
        "status": "known",
        "first_set": "",
        "first_released_at": "",
        "fetched_at": time.time(),
    }


async def fetch_set_debut_mechanics(
    scry: ScryFn,
    set_code: str,
    set_released_at: str | None,
    mech_cache: dict,
    *,
    persist: bool = True,
) -> dict:
    """Debut mechanics for one released set. Unknown on failed card look."""
    code = (set_code or "").lower()
    cached = cached_set_mechanics(mech_cache, code)
    if cached is not _MISSING:
        return cached  # type: ignore[return-value]

    cards = await _page_search(scry, f"set:{code}")
    if cards is None:
        entry = {"status": "unknown", "fetched_at": time.time()}
        mech_cache.setdefault("sets", {})[code] = entry
        if persist:
            write_mechanics_cache(mech_cache)
        return mechanics_payload(status="unknown")

    candidates = mechanic_candidates_from_cards(cards)
    debut_index = mech_cache.setdefault("debut", {})
    for kw in candidates:
        prior = debut_index.get(kw)
        if isinstance(prior, dict) and prior.get("status") == "known" \
                and _entry_fresh(prior, DEBUT_CACHE_MAX_AGE):
            continue
        debut_index[kw] = await _keyword_debut_entry(scry, kw)

    names, unchecked = filter_debut_names(
        code, set_released_at, candidates, debut_index)
    if names is None:
        entry = {"status": "unknown", "fetched_at": time.time()}
        mech_cache.setdefault("sets", {})[code] = entry
        if persist:
            write_mechanics_cache(mech_cache)
        return mechanics_payload(status="unknown", set_code=code)

    entry = {
        "status": "known",
        "names": names,
        "keywords_seen": candidates,
        "unchecked": unchecked,
        "fetched_at": time.time(),
    }
    mech_cache.setdefault("sets", {})[code] = entry
    if persist:
        write_mechanics_cache(mech_cache)
    return mechanics_payload(status="known", names=names, set_code=code)


async def enrich_released_mechanics(
    scry: ScryFn,
    set_rows: list[dict],
    *,
    fetch_art: bool = True,
) -> dict[str, dict]:
    """Fill debut mechanics (and optional art) for released sets.

    `set_rows` need `code` and `released_at`. Returns
    {code: {"art_url", "mechanics"}}.
    """
    rows = [r for r in set_rows if r.get("code")]
    if not rows:
        return {}

    mech_cache = read_mechanics_cache()
    sets_disk = read_sets_cache() or {"sets": [], "art": {}, "fetched_at": 0}
    codes = [str(r["code"]).lower() for r in rows]

    async def mech_job(row: dict) -> tuple[str, dict]:
        code = str(row.get("code") or "").lower()
        payload = await fetch_set_debut_mechanics(
            scry, code, row.get("released_at"), mech_cache, persist=False)
        # Failed look / warm unknown must not wipe hand-announced debuts.
        payload = merge_overlay_mechanics(
            payload, row.get("overlay_mechanics") or [], set_code=code)
        return code, payload

    mech_tasks = [mech_job(r) for r in rows]
    art_tasks = [
        fetch_set_art(scry, code, sets_disk, persist=False, fetch=fetch_art)
        for code in codes
    ] if fetch_art else []

    if art_tasks:
        mech_results, art_urls = await asyncio.gather(
            asyncio.gather(*mech_tasks),
            asyncio.gather(*art_tasks),
        )
    else:
        mech_results = await asyncio.gather(*mech_tasks)
        art_urls = [None] * len(codes)

    write_mechanics_cache(mech_cache)
    if fetch_art:
        write_sets_cache(sets_disk)

    by_code = {code: payload for code, payload in mech_results}
    out: dict[str, dict] = {}
    for i, code in enumerate(codes):
        out[code] = {
            "art_url": art_urls[i] if i < len(art_urls) else None,
            "mechanics": by_code.get(code) or mechanics_payload(
                status="unknown", set_code=code),
        }
    return out


async def enrich_released_counts(
    scry: ScryFn,
    set_codes: list[str],
    decks: list[dict],
    *,
    fetch_art: bool = True,
) -> dict[str, dict]:
    """Deprecated CON-147 path — kept for callers/tests; prefer mechanics.

    CON-218 calendar no longer uses this. Still honest: unknown ≠ zero.
    """
    codes = [c.lower() for c in set_codes if c]
    if not codes:
        return {}

    deck_rows = [
        {"data": d, "identity": saved_deck_identity(d)}
        for d in decks
    ]
    identities = sorted({dr["identity"] for dr in deck_rows}) or ["C"]
    counts_cache = read_counts_cache()
    sets_disk = read_sets_cache() or {"sets": [], "art": {}, "fetched_at": 0}
    role_queries = mechanics.queries_by_plan_role()

    async def counts_job(identity: str) -> None:
        await _count_identity_across_sets(
            scry, codes, identity, counts_cache, role_queries=role_queries)

    count_tasks = [counts_job(i) for i in identities]
    art_tasks = [
        fetch_set_art(scry, code, sets_disk, persist=False, fetch=fetch_art)
        for code in codes
    ] if fetch_art else []

    art_urls: list[str | None] = []
    if art_tasks:
        count_results, art_urls = await asyncio.gather(
            asyncio.gather(*count_tasks),
            asyncio.gather(*art_tasks),
        )
        _ = count_results
    else:
        await asyncio.gather(*count_tasks)
        art_urls = [None] * len(codes)

    write_counts_cache(counts_cache)
    if fetch_art:
        write_sets_cache(sets_disk)

    out: dict[str, dict] = {}
    for i, code in enumerate(codes):
        out[code] = {
            "art_url": art_urls[i] if i < len(art_urls) else None,
            "decks": _hits_for_set(code, deck_rows, counts_cache, allow_pending=False),
        }
    return out


async def build_calendar(
    scry: ScryFn,
    decks: list[dict] | None = None,
    *,
    today: date | None = None,
    force_sets: bool = False,
    released_limit: int = RELEASED_PAGE_SIZE,
    released_offset: int = 0,
    fetch_counts: bool = False,
    fetch_mechanics: bool = False,
) -> dict[str, Any]:
    """Calendar payload for /api/calendar.

    First paint: `/sets` (or disk cache), released page capped, debut mechanics
    only from cache. Misses are `pending`, never an invented empty debut list.
    `decks` is ignored (CON-218). `fetch_counts` is accepted as an alias for
    `fetch_mechanics` so old query params keep working.
    """
    today = today or _today()
    merged = await get_merged_sets(scry, force=force_sets)
    overlay = load_overlay()
    _ = decks  # CON-218: saved decks no longer drive the panel

    eligible = [s for s in merged if calendar_eligible(s, today)]
    future_rows = sorted(
        [s for s in eligible if is_future_set(s, today)],
        key=lambda r: parse_released_at(r.get("released_at")) or date.max,
        reverse=True,
    )
    released_all = sorted(
        [s for s in eligible if not is_future_set(s, today)],
        key=lambda r: parse_released_at(r.get("released_at")) or date.min,
        reverse=True,
    )
    released_page, released_total, has_more = _slice_released(
        released_all, released_limit, released_offset)

    mech_cache = read_mechanics_cache()
    sets_disk = read_sets_cache() or {}
    do_fetch = bool(fetch_mechanics or fetch_counts)

    if do_fetch and released_page:
        enriched = await enrich_released_mechanics(
            scry, released_page, fetch_art=True)
        mech_cache = read_mechanics_cache()
        sets_disk = read_sets_cache() or sets_disk
    else:
        enriched = {}

    future_out: list[dict] = []
    for r in future_rows:
        code = r.get("code") or ""
        announced = r.get("overlay_mechanics") or []
        if announced:
            # Hand-announced debuts (What's coming before spoilers land).
            mech = mechanics_payload(
                status="known", names=list(announced), set_code=code)
        else:
            mech = mechanics_payload(status="pending", set_code=code)
        future_out.append(_set_shell(r, future=True, mechanics=mech))

    released_out: list[dict] = []
    for row in released_page:
        code = row.get("code") or ""
        announced = row.get("overlay_mechanics") or []
        if code in enriched:
            art = enriched[code].get("art_url")
            mech = enriched[code].get("mechanics") or mechanics_payload(
                status="unknown", set_code=code)
        else:
            art = await fetch_set_art(
                scry, code, sets_disk, persist=False, fetch=False)
            cached = cached_set_mechanics(mech_cache, code)
            if cached is _MISSING:
                # Released but enrich pending — overlay still beats silence.
                if announced:
                    mech = mechanics_payload(
                        status="known", names=list(announced), set_code=code)
                else:
                    mech = mechanics_payload(status="pending", set_code=code)
            else:
                mech = cached  # type: ignore[assignment]
        # CON-218 review: warm unknown / empty cache must not bury overlay.
        mech = merge_overlay_mechanics(mech, announced, set_code=code)
        released_out.append(
            _set_shell(row, future=False, art_url=art, mechanics=mech))

    pending = any(
        (row.get("mechanics") or {}).get("status") == "pending"
        for row in released_out
    )

    return {
        "today": today.isoformat(),
        "future": future_out,
        "released": released_out,
        "released_total": released_total,
        "released_has_more": has_more,
        "released_limit": max(0, int(released_limit)),
        "released_offset": max(0, int(released_offset)),
        "mechanics_complete": not pending,
        # Alias so older UI that checked counts_complete still progresses.
        "counts_complete": not pending,
        "overlay_codes": sorted(overlay.keys()),
    }
