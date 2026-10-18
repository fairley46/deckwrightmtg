"""EDHREC-seeded build (product path).

Thin live client for commander page + average deck (~2 GETs per build).
semantics: ok / missing / unknown — never `{}` on failure.

Pool injection happens before the model shortlist (not steer-only). Seed lands
are ignored; V1 curve / mana base unchanged.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from deckwright import commanders

_DEFAULT_EDHREC_BASE = "https://json.edhrec.com/pages"
EDHREC_UA = {
    "User-Agent": "Deckwright/1.0 (local personal deckbuilder)",
    "Accept": "application/json",
}
MIN_DECKS = 2000
TREND_RISING = 2.0


def edhrec_base() -> str:
    """Live EDHREC JSON root. Override with DECKWRIGHT_EDHREC_BASE for outage tests.

    Trailing slashes stripped. Empty / unset → production host. Never invent
    a page — a bad override yields unreachable, same as a dead network.
    """
    raw = os.environ.get("DECKWRIGHT_EDHREC_BASE", "").strip()
    if not raw:
        return _DEFAULT_EDHREC_BASE
    return raw.rstrip("/")

PROVENANCE = {
    "seeded": "Built from what people play, tuned so you can cast it.",
    "thin_data": (
        "Not enough people have played this commander — built from the card text."
    ),
    "unreachable": "Couldn't reach EDHREC — built from the card text.",
}

_LAND_BUCKETS = frozenset({"land", "basic", "nonbasic"})


def enabled() -> bool:
    """Green-only product flag — never on blue production."""
    flag = os.environ.get("DECKWRIGHT_EDHREC_SEED", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    try:
        from deckwright import instance
        return instance.env_name() == "green"
    except Exception:
        return False


def commander_json_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    container = data.get("container") or {}
    jd = container.get("json_dict") if isinstance(container, dict) else None
    return jd if isinstance(jd, dict) else {}


def parse_num_decks(commander_page: dict | None) -> int | None:
    jd = commander_json_dict(commander_page)
    card = jd.get("card") if isinstance(jd.get("card"), dict) else {}
    raw = card.get("num_decks")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def parse_card_meta(commander_page: dict | None, *, num_decks: int | None) -> dict[str, dict]:
    """Name → inclusion %, synergy, rising (from cardlists)."""
    jd = commander_json_dict(commander_page)
    lists = jd.get("cardlists") or []
    meta: dict[str, dict] = {}
    new_names: set[str] = set()
    denom = num_decks or 0
    for block in lists:
        if not isinstance(block, dict):
            continue
        header = str(block.get("header") or "")
        if header.lower().startswith("new"):
            for cv in block.get("cardviews") or []:
                if isinstance(cv, dict) and cv.get("name"):
                    new_names.add(str(cv["name"]))
        for cv in block.get("cardviews") or []:
            if not isinstance(cv, dict):
                continue
            name = (cv.get("name") or "").strip()
            if not name:
                continue
            nd = cv.get("num_decks")
            try:
                card_decks = int(nd) if nd is not None else None
            except (TypeError, ValueError):
                card_decks = None
            inclusion = None
            if card_decks is not None and denom > 0:
                inclusion = round(100.0 * card_decks / denom, 1)
            try:
                synergy = float(cv.get("synergy")) if cv.get("synergy") is not None else None
            except (TypeError, ValueError):
                synergy = None
            try:
                trend = float(cv.get("trend_zscore")) if cv.get("trend_zscore") is not None else None
            except (TypeError, ValueError):
                trend = None
            rising = name in new_names or (
                trend is not None and trend >= TREND_RISING
            )
            prev = meta.get(name) or {}
            meta[name] = {
                "inclusion_pct": inclusion if inclusion is not None else prev.get("inclusion_pct"),
                "synergy": synergy if synergy is not None else prev.get("synergy"),
                "trend_zscore": trend if trend is not None else prev.get("trend_zscore"),
                "rising": rising or prev.get("rising", False),
            }
    return meta


def parse_average_names(average_page: dict | None) -> list[str]:
    """Non-land card names from the average-deck payload. Lands skipped."""
    if not isinstance(average_page, dict):
        return []
    deck = average_page.get("deck")
    if not isinstance(deck, dict):
        return []
    cards = deck.get("cards")
    if not isinstance(cards, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for bucket, entries in cards.items():
        if str(bucket).lower() in _LAND_BUCKETS:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or not entry:
                continue
            name = str(entry[0]).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def classify_seed(
    commander_status: str,
    average_status: str,
    num_decks: int | None,
    average_names: list[str],
) -> str:
    if commander_status == "unknown" or average_status == "unknown":
        return "unreachable"
    if commander_status == "missing":
        return "thin_data"
    if num_decks is None or num_decks < MIN_DECKS:
        return "thin_data"
    if average_status != "ok" or not average_names:
        return "thin_data"
    return "seeded"


@dataclass
class SeedBundle:
    status: str
    provenance: str
    num_decks: int | None = None
    commander_fetch: str = "unknown"
    average_fetch: str = "unknown"
    average_names: list[str] = field(default_factory=list)
    meta: dict[str, dict] = field(default_factory=dict)
    slug: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "seeded" and bool(self.average_names)

    def as_response(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provenance": self.provenance,
            "num_decks": self.num_decks,
            "commander_fetch": self.commander_fetch,
            "average_fetch": self.average_fetch,
            "seed_cards": len(self.average_names),
            "min_decks": MIN_DECKS,
        }


async def _fetch_json(
    url: str,
    client: httpx.AsyncClient,
) -> tuple[str, dict | None]:
    """One EDHREC GET. Returns (ok|missing|unknown, payload)."""
    r = None
    for attempt in range(3):
        try:
            r = await client.get(url)
        except Exception:
            if attempt == 2:
                return "unknown", None
            await asyncio.sleep(0.8 * (2 ** attempt))
            continue
        if r.status_code == 429:
            await asyncio.sleep(1.0 * (2 ** attempt))
            continue
        if r.status_code in (500, 502, 503):
            await asyncio.sleep(0.8 * (2 ** attempt))
            continue
        break
    if r is None:
        return "unknown", None
    if r.status_code in (403, 404):
        return "missing", None
    if r.status_code != 200:
        return "unknown", None
    try:
        return "ok", r.json()
    except Exception:
        return "unknown", None


async def load_for_commander(
    commander_name: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> SeedBundle:
    """Live fetch: commanders page + average deck for one commander."""
    slug = commanders.edhrec_slug(commander_name)
    if not slug:
        bundle = SeedBundle(
            status="unreachable",
            provenance=PROVENANCE["unreachable"],
            commander_fetch="missing",
            average_fetch="skipped",
        )
        return bundle

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=20, headers=EDHREC_UA)
    try:
        base = edhrec_base()
        cmd_status, cmd_data = await _fetch_json(
            f"{base}/commanders/{slug}.json", client)
        num_decks = parse_num_decks(cmd_data) if cmd_status == "ok" else None

        avg_status = "skipped"
        avg_data = None
        average_names: list[str] = []
        if cmd_status == "ok" and num_decks is not None and num_decks >= MIN_DECKS:
            avg_status, avg_data = await _fetch_json(
                f"{base}/average-decks/{slug}.json", client)
            average_names = parse_average_names(avg_data)

        status = classify_seed(cmd_status, avg_status, num_decks, average_names)
        # Inclusion % is a finding only when we seeded. Thin samples (e.g. 1.4%
        # of 139) must not paint the same visual as 77% of 43k — unknown≠zero
        # in a new costume (Box review after green-d band probe).
        meta: dict[str, dict] = {}
        if status == "seeded" and cmd_status == "ok":
            meta = parse_card_meta(cmd_data, num_decks=num_decks)
        return SeedBundle(
            status=status,
            provenance=PROVENANCE[status],
            num_decks=num_decks,
            commander_fetch=cmd_status,
            average_fetch=avg_status,
            average_names=average_names,
            meta=meta,
            slug=slug,
        )
    finally:
        if owns:
            await client.aclose()


def is_land_type(type_line: str | None) -> bool:
    return "land" in (type_line or "").lower()


def accepts_seed_card(
    card: dict,
    *,
    ci: str,
    card_pool: str,
    max_usd: float | None,
    bracket: int = 2,
) -> bool:
    """Legality / colour / budget / land filter for a resolved seed card."""
    if is_land_type(card.get("type")):
        return False
    if bracket <= 2 and card.get("gc"):
        return False
    if card_pool == "tournament":
        if (card.get("legal") or "") != "legal":
            return False
    ci_set = set(ci or "")
    colors = set(card.get("color_identity") or [])
    if ci_set and colors and not colors <= ci_set:
        return False
    usd = card.get("usd")
    if max_usd is not None and usd is not None:
        try:
            if float(usd) > max_usd:
                return False
        except (TypeError, ValueError):
            pass
    return True


def merge_pool_front(pool: list[dict], seed_cards: list[dict]) -> list[dict]:
    if not seed_cards:
        return list(pool)
    have = {c.get("name") for c in pool if c.get("name")}
    front = [c for c in seed_cards if c.get("name") and c["name"] not in have]
    return front + list(pool)


def prefer_seed_order(pool: list[dict], meta: dict[str, dict]) -> list[dict]:
    def key(card: dict) -> tuple:
        name = card.get("name") or ""
        m = meta.get(name) or {}
        inc = m.get("inclusion_pct")
        try:
            score = float(inc) if inc is not None else -1.0
        except (TypeError, ValueError):
            score = -1.0
        return (-score, name.lower())

    return sorted(pool, key=key)


def inject_role_pools(
    role_pools: dict[str, list[dict]],
    seed_cards: list[dict],
    spell_roles: list[tuple],
) -> int:
    """Prepend resolved seed cards to every non-land role pool."""
    if not seed_cards:
        return 0
    added = 0
    for role, _target, _desc in spell_roles:
        if role == "lands":
            continue
        before = len(role_pools.get(role) or [])
        role_pools[role] = merge_pool_front(role_pools.get(role) or [], seed_cards)
        added += len(role_pools[role]) - before
    return added


def annotate_card(card: dict, meta: dict[str, dict], *, status: str) -> dict:
    """Attach EDHREC inclusion / rising for deck rows (non-scoring).

    Only when status is ``seeded``. Thin-data / unreachable keep the provenance
    line and leave card rows unadorned.
    """
    if status != "seeded" or not meta:
        return card
    name = card.get("name") or ""
    m = meta.get(name) or {}
    if not m:
        return card
    out = dict(card)
    if m.get("inclusion_pct") is not None:
        out["edhrec_inclusion_pct"] = m["inclusion_pct"]
    if m.get("rising"):
        out["edhrec_rising"] = True
    if m.get("synergy") is not None:
        out["edhrec_synergy"] = m["synergy"]
    return out
