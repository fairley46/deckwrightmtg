"""
mechanics.py — Scryfall catalogs + family taxonomy + deck density.

This is the substrate for plan math, not a label printer.

Two layers:
  data/mechanic_taxonomy.json  — play-structure map (what / how / supports / queries)
  data/mechanic_families.json  — reviewed plan_role / math_kind / signals / relations

Adapter merges the map into families. Reviewed placements always win. Supports use
card *shapes* and Scryfall queries — never invented card names.

Every named mechanic sits in a *family* with:
  plan_role  — where it sits on a synergy graph
               (condition | engine | enabler | payoff | modifier | cost | protection)
  math_kind  — what kind of equation it changes
               (threshold_count | resource_loop | rate | cost_alt | combat_filter | …)

tag_deck() counts density by mechanic and family and buckets cards by plan_role
so later work can reason about composition instead of guessing from names.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from deckwright import instance

# In-repo taxonomy — reviewed by hand when sets add ~10–15 mechanics/year.
REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
FAMILIES_PATH = REPO_DIR / "data" / "mechanic_families.json"
# Play-structure map (source of truth for what/how/supports). Adapter merges
# into families; reviewed plan_role/math_kind/signals/relations always win.
TAXONOMY_PATH = REPO_DIR / "data" / "mechanic_taxonomy.json"

# Live catalog cache (Scryfall). Same home as vocab.json.
DEFAULT_CATALOG_PATH = instance.home_dir() / "mechanics.json"

# Longest-prefix match: taxonomy path → reviewed family id.
# Order matters — more specific prefixes first.
PATH_TO_FAMILY: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Conditions", "Thresholds", "Battlefield"), "thresholds_battlefield"),
    (("Conditions", "Thresholds", "Graveyard"), "thresholds_graveyard"),
    (("Conditions", "Thresholds", "Event Occurred this Turn"), "thresholds_turn_event"),
    (("Conditions", "Thresholds", "Other"), "thresholds_turn_event"),
    (("Conditions", "Variables"), "variables"),
    (("Conditions", "Batching"), "batching"),
    (("Activated", "Cast from Graveyard"), "cast_from_graveyard"),
    (("Activated", "Return from Graveyard"), "return_from_graveyard"),
    (("Activated", "Hand Abilities"), "hand_abilities"),
    (("Activated", "Battlefield", "Improve This Permanent"), "counters_plus"),
    (("Activated", "Battlefield", "Change Types"), "equipment_aura"),
    (("Activated", "Battlefield"), "counters_plus"),
    (("Alternate Costs",), "alternate_costs"),
    (("Additional Costs",), "additional_costs"),
    (("Variable Cost Reduction",), "alternate_costs"),
    (("Situational Reduced Costs",), "alternate_costs"),
    (("Alternatives to Mana",), "alternate_costs"),
    (("Face-Down Cards",), "face_down"),
    (("Timing",), "timing"),
    (("When You Cast This",), "when_you_cast"),
    (("Delayed Re-Cast",), "delayed_recast"),
    (("Actions", "Cast Spells"), "when_you_cast"),
    (("Can / Can't Attack / Block", "Evasion"), "evasion"),
    (("Can / Can't Attack / Block",), "combat_timing"),
    (("Attacking Triggers",), "attacking_triggers"),
    (("Saboteur Abilities",), "saboteur"),
    (("Combat Rules Modification",), "combat_timing"),
    (("Blocking / Not Blocking",), "combat_timing"),
    (("End of Combat",), "combat_timing"),
    (("Actions", "Tokens"), "tokens"),
    (("Actions", "Top of Library Manipulation"), "library_manipulation"),
    (("Actions", "New Components"), "new_components"),
    (("Actions", "Distribute Counters"), "counters_plus"),
    (("Triggers", "Other Permanents ETB"), "etb_triggers"),
    (("Triggers", "Other Creatures ETB"), "etb_triggers"),
    (("Triggers", "This Enters the Battlefield"), "etb_triggers"),
    (("Triggers", "ETB with Counters"), "counters_plus"),
    (("Triggers", "You Cast a Spell"), "spell_cast_triggers"),
    (("Triggers", "This Dies"), "dies_triggers"),
    (("Protect Permanents",), "protection"),
    (("Damage Modification",), "damage_modification"),
    (("Counters", "Permanents"), "counters_plus"),
    (("Subtypes and Properties", "With their own Rules"), "equipment_aura"),
    (("Library Manipulation",), "library_manipulation"),
    (("Formats", "Commander"), "new_components"),
)

# Scryfall catalog endpoints that name mechanics (not creature types).
CATALOG_KEYS = (
    ("keyword_abilities", "keyword-abilities"),
    ("keyword_actions", "keyword-actions"),
    ("ability_words", "ability-words"),
)

# plan_role allowlist — keep tight; extend via the taxonomy, not free text.
PLAN_ROLES = frozenset({
    "condition",   # metalcraft, raid, delirium — state gate
    "engine",      # flashback, landfall loops — repeatable resource
    "enabler",     # puts the condition/engine online
    "payoff",      # rewards the condition/engine
    "modifier",    # flying, lifelink — changes rate/shape of an action
    "cost",        # convoke, delve, kicker — changes the mana equation
    "protection",  # hexproof, indestructible — survival
})

# Calendar / UI order — engines and enablers first (SCORING-CONTRACT finding).
ROLE_DISPLAY_ORDER: tuple[str, ...] = (
    "engine", "payoff", "enabler", "condition", "modifier", "cost", "protection",
)

ROLE_PLURAL: dict[str, str] = {
    "engine": "engines",
    "payoff": "payoffs",
    "enabler": "enablers",
    "condition": "conditions",
    "modifier": "modifiers",
    "cost": "costs",
    "protection": "protections",
}


def _empty_catalog() -> dict[str, Any]:
    return {
        "fetched_at": None,
        "keyword_abilities": [],
        "keyword_actions": [],
        "ability_words": [],
    }


def load_catalog(path: pathlib.Path | None = None) -> dict[str, Any]:
    """Load cached Scryfall mechanic catalogs. Missing/corrupt → empty shape."""
    path = path or DEFAULT_CATALOG_PATH
    if not path.is_file():
        return _empty_catalog()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty_catalog()
    if not isinstance(data, dict):
        return _empty_catalog()
    out = _empty_catalog()
    out["fetched_at"] = data.get("fetched_at")
    for key, _ in CATALOG_KEYS:
        vals = data.get(key) or []
        if isinstance(vals, list):
            out[key] = [str(v) for v in vals if isinstance(v, str)]
    return out


def write_catalog(payload: dict[str, Any], path: pathlib.Path | None = None) -> pathlib.Path:
    """Persist a catalog. Refuses to write when every list is empty."""
    path = path or DEFAULT_CATALOG_PATH
    abilities = list(payload.get("keyword_abilities") or [])
    actions = list(payload.get("keyword_actions") or [])
    words = list(payload.get("ability_words") or [])
    if not (abilities or actions or words):
        raise ValueError("refusing to write empty mechanics catalog")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "fetched_at": payload.get("fetched_at"),
        "keyword_abilities": abilities,
        "keyword_actions": actions,
        "ability_words": words,
    }
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return path


async def fetch_catalog(
    scry: Callable[[str], Awaitable[dict]],
    path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Pull Scryfall keyword catalogs and cache them. Preserves old cache on total miss."""
    path = path or DEFAULT_CATALOG_PATH
    built = _empty_catalog()
    built["fetched_at"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    any_ok = False
    for key, endpoint in CATALOG_KEYS:
        try:
            j = await scry(f"/catalog/{endpoint}")
        except Exception:
            continue
        names = [str(v) for v in (j.get("data") or []) if isinstance(v, str)]
        if names:
            built[key] = sorted(set(names), key=str.lower)
            any_ok = True
    if any_ok:
        write_catalog(built, path)
        return built
    # Total miss — keep whatever was on disk.
    existing = load_catalog(path)
    if existing["keyword_abilities"] or existing["keyword_actions"] or existing["ability_words"]:
        return existing
    return built


def all_catalog_names(catalog: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key, _ in CATALOG_KEYS:
        names.update(catalog.get(key) or [])
    return names


def load_taxonomy(path: pathlib.Path | None = None) -> dict[str, Any]:
    """Load the play-structure map (what / how / supports). Missing → empty."""
    path = path or TAXONOMY_PATH
    empty: dict[str, Any] = {"version": 0, "mechanics": [], "notes": []}
    if not path.is_file():
        return dict(empty)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(data, dict):
        return dict(empty)
    mechs = data.get("mechanics") or []
    if not isinstance(mechs, list):
        mechs = []
    return {
        "version": data.get("version", 1),
        "updated": data.get("updated"),
        "source": data.get("source"),
        "notes": data.get("notes") or [],
        "mechanics": mechs,
    }


def taxonomy_leaves(taxonomy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Named mechanic leaves with path/how/supports. Skips malformed rows."""
    taxonomy = taxonomy if taxonomy is not None else load_taxonomy()
    out: list[dict[str, Any]] = []
    for raw in taxonomy.get("mechanics") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        path = raw.get("path") if isinstance(raw.get("path"), list) else []
        supports = raw.get("supports") if isinstance(raw.get("supports"), dict) else {}
        out.append({
            "name": name.strip(),
            "path": [str(p) for p in path if isinstance(p, str)],
            "how": str(raw.get("how") or ""),
            "supports": {
                "enablers": [str(x) for x in (supports.get("enablers") or []) if isinstance(x, str)],
                "payoffs": [str(x) for x in (supports.get("payoffs") or []) if isinstance(x, str)],
                "queries": [str(x) for x in (supports.get("queries") or []) if isinstance(x, str)],
            },
            "evergreen": bool(raw.get("evergreen")),
            "deciduous": bool(raw.get("deciduous")),
        })
    return out


def _taxonomy_index(taxonomy: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """lowercase name → leaf."""
    return {m["name"].lower(): m for m in taxonomy_leaves(taxonomy)}


def _family_for_path(path: list[str]) -> str | None:
    for prefix, fam in PATH_TO_FAMILY:
        if tuple(path[: len(prefix)]) == prefix:
            return fam
    return None


def load_families(
    path: pathlib.Path | None = None,
    *,
    merge: bool = True,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the reviewed family taxonomy from the repo.

    merge=True (default): overlay the play-structure map — reviewed
    plan_role/math_kind/placement/signals/relations win; map fills gaps and
    attaches how/supports for reasoning.
    """
    path = path or FAMILIES_PATH
    empty = {"version": 0, "families": {}, "lineage": [],
             "oracle_signals": {}, "relations": [], "taxonomy_index": {}}
    if not path.is_file():
        reviewed = dict(empty)
    else:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            reviewed = dict(empty)
        else:
            if not isinstance(data, dict):
                reviewed = dict(empty)
            else:
                families = data.get("families") or {}
                if not isinstance(families, dict):
                    families = {}
                lineage = data.get("lineage") or []
                if not isinstance(lineage, list):
                    lineage = []
                signals = data.get("oracle_signals") or {}
                if not isinstance(signals, dict):
                    signals = {}
                relations = data.get("relations") or []
                if not isinstance(relations, list):
                    relations = []
                reviewed = {
                    "version": data.get("version", 1),
                    "families": families,
                    "lineage": lineage,
                    "oracle_signals": signals,
                    "relations": relations,
                }
    if not merge:
        reviewed["taxonomy_index"] = {}
        return reviewed
    return merge_families(reviewed, taxonomy)


def merge_families(
    reviewed: dict[str, Any] | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge play map into reviewed families. Never replaces reviewed roles.

    - Existing mechanic placements keep their family / plan_role / math_kind.
    - Map-only leaves are appended into the path-mapped family when one exists.
    - oracle_signals and relations are copied from reviewed only.
    - taxonomy_index carries how/supports for mechanic_meta().
    """
    reviewed = reviewed if reviewed is not None else load_families(merge=False)
    taxonomy = taxonomy if taxonomy is not None else load_taxonomy()
    tax_idx = _taxonomy_index(taxonomy)

    # Deep-ish copy of reviewed families so we can append names.
    families: dict[str, Any] = {}
    for fam_name, body in (reviewed.get("families") or {}).items():
        if not isinstance(body, dict):
            continue
        families[fam_name] = {
            "plan_role": body.get("plan_role"),
            "math_kind": body.get("math_kind"),
            "description": body.get("description"),
            "mechanics": list(body.get("mechanics") or []),
        }

    known_lower = {
        m.lower()
        for body in families.values()
        for m in (body.get("mechanics") or [])
        if isinstance(m, str)
    }

    for leaf in tax_idx.values():
        key = leaf["name"].lower()
        if key in known_lower:
            continue
        fam = _family_for_path(leaf["path"])
        if not fam:
            continue
        if fam not in families:
            # Don't invent new family buckets — triage places those later.
            continue
        families[fam].setdefault("mechanics", []).append(leaf["name"])
        known_lower.add(key)

    return {
        "version": reviewed.get("version", 1),
        "families": families,
        "lineage": list(reviewed.get("lineage") or []),
        "oracle_signals": dict(reviewed.get("oracle_signals") or {}),
        "relations": list(reviewed.get("relations") or []),
        "taxonomy_index": tax_idx,
    }


def _index_families(families: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """mechanic name (case-preserved from file) -> meta."""
    idx: dict[str, dict[str, Any]] = {}
    for fam_name, body in (families.get("families") or {}).items():
        if not isinstance(body, dict):
            continue
        role = body.get("plan_role") or "modifier"
        math_kind = body.get("math_kind") or "unspecified"
        for mech in body.get("mechanics") or []:
            if not isinstance(mech, str) or not mech.strip():
                continue
            idx[mech] = {
                "name": mech,
                "family": fam_name,
                "plan_role": role if role in PLAN_ROLES else "modifier",
                "math_kind": math_kind,
                "reviewed": True,
            }
    return idx


def mechanic_meta(name: str, families: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lookup one mechanic. Unknown → reviewed=False, no family.

    When the play map is merged, also returns how / supports / path for reasoning.
    """
    families = families if families is not None else load_families()
    idx = _index_families(families)
    tax_idx = families.get("taxonomy_index") or {}
    if not isinstance(tax_idx, dict) or not tax_idx:
        tax_idx = _taxonomy_index()

    def _with_map(meta: dict[str, Any]) -> dict[str, Any]:
        leaf = tax_idx.get((meta.get("name") or name or "").lower())
        if not leaf:
            leaf = tax_idx.get((name or "").lower())
        if leaf:
            meta["how"] = leaf.get("how") or ""
            meta["supports"] = dict(leaf.get("supports") or {})
            meta["path"] = list(leaf.get("path") or [])
            meta["evergreen"] = bool(leaf.get("evergreen"))
            meta["deciduous"] = bool(leaf.get("deciduous"))
        return meta

    if name in idx:
        return _with_map(dict(idx[name]))
    # Case-insensitive fallback — Scryfall canonical casing wins when present.
    lower = {k.lower(): k for k in idx}
    hit = lower.get((name or "").lower())
    if hit:
        return _with_map(dict(idx[hit]))
    leaf = tax_idx.get((name or "").lower())
    if leaf:
        return {
            "name": leaf["name"],
            "family": _family_for_path(leaf.get("path") or []),
            "plan_role": None,
            "math_kind": None,
            "reviewed": False,
            "how": leaf.get("how") or "",
            "supports": dict(leaf.get("supports") or {}),
            "path": list(leaf.get("path") or []),
            "evergreen": bool(leaf.get("evergreen")),
            "deciduous": bool(leaf.get("deciduous")),
        }
    return {
        "name": name,
        "family": None,
        "plan_role": None,
        "math_kind": None,
        "reviewed": False,
    }


def reviewed_names(families: dict[str, Any] | None = None) -> set[str]:
    families = families if families is not None else load_families()
    return set(_index_families(families))


def unreviewed(catalog: dict[str, Any], families: dict[str, Any] | None = None) -> list[str]:
    """Catalog names with no family placement — human review queue."""
    families = families if families is not None else load_families()
    known = {n.lower() for n in reviewed_names(families)}
    missing = sorted(
        (n for n in all_catalog_names(catalog) if n.lower() not in known),
        key=str.lower,
    )
    return missing


def _is_land(card: dict) -> bool:
    return "land" in (card.get("type") or "").lower()


def _compile_signals(families: dict[str, Any]) -> list[tuple[str, dict[str, Any], re.Pattern]]:
    """Oracle-text signals: non-keyword plan nodes (lifegain triggers, drain, …)."""
    out: list[tuple[str, dict[str, Any], re.Pattern]] = []
    raw = families.get("oracle_signals") or {}
    if not isinstance(raw, dict):
        return out
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        pats = [p for p in (body.get("patterns") or []) if isinstance(p, str) and p]
        if not pats:
            continue
        try:
            rx = re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
        except re.error:
            continue
        out.append((name, body, rx))
    return out


def tag_deck(deck: list[dict], families: dict[str, Any] | None = None) -> dict[str, Any]:
    """Count mechanic density on a decklist.

    Density denominator is non-lands. Two sources, never invented card names:

    1. Scryfall `keywords` on each card → family taxonomy
    2. `oracle_signals` regexes → non-keyword plan nodes (lifegain, drain, …)

    Keywords alone cannot see Sanguine Bond / Exquisite Blood. Signals exist so
    the plan math can.
    """
    families = families if families is not None else load_families()
    idx = _index_families(families)
    signals = _compile_signals(families)

    lands = sum(1 for c in deck if _is_land(c))
    nonlands = [c for c in deck if not _is_land(c)]
    denom = len(nonlands) or 1

    mech_counts: Counter[str] = Counter()
    # Per-card family membership (a card with Flying+Lifelink contributes to
    # both families once each).
    family_card_counts: Counter[str] = Counter()
    role_card_counts: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()

    for card in nonlands:
        kws = [k for k in (card.get("keywords") or []) if isinstance(k, str)]
        mech_counts.update(kws)
        fams_here: set[str] = set()
        roles_here: set[str] = set()
        for kw in kws:
            meta = idx.get(kw)
            if meta is None:
                for canon, m in idx.items():
                    if canon.lower() == kw.lower():
                        meta = m
                        break
            if not meta:
                continue
            fams_here.add(meta["family"])
            roles_here.add(meta["plan_role"])
        text = card.get("text") or ""
        for sig_name, body, rx in signals:
            if rx.search(text):
                signal_counts[sig_name] += 1
                role = body.get("plan_role")
                if role in PLAN_ROLES:
                    roles_here.add(role)
                fam = body.get("family")
                if isinstance(fam, str) and fam:
                    fams_here.add(fam)
        for f in fams_here:
            family_card_counts[f] += 1
        for r in roles_here:
            role_card_counts[r] += 1

    mechanics_out = [
        {
            "name": name,
            "count": count,
            "density": round(count / denom, 4),
            **{k: v for k, v in (idx.get(name) or mechanic_meta(name, families)).items()
               if k != "name"},
        }
        for name, count in mech_counts.most_common()
    ]
    families_out = [
        {
            "family": fam,
            "count": count,
            "density": round(count / denom, 4),
            "plan_role": (families.get("families") or {}).get(fam, {}).get("plan_role"),
            "math_kind": (families.get("families") or {}).get(fam, {}).get("math_kind"),
        }
        for fam, count in family_card_counts.most_common()
    ]
    signals_out = []
    for name, count in signal_counts.most_common():
        body = (families.get("oracle_signals") or {}).get(name) or {}
        signals_out.append({
            "name": name,
            "count": count,
            "density": round(count / denom, 4),
            "plan_role": body.get("plan_role"),
            "math_kind": body.get("math_kind"),
            "family": body.get("family"),
        })
    by_role = {role: int(role_card_counts.get(role, 0)) for role in sorted(PLAN_ROLES)}
    return {
        "version": 1,
        "nonlands": len(nonlands),
        "lands": lands,
        "mechanics": mechanics_out,
        "families": families_out,
        "signals": signals_out,
        "by_role": by_role,
        "taxonomy_version": families.get("version", 1),
    }


def attach_mechanics(data: dict, families: dict[str, Any] | None = None) -> dict:
    """Stamp a mechanics density block onto a saved-deck dict. Mutates and returns it."""
    data["mechanics"] = tag_deck(data.get("deck") or [], families)
    return data


# ---------------------------------------------------------------- relations
#
# Lineage (Storied ← Ascend) and explicit family edges (tokens → go_wide) are
# how plans compose. related_* helpers are what token perception and the commander difficulty sentence read — never a
# free-text guess at "what synergizes."


def lineage_neighbors(name: str, families: dict[str, Any] | None = None) -> list[dict]:
    """Mechanics linked by lineage edges to/from `name` (case-insensitive)."""
    families = families if families is not None else load_families()
    key = (name or "").lower()
    out = []
    for edge in families.get("lineage") or []:
        if not isinstance(edge, dict):
            continue
        frm, to = edge.get("from") or "", edge.get("to") or ""
        if frm.lower() == key:
            out.append({"name": to, "direction": "to", "note": edge.get("note") or ""})
        elif to.lower() == key:
            out.append({"name": frm, "direction": "from", "note": edge.get("note") or ""})
    return out


def family_relations(family: str, families: dict[str, Any] | None = None) -> list[dict]:
    """Directed edges touching `family` from the relations table."""
    families = families if families is not None else load_families()
    key = (family or "").lower()
    out = []
    for edge in families.get("relations") or []:
        if not isinstance(edge, dict):
            continue
        frm, to = edge.get("from") or "", edge.get("to") or ""
        if frm.lower() == key:
            out.append({
                "family": to, "direction": "to",
                "relation": edge.get("relation") or "related",
                "note": edge.get("note") or "",
            })
        elif to.lower() == key:
            out.append({
                "family": frm, "direction": "from",
                "relation": edge.get("relation") or "related",
                "note": edge.get("note") or "",
            })
    return out


def related(name: str, families: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full relation view for one mechanic: family, lineage, sibling families.

    This is the unit the commander difficulty sentence uses to expand a goal ("more tokens") into the
    families and signals that actually feed or spend that plan.
    """
    families = families if families is not None else load_families()
    meta = mechanic_meta(name, families)
    fam = meta.get("family")
    same_family = []
    if fam:
        body = (families.get("families") or {}).get(fam) or {}
        same_family = [m for m in (body.get("mechanics") or [])
                       if isinstance(m, str) and m.lower() != (name or "").lower()]
    # Signals that declare this family.
    signals = []
    for sig_name, body in (families.get("oracle_signals") or {}).items():
        if isinstance(body, dict) and body.get("family") == fam:
            signals.append(sig_name)
    return {
        "mechanic": meta,
        "same_family": same_family,
        "lineage": lineage_neighbors(name, families),
        "family_relations": family_relations(fam, families) if fam else [],
        "signals": signals,
    }


def expand_goal_families(seed_families: list[str],
                         families: dict[str, Any] | None = None,
                         *, depth: int = 1) -> list[str]:
    """Grow a goal's family set along taxonomy edges (feeds/composes/needs/
    multiplies/spends/pairs)."""
    families = families if families is not None else load_families()
    seen = {f for f in seed_families if f}
    frontier = set(seen)
    for _ in range(max(0, depth)):
        nxt: set[str] = set()
        for f in frontier:
            for edge in family_relations(f, families):
                # Both directions matter: "tokens feeds go_wide" means a
                # go_wide goal also wants token enablers.
                other = edge.get("family")
                if other and other not in seen:
                    nxt.add(other)
        seen |= nxt
        frontier = nxt
        if not frontier:
            break
    return sorted(seen)


def plan_role_coverage(
    families: dict[str, Any] | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """How much of the taxonomy join carries a plan_role — for UI honesty.

    "3 engines" must not be read as "only 3 engines exist." The
    calendar states this coverage next to the counts.
    """
    families = families if families is not None else load_families()
    leaves = taxonomy_leaves(taxonomy)
    by_role: dict[str, int] = {r: 0 for r in ROLE_DISPLAY_ORDER}
    mapped = 0
    for leaf in leaves:
        role = mechanic_meta(leaf["name"], families).get("plan_role")
        if role in PLAN_ROLES:
            mapped += 1
            by_role[role] = by_role.get(role, 0) + 1
    total = len(leaves)
    unmapped = total - mapped
    pct = int(round(100.0 * mapped / total)) if total else 0
    return {
        "taxonomy": total,
        "mapped": mapped,
        "unmapped": unmapped,
        "pct": pct,
        "by_role": by_role,
    }


def queries_by_plan_role(
    families: dict[str, Any] | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Verified taxonomy supports.queries grouped by plan_role.

    Only mechanics with a known plan_role contribute. Duplicate query strings
    within a role are collapsed. Order is stable (taxonomy leaf order).
    """
    families = families if families is not None else load_families()
    out: dict[str, list[str]] = {r: [] for r in ROLE_DISPLAY_ORDER}
    seen: dict[str, set[str]] = {r: set() for r in ROLE_DISPLAY_ORDER}
    for leaf in taxonomy_leaves(taxonomy):
        role = mechanic_meta(leaf["name"], families).get("plan_role")
        if role not in PLAN_ROLES:
            continue
        for q in (leaf.get("supports") or {}).get("queries") or []:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if not q or q in seen[role]:
                continue
            seen[role].add(q)
            out[role].append(q)
    return out


def _unquote_scry_term(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _card_oracle_texts(card: dict) -> list[str]:
    texts: list[str] = []
    if card.get("oracle_text"):
        texts.append(str(card["oracle_text"]))
    elif card.get("text"):
        texts.append(str(card["text"]))
    for face in card.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        if face.get("oracle_text"):
            texts.append(str(face["oracle_text"]))
        elif face.get("text"):
            texts.append(str(face["text"]))
    return texts


def _card_type_line(card: dict) -> str:
    parts = [str(card.get("type_line") or "")]
    for face in card.get("card_faces") or []:
        if isinstance(face, dict) and face.get("type_line"):
            parts.append(str(face["type_line"]))
    return " ".join(parts)


def card_matches_taxonomy_query(card: dict, query: str) -> bool:
    """Evaluate one verified taxonomy query against a Scryfall card dict.

    Supports the shapes the taxonomy query gate checks: keyword:, o:, t:, is:. Unknown shapes
    are not matches — an unknown is not a zero, and a fallback is not a match.
    """
    q = (query or "").strip()
    if not q or not isinstance(card, dict):
        return False
    if q.startswith("keyword:"):
        want = _unquote_scry_term(q[len("keyword:"):]).lower()
        if not want:
            return False
        return any(
            isinstance(k, str) and k.lower() == want
            for k in (card.get("keywords") or [])
        )
    if q.startswith("o:"):
        needle = _unquote_scry_term(q[2:]).lower()
        if not needle:
            return False
        return any(needle in t.lower() for t in _card_oracle_texts(card))
    if q.startswith("t:"):
        needle = _unquote_scry_term(q[2:]).lower()
        if not needle:
            return False
        return needle in _card_type_line(card).lower()
    if q.startswith("is:"):
        flag = q[3:].strip().lower()
        layout = str(card.get("layout") or "").lower()
        if flag == "split":
            return layout == "split"
        if flag == "mdfc":
            return layout == "modal_dfc"
        return False
    return False


def classify_card_roles(
    card: dict,
    role_queries: dict[str, list[str]] | None = None,
) -> set[str]:
    """Which plan_roles a card matches via taxonomy queries. May be empty."""
    role_queries = role_queries if role_queries is not None else queries_by_plan_role()
    hits: set[str] = set()
    for role, queries in role_queries.items():
        if role not in PLAN_ROLES:
            continue
        for q in queries:
            if card_matches_taxonomy_query(card, q):
                hits.add(role)
                break
    return hits


def format_role_counts(roles: dict[str, int] | None) -> str:
    """'3 engines · 6 payoffs · 12 enablers' — omits zero roles."""
    if not roles:
        return ""
    parts: list[str] = []
    for role in ROLE_DISPLAY_ORDER:
        n = int(roles.get(role) or 0)
        if n <= 0:
            continue
        if n == 1:
            parts.append(f"1 {role}")
        else:
            parts.append(f"{n} {ROLE_PLURAL.get(role, role + 's')}")
    return " · ".join(parts)


def card_plan_hits(card: dict, families: dict[str, Any] | None = None) -> dict[str, Any]:
    """Which families/signals/roles one card touches — for cut/add scoring."""
    families = families if families is not None else load_families()
    idx = _index_families(families)
    signals = _compile_signals(families)
    fams: set[str] = set()
    sigs: set[str] = set()
    roles: set[str] = set()
    kws = []
    for kw in card.get("keywords") or []:
        if not isinstance(kw, str):
            continue
        kws.append(kw)
        meta = idx.get(kw)
        if meta is None:
            for canon, m in idx.items():
                if canon.lower() == kw.lower():
                    meta = m
                    break
        if meta:
            fams.add(meta["family"])
            roles.add(meta["plan_role"])
    text = card.get("text") or ""
    for sig_name, body, rx in signals:
        if rx.search(text):
            sigs.add(sig_name)
            if body.get("plan_role") in PLAN_ROLES:
                roles.add(body["plan_role"])
            if isinstance(body.get("family"), str):
                fams.add(body["family"])
    return {
        "name": card.get("name"),
        "keywords": kws,
        "families": sorted(fams),
        "signals": sorted(sigs),
        "roles": sorted(roles),
    }
