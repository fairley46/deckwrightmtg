"""
plan_graph.py — deterministic plan graph from brief + commander.

Joins interrogation brief fields with commander oracle via sim.mechanics() and
the mechanic family taxonomy. No model call — fixture-testable and repeatable.

Shape: docs/PLAN-GRAPH-SCHEMA.md
"""

from __future__ import annotations

import re
from typing import Any

from deckwright import curve
from deckwright import mechanics
from deckwright import sim
from deckwright import synergy

# Commander oracle that fires once per combat (or on attack). Extra combats
# multiply these activations — a plan fact, not a synergy object.
# Include "whenever you attack" (collective), not only "whenever ~ attacks".
_COMBAT_ACTIVATION_RE = re.compile(
    r"at the beginning of (?:each |your )?combat"
    r"|whenever you attack\b"
    r"|whenever .{0,60}attacks"
    r"|when .{0,60}attacks",
    re.IGNORECASE,
)

# Roles on the 99 that can carry plan payoffs when glue is empty/unknown.
_PLAN_PAYOFF_ROLES = ("plan", "theme", "deepcut")

PLAN_VERSION = 1

# oracle_signals family names that are not commitment-template keys.
_FAMILY_ALIASES = {
    "counters": "counters_plus",
    "combat_triggers": "attacking_triggers",
    "recursion": "return_from_graveyard",
    "spellslinger": "when_you_cast",
}

# Brief phrase → seed family ids. Explicit match only — relation expand was
# was removed because it invented commitments with no basis in the
# commander or the brief.
_PHRASE_FAMILIES: dict[str, tuple[str, ...]] = {
    "lifegain": ("life_math",),
    "life gain": ("life_math",),
    "drain": ("life_math",),
    "lifelink": ("life_math", "damage_modification"),
    "go wide": ("tokens", "go_wide_payoffs"),
    "tokens": ("tokens",),
    "token": ("tokens",),
    "treasure": ("tokens",),
    "aggro": ("attacking_triggers", "go_wide_payoffs"),
    "attack": ("attacking_triggers", "saboteur"),
    "sacrifice": ("dies_triggers",),
    "aristocrats": ("dies_triggers",),
    "graveyard": ("cast_from_graveyard", "thresholds_graveyard"),
    "flashback": ("cast_from_graveyard",),
    "reanimate": ("return_from_graveyard",),
    "mill": ("library_manipulation",),
    "ramp": ("alternate_costs",),
    "landfall": ("etb_triggers",),
    "counters": ("counters_plus", "go_tall_payoffs"),
    "control": ("interaction",),
    "counterspell": ("interaction",),
    "draw": ("library_manipulation",),
    "spellslinger": ("when_you_cast", "spell_cast_triggers"),
    "combo": ("when_you_cast",),
}

# Family → commitment floor on the 99 (id, label, role, min, why).
_COMMITMENT_TEMPLATES: dict[str, tuple[str, str, str, int, str]] = {
    "life_math": (
        "lifegain_density",
        "enough lifegain to turn on the payoffs",
        "theme",
        10,
        "The payoffs are dead cards without triggers to feed them.",
    ),
    "tokens": (
        "token_density",
        "enough token makers for the board to matter",
        "theme",
        12,
        "Width needs bodies to spend before payoffs can fire.",
    ),
    "go_wide_payoffs": (
        "wide_payoff_density",
        "payoffs that scale with board width",
        "plan",
        6,
        "Tokens without a payoff are just chump blockers.",
    ),
    "cast_from_graveyard": (
        "graveyard_density",
        "enough graveyard fuel and recursion",
        "theme",
        10,
        "Yard plans stall when nothing fills the graveyard or comes back.",
    ),
    "thresholds_graveyard": (
        "graveyard_density",
        "enough graveyard fuel and recursion",
        "theme",
        10,
        "Yard plans stall when nothing fills the graveyard or comes back.",
    ),
    "return_from_graveyard": (
        "graveyard_density",
        "enough graveyard fuel and recursion",
        "theme",
        10,
        "Yard plans stall when nothing fills the graveyard or comes back.",
    ),
    "dies_triggers": (
        "sacrifice_density",
        "outlets and death triggers that close the loop",
        "plan",
        8,
        "Sacrifice payoffs need fodder and a way to sacrifice.",
    ),
    "attacking_triggers": (
        "combat_piece_density",
        "creatures and triggers that reward attacking",
        "theme",
        10,
        "Aggro plans need bodies that connect or trigger on attack.",
    ),
    "library_manipulation": (
        "dig_density",
        "enough card selection and draw to find payoffs",
        "draw",
        7,
        "Grindy plans need to see the right cards more than once.",
    ),
    # counter producers had no commitment template, so Hei Bai's only
    # payoff never became a floor on the 99.
    "counters_plus": (
        "counter_payoff_density",
        "plus-one counters and payoffs that spend them",
        "plan",
        8,
        "A commander whose payoff is counters is dead without counter synergies.",
    ),
    "go_tall_payoffs": (
        "counter_payoff_density",
        "plus-one counters and payoffs that spend them",
        "plan",
        8,
        "A commander whose payoff is counters is dead without counter synergies.",
    ),
    # Dedicated key — not etb_triggers. Landfall briefs map to etb_triggers and
    # must not inherit a blink floor; re-entry is for leaves/self-ETB commanders.
    "reentry": (
        "reentry_density",
        "ways to re-enter and retrigger enters abilities",
        "plan",
        6,
        "Enters triggers only fire again if the permanent leaves and comes back.",
    ),
}

_WEIGHT_ORDER = {"core": 0, "supporting": 1, "nice": 2}


def _brief_text(brief: dict) -> str:
    return " ".join(filter(None, [
        brief.get("strategy") or "",
        brief.get("plan") or brief.get("angle") or "",
        brief.get("does") or "",
        brief.get("playstyle") or "",
        " ".join(str(m) for m in (brief.get("mechanics") or [])),
    ])).lower()


def _brief_fields_used(brief: dict) -> list[str]:
    fields = []
    for key in ("strategy", "mechanics", "playstyle", "theme", "angle", "plan",
                "strategy_shape"):
        val = brief.get(key)
        if val:
            fields.append(key)
    return fields


def _normalize_family(fam: str) -> str:
    return _FAMILY_ALIASES.get(fam, fam)


def _explicit_brief_families(text: str, mechanics_list: list[str]) -> list[str]:
    """Phrase/mechanic hits only — no relation expand.

    dies_triggers→life_math used to invent a lifegain commitment for a
    sacrifice brief that never said lifegain. A fallback is not a match; only
    words present in the brief count as brief basis.
    """
    families_data = mechanics.load_families()
    seeds: set[str] = set()
    haystack = text
    for mech in mechanics_list:
        token = str(mech).strip().lower()
        if token:
            haystack += f" {token}"
            meta = mechanics.mechanic_meta(mech, families_data)
            if meta.get("family"):
                seeds.add(_normalize_family(meta["family"]))
    for phrase, fams in _PHRASE_FAMILIES.items():
        if phrase in haystack:
            seeds.update(fams)
    for sig_name, body in (families_data.get("oracle_signals") or {}).items():
        if sig_name.replace("_", " ") in haystack or sig_name in haystack:
            if isinstance(body, dict) and body.get("family"):
                seeds.add(_normalize_family(body["family"]))
    return sorted(seeds)


def _families_from_demand(commander_card: dict) -> list[str]:
    """Commitment families implied by synergy.demand_profile(commander).

    The commander drives. Signals/objects map directly — relation expand is not
    applied here, because dies_triggers→life_math is exactly how Hei Bai got a
    lifegain plan with no life-related oracle text.
    """
    demand = synergy.demand_profile(commander_card)
    if demand["status"] == synergy.UNKNOWN:
        return []
    signals_meta = synergy.load_signals()
    out: set[str] = set()

    # Signals handled specially below (enters/landfall must not collapse).
    skip = {"enters", "land_trigger", "blink"}
    for sig in demand["signals"]:
        if sig in skip:
            continue
        meta = signals_meta.get(sig) or {}
        fam = meta.get("family")
        if fam:
            out.add(_normalize_family(fam))

    if "+1/+1 counter" in demand["produces"]:
        out.update(("counters_plus", "go_tall_payoffs"))
    if "life" in demand["produces"] or "life" in demand["consumes"]:
        out.add("life_math")

    if _needs_sac_fodder(demand):
        out.add("dies_triggers")
        # tokens stays OUT of families here. Putting it in flips conditions to
        # board_width for every sac outlet (Ayli lost life_engine_online that
        # way). Fodder is added as a commitment-only family in
        # _build_commitments.

    # Re-entry: leaving the battlefield is the tell. A landfall "enters" signal
    # is about lands, not blinking the commander back.
    if "leaves_battlefield" in demand["signals"] or (
        "enters" in demand["signals"] and "land_trigger" not in demand["signals"]
    ):
        out.add("reentry")

    if "attacks" in demand["signals"]:
        out.add("attacking_triggers")

    return sorted(out)


def _needs_sac_fodder(demand: dict) -> bool:
    """Commander eats creatures/artifacts and has a way to spend them."""
    if demand.get("status") == synergy.UNKNOWN:
        return False
    if not (demand.get("consumes") or set()) & {"creature", "artifact"}:
        return False
    sigs = set(demand.get("signals") or [])
    return bool(
        sigs & {"sacrifice_outlet", "dies_trigger"}
        or "sacrifice" in (demand.get("consumes") or set())
    )


def _basis_families(brief: dict, commander_card: dict | None) -> list[str]:
    """Commander demand first; brief theme modulates; neither may invent.

    Union of demand_profile families and explicit brief phrases. No relation
    expand — that path fabricated lifegain for Hei Bai.

    a chosen way_to_play (playstyles.json id) adds that playstyle's
    signal families so switching aristocrats↔blink changes commitments.
    """
    text = _brief_text(brief)
    mech_list = [str(m) for m in (brief.get("mechanics") or [])]
    brief_fams = set(_explicit_brief_families(text, mech_list))
    brief_fams.update(_families_from_way(brief))
    if commander_card:
        return sorted(set(_families_from_demand(commander_card)) | brief_fams)
    return sorted(brief_fams)


def _families_from_way(brief: dict) -> set[str]:
    """Families implied by brief.way_to_play — vocabulary playstyles only."""
    from deckwright import playstyles  # local; plan_graph stays import-light at module load

    way = brief.get("way_to_play") or brief.get("chosen_playstyle")
    if not isinstance(way, str) or not way.strip():
        return set()
    vocab = playstyles.load_playstyles()
    entry = vocab.get(way.strip())
    if not entry:
        # Unknown id is not a match — do not invent a family.
        return set()
    det = entry.get("detect") or {}
    signals = (
        list(det.get("requires") or [])
        + list(det.get("requires_any") or [])
        + list(det.get("boosts") or [])
    )
    signals_meta = synergy.load_signals()
    out: set[str] = set()
    for sig in signals:
        meta = signals_meta.get(sig) or {}
        fam = meta.get("family")
        if fam:
            out.add(_normalize_family(fam))
    return out


def _commander_turn(cmdr_mech: dict) -> int:
    cmc = int(cmdr_mech.get("cmc") or 0)
    return min(8, max(5, 3 + cmc))


def _payoff_count(text: str, cmdr_mech: dict) -> int:
    if any(w in text for w in ("go wide", "tokens", "swarm", "many creature")):
        return 5
    if any(w in text for w in ("haymaker", "bomb", "finisher", "one big")):
        return 2
    if cmdr_mech.get("bodies", 0) > 1 or cmdr_mech.get("anthem", 0) > 0:
        return 4
    return 3


def _build_conditions(
    brief: dict,
    text: str,
    families: list[str],
    cmdr_card: dict | None,
    cmdr_mech: dict | None,
) -> list[dict]:
    cmdr_mech = cmdr_mech or {}
    payoff_n = _payoff_count(text, cmdr_mech)
    cmdr_turn = _commander_turn(cmdr_mech) if cmdr_card else 6

    out: list[dict] = [{
        "id": "commander_out",
        "label": "Commander is out",
        "why": "Nothing else in the plan works until the commander has resolved and stuck.",
        "check": {"commander_in_play": True},
        "by_turn": cmdr_turn,
        "weight": "core",
    }, {
        "id": "engine_online",
        "label": f"{payoff_n} payoffs online",
        "why": "One payoff is a card. Two is an engine.",
        "check": {"payoffs_in_play": payoff_n},
        "by_turn": 5,
        "weight": "core",
    }]

    high_curve = any(w in text for w in (
        "seven", "seven-drop", "7-drop", "ramp into", "haymaker", "expensive",
    ))
    wants_draw = any(w in text for w in (
        "draw", "hand", "hold", "refill", "accumulate", "card advantage",
    ))
    wants_land = any(w in text for w in ("landfall", "extra land"))
    wants_wide = any(w in text for w in ("go wide", "tokens", "swarm")) or (
        "tokens" in families or "go_wide_payoffs" in families
    )

    if cmdr_mech.get("draw_upkeep") or cmdr_mech.get("draw", 0) >= 4:
        wants_draw = True

    if cmdr_mech.get("drop", 0) > 0 or wants_land:
        out.append({
            "id": "extra_land_online",
            "label": "Extra land drop online",
            "why": "Landfall and ramp plans need more than one land drop per turn.",
            "check": {"extra_land_drops": 1},
            "by_turn": 6,
            "weight": "supporting",
        })
    elif wants_draw:
        threshold = 6 if "accumulate" in text or "control" in text else 4
        out.append({
            "id": "draw_engine",
            "label": f"{threshold} extra cards drawn",
            "why": "Card advantage has to clear setup cost before it counts as an engine.",
            "check": {"cards_drawn": threshold},
            "by_turn": 10,
            "weight": "supporting",
        })
    elif wants_wide:
        bodies = 5 if payoff_n >= 5 else 4
        out.append({
            "id": "board_width",
            "label": f"{bodies} creatures on board",
            "why": "Go-wide plans need bodies before payoffs can matter.",
            "check": {"creatures_in_play": bodies},
            "by_turn": 6,
            "weight": "supporting",
        })
    elif high_curve:
        out.append({
            "id": "seven_mana",
            "label": "Seven mana available",
            "why": "Haymakers need the mana to cast them on curve.",
            "check": {"mana_available": 7},
            "by_turn": 7,
            "weight": "supporting",
        })
    elif cmdr_mech.get("anthem", 0) > 0:
        out.append({
            "id": "board_power",
            "label": "Board power online",
            "why": "Anthem commanders need bodies that survive long enough to pump.",
            "check": {"board_power": 8},
            "by_turn": 6,
            "weight": "supporting",
        })
    elif "life_math" in families and cmdr_mech.get("lifegain_payoff", 0) > 0:
        # the commander IS the payoff (Vito, Heliod, Aerith). The deck
        # does not need more payoffs — it needs gain EVENTS to feed this one.
        # Vito drains on every gain, so many small triggers beat one big gain,
        # and the condition has to count events rather than a life total.
        repeating = cmdr_mech.get("drain_proxy_per_turn", 0) > 0
        out.append({
            "id": "gain_events_online",
            "label": "12 life gained" if repeating else "10 life gained",
            "why": ("The commander converts every gain, so the deck wants many "
                    "small triggers rather than one big gain."),
            "check": {"life_gained": 12 if repeating else 10},
            "by_turn": 6,
            "weight": "core",
        })
    elif "life_math" in families:
        # lifegain perception landed and life_gained /
        # life_drained are live THESIS_KEYS, but no condition used them — so a
        # lifegain deck was scored on mana and the scorer had no reason to
        # prefer a payoff over an enabler. A real build came back 26 enablers
        # and 1 payoff, which is the exact failure the plan is meant to stop.
        #
        # Threshold 8, not 3: per SCORING-CONTRACT.md a bar a single card can
        # clear in one cast measures "did I play a card", not "is the engine
        # on". 8 life by turn 6 needs a repeating source or several triggers.
        out.append({
            "id": "life_engine_online",
            "label": "8 life gained",
            "why": "Lifegain is only a plan when it repeats — one cast is a card, not an engine.",
            "check": {"life_gained": 8},
            "by_turn": 6,
            "weight": "core",
        })
    else:
        mana_target = 5
        if cmdr_mech.get("produces", 0) >= 2 or cmdr_mech.get("fetch", 0) > 0:
            mana_target = 6
        out.append({
            "id": "mana_online",
            "label": f"{mana_target} mana available",
            "why": "The plan needs enough mana to cast its pieces on time.",
            "check": {"mana_available": mana_target},
            "by_turn": 5,
            "weight": "supporting",
        })

    out.sort(key=lambda c: (c["by_turn"], _WEIGHT_ORDER.get(c["weight"], 9), c["id"]))
    return out[:4]


# tempo changes HOW MUCH the plan asks for, never WHICH families it
# asks for. The commander decides what the deck needs; the archetype
# decides how demanding each ask is. Keeping that split is what stops this
# becoming the theme-word expansion the commitment derivation removed.
#
# Measured: two wishes differing only in tempo produced a different archetype
# and a 38.5%-Jaccard-different 99, but an IDENTICAL plan -- because the
# archetype was not an input here at all. The scoring contract put 34 points on this axis,
# and the mechanism was what the plan asks for, not just how cards score.
#
# A burst plan wants fewer, larger payoffs; a grind plan wants more, smaller,
# repeatable ones. Floors move; families do not.
_ARCHETYPE_SCALE: dict[str, dict[str, float]] = {
    # payoff-shaped asks shrink for burst (fewer, bigger) and grow for
    # incremental (more, smaller). Enabler-shaped asks move the other way:
    # a burst turn needs more fuel to spend at once.
    "burst":       {"payoff": 0.7, "enabler": 1.2},
    "incremental": {"payoff": 1.3, "enabler": 1.0},
    "aggro":       {"payoff": 1.0, "enabler": 1.1},
}
# Which commitment ids are the payoff side of a plan and which are the fuel.
_PAYOFF_IDS = frozenset({
    "lifegain_density", "wide_payoff_density", "counter_payoff_density",
    "combat_piece_density",
})


def _scaled_min(cid: str, minimum: int, archetype: str | None) -> int:
    """Floor for one commitment under a tempo. Never below 1."""
    scale = _ARCHETYPE_SCALE.get(archetype or "")
    if not scale:
        return minimum
    key = "payoff" if cid in _PAYOFF_IDS else "enabler"
    return max(1, round(minimum * scale[key]))


def _build_commitments(
    families: list[str],
    commander_card: dict | None = None,
    archetype: str | None = None,
) -> list[dict]:
    fams = list(families)
    # sac outlets need fodder on the 99. Emit token_density without
    # putting "tokens" into plan families — that would make every aristocrat
    # condition prefer board_width over the life/engine check the brief named.
    if commander_card:
        demand = synergy.demand_profile(commander_card)
        if _needs_sac_fodder(demand) and "tokens" not in fams:
            fams.append("tokens")
    seen: set[str] = set()
    out: list[dict] = []
    for fam in fams:
        tpl = _COMMITMENT_TEMPLATES.get(fam)
        if not tpl:
            continue
        cid, label, role, minimum, why = tpl
        if cid in seen:
            continue
        seen.add(cid)
        out.append({
            "id": cid,
            "label": label,
            "counts": {"role": role,
                       "min": _scaled_min(cid, minimum, archetype)},
            "why": why,
        })
    out.sort(key=lambda c: c["id"])
    return out


def _load_bearing(cmdr_card: dict | None, partner_card: dict | None) -> list[str]:
    names = []
    if cmdr_card and cmdr_card.get("name"):
        names.append(cmdr_card["name"])
    if partner_card and partner_card.get("name"):
        names.append(partner_card["name"])
    return names


def _commander_oracle(commander_card: dict) -> str | None:
    """Readable commander oracle, or None when we could not look."""
    for key in ("text", "oracle_text"):
        if key not in commander_card:
            continue
        text = commander_card.get(key)
        if isinstance(text, str):
            return text
    for face in commander_card.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        for key in ("oracle_text", "text"):
            ft = face.get(key)
            if isinstance(ft, str):
                return ft
    return None


def wants_activation_multiplier(commander_card: dict | None) -> bool:
    """True when extra combats/turns would multiply this commander's firings.

    Beginning-of-combat and attacks triggers fire once per combat. Timing
    multipliers (Full Throttle, etc.) are plan-relevant here — not synergy
    edges. Unknown oracle → False (could not look; do not invent relevance).
    """
    if not commander_card:
        return False
    text = _commander_oracle(commander_card)
    if text is None:
        return False
    if _COMBAT_ACTIVATION_RE.search(text):
        return True
    demand = synergy.demand_profile(commander_card)
    if demand["status"] == synergy.UNKNOWN:
        return False
    return "attacks" in (demand.get("signals") or [])


def serves_activation_multiplier(
    card: dict, commander_card: dict | None,
) -> bool:
    """Card is plan-relevant as a tempo/activation multiplier for commander.

    Thin timing-multiplier link: combat-triggered commander + timing_multiplier card.
    Does not add combat/turn as synergy objects or invent produce→consume edges.
    """
    if not wants_activation_multiplier(commander_card):
        return False
    return synergy.timing_multiplier(card)


def _activation_multiplier_block(commander_card: dict | None) -> dict[str, Any]:
    relevant = wants_activation_multiplier(commander_card)
    return {
        "relevant": relevant,
        "why": (
            "Commander triggers at combat or on attack; extra combats, "
            "extra turns, and untap-all multiply activations."
            if relevant else
            "Commander has no combat/attack trigger to multiply."
        ),
    }


def _theme_block(brief: dict) -> dict:
    label = str(brief.get("theme") or brief.get("angle") or "").strip()
    source = "brief.theme" if brief.get("theme") else (
        "brief.angle" if brief.get("angle") else "brief"
    )
    return {"label": label, "source": source, "binding": False}


def _derive_core(
    brief: dict,
    commander_card: dict | None,
    partner_card: dict | None,
) -> dict[str, Any]:
    text = _brief_text(brief)
    # demand_profile(commander) ∪ explicit brief phrases. Theme
    # modulates the commander; relation expand must not replace either.
    families = _basis_families(brief, commander_card)

    cmdr_mech = None
    if commander_card:
        cmdr_mech = sim.mechanics(commander_card, set())

    conditions = _build_conditions(
        brief, text, families, commander_card, cmdr_mech,
    )
    for cond in conditions:
        for key in (cond.get("check") or {}):
            if key not in sim.THESIS_KEYS:
                raise ValueError(f"plan condition uses unknown THESIS_KEY: {key}")

    # the commander opens a fan of archetypes; her brief picks the
    # line. decided=False means her words did not choose — the caller should
    # say the commander supports several rather than pretending she did.
    #
    # decided:true is structurally impossible without a label. An
    # empty candidate list used to return decided=True with id/label None.
    #
    # prefer brief.strategy_shape (stated) over phrase matching
    # (inferred). source reports which path decided — an unknown path is not
    # a silent phrases win.
    _cands = archetypes_for(cmdr_mech, families)
    _arch, _decided, _source = pick_archetype(brief, _cands)
    _label = _ARCHETYPES.get(_arch or "", {}).get("label")
    if not _label:
        _decided = False
        _arch = None if not _cands else _arch
    # the implication runs BOTH ways. decided:true requires a label
    # (above), and an undecided archetype must not carry one either.
    #
    # pick_archetype returns candidates[0] as its undecided fallback, so a
    # label was being read off a card we explicitly did NOT choose. That only
    # stayed hidden while archetypes_for was gated on life_math and handed
    # back no candidates for most commanders — generalising the gate surfaced
    # it immediately as "decided=False, label='grind it out'".
    #
    # Candidates stay populated: the plan header shows her what the commander
    # supports when we could not choose. A label would read as a choice.
    if not _decided:
        _label = None
    commitments = _build_commitments(families, commander_card, _arch)
    # A decision with nothing committed is not a decision.
    if _decided and not commitments:
        _decided = False
    # curve preference is a first-class plan axis, same as archetype.
    # Heard from the brief fields that survive commander overwrite; consumed by
    # card_score's CMC tiebreak and (reporting-only) curve.skeleton_for.
    _curve = curve.preference(brief)
    return {
        "version": PLAN_VERSION,
        "archetype": {
            "id": _arch,
            "label": _label,
            "candidates": _cands,
            "decided": _decided,
            "source": _source,
        },
        "curve": _curve,
        "derived_from": {
            "commander": (commander_card or {}).get("name"),
            "partner": (partner_card or {}).get("name"),
            "brief_fields": _brief_fields_used(brief),
            "sim_version": sim.SIM_VERSION,
        },
        "conditions": conditions,
        "commitments": commitments,
        "load_bearing": _load_bearing(commander_card, partner_card),
        "theme": _theme_block(brief),
        # plan-level note only — not a floor, not a synergy object.
        "activation_multiplier": _activation_multiplier_block(commander_card),
    }


def derive(
    brief: dict,
    commander_card: dict,
    partner_card: dict | None = None,
) -> dict[str, Any]:
    """Full plan graph once a commander is chosen — the real derivation."""
    return _derive_core(brief or {}, commander_card, partner_card)


def derive_provisional(brief: dict) -> dict[str, Any]:
    """Loose plan from the ask before a commander exists — enough to search with."""
    return _derive_core(brief or {}, None, None)


# Family → the query that finds its PAYOFFS.
#
# The role ladder searches for the theme — o:"gain life" returns 211 cards that
# GAIN life. It never searches o:"whenever you gain life", which is the 67 cards
# that PAY OFF lifegain. A real build came back with 26 enablers and 1 payoff
# because the payoffs were never candidates, not because they ranked low.
#
# Hand-written on purpose. The taxonomy cannot supply these: it is named-
# mechanics-only and has exactly two lifegain entries (extort, lifelink). And
# otag: has no payoff/enabler split — otag:lifegain is the whole behaviour.
# A small honest map beats an elegant one that returns nothing.
#
# Every pattern is probed at build time (scripts/check_payoff_queries.py) the
# way the taxonomy query gate checks the taxonomy: a dead pattern fails loudly rather than
# returning an empty pool the builder silently pads around.
_PAYOFF_QUERIES: dict[str, str] = {
    "life_math":             'o:"whenever you gain life"',
    "damage_modification":   'o:"damage" o:"instead"',
    "tokens":                'o:"whenever a creature you control enters" or o:"whenever one or more creature tokens"',
    "go_wide_payoffs":       'o:"creatures you control get +"',
    "attacking_triggers":    'o:"whenever you attack"',
    "saboteur":              'o:"deals combat damage to a player"',
    "dies_triggers":         'o:"whenever a creature you control dies" or o:"whenever another creature dies"',
    "cast_from_graveyard":   '(o:"you may cast" o:"from your graveyard" or keyword:flashback or keyword:escape) -t:land',
    "return_from_graveyard": 'o:"return target creature card from your graveyard"',
    # thresholds_graveyard is deliberately ABSENT. Every pattern I tried
    # (o:"cards in your graveyard", keyword:delirium/threshold) returns lands
    # and rituals -- Shifting Woodland, Cabal Ritual, Cephalid Coliseum -- not
    # payoffs. An absent family means payoff_searches() skips it and the roles
    # fill normally, which is honest. A wrong pattern would quietly fill the
    # 99 with lands and look like it worked.
    "library_manipulation":  'o:"whenever you draw" or o:"if you would draw"',
    # counter and re-entry floors need candidates in the pool.
    # otag:counters-matter / otag:blink are gated by check_payoff_queries.py.
    "counters_plus":         "otag:counters-matter",
    "go_tall_payoffs":       "otag:counters-matter",
    "reentry":               "otag:blink",
}


# Archetype: the strategy a commander ENABLES, not the one it is.
#
# A commander is a seed, not a plan. Vito supports three decks that share
# almost no cards: incremental drain (many small gain triggers), burst combo
# (one huge gain converted to lethal), and lifelink aggro. Commander-aware plans made the
# plan read the commander but only moved a threshold — same strategy, different
# number. This is the layer where a different strategy means different CARDS.
#
# The commander opens the fan; HER BRIEF picks the line. If the brief does not
# distinguish, we say so rather than defaulting silently — an unresolved
# archetype is an unknown, not a zero.
_ARCHETYPES: dict[str, dict] = {
    "incremental": {
        "label": "grind it out",
        # "steady"/"attrition": models write those more often than "steadily".
        "phrases": ("every turn", "each turn", "incremental", "slowly", "grind",
                    "over time", "value", "steadily", "steady", "attrition"),
        "extra_payoffs": (),
    },
    "burst": {
        "label": "one big turn",
        # the preserved drain-burst brief said "one explosive finishing
        # turn" / "burst finisher", never "one big" or the contiguous "one turn".
        # First-match against the old list scored zero and fell through to
        # incremental/undecided while the deck itself had already moved.
        "phrases": ("one big", "combo", "infinite", "out of nowhere", "burst",
                    "all at once", "kill from", "one turn", "explosive",
                    "finishing turn", "finisher"),
        # Burst wants what a grindy deck does not: both halves of the loop, life
        # doublers, and drains that scale with a big life total.
        #
        # Kept as SEPARATE entries, not one OR: a combined arm let the
        # Bond half alone satisfy min:6 and drop Exquisite Blood — the half that
        # actually closes the Vito loop and scores zero in a goldfish.
        #
        # o:"you win the game" was here and is deliberately gone. Measured: it
        # returns Thassa's Oracle and Laboratory Maniac, alt-wincons unrelated
        # to lifegain, and they crowded out the real payoffs. The build came
        # back with ONE payoff and zero combo pieces. "Returns something" is
        # not the bar; what it returns is.
        "extra_payoffs": (
            # Vito, Sanguine Bond, Enduring Tenacity — the gain-to-drain half.
            'o:"whenever you gain life" o:"loses that much life"',
            # Exquisite Blood, Bloodthirsty Conqueror — the drain-to-gain half
            # that closes the loop. Two cards, and they are the archetype.
            'o:"whenever an opponent loses life" o:"you gain"',
            # Alhammarret's Archive, Cleric Class — doubling turns one gain
            # into the big one.
            'o:"if you would gain life" or o:"gain twice"',
            # Gray Merchant, Exsanguinate — convert a large life total at once.
            'o:"gain life equal to"',
        ),
    },
    "aggro": {
        "label": "attack with it",
        "phrases": ("attack", "swing", "aggressive", "combat", "beat down",
                    "aggro", "creatures attack"),
        "extra_payoffs": ('keyword:lifelink t:creature',),
    },
}

# Contrast briefs name the line they reject ("not a combo", "rather than
# attrition"). A bare substring hit on the rejected words is not a vote.
_ARCHETYPE_NEGATORS = (
    "not ", "no ", "rather than ", "instead of ", "without ", "never ",
)


def archetypes_for(commander_mech: dict | None, families: list[str]) -> list[str]:
    """Which archetypes this commander can actually support.

    Derived from what the sim can read off it, never from the brief — the
    commander decides what is POSSIBLE and the brief decides which we build.
    """
    out: list[str] = []
    mech = commander_mech or {}

    # this used to open with `if "life_math" not in families: return
    # []`, so the archetype axis was live for lifegain decks and DEAD for
    # everything else — sacrifice, tokens, counters, spellslinger, artifacts,
    # blink, tribal all got no tempo decision at all. The scoring contract measured that
    # axis at 34 points, so it was 34 points of scoring that twelve of our
    # thirteen playstyles never reached.
    #
    # It also hid itself: every differential pair we built was lifegain-
    # flavoured, so the axis was only ever exercised where it worked.
    #
    # Tempo is orthogonal to theme. A sacrifice deck can grind or explode
    # exactly as a lifegain deck can, so candidates come from what the
    # commander supports, not from one family being present.
    if not families:
        # No plan is no basis, even with a readable commander. My first cut
        # allowed a commander body alone to yield candidates, and that broke
        # immediately: Tatyova and an empty brief both came back
        # decided=False while carrying id='incremental' and the label "grind
        # it out". A label without a decision is exactly the lie the empty-brief refusal guards against
        # exists to prevent. The archetype answers "how does this PLAN win",
        # so no plan means no answer.
        return out

    # Grinding is available to any deck with a plan — it is what a deck does
    # absent a reason to do otherwise.
    out.append("incremental")

    # A conversion engine combos when fed one big input. life_math has an
    # explicit payoff reading; other families carry their own payoff shape.
    _BURST_FAMILIES = {"life_math", "dies_triggers", "tokens", "counters_plus",
                       "go_tall_payoffs", "cast_from_graveyard", "spellslinger"}
    if mech.get("lifegain_payoff", 0) > 0 or (set(families) & _BURST_FAMILIES):
        out.append("burst")

    # Attacking needs a body to attack with, or a plan that makes them.
    if mech.get("bodies", 0) > 0 or (
            {"tokens", "go_wide_payoffs", "attacking_triggers"} & set(families)):
        out.append("aggro")

    return out


def _brief_archetype_text(brief: dict) -> str:
    """Prose the picker may read. Mechanics are part of the brief signal.

    drain-burst's decisive "burst finisher" lived only in mechanics;
    scanning angle/strategy alone left the picker with nothing to hear.
    """
    parts = [str(brief.get(k) or "") for k in
             ("strategy", "theme", "playstyle", "plan", "angle")]
    mechs = brief.get("mechanics") or []
    if isinstance(mechs, (list, tuple)):
        parts.extend(str(m) for m in mechs)
    elif mechs:
        parts.append(str(mechs))
    return " ".join(parts).lower()


def _positive_phrase_hits(phrase: str, text: str) -> int:
    """Count phrase occurrences that are not under a nearby negation."""
    hits = 0
    start = 0
    step = max(len(phrase), 1)
    while True:
        i = text.find(phrase, start)
        if i < 0:
            return hits
        window = text[max(0, i - 40):i]
        if not any(n in window for n in _ARCHETYPE_NEGATORS):
            hits += 1
        start = i + step


# Values the interrogator may put in brief.strategy_shape.
# Kept wider than _ARCHETYPES on purpose: "control" is a real wish even when
# the current commander fan cannot support it. Stated-but-unsupported is
# decided=False with source="strategy_shape", never a silent phrase guess.
_STRATEGY_SHAPES = frozenset({"incremental", "burst", "aggro", "control"})


def pick_archetype(
    brief: dict, candidates: list[str],
) -> tuple[str | None, bool, str]:
    """(archetype, decided, source). decided=False means her words did not choose.

    source is which path produced the answer:
      "strategy_shape" — brief.strategy_shape was set (even if unsupported)
      "phrases"        — field was null; substring match against prose decided
      "none"           — neither path could decide

    Never guesses. A brief that does not distinguish returns the first
    candidate with decided=False so the caller can say the commander supports
    several rather than pretending she picked.

    no candidates means undecided, not a silent decision. decided:true
    with id/label None was the lie an empty brief used to ship.

    score every candidate and ignore negated contrast phrases. The old
    first-match walk returned whichever candidate was listed first as soon as
    any of its words appeared — so "not a combo" could never elect burst, and a
    burst brief that used "explosive" instead of "one big" scored zero.

    prefer the structured strategy_shape field. Phrase matching is a
    fallback for null only — racing the model's vocabulary with more substrings
    is how run 4 collapsed both sides onto aggro after run 3 passed.
    """
    if not candidates:
        return None, False, "none"

    raw = brief.get("strategy_shape")
    if raw is not None and str(raw).strip():
        shape = str(raw).strip().lower()
        # Non-empty is "stated". Phrase fallback is null-only.
        # Values come from _STRATEGY_SHAPES (CHAT schema); candidates is the
        # narrower commander fan. control is valid to state and may still be
        # unsupported here — that is decided=False, not a silent phrase path.
        if shape in candidates:
            return shape, True, "strategy_shape"
        return candidates[0], False, "strategy_shape"

    text = _brief_archetype_text(brief)
    scores = {
        name: sum(_positive_phrase_hits(p, text)
                  for p in _ARCHETYPES[name]["phrases"])
        for name in candidates
    }
    best = max(scores.values())
    if best <= 0:
        return candidates[0], False, "none"
    winners = [n for n in candidates if scores[n] == best]
    if len(winners) != 1:
        # Two lines fit equally — unknown, not a coin flip on list order.
        return winners[0], False, "none"
    return winners[0], True, "phrases"


def plan_is_success(plan: dict | None) -> bool:
    """Whether a plan is an honest, finished decision.

    decided:true requires a label. Empty commitments are an error state, never
    a silent success — the UI already renders the undecided case.
    """
    if not plan:
        return False
    arch = plan.get("archetype") or {}
    if not arch.get("decided") or not arch.get("label"):
        return False
    if not (plan.get("commitments") or []):
        return False
    return True


def payoff_searches(plan: dict) -> list[dict]:
    """Payoff queries the build must fill toward, from the plan's commitments.

    A commitment is a floor on the 99 — "at least 10 cards that turn lifegain
    into something". This turns each one into a real Scryfall search so those
    cards become candidates at all. Returns [] when no commitment has a known
    payoff pattern: an unknown is not a zero, and the caller keeps its own
    rungs rather than searching for nothing.
    """
    # Commitments carry their template id (lifegain_density), not the family
    # (life_math). Invert _COMMITMENT_TEMPLATES rather than string-matching.
    by_id = {tpl[0]: fam for fam, tpl in _COMMITMENT_TEMPLATES.items()}
    out: list[dict] = []
    seen: set[str] = set()
    for c in plan.get("commitments") or []:
        fam = c.get("family") or by_id.get(c.get("id") or "")
        q = _PAYOFF_QUERIES.get(fam or "")
        if not q or q in seen:
            continue
        seen.add(q)
        out.append({"id": c.get("id"), "query": q,
                    "min": int(c.get("counts", {}).get("min") or 8)})

    # the archetype changes WHICH cards are candidates, not just a
    # threshold. A burst plan searches the loop halves; an aggro plan searches
    # lifelink bodies. Same commander, same brief family, different 99.
    arch = (plan.get("archetype") or {}).get("id")
    for extra in _ARCHETYPES.get(arch or "", {}).get("extra_payoffs", ()):
        if extra not in seen:
            seen.add(extra)
            out.append({"id": f"archetype_{arch}", "query": extra, "min": 6})
    return out


def conditions_for_scoring(plan: dict) -> list[dict]:
    """Thesis-compatible condition list for card_score / sim.

    Keeps `by_turn` when present so the UI can headline the plan's own
    deadline (engine online by turn 5) instead of always reading turn 5.
    """
    out: list[dict] = []
    for c in plan.get("conditions") or []:
        if not isinstance(c, dict):
            continue
        check = c.get("check")
        if not isinstance(check, dict) or not c.get("label"):
            continue
        if not all(k in sim.THESIS_KEYS for k in check):
            continue
        row = {
            "label": c["label"],
            "check": dict(check),
            "note": c.get("why") or "",
        }
        by_turn = c.get("by_turn")
        if isinstance(by_turn, int) and 1 <= by_turn <= 10:
            row["by_turn"] = by_turn
        out.append(row)
    return out


def payoff_names_from_deck(
    deck: list[dict],
    commander_card: dict | None = None,
    *,
    cap: int = 20,
) -> dict[str, Any]:
    """Payoff names from the real 99 for goldfish / scoring.

    Prefer glued synergy candidates (exact deck names with edges). If glue is
    empty or the commander is unreadable, fall back to plan/theme/deepcut
    roles already on the list — never invent names.
    """
    glued = synergy.glued_payoff_candidates(
        deck or [], commander_card, cap=cap)
    status = glued.get("status") or "empty"
    names = list(glued.get("names") or [])
    if names:
        return {
            "status": status,
            "names": names[: max(1, int(cap))],
            "source": "glued",
            "glued_names": names,
        }

    role_names: list[str] = []
    seen: set[str] = set()
    for role in _PLAN_PAYOFF_ROLES:
        for c in deck or []:
            if not isinstance(c, dict):
                continue
            if (c.get("role") or "") != role:
                continue
            n = c.get("name")
            if not n or n in seen:
                continue
            seen.add(n)
            role_names.append(n)
            if len(role_names) >= max(1, int(cap)):
                break
        if len(role_names) >= max(1, int(cap)):
            break
    return {
        "status": status,
        "names": role_names,
        "source": "roles" if role_names else "empty",
        "glued_names": [],
    }


def thesis_from_plan(
    plan: dict | None,
    deck: list[dict],
    commander_card: dict | None = None,
    *,
    payoff_cap: int = 20,
) -> dict[str, Any] | None:
    """Goldfish thesis from the plan that selected the 99 — any plan shape.

    Returns None when the plan has no measurable conditions (caller may fall
    back to the model thesis). Payoff cards are always exact names from the
    deck. Condition keys are already constrained to sim.THESIS_KEYS.
    """
    if not isinstance(plan, dict):
        return None
    conditions = conditions_for_scoring(plan)
    if not conditions:
        return None

    payoffs = payoff_names_from_deck(
        deck or [], commander_card, cap=payoff_cap)
    names = list(payoffs.get("names") or [])
    glued_names = list(payoffs.get("glued_names") or [])
    return {
        "payoff_cards": names,
        "conditions": conditions,
        "source": "plan",
        "payoff_source": payoffs.get("source") or "empty",
        "glued_payoff_candidates": glued_names,
        "glued_payoff_status": payoffs.get("status") or "empty",
        "glued_payoff_coverage": synergy.payoff_edge_coverage(
            names, deck or [], commander_card or {}),
    }
