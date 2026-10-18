"""
thesis_check.py — is the thesis measuring the right thing?

Three model-driven layers stack in this app. `THESIS` decides what "working"
means for a deck, `sim.run()` measures exactly those conditions, and the
overview explains the results. Each layer trusts the one below it, and until
now **nothing checked the first**. A thesis that picks the wrong conditions
produces a simulation that faithfully measures the wrong thing and an overview
that confidently explains it — internally consistent end to end, and wrong.
The output looks exactly like a correct one, which is what makes it dangerous.

The existing verifier checks that conditions use allowed `sim.THESIS_KEYS` and
that payoff cards exist in the deck. That is *validity*. This is *quality*.

## Why there is no model in this file

The obvious move is to ask a model whether the thesis is good. That produces a
fourth opinion sitting on top of three others, and no more reason to trust the
stack than before.

Everything here is arithmetic over data we already have — the simulation's own
curve points, the roles the builder assigned, the strategy the player chose,
and `sim.mechanics()` over oracle text. Same discipline as a claim
about the deck has to be countable from the deck. A check that cannot be
counted is not a check.

## What it cannot do

It cannot tell you the thesis is *right*. It can tell you when the thesis is
measuring nothing, measuring something the deck never attempted, grading a
creature deck on its mana base, or narrating the commander's oracle for a key
the goldfish cannot see on that card. Those are the failures that
look like success.
"""

from __future__ import annotations

import re
from typing import Any

from deckwright import sim

# A curve everyone clears says nothing about the deck; a curve nobody clears
# usually means the question was wrong, not that the deck is broken. Both
# readings lead to opposite advice, which is why they have to be told apart.
CEILING = 90.0
FLOOR = 10.0

# Conditions that are purely about mana. A deck can be graded on these, but a
# deck graded ONLY on these is being graded on its mana base rather than its plan.
MANA_KEYS = {"mana_available", "lands_in_play", "land_drops_this_turn",
             "extra_land_drops"}

# Roles the chosen strategy actually asked for. A payoff card should live here.
PLAN_ROLES = {"plan", "theme", "deepcut"}

# Thesis keys that need extractable engines in the 99 (or commander). Mana and
# commander_in_play are structural — always "supported" by the goldfish.
ENGINE_KEYS = {
    "life_gained",
    "life_drained",
    "cards_drawn",
    "creatures_in_play",
    "board_power",
    # payoffs_in_play is not here: mechanics(card, {name}) marks every card a
    # payoff of itself, so "support" would always pass. Alignment is the
    # payoff_alignment check.
}

# Note/label language that claims the *commander's* oracle did something the
# goldfish only measures via 99-card engines (or not at all).
_COMMANDER_STORY = re.compile(
    r"\b("
    r"flip|exchange|inversion|darkforce|"
    r"enters?(?:\s+the\s+battlefield)?|"
    r"etb|life totals?"
    r")\b",
    re.I,
)
_STORY_KEYS = {"life_gained", "life_drained", "cards_drawn"}


def _t5(curve: dict) -> float:
    """The turn-5 reading, which is what the UI shows."""
    pts = curve.get("points") or []
    return float(pts[4]) if len(pts) > 4 else 0.0


def spread(curves: list) -> dict:
    """Is the set of conditions informative, or does it measure nothing?"""
    vals = [_t5(c) for c in curves or []]
    if not vals:
        return {"ok": False, "reason": "no conditions were measured"}
    if all(v >= CEILING for v in vals):
        return {"ok": False, "reason":
                f"every condition clears {CEILING:.0f}% — this measures whether the deck "
                f"functions at all, not whether its plan comes together"}
    if all(v <= FLOOR for v in vals):
        return {"ok": False, "reason":
                f"no condition reaches {FLOOR:.0f}% — either the deck genuinely cannot do "
                f"this, or the thesis asked for something it never set out to do"}
    return {"ok": True, "reason": ""}


def payoff_alignment(payoff_cards: list, deck: list) -> dict:
    """Are the payoff cards the ones the strategy called for?

    They are already verified to exist in the deck. That is not the same as
    being on-plan: a thesis can name ten lands as its payoffs and pass.
    """
    roles = {c["name"]: c.get("role") for c in deck}
    named = [n for n in (payoff_cards or []) if n in roles]
    if not named:
        return {"ok": False, "on_plan": 0, "reason": "no payoff card is in the deck"}
    on_plan = sum(1 for n in named if roles[n] in PLAN_ROLES)
    share = on_plan / len(named)
    if share < 0.5:
        off = [n for n in named if roles[n] not in PLAN_ROLES][:4]
        return {"ok": False, "on_plan": on_plan, "reason":
                f"only {on_plan} of {len(named)} payoff cards are cards the strategy "
                f"asked for — the rest are support ({', '.join(off)})"}
    return {"ok": True, "on_plan": on_plan, "reason": ""}


def condition_focus(conditions: list) -> dict:
    """Is the deck being graded on its plan, or just on its mana base?"""
    keys = []
    for c in conditions or []:
        keys.extend((c.get("check") or {}).keys())
    if not keys:
        return {"ok": False, "reason": "no conditions to read"}
    mana_only = [k for k in keys if k in MANA_KEYS]
    if len(mana_only) == len(keys):
        return {"ok": False, "reason":
                "every condition is about mana — this grades the mana base, not the deck"}
    return {"ok": True, "reason": ""}


def _mechanics_of(card: dict) -> dict:
    name = card.get("name") or ""
    # Same text/type normalization as sim.run(commander_card=…).
    c = dict(card)
    if not (c.get("text") or "").strip() and c.get("oracle_text"):
        c["text"] = c["oracle_text"]
    if not (c.get("type") or "").strip() and c.get("type_line"):
        c["type"] = c["type_line"]
    return sim.mechanics(c, {name} if name else set())


def _contributes(key: str, m: dict) -> bool:
    """Whether this mechanics profile can move the thesis key in goldfish."""
    if key == "life_gained":
        return bool(m.get("life_gain") or m.get("life_gain_proxy_per_turn")
                    or m.get("lifegain_payoff"))
    if key == "life_drained":
        return bool(m.get("drain") or m.get("drain_amount")
                    or m.get("drain_proxy_per_turn"))
    if key == "cards_drawn":
        return bool(m.get("draws") or m.get("draw_upkeep")
                    or m.get("draw_proxy_per_turn") or m.get("flashback_draws"))
    if key == "creatures_in_play":
        return bool(m.get("bodies") or m.get("creature_tokens")
                    or m.get("creature_token_proxy_per_turn")
                    or m.get("creature_token_x_proxy"))
    if key == "board_power":
        return _contributes("creatures_in_play", m) or bool(m.get("anthem"))
    if key == "payoffs_in_play":
        return bool(m.get("payoff"))
    return True  # structural keys — not gated here


# Opponent-required language — still unmodelled.
_OPPONENT_STORY = re.compile(
    r"\b(exchange|life totals?)\b", re.I,
)


def key_support(conditions: list, deck: list,
                commander_card: dict | None = None) -> dict[str, Any]:
    """Do engine keys have at least one extractable contributor?

    a `life_drained` condition with no drain text is grading a
    question the goldfish will answer 0% forever. The commander on-cast modelling applies commander
    oracle on cast, so the commander counts as support when its mechanics
    contribute — except opponent-only effects (life exchange), which still
    extract as zeros.
    """
    keys: list[str] = []
    for c in conditions or []:
        keys.extend((c.get("check") or {}).keys())
    needed = [k for k in keys if k in ENGINE_KEYS]
    if not needed:
        return {"ok": True, "unsupported": [], "reason": ""}

    profiles = [_mechanics_of(c) for c in (deck or [])]
    if commander_card:
        profiles.append(_mechanics_of(commander_card))

    unsupported: list[str] = []
    for key in sorted(set(needed)):
        if not any(_contributes(key, m) for m in profiles):
            unsupported.append(key)
    if unsupported:
        shown = ", ".join(unsupported)
        return {
            "ok": False,
            "unsupported": unsupported,
            "reason": (
                f"condition key(s) {shown} have no card the goldfish can read "
                f"as contributing — lifelink/combat/opponent life-exchange do "
                f"not count. These percentages will sit near zero for the "
                f"wrong reason."
            ),
        }
    return {"ok": True, "unsupported": [], "reason": ""}


def commander_story_mismatch(conditions: list,
                             commander_card: dict | None) -> dict[str, Any]:
    """Catch notes that narrate commander oracle the goldfish still cannot see.

    on-cast extractable effects (draws, bodies, drain proxies) fire.
    Opponent life-exchange still does not. So "refuel off the flip" +
    `cards_drawn` is sound when the commander’s mechanics contribute draws;
    "life exchange padded you" + `life_gained` stays a mismatch when the
    commander has no extractable life_gain.
    """
    if not commander_card or not (commander_card.get("name")
                                  or commander_card.get("oracle_text")
                                  or commander_card.get("text")):
        return {"ok": True, "reason": ""}

    name = commander_card.get("name") or "the commander"
    cmdr_m = _mechanics_of(commander_card)
    problems: list[str] = []
    for c in conditions or []:
        check = c.get("check") or {}
        story = f"{c.get('label') or ''} {c.get('note') or ''}"
        if not _COMMANDER_STORY.search(story):
            continue
        for key in check:
            if key not in _STORY_KEYS:
                continue
            # Opponent-required claim for a key the commander cannot move.
            if _OPPONENT_STORY.search(story) and not _contributes(key, cmdr_m):
                problems.append(
                    f"“{c.get('label') or key}” narrates an opponent "
                    f"interaction as {key}, but the goldfish has no opponent "
                    f"— {name} does not contribute {key}"
                )
                continue
            if _contributes(key, cmdr_m):
                continue
            problems.append(
                f"“{c.get('label') or key}” narrates {name}’s oracle as "
                f"{key}, but that card’s extractable text does not move {key}"
            )
    if problems:
        return {"ok": False, "reason": problems[0] + "."}
    return {"ok": True, "reason": ""}


def stability(a: dict, b: dict) -> dict:
    """Do two theses for the same deck ask comparable questions?

    Cheapest and most revealing check in the file. If the same deck produces
    wildly different conditions run to run, the percentages are not
    reproducible, and every claim built on them is weaker than it looks.
    Belongs in the eval harness rather than the request path.
    """
    ka = {k for c in (a.get("conditions") or []) for k in (c.get("check") or {})}
    kb = {k for c in (b.get("conditions") or []) for k in (c.get("check") or {})}
    if not ka or not kb:
        return {"ok": False, "overlap": 0.0, "reason": "a run produced no conditions"}
    overlap = len(ka & kb) / len(ka | kb)
    if overlap < 0.5:
        return {"ok": False, "overlap": round(overlap, 2), "reason":
                f"two runs on the same deck agreed on {overlap:.0%} of what to measure"}
    return {"ok": True, "overlap": round(overlap, 2), "reason": ""}


def review(spec: dict, curves: list, deck: list,
           commander_card: dict | None = None) -> dict:
    """The verdict the UI shows. Never fatal — a suspect thesis is still shown,
    it is just no longer presented as fact."""
    conditions = (spec or {}).get("conditions")
    checks = [
        ("spread", spread(curves)),
        ("payoffs", payoff_alignment((spec or {}).get("payoff_cards"), deck)),
        ("focus", condition_focus(conditions)),
        ("support", key_support(conditions, deck, commander_card=commander_card)),
        ("commander_story",
         commander_story_mismatch(conditions, commander_card)),
    ]
    problems = [(name, r["reason"]) for name, r in checks if not r["ok"] and r["reason"]]
    return {
        "sound": not problems,
        "problems": [{"check": n, "detail": d} for n, d in problems],
        # What the reader should do with the numbers below, in one line.
        "caveat": "" if not problems else
                  "These percentages are measured correctly — but read them carefully: "
                  + problems[0][1],
    }
