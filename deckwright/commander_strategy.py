"""CommanderStrategy from one card's oracle text.

Layer 2 of the V2 design. Clause-level extraction on the
commander only. Unreadable text → status unread, empty arrays, honesty note.
Never invent a strategy. No named-commander branches.
"""

from __future__ import annotations

import re
from typing import Any

# Soft combo / hidden power stay in the model at weight 0 (v2-design §B.1).
_UNSCOREABLE = ("soft_combo", "hidden_power")

_HONESTY_UNREAD = (
    "I could not read a concept model from this commander. "
    "The list is the usual build path."
)

_HONESTY_PARTIAL_OUTPUTS_ONLY = (
    "I read what it produces, not what it needs."
)

_HONESTY_PARTIAL_INPUTS_ONLY = (
    "I read what it needs, not what it produces."
)

# Trigger patterns → (event, supplier, optional filter extractor)
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
        r"when(?:ever)? .+ deals combat damage to (?:a )?player",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? .+ deals (?:combat )?damage to an opponent",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? (?:a|another) creature (?:you control )?dies",
        re.I), "dies", "self"),
    (re.compile(
        r"when(?:ever)? this creature dies",
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
    # Non-greedy — greedy `.+` overshoots past "enters" (coverage).
    (re.compile(
        r"when(?:ever)? .+? enters(?: the battlefield)?(?=[,.\s]|$)",
        re.I), "etb_creature", "self"),
    (re.compile(
        r"when(?:ever)? this (?:creature|permanent|artifact|enchantment) enters",
        re.I), "etb_creature", "self"),
    (re.compile(
        r"when(?:ever)? a land (?:you control )?enters",
        re.I), "landfall", "self"),
    (re.compile(
        r"when(?:ever)? this creature attacks",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? .+ attacks(?:[,.]|$| and)",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? you attack",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? .+ blocks",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"at the beginning of combat",
        re.I), "combat_damage", "self"),
    (re.compile(
        r"when(?:ever)? .+ is dealt damage|enrage\s*[—\-]",
        re.I), "dies", "self"),
    (re.compile(
        r"when(?:ever)? enchanted creature dies",
        re.I), "dies", "self"),
    (re.compile(
        r"when(?:ever)? an opponent discards",
        re.I), "draw", "opponent"),
    (re.compile(
        r"when(?:ever)? .+ becomes crewed",
        re.I), "etb_creature", "self"),
    (re.compile(
        r"when(?:ever)? enchanted .+ is tapped for mana",
        re.I), "activate", "self"),
    (re.compile(
        r"when(?:ever)? you cast (?:an )?instant or sorcery",
        re.I), "cast_noncreature", "self"),
    (re.compile(
        r"when(?:ever)? .+ becomes the target",
        re.I), "cast_spell", "anyone"),
    (re.compile(
        r"when(?:ever)? a player taps a land for mana",
        re.I), "activate", "anyone"),
    (re.compile(
        r"when(?:ever)? you cast your first spell",
        re.I), "cast_spell", "self"),
    (re.compile(
        r"when(?:ever)? a player casts? (?:an )?instant or sorcery",
        re.I), "cast_noncreature", "anyone"),
    (re.compile(
        r"when(?:ever)? .+ attack(?:s|)",
        re.I), "combat_damage", "self"),
]

_OUTPUT_SPECS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"return target .+ from (?:your )?graveyard to the battlefield",
        re.I), "recur_to_battlefield"),
    (re.compile(
        r"return .+ from (?:your )?graveyard to (?:your )?hand",
        re.I), "recur_to_hand"),
    (re.compile(
        r"return that card to (?:its owner's|your) hand",
        re.I), "recur_to_hand"),
    (re.compile(r"create .+(?:token|tokens)", re.I), "tokens"),
    (re.compile(r"draw (?:a |one |two |three |\d+ )?cards?", re.I), "draw"),
    (re.compile(r"you gain (?:\d+|that much|X) life|you gain life equal", re.I),
     "life"),
    (re.compile(r"gains? lifelink", re.I), "life"),
    (re.compile(r"(?:add|creates?) .*(?:mana|{[wubrgc0-9]})", re.I), "mana"),
    (re.compile(r"untap (?:up to )?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|all) lands", re.I), "mana"),
    (re.compile(r"adds? an additional", re.I), "mana"),
    (re.compile(r"put .+ \+1/\+1 counter", re.I), "counters"),
    (re.compile(r"put .+ -1/-1 counter", re.I), "counters"),
    (re.compile(r"gets? [+-]?\d+/[+-]?\d+", re.I), "counters"),
    (re.compile(r"get [+-]?\d+/[+-]?\d+", re.I), "counters"),
    (re.compile(
        r"deals (?:\d+|X|that much) damage(?: to | divided)",
        re.I), "life"),
    (re.compile(
        r"deals damage to (?:any target|target)",
        re.I), "life"),
    (re.compile(
        r"(?:each opponent loses|loses) (?:\d+|X) life",
        re.I), "life"),
    (re.compile(
        r"exile target .+ from (?:an opponent's )?graveyard",
        re.I), "recur_to_battlefield"),
    (re.compile(
        r"return (?:this|target) .+ to (?:its owner's|your) hand",
        re.I), "recur_to_hand"),
    (re.compile(r"exile .+ then (?:cast|play)", re.I), "deploy_without_cast"),
    (re.compile(
        r"put (?:an? |target )?(?:artifact|creature|permanent|land).+onto the battlefield",
        re.I), "deploy_without_cast"),
    (re.compile(
        r"you may cast .+ from (?:your )?graveyard",
        re.I), "deploy_without_cast"),
    (re.compile(r"search your library for", re.I), "draw"),
    (re.compile(r"look at the top", re.I), "draw"),
    (re.compile(r"destroy (?:all |target |each )", re.I), "life"),
    (re.compile(r"exile (?:all |target |each )", re.I), "life"),
    (re.compile(r"mill (?:\d+|X|that many)", re.I), "draw"),
    (re.compile(r"discards? (?:a |one |\d+|all )?cards?", re.I), "draw"),
    (re.compile(r"you get \{E\}", re.I), "counters"),
    (re.compile(r"\badapt \d+", re.I), "counters"),
    (re.compile(r"tap (?:all |target |each )", re.I), "life"),
    (re.compile(
        r"gains? (?:first strike|flying|vigilance|trample|haste|hexproof|"
        r"indestructible|deathtouch|lifelink|menace)",
        re.I), "counters"),
    (re.compile(r"counter target", re.I), "life"),
    (re.compile(r"gain control of", re.I), "deploy_without_cast"),
    (re.compile(r"spells? you cast cost", re.I), "mana"),
    (re.compile(r"reveal (?:the top|a card)", re.I), "draw"),
    (re.compile(r"cast (?:it|that spell|a copy) without paying", re.I), "deploy_without_cast"),
    (re.compile(r"copy (?:it|that spell|target)", re.I), "tokens"),
    (re.compile(r"prevent all damage", re.I), "life"),
    (re.compile(r"phases? out", re.I), "recur_to_hand"),
    (re.compile(r"put .+ onto the battlefield", re.I), "deploy_without_cast"),
    (re.compile(r"sacrifice .+\:", re.I), "life"),
    (re.compile(r"play an additional land", re.I), "mana"),
    (re.compile(r"cost \{?\d+\}? more to (?:cast|activate)", re.I), "mana"),
    (re.compile(r"enters with .{0,40}counter", re.I), "counters"),
    (re.compile(r"is a creature with base power", re.I), "tokens"),
    (re.compile(r"exiles? the top", re.I), "draw"),
    (re.compile(r"return it to the battlefield", re.I), "recur_to_battlefield"),
    (re.compile(r"take an extra turn", re.I), "extra_turn"),
]

# MV ≤ life gained this turn (v2-design §B.3)
_MV_LEQ_LIFE = re.compile(
    r"mana value (?:less than or equal to|≤|<=) .{0,40}?"
    r"(?:life you gained|amount of life you gained|life gained this turn)",
    re.I | re.S,
)

# static MV floor / ceiling. op lives on the bound entry (leq|geq).
_MV_FLOOR = re.compile(
    r"mana value (?:(\d+) or greater|greater than or equal to (\d+))",
    re.I,
)
_MV_STATIC_CEILING = re.compile(
    r"mana value (?:(\d+) or less|less than or equal to (\d+))",
    re.I,
)
# Dynamic ceilings (life / experience) must not become static numeric bounds.
_MV_DYNAMIC_TAIL = re.compile(
    r"^\s*(?:the )?(?:amount of )?life\b|^\s*(?:the )?number of experience",
    re.I,
)

# MV ≤ experience counters (Meren-shaped). Parallel to life capacity.
_MV_LEQ_EXPERIENCE = re.compile(
    r"mana value (?:is )?(?:less than or equal to|≤|<=) .{0,80}?"
    r"(?:the )?number of experience counters?",
    re.I | re.S,
)

# power ceiling (Shirei-shaped).
_POWER_CEILING = re.compile(
    r"power (?:(\d+) or less|less than or equal to (\d+))",
    re.I,
)

# global draw forbid (Maralen-shaped).
_FORBID_DRAW = re.compile(
    r"(?:players?|you) can't draw cards?",
    re.I,
)

# Subtype from lord anthems ("Goblin creatures you control get…").
_SUBTYPE_ANTHEM = re.compile(
    r"(\w+) creatures? you control get",
    re.I,
)
# Subtype from tutor / steal lines (Sliver Overlord-shaped).
_SUBTYPE_TUTOR = re.compile(
    r"search (?:your |a )?library for (?:a |an )?(\w+) card",
    re.I,
)
_SUBTYPE_TARGET = re.compile(
    r"(?:gain control of|return) target (\w+)\b",
    re.I,
)

# Keep old name as alias so cardfacts / callers that referenced it still work.
_SUBTYPE_ONLY = _SUBTYPE_ANTHEM

# Words that look like (\w+) but are card types / generics, not creature types.
_NOT_CREATURE_SUBTYPE = frozenset({
    "artifact", "enchantment", "land", "instant", "sorcery",
    "planeswalker", "battle", "creature", "permanent", "card",
    "spell", "player", "token", "noncreature",
})

_ONCE_EACH_TURN = re.compile(r"only once each turn|once each turn", re.I)

_BOARD_COUNT = re.compile(
    r"where X is the number of (\w+?)s? you control",
    re.I,
)


def _normalize_subtype(word: str) -> str:
    """Singular, lowercased creature type for filters."""
    w = (word or "").strip()
    if w.lower().endswith("s") and len(w) > 3:
        w = w[:-1]
    return w.lower()


def _oracle(card: dict) -> str:
    return (card.get("text") or card.get("oracle_text") or "").strip()


def _subtype_from_combat(text: str) -> str | None:
    m = re.search(
        r"whenever a(?:n)? (\w+) you control deals combat damage",
        text, re.I,
    )
    if m:
        return m.group(1)
    return None


def _subtype_from_cast(text: str, pat: re.Pattern[str]) -> str | None:
    m = pat.search(text)
    if m:
        return m.group(1)
    return None


def _extract_board_count_inputs(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for m in _BOARD_COUNT.finditer(text):
        sub = _normalize_subtype(m.group(1))
        found.append({
            "event": "board_count",
            "supplier": "self",
            "filters": {"subtype": sub},
        })
    return found


def _extract_inputs(text: str) -> list[dict[str, Any]]:
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
        entry: dict[str, Any] = {
            "event": event,
            "supplier": supplier,
        }
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


def _extract_outputs(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    once = bool(_ONCE_EACH_TURN.search(text))
    for pat, kind in _OUTPUT_SPECS:
        if not pat.search(text):
            continue
        if kind in seen:
            continue
        seen.add(kind)
        mag = _token_magnitude(text) if kind == "tokens" else {"mode": "unbound"}
        found.append({
            "kind": kind,
            "magnitude": mag,
            "once_each_turn": once and kind in (
                "recur_to_battlefield", "recur_to_hand", "draw", "tokens",
            ),
        })
    return found


def _mv_subject_near(text: str, match_start: int) -> str:
    """Type guard subject from words ahead of an MV phrase. No card-name branches.

    Prefer the *nearest* type word so an earlier unrelated 'creature' does not
    steal subject from 'permanent card with mana value N or less'.
    """
    window = text[max(0, match_start - 90):match_start]
    # (distance_from_end_of_window, subject) — smaller distance = closer to MV.
    candidates: list[tuple[int, str]] = []
    for pat, subject in (
        (r"\bartifacts?\b", "artifact_mv"),
        (r"\bcreatures?\b", "creature_mv"),
        (r"\benchantments?\b", "enchantment_mv"),
        (r"\bpermanents?\b", "card_mv"),
        (r"\bcards?\b", "card_mv"),
    ):
        for m in re.finditer(pat, window, re.I):
            candidates.append((len(window) - m.end(), subject))
    if not candidates:
        return "card_mv"
    candidates.sort(key=lambda x: x[0])
    nearest = candidates[0][1]
    # "artifact and … enchantment … mana value N" without non-X prefixes:
    # prefer artifact_mv over a lone nearest enchantment/card so cheap
    # creatures/instants are not false violations. Dual non-X floors use
    # _mv_floor_subjects instead.
    if nearest in ("card_mv", "enchantment_mv") and any(
        s == "artifact_mv" for _, s in candidates
    ):
        return "artifact_mv"
    return nearest


def _mv_floor_subjects(text: str, match_start: int) -> list[tuple[str, list[str]]]:
    """Subjects (+ optional excludes) for one MV floor phrase.

    Pattern-only: 'non-Equipment artifact' / 'non-Aura enchantment' emit typed
    floors with exclusions. Dual phrasing yields two bounds so cheap
    enchantments are visible to steer (the Bello gap).
    """
    window = text[max(0, match_start - 120):match_start]
    found: list[tuple[str, list[str]]] = []
    if re.search(r"\bnon-Equipment artifacts?\b", window, re.I):
        found.append(("artifact_mv", ["equipment"]))
    elif re.search(r"\bartifacts?\b", window, re.I):
        found.append(("artifact_mv", []))
    if re.search(r"\bnon-Aura enchantments?\b", window, re.I):
        found.append(("enchantment_mv", ["aura"]))
    elif re.search(r"\benchantments?\b", window, re.I):
        # Bare 'enchantment' next to an artifact floor → second subject.
        # Alone → enchantment_mv (not card_mv) so the type guard holds.
        if any(s == "artifact_mv" for s, _ in found) or not found:
            found.append(("enchantment_mv", []))
    if found:
        return found
    return [(_mv_subject_near(text, match_start), [])]


def _static_mv_value(match: re.Match[str]) -> int | None:
    for g in match.groups():
        if g is not None:
            return int(g)
    return None


def _extract_bounds(text: str) -> list[dict[str, Any]]:
    bounds: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    if _MV_LEQ_LIFE.search(text):
        # Prefer artifact when the return target is an artifact card.
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

    if _MV_LEQ_EXPERIENCE.search(text):
        if re.search(r"creature card", text, re.I):
            subject = "creature_mv"
        elif re.search(r"artifact card", text, re.I):
            subject = "artifact_mv"
        else:
            subject = "card_mv"
        bounds.append({
            "subject": subject,
            "op": "leq",
            "bound": {
                "mode": "event_capacity",
                "capacity_of": {
                    "resource": "experience",
                    "aggregator": "max_static",
                },
            },
        })
        for m in _MV_LEQ_EXPERIENCE.finditer(text):
            occupied.append((m.start(), m.end()))

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= a or start >= b) for a, b in occupied)

    for m in _MV_FLOOR.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        val = _static_mv_value(m)
        if val is None:
            continue
        for subject, excludes in _mv_floor_subjects(text, m.start()):
            entry: dict[str, Any] = {
                "subject": subject,
                "op": "geq",
                "bound": {"mode": "static", "value": val},
            }
            if excludes:
                entry["excludes"] = list(excludes)
            bounds.append(entry)
        occupied.append((m.start(), m.end()))

    for m in _MV_STATIC_CEILING.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        tail = text[m.end():m.end() + 64]
        if _MV_DYNAMIC_TAIL.search(tail):
            continue
        # "less than or equal to the amount of life…" already covered above.
        if re.search(r"life you gained|life gained this turn", tail, re.I):
            continue
        if re.search(r"experience counters?", tail, re.I):
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

    for m in _POWER_CEILING.finditer(text):
        val = _static_mv_value(m)
        if val is None:
            continue
        bounds.append({
            "subject": "creature_power",
            "op": "leq",
            "bound": {"mode": "static", "value": val},
        })

    if _FORBID_DRAW.search(text):
        bounds.append({
            "subject": "draw",
            "op": "forbid",
            "bound": {"mode": "static", "value": True},
        })

    seen_sub: set[str] = set()
    for pat in (_SUBTYPE_ANTHEM, _SUBTYPE_TUTOR, _SUBTYPE_TARGET):
        for m in pat.finditer(text):
            sub = _normalize_subtype(m.group(1))
            if not sub or sub in seen_sub:
                continue
            if sub in _NOT_CREATURE_SUBTYPE:
                continue
            seen_sub.add(sub)
            bounds.append({
                "subject": "creature_subtype",
                "op": "in_set",
                "bound": {
                    "mode": "static",
                    "value": sub,
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


def _weights_from(shell: dict[str, Any]) -> dict[str, float]:
    """Pure function of Layer 2 fields — never commander name."""
    rate = (shell.get("rate") or {}).get("supplier") or "self"
    has_bounds = bool(shell.get("bounds"))
    outputs = {o.get("kind") for o in shell.get("outputs") or []}
    w = {
        "scaling": 0.15,
        "resilience": 0.15,
        "explosive": 0.1,
        "soft_combo": 0.0,
        "free_value": 0.15,
        "hidden_power": 0.0,
        "multi_engine": 0.15,
        "value_to_turn": 0.15,
    }
    if rate == "opponent":
        w["scaling"] = 0.35
        w["value_to_turn"] = 0.2
    if has_bounds:
        w["multi_engine"] = 0.1
        w["resilience"] = 0.2
    if "recur_to_battlefield" in outputs or "recur_to_hand" in outputs:
        w["resilience"] = max(w["resilience"], 0.25)
    if "tokens" in outputs and rate == "opponent":
        w["explosive"] = 0.2
    # soft_combo / hidden_power forced 0
    w["soft_combo"] = 0.0
    w["hidden_power"] = 0.0
    return w


def _primary_axis(shell: dict[str, Any]) -> str:
    rate = (shell.get("rate") or {}).get("supplier")
    if rate == "opponent":
        return "opponent_rate"
    kinds = {o.get("kind") for o in shell.get("outputs") or []}
    if "recur_to_battlefield" in kinds or "recur_to_hand" in kinds:
        return "recursion"
    for inp in shell.get("inputs") or []:
        if (inp.get("filters") or {}).get("subtype"):
            return "type_matter"
    if shell.get("bounds"):
        return "threshold"
    if "tokens" in kinds or "extra_turn" in kinds:
        return "speed"
    return "mixed" if (shell.get("inputs") or shell.get("outputs")) else "mixed"


def _looks_like_reminder_only(text: str) -> bool:
    stripped = re.sub(r"\([^)]*\)", "", text).strip()
    if not stripped:
        return True
    # MV floor/ceiling phrases are ability text, even without a
    # classic trigger verb ("with mana value 4 or greater is a creature…").
    if (
        _MV_FLOOR.search(stripped)
        or _MV_STATIC_CEILING.search(stripped)
        or _MV_LEQ_LIFE.search(stripped)
    ):
        return False
    # No ability verbs → unread rather than a guessed empty-ok.
    if not re.search(
        r"\b(whenever|when |at the beginning|landfall|enrage|create|draw|gain|return|"
        r"exile|cast|put |add |destroy|counter|deals |search|look at|mill|"
        r"discard|sacrifice|equip|adapt|enchanted|gets? [+-]|get [+-]|"
        r"you may|cost \{|spells? you cast|\{T\})\b",
        stripped, re.I,
    ):
        # Activated costs without word-boundary verbs (hybrid mana etc.).
        if re.search(r"\{[^}]+\}:", stripped):
            return False
        return True
    return False


def derive(card: dict | None) -> dict[str, Any]:
    """Return a CommanderStrategy dict for one commander card."""
    card = card or {}
    name = (card.get("name") or "").strip() or "unknown"
    text = _oracle(card)

    empty: dict[str, Any] = {
        "status": "unread",
        "commander": name,
        "inputs": [],
        "outputs": [],
        "bounds": [],
        "rate": {"supplier": "self", "growth_2p_to_4p": 1},
        "weights": {k: 0.0 for k in (
            "scaling", "resilience", "explosive", "soft_combo", "free_value",
            "hidden_power", "multi_engine", "value_to_turn",
        )},
        "primary_axis": "mixed",
        "clock": "unknown",
        "honesty": _HONESTY_UNREAD,
        "unscoreable": list(_UNSCOREABLE),
    }

    if not text or _looks_like_reminder_only(text):
        return empty

    inputs = _extract_inputs(text)
    outputs = _extract_outputs(text)
    bounds = _extract_bounds(text)

    if not inputs and not outputs and not bounds:
        return empty

    shell = {
        "inputs": inputs,
        "outputs": outputs,
        "bounds": bounds,
        "rate": _rate_from_inputs(inputs),
    }
    has_inputs = bool(inputs)
    has_outputs = bool(outputs)
    honesty = None
    if has_inputs and has_outputs:
        status = "ok"
    else:
        status = "partial"
        if has_outputs and not has_inputs:
            honesty = _HONESTY_PARTIAL_OUTPUTS_ONLY
        elif has_inputs and not has_outputs:
            honesty = _HONESTY_PARTIAL_INPUTS_ONLY
        elif bounds and not has_inputs:
            honesty = _HONESTY_PARTIAL_OUTPUTS_ONLY

    rate = shell["rate"]
    weights = _weights_from(shell)
    out = {
        "status": status,
        "commander": name,
        "inputs": inputs,
        "outputs": outputs,
        "bounds": bounds,
        "rate": rate,
        "weights": weights,
        "primary_axis": _primary_axis({**shell, "rate": rate}),
        "clock": "burst" if rate.get("supplier") == "opponent" else (
            "grind" if any(
                o.get("kind") in ("recur_to_battlefield", "recur_to_hand")
                for o in outputs
            ) else "unknown"
        ),
        "unscoreable": list(_UNSCOREABLE),
    }
    if honesty:
        out["honesty"] = honesty
    return out
