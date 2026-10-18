"""
curve.py — can she actually play the plan?

DERIVATION.md's vertical clauses judge each card against the plan. The synergy
graph asks whether cards hold together. This is the remaining clause:

    The curve decides whether she can actually play them.

Playtest question: average cost vs ramp vs lands vs explosive ramp — can the player play
the plan every turn? A deck can be perfectly connected and uncastable.

No model. Pure functions over decks + the existing goldfish.
"""

from __future__ import annotations

import re
from typing import Any

from deckwright import sim

# A deck (or brief) we could not read. NOT a mid-curve guess and NOT a zero
# act rate. Callers must branch on status / UNKNOWN rather than on emptiness.
UNKNOWN = "unknown"

# derived skeleton shapes live role counts. Kept as a named flag so
# a regression can turn observation-only back on without rewriting callers.
# Sum-zero within spell roles is the invariant; unknown preference stays mid
# (never invents low/high from an empty brief).
SKELETON_LIVE = True

# Phrases that name a curve preference. Scored like the plan graph's archetype
# picker: every field that survives commander overwrite is in play, and a
# nearby negation ("not cheap") does not elect the opposite.
_LOW_PHRASES = (
    "low curve", "low-curve", "low-to-the-ground", "low to the ground",
    "low mana", "low mana cost", "low mana costs", "cmc low",
    "cheap and fast", "cheap", "fast", "curve out", "aggro", "aggressive",
    "1-mana", "2-mana", "doing things early", "early game", "early turns",
)
_HIGH_PHRASES = (
    "high curve", "high-curve", "seven-drop", "7-drop", "haymaker",
    "haymakers", "ramp into", "expensive", "top-end", "top end",
    "late game", "big creature", "big expensive", "big spell", "big spells",
    "bomb", "don't mind waiting", "do not mind waiting", "i don't mind waiting",
)

_NEGATORS = ("not ", "no ", "never ", "without ", "isn't ", "is not ",
             "don't want ", "do not want ")

# Explosive ramp: mana doublers, rituals, mass treasure. Sibling of the
# synergy graph's oracle_signals — the two specs meet in mechanic_families
# and nowhere else. Patterns here are the fallback when the signal file
# has not been extended yet.
_EXPLOSIVE_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"add \{[wubrgc]/\{[wubrgc]/\{[wubrgc]\}",  # ritual: three pips
        r"add \{[wubrgc]\}\{[wubrgc]\}\{[wubrgc]\}",
        r"untap all (?:lands|permanents)",
        r"lands? (?:you control )?tap for (?:an )?additional",
        r"(?:permanents|lands) you control .+ (?:tap for|add) .+ additional",
        r"double (?:your )?mana",
        r"twice as much mana",
        r"create (?:two|three|four|five|\d+) treasure tokens?",
        r"mana flare",
    )
]

_NORMAL_RAMP_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"\{t\}: add \{",
        r"\{t\}: add one mana",
        r"search your library for (?:a |up to .+ )?(?:basic )?land",
        r"put (?:a|that) land .+ onto the battlefield",
        r"add \{[wubrgc]\}",
    )
]


def _brief_text(brief: dict | None) -> str | None:
    """Prose the curve picker may read.

    commander overwrite replaces strategy/plan/mechanics
    with the pick write-up. angle, fantasy, theme, playstyle, and fits still
    carry her words — and `plan or angle` quietly dropped angle whenever plan
    was set. Join every field; do not let one shadow another.
    """
    if not isinstance(brief, dict) or not brief:
        return None
    parts: list[str] = []
    for k in ("strategy", "theme", "playstyle", "plan", "angle", "fantasy",
              "fits", "does"):
        v = brief.get(k)
        if v:
            parts.append(str(v))
    mechs = brief.get("mechanics") or []
    if isinstance(mechs, (list, tuple)):
        parts.extend(str(m) for m in mechs if m)
    elif mechs:
        parts.append(str(mechs))
    text = " ".join(parts).strip().lower()
    return text or None


def _positive_hits(phrase: str, text: str) -> int:
    hits = 0
    start = 0
    step = max(len(phrase), 1)
    while True:
        i = text.find(phrase, start)
        if i < 0:
            return hits
        window = text[max(0, i - 40):i]
        if not any(n in window for n in _NEGATORS):
            hits += 1
        start = i + step


def preference(brief: dict | None) -> str:
    """low | mid | high | UNKNOWN.

    UNKNOWN when there is nothing to read. mid when her words do not choose.
    Never guess high or low from an empty brief.
    """
    text = _brief_text(brief)
    if text is None:
        return UNKNOWN
    low = sum(_positive_hits(p, text) for p in _LOW_PHRASES)
    high = sum(_positive_hits(p, text) for p in _HIGH_PHRASES)
    if low == 0 and high == 0:
        return "mid"
    if low > high:
        return "low"
    if high > low:
        return "high"
    # Tie — she said both, or the contrast cancelled. Unknown, not a coin flip.
    return "mid"


def cmc_sort_key(card: dict, pref: str) -> float:
    """Tiebreak key (higher wins when reverse-sorted).

    low  -> cheaper wins
    high -> dearer wins
    mid / UNKNOWN / missing cmc -> 0 (do not tip)
    """
    if pref not in ("low", "high"):
        return 0.0
    if "cmc" not in card and "mana_value" not in card:
        return 0.0
    raw = card["cmc"] if "cmc" in card else card.get("mana_value")
    try:
        cmc = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return -cmc if pref == "low" else cmc


def _oracle(card: dict) -> str:
    parts = []
    for key in ("text", "oracle_text"):
        t = card.get(key)
        if isinstance(t, str) and t.strip():
            parts.append(t)
            break
    for face in card.get("card_faces") or []:
        ft = (face or {}).get("oracle_text")
        if isinstance(ft, str) and ft.strip():
            parts.append(ft)
    return "\n".join(parts)


def _is_land(card: dict) -> bool:
    role = (card.get("role") or "").lower()
    if role in ("lands", "basic"):
        return True
    typ = (card.get("type") or card.get("type_line") or "").lower()
    head = typ.split("—")[0]
    return "land" in head.split()


def _deck_measurable(deck: list[dict]) -> str | None:
    """None if ok; otherwise the unknown reason."""
    if not deck:
        return "empty deck"
    for c in deck:
        if _is_land(c):
            continue
        if "cmc" not in c and "mana_value" not in c:
            return f"missing cmc on {c.get('name')!r}"
        raw = c["cmc"] if "cmc" in c else c.get("mana_value")
        try:
            float(raw)
        except (TypeError, ValueError):
            return f"unparseable cmc on {c.get('name')!r}"
    return None


def castable_curve(
    deck: list[dict],
    cmdr_cmc: int,
    *,
    payoff_names: set[str] | None = None,
    conditions: list[dict] | None = None,
    runs: int = 400,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Per-turn fraction of goldfish runs where she cast a non-land spell.

    Not win rate — a standing design constraint. "Was she able to act."
    """
    reason = _deck_measurable(deck)
    if reason:
        return {"status": UNKNOWN, "reason": reason}
    conds = conditions or [{"label": "_curve_probe",
                            "check": {"mana_available": 99}}]
    payoffs = set(payoff_names or [])
    cards = sim.prep(deck, payoffs)
    acted = [0] * sim.MAX_TURN
    rng_state = None
    if seed is not None:
        import random
        rng_state = random.getstate()
        random.seed(seed)
    try:
        for _ in range(runs):
            _fired, _log, trace = sim.play_one(cards, cmdr_cmc, conds)
            for t in trace.get("acted_turns") or []:
                if 1 <= t <= sim.MAX_TURN:
                    acted[t - 1] += 1
    finally:
        if rng_state is not None:
            import random
            random.setstate(rng_state)
    return {
        "status": "ok",
        "by_turn": [round(n / runs, 4) for n in acted],
        "runs": runs,
    }


def missed_land_drops(
    deck: list[dict],
    cmdr_cmc: int,
    *,
    runs: int = 400,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Fraction of land-drop opportunities she could not take.

    The direct measurement behind 36-vs-38. rate in [0, 1]; UNKNOWN when the
    deck cannot be measured.
    """
    reason = _deck_measurable(deck)
    if reason:
        return {"status": UNKNOWN, "reason": reason}
    conds = [{"label": "_curve_probe", "check": {"mana_available": 99}}]
    cards = sim.prep(deck, set())
    missed = 0
    opportunities = 0
    rng_state = None
    if seed is not None:
        import random
        rng_state = random.getstate()
        random.seed(seed)
    try:
        for _ in range(runs):
            _fired, _log, trace = sim.play_one(cards, cmdr_cmc, conds)
            missed += int(trace.get("missed_land_drops") or 0)
            opportunities += int(trace.get("land_drop_opportunities") or 0)
    finally:
        if rng_state is not None:
            import random
            random.setstate(rng_state)
    if opportunities <= 0:
        return {"status": UNKNOWN, "reason": "no land-drop opportunities recorded"}
    return {
        "status": "ok",
        "rate": round(missed / opportunities, 4),
        "missed": missed,
        "opportunities": opportunities,
        "runs": runs,
    }


def ramp_split(deck: list[dict]) -> dict[str, Any]:
    """{normal, explosive} counts. UNKNOWN when oracle text is unreadable."""
    if not deck:
        return {"status": UNKNOWN, "reason": "empty deck",
                "normal": 0, "explosive": 0}
    normal = explosive = 0
    unknowns = 0
    for c in deck:
        if _is_land(c):
            continue
        text = _oracle(c)
        if not text:
            unknowns += 1
            continue
        if any(p.search(text) for p in _EXPLOSIVE_PATTERNS):
            explosive += 1
            continue
        m = sim.mechanics(c, set())
        if m.get("produces") or m.get("fetch") or m.get("drop"):
            normal += 1
    if unknowns and normal == 0 and explosive == 0:
        return {"status": UNKNOWN, "reason": "no readable ramp cards",
                "normal": 0, "explosive": 0, "unknown": unknowns}
    return {"status": "ok", "normal": normal, "explosive": explosive,
            "unknown": unknowns}


def skeleton_for(
    commander: dict | None,
    plan: dict | None,
) -> dict[str, Any]:
    """Demand-derived ROLES from commander CMC + plan curve preference.

    Always returns a report. Live application is gated by SKELETON_LIVE —
    until castable_curve shows derived beats fixed, this is observation only.
    """
    # Import here so curve.py stays importable before app is fully loaded.
    from deckwright import app as _app

    cmc = int((commander or {}).get("cmc") or 0)
    pref = "mid"
    if isinstance(plan, dict):
        pref = plan.get("curve") or preference(plan.get("brief") or plan) or "mid"
        if pref == UNKNOWN:
            pref = "mid"

    # Start from the conventional skeleton, then densify early or top-end.
    roles = [(n, c, lab) for n, c, lab in _app.ROLES]
    by = {n: i for i, (n, _, _) in enumerate(roles)}

    def _set(name: str, count: int) -> None:
        i = by[name]
        n, _, lab = roles[i]
        roles[i] = (n, max(2, count) if count else count, lab)

    # Four-mana commanders that want a board first densify 1–3; the tutorial
    # case. Low-curve wish does the same regardless of CMC.
    if pref == "low" or (cmc >= 4 and pref != "high"):
        _set("draw", max(roles[by["draw"]][1], 12))  # tutorial floor hypothesis
        _set("ramp", max(roles[by["ramp"]][1], 10))
        # Pull from theme/deepcut — small, reversible shifts.
        _set("theme", max(2, roles[by["theme"]][1] - 2))
        _set("deepcut", max(2, roles[by["deepcut"]][1] - 1))
    elif pref == "high":
        _set("ramp", roles[by["ramp"]][1] + 2)
        _set("wipe", roles[by["wipe"]][1] + 1)
        _set("theme", max(2, roles[by["theme"]][1] - 2))
        _set("deepcut", max(2, roles[by["deepcut"]][1] - 1))

    return {
        "roles": roles,
        "reporting_only": not SKELETON_LIVE,
        "preference": pref,
        "commander_cmc": cmc,
        "live": SKELETON_LIVE,
    }
