"""
suggest.py — goal-scoped swap plans grounded in mechanic relations (CON-122/134).

The model does not name cards. We pick cuts/adds from the saved deck's verified
role_pools, scored by whether a card hits the goal's families/signals — expanded
through mechanics.related / expand_goal_families so "more tokens" also wants
go-wide payoffs that spend those tokens.

Plans are proposals only. Caller stages them into pending (CON-126); nothing
mutates the 99 here.
"""

from __future__ import annotations

from typing import Any

from deckwright import mechanics
from deckwright import substitution
from deckwright import swap

# Goals are mechanic plans, not vibes. Each seed family/signal is expanded
# along the taxonomy relation graph before scoring cards.
GOALS: dict[str, dict[str, Any]] = {
    "more_interaction": {
        "label": "More interaction",
        "families": ["interaction", "protection"],
        "signals": ["counterspell"],
        "prefer_add_roles": ["spot", "wipe"],
        "prefer_cut_roles": ["deepcut", "theme"],
    },
    "more_aggro": {
        "label": "More aggressive",
        "families": ["attacking_triggers", "saboteur", "go_wide_payoffs", "combat_timing"],
        "signals": ["creature_tokens", "go_wide_payoff"],
        "prefer_add_roles": ["plan", "theme"],
        "prefer_cut_roles": ["wipe", "deepcut"],
    },
    "faster_mana": {
        "label": "Faster mana",
        "families": ["alternate_costs"],
        "signals": ["resource_tokens"],
        "prefer_add_roles": ["ramp"],
        "prefer_cut_roles": ["deepcut", "theme"],
    },
    "more_tokens_wide": {
        "label": "Wider token board",
        "families": ["tokens", "go_wide_payoffs"],
        "signals": ["creature_tokens", "go_wide_payoff", "token_create"],
        "prefer_add_roles": ["plan", "theme"],
        "prefer_cut_roles": ["deepcut"],
    },
    "more_tokens_tall": {
        "label": "Taller threats",
        "families": ["tokens", "go_tall_payoffs", "counters_plus"],
        "signals": ["creature_tokens", "go_tall_payoff"],
        "prefer_add_roles": ["plan", "theme"],
        "prefer_cut_roles": ["deepcut"],
    },
    "more_lifegain": {
        "label": "More lifegain / drain",
        "families": ["life_math", "damage_modification"],
        "signals": ["lifegain_source", "lifegain_trigger", "drain"],
        "keywords": ["Lifelink"],
        "prefer_add_roles": ["plan", "theme"],
        "prefer_cut_roles": ["deepcut"],
    },
    "more_graveyard": {
        "label": "More graveyard value",
        "families": ["cast_from_graveyard", "return_from_graveyard",
                     "thresholds_graveyard", "hand_abilities"],
        "signals": [],
        "prefer_add_roles": ["plan", "theme"],
        "prefer_cut_roles": ["deepcut"],
    },
    "beat_aggro": {
        "label": "Answer aggro",
        "families": ["interaction", "protection"],
        "signals": ["counterspell"],
        "prefer_add_roles": ["wipe", "spot"],
        "prefer_cut_roles": ["deepcut"],
    },
}


def goal_spec(goal: str, families: dict | None = None) -> dict[str, Any] | None:
    """Resolve a goal id into expanded families + signals."""
    raw = GOALS.get(goal)
    if not raw:
        return None
    families = families if families is not None else mechanics.load_families()
    expanded = mechanics.expand_goal_families(list(raw.get("families") or []),
                                             families, depth=1)
    return {
        "goal": goal,
        "label": raw["label"],
        "families": expanded,
        "signals": list(raw.get("signals") or []),
        "keywords": list(raw.get("keywords") or []),
        "prefer_add_roles": list(raw.get("prefer_add_roles") or []),
        "prefer_cut_roles": list(raw.get("prefer_cut_roles") or []),
    }


def _score_card(card: dict, spec: dict, *, want: bool) -> int:
    """Higher is better when want=True (adds); when want=False, higher = better cut."""
    hits = mechanics.card_plan_hits(card)
    fam_hit = len(set(hits["families"]) & set(spec["families"]))
    sig_hit = len(set(hits["signals"]) & set(spec["signals"]))
    kw_hit = len({k.lower() for k in hits["keywords"]}
                 & {k.lower() for k in spec.get("keywords") or []})
    on_plan = fam_hit + sig_hit + kw_hit
    if want:
        return on_plan
    # Cuts: prefer cards that miss the goal entirely.
    return 0 if on_plan else 1


def _is_basic(card: dict) -> bool:
    t = (card.get("type") or "").lower()
    return "basic" in t and "land" in t


def plan_swaps(data: dict, goal: str, *,
               count: int = 5,
               families: dict | None = None) -> dict[str, Any]:
    """Build up to `count` verified {cut, add, reason} proposals.

    Adds must appear in role_pools (Scryfall-verified). Invented names cannot
    enter — there is no path that writes a name that wasn't in a pool.
    """
    spec = goal_spec(goal, families)
    if not spec:
        return {"ok": False, "error": f"unknown goal: {goal}",
                "goals": sorted(GOALS)}

    deck = list(data.get("deck") or [])
    role_pools = data.get("role_pools") or {}
    if not deck:
        return {"ok": False, "error": "deck is empty", "goal": spec, "suggestions": []}
    if not role_pools:
        return {"ok": False,
                "error": "no role_pools — rebuild to enable suggestions",
                "goal": spec, "suggestions": []}

    ci = ""
    cmdr = data.get("commander_card") or {}
    if cmdr.get("color_identity"):
        ci = "".join(cmdr["color_identity"])

    deck_names = {c.get("name") for c in deck}

    # Rank cuts: off-goal nonbasics in preferred cut roles first.
    cut_candidates = []
    for card in deck:
        if _is_basic(card):
            continue
        role = card.get("role") or "theme"
        if substitution.REPLACEABILITY.get(role, "flexible") == "core":
            continue
        score = _score_card(card, spec, want=False)
        role_bonus = 1 if role in spec["prefer_cut_roles"] else 0
        cut_candidates.append((score + role_bonus, role, card))
    cut_candidates.sort(key=lambda x: (-x[0], x[2].get("name") or ""))

    suggestions = []
    used_adds: set[str] = set()
    used_cuts: set[str] = set()

    for _, role, cut in cut_candidates:
        if len(suggestions) >= count:
            break
        if cut.get("name") in used_cuts:
            continue
        pool = swap.pool_for_role(role_pools, role)
        # Prefer same role; if empty, try preferred add roles.
        roles_to_try = [role] + [r for r in spec["prefer_add_roles"] if r != role]
        add_card = None
        add_role = role
        for try_role in roles_to_try:
            try_pool = swap.pool_for_role(role_pools, try_role) if try_role != role else pool
            ranked = []
            for cand in try_pool:
                name = cand.get("name")
                if not name or name in deck_names or name in used_adds:
                    continue
                if not substitution._in_color_identity(cand, ci):
                    continue
                sc = _score_card(cand, spec, want=True)
                if sc <= 0:
                    continue
                role_bonus = 1 if try_role in spec["prefer_add_roles"] else 0
                ranked.append((sc + role_bonus, cand))
            ranked.sort(key=lambda x: (-x[0], x[1].get("name") or ""))
            if ranked:
                add_card = ranked[0][1]
                add_role = try_role
                break
        if not add_card:
            continue

        # Final verifier: add must be findable in a pool by exact name.
        verified = swap.find_in_pool(add_card["name"],
                                     swap.pool_for_role(role_pools, add_role))
        if not verified:
            continue

        hits_add = mechanics.card_plan_hits(verified)
        hits_cut = mechanics.card_plan_hits(cut)
        reason = (
            f"Cuts {cut['name']} (misses {spec['label'].lower()}); "
            f"adds {verified['name']} for "
            + (", ".join(hits_add["signals"] or hits_add["families"] or ["plan fit"])
               or "plan fit")
            + "."
        )
        suggestions.append({
            "cut": cut["name"],
            "add": verified["name"],
            # Persist the pool card so approve can apply without re-reading pools.
            "add_card": verified,
            "role": add_role,
            "reason": reason,
            "cut_hits": hits_cut,
            "add_hits": hits_add,
            "query": None,  # pool-sourced; no new Scryfall query invented here
        })
        used_adds.add(verified["name"])
        used_cuts.add(cut["name"])

    return {
        "ok": True,
        "goal": spec,
        "suggestions": suggestions,
        "count": len(suggestions),
    }
