"""CON-321 Gate 2 — audit a 99 against CommanderStrategy. No selection steer.

Membership of the deck list is never changed. Unknown capacity is not a
violation and not a silent pass (unknown ≠ zero).
"""

from __future__ import annotations

import re
from typing import Any

from deckwright import commander_strategy

# Coarse feeder detectors keyed by strategy input event.
_FEEDER_PATTERNS: dict[str, re.Pattern[str]] = {
    "gain_life": re.compile(
        r"you gain (?:\d+|that much) life|you gain life equal|\bgain \d+ life\b",
        re.I,
    ),
    "cast_noncreature": re.compile(
        r"noncreature spell|instant or sorcery|cast (?:an )?artifact|"
        r"cast (?:an )?enchantment",
        re.I,
    ),
    "cast_spell": re.compile(r"whenever you cast|cast a spell", re.I),
    "combat_damage": re.compile(
        r"deals combat damage|combat damage to (?:a )?player|"
        r"when(?:ever)? .+ attacks|when(?:ever)? you attack",
        re.I,
    ),
    "dies": re.compile(r"whenever .+ dies|when .+ dies|enrage", re.I),
    "draw": re.compile(r"draw (?:a |one |\d+ )?cards?|search your library", re.I),
    "upkeep": re.compile(r"at the beginning of (?:your )?upkeep", re.I),
    "end_step": re.compile(r"at the beginning of (?:your )?end step", re.I),
    "etb_creature": re.compile(
        r"enters? the battlefield|when .+ enters",
        re.I,
    ),
    "landfall": re.compile(
        r"landfall|when(?:ever)? a land (?:you control )?enters",
        re.I,
    ),
    "activate": re.compile(r"\{T\}:|\{[0-9WUBRGC]+\}\s*:|\bequip \{", re.I),
}

# Cards that *consume* an output kind (coarse Gate 2).
_CONSUMER_PATTERNS: dict[str, re.Pattern[str]] = {
    "tokens": re.compile(
        r"token|creature(?:s)? you control get|populate",
        re.I,
    ),
    "draw": re.compile(r"draw |card advantage|whenever you draw", re.I),
    "life": re.compile(
        r"whenever you gain life|lifegain|life you gained",
        re.I,
    ),
    "mana": re.compile(r"add {|mana pool|spend mana", re.I),
    "counters": re.compile(r"\+1/\+1 counter|counter on", re.I),
    "recur_to_battlefield": re.compile(
        r"from (?:your )?graveyard|reanimate|return .+ battlefield",
        re.I,
    ),
    "recur_to_hand": re.compile(
        r"from (?:your )?graveyard to (?:your )?hand|regrowth",
        re.I,
    ),
}


def _text(card: dict) -> str:
    return (card.get("text") or card.get("oracle_text") or "")


def _type_line(card: dict) -> str:
    return (card.get("type") or card.get("type_line") or "")


def _cmc(card: dict) -> float:
    try:
        return float(card.get("cmc") or 0)
    except (TypeError, ValueError):
        return 0.0


def _power(card: dict) -> float | None:
    """Creature power as float, or None when unread (* / missing)."""
    raw = card.get("power")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _static_life_magnitudes(deck: list[dict]) -> list[int]:
    """Static 'you gain N life' amounts from the 99. Ref magnitudes skipped."""
    amounts: list[int] = []
    for card in deck:
        t = _text(card)
        if re.search(r"would gain life|gains? no life instead", t, re.I):
            continue
        for m in re.finditer(r"you gain (\d+) life", t, re.I):
            amounts.append(int(m.group(1)))
        for m in re.finditer(r"\bgain (\d+) life\b", t, re.I):
            amounts.append(int(m.group(1)))
    return amounts


def _capacity(strategy: dict, deck: list[dict]) -> dict[str, Any]:
    """Event-capacity estimator for dynamic bounds. Unknown when unreadable."""
    for b in strategy.get("bounds") or []:
        bound = b.get("bound") or {}
        if bound.get("mode") != "event_capacity":
            continue
        cap = bound.get("capacity_of") or {}
        resource = (cap.get("resource") or "").lower()
        if resource == "experience":
            # Experience counters accumulate over the game; no static deck
            # magnitude. Unknown ≠ invent a typical-4.
            return {
                "status": "unknown",
                "reason": "experience capacity not estimable from the 99",
                "subject": b.get("subject"),
            }
        if resource != "life":
            return {"status": "unknown", "reason": "unsupported resource"}
        amounts = _static_life_magnitudes(deck)
        if not amounts:
            # Ref-only or no lifegain facts.
            return {
                "status": "unknown",
                "reason": "no static life magnitudes in the 99",
                "subject": b.get("subject"),
            }
        agg = cap.get("aggregator") or "max_static"
        value = max(amounts) if agg == "max_static" else sorted(amounts)[len(amounts) // 2]
        return {
            "status": "ok",
            "value": value,
            "aggregator": agg,
            "subject": b.get("subject"),
        }
    return {"status": "none"}


def _is_artifact(card: dict) -> bool:
    return "artifact" in _type_line(card).lower()


def _is_creature(card: dict) -> bool:
    return "creature" in _type_line(card).lower()


def _is_enchantment(card: dict) -> bool:
    return "enchantment" in _type_line(card).lower()


_MV_SUBJECTS = ("artifact_mv", "creature_mv", "enchantment_mv", "card_mv")


def _mv_subject_applies(
    card: dict,
    subject: str,
    excludes: list[str] | None = None,
) -> bool:
    """True when the card is in-scope for subject, honouring type excludes.

    excludes are type-line tokens (e.g. equipment, aura) from oracle phrasing
    like 'non-Equipment artifact' / 'non-Aura enchantment' (CON-334).
    """
    if subject == "artifact_mv":
        if not _is_artifact(card):
            return False
    elif subject == "creature_mv":
        if not _is_creature(card):
            return False
    elif subject == "enchantment_mv":
        if not _is_enchantment(card):
            return False
    elif subject == "card_mv":
        pass
    else:
        return False
    tl = _type_line(card).lower()
    for ex in excludes or []:
        token = (ex or "").strip().lower()
        if token and token in tl:
            return False
    return True


def _cmc_breaks_op(cmc: float, op: str, limit: float) -> bool:
    """True when cmc violates the bound. Honour op (leq ceiling / geq floor)."""
    op_l = (op or "leq").lower()
    if op_l == "geq":
        return cmc < limit
    return cmc > limit


def _power_breaks_op(power: float, op: str, limit: float) -> bool:
    op_l = (op or "leq").lower()
    if op_l == "geq":
        return power < limit
    return power > limit


def _is_pure_draw_spell(card: dict) -> bool | None:
    """True when oracle is draw-only; False when mixed; None when unread."""
    t = _text(card).strip()
    if not t:
        return None
    stripped = re.sub(r"\([^)]*\)", "", t).strip()
    if not stripped:
        return None
    if not re.search(r"\bdraw\b", stripped, re.I):
        return False
    remainder = re.sub(
        r"(?:target player draws?|draw) (?:a |one |two |three |that many |\d+ )?cards?\.?",
        "",
        stripped,
        flags=re.I,
    ).strip(" .\n")
    if not remainder:
        return True
    return False


def _static_mv_limit(bound: dict) -> float | None:
    if bound.get("mode") != "static":
        return None
    try:
        return float(bound["value"])
    except (KeyError, TypeError, ValueError):
        return None


def _produces_mana(card: dict) -> bool:
    """Coarse ramp detector for CON-335 bound-violation tagging."""
    t = _text(card)
    if re.search(r"\{T\}:\s*Add\b", t, re.I):
        return True
    if re.search(r"\bAdd \{[WUBRGC0-9]", t, re.I):
        return True
    try:
        from deckwright import sim
        return bool(sim.mechanics(card).get("produces"))
    except Exception:
        return False


def _bound_violation_kind(card: dict, subject: str, op: str) -> str:
    """Ramp vs payoff — cheap mana rocks are not 'wrong payoffs' (CON-335)."""
    if (op or "leq").lower() == "geq" and subject in _MV_SUBJECTS:
        if _produces_mana(card):
            return "ramp"
    return "payoff"


def card_bound_ok(card: dict, strategy: dict, deck_so_far: list[dict]) -> str:
    """Per-card bound check shared by audit and steer (CON-329 / CON-331).

    Returns 'ok', 'false', or 'unknown'. Unknown ≠ zero: no silent pass.
    """
    strategy = strategy or {}
    bounds = strategy.get("bounds") or []
    if not bounds:
        return "ok"

    cap = _capacity(strategy, list(deck_so_far or []))
    saw_unknown = False
    for b in bounds:
        bound = b.get("bound") or {}
        subject = b.get("subject") or ""
        op = (b.get("op") or "leq").lower()
        mode = bound.get("mode")

        excludes = b.get("excludes")
        if mode == "event_capacity" and subject in _MV_SUBJECTS:
            if not _mv_subject_applies(card, subject, excludes):
                continue
            if cap.get("status") == "unknown":
                saw_unknown = True
                continue
            if cap.get("status") != "ok":
                continue
            limit = float(cap["value"])
            if _cmc_breaks_op(_cmc(card), op, limit):
                return "false"
        elif mode == "static" and subject in _MV_SUBJECTS:
            if not _mv_subject_applies(card, subject, excludes):
                continue
            limit = _static_mv_limit(bound)
            if limit is None:
                saw_unknown = True
                continue
            if _cmc_breaks_op(_cmc(card), op, limit):
                return "false"
        elif mode == "static" and subject == "creature_power":
            if not _is_creature(card):
                continue
            limit = _static_mv_limit(bound)
            if limit is None:
                saw_unknown = True
                continue
            power = _power(card)
            if power is None:
                saw_unknown = True
                continue
            if _power_breaks_op(power, op, limit):
                return "false"
        elif mode == "static" and subject == "creature_subtype":
            want = str(bound.get("value") or "").lower()
            if not want:
                saw_unknown = True
                continue
            if not _is_creature(card):
                continue
            if want not in _type_line(card).lower():
                return "false"
        elif op == "forbid" and subject == "draw":
            pure = _is_pure_draw_spell(card)
            if pure is None:
                saw_unknown = True
            elif pure:
                return "false"
        else:
            # Unrecognised subject/op — do not silently pass as ok.
            saw_unknown = True

    if saw_unknown:
        return "unknown"
    return "ok"


def _feeds_input(card: dict, inp: dict) -> bool:
    event = inp.get("event") or ""
    filt = inp.get("filters") or {}
    sub = (filt.get("subtype") or "").lower()

    if event == "board_count" and sub:
        return _is_creature(card) and sub in _type_line(card).lower()

    if event == "cast_spell" and sub:
        if sub in _type_line(card).lower():
            return True
        return bool(re.search(
            rf"cast(?:s|ing)? (?:a |another )?{re.escape(sub)}",
            _text(card), re.I,
        ))

    pat = _FEEDER_PATTERNS.get(event)
    if not pat:
        return False
    t = _text(card)
    if not pat.search(t):
        # Subtype combat: a creature of the named type can be a structural feeder
        # even without the trigger text.
        if event == "combat_damage" and sub:
            return _is_creature(card) and sub in _type_line(card).lower()
        return False
    return True


def _consumes_output(card: dict, kind: str) -> bool:
    pat = _CONSUMER_PATTERNS.get(kind)
    if not pat:
        return False
    return bool(pat.search(_text(card)))


def audit(deck: list[dict], strategy: dict) -> dict[str, Any]:
    """Score the 99 against a CommanderStrategy. Never mutates deck."""
    deck = list(deck or [])
    strategy = strategy or {}
    status = strategy.get("status") or "unread"

    feeders: list[dict[str, str]] = []
    seen_f: set[str] = set()
    inputs = strategy.get("inputs") or []
    feeder_audit_skipped = not inputs
    for inp in inputs:
        for card in deck:
            name = card.get("name") or ""
            if not name or name in seen_f:
                continue
            if _feeds_input(card, inp):
                seen_f.add(name)
                feeders.append({
                    "name": name,
                    "event": inp.get("event") or "",
                })

    dead_outputs: list[dict[str, str]] = []
    for out in strategy.get("outputs") or []:
        kind = out.get("kind") or ""
        if not kind:
            continue
        if any(_consumes_output(c, kind) for c in deck):
            continue
        dead_outputs.append({"kind": kind})

    cap = _capacity(strategy, deck)
    bound_violations: list[dict[str, Any]] = []
    bound_unknown: list[dict[str, Any]] = []

    for b in strategy.get("bounds") or []:
        bound = b.get("bound") or {}
        subject = b.get("subject") or ""
        op = b.get("op") or "leq"
        mode = bound.get("mode")
        excludes = b.get("excludes")
        if mode == "event_capacity":
            if subject not in _MV_SUBJECTS:
                continue
            if cap.get("status") == "unknown":
                bound_unknown.append({
                    "subject": subject,
                    "reason": cap.get("reason") or "capacity unknown",
                })
                continue
            if cap.get("status") != "ok":
                continue
            limit = float(cap["value"])
            for card in deck:
                if not _mv_subject_applies(card, subject, excludes):
                    continue
                cmc = _cmc(card)
                if _cmc_breaks_op(cmc, op, limit):
                    bound_violations.append({
                        "name": card.get("name") or "",
                        "cmc": cmc,
                        "limit": limit,
                        "subject": subject,
                        "op": op,
                        "kind": _bound_violation_kind(card, subject, op),
                    })
        elif mode == "static" and subject in _MV_SUBJECTS:
            limit = _static_mv_limit(bound)
            if limit is None:
                bound_unknown.append({
                    "subject": subject,
                    "reason": "static mv value missing",
                })
                continue
            for card in deck:
                if not _mv_subject_applies(card, subject, excludes):
                    continue
                cmc = _cmc(card)
                if _cmc_breaks_op(cmc, op, limit):
                    bound_violations.append({
                        "name": card.get("name") or "",
                        "cmc": cmc,
                        "limit": limit,
                        "subject": subject,
                        "op": op,
                        "kind": _bound_violation_kind(card, subject, op),
                    })
        elif mode == "static" and subject == "creature_subtype":
            want = str(bound.get("value") or "").lower()
            if not want:
                bound_unknown.append({
                    "subject": subject,
                    "reason": "subtype value missing",
                })
                continue
            for card in deck:
                if not _is_creature(card):
                    continue
                if want not in _type_line(card).lower():
                    bound_violations.append({
                        "name": card.get("name") or "",
                        "subject": subject,
                        "op": op,
                        "want": want,
                        "kind": "payoff",
                    })
        elif mode == "static" and subject == "creature_power":
            limit = _static_mv_limit(bound)
            if limit is None:
                bound_unknown.append({
                    "subject": subject,
                    "reason": "static power value missing",
                })
                continue
            for card in deck:
                if not _is_creature(card):
                    continue
                power = _power(card)
                if power is None:
                    bound_unknown.append({
                        "subject": subject,
                        "reason": f"power unread on {card.get('name') or '?'}",
                    })
                    continue
                if _power_breaks_op(power, op, limit):
                    bound_violations.append({
                        "name": card.get("name") or "",
                        "power": power,
                        "limit": limit,
                        "subject": subject,
                        "op": op,
                        "kind": "payoff",
                    })
        elif (op or "").lower() == "forbid" and subject == "draw":
            for card in deck:
                pure = _is_pure_draw_spell(card)
                if pure is None:
                    bound_unknown.append({
                        "subject": subject,
                        "reason": f"draw unread on {card.get('name') or '?'}",
                    })
                elif pure:
                    bound_violations.append({
                        "name": card.get("name") or "",
                        "subject": subject,
                        "op": "forbid",
                        "kind": "payoff",
                    })

    payoff_violations = [
        v for v in bound_violations if v.get("kind") != "ramp"
    ]
    ramp_violations = [
        v for v in bound_violations if v.get("kind") == "ramp"
    ]

    honesty = None
    if status == "unread":
        honesty = strategy.get("honesty") or commander_strategy._HONESTY_UNREAD
    elif status == "partial":
        honesty = strategy.get("honesty")

    return {
        "strategy_status": status,
        "feeders": feeders,
        "feeder_count": len(feeders),
        "feeder_audit_skipped": feeder_audit_skipped,
        "dead_outputs": dead_outputs,
        "bound_violations": bound_violations,
        "violation_count": len(bound_violations),
        "bound_violations_payoff": payoff_violations,
        "violation_count_payoff": len(payoff_violations),
        "bound_violations_ramp": ramp_violations,
        "violation_count_ramp": len(ramp_violations),
        "bound_unknown": bound_unknown,
        "unknown_count": len(bound_unknown),
        "capacity": cap,
        "unscoreable": list(strategy.get("unscoreable") or ["soft_combo", "hidden_power"]),
        "rate": strategy.get("rate") or {},
        "primary_axis": strategy.get("primary_axis") or "mixed",
        "honesty": honesty,
        "steer": _audit_steer_flag(strategy),
    }


def _audit_steer_flag(strategy: dict) -> bool:
    try:
        from deckwright import strategy_steer
        return strategy_steer.steer_enabled() and strategy.get("status") != "unread"
    except Exception:
        return False


def attach_clarity(out: dict, *, enabled: bool) -> dict:
    """Attach commander_strategy + strategy_audit. Never changes deck names.

    When enabled is False, leaves out untouched (no strategy keys).
    """
    if not enabled:
        return out
    cmdr = out.get("commander_card") or {}
    # Partner: Gate 2 reads the primary commander only (clarity slice).
    strategy = commander_strategy.derive(cmdr)
    deck = out.get("deck") or []
    report = audit(deck, strategy)
    out["commander_strategy"] = strategy
    out["strategy_audit"] = report
    return out


def clarity_enabled() -> bool:
    """True when green is running candidate B or C (clarity / later steer)."""
    try:
        from deckwright import instance
        return instance.candidate() in ("B", "C")
    except Exception:
        return False
