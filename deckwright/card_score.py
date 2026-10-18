"""
card_score.py — pick cards by marginal simulation lift, not vibes.

CON-103: for each role, score candidates against the deck being built. The model
still proposes a reasonable shortlist; the sim ranks within it. Zero-score cards
(interaction, flavour) keep model order — they are not dropped for scoring zero.

Scores against a strategy-derived thesis condition with measurable headroom.
Saturated baselines (>90% or <10%) skip scoring rather than emitting +0.0 notes.
"""

from __future__ import annotations

import asyncio
import time
from functools import cmp_to_key
from typing import Any

from deckwright import curve
from deckwright import mana
from deckwright import overview
from deckwright import plan_graph
from deckwright import sim
from deckwright import strategy_steer
from deckwright import synergy
from deckwright import thesis_check

# Faster than the 1500-run build report; issue budgets ~30s across all roles.
SCORE_RUNS = 400
SAMPLE_SIZE = 20
# CON-225: judge at T10 so engine edges (CON-162) are visible; T5 muted them to Δ=0.
TARGET_TURN = 10

_BASIC = {
    "W": ("Plains", "{T}: Add {W}."),
    "U": ("Island", "{T}: Add {U}."),
    "B": ("Swamp", "{T}: Add {B}."),
    "R": ("Mountain", "{T}: Add {R}."),
    "G": ("Forest", "{T}: Add {G}."),
}


def _cmp_edge_votes(va: int | None, vb: int | None) -> int:
    """Compare STEER edge votes. 0 means omit / tie on this key.

    None = timing-unrepresentable (CON-222 no vote). Against a true orphan
    zero, None sorts higher so STEER does not punish timing as Shock. Against
    a positive edge count, None is equal — compete on cmc / model_rank only.
    """
    if va is None and vb is None:
        return 0
    if va is None:
        return 1 if vb == 0 else 0
    if vb is None:
        return -1 if va == 0 else 0
    if va == vb:
        return 0
    return 1 if va > vb else -1


def _is_land(card: dict) -> bool:
    return "land" in (card.get("type") or "").lower()


def _turn_pct(sim_out: dict, label: str, turn: int) -> float:
    for curve in sim_out.get("curves") or []:
        if curve.get("label") == label:
            pts = curve.get("points") or []
            if turn - 1 < len(pts):
                return float(pts[turn - 1])
    return 0.0


def provisional_conditions(brief: dict) -> list[dict]:
    """Strategy-aware thesis conditions for build-time scoring.

    Derived from brief.strategy and mechanics — the thesis runs after the build,
    so these are provisional until the real thesis arrives.
    """
    text = " ".join(filter(None, [
        brief.get("strategy") or "",
        brief.get("plan") or brief.get("angle") or "",
        brief.get("does") or "",
        " ".join(brief.get("mechanics") or []),
    ])).lower()

    conditions = [
        {"label": "Commander out", "note": "Your commander is on the battlefield.",
         "check": {"commander_in_play": True}},
    ]

    payoff_n = 3
    if any(w in text for w in ("go wide", "tokens", "swarm", "many creature")):
        payoff_n = 5
    elif any(w in text for w in ("haymaker", "bomb", "finisher", "one big")):
        payoff_n = 2
    conditions.append({
        "label": f"{payoff_n} payoffs",
        "note": f"{payoff_n} plan cards in play.",
        "check": {"payoffs_in_play": payoff_n},
    })

    high_curve = any(w in text for w in ("seven", "seven-drop", "7-drop",
                                         "ramp into", "haymaker", "expensive"))
    if high_curve:
        conditions.append({
            "label": "Seven mana", "note": "Seven mana sources on the battlefield.",
            "check": {"mana_available": 7},
        })
    elif any(w in text for w in ("draw", "hand", "hold", "refill", "accumulate")):
        conditions.append({
            "label": "Full hand", "note": "Five cards in hand.",
            "check": {"cards_in_hand": 5},
        })
    elif any(w in text for w in ("landfall", "extra land")):
        conditions.append({
            "label": "Extra land drop", "note": "More than one land drop per turn.",
            "check": {"extra_land_drops": 1},
        })
    else:
        conditions.append({
            "label": "Five mana", "note": "Five mana sources on the battlefield.",
            "check": {"mana_available": 5},
        })

    return conditions[:4]


def pick_scoring_target(baseline_out: dict, conditions: list[dict]) -> dict | None:
    """Pick the condition with measurable headroom at TARGET_TURN.

    Returns None when every condition is saturated (>=90%) or on the floor (<=10%).
    Prefers plan conditions over pure mana milestones.
    """
    by_label = {c["label"]: c for c in baseline_out.get("curves") or []}
    candidates: list[tuple[float, dict, float]] = []

    for cond in conditions:
        curve = by_label.get(cond["label"])
        if not curve:
            continue
        pct = _turn_pct({"curves": [curve]}, cond["label"], TARGET_TURN)
        if pct <= thesis_check.FLOOR or pct >= thesis_check.CEILING:
            continue
        keys = list((cond.get("check") or {}).keys())
        mana_only = bool(keys) and all(k in thesis_check.MANA_KEYS for k in keys)
        # Prefer conditions near 50% — most room to move in either direction.
        score = abs(50 - pct) + (30 if mana_only else 0)
        candidates.append((score, cond, pct))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _, cond, baseline_pct = candidates[0]
    return {"label": cond["label"], "baseline_pct": baseline_pct}


def _basic_placeholders(ci: str, count: int) -> list[dict]:
    fills = [(c, *_BASIC[c]) for c in ci if c in _BASIC] or [("C", "Wastes", "{T}: Add {C}.")]
    out = []
    for i in range(count):
        sym, name, text = fills[i % len(fills)]
        pm = [] if name == "Wastes" else [sym]
        out.append({
            "name": f"__scaffold_{name}_{i}",
            "type": f"Basic Land — {name}",
            "cmc": 0,
            "text": text,
            "produced_mana": pm,
            "cost": "",
            "role": "basic",
        })
    return out


def _role_counts(deck: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in deck:
        role = c.get("role") or "theme"
        counts[role] = counts.get(role, 0) + 1
    return counts


def build_scaffold(
    deck_so_far: list[dict],
    role_pools: dict[str, list[dict]],
    spell_roles: list[tuple[str, int, str]],
    current_role: str,
    current_target: int,
    seen: set[str],
    *,
    cmdr_card: dict,
    ci: str,
    land_pool: list[dict] | None = None,
) -> tuple[list[dict], int | None]:
    """Assemble a sim deck with placeholders for unfilled slots.

    Returns (scaffold, swap_idx) where swap_idx is the first placeholder slot for
    current_role — candidates are swapped in there for marginal scoring.
    """
    scaffold = [dict(c) for c in deck_so_far]
    in_deck = {c["name"] for c in scaffold}
    counts = _role_counts(scaffold)
    swap_idx: int | None = None

    for role, target, _ in spell_roles:
        have = counts.get(role, 0)
        need = max(0, target - have)
        if need == 0:
            continue
        pool = list(role_pools.get(role) or [])
        added = 0
        for c in pool:
            if c["name"] in in_deck or c["name"] in seen:
                continue
            if role != "lands" and _is_land(c):
                continue
            card = {**c, "role": role}
            if role == current_role and swap_idx is None:
                swap_idx = len(scaffold)
            scaffold.append(card)
            in_deck.add(c["name"])
            added += 1
            if added >= need:
                break

    total_lands, _ = mana.total_lands_target(scaffold, cmdr_card)
    land_have = sum(1 for c in scaffold if _is_land(c))
    land_need = max(0, total_lands - land_have)
    if land_need and (land_pool or current_role == "lands"):
        for c in land_pool or []:
            if land_need <= 0:
                break
            if c["name"] in in_deck or c["name"] in seen:
                continue
            card = {**c, "role": "lands"}
            if current_role == "lands" and swap_idx is None:
                swap_idx = len(scaffold)
            scaffold.append(card)
            in_deck.add(c["name"])
            land_need -= 1
    elif land_need:
        for c in _basic_placeholders(ci, land_need):
            if current_role == "lands" and swap_idx is None:
                swap_idx = len(scaffold)
            scaffold.append(c)
            in_deck.add(c["name"])

    while len(scaffold) < 60:
        scaffold.extend(_basic_placeholders(ci, 1))
    return scaffold, swap_idx


def sample_candidates(pool: list[dict], model_picks: list[dict],
                      sample_size: int = SAMPLE_SIZE) -> list[dict]:
    """Score the model shortlist; fall back to pool only when the model delivered nothing."""
    if model_picks:
        return model_picks[:sample_size]
    return pool[:sample_size]


def _score_candidate(
    scaffold: list[dict],
    swap_idx: int | None,
    candidate: dict,
    role: str,
    cmdr_cmc: int,
    conditions: list[dict],
    target_label: str,
    baseline_pct: float,
    runs: int,
) -> float:
    if swap_idx is None:
        return 0.0
    trial = list(scaffold)
    trial[swap_idx] = {**candidate, "role": role}
    payoffs = overview.payoff_names_from_deck(trial)
    out = sim.run(trial, cmdr_cmc, payoffs, conditions, runs=runs)
    pct = _turn_pct(out, target_label, TARGET_TURN)
    return round(pct - baseline_pct, 1)


def score_candidates_sync(
    scaffold: list[dict],
    swap_idx: int | None,
    candidates: list[dict],
    role: str,
    cmdr_cmc: int,
    *,
    conditions: list[dict] | None = None,
    runs: int = SCORE_RUNS,
) -> tuple[list[dict], dict | None]:
    """Score each candidate's marginal lift on the best measurable condition.

    Returns (scored rows, target) where target is None when all conditions
    are saturated and scoring was skipped.
    """
    conds = conditions or overview.BUILD_CONDITIONS
    payoffs = overview.payoff_names_from_deck(scaffold)
    baseline = sim.run(scaffold, cmdr_cmc, payoffs, conds, runs=runs)
    target = pick_scoring_target(baseline, conds)
    if not target:
        return [{"card": c, "delta_pct": 0.0, "baseline_pct": 0.0} for c in candidates], None

    label = target["label"]
    baseline_pct = target["baseline_pct"]
    scored = []
    for c in candidates:
        delta = _score_candidate(
            scaffold, swap_idx, c, role, cmdr_cmc, conds, label, baseline_pct, runs,
        )
        scored.append({"card": c, "delta_pct": delta, "baseline_pct": baseline_pct,
                       "condition": label})
    return scored, target


async def rank_for_role(
    deck_so_far: list[dict],
    pool: list[dict],
    role: str,
    target: int,
    model_picks: list[dict],
    cmdr_cmc: int,
    cmdr_card: dict,
    ci: str,
    role_pools: dict[str, list[dict]],
    spell_roles: list[tuple[str, int, str]],
    seen: set[str],
    *,
    brief: dict | None = None,
    land_pool: list[dict] | None = None,
    runs: int = SCORE_RUNS,
    strategy: dict | None = None,
) -> tuple[list[dict], dict[str, dict], int, dict]:
    """Return (chosen cards, sim_pick by name, latency_ms, score_info).

    score_info is {"skipped": True, "reason": "..."} when no condition had
    measurable headroom; otherwise {"skipped": False, "condition": label}.
    """
    t0 = time.perf_counter()
    candidates = sample_candidates(pool, model_picks)

    # CON-173: the graph decides which cards are ELIGIBLE; the sim still decides
    # how good they are. Gate only theme/plan -- ramp and removal earn their
    # slots on their own merits.
    synergy_dropped = 0
    synergy_gate_failed_open = False
    if role in synergy.GATED_ROLES and cmdr_card:
        kept = [c for c in candidates
                if synergy.eligible(c, cmdr_card, deck_so_far)]
        # Never empty the pool. If the gate would leave nothing, keep the
        # original list and say so -- an over-strict gate must not silently
        # produce a worse deck than no gate at all.
        if kept:
            synergy_dropped = len(candidates) - len(kept)
            candidates = kept
        else:
            # CON-188: callers must see fail-open. synergy_dropped stays 0 when
            # nothing was dropped; the flag is what distinguishes that from
            # "gate kept everyone because everyone was eligible."
            synergy_gate_failed_open = True
            print(f"  [synergy] {role}: gate would empty the pool — keeping all "
                  f"{len(candidates)} candidates")

    scaffold, swap_idx = build_scaffold(
        deck_so_far, role_pools, spell_roles, role, target, seen,
        cmdr_card=cmdr_card, ci=ci, land_pool=land_pool,
    )
    model_rank = {c["name"]: i for i, c in enumerate(model_picks)}
    # CON-139: score against the PLAN, not a keyword guess. plan_graph.derive()
    # knows the commander and emits conditions in live THESIS_KEYS — including
    # life_gained, which provisional_conditions() has no way to reach. Without
    # this a lifegain deck is scored on mana and the scorer has no reason to
    # prefer Well of Lost Dreams over Healing Salve.
    conditions = None
    derive_failed = False
    if cmdr_card:
        try:
            conditions = plan_graph.conditions_for_scoring(
                plan_graph.derive(brief or {}, cmdr_card))
        except Exception as e:
            derive_failed = True
            conditions = None
            print(f"  [plan-derive] {type(e).__name__}: {e} — scoring on provisional conditions")
    # Fall back only when there is no commander yet (or the derive failed) —
    # a missing plan is not a reason to score nothing.
    conditions = conditions or provisional_conditions(brief or {})
    payoffs = overview.payoff_names_from_deck(scaffold)

    baseline_out = await asyncio.to_thread(
        sim.run, scaffold, cmdr_cmc, payoffs, conditions, runs,
    )
    scoring = pick_scoring_target(baseline_out, conditions)
    ms = int((time.perf_counter() - t0) * 1000)

    if not scoring:
        spread = thesis_check.spread(baseline_out.get("curves") or [])
        reason = spread.get("reason") or "no condition had measurable headroom"
        # CON-214: when the sim cannot rank, edged cards still beat orphans —
        # same soft prefer as the scored path, never a weight. CON-175 curve
        # preference stays the next key so low-MV wishes still apply.
        pref = curve.preference(brief or {})
        cmdr_for_edges = cmdr_card or {}

        def _skip_cmp(a: dict, b: dict) -> int:
            # CON-222: timing multipliers with 0 material edges cast no edge
            # vote — omit that key rather than sorting them as orphans.
            if synergy.STEER:
                va = synergy.edge_steer_vote(a, cmdr_for_edges, deck_so_far)
                vb = synergy.edge_steer_vote(b, cmdr_for_edges, deck_so_far)
                edge = _cmp_edge_votes(va, vb)
                if edge:
                    return edge
            if strategy_steer.should_steer(strategy):
                sa = strategy_steer.steer_vote(a, strategy, deck_so_far)
                sb = strategy_steer.steer_vote(b, strategy, deck_so_far)
                steer = strategy_steer.cmp_votes(sa, sb)
                if steer:
                    return steer
            ca = curve.cmc_sort_key(a, pref)
            cb = curve.cmc_sort_key(b, pref)
            if ca != cb:
                return 1 if ca > cb else -1
            ra = -model_rank.get(a["name"], 999)
            rb = -model_rank.get(b["name"], 999)
            if ra != rb:
                return 1 if ra > rb else -1
            return 0

        ordered = sorted(model_picks, key=cmp_to_key(_skip_cmp), reverse=True)
        chosen = ordered[:target]
        cohesion_summary = None
        if cmdr_card and chosen:
            try:
                cohesion_summary = synergy.cohesion(
                    deck_so_far + chosen, cmdr_card)
            except Exception:
                cohesion_summary = None
        return chosen, {}, ms, {
            "skipped": True, "reason": reason,
            "synergy_dropped": synergy_dropped,
            "synergy_gate_failed_open": synergy_gate_failed_open,
            "derive_failed": derive_failed,
            "cohesion": cohesion_summary,
            "cohesion_steer": bool(synergy.STEER),
        }

    label = scoring["label"]
    baseline_pct = scoring["baseline_pct"]

    async def scored(cand: dict) -> dict:
        delta = await asyncio.to_thread(
            _score_candidate, scaffold, swap_idx, cand, role, cmdr_cmc,
            conditions, label, baseline_pct, runs,
        )
        return {"card": cand, "delta_pct": delta, "baseline_pct": baseline_pct,
                "condition": label}

    results = await asyncio.gather(*[scored(c) for c in candidates])
    # Tiebreak within a sim-score band. Most candidates score near-identically
    # (CON-159: recall mattered far more than ranking), so this is where the
    # tutorial's "not one synergy, but multiple overlapping synergies" lands.
    # BAND is a tiebreak WIDTH, not a weight — it decides when two scores count
    # as equal, never how much synergy is worth.
    #
    # CON-175: curve preference is the next tiebreak — mana value, not dollars.
    # Not a theme-role cmc filter (the curve spec forbids bolting that on). Same
    # shape as synergy's edge_count: a key inside the band, never a coefficient
    # added to the sim score.
    BAND = 0.5
    cmdr_for_edges = cmdr_card or {}
    curve_pref = curve.preference(brief or {})

    def _tiebreak_cmp(a: dict, b: dict) -> int:
        sa = round(a["delta_pct"] / BAND)
        sb = round(b["delta_pct"] / BAND)
        if sa != sb:
            return 1 if sa > sb else -1
        # CON-214: STEER off keeps orphans equal inside the band (tests).
        # CON-222: timing-unrepresentable zeros omit the edge key (no vote).
        if synergy.STEER:
            va = synergy.edge_steer_vote(
                a["card"], cmdr_for_edges, deck_so_far)
            vb = synergy.edge_steer_vote(
                b["card"], cmdr_for_edges, deck_so_far)
            edge = _cmp_edge_votes(va, vb)
            if edge:
                return edge
        if strategy_steer.should_steer(strategy):
            sa = strategy_steer.steer_vote(a["card"], strategy, deck_so_far)
            sb = strategy_steer.steer_vote(b["card"], strategy, deck_so_far)
            steer = strategy_steer.cmp_votes(sa, sb)
            if steer:
                return steer
        ca = curve.cmc_sort_key(a["card"], curve_pref)
        cb = curve.cmc_sort_key(b["card"], curve_pref)
        if ca != cb:
            return 1 if ca > cb else -1
        ra = -model_rank.get(a["card"]["name"], 999)
        rb = -model_rank.get(b["card"]["name"], 999)
        if ra != rb:
            return 1 if ra > rb else -1
        return 0

    results.sort(key=cmp_to_key(_tiebreak_cmp), reverse=True)
    chosen = [r["card"] for r in results[:target]]

    meta: dict[str, dict] = {}
    for r in results[:target]:
        d = r["delta_pct"]
        note = (f"Cut from {len(candidates)} candidates; "
                f"{'+' if d >= 0 else ''}{d}% to \"{label}\" by turn {TARGET_TURN}")
        meta[r["card"]["name"]] = {
            "delta_pct": d,
            "condition": label,
            "turn": TARGET_TURN,
            "baseline_pct": round(r["baseline_pct"], 1),
            "candidates_scored": len(candidates),
            "note": note,
        }

    cohesion_summary = None
    if cmdr_card and chosen:
        try:
            cohesion_summary = synergy.cohesion(deck_so_far + chosen, cmdr_card)
        except Exception:
            cohesion_summary = None

    ms = int((time.perf_counter() - t0) * 1000)
    return chosen, meta, ms, {
        "skipped": False,
        "condition": label,
        "synergy_dropped": synergy_dropped,
        "synergy_gate_failed_open": synergy_gate_failed_open,
        "derive_failed": derive_failed,
        "cohesion": cohesion_summary,
        "cohesion_steer": bool(synergy.STEER),
    }


def stamp_sim_pick(deck: list[dict], meta: dict[str, dict]) -> None:
    """Attach sim_pick to deck cards that were ranked this pass."""
    for c in deck:
        if c["name"] in meta:
            c["sim_pick"] = meta[c["name"]]
