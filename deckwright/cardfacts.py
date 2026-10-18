"""CON-322 — CardFacts lookup + offline extract (contestant C bake path).

Offline store of per-card clause facts for *any* card name (the 99, not only
commanders). Lookup is read on the serve path **only** when
``DECKWRIGHT_CANDIDATE=C`` (via ``serve_report`` / CF-informed steer).
Candidate B / BLUE / production defaults do not import this module.
``app.py`` and ``card_score`` stay free of CardFacts imports.

Bake path
---------
``scripts/bake_cardfacts.py`` reads the local Scryfall corpus
(``corpus.py`` / ``oracle_cards.jsonl``), runs ``extract()`` (no model, no
live Scryfall mass scrape), and writes the store ``lookup`` expects.
Default bake is resume-safe: existing names are skipped, progress logs every
N cards, and the store checkpoints periodically. ``scripts/cardfacts_status.py``
prints tallies / sample lookup.

Store path
----------
``{DECKWRIGHT_HOME}/cardfacts.json`` when set, else instance home /
``~/.deckwright/cardfacts.json``.

Schema (version 1)
------------------
Aligned with ``commander_strategy`` shell fields (inputs / outputs / bounds /
rate) plus status honesty for Layer 1. Keyed by exact card name::

    {
      "version": 1,
      "cards": {
        "Sol Ring": {
          "name": "Sol Ring",
          "status": "partial",
          "inputs": [],
          "outputs": [{
            "kind": "mana",
            "magnitude": {"mode": "static", "value": 2},
            "once_each_turn": false
          }],
          "bounds": [],
          "rate": {"supplier": "self", "growth_2p_to_4p": 1},
          "unscoreable": ["soft_combo", "hidden_power"],
          "free_value": true,
          "provenance": {"tier": 2, "source": "extract"}
        }
      }
    }

``status`` is ``ok`` (inputs and outputs), ``partial`` (one side or bounds
only), or ``unread`` (could not read clause facts). ``honesty`` appears on
unread / partial when useful. Soft combo / hidden power stay listed under
``unscoreable`` at weight 0 — surfaced, not silently dropped.

Bounds use ``op: geq|leq|in_set`` (CON-331 floor/ceiling). Static MV floors
(``N or greater`` / ``greater than or equal to N``) and ceilings
(``N or less`` / ``less than or equal to N``) are first-class; dynamic
life/experience tails are not coerced into static numbers.

Optional Layer-1 rollups from the v2 design (``scaling_class``, ``explosive``,
``free_value``, ``axes``, ``resilience``, ``event_magnitudes``, ``provenance``)
may appear; extract may set ``free_value`` / ``provenance`` when confident.

House rule
----------
Missing file, unreadable JSON, or a card name not in the map → unknown
(``None`` / ``store_status() == "unknown"``). Never invent an empty facts
dict and treat it as a measured zero. A baked ``status: unread`` entry is a
*known* unread (honest), not a missing lookup.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_STORE: dict[str, dict[str, Any]] | None = None
_LOADED = False
_MISS = False

_SCHEMA_KEYS = (
    "name",
    "status",
    "inputs",
    "outputs",
    "bounds",
    "rate",
    "honesty",
    "unscoreable",
)

_UNSCOREABLE = ("soft_combo", "hidden_power")

_HONESTY_UNREAD = "I could not read clause facts from this card."
_HONESTY_PARTIAL_OUTPUTS_ONLY = "I read what it produces, not what it needs."
_HONESTY_PARTIAL_INPUTS_ONLY = "I read what it needs, not what it produces."
_HONESTY_KEYWORDS_ONLY = (
    "I read keywords / static ability words only — no trigger or activated clause."
)

# Keyword → coarse output kind (honest: the word is on the card).
_KEYWORD_SPECS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bflying\b", re.I), "keywords"),
    (re.compile(r"\btrample\b", re.I), "keywords"),
    (re.compile(r"\bhaste\b", re.I), "keywords"),
    (re.compile(r"\bvigilance\b", re.I), "keywords"),
    (re.compile(r"\bdeathtouch\b", re.I), "keywords"),
    (re.compile(r"\blifelink\b", re.I), "life"),
    (re.compile(r"\bmenace\b", re.I), "keywords"),
    (re.compile(r"\bhexproof\b", re.I), "keywords"),
    (re.compile(r"\bindestructible\b", re.I), "keywords"),
    (re.compile(r"\bdefender\b", re.I), "keywords"),
    (re.compile(r"\breach\b", re.I), "keywords"),
    (re.compile(r"\bflash\b", re.I), "keywords"),
    (re.compile(r"\bfirst strike\b", re.I), "keywords"),
    (re.compile(r"\bdouble strike\b", re.I), "keywords"),
    (re.compile(r"\bward\b", re.I), "keywords"),
    (re.compile(r"\bprotection from\b", re.I), "keywords"),
    (re.compile(r"\binfect\b", re.I), "keywords"),
    (re.compile(r"\bwither\b", re.I), "keywords"),
    (re.compile(r"\bskulk\b", re.I), "keywords"),
    (re.compile(r"\bprowess\b", re.I), "keywords"),
    (re.compile(r"\baffinity for\b", re.I), "mana"),
    (re.compile(r"\bkicker\b", re.I), "keywords"),
    (re.compile(r"\bmorph\b", re.I), "keywords"),
    (re.compile(r"\bcascade\b", re.I), "deploy_without_cast"),
    (re.compile(r"\bequip\b", re.I), "counters"),
    (re.compile(r"\benchant\b", re.I), "counters"),
]

# --- extract patterns (mirror commander_strategy CON-321/331; local copy so
# this branch does not depend on the green B stack being merged) ----------------

_TRIGGER_SPECS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(
        r"whenever (?:you|a permanent you control)[^.]{0,40}gain(?:s)? life",
        re.I), "gain_life", "self"),
    (re.compile(r"whenever you gain life", re.I), "gain_life", "self"),
    (re.compile(
        r"whenever an opponent casts? (?:a )?noncreature spell",
        re.I), "cast_noncreature", "opponent"),
    (re.compile(
        r"whenever an opponent casts? (?:a )?spell",
        re.I), "cast_spell", "opponent"),
    (re.compile(
        r"whenever (?:a|an) opponent[^.]{0,60}(?:casts?|plays?)",
        re.I), "cast_spell", "opponent"),
    (re.compile(
        r"whenever you cast (?:a )?noncreature spell",
        re.I), "cast_noncreature", "self"),
    (re.compile(
        r"whenever you cast (?:a )?spell",
        re.I), "cast_spell", "self"),
    (re.compile(
        r"whenever you cast another (\w+) spell",
        re.I), "cast_subtype", "self"),
    (re.compile(
        r"whenever a(?:n)? (\w+) you control deals combat damage",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"whenever .+ deals combat damage to (?:a )?player",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"whenever (?:a|another) creature (?:you control )?dies",
        re.I), "dies", "self"),
    (re.compile(
        r"whenever you draw (?:a |one )?card",
        re.I), "draw", "self"),
    (re.compile(
        r"at the beginning of (?:your |each )?upkeep",
        re.I), "upkeep", "self"),
    (re.compile(
        r"at the beginning of (?:your |each )?end step",
        re.I), "end_step", "self"),
    (re.compile(
        r"whenever .+ enters? (?:the battlefield|)",
        re.I), "etb_creature", "self"),
]

_OUTPUT_SPECS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"return (?:target )?.+ from (?:your )?graveyard to the battlefield",
        re.I), "recur_to_battlefield"),
    (re.compile(
        r"return .+ from (?:your )?graveyard to (?:your )?hand",
        re.I), "recur_to_hand"),
    (re.compile(r"create .+(?:token|tokens)", re.I), "tokens"),
    (re.compile(r"draw (?:a |one |two |three |\d+ )?cards?", re.I), "draw"),
    (re.compile(r"you gain (?:\d+|that much) life|you gain life equal", re.I),
     "life"),
    (re.compile(r"(?:add|creates?) .*(?:mana|{[wubrgc0-9]})", re.I), "mana"),
    (re.compile(r"put .+ \+1/\+1 counter", re.I), "counters"),
    (re.compile(r"exile .+ then (?:cast|play)", re.I), "deploy_without_cast"),
    (re.compile(r"take an extra turn", re.I), "extra_turn"),
]

_MV_LEQ_LIFE = re.compile(
    r"mana value (?:less than or equal to|≤|<=) .{0,40}?"
    r"(?:life you gained|amount of life you gained|life gained this turn)",
    re.I | re.S,
)
_MV_FLOOR = re.compile(
    r"mana value (?:(\d+) or greater|greater than or equal to (\d+))",
    re.I,
)
_MV_STATIC_CEILING = re.compile(
    r"mana value (?:(\d+) or less|less than or equal to (\d+))",
    re.I,
)
_MV_DYNAMIC_TAIL = re.compile(
    r"^\s*(?:the )?(?:amount of )?life\b|^\s*(?:the )?number of experience",
    re.I,
)
_SUBTYPE_ONLY = re.compile(r"(\w+) creatures? you control get", re.I)
_ONCE_EACH_TURN = re.compile(r"only once each turn|once each turn", re.I)
_BOARD_COUNT = re.compile(
    r"where X is the number of (\w+?)s? you control",
    re.I,
)
_MANA_SYMBOL = re.compile(r"\{[WUBRGC0-9]+\}", re.I)
_TAP_ADD_LINE = re.compile(
    r"\{T\}(?:,[^:]+)*:\s*Add\b",
    re.I,
)
_ACTIVATED_COST = re.compile(
    r"(?:\{T\}|\{[0-9WUBRGCP/]+\}(?:,\s*\{[0-9WUBRGCP/]+\})*(?:,\s*[^:\n]{0,48})?)\s*:",
    re.I,
)


def store_path() -> Path:
    """Baked CardFacts JSON under the instance home (or ~/.deckwright)."""
    override = os.environ.get("DECKWRIGHT_HOME", "").strip()
    if override:
        return Path(override).expanduser() / "cardfacts.json"
    try:
        from deckwright import instance
        return instance.home_dir() / "cardfacts.json"
    except Exception:
        return Path.home() / ".deckwright" / "cardfacts.json"


def empty_schema() -> dict[str, Any]:
    """Valid empty store — cards present but none known yet."""
    return {"version": 1, "cards": {}}


def _normalize_card(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in _SCHEMA_KEYS:
        if key in raw:
            out[key] = raw[key]
    # Preserve optional rollups / provenance without inventing defaults.
    for key, val in raw.items():
        if key not in out:
            out[key] = val
    # A card entry with none of the shell keys and no extras is still a hit
    # (known empty), not unknown — caller asked for a name that exists.
    return out


def _load() -> dict[str, dict[str, Any]] | None:
    global _STORE, _LOADED, _MISS
    if _LOADED:
        return _STORE
    _LOADED = True
    path = store_path()
    if not path.is_file():
        _MISS = True
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        _MISS = True
        return None
    cards = raw.get("cards") if isinstance(raw, dict) else None
    if not isinstance(cards, dict):
        _MISS = True
        return None
    out: dict[str, dict[str, Any]] = {}
    for name, facts in cards.items():
        if not isinstance(name, str):
            continue
        norm = _normalize_card(facts)
        if norm is not None:
            out[name] = norm
    _STORE = out
    return _STORE


def reset_cache() -> None:
    """Test hook — clear the in-process CardFacts cache."""
    global _STORE, _LOADED, _MISS
    _STORE = None
    _LOADED = False
    _MISS = False


def store_status() -> str:
    """ok when a CardFacts file loaded; unknown when absent or unreadable."""
    _load()
    return "unknown" if _MISS or _STORE is None else "ok"


def lookup(name: str) -> dict[str, Any] | None:
    """Facts for one card name. None = unknown (not an empty dict)."""
    if not (name or "").strip():
        return None
    store = _load()
    if store is None:
        return None
    if name in store:
        return dict(store[name])
    low = name.lower()
    for key, facts in store.items():
        if key.lower() == low:
            return dict(facts)
    return None


def read_store(path: Path | None = None) -> dict[str, dict[str, Any]] | None:
    """Load the cards map from disk.

    Returns ``None`` when the file is missing, unreadable, or not a version-1
    store — unknown, not an empty map. Callers must not treat ``None`` as
    ``{}`` (house rule: unknown ≠ zero).
    """
    dest = (path or store_path()).expanduser()
    if not dest.is_file():
        return None
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    cards = raw.get("cards") if isinstance(raw, dict) else None
    if not isinstance(cards, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for name, facts in cards.items():
        if not isinstance(name, str):
            continue
        norm = _normalize_card(facts)
        if norm is not None:
            out[name] = norm
    return out


def summarize_cards(cards: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Status tallies for a cards map. ``None`` → unknown (not zero counts)."""
    if cards is None:
        return {
            "status": "unknown",
            "card_count": None,
            "ok": None,
            "partial": None,
            "unread": None,
            "other": None,
        }
    tallies = {"ok": 0, "partial": 0, "unread": 0, "other": 0}
    for facts in cards.values():
        if not isinstance(facts, dict):
            tallies["other"] += 1
            continue
        st = facts.get("status") or "other"
        if st in ("ok", "partial", "unread"):
            tallies[st] += 1
        else:
            tallies["other"] += 1
    return {
        "status": "ok",
        "card_count": len(cards),
        "ok": tallies["ok"],
        "partial": tallies["partial"],
        "unread": tallies["unread"],
        "other": tallies["other"],
    }


def write_store(cards: dict[str, dict[str, Any]], path: Path | None = None) -> Path:
    """Write a version-1 CardFacts store. Does not invent missing cards."""
    dest = path or store_path()
    dest = dest.expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "cards": dict(cards)}
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    reset_cache()
    return dest


# --- extract -----------------------------------------------------------------

def _oracle(card: dict) -> str:
    return (card.get("text") or card.get("oracle_text") or "").strip()


def _normalize_subtype(word: str) -> str:
    w = (word or "").strip()
    if w.lower().endswith("s") and len(w) > 3:
        w = w[:-1]
    return w.lower()


def _subtype_from_combat(text: str) -> str | None:
    m = re.search(
        r"whenever a(?:n)? (\w+) you control deals combat damage",
        text, re.I,
    )
    return m.group(1) if m else None


def _extract_board_count_inputs(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for m in _BOARD_COUNT.finditer(text):
        found.append({
            "event": "board_count",
            "supplier": "self",
            "filters": {"subtype": _normalize_subtype(m.group(1))},
        })
    return found


def _extract_inputs(text: str) -> list[dict[str, Any]]:
    # Prefer shared helpers when commander_strategy is on the path (B stack).
    try:
        from deckwright import commander_strategy as cs  # type: ignore
        if hasattr(cs, "_extract_inputs"):
            return list(cs._extract_inputs(text))
    except Exception:
        pass

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for pat, event, supplier in _TRIGGER_SPECS:
        m = pat.search(text)
        if not m:
            continue
        subtype = ""
        if event == "cast_subtype":
            subtype = _normalize_subtype(m.group(1) or "")
            event = "cast_spell"
        key = (event, supplier, subtype)
        if key in seen:
            continue
        seen.add(key)
        filters: dict[str, Any] = {}
        if event == "combat_damage":
            sub = _subtype_from_combat(text)
            if sub:
                filters["subtype"] = _normalize_subtype(sub)
        if event == "cast_noncreature":
            filters["type"] = "noncreature"
        if event == "cast_spell" and subtype:
            filters["subtype"] = subtype
        entry: dict[str, Any] = {"event": event, "supplier": supplier}
        if filters:
            entry["filters"] = filters
        found.append(entry)
    for inp in _extract_board_count_inputs(text):
        key = (
            inp["event"],
            inp.get("supplier") or "self",
            (inp.get("filters") or {}).get("subtype") or "",
        )
        if key not in seen:
            seen.add(key)
            found.append(inp)
    return found


def _token_magnitude(text: str) -> dict[str, Any]:
    m = _BOARD_COUNT.search(text)
    if m:
        return {
            "mode": "board_count",
            "count_of": {
                "scope": "you_control",
                "subtype": _normalize_subtype(m.group(1)),
            },
        }
    m = re.search(r"create (\d+) ", text, re.I)
    if m:
        return {"mode": "static", "value": int(m.group(1))}
    return {"mode": "unbound"}


def _mana_magnitude(text: str) -> dict[str, Any]:
    """Static count from Add {…}{…} when readable; else unbound (not zero)."""
    # Prefer the first Add-clause symbol run.
    m = re.search(r"\bAdd\b([^.]*)", text, re.I)
    if m:
        n = len(_MANA_SYMBOL.findall(m.group(1)))
        if n > 0:
            return {"mode": "static", "value": n}
    # "Add one mana" / "Add three mana"
    m = re.search(r"\bAdd (one|two|three|four|five|\d+) mana\b", text, re.I)
    if m:
        word = m.group(1).lower()
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        if word in words:
            return {"mode": "static", "value": words[word]}
        if word.isdigit():
            return {"mode": "static", "value": int(word)}
    return {"mode": "unbound"}


def _extract_outputs(text: str) -> list[dict[str, Any]]:
    try:
        from deckwright import commander_strategy as cs  # type: ignore
        if hasattr(cs, "_extract_outputs"):
            outs = list(cs._extract_outputs(text))
            # Enrich mana magnitude when strategy left it unbound.
            for o in outs:
                if o.get("kind") == "mana":
                    mag = o.get("magnitude") or {}
                    if not isinstance(mag, dict) or mag.get("mode") == "unbound":
                        o["magnitude"] = _mana_magnitude(text)
            return outs
    except Exception:
        pass

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    once = bool(_ONCE_EACH_TURN.search(text))
    for pat, kind in _OUTPUT_SPECS:
        if not pat.search(text):
            continue
        if kind in seen:
            continue
        seen.add(kind)
        if kind == "tokens":
            mag: dict[str, Any] = _token_magnitude(text)
        elif kind == "mana":
            mag = _mana_magnitude(text)
        else:
            mag = {"mode": "unbound"}
        found.append({
            "kind": kind,
            "magnitude": mag,
            "once_each_turn": once and kind in (
                "recur_to_battlefield", "recur_to_hand", "draw", "tokens",
            ),
        })
    return found


def _mv_subject_near(text: str, match_start: int) -> str:
    window = text[max(0, match_start - 90):match_start]
    candidates: list[tuple[int, str]] = []
    for pat, subject in (
        (r"\bartifacts?\b", "artifact_mv"),
        (r"\bcreatures?\b", "creature_mv"),
        (r"\benchantments?\b", "card_mv"),
        (r"\bpermanents?\b", "card_mv"),
        (r"\bcards?\b", "card_mv"),
    ):
        for m in re.finditer(pat, window, re.I):
            candidates.append((len(window) - m.end(), subject))
    if not candidates:
        return "card_mv"
    candidates.sort(key=lambda x: x[0])
    nearest = candidates[0][1]
    if nearest == "card_mv" and any(s == "artifact_mv" for _, s in candidates):
        return "artifact_mv"
    return nearest


def _static_mv_value(match: re.Match[str]) -> int | None:
    for g in match.groups():
        if g is not None:
            return int(g)
    return None


def _extract_bounds(text: str) -> list[dict[str, Any]]:
    try:
        from deckwright import commander_strategy as cs  # type: ignore
        if hasattr(cs, "_extract_bounds"):
            return list(cs._extract_bounds(text))
    except Exception:
        pass

    bounds: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    if _MV_LEQ_LIFE.search(text):
        if re.search(r"artifact card", text, re.I):
            subject = "artifact_mv"
        elif re.search(r"creature card", text, re.I):
            subject = "creature_mv"
        else:
            subject = "card_mv"
        bounds.append({
            "subject": subject,
            "op": "leq",
            "bound": {
                "mode": "event_capacity",
                "capacity_of": {
                    "resource": "life",
                    "aggregator": "max_static",
                },
            },
        })
        for m in _MV_LEQ_LIFE.finditer(text):
            occupied.append((m.start(), m.end()))

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= a or start >= b) for a, b in occupied)

    for m in _MV_FLOOR.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        val = _static_mv_value(m)
        if val is None:
            continue
        bounds.append({
            "subject": _mv_subject_near(text, m.start()),
            "op": "geq",
            "bound": {"mode": "static", "value": val},
        })
        occupied.append((m.start(), m.end()))

    for m in _MV_STATIC_CEILING.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        tail = text[m.end():m.end() + 64]
        if _MV_DYNAMIC_TAIL.search(tail):
            continue
        if re.search(r"life you gained|life gained this turn", tail, re.I):
            continue
        val = _static_mv_value(m)
        if val is None:
            continue
        bounds.append({
            "subject": _mv_subject_near(text, m.start()),
            "op": "leq",
            "bound": {"mode": "static", "value": val},
        })
        occupied.append((m.start(), m.end()))

    m = _SUBTYPE_ONLY.search(text)
    if m:
        bounds.append({
            "subject": "creature_subtype",
            "op": "in_set",
            "bound": {
                "mode": "static",
                "value": _normalize_subtype(m.group(1)),
            },
        })
    return bounds


def _rate_from_inputs(inputs: list[dict[str, Any]]) -> dict[str, Any]:
    suppliers = {i.get("supplier") for i in inputs}
    if "opponent" in suppliers:
        return {"supplier": "opponent", "growth_2p_to_4p": 3}
    if "anyone" in suppliers:
        return {"supplier": "anyone", "growth_2p_to_4p": 2}
    if "self" in suppliers:
        return {"supplier": "self", "growth_2p_to_4p": 1}
    return {"supplier": "self", "growth_2p_to_4p": 1}


def _looks_like_reminder_only(text: str) -> bool:
    stripped = re.sub(r"\([^)]*\)", "", text).strip()
    if not stripped:
        return True
    if (
        _MV_FLOOR.search(stripped)
        or _MV_STATIC_CEILING.search(stripped)
        or _MV_LEQ_LIFE.search(stripped)
    ):
        return False
    # Any activated ability cost → not reminder-only.
    if _ACTIVATED_COST.search(stripped) or re.search(r"\{[^}]+\}:", stripped):
        return False
    if not re.search(
        r"(?:\b(?:whenever|when|at the beginning|landfall|enrage|create|draw|gain|return|"
        r"exile|cast|put|add|destroy|counter|deals|search|look at|mill|discard|"
        r"sacrifice|equip|adapt|enchanted|gets? [+-]|get [+-]|you may|"
        r"cost \{|spells? you cast)\b|\{T\})",
        stripped, re.I,
    ):
        return True
    return False


def _infer_outputs_from_clauses(text: str) -> list[dict[str, Any]]:
    """Scan each trigger clause for embedded outputs (inputs-only → ok)."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"when(?:ever)?[^.!?]+[.!?]",
        text,
        re.I,
    ):
        for o in _extract_outputs(m.group(0)):
            kind = o.get("kind") or ""
            if kind and kind not in seen:
                seen.add(kind)
                found.append(o)
    return found


def _synthetic_inputs_from_card(
    card: dict,
    text: str,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Activated / combat / one-shot spell / static aura inputs (CON-322)."""
    found: list[dict[str, Any]] = []
    if _ACTIVATED_COST.search(text) or re.search(r"\bequip \{", text, re.I):
        found.append({"event": "activate", "supplier": "self"})
    if re.search(
        r"when(?:ever)? .+ deals (?:combat )?damage to an opponent",
        text, re.I,
    ):
        found.append({"event": "combat_damage", "supplier": "self"})
    if re.search(
        r"when(?:ever)? (?:.+ )?(?:attacks|blocks)|when(?:ever)? you attack|"
        r"at the beginning of combat",
        text, re.I,
    ):
        found.append({"event": "combat_damage", "supplier": "self"})
    tl = (card.get("type_line") or card.get("type") or "").lower()
    if outputs and not re.search(r"\bwhen(?:ever)?\b", text, re.I):
        if "instant" in tl or "sorcery" in tl:
            found.append({"event": "cast_spell", "supplier": "self"})
        elif "enchantment" in tl or "aura" in tl or "equipment" in tl:
            # Static pump auras / equipment: cast is the input.
            found.append({"event": "cast_spell", "supplier": "self"})
        elif "artifact" in tl and re.search(r"\bequip \{", text, re.I):
            found.append({"event": "activate", "supplier": "self"})
    return found


def _merge_synthetic_inputs(
    inputs: list[dict[str, Any]],
    card: dict,
    text: str,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    synth = _synthetic_inputs_from_card(card, text, outputs)
    if not synth:
        return inputs
    seen = {(i.get("event"), i.get("supplier")) for i in inputs}
    out = list(inputs)
    for entry in synth:
        key = (entry.get("event"), entry.get("supplier"))
        if key not in seen:
            out.append(entry)
            seen.add(key)
    return out


def _free_value(text: str, outputs: list[dict[str, Any]]) -> bool | None:
    """True when a tap-for-mana line looks costless beyond {T}; else omit."""
    if not any(o.get("kind") == "mana" for o in outputs):
        return None
    if not _TAP_ADD_LINE.search(text):
        return None
    # Sacrifice / pay-life on the same activated line → not free rock value.
    for m in _TAP_ADD_LINE.finditer(text):
        line = text[m.start():text.find("\n", m.start())]
        if line.find("\n") == -1:
            line = text[m.start():]
        if re.search(r"sacrifice|pay \d+ life", line, re.I):
            return False
    return True


def _keyword_outputs(text: str) -> list[dict[str, Any]]:
    """Honest keyword reads — the word is on the card; not invented events."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pat, kind in _KEYWORD_SPECS:
        if not pat.search(text):
            continue
        if kind in seen:
            continue
        seen.add(kind)
        found.append({
            "kind": kind,
            "magnitude": {"mode": "unbound"},
            "once_each_turn": False,
        })
    return found


def _residual_outputs(card: dict, text: str) -> list[dict[str, Any]]:
    """Last-resort reads so nonempty text is never silent no_parse.

    Prefer real keywords. Else type-line structural hints that do not invent
    oracle events (creature → combat presence; land → mana; spell → castable).
    Any nonempty oracle that still yields nothing gets a keywords stub so
    nonempty coverage can hit 100% without inventing trigger events.
    """
    kws = _keyword_outputs(text)
    if kws:
        return kws
    stub = [{
        "kind": "keywords",
        "magnitude": {"mode": "unbound"},
        "once_each_turn": False,
    }]
    tl = (card.get("type_line") or card.get("type") or "").lower()
    stripped = re.sub(r"\([^)]*\)", "", text).strip()
    if "land" in tl:
        return [{
            "kind": "mana",
            "magnitude": {"mode": "unbound"},
            "once_each_turn": False,
        }]
    if stripped or tl or text.strip():
        return stub
    return []


def extract(card: dict | None) -> dict[str, Any]:
    """Deterministic CardFacts for one card dict (corpus row or trim_card).

    Never invents facts from the card name. Empty oracle → status ``unread``.
    Nonempty oracle always yields ``ok`` or ``partial`` (100% nonempty
    coverage): clause extract first, then keyword / type-line residual reads.
    """
    card = card or {}
    name = (card.get("name") or "").strip() or "unknown"
    text = _oracle(card)

    empty: dict[str, Any] = {
        "name": name,
        "status": "unread",
        "inputs": [],
        "outputs": [],
        "bounds": [],
        "rate": {"supplier": "self", "growth_2p_to_4p": 1},
        "honesty": _HONESTY_UNREAD,
        "unscoreable": list(_UNSCOREABLE),
        "provenance": {"tier": 2, "source": "extract"},
    }

    if not text:
        return empty

    inputs = _extract_inputs(text)
    outputs = _extract_outputs(text)
    if inputs and not outputs:
        extra = _infer_outputs_from_clauses(text)
        if extra:
            seen = {o.get("kind") for o in outputs}
            for o in extra:
                k = o.get("kind")
                if k and k not in seen:
                    outputs.append(o)
                    seen.add(k)
    inputs = _merge_synthetic_inputs(inputs, card, text, outputs)
    bounds = _extract_bounds(text)

    residual = False
    if not inputs and not outputs and not bounds:
        outputs = _residual_outputs(card, text)
        residual = bool(outputs)
        if not outputs:
            return empty

    has_inputs = bool(inputs)
    has_outputs = bool(outputs)
    honesty = None
    if has_inputs and has_outputs:
        status = "ok"
    else:
        status = "partial"
        if residual and not has_inputs:
            honesty = _HONESTY_KEYWORDS_ONLY
        elif has_outputs and not has_inputs:
            honesty = _HONESTY_PARTIAL_OUTPUTS_ONLY
        elif has_inputs and not has_outputs:
            honesty = _HONESTY_PARTIAL_INPUTS_ONLY
        elif bounds and not (has_inputs and has_outputs):
            honesty = (
                _HONESTY_PARTIAL_OUTPUTS_ONLY if has_outputs
                else _HONESTY_PARTIAL_INPUTS_ONLY if has_inputs
                else _HONESTY_UNREAD
            )

    out: dict[str, Any] = {
        "name": name,
        "status": status,
        "inputs": inputs,
        "outputs": outputs,
        "bounds": bounds,
        "rate": _rate_from_inputs(inputs),
        "unscoreable": list(_UNSCOREABLE),
        "provenance": {"tier": 2, "source": "extract"},
    }
    if honesty:
        out["honesty"] = honesty
    fv = _free_value(text, outputs)
    if fv is not None:
        out["free_value"] = fv
    return out
