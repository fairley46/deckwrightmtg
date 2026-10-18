"""
precon_bake.py — static precon analysis shipped in the repo.

A precon is the same 100 cards forever. Overview, signals, simulation, and the
resolved list are deterministic — compute once, commit the result, serve it
with no model call and no simulation run. Prices stay live.

Each entry is keyed by Archidekt deck id. Stamps:
  deck_hash          — card-list fingerprint; mismatch means Archidekt edited
  analysis_version   — overview prompt / signals shape we baked against
  sim_version        — simulator engine that produced the curves

Either stamp mismatch marks the entry stale. Stale entries are not served —
we refuse to stand behind numbers from an engine we no longer run.

every bake also carries a `brief` derived from the analysis so the
plan half of the page — thesis, beat sheet, simulation — has something to
read. `source: "precon"` marks that she did not ask for this; she bought it.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from typing import Any

from deckwright import bake
from deckwright import card_score
from deckwright import overview
from deckwright import sheet
from deckwright import safe_paths
from deckwright import sim

# Bump when OVERVIEW prompt text, verify_overview shape, deck_signals()
# thresholds, or brief_from_analysis shape change what the baked payload means.
# Keep in lockstep with overview.ANALYSIS_VERSION.
ANALYSIS_VERSION = overview.ANALYSIS_VERSION

# Fixed seed for precon goldfish — reproducible, not merely cached.
PRECON_SIM_SEED = 102001
PRECON_SIM_RUNS = 1500

# In-repo static store. Not ~/.deckwright — this ships with the code.
DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "precons"

_PLAN_ROLES = frozenset({"theme", "deepcut", "plan"})
# Universal race filler that rides along with a tribal type
# (Human Wizard → wizard, Human Pirate → pirate). Real tribes stay eligible.
_RACE_CHAFF = frozenset({"Human", "Kor", "Vedalken"})


def bake_path(precon_id: str, directory: pathlib.Path | None = None) -> pathlib.Path | None:
    """Path of one baked precon, or None when the id is not a safe file stem.

    Precon ids come from request URLs, so the id must be a bare name and the
    resolved path must stay inside the bake directory.
    """
    return safe_paths.safe_child_path(directory or DATA_DIR, str(precon_id))


def read_bake(precon_id: str, directory: pathlib.Path | None = None) -> dict | None:
    path = bake_path(precon_id, directory)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_bake(payload: dict, directory: pathlib.Path | None = None) -> pathlib.Path:
    directory = directory or DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    precon_id = str(payload["id"])
    path = bake_path(precon_id, directory)
    if path is None:
        raise ValueError(f"invalid precon id: {precon_id!r}")
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return path


def list_baked_ids(directory: pathlib.Path | None = None) -> list[str]:
    directory = directory or DATA_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.is_file())


def analysis_fresh(cached: dict | None, deck: list[dict] | None = None) -> bool:
    """True when bake stamps match the current engine and (if given) card list.

    When deck is None, only the version stamps are checked — used when Archidekt
    is unreachable and we serve the shipped list as a stale catalog.

    Incomplete bakes (complete=False, or empty overview) are never fresh —
    they must not be served as finished analysis.
    """
    if not isinstance(cached, dict):
        return False
    if cached.get("complete") is False:
        return False
    overview_data = cached.get("overview") or {}
    if not (overview_data.get("strategy") or overview_data.get("how_it_works")):
        return False
    brief = cached.get("brief") or {}
    if not brief.get("strategy") or brief.get("source") != "precon":
        return False
    if cached.get("analysis_version") != int(ANALYSIS_VERSION):
        return False
    if cached.get("sim_version") != int(sim.SIM_VERSION):
        return False
    if deck is not None:
        if cached.get("deck_hash") != bake.deck_list_hash(deck):
            return False
    elif not cached.get("deck_hash"):
        return False
    return True


def _creature_subtypes(type_line: str) -> list[str]:
    """Subtype words from a type line — 'Human Wizard', both faces of a DFC."""
    _, sub = sheet.split_type(type_line or "")
    if not sub:
        return []
    out: list[str] = []
    for face in sub.split("//"):
        for word in face.strip().split():
            if word and word[0].isupper() and word.isalpha():
                out.append(word)
    return out


def theme_from_deck(deck: list[dict],
                    commander_card: dict | None = None) -> str | None:
    """Dominant creature type on the plan cards — the deck's own theme."""
    counts: Counter[str] = Counter()
    for c in deck:
        role = c.get("role")
        if role is not None and role not in _PLAN_ROLES:
            continue
        typ = c.get("type") or ""
        if "Creature" not in typ:
            continue
        for st in _creature_subtypes(typ):
            counts[st] += 1
    if not counts:
        for c in deck:
            typ = c.get("type") or ""
            if "Creature" not in typ:
                continue
            for st in _creature_subtypes(typ):
                counts[st] += 1
    if not counts:
        return None

    cmdr_types = set(_creature_subtypes((commander_card or {}).get("type") or ""))
    ranked = counts.most_common()

    # Prefer a subtype that also sits on the commander and clears a small floor.
    for typ, n in ranked:
        if typ in cmdr_types and n >= 3 and typ not in _RACE_CHAFF:
            return typ.lower()
    for typ, n in ranked:
        if typ in cmdr_types and n >= 3:
            return typ.lower()

    # Skip a race word when a more specific type is also present in force.
    best, n = ranked[0]
    if best in _RACE_CHAFF:
        for typ, n2 in ranked[1:]:
            if typ not in _RACE_CHAFF and n2 >= 3:
                return typ.lower()
    if n >= 3:
        return best.lower()
    return ranked[0][0].lower()


def _dedupe_keep(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def archetype_mechanics(deck: list[dict],
                        signals: dict | None = None) -> list[str]:
    """Shape words read off the list so provisional_conditions can diverge.

    Used when overview.roles is empty (from_counts bakes). Countable only —
    token makers, counterspell density, win-the-game lines, ramp package.
    Picks one primary shape so a token-heavy ramp list does not also read as
    control and collapse every precon onto the same thesis conditions.
    """
    counts = (signals or {}).get("counts") or {}
    texts = [(c.get("text") or "").lower() for c in deck]

    token_cards = sum(
        1 for t in texts
        if "token" in t and ("create" in t or "put" in t)
    )
    avg = counts.get("avg_cmc")
    creatures = sum(1 for c in deck if "Creature" in (c.get("type") or ""))
    counters = int(counts.get("counters") or 0)
    instants = int(counts.get("instants") or 0)
    win = sum(1 for t in texts if "win the game" in t)
    comboish = sum(
        1 for t in texts
        if re.search(r"\binfinite\b|\bcombo\b|storm count|"
                     r"copy target (instant|sorcery)", t)
    )
    ramp = int(counts.get("mana_rocks") or 0) + int(counts.get("land_ramp") or 0)

    scores: dict[str, float] = {}
    # Floor is high enough that staple Sol Ring + a couple of token makers
    # do not make every precon "go wide".
    if token_cards >= 8:
        scores["tokens"] = float(token_cards)
    if avg is not None and float(avg) <= 2.6 and creatures >= 20:
        scores["aggro"] = (2.6 - float(avg)) * creatures
    if counters >= 4 or (instants >= 14 and counters >= 2):
        scores["control"] = float(counters * 3 + instants)
    if win >= 1 or comboish >= 3:
        scores["combo"] = float(win * 5 + comboish)
    if ramp >= 12:
        scores["ramp"] = float(ramp)

    if not scores:
        return []

    winner = max(scores, key=scores.get)
    # Aggro + tokens is one real shape (go-wide beatdown). Other pairs fight.
    if winner in ("aggro", "tokens") and "aggro" in scores and "tokens" in scores:
        return _dedupe_keep(["go wide", "tokens", "aggro", "cheap", "refill"])
    return {
        "tokens": ["go wide", "tokens"],
        "aggro": ["aggro", "cheap", "refill"],
        "control": ["control", "hold", "accumulate"],
        "combo": ["finisher", "one big"],
        "ramp": ["ramp into", "expensive"],
    }[winner]


def mechanics_from_analysis(overview_data: dict, deck: list[dict],
                            signals: dict | None = None) -> list[str]:
    """Role keys when the model wrote them; otherwise archetype words from cards."""
    mechs: list[str] = []
    for role, line in (overview_data.get("roles") or {}).items():
        if isinstance(role, str) and role.strip():
            mechs.append(role.strip())
        if isinstance(line, str) and line.strip() and role not in mechs:
            # Keep the role name only — the line is prose for the page, not a
            # mechanic tag provisional_conditions can read.
            pass
    if mechs:
        return _dedupe_keep(mechs)[:8]
    return archetype_mechanics(deck, signals)[:8]


def brief_from_analysis(
    overview_data: dict,
    deck: list[dict],
    commander_card: dict | None = None,
    signals: dict | None = None,
) -> dict[str, Any]:
    """Build the brief the rest of the pipeline reads — from the cards, not an ask.

    strategy / angle come from the already-computed overview. theme is the
    dominant creature type. mechanics come from roles when present, otherwise
    from countable archetype signals so thesis conditions can differ across
    precons. source is always "precon".
    """
    overview_data = overview_data or {}
    return {
        "strategy": str(overview_data.get("strategy") or "").strip(),
        "theme": theme_from_deck(deck, commander_card),
        "mechanics": mechanics_from_analysis(overview_data, deck, signals),
        "angle": str(overview_data.get("how_it_works") or "").strip(),
        "source": "precon",
    }


def conditions_for_brief(brief: dict) -> list[dict]:
    """Thesis conditions from strategy + mechanics only.

    brief.angle is how_it_works, which cites sim curve labels like "Seven mana
    by turn 6". Feeding that into provisional_conditions makes every precon
    look high-curve. The displayed angle stays intact; conditions read the plan.
    """
    return card_score.provisional_conditions({
        "strategy": brief.get("strategy") or "",
        "mechanics": brief.get("mechanics") or [],
        "plan": "",
        "angle": "",
        "does": "",
    })


def build_payload(
    *,
    precon_id: str,
    name: str,
    source: str,
    commander: str,
    commander_card: dict,
    deck: list[dict],
    signals: dict,
    overview_data: dict,
    sim_out: dict,
    brief: dict | None = None,
    conditions: list[dict] | None = None,
) -> dict[str, Any]:
    """Assemble a stamped bake entry. Prices are stripped — refreshed at view time."""
    deck_clean = [_strip_price(c) for c in deck]
    cmdr_clean = _strip_price(commander_card)
    brief_data = brief or brief_from_analysis(
        overview_data, deck, commander_card, signals)
    conds = conditions or list(
        sim_out.get("conditions")
        or conditions_for_brief(brief_data)
    )
    payload = {
        "id": str(precon_id),
        "name": name,
        "source": source,
        "commander": commander,
        "commander_card": cmdr_clean,
        "deck": deck_clean,
        "signals": signals,
        "overview": overview_data,
        "brief": brief_data,
        "sim": {
            "runs": sim_out.get("runs"),
            "curves": sim_out.get("curves") or [],
            "diagnostics": sim_out.get("diagnostics") or {},
            "assumptions": sim_out.get("assumptions") or sim.ASSUMPTIONS,
            "good_hand": sim_out.get("good_hand"),
            "bad_hand": sim_out.get("bad_hand"),
            "conditions": conds,
            "payoff_cards": overview.payoff_names_from_deck(deck),
            "seed": PRECON_SIM_SEED,
        },
        "analysis_version": int(ANALYSIS_VERSION),
        "sim_version": int(sim.SIM_VERSION),
        "deck_hash": bake.deck_list_hash(deck),
    }
    return payload


def _strip_price(card: dict) -> dict:
    out = dict(card)
    out.pop("usd", None)
    return out


def run_precon_sim(deck: list[dict], cmdr_cmc: int,
                   conditions: list[dict] | None = None) -> dict:
    """Goldfish with the fixed precon seed — same numbers every bake."""
    payoffs = overview.payoff_names_from_deck(deck)
    conds = conditions or overview.BUILD_CONDITIONS
    return sim.run(
        deck, cmdr_cmc, payoffs, conds,
        runs=PRECON_SIM_RUNS, seed=PRECON_SIM_SEED,
    )


def apply_prices(deck: list[dict], commander_card: dict,
                 priced: dict[str, dict]) -> tuple[list[dict], dict]:
    """Copy live usd onto baked cards. Returns new deck + commander_card."""
    def with_usd(card: dict) -> dict:
        out = dict(card)
        hit = priced.get(card.get("name") or "")
        if hit is not None and "usd" in hit:
            out["usd"] = hit.get("usd")
        else:
            out.pop("usd", None)
        return out

    return [with_usd(c) for c in deck], with_usd(commander_card)


def response_from_bake(
    cached: dict,
    *,
    deck: list[dict],
    commander_card: dict,
    price: float,
    budget: dict,
    legal_report: dict,
    archidekt: str,
    tcgplayer: dict,
    card_pool: str,
) -> dict[str, Any]:
    """Shape a /api/precons/{id} response from a fresh bake + live prices."""
    brief = cached.get("brief") or brief_from_analysis(
        cached.get("overview") or {},
        deck,
        commander_card,
        cached.get("signals") or {},
    )
    return {
        "unresolved": [],
        "commander_pick": {
            "status": "found",
            "commander": cached["commander"],
            "candidates": [],
        },
        "entries": [],
        "deck": deck,
        "notes": [],
        "precon": {
            "id": str(cached["id"]),
            "name": cached.get("name") or "",
            "source": cached.get("source") or "",
        },
        "commander": cached["commander"],
        "commander_card": commander_card,
        "price": price,
        "budget": budget,
        "archidekt": archidekt,
        "tcgplayer": tcgplayer,
        "signals": cached.get("signals") or {},
        "overview": cached.get("overview") or {},
        "brief": brief,
        "sim": cached.get("sim") or {},
        "legal": legal_report["ok"],
        "buyable": legal_report["buyable"],
        "violations": legal_report["violations"],
        "banned": legal_report["banned"],
        "card_pool": card_pool,
        "legality_mode": legal_report["mode"],
        "baked": True,
        "deck_hash": cached.get("deck_hash"),
        "analysis_version": cached.get("analysis_version"),
        "sim_version": cached.get("sim_version"),
    }
