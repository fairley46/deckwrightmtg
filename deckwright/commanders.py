"""
commanders.py — CON-190 commander knowledge base.

Lookup over data/commanders.json. A missing name is ABSENT (not researched).
An entry with ways_to_play: [] means we looked and found none. Those are
different — the house rule, applied to the store itself.

Playstyles come from data/playstyles.json only. Never invent a playstyle id.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
KB_PATH = REPO / "data" / "commanders.json"

_KB: dict[str, Any] | None = None

ALLOWED_METHODS = frozenset({"both", "community", "derived"})
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low"})

# CON-196: EDHREC tag_counts → playstyle allowlist only.
# Creature-type tags (Elves, Zombies) are not playstyle ids — do not invent
# tribal from type names. Synonyms (equipment→voltron, sacrifice→aristocrats)
# are community labels for the same allowlisted playstyle.
EDHREC_TAG_TO_PLAYSTYLE: dict[str, str] = {
    "aristocrats": "aristocrats",
    "sacrifice": "aristocrats",
    "blink": "blink",
    "+1/+1 counters": "counters",
    "counters matter": "counters",
    "modular": "counters",
    "proliferate": "counters",
    "voltron": "voltron",
    "equipment": "voltron",
    "auras": "voltron",
    "spellslinger": "spellslinger",
    "landfall": "landfall",
    "tokens": "tokens",
    "populate": "tokens",
    "amass": "tokens",
    "reanimator": "reanimator",
    "self-mill": "reanimator",
    "stax": "stax",
    "group hug": "group_hug",
    "lifegain": "lifegain",
    "lifedrain": "lifegain",
    "artifacts": "artifacts",
    # Treasure is an artifact token — not the creature-tokens playstyle (CON-213).
    "treasure": "artifacts",
    "treasures": "artifacts",
    "tribal": "tribal",
}
# Below this deck-count, a tag is noise on a thin page — not a playstyle claim.
# Floor restored to 5 (CON-239): Akroma Voltron=4 / Arcades Auras=3 drop again —
# Brad authorized the restore over keeping those thin tags.
EDHREC_TAG_MIN_COUNT = 5
WAYS_MAX = 5


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def edhrec_slug(name: str, *, face: str = "front") -> str:
    """EDHREC commander page slug.

    Accents fold via NFKD (Adéwalé → adewale). Apostrophes are stripped, not
    turned into hyphens — O'Maul → omaul, Wind's → winds. Wrong hyphenation
    403s on EDHREC. DFCs: face='back' uses the name after //.
    """
    s = (name or "").lower()
    if " // " in s:
        front, back = s.split(" // ", 1)
        s = back if face == "back" else front
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def playstyles_from_edhrec_tags(
    taglinks: list[dict] | None,
    *,
    vocab: dict[str, dict] | None = None,
    min_count: int = EDHREC_TAG_MIN_COUNT,
) -> list[str]:
    """Map EDHREC taglinks/tag_counts onto the playstyle allowlist.

    Unknown tags are dropped. Counts below min_count are dropped. Order is
    strongest count first. Never invents a playstyle id outside playstyles.json.
    """
    allowed = set(vocab) if vocab is not None else set(EDHREC_TAG_TO_PLAYSTYLE.values())
    scored: list[tuple[int, str]] = []
    for row in taglinks or []:
        raw = (row.get("value") or row.get("slug") or "").strip()
        if not raw:
            continue
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count < min_count:
            continue
        pid = EDHREC_TAG_TO_PLAYSTYLE.get(raw.lower())
        if pid is None or pid not in allowed:
            continue
        scored.append((count, pid))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out: list[str] = []
    seen: set[str] = set()
    for _, pid in scored:
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def derived_playstyles(entry: dict) -> list[str]:
    """Playstyle ids already credited to mechanics (derived or both)."""
    out: list[str] = []
    seen: set[str] = set()
    for w in entry.get("ways_to_play") or []:
        if w.get("method") not in ("derived", "both"):
            continue
        pid = w.get("playstyle")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def has_community_ways(entry: dict) -> bool:
    return any(
        w.get("method") in ("community", "both")
        for w in (entry.get("ways_to_play") or [])
    )


def research_targets(
    entries: dict[str, dict],
    *,
    include_empty: bool,
    include_derived: bool,
    limit: int = 0,
) -> list[str]:
    """Names to research. Empties first, then derived-only (no community yet)."""
    names: list[str] = []
    seen: set[str] = set()
    if include_empty:
        for name, e in entries.items():
            if e.get("ways_to_play"):
                continue
            names.append(name)
            seen.add(name)
    if include_derived:
        for name, e in entries.items():
            if name in seen:
                continue
            if not (e.get("ways_to_play") or []):
                continue
            if has_community_ways(e):
                continue
            names.append(name)
            seen.add(name)
    if limit and limit > 0:
        names = names[:limit]
    return names


def apply_edhrec_community(
    entry: dict,
    *,
    community: list[str],
    community_source: str,
    community_why: str,
    researched_at: str,
    vocab: dict[str, dict],
) -> dict:
    """Merge EDHREC community labels onto an existing KB entry (CON-196 B grow).

    Reuses derived playstyles already on the entry — KB rows do not store
    oracle text. Never invents playstyle ids outside vocab.
    """
    derived = derived_playstyles(entry)
    community_ok = [p for p in community if p in vocab]
    if not community_ok and not derived:
        out = dict(entry)
        out["ways_to_play"] = []
        out["method"] = "derived"
        out["researched_at"] = researched_at
        out["research_note"] = (
            "EDHREC fetched; no allowlisted playstyle tags "
            f"(min count {EDHREC_TAG_MIN_COUNT})."
        )
        return out
    ways = merge_ways(
        community=community_ok,
        derived=derived,
        scores={},
        vocab=vocab,
        community_source=community_source,
        community_why=community_why,
    )
    out = dict(entry)
    out["ways_to_play"] = ways
    out["method"] = "both" if community_ok else "derived"
    out["researched_at"] = researched_at
    if community_ok:
        out.pop("research_note", None)
    else:
        out["research_note"] = (
            "EDHREC fetched; no allowlisted playstyle tags "
            f"(min count {EDHREC_TAG_MIN_COUNT})."
        )
    return out


def load(path: pathlib.Path | None = None) -> dict[str, Any]:
    """Full store payload. Cached for the default path."""
    global _KB
    p = path or KB_PATH
    if path is None and _KB is not None:
        return _KB
    if not p.is_file():
        payload = {
            "version": 1,
            "note": "empty — run scripts/build_commanders_kb.py",
            "legal_estimate": 3321,
            "mechanics": 0,
            "researched": 0,
            "commanders": {},
        }
        if path is None:
            _KB = payload
        return payload
    payload = json.loads(p.read_text())
    if path is None:
        _KB = payload
    return payload


def clear_cache() -> None:
    global _KB
    _KB = None


def lookup(name: str, store: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return the KB entry, or None when the commander has not been researched.

    None is ABSENT. An entry with ways_to_play=[] is researched and empty.
    """
    payload = store if store is not None else load()
    entries = payload.get("commanders") or {}
    if name in entries:
        return entries[name]
    # Soft match on squashed name — Scryfall punctuation variants.
    target = squash(name)
    for key, entry in entries.items():
        if squash(key) == target:
            return entry
    return None


def coverage(store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mechanics, ways-to-play, and research — three fractions, never one.

    CON-194: mechanics is free (oracle → demand_profile) and must be 100%.
    CON-196: ways_to_play is the articulated middle — mechanics ran and at
    least one playstyle matched. Research is expensive and may be partial.
    Reporting only `researched` hid a complete free half behind a tiny
    expensive one; reporting only mechanics hid the empty-ways gap.
    """
    payload = store if store is not None else load()
    entries = payload.get("commanders") or {}
    estimate = int(payload.get("legal_estimate") or len(entries) or 3321)
    if "mechanics" in payload:
        mechanics = int(payload["mechanics"])
    else:
        mechanics = sum(
            1 for e in entries.values()
            if (e or {}).get("mechanics_status") == "ok"
        )
    if "ways_to_play" in payload and not isinstance(payload["ways_to_play"], list):
        ways = int(payload["ways_to_play"])
    else:
        ways = sum(
            1 for e in entries.values()
            if (e or {}).get("ways_to_play")
        )
    researched = int(payload.get("researched") or 0)
    mech_pct = (100.0 * mechanics / estimate) if estimate else 0.0
    ways_pct = (100.0 * ways / estimate) if estimate else 0.0
    res_pct = (100.0 * researched / estimate) if estimate else 0.0
    return {
        "mechanics": mechanics,
        "ways_to_play": ways,
        "researched": researched,
        "legal_estimate": estimate,
        "mechanics_fraction": (mechanics / estimate) if estimate else None,
        "ways_to_play_fraction": (ways / estimate) if estimate else None,
        "research_fraction": (researched / estimate) if estimate else None,
        # Backward-compatible single label — research half only. Prefer the
        # three explicit labels below.
        "fraction": (researched / estimate) if estimate else None,
        "label": f"{researched} of {estimate}",
        "mechanics_label": f"{mechanics} of {estimate}",
        "ways_to_play_label": f"{ways} of {estimate} ({ways_pct:.1f}%)",
        "research_label": f"{researched} of {estimate} ({res_pct:.1f}%)",
        "mechanics_pct": mech_pct,
        "ways_to_play_pct": ways_pct,
        "research_pct": res_pct,
    }


def disagreement_rate(store: dict[str, Any] | None = None) -> dict[str, int]:
    """How often research and mechanics disagree — measured, not resolved."""
    payload = store if store is not None else load()
    both = community_only = derived_only = 0
    with_ways = 0
    for entry in (payload.get("commanders") or {}).values():
        ways = entry.get("ways_to_play") or []
        if not ways:
            continue
        with_ways += 1
        for w in ways:
            m = w.get("method")
            if m == "both":
                both += 1
            elif m == "community":
                community_only += 1
            elif m == "derived":
                derived_only += 1
    return {
        "both": both,
        "community_only": community_only,
        "derived_only": derived_only,
        "commanders_with_ways": with_ways,
    }


def merge_ways(
    *,
    community: list[str],
    derived: list[str],
    scores: dict[str, int],
    vocab: dict[str, dict],
    community_source: str,
    community_why: str,
    ways_max: int = WAYS_MAX,
) -> list[dict[str, Any]]:
    """Union of community + derived playstyles. Disagreements stay labelled.

    Playstyle ids not in vocab are dropped — never invented.
    Capped at ways_max: all both+derived kept first, then top community by
    EDHREC count (community list order).
    """
    community_set = {p for p in community if p in vocab}
    derived_set = {p for p in derived if p in vocab}
    comm_rank = {p: i for i, p in enumerate(community)}
    # Prefer both, then community, then derived; within a method, higher
    # boost score then name. Ranking is display-only.
    ordered = sorted(
        community_set | derived_set,
        key=lambda p: (
            0 if p in community_set and p in derived_set
            else 1 if p in community_set else 2,
            -int(scores.get(p, 0)),
            p,
        ),
    )
    ways: list[dict[str, Any]] = []
    for pid in ordered:
        entry = vocab[pid]
        in_c = pid in community_set
        in_d = pid in derived_set
        says = entry.get("says") or pid
        if in_c and in_d:
            ways.append({
                "playstyle": pid,
                "confidence": "high",
                "method": "both",
                "why": (community_why or "").strip() or says,
                "source": community_source or "community + mechanics",
            })
        elif in_c:
            ways.append({
                "playstyle": pid,
                "confidence": "medium",
                "method": "community",
                "why": (community_why or "").strip() or says,
                "source": community_source or "community",
                "note": "Community plays this; mechanics did not detect it.",
            })
        else:
            conf = "medium" if int(scores.get(pid, 0)) > 0 else "low"
            ways.append({
                "playstyle": pid,
                "confidence": conf,
                "method": "derived",
                "why": says,
                "source": "synergy.demand_profile / playstyles.detect",
                "note": ("Mechanically available; no community source "
                         "builds this way."),
            })
    return _cap_ways(ways, community, comm_rank, scores, ways_max)


def _cap_ways(
    ways: list[dict[str, Any]],
    community: list[str],
    comm_rank: dict[str, int],
    scores: dict[str, int],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep both+derived first, then highest-count community rows. Max `limit`."""
    if limit <= 0 or len(ways) <= limit:
        return ways

    def _tier(method: str) -> int:
        if method == "both":
            return 0
        if method == "derived":
            return 1
        return 2

    ranked = sorted(
        ways,
        key=lambda w: (
            _tier(w.get("method") or ""),
            comm_rank.get(w.get("playstyle") or "", 999),
            -int(scores.get(w.get("playstyle") or "", 0)),
            w.get("playstyle") or "",
        ),
    )
    kept = ranked[:limit]
    # Display: both, then derived, then community (cap keeps mechanics first).
    kept.sort(
        key=lambda w: (
            0 if w.get("method") == "both"
            else 1 if w.get("method") == "derived" else 2,
            comm_rank.get(w.get("playstyle") or "", 999),
            -int(scores.get(w.get("playstyle") or "", 0)),
            w.get("playstyle") or "",
        ),
    )
    return kept


# CON-229 / GA-PATH: junk from type-line tokenization that pollutes top-theme
# lists. Human is a real creature type, but at KB scale it drowns tribal signal.
_TYPE_LINE_THEME_DROP = frozenset({
    "legendary", "creature", "artifact", "enchantment", "land",
    "planeswalker", "battle", "tribal", "kindred", "token", "basic", "snow",
    "world",
    "human",  # GA path — drop from KB themes, not from deck briefs (CON-229)
})
_PUNCT_THEME_FRAGMENTS = frozenset({"//", "/", "—", "-"})


def filter_theme_tokens(tokens: list[str]) -> list[str]:
    """Drop type-line pollution tokens (CON-229)."""
    out: list[str] = []
    for raw in tokens:
        t = (raw or "").strip().lower()
        if not t or t in _TYPE_LINE_THEME_DROP or t in _PUNCT_THEME_FRAGMENTS:
            continue
        out.append(t)
    return out


def themes_from_type_line(type_line: str) -> list[str]:
    """Creature types after the em-dash — never invented playstyle names."""
    if not type_line:
        return []
    # "Legendary Creature — Bear Spirit" → ["bear", "spirit"]
    if "—" in type_line:
        right = type_line.split("—", 1)[1]
    elif " - " in type_line:
        right = type_line.split(" - ", 1)[1]
    else:
        return []
    raw = [t.strip().lower() for t in right.split() if t.strip()]
    return filter_theme_tokens(raw)


# ── CON-191: theme fit + strategy fit + relational copy ──────────────────


# Connectors / filler in conversational themes ("pirates that steal",
# "horses and knights"). Same defect class as strategy #157 — the whole
# phrase must not be the only term (CON-207).
_THEME_STOPWORDS = frozenset({
    "a", "an", "and", "for", "in", "my", "of", "on", "or", "our", "team",
    "that", "the", "to", "which", "who", "with",
})


def _brief_theme_terms(brief: dict | None) -> list[str]:
    """Words she used for what the deck is about — tokenized.

    Keeps the full phrase (exact match still counts) and emits word tokens
    so "pirates that steal" hits a Pirate type line. Light singularisation
    per token (clerics→cleric, bears→bear). Stopwords stripped.
    """
    theme = (brief or {}).get("theme")
    if not isinstance(theme, str) or not theme.strip():
        return []
    raw = theme.strip().lower()
    terms = [raw]
    for tok in re.split(r"[^a-z0-9+]+", raw):
        if not tok or tok in _THEME_STOPWORDS:
            continue
        # dog/cat are real types; shorter crumbs are noise.
        if len(tok) < 3:
            continue
        terms.append(tok)
        if tok.endswith("s") and len(tok) > 3:
            terms.append(tok[:-1])
    # De-dupe, keep order.
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# Phrase-valued brief keys get tokenized. Stopwords, short fragments, and
# nouns that show up in most playstyle `says` lines must not score a hit.
_STRATEGY_STOPWORDS = frozenset({
    "a", "an", "and", "as", "at", "be", "by", "do", "down", "for", "from", "get",
    "have", "her", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "just", "like", "me", "my", "of", "on", "or", "our", "out", "own", "so",
    "than", "that", "the", "their", "them", "then", "they", "this", "to",
    "too", "up", "want", "wants", "we", "with", "you", "your",
    # Too common across playstyle says — noise, not a strategy signal.
    "abilities", "battlefield", "card", "cards", "creature", "creatures",
    "deck", "every", "permanent", "permanents", "pile", "play", "table",
    "turn", "turns", "win", "wins",
    # CON-223 / Gate 1 BUG-2+6: fillers that live inside playstyle `says`
    # ("machines that feed each other", "build around…") and were printed
    # as her strategy label / false-matched artifacts.
    "each", "other", "around", "build", "feed", "feeds", "also", "into",
    "with", "from", "that", "this", "your", "their",
})


def _brief_strategy_terms(brief: dict | None) -> list[str]:
    """Words she used for how she wants to play — never invented.

    `angle` / `strategy` are sentences. Keep the full phrase (exact match
    still counts) and emit word tokens so "sacrifice creatures for value,
    grind them down" can hit aristocrats.says. `mechanics` entries stay
    whole — those are already atomic.
    """
    brief = brief or {}
    out: list[str] = []
    for key in ("playstyle", "strategy", "angle"):
        v = brief.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        phrase = v.strip().lower()
        out.append(phrase)
        for raw in re.split(r"[^a-z0-9+]+", phrase):
            if not raw:
                continue
            if raw in _STRATEGY_STOPWORDS:
                continue
            if len(raw) < 4:
                continue
            out.append(raw)
    for m in brief.get("mechanics") or []:
        if isinstance(m, str) and m.strip():
            out.append(m.strip().lower())
    # De-dupe, keep order.
    seen, ordered = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _commander_haystack(card: dict) -> str:
    return " ".join([
        card.get("name") or "",
        card.get("type") or card.get("type_line") or "",
        card.get("text") or card.get("oracle_text") or "",
        " ".join(card.get("keywords") or []),
    ]).lower()


def theme_fit(card: dict, brief: dict | None) -> dict[str, Any]:
    """How well the commander matches what the deck is *about*.

    score is a count of matching theme terms — a COUNT, not a coefficient.
    status empty means she gave no theme; that is not a zero match.
    """
    terms = _brief_theme_terms(brief)
    if not terms:
        return {"status": "empty", "score": None, "matched": []}
    hay = _commander_haystack(card)
    matched = [t for t in terms if t in hay]
    return {"status": "ok", "score": len(matched), "matched": matched}


def _ways_for_card(card: dict) -> tuple[str, list[dict[str, Any]]]:
    """(status, ways). status unknown when we could not look."""
    from deckwright import playstyles  # local — avoids import cycle at module load

    name = card.get("name") or ""
    entry = lookup(name) if name else None
    if entry is not None:
        ways = list(entry.get("ways_to_play") or [])
        return "ok", ways

    detected = playstyles.detect(card)
    if detected.get("status") == "unknown":
        return "unknown", []
    vocab = playstyles.load_playstyles()
    ways = []
    for pid in detected.get("matched") or []:
        if pid not in vocab:
            continue
        ways.append({
            "playstyle": pid,
            "confidence": "medium",
            "method": "derived",
            "why": vocab[pid].get("says") or pid,
            "source": "synergy.demand_profile / playstyles.detect",
            "note": "Not in the knowledge base — live detection only.",
        })
    return "ok", ways


def strategy_fit(card: dict, brief: dict | None) -> dict[str, Any]:
    """How well the commander supports how she wants to *play*.

    Matched playstyles are those whose id/name/detect keys overlap her
    strategy words. score is a count. status empty = no strategy ask; unknown
    = could not read the commander. An unknown is never a zero.

    CON-223 / Gate 1 BUG-6: do **not** match against `says` prose — marketing
    copy like "machines that feed each other" false-hit on filler words and
    seated every cavalry commander as artifacts.

    CON-257: a mapped `way_playstyle` on the brief is a hard hit when the
    commander detects that way — stated preference, not phrase guesswork.
    """
    from deckwright import playstyles

    brief = brief or {}
    terms = _brief_strategy_terms(brief)
    want_way = brief.get("way_playstyle")
    if isinstance(want_way, str):
        want_way = want_way.strip() or None
    else:
        want_way = None
    if not terms and not want_way:
        return {"status": "empty", "score": None, "matched": [], "ways": []}

    status, ways = _ways_for_card(card)
    if status == "unknown":
        return {"status": "unknown", "score": None, "matched": [], "ways": []}

    vocab = playstyles.load_playstyles()
    matched: list[str] = []
    way_ids = []
    for w in ways:
        pid = w.get("playstyle") or ""
        if pid:
            way_ids.append(pid)
        entry = vocab.get(pid) or {}
        detect = entry.get("detect") or {}
        blob = " ".join([
            pid,
            entry.get("name") or "",
            " ".join(detect.get("requires") or []),
            " ".join(detect.get("requires_any") or []),
            " ".join(detect.get("wants") or []),
        ]).lower()
        if terms and any(t in blob or pid in t or t in pid for t in terms):
            matched.append(pid)
    # De-dupe, keep way order.
    seen, ordered = set(), []
    for p in matched:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    # Stated way preference seats first when the commander has that way.
    if want_way and want_way in way_ids:
        ordered = [want_way] + [p for p in ordered if p != want_way]
    return {
        "status": "ok",
        "score": len(ordered),
        "matched": ordered,
        "ways": ways,
    }


def _her_strategy_label(
    brief: dict,
    strategy_terms: list[str],
    lead: str,
    says: str,
) -> str:
    """Word or phrase she used that names the matched way — not the machine id."""
    playstyle = (brief.get("playstyle") or "").strip().lower()
    if playstyle and playstyle == (lead or "").lower():
        return playstyle

    says_l = (says or "").lower()
    lead_l = (lead or "").lower()
    word_terms = [
        t for t in strategy_terms
        if " " not in t and t not in _STRATEGY_STOPWORDS
    ]
    # Tokens she used that echo in the matched way's player-language line.
    for t in word_terms:
        if t in says_l:
            return t
    for t in word_terms:
        if t == lead_l or lead_l.startswith(t):
            return t
    if word_terms:
        return word_terms[0]
    return lead


def fit_rank_key(theme: dict, strategy: dict) -> tuple:
    """Lexicographic gates for ranking — never a weighted sum.

    Order: both-axes hit → theme count → strategy count (None sorts last).
    """
    t = theme.get("score")
    s = strategy.get("score")
    t_n = int(t) if isinstance(t, int) else 0
    s_known = isinstance(s, int)
    s_n = int(s) if s_known else None
    both = 1 if t_n > 0 and s_known and s_n > 0 else 0
    # None sorts after numbers when used as a key component via a flag.
    return (both, t_n, 1 if s_known else 0, s_n if s_known else -1)


def relational_copy(
    card: dict,
    brief: dict | None,
    theme: dict | None = None,
    strategy: dict | None = None,
) -> str:
    """Copy that restates her ask and names sourced ways — no deck card names.

    Two halves: we heard you, and here is what exists. When only one axis
    hits, say so rather than hiding it.
    """
    from deckwright import playstyles

    brief = brief or {}
    theme = theme if theme is not None else theme_fit(card, brief)
    strategy = strategy if strategy is not None else strategy_fit(card, brief)
    vocab = playstyles.load_playstyles()

    theme_term = (_brief_theme_terms(brief) or [""])[0]
    strategy_terms = _brief_strategy_terms(brief)
    her_strategy = strategy_terms[0] if strategy_terms else ""

    ways = list(strategy.get("ways") or [])
    if not ways:
        _, ways = _ways_for_card(card)

    matched = list(strategy.get("matched") or [])
    t_score = theme.get("score")
    s_score = strategy.get("score")
    s_status = strategy.get("status")

    parts: list[str] = []

    if s_status == "unknown":
        if isinstance(t_score, int) and t_score > 0 and theme_term:
            parts.append(
                f"Matches your {theme_term}, but I could not read how this "
                f"commander wants to be played."
            )
        else:
            parts.append(
                "I could not read how this commander wants to be played."
            )
        return " ".join(parts)

    if isinstance(s_score, int) and s_score > 0 and matched:
        lead = matched[0]
        says = (vocab.get(lead) or {}).get("says") or lead
        # CON-257: quote the scene she picked when present.
        way_pref = (brief.get("way_preference") or "").strip()
        her = way_pref or _her_strategy_label(brief, strategy_terms, lead, says)
        if isinstance(t_score, int) and t_score > 0 and theme_term:
            opener = f"For your {theme_term}, fits your {her} strategy"
        else:
            opener = f"Fits your {her} strategy"
        parts.append(f"{opener}: {says}")
        others = [p for p in (w.get("playstyle") for w in ways) if p and p != lead]
        # Cap at two alternates — CON-179 will surface the full switcher.
        alts = []
        for pid in others[:2]:
            alt_says = (vocab.get(pid) or {}).get("says") or pid
            alts.append(f"also playable as {pid} ({alt_says.rstrip('.')})")
        if alts:
            # Capitalise the first "also" so the sentence reads cleanly.
            joined = "; ".join(alts)
            parts.append(joined[:1].upper() + joined[1:] + ".")
        if isinstance(t_score, int) and t_score == 0 and theme_term:
            parts.append(
                f"Theme note: I could not find “{theme_term}” on the card itself."
            )
        return " ".join(parts)

    # Strategy asked, no match — label the one-axis case honestly.
    if her_strategy and s_status == "ok":
        if isinstance(t_score, int) and t_score > 0 and theme_term:
            parts.append(
                f"Matches your {theme_term}, but I can't tell how it wants to "
                f"be played for “{her_strategy}”."
            )
        else:
            parts.append(
                f"I can't tell how this commander wants to be played for "
                f"“{her_strategy}”."
            )
        if ways:
            shown = ", ".join(
                (w.get("playstyle") or "") for w in ways[:3] if w.get("playstyle")
            )
            if shown:
                parts.append(f"Known ways: {shown}.")
        return " ".join(parts)

    # No strategy ask — speak theme + available ways if any.
    if isinstance(t_score, int) and t_score > 0 and theme_term:
        parts.append(f"Matches your {theme_term}.")
    if ways:
        shown = []
        for w in ways[:3]:
            pid = w.get("playstyle")
            if not pid:
                continue
            says = (vocab.get(pid) or {}).get("says") or pid
            shown.append(f"{pid} — {says}")
        if shown:
            parts.append("Ways to play: " + " · ".join(shown) + ".")
    if not parts:
        parts.append("No theme or strategy fit I can show yet.")
    return " ".join(parts)


# Cap how many mechanical signals the picker shows. Enough to tell two angels
# apart; not a dump of the whole demand profile.
_PICKER_SIGNAL_CAP = 4
_PICKER_THEME_CAP = 4


def picker_differs(
    card: dict,
    theme: dict | None = None,
    strategy: dict | None = None,
    ways: list | None = None,
) -> dict[str, Any]:
    """Facts that distinguish this commander from others on the same rung.

    CON-286: derived from Scryfall + KB — type, colours, themes, signals, ways.
    Never a prose template that only swaps the name. Empty lists mean we looked
    and found none; absent KB is themes/signals empty, not invented.
    """
    theme = theme or {}
    strategy = strategy or {}
    way_rows = list(ways) if ways is not None else list(strategy.get("ways") or [])
    way_ids: list[str] = []
    seen_w: set[str] = set()
    for w in way_rows:
        pid = (w.get("playstyle") or "").strip()
        if pid and pid not in seen_w:
            seen_w.add(pid)
            way_ids.append(pid)

    type_line = (card.get("type") or card.get("type_line") or "").strip()
    colors = [c for c in (card.get("color_identity") or []) if c]

    themes: list[str] = []
    signals: list[str] = []
    entry = lookup(card.get("name") or "")
    if entry is not None:
        for t in entry.get("themes") or []:
            t = (t or "").strip().lower()
            if t and t not in themes:
                themes.append(t)
            if len(themes) >= _PICKER_THEME_CAP:
                break
        for s in entry.get("signals") or []:
            s = (s or "").strip()
            if s and s not in signals:
                signals.append(s)
            if len(signals) >= _PICKER_SIGNAL_CAP:
                break
    # Type-line subtypes fill themes when the KB has not researched this name.
    if not themes and type_line:
        for t in themes_from_type_line(type_line):
            if t not in themes:
                themes.append(t)
            if len(themes) >= _PICKER_THEME_CAP:
                break

    return {
        "type": type_line,
        "color_identity": colors,
        "themes": themes,
        "signals": signals,
        "ways": way_ids,
        "theme_matched": list(theme.get("matched") or []),
        "strategy_matched": list(strategy.get("matched") or []),
    }


def picker_differs_fingerprint(differs: dict | None) -> str:
    """Stable string for tests — two picks that share this look the same."""
    d = differs or {}
    parts = [
        d.get("type") or "",
        ",".join(d.get("color_identity") or []),
        ",".join(d.get("ways") or []),
        ",".join(d.get("themes") or []),
        ",".join(d.get("signals") or []),
    ]
    return "|".join(parts)
