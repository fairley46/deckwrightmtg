"""
overview.py — deck overview grounded in the list and the simulation.

how it works, combos, pros, cons. Every claim must trace to a card in
the 99 or a number from sim.diagnose() / deck_signals(). The model phrases;
it does not recall Magic facts.
"""

import json
import re

from deckwright import sim

# bump when OVERVIEW / verify_overview change what baked prose means.
# also bump when precon brief_from_analysis shape changes — same stamp.
# precon_bake.ANALYSIS_VERSION travels with each entry; keep the two in lockstep.
ANALYSIS_VERSION = 3

OVERVIEW = """You write a deck overview for someone who has never built a deck.

Every fact must come from the FACTS block — the decklist, simulation diagnostics,
simulation curves, and countable signals. You may phrase, group, and explain. You
may not introduce any fact not in FACTS.

Do not predict matchups. Do not mention the metagame. Do not say "decks like X".

Return only json:
{
  "strategy": "one or two sentences naming what THIS deck is trying to do — the
               thing every other card in it is serving. For a deck someone else
               built (a precon, a pasted list) you are reading it off the cards;
               say it as plainly as you would to someone holding the box. This is
               the sentence the whole page hangs off, so it has to be the actual
               plan, not a description of the colours or the commander.",
  "beats": [{"when": "Turns 1-3", "what": "one short line — what she is doing and
              why. Name real cards from FACTS. Cite a sim percentage where one is
              relevant. Three to five of these, in turn order: the early game, the
              turn the deck comes online, and how it closes. Magic is played in
              turns, so the plan reads as turns rather than as a paragraph. Each
              line is one breath long."}],
  "how_it_works": "3-5 sentences on the turn-by-turn sequence for THIS deck and
                  THIS strategy. Tie to strategy and mechanics from FACTS. When
                  describing pacing, cite a sim curve median turn or a percentage
                  from FACTS (e.g. 'five mana by turn 5 — 62% in the sim').",
  "combos": [{"cards": ["exact name 1", "exact name 2"],
              "text": "one sentence on how their oracle texts interact"}],
  "pros": [{"text": "...", "counts": {"key": number}}],
  "cons": [{"text": "...", "counts": {"key": number}}],
  "roles": {"<role name from FACTS>": "one line on what this GROUP of cards does
             for THIS deck's plan — not what the role does in general. 'Answers
             one thing' is true of every removal spell ever printed and tells her
             nothing. 'Four ways to remove a blocker so your goblins keep
             connecting' tells her why they are in HER deck. Name the plan they
             serve, and phrase it as serving the STRATEGY you just named above.
             Skip a role entirely rather than write a generic line."}
}

Roles: one line each, and only where you can say something specific to this deck.
A line that would fit any Commander deck is worse than no line — she has already
read the role name and can guess what removal does.

Combos: only claim synergies between two cards whose oracle text appears in FACTS.
If no obvious pair exists, return an empty combos array.

Pros: from signals.strong and sim curves that reach 50% or more by turn 6. Each
line must include the number it comes from.

Cons: from signals.watch and sim curves still below 15% by turn 6. Each line must
include the number. Do not soften honest weaknesses.

Card names must be exact matches from the decklist or commander name in FACTS."""


MATCHUP_BANNED = re.compile(
    r"\b(beats|loses to|good against|bad against|the meta|metagame|tier list)\b",
    re.I,
)

# there are two goldfish runs in this app and they answer different
# questions. Naming them the same thing ("the sim") is how a number from one
# gets read as evidence for the other.
#
#   OVERVIEW sim  — here. Generic conditions, ~1500 runs, during /api/build.
#                   Feeds the overview prose only. She never sees these curves.
#   THESIS sim    — sim.run via /api/simulate, conditioned on the plan that
#                   built the 99 (`plan_graph.thesis_from_plan`). Model thesis
#                   only when no measurable plan exists. Curves on screen.
#
# Same engine, same math, different question. Nothing here changes the numbers.
OVERVIEW_CONDITIONS = [
    {"label": "Commander out", "note": "Your commander is on the battlefield.",
     "check": {"commander_in_play": True}},
    {"label": "Five mana", "note": "Five mana sources on the battlefield.",
     "check": {"mana_available": 5}},
    {"label": "Three payoffs", "note": "Three plan cards in play.",
     "check": {"payoffs_in_play": 3}},
    {"label": "Seven mana", "note": "Seven mana sources on the battlefield.",
     "check": {"mana_available": 7}},
]

# Old name, kept one release for anything external reading it. Same list object.
BUILD_CONDITIONS = OVERVIEW_CONDITIONS


def payoff_names_from_deck(deck: list) -> list[str]:
    """Plan cards for build-time sim — roles that carry the strategy."""
    names = []
    for c in deck:
        if c.get("role") in ("theme", "deepcut", "ramp", "draw"):
            names.append(c["name"])
    return names[:20]


def run_overview_sim(deck: list, cmdr_cmc: int, runs: int = 1500,
                     seed: int | None = None) -> dict:
    """Goldfish snapshot during build, for the OVERVIEW prose only.

    Not the curves she sees. Those come from /api/simulate against the thesis
    conditions for this specific deck. This run uses OVERVIEW_CONDITIONS —
    generic checks that apply to any deck — so its percentages are not
    comparable with the thesis curves and must never be quoted as them.
    """
    payoffs = payoff_names_from_deck(deck)
    return sim.run(deck, cmdr_cmc, payoffs, OVERVIEW_CONDITIONS, runs=runs, seed=seed)


# Old name, kept one release. Identical behaviour — renamed, not rewired.
run_build_sim = run_overview_sim


def names_mentioned_in_text(text: str, candidates: set[str]) -> list[str]:
    """Find deck card names appearing as substrings in prose."""
    if not text:
        return []
    lower = text.lower()
    found = []
    for name in sorted(candidates, key=len, reverse=True):
        if name.lower() in lower:
            found.append(name)
    return found


def all_named_cards(overview: dict, deck_names: set[str], commander: str) -> list[str]:
    """Every card name cited in an overview — for verifier tests."""
    allowed = deck_names | {commander}
    names: list[str] = []
    names.extend(names_mentioned_in_text(overview.get("how_it_works") or "", allowed))
    for combo in overview.get("combos") or []:
        names.extend(combo.get("cards") or [])
        names.extend(names_mentioned_in_text(combo.get("text") or "", allowed))
    for side in ("pros", "cons"):
        for item in overview.get(side) or []:
            names.extend(names_mentioned_in_text(item.get("text") or "", allowed))
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def verify_overview(overview: dict, deck_names: set[str], commander: str) -> dict:
    """Drop invented card names from combos; normalize structure."""
    allowed = deck_names | {commander}
    combos = []
    for combo in overview.get("combos") or []:
        cards = [c for c in (combo.get("cards") or []) if c in allowed]
        if len(cards) >= 2 and combo.get("text"):
            combos.append({"cards": cards[:2], "text": str(combo["text"]).strip()})

    pros, cons = [], []
    for item in overview.get("pros") or []:
        if item.get("text") and item.get("counts"):
            pros.append({"text": str(item["text"]).strip(),
                         "counts": dict(item["counts"])})
    for item in overview.get("cons") or []:
        if item.get("text") and item.get("counts"):
            cons.append({"text": str(item["text"]).strip(),
                         "counts": dict(item["counts"])})

    # One line per role, but only where it says something about THIS deck.
    roles = {}
    for role, line in (overview.get("roles") or {}).items():
        if isinstance(line, str) and line.strip():
            roles[str(role)] = line.strip()

    # Turn-ordered beats. A paragraph describing turns one through six is a
    # wall; the same content as beats is scannable, and turns are the game's
    # own structure rather than a decoration we imposed.
    beats = []
    for b in overview.get("beats") or []:
        if isinstance(b, dict) and b.get("what"):
            beats.append({"when": str(b.get("when") or "").strip(),
                          "what": str(b["what"]).strip()})

    return {
        "strategy": str(overview.get("strategy") or "").strip(),
        "beats": beats[:6],
        "roles": roles,
        "how_it_works": str(overview.get("how_it_works") or "").strip(),
        "combos": combos,
        "pros": pros,
        "cons": cons,
    }


def _curve_by_turn(curves: list, label: str, turn: int = 6) -> float | None:
    for c in curves or []:
        if c.get("label") == label:
            pts = c.get("points") or []
            if len(pts) >= turn:
                return float(pts[turn - 1])
            return None
    return None


def from_counts(commander: str, deck: list, signals: dict, sim_out: dict) -> dict:
    """Deterministic overview from countable facts — no model call.

    bake path: when Anthropic is unavailable (or for a first-pass bake),
    write the same shape generate_overview returns, citing only diagnostics,
    signal lines, and sim curve percentages. Combos stay empty — pairing
    oracle texts is a judgment call the model owns.
    """
    diag = sim_out.get("diagnostics") or {}
    curves = sim_out.get("curves") or []
    lands = int(diag.get("lands") or 0)
    rocks = int(diag.get("mana_rocks") or 0)
    land_ramp = int(diag.get("land_ramp") or 0)
    avg = diag.get("avg_cmc")
    ramp = rocks + land_ramp

    five = _curve_by_turn(curves, "Five mana", 5)
    seven = _curve_by_turn(curves, "Seven mana", 6)
    cmdr_out = _curve_by_turn(curves, "Commander out", 5)
    payoffs = _curve_by_turn(curves, "Three payoffs", 6)

    # Strategy sentence — shape from the curve and ramp counts, name the commander.
    if avg is not None and float(avg) <= 2.5 and ramp <= 8:
        shape = "a low curve that wants to spend mana every turn"
    elif ramp >= 12 or (seven is not None and seven >= 40):
        shape = "a ramp plan that builds toward expensive plays"
    else:
        shape = "a midrange curve that develops lands and threats together"
    strategy = f"{commander} — {shape}."

    # Beats from the sim's own milestones.
    beats = []
    if lands:
        beats.append({
            "when": "Turns 1-3",
            "what": (f"Hit land drops. This list has {lands} lands"
                     + (f" and {ramp} ramp pieces" if ramp else "")
                     + (f"; average nonland MV {avg}." if avg is not None else ".")),
        })
    if five is not None:
        beats.append({
            "when": "Turn 5",
            "what": f"Five mana is available in {five:.0f}% of goldfish games.",
        })
    if cmdr_out is not None:
        beats.append({
            "when": "Commander",
            "what": f"{commander} is in play by turn 5 in {cmdr_out:.0f}% of games.",
        })
    if payoffs is not None:
        beats.append({
            "when": "Payoffs",
            "what": f"Three plan cards are in play by turn 6 in {payoffs:.0f}% of games.",
        })

    how_parts = []
    if avg is not None:
        how_parts.append(f"Average nonland mana value is {avg}.")
    how_parts.append(f"The mana base is {lands} lands"
                     + (f" plus {rocks} rocks and {land_ramp} land-ramp spells."
                        if rocks or land_ramp else "."))
    if five is not None:
        how_parts.append(f"Five mana by turn 5 — {five:.0f}% in the sim.")
    if seven is not None:
        how_parts.append(f"Seven mana by turn 6 — {seven:.0f}% in the sim.")
    if cmdr_out is not None:
        how_parts.append(f"Commander on the battlefield by turn 5 — {cmdr_out:.0f}%.")
    how_it_works = " ".join(how_parts)

    # Pros/cons: lift signal lines (already carry counts), then curve thresholds.
    pros = []
    for item in (signals.get("strong") or []):
        if item.get("text") and item.get("counts"):
            pros.append({"text": item["text"], "counts": dict(item["counts"])})
    cons = []
    for item in (signals.get("watch") or []):
        if item.get("text") and item.get("counts"):
            cons.append({"text": item["text"], "counts": dict(item["counts"])})

    if five is not None and five >= 50:
        pros.append({"text": f"Five mana by turn 5 in {five:.0f}% of games.",
                     "counts": {"five_mana_t5_pct": five}})
    if five is not None and five < 15:
        cons.append({"text": f"Five mana by turn 5 only {five:.0f}% of the time.",
                     "counts": {"five_mana_t5_pct": five}})
    if cmdr_out is not None and cmdr_out < 15:
        cons.append({"text": f"Commander out by turn 5 only {cmdr_out:.0f}% of the time.",
                     "counts": {"commander_out_t5_pct": cmdr_out}})
    if payoffs is not None and payoffs < 15:
        cons.append({"text": f"Three payoffs by turn 6 only {payoffs:.0f}% of the time.",
                     "counts": {"three_payoffs_t6_pct": payoffs}})

    return verify_overview({
        "strategy": strategy,
        "beats": beats,
        "how_it_works": how_it_works,
        "combos": [],
        "pros": pros,
        "cons": cons,
        "roles": {},
    }, {c["name"] for c in deck}, commander)


def facts_block(commander: str, cmdr_card: dict, deck: list, brief: dict,
                signals: dict, sim_out: dict, for_model) -> str:
    """Assemble FACTS for the overview prompt — only what the model may cite."""
    listing = [for_model(c) for c in deck if c.get("role") not in ("basic", "lands")]
    curves = []
    for c in sim_out.get("curves") or []:
        pts = c.get("points") or []
        t6 = pts[5] if len(pts) > 5 else None
        curves.append({
            "label": c.get("label"),
            "note": c.get("note"),
            "median_turn": c.get("median_turn"),
            "percent_by_turn_6": t6,
            "points": pts,
        })

    # type breakdown is a first-class fact so cons can cite
    # "only 12 creatures" without the model inventing a count.
    type_counts = {}
    for key in ("creatures", "instants_sorceries", "artifacts",
                "enchantments", "lands"):
        if isinstance(signals.get("counts"), dict) and key in signals["counts"]:
            type_counts[key] = signals["counts"][key]

    facts = {
        "commander": commander,
        "commander_card": for_model(cmdr_card),
        "strategy": brief.get("strategy") or "",
        "mechanics": brief.get("mechanics") or [],
        "plan": brief.get("plan") or brief.get("angle") or "",
        "decklist": listing,
        "type_breakdown": type_counts,
        "signals": signals,
        "sim_diagnostics": sim_out.get("diagnostics"),
        "sim_curves": curves,
        "sim_assumptions": sim_out.get("assumptions"),
    }
    return "FACTS:\n" + json.dumps(facts, indent=1)


async def generate_overview(client, model: str, commander: str, cmdr_card: dict,
                            deck: list, brief: dict, signals: dict, sim_out: dict,
                            for_model, parse_json,
                            partner_card: dict | None = None) -> dict:
    """Call the model and verify card names against the deck."""
    facts = facts_block(commander, cmdr_card, deck, brief, signals, sim_out, for_model)
    if partner_card:
        # both halves of a partner pair are nameable in the write-up.
        facts = facts + "\nPARTNER:\n" + json.dumps(for_model(partner_card), indent=1)
    response = await client.messages.create(
        # The object grew: strategy, beats and per-role lines on top of the
        # original fields. 3000 was sized for the old shape and the model was
        # returning malformed JSON at the end of a long object.
        model=model, max_tokens=5000, system=OVERVIEW,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": facts}],
    )
    spec = parse_json(response, "overview", expect="object")
    if not isinstance(spec, dict):
        spec = {}
    deck_names = {c["name"] for c in deck}
    if partner_card and partner_card.get("name"):
        deck_names.add(partner_card["name"])
    # Label may be "A / B" while prose names each half — allow both forms.
    if " / " in (commander or ""):
        for part in commander.split(" / "):
            if part.strip():
                deck_names.add(part.strip())
    return verify_overview(spec, deck_names, commander)
