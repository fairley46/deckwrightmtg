"""
playstyles.py — community archetypes as compositions of oracle_signals.

lands the vocabulary in data/playstyles.json. This module loads it and
matches a commander's synergy.demand_profile() against each entry's detect
rules. No model. No network. No invented card names.

A playstyle with empty requires cannot match — that is the missing_signals
case, not a free pass. An unknown demand profile matches nothing.

map_way_playstyle() turns her scene words into one of the 13 ids, or
null when we cannot map. Theft/bounce/etc. stay honestly unmapped — an otag
is not a playstyle. Never invent a 14th id.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from deckwright import synergy

REPO = pathlib.Path(__file__).resolve().parents[1]
PLAYSTYLES_PATH = REPO / "data" / "playstyles.json"

_VOCAB: dict[str, dict] | None = None

# Verified otag tags → playstyle when the tag *is* that way.
# Absent tags (theft, bounce, ramp, …) stay unmapped — not a freestyle guess.
_OTAG_TO_PLAYSTYLE: dict[str, str] = {
    "lifegain": "lifegain",
    "lifedrain": "lifegain",
    "blink": "blink",
    "sac-outlet": "aristocrats",
    "reanimate": "reanimator",
}

# Phrases she says → playstyle. Includes the beginner alias pack dead behaviours that still
# name a detectable way (go wide → tokens) and direct id/community labels.
# Longer keys win. Never invent an id outside playstyles.json.
_PHRASE_TO_PLAYSTYLE: dict[str, str] = {
    "go wide": "tokens",
    "many creatures": "tokens",
    "make a bunch of stuff": "tokens",
    "token swarm": "tokens",
    "tokens": "tokens",
    "populate": "tokens",
    "help them grow": "counters",
    "plus one plus one counters": "counters",
    "+1/+1 counters": "counters",
    "counters matter": "counters",
    "proliferate": "counters",
    "counters": "counters",
    "grow": "counters",
    "commander damage": "voltron",
    "suit up": "voltron",
    "equipment": "voltron",
    "voltron": "voltron",
    "auras": "voltron",
    "spellslinger": "spellslinger",
    "cast spells": "spellslinger",
    "landfall": "landfall",
    "lands matter": "landfall",
    "reanimator": "reanimator",
    "from the graveyard": "reanimator",
    "aristocrats": "aristocrats",
    "sacrifice outlet": "aristocrats",
    "sacrifice": "aristocrats",
    "sac outlet": "aristocrats",
    "flicker": "blink",
    "blink": "blink",
    "group hug": "group_hug",
    "help everyone": "group_hug",
    "stax": "stax",
    "slow them down": "stax",
    "artifacts": "artifacts",
    "treasure": "artifacts",
    "treasures": "artifacts",
    "tribal": "tribal",
    "creature type matters": "tribal",
    "gain life": "lifegain",
    "gaining life": "lifegain",
    "keep me alive": "lifegain",
    "stay alive": "lifegain",
    "keep alive": "lifegain",
    "lifegain": "lifegain",
    "life gain": "lifegain",
    "drain": "lifegain",
    "lifedrain": "lifegain",
    "make them lose life": "lifegain",
}


def load_playstyles(path: pathlib.Path | None = None) -> dict[str, dict]:
    """id -> playstyle entry. Keys are the machine ids (aristocrats, blink, …)."""
    global _VOCAB
    if _VOCAB is None or path is not None:
        payload = json.loads((path or PLAYSTYLES_PATH).read_text())
        styles = payload.get("playstyles") or {}
        if path is None:
            _VOCAB = styles
        return styles
    return _VOCAB


def match_signals(
    signals: set[str] | list[str],
    path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Match a signal set against playstyle detect rules (no oracle read).

    Used offline against KB-stored signals after mechanics-refresh, and in
    tests that count empty_ways_signals_no_playstyle without Scryfall.
    """
    sigs = set(signals)
    matched: list[str] = []
    scores: dict[str, int] = {}

    for pid, entry in load_playstyles(path).items():
        det = entry.get("detect") or {}
        requires = list(det.get("requires") or [])
        requires_any = list(det.get("requires_any") or [])
        if not requires and not requires_any:
            continue
        if requires and not all(r in sigs for r in requires):
            continue
        if requires_any and not any(r in sigs for r in requires_any):
            continue
        boosts = list(det.get("boosts") or [])
        matched.append(pid)
        scores[pid] = sum(1 for b in boosts if b in sigs)

    matched.sort(key=lambda p: (-scores.get(p, 0), p))
    return {"matched": matched, "scores": scores}


def detect(commander: dict, path: pathlib.Path | None = None) -> dict[str, Any]:
    """Which playstyles the commander's oracle signals satisfy.

    Returns:
      {
        "status": "ok" | "unknown",
        "matched": [ids…],          # requires all present
        "scores": {id: n_boosts},   # boost hits among matched
        "signals": […],
      }

    status unknown means we could not read the commander — callers must not
    treat that as "no playstyles". Matched empty with status ok means we
    looked and found nothing; that is unknown-as-answer, not a default.
    """
    demand = synergy.demand_profile(commander)
    if demand["status"] == synergy.UNKNOWN:
        return {"status": "unknown", "matched": [], "scores": {},
                "signals": []}

    signals = set(demand.get("signals") or [])
    matched: list[str] = []
    scores: dict[str, int] = {}

    for pid, entry in load_playstyles(path).items():
        det = entry.get("detect") or {}
        requires = list(det.get("requires") or [])
        # a mechanic often has more than one shape, and `requires`
        # as a pure AND could not express that. Atraxa is the poster counters
        # commander and only proliferates, so requiring counter_placement
        # missed her entirely. requires_any lists alternatives — any one
        # satisfies it — and is ANDed with requires when both are present.
        requires_any = list(det.get("requires_any") or [])
        # Neither = not detectable with current vocabulary. Never
        # default-match; a fallback is not a match.
        if not requires and not requires_any:
            continue
        if requires and not all(r in signals for r in requires):
            continue
        if requires_any and not any(r in signals for r in requires_any):
            continue
        boosts = list(det.get("boosts") or [])
        matched.append(pid)
        scores[pid] = sum(1 for b in boosts if b in signals)

    # Stable order: more boost hits first, then name. Ranking is for display;
    # hit/miss/extra scoring treats matched as an unordered set.
    matched.sort(key=lambda p: (-scores.get(p, 0), p))
    return {
        "status": "ok",
        "matched": matched,
        "scores": scores,
        "signals": sorted(signals),
    }


def _norm_phrase(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def map_way_playstyle(way_preference: str | None,
                      path: pathlib.Path | None = None) -> str | None:
    """Her scene words → one detectable playstyle id, or None.

    Deterministic. Uses phrase aliases, then the beginner alias pack otag resolution, then
    exact playstyle id. Never invents an id outside playstyles.json.
    otag:theft and friends with no playstyle mapping stay None — unknown is
    not a guess.
    """
    raw = _norm_phrase(way_preference or "")
    if not raw:
        return None
    vocab = load_playstyles(path)
    if raw in vocab:
        return raw

    # Longest phrase first so "sacrifice outlet" beats "sacrifice".
    phrases = sorted(_PHRASE_TO_PLAYSTYLE.keys(), key=len, reverse=True)
    for phrase in phrases:
        if phrase == raw or phrase in raw:
            pid = _PHRASE_TO_PLAYSTYLE[phrase]
            return pid if pid in vocab else None

    # Behaviour resolve — only when the whole preference is one resolvable term.
    try:
        from deckwright import resolve
        resolve.load_grammar()
        hit = resolve.resolve_term(raw)
    except Exception:
        hit = None
    if isinstance(hit, dict) and hit.get("operator") == "otag":
        tag = (hit.get("value") or "").strip().lower()
        pid = _OTAG_TO_PLAYSTYLE.get(tag)
        if pid and pid in vocab:
            return pid
        return None  # resolved tag, no playstyle — honest null

    return None
