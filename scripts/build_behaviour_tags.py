#!/usr/bin/env python3
"""
Build data/behaviour_tags.json — verified Scryfall otag: behaviour vocabulary.

otag: has no /catalog endpoint. A guessed tag 404s and the app reads that as
empty (CON-110). Every candidate is probed live; the build fails on 404, zero
cards, or an unreachable probe. Unverified tags are never written.

CON-151. Run: python scripts/build_behaviour_tags.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any, Awaitable, Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "behaviour_tags.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Verified seed (probed live while writing CON-151). aliases share a card count
# with the canonical tag — recorded once, not as duplicate entries. synonyms are
# player words that map onto the tag (steal → theft); they are not probed as tags.
SEED: list[dict[str, Any]] = [
    {"tag": "removal", "aliases": [], "synonyms": [
        "removal", "remove", "removing", "removes",
    ]},
    {"tag": "creature-removal", "aliases": [], "synonyms": [
        "creature-removal", "creature removal",
    ]},
    {"tag": "evasion", "aliases": [], "synonyms": ["evasion"]},
    {"tag": "spot-removal", "aliases": [], "synonyms": [
        "spot-removal", "spot removal",
    ]},
    {"tag": "draw", "aliases": [], "synonyms": [
        "draw", "drawing", "card draw", "carddraw", "draws",
    ]},
    {"tag": "burn", "aliases": [], "synonyms": ["burn", "burning"]},
    {"tag": "lifegain", "aliases": [], "synonyms": [
        "lifegain", "life gain", "gain life", "gaining life", "gains life",
    ]},
    {"tag": "ramp", "aliases": [], "synonyms": ["ramp", "ramping"]},
    {"tag": "recursion", "aliases": [], "synonyms": ["recursion"]},
    {"tag": "sacrifice-outlet", "aliases": [], "synonyms": [
        "sacrifice-outlet", "sacrifice outlet", "sac outlet",
    ]},
    {"tag": "protection", "aliases": [], "synonyms": ["protection"]},
    {"tag": "mill", "aliases": [], "synonyms": ["mill", "milling"]},
    {"tag": "tutor", "aliases": [], "synonyms": [
        "tutor", "tutoring", "tutors",
    ]},
    {"tag": "reanimate", "aliases": [], "synonyms": [
        "reanimate", "reanimation", "reanimating", "reanimator",
    ]},
    {"tag": "copy", "aliases": [], "synonyms": ["copy", "copying"]},
    # boardwipe / sweeper / wipe — same pool (934); one entry.
    {"tag": "boardwipe", "aliases": ["sweeper", "wipe"], "synonyms": [
        "boardwipe", "board wipe", "wipe", "wrath", "sweeper", "boardwipes",
    ]},
    {"tag": "untapper", "aliases": [], "synonyms": ["untapper"]},
    {"tag": "theft", "aliases": [], "synonyms": [
        "theft", "steal", "stealing", "steals",
    ]},
    {"tag": "cantrip", "aliases": [], "synonyms": ["cantrip", "cantrips"]},
    {"tag": "land-ramp", "aliases": [], "synonyms": [
        "land-ramp", "land ramp",
    ]},
    {"tag": "discard", "aliases": [], "synonyms": ["discard", "discarding"]},
    {"tag": "counterspell", "aliases": [], "synonyms": [
        "counterspell", "counterspells",
    ]},
    {"tag": "tax", "aliases": [], "synonyms": ["tax", "taxes"]},
    {"tag": "anthem", "aliases": [], "synonyms": ["anthem", "anthems"]},
    {"tag": "graveyard-hate", "aliases": [], "synonyms": [
        "graveyard-hate", "graveyard hate", "gy hate",
    ]},
    {"tag": "group-hug", "aliases": [], "synonyms": [
        "group-hug", "group hug",
    ]},
    {"tag": "lifedrain", "aliases": [], "synonyms": [
        "lifedrain", "life drain", "drain", "draining",
    ]},
    {"tag": "landfall", "aliases": [], "synonyms": ["landfall"]},
    # flicker / blink — same pool (196); one entry.
    {"tag": "flicker", "aliases": ["blink"], "synonyms": [
        "flicker", "flickering", "blink", "blinking",
    ]},
    {"tag": "wheel", "aliases": [], "synonyms": ["wheel", "wheels"]},
    {"tag": "clone", "aliases": [], "synonyms": ["clone", "clones", "cloning"]},
    {"tag": "ritual", "aliases": [], "synonyms": ["ritual", "rituals"]},
    {"tag": "pillowfort", "aliases": [], "synonyms": [
        "pillowfort", "pillow fort",
    ]},
    {"tag": "extra-turn", "aliases": [], "synonyms": [
        "extra-turn", "extra turn", "extra turns",
    ]},
    # otag:forcing was in the CON-151 seed table (52) but 404s live now —
    # the gate correctly refuses it. Do not re-add without a fresh probe.
]

ProbeFn = Callable[[str], Awaitable[tuple[str, int | None]]]


async def probe_tag(tag: str) -> tuple[str, int | None]:
    """Hit Scryfall for otag:<tag>. Returns (ok|zero|bad_request|unknown, cards).

    Network/throttle failures are unknown — never scored as zero (CON-56).
    """
    import time

    import httpx
    from deckwright import app

    q = f"otag:{tag}"
    async with app._gate:
        gap = time.time() - app._last[0]
        if gap < 0.12:
            await asyncio.sleep(0.12 - gap)
        app._last[0] = time.time()
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.get(
                    f"{app.SCRYFALL}/cards/search",
                    params={"q": q, "unique": "cards", "page": 1},
                    headers=app.UA,
                )
        except httpx.HTTPError:
            return "unknown", None
    if r.status_code == 400:
        return "bad_request", 0
    if r.status_code == 404:
        return "zero", 0
    if r.status_code == 429:
        await asyncio.sleep(1.2)
        return await probe_tag(tag)
    if r.status_code >= 500 or r.status_code != 200:
        return "unknown", None
    try:
        body = r.json()
    except ValueError:
        return "unknown", None
    total = body.get("total_cards")
    if total is None:
        total = len(body.get("data") or [])
    if not total:
        return "zero", 0
    return "ok", int(total)


async def build(
    *,
    candidates: list[dict[str, Any]] | None = None,
    out_path: pathlib.Path | None = None,
    probe: ProbeFn | None = None,
) -> int:
    """Probe every candidate + alias. Write only on a clean gate. Returns exit code."""
    candidates = candidates if candidates is not None else SEED
    out_path = out_path or OUT
    probe = probe or probe_tag

    dead: list[str] = []
    unknown: list[str] = []
    verified: list[dict[str, Any]] = []

    # Canonical tags first, then aliases — one probe each, never invent.
    to_probe: list[tuple[str, str]] = []  # (tag_or_alias, owner_tag)
    seen_probe: set[str] = set()
    for cand in candidates:
        tag = (cand.get("tag") or "").strip()
        if not tag:
            dead.append("missing tag in candidate")
            continue
        if tag not in seen_probe:
            seen_probe.add(tag)
            to_probe.append((tag, tag))
        for alias in cand.get("aliases") or []:
            a = (alias or "").strip()
            if not a:
                continue
            if a not in seen_probe:
                seen_probe.add(a)
                to_probe.append((a, tag))

    print(f"probing {len(to_probe)} otag: name(s)…")
    counts: dict[str, int] = {}
    for i, (name, owner) in enumerate(to_probe, 1):
        status, n = await probe(name)
        if status == "ok" and n and n >= 1:
            counts[name] = n
        elif status == "unknown":
            unknown.append(name)
        else:
            dead.append(f"{status}: otag:{name} (for {owner})")
        if i % 20 == 0 or i == len(to_probe):
            print(f"  probed {i}/{len(to_probe)} …", flush=True)

    if unknown:
        print(
            f"error: {len(unknown)} otag: name(s) unreachable — not grading as "
            f"zero; refusing to write a vocabulary we could not prove",
            file=sys.stderr,
        )
        for name in unknown[:20]:
            print(f"  unknown: otag:{name}", file=sys.stderr)
        return 2

    if dead:
        print(
            f"error: {len(dead)} otag: name(s) 404-or-zero — refusing to write",
            file=sys.stderr,
        )
        for line in dead[:40]:
            print(f"  {line}", file=sys.stderr)
        return 1

    for cand in candidates:
        tag = cand["tag"].strip()
        aliases = [a.strip() for a in (cand.get("aliases") or []) if a and a.strip()]
        # Synonyms always include the tag + aliases so lookup is symmetric.
        syn_raw = list(cand.get("synonyms") or [])
        for extra in [tag, *aliases]:
            if extra not in syn_raw:
                syn_raw.append(extra)
        # Dedupe synonyms by normalised form, keep first spelling.
        seen_syn: set[str] = set()
        synonyms: list[str] = []
        try:
            from deckwright import app as app_mod
            norm = app_mod.norm_term
        except Exception:
            norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
        for s in syn_raw:
            key = norm(s)
            if not key or key in seen_syn:
                continue
            seen_syn.add(key)
            synonyms.append(s)

        verified.append({
            "tag": tag,
            "cards": counts[tag],
            "aliases": aliases,
            "synonyms": synonyms,
        })

    payload = {
        "version": 1,
        "source": "verified otag: probes — CON-151; no /catalog endpoint exists",
        "notes": [
            "Behaviour vocabulary for resolve_term() — checked before type/keyword catalogs.",
            "Every tag and alias was probed live; build fails on HTTP 404 or zero cards.",
            "aliases share a card pool with the canonical tag (boardwipe/sweeper, flicker/blink).",
            "synonyms are player words (steal → theft); dead guesses like stax are absent.",
        ],
        "tags": sorted(verified, key=lambda t: t["tag"]),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(f"wrote {out_path} ({len(verified)} tags)")
    return 0


def main() -> int:
    return asyncio.run(build())


if __name__ == "__main__":
    raise SystemExit(main())
