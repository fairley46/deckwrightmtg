#!/usr/bin/env python3
"""CON-319: V2 bake-off harness.

Frozen before any candidate exists. Measures E (EDHREC fixture) and BLUE
(/api/build on green) today; A/B/C later by registration only.

    python scripts/v2_benchmark.py --validate
    python scripts/v2_benchmark.py --contestants E
    python scripts/v2_benchmark.py
    python test_v2_benchmark.py

The harness calls a build, reads the response, and computes every metric
itself. It does not import candidate internals. A candidate that scores
itself is not a measurement.

House rule: unknown is not a zero. A missing fixture, a failed HTTP build,
an unread spec, or a card with no oracle text is UNKNOWN — never a fake 0.

Goldfish is stochastic; seed is 319 (the card) and is stamped on every report.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "benchmarks"
COMMANDERS_PATH = DATA / "commanders.json"
CONTESTANTS_PATH = DATA / "contestants.json"
EDHREC_DIR = DATA / "edhrec"
REPORT_DIR = ROOT / "docs" / "benchmarks" / "runs"

# CON-328: measured BLUE builds exceed 300s on the demo host (~377s for Krenko).
# Transport timeout must allow collection; UNKNOWN on genuine timeout stays honest.
# PR #322 rewrite silently reverted this to 300 — pinned by test_v2_benchmark.
DEFAULT_BUILD_TIMEOUT = 1800.0

# Bello (floor case) hit the Claude CLI default of 180s. The harness cannot
# raise the server's LLM timeout; export DECKWRIGHT_LLM_TIMEOUT on long builds.
RECOMMENDED_LLM_TIMEOUT_S = 600.0
LLM_TIMEOUT_ENV = "DECKWRIGHT_LLM_TIMEOUT"

# Conventional ROLES spell slots. Frozen with the harness — not live skeleton.
NONLAND_N = 59
LANDS_N = 38
DECK_N = 99
SIM_SEED = 319
UA = "Deckwright/1.0 (CON-319 V2 benchmark harness)"

# Concepts 4 and 6 are not computable without a rules/threat engine (v2-design B.1).
UNCOMPUTABLE = frozenset({"soft_combos", "hidden_power"})

OK, UNKNOWN, UNREAD = "ok", "unknown", "unread"
OPPONENT_SUPPLIED = "opponent_supplied"

BuildFn = Callable[["BuildRequest"], "BuildResponse"]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_commanders(path: Path = COMMANDERS_PATH) -> dict:
    data = load_json(path)
    if not isinstance(data.get("commanders"), list):
        raise ValueError(f"{path} has no commanders list")
    return data


def load_contestant_registry(path: Path = CONTESTANTS_PATH) -> dict:
    data = load_json(path)
    if not isinstance(data.get("contestants"), list):
        raise ValueError(f"{path} has no contestants list")
    errors = validate_contestant_registry(data)
    if errors:
        raise ValueError(f"{path} invalid: " + "; ".join(errors))
    return data


_VALID_CONTESTANT_KINDS = frozenset({"edhrec_fixture", "http_build", "callable"})


def validate_contestant_registry(reg: dict) -> list[str]:
    """Registry shape + port/env uniqueness. Enable flags are data, not contract.

    Toggle contestants by editing contestants.json — tests should not pin
    which ids are enabled (CON-332 class).
    """
    errors: list[str] = []
    rows = reg.get("contestants")
    if not isinstance(rows, list) or not rows:
        return ["contestants must be a non-empty list"]

    seen_ids: set[str] = set()
    url_by_contestant: dict[str, str] = {}
    env_by_contestant: dict[str, str] = {}

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"contestants[{i}] must be an object")
            continue
        cid = row.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"contestants[{i}] missing id")
            continue
        if cid in seen_ids:
            errors.append(f"duplicate contestant id {cid!r}")
        seen_ids.add(cid)

        kind = row.get("kind")
        if kind not in _VALID_CONTESTANT_KINDS:
            errors.append(f"{cid}: unknown kind {kind!r}")
            continue

        enabled = bool(row.get("enabled"))

        if kind == "http_build":
            url_env = row.get("url_env")
            url_default = row.get("url_default")
            if not isinstance(url_env, str) or not url_env.strip():
                errors.append(f"{cid}: http_build requires url_env")
            elif url_env in env_by_contestant:
                other = env_by_contestant[url_env]
                errors.append(
                    f"url_env collision: {cid} and {other} both use {url_env!r}")
            else:
                env_by_contestant[url_env] = cid

            if not isinstance(url_default, str) or not url_default.strip():
                errors.append(f"{cid}: http_build requires url_default")
            elif url_default in url_by_contestant:
                other = url_by_contestant[url_default]
                errors.append(
                    f"url_default collision: {cid} and {other} "
                    f"both use {url_default!r}")
            else:
                url_by_contestant[url_default] = cid

            if enabled and (
                not isinstance(url_default, str)
                or not url_default.startswith("http://127.0.0.1:")
            ):
                errors.append(
                    f"{cid}: enabled http_build url_default must be "
                    f"http://127.0.0.1:<port>")

        elif kind == "callable" and enabled and not row.get("target"):
            errors.append(f"{cid}: enabled callable requires target")

    enabled_http = [
        r["id"] for r in rows
        if isinstance(r, dict)
        and r.get("kind") == "http_build"
        and r.get("enabled")
    ]
    if len(enabled_http) != len(set(enabled_http)):
        errors.append("enabled http_build ids must be unique")

    return errors


def enabled_resolveable_ids(registry: dict) -> list[str]:
    """Contestant ids resolve_contestants() picks with no --contestants filter."""
    out: list[str] = []
    for row in registry.get("contestants") or []:
        if not row.get("enabled"):
            continue
        kind = row.get("kind")
        if kind == "callable" and not row.get("target"):
            continue
        out.append(row["id"])
    return sorted(out)


def git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return UNKNOWN


def default_build_timeout() -> float:
    """HTTP timeout for /api/build contestants. Env overrides CLI default."""
    raw = os.environ.get("DECKWRIGHT_BUILD_TIMEOUT", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_BUILD_TIMEOUT


def rank_band(commander_rank: int | None) -> str:
    if commander_rank is None:
        return "no_rank"
    if commander_rank <= 100:
        return "top_100"
    if commander_rank <= 1000:
        return "101_1000"
    return "1001_plus"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _oracle(card: dict) -> str | None:
    for key in ("text", "oracle_text"):
        t = card.get(key)
        if isinstance(t, str) and t.strip():
            return t
    return None


def _type_line(card: dict) -> str:
    return (card.get("type") or card.get("type_line") or "") or ""


def _is_land(card: dict) -> bool:
    role = (card.get("role") or "").lower()
    if role in ("lands", "basic"):
        return True
    head = _type_line(card).lower().split("—")[0]
    return "land" in head.split()


def _is_creature(card: dict) -> bool:
    head = _type_line(card).lower().split("—")[0]
    return "creature" in head.split()


def _is_permanent(card: dict) -> bool:
    head = _type_line(card).lower().split("—")[0]
    return any(t in head.split() for t in
               ("creature", "artifact", "enchantment", "planeswalker",
                "battle", "land"))


def _cmc(card: dict) -> float | None:
    raw = card.get("cmc", card.get("mana_value"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _power(card: dict) -> float | None:
    raw = card.get("power")
    if raw is None:
        return None
    try:
        return float(str(raw).split("/")[0].replace("*", "0"))
    except (TypeError, ValueError):
        return None


def _usd(card: dict) -> float | None:
    raw = card.get("usd")
    if raw is None:
        prices = card.get("prices") or {}
        raw = prices.get("usd") if isinstance(prices, dict) else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _ci(card: dict) -> list[str]:
    raw = card.get("color_identity")
    if isinstance(raw, list):
        return [str(c) for c in raw]
    if isinstance(raw, str):
        return [c for c in raw if c in "WUBRG"]
    return []


def names_of(cards: list[dict]) -> list[str]:
    return [c.get("name") or "" for c in cards if c.get("name")]


def normalize_card(card: dict) -> dict:
    """Map Scryfall or build-response shapes onto sim/curve fields."""
    out = dict(card)
    if not out.get("text") and out.get("oracle_text"):
        out["text"] = out["oracle_text"]
    if not out.get("type") and out.get("type_line"):
        out["type"] = out["type_line"]
    if "cmc" not in out and "mana_value" in out:
        out["cmc"] = out["mana_value"]
    return out


def corpus_lookup(name: str) -> dict | None:
    """Best-effort local bulk lookup. Missing corpus is unknown, not a skip."""
    try:
        from deckwright import corpus as corp
    except Exception:
        return None
    try:
        c = corp.get_corpus()
        raw = c.by_name.get(name.lower())
    except Exception:
        return None
    if not raw:
        return None
    return {
        "name": raw.get("name") or name,
        "text": raw.get("oracle_text") or "",
        "oracle_text": raw.get("oracle_text") or "",
        "type": raw.get("type_line") or "",
        "type_line": raw.get("type_line") or "",
        "cmc": raw.get("cmc") or 0,
        "cost": raw.get("mana_cost") or "",
        "usd": (raw.get("prices") or {}).get("usd"),
        "color_identity": list(raw.get("color_identity") or []),
        "power": raw.get("power"),
        "produced_mana": list(raw.get("produced_mana") or []),
        "keywords": list(raw.get("keywords") or []),
    }


def enrich_cards(cards: list[dict]) -> list[dict]:
    """Fill missing oracle from the local corpus. Unfound names stay name-only."""
    out = []
    for c in cards:
        n = normalize_card(c)
        if _oracle(n) and _type_line(n):
            out.append(n)
            continue
        looked = corpus_lookup(n.get("name") or "")
        if looked:
            merged = dict(looked)
            merged.update({k: v for k, v in n.items() if v not in (None, "", [])})
            out.append(normalize_card(merged))
        else:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Matchers — frozen with the harness, not imported from candidates
# ---------------------------------------------------------------------------

def _bello_permanent(card: dict) -> bool:
    tl = _type_line(card).lower()
    if "land" in tl.split("—")[0].split():
        return False
    if "artifact" in tl and "equipment" not in tl:
        return True
    if "enchantment" in tl and "aura" not in tl:
        return True
    return False


def card_matches(card: dict, match: dict | None) -> bool | None:
    """True / False / None (could not look)."""
    if not match:
        return None
    kind = match.get("kind")
    text = _oracle(card)
    tl = _type_line(card)
    if kind == "subtype":
        if not tl:
            return None
        return match["subtype"].lower() in tl.lower()
    if kind == "type":
        if not tl:
            return None
        return match["type"].lower() in tl.lower().split("—")[0].split()
    if kind == "oracle_re":
        if text is None:
            return None
        return bool(re.search(match["pattern"], text, re.I))
    if kind == "draw":
        if text is None:
            return None
        return bool(re.search(r"draw (a|one|two|three|that many|\d+) cards?", text, re.I))
    if kind == "dies_feeder":
        if text is None and not tl:
            return None
        if tl and "creature" in tl.lower().split("—")[0].split():
            return True
        if text and re.search(r"dies|sacrific", text, re.I):
            return True
        return False
    if kind == "land_feeder":
        if not tl and text is None:
            return None
        if _is_land(card):
            return True
        if text and re.search(
            r"search your library for .* land|additional land|play .* additional lands?",
            text, re.I,
        ):
            return True
        return False
    if kind == "noncreature_spell":
        if not tl:
            return None
        head = tl.lower().split("—")[0]
        return any(t in head.split() for t in ("instant", "sorcery"))
    if kind == "bello_permanent":
        if not tl:
            return None
        return _bello_permanent(card)
    if kind == "opponent_draw":
        if text is None:
            return None
        return bool(re.search(
            r"each opponent draws|target opponent draws|each player draws",
            text, re.I,
        ))
    return None


def applies_to(card: dict, applies: str | None) -> bool | None:
    if not applies or applies == "any":
        return True
    tl = _type_line(card)
    if applies in ("creature", "artifact", "enchantment", "land", "permanent"):
        if not tl:
            return None
        if applies == "permanent":
            return _is_permanent(card)
        return applies in tl.lower().split("—")[0].split()
    if applies == "bello_permanent":
        if not tl:
            return None
        return _bello_permanent(card)
    if applies == "tutor_target":
        return None
    return None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def unknown_metric(reason: str, **extra: Any) -> dict:
    out = {"status": UNKNOWN, "reason": reason}
    out.update(extra)
    return out


def unread_metric(reason: str, **extra: Any) -> dict:
    out = {"status": UNREAD, "reason": reason}
    out.update(extra)
    return out


def list_divergence(left: list[str], right: list[str]) -> dict:
    """Percent of the 99 that differs. Empty or missing side is unknown."""
    a = {n for n in left if n}
    b = {n for n in right if n}
    if not a or not b:
        return unknown_metric(
            "one or both lists empty — unknown, not 100% different",
            pct=None, added=[], cut=[],
        )
    added = sorted(b - a)
    cut = sorted(a - b)
    union = a | b
    pct = round(len(added) + len(cut)) / (2 * max(len(a), len(b))) * 100
    # Jaccard distance as the comparable %: 1 - |∩|/|∪|
    jacc = 1.0 - (len(a & b) / len(union))
    return {
        "status": OK,
        "pct": round(jacc * 100, 1),
        "added": added,
        "cut": cut,
        "shared": sorted(a & b),
        "left_n": len(a),
        "right_n": len(b),
        "alt_symmetric_pct": round(pct, 1),
    }


def overlap_with_e(candidate_names: list[str], e_names: list[str]) -> dict:
    """Fraction of E's harvested list present in the candidate 99.

    CON-336 sanity check only — D is seeded from the same corpus as E, so high
    overlap confirms the seed reached the deck; it is not a product-quality signal.
    """
    a = {n for n in candidate_names if n}
    b = {n for n in e_names if n}
    if not b:
        return unknown_metric("E list empty — overlap is unknown, not zero")
    if not a:
        return unknown_metric("candidate list empty")
    shared = a & b
    union = a | b
    return {
        "status": OK,
        "pct_of_e": round(100.0 * len(shared) / len(b), 1),
        "jaccard_pct": round(100.0 * len(shared) / len(union), 1),
        "shared_n": len(shared),
        "e_n": len(b),
        "candidate_n": len(a),
    }


def attach_overlap_with_e(per: list[dict]) -> None:
    """Add overlap_with_e to each non-E row when E built ok for that commander."""
    e_names: dict[str, list[str]] = {}
    for row in per:
        if row.get("contestant") == "E" and row.get("status") == OK:
            e_names[row["commander"]] = list(row.get("names") or [])
    for row in per:
        cid = row.get("contestant")
        metrics = row.get("metrics")
        if cid == "E" or row.get("status") != OK or not metrics:
            continue
        en = e_names.get(row["commander"])
        if not en:
            metrics["overlap_with_e"] = unknown_metric(
                "E build unknown for this commander")
            continue
        metrics["overlap_with_e"] = overlap_with_e(row.get("names") or [], en)


def bound_violations(spec: dict, cards: list[dict]) -> dict:
    if spec.get("spec_status") == UNREAD:
        return unread_metric("commander spec unread — not a zero-violation win")
    bounds = spec.get("bounds") or []
    if not bounds:
        return {"status": OK, "count": 0, "unknown": 0, "violations": [],
                "reason": "no bounds declared"}
    violations = []
    unknown = 0
    for bound in bounds:
        applies = bound.get("applies_to")
        if applies == "tutor_target":
            unknown += 1
            violations.append({
                "id": bound.get("id"),
                "status": UNKNOWN,
                "reason": "tutor target is not a 99-wide bound",
                "cards": [],
            })
            continue
        hits = []
        bound_unknown = 0
        for c in cards:
            in_scope = applies_to(c, applies)
            if in_scope is None:
                bound_unknown += 1
                continue
            if not in_scope:
                continue
            kind = bound.get("kind")
            ok = None
            if kind == "mv_leq":
                mv = _cmc(c)
                ok = None if mv is None else mv <= float(bound["value"])
            elif kind == "mv_geq":
                mv = _cmc(c)
                ok = None if mv is None else mv >= float(bound["value"])
            elif kind == "power_leq":
                p = _power(c)
                ok = None if p is None else p <= float(bound["value"])
            elif kind == "subtype_includes":
                tl = _type_line(c)
                ok = None if not tl else bound["subtype"].lower() in tl.lower()
            elif kind == "oracle_forbids":
                text = _oracle(c)
                if text is None:
                    ok = None
                else:
                    ok = not bool(re.search(bound["pattern"], text, re.I))
            else:
                ok = None
            if ok is None:
                bound_unknown += 1
            elif ok is False:
                hits.append(c.get("name") or "")
        unknown += bound_unknown
        violations.append({
            "id": bound.get("id"),
            "status": UNKNOWN if bound_unknown and not hits else OK,
            "count": len(hits),
            "unknown": bound_unknown,
            "cards": hits,
        })
    total = sum(v.get("count") or 0 for v in violations if v.get("status") != UNKNOWN or v.get("count"))
    # If every bound was unknown and none produced a count, the metric is unknown.
    if unknown and total == 0 and all(
            v.get("status") == UNKNOWN and not v.get("cards") for v in violations):
        return unknown_metric(
            "bounds could not be evaluated",
            count=None, unknown=unknown, violations=violations,
        )
    return {
        "status": OK,
        "count": total,
        "unknown": unknown,
        "violations": violations,
    }


def input_feeder_density(spec: dict, cards: list[dict]) -> dict:
    if spec.get("spec_status") == UNREAD:
        return unread_metric(
            "commander spec unread — feeder density is not zero",
            count=None, density=None, inputs=[],
        )
    inputs = spec.get("inputs") or []
    if not inputs:
        # No declared input is not "0 feeders". Maralen's constraint is a bound.
        return {"status": OK, "count": None, "density": None, "inputs": [],
                "reason": "no inputs declared"}
    rows = []
    any_self = False
    for inp in inputs:
        supplier = inp.get("supplier") or "self"
        if supplier == "opponent":
            rows.append({
                "id": inp.get("id"),
                "status": OPPONENT_SUPPLIED,
                "count": None,
                "reason": "input is supplied by opponents — not a zero from the 99",
            })
            continue
        any_self = True
        hits = []
        unk = 0
        for c in cards:
            m = card_matches(c, inp.get("match"))
            if m is None:
                unk += 1
            elif m:
                hits.append(c.get("name") or "")
        if unk and not hits and unk == len(cards):
            rows.append({
                "id": inp.get("id"), "status": UNKNOWN, "count": None,
                "unknown": unk, "reason": "no readable cards for this matcher",
            })
        else:
            rows.append({
                "id": inp.get("id"), "status": OK, "count": len(hits),
                "unknown": unk, "cards": hits,
            })
    self_rows = [r for r in rows if r["status"] not in (OPPONENT_SUPPLIED,)]
    if not any_self:
        return {
            "status": OPPONENT_SUPPLIED,
            "count": None,
            "density": None,
            "inputs": rows,
            "reason": "all inputs opponent-supplied",
        }
    if all(r["status"] == UNKNOWN for r in self_rows):
        return unknown_metric(
            "could not read cards for self-supplied inputs",
            count=None, density=None, inputs=rows,
        )
    count = sum(r.get("count") or 0 for r in self_rows if r["status"] == OK)
    n = max(len(cards), 1)
    return {
        "status": OK,
        "count": count,
        "density": round(count / n, 4),
        "inputs": rows,
    }


def dead_outputs(spec: dict, cards: list[dict]) -> dict:
    if spec.get("spec_status") == UNREAD:
        return unread_metric(
            "commander spec unread — dead outputs are not zero",
            dead=None, outputs=[],
        )
    outputs = spec.get("outputs") or []
    if not outputs:
        return {"status": OK, "dead": 0, "outputs": [],
                "reason": "no outputs declared"}
    rows = []
    for outp in outputs:
        consumers = []
        unk = 0
        for c in cards:
            m = card_matches(c, outp.get("match"))
            if m is None:
                unk += 1
            elif m:
                consumers.append(c.get("name") or "")
        if unk and not consumers and unk == len(cards):
            rows.append({
                "id": outp.get("id"), "kind": outp.get("kind"),
                "status": UNKNOWN, "consumers": None, "unknown": unk,
                "dead": None,
                "reason": "could not read consumers",
            })
            continue
        dead = len(consumers) == 0
        rows.append({
            "id": outp.get("id"), "kind": outp.get("kind"),
            "status": OK, "consumers": len(consumers), "unknown": unk,
            "dead": dead, "cards": consumers[:20],
        })
    if all(r["status"] == UNKNOWN for r in rows):
        return unknown_metric(
            "could not read any output consumers",
            dead=None, outputs=rows,
        )
    dead_n = sum(1 for r in rows if r.get("dead") is True)
    return {"status": OK, "dead": dead_n, "outputs": rows}


def keepable_hand_pct(
    cards: list[dict],
    *,
    runs: int = 200,
    seed: int = SIM_SEED,
) -> dict:
    """Fraction of opening sevens that sim._keep would keep.

    Computed here. A candidate stamp is ignored — that is not a measurement.
    """
    from deckwright import sim
    if not cards:
        return unknown_metric("empty deck")
    prepared = None
    try:
        prepared = sim.prep(enrich_cards(cards), set())
    except Exception as e:
        return unknown_metric(f"prep failed: {type(e).__name__}: {e}")
    if not any(c.get("land") for c in prepared):
        return unknown_metric("no lands after prep — keep rate would be a fake zero")
    rng_state = random.getstate()
    random.seed(seed)
    try:
        keeps = 0
        for _ in range(runs):
            lib = prepared[:]
            random.shuffle(lib)
            hand = lib[:7]
            if sim._keep(hand):
                keeps += 1
        return {
            "status": OK,
            "pct": round(keeps / runs * 100, 1),
            "keeps": keeps,
            "runs": runs,
            "seed": seed,
        }
    finally:
        random.setstate(rng_state)


def goldfish_metrics(
    cards: list[dict],
    cmdr_cmc: int,
    *,
    runs: int = 80,
    seed: int = SIM_SEED,
) -> dict:
    from deckwright import curve
    deck = enrich_cards(cards)
    castable = curve.castable_curve(deck, cmdr_cmc, runs=runs, seed=seed)
    missed = curve.missed_land_drops(deck, cmdr_cmc, runs=runs, seed=seed)
    return {
        "castable_curve": castable,
        "missed_land_drops": missed,
        "runs": runs,
        "seed": seed,
        "solitaire": True,
        "note": "No opponent. Not a win rate.",
    }


def concept_scores(spec: dict, cards: list[dict], goldfish: dict) -> dict:
    """Computable concepts only. 4 and 6 are unknown, never zero."""
    if spec.get("spec_status") == UNREAD:
        return unread_metric(
            "no derived weights — concepts not scored",
            scores={},
        )
    weights = spec.get("weights") or {}
    deck = enrich_cards(cards)
    scores: dict[str, dict] = {}
    for name in UNCOMPUTABLE:
        scores[name] = {
            "status": UNKNOWN,
            "reason": "computable: false (v2-design B.1)",
            "weight": weights.get(name, 0),
            "value": None,
        }
    # Explosive ramp from existing curve.ramp_split.
    try:
        from deckwright import curve
        split = curve.ramp_split(deck)
        if split.get("status") == curve.UNKNOWN:
            scores["explosive_ramp"] = unknown_metric(
                split.get("reason") or "ramp_split unread",
                weight=weights.get("explosive_ramp", 0), value=None,
            )
        else:
            n = max(1, sum(1 for c in deck if not _is_land(c)))
            scores["explosive_ramp"] = {
                "status": OK,
                "value": round(split.get("explosive", 0) / n, 4),
                "weight": weights.get("explosive_ramp", 0),
                "raw": split,
            }
    except Exception as e:
        scores["explosive_ramp"] = unknown_metric(
            f"ramp_split failed: {type(e).__name__}",
            weight=weights.get("explosive_ramp", 0), value=None,
        )

    # Resilience proxy: recursion / pillowfort phrases. Count, don't invent.
    rec = 0
    rec_unk = 0
    for c in deck:
        t = _oracle(c)
        if t is None:
            rec_unk += 1
            continue
        if re.search(r"return .* (battlefield|hand)|indestructible|hexproof|ward |regenerate", t, re.I):
            rec += 1
    if rec_unk == len(deck) and rec == 0:
        scores["resilience"] = unknown_metric(
            "no readable oracle for resilience",
            weight=weights.get("resilience", 0), value=None,
        )
    else:
        scores["resilience"] = {
            "status": OK,
            "value": round(rec / max(len(deck), 1), 4),
            "weight": weights.get("resilience", 0),
            "unknown": rec_unk,
        }

    # Free value: without paying / trigger draw.
    fv = 0
    fv_unk = 0
    for c in deck:
        t = _oracle(c)
        if t is None:
            fv_unk += 1
            continue
        if re.search(r"without paying|draw a card.*whenever|whenever .* draw", t, re.I):
            fv += 1
    if fv_unk == len(deck) and fv == 0:
        scores["free_value"] = unknown_metric(
            "no readable oracle for free value",
            weight=weights.get("free_value", 0), value=None,
        )
    else:
        scores["free_value"] = {
            "status": OK,
            "value": round(fv / max(len(deck), 1), 4),
            "weight": weights.get("free_value", 0),
            "unknown": fv_unk,
        }

    # Multi-engine: cards whose synergy profile produces 2+ of ramp/CA/payoff
    # objects. Unknown profile does not count as zero engines.
    try:
        from deckwright import synergy
        multi = 0
        syn_unk = 0
        for c in deck:
            if _is_land(c):
                continue
            prof = synergy.profile(c)
            if prof.get("status") == synergy.UNKNOWN:
                syn_unk += 1
                continue
            objs = (prof.get("produces") or set()) | (prof.get("consumes") or set())
            axes = 0
            blob = " ".join(objs).lower() + " " + " ".join(prof.get("signals") or [])
            if re.search(r"mana|treasure|land", blob):
                axes += 1
            if re.search(r"card|draw", blob):
                axes += 1
            if re.search(r"token|counter|damage|drain|life", blob):
                axes += 1
            if axes >= 2:
                multi += 1
        if syn_unk and multi == 0:
            scores["multi_engine"] = unknown_metric(
                "synergy profiles unread",
                weight=weights.get("multi_engine", 0), value=None,
            )
        else:
            scores["multi_engine"] = {
                "status": OK,
                "value": round(multi / max(len(deck), 1), 4),
                "weight": weights.get("multi_engine", 0),
                "unknown": syn_unk,
            }
    except Exception as e:
        scores["multi_engine"] = unknown_metric(
            f"synergy.profile failed: {type(e).__name__}",
            weight=weights.get("multi_engine", 0), value=None,
        )

    # Multiplayer scaling is a commander fact (supplier class), not a 99 score.
    suppliers = {i.get("supplier") for i in (spec.get("inputs") or [])}
    if "opponent" in suppliers:
        scaling = "opponent"
        sval = 1.0
    elif "anyone" in suppliers:
        scaling = "anyone"
        sval = 0.5
    elif suppliers:
        scaling = "self_capped"
        sval = 0.1
    else:
        scaling = "neutral"
        sval = 0.0
    scores["multiplayer_scaling"] = {
        "status": OK,
        "value": sval,
        "weight": weights.get("multiplayer_scaling", 0),
        "class": scaling,
        "note": "Structural / commander-level until CON-316 table rates.",
    }

    # Value-to-turn: goldfish act-by-turn-N vs 2N baseline. Solitaire only.
    castable = (goldfish or {}).get("castable_curve") or {}
    if castable.get("status") != OK:
        scores["value_to_turn"] = unknown_metric(
            castable.get("reason") or "castable curve unread",
            weight=weights.get("value_to_turn", 0), value=None,
        )
    else:
        by = castable.get("by_turn") or []
        # Turn 5 act rate as the solitaire proxy.
        t5 = by[4] if len(by) >= 5 else None
        if t5 is None:
            scores["value_to_turn"] = unknown_metric(
                "castable curve missing turn 5",
                weight=weights.get("value_to_turn", 0), value=None,
            )
        else:
            scores["value_to_turn"] = {
                "status": OK,
                "value": t5,
                "weight": weights.get("value_to_turn", 0),
                "note": "solitaire act rate at T5, not table VTR",
            }

    return {"status": OK, "scores": scores}


def score_build(spec: dict, cards: list[dict], *, sim_runs: int = 80) -> dict:
    deck = enrich_cards(cards)
    cmc = int(spec.get("cmc") or 5)
    gold = goldfish_metrics(deck, cmc, runs=sim_runs, seed=SIM_SEED)
    return {
        "bound_violations": bound_violations(spec, deck),
        "input_feeder_density": input_feeder_density(spec, deck),
        "dead_outputs": dead_outputs(spec, deck),
        "keepable_hand": keepable_hand_pct(deck, seed=SIM_SEED),
        "goldfish": gold,
        "concepts": concept_scores(spec, deck, gold),
        "n": len(deck),
        "named": len([c for c in deck if c.get("name")]),
        "readable": len([c for c in deck if _oracle(c)]),
    }


# ---------------------------------------------------------------------------
# Contestants
# ---------------------------------------------------------------------------

@dataclass
class BuildRequest:
    commander: str
    brief: dict
    color_identity: list[str]
    budget_total: float
    bracket: int | None
    spec: dict


@dataclass
class BuildResponse:
    status: str
    contestant_id: str
    names: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    wall_clock_s: float | None = None
    unknown_reason: str | None = None
    reasons: dict[str, str] = field(default_factory=dict)
    edhrec_seed: dict[str, Any] | None = None


def ci_legal(card: dict, commander_ci: list[str]) -> bool:
    extra = set(_ci(card)) - set(commander_ci)
    return not extra


def budget_each(budget_total: float, deck_size: int = DECK_N) -> float:
    """Same formula V1 uses: budget_total / 99 * 3."""
    return max(1.0, float(budget_total) / deck_size * 3)


def edhrec_slug(spec: dict) -> str:
    if spec.get("edhrec_slug"):
        return spec["edhrec_slug"]
    s = spec["name"].lower().split("//")[0].strip()
    s = s.replace("'", "").replace(",", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def load_edhrec_fixture(spec: dict, directory: Path = EDHREC_DIR) -> dict | None:
    path = directory / f"{edhrec_slug(spec)}.json"
    if not path.is_file():
        return None
    return load_json(path)


def build_edhrec(req: BuildRequest, *, directory: Path = EDHREC_DIR) -> BuildResponse:
    t0 = time.perf_counter()
    fixture = load_edhrec_fixture(req.spec, directory)
    if not fixture:
        return BuildResponse(
            status=UNKNOWN,
            contestant_id="E",
            unknown_reason=f"no EDHREC fixture for {edhrec_slug(req.spec)}",
            wall_clock_s=time.perf_counter() - t0,
        )
    raw = fixture.get("cards") or []
    if not raw:
        return BuildResponse(
            status=UNKNOWN,
            contestant_id="E",
            unknown_reason="EDHREC fixture has no cards",
            wall_clock_s=time.perf_counter() - t0,
        )
    ceiling = budget_each(req.budget_total)
    picked: list[dict] = []
    seen: set[str] = set()
    skipped_budget = 0
    skipped_ci = 0
    unknown_filter = 0
    for row in raw:
        name = row.get("name")
        if not name or name in seen:
            continue
        card = normalize_card(row)
        looked = None
        if not _type_line(card) or _usd(card) is None:
            looked = corpus_lookup(name)
            if looked:
                card = normalize_card({**looked, **{k: v for k, v in card.items() if v}})
        if not ci_legal(card, req.color_identity):
            # Name-only rows cannot be CI-filtered. Count unknown, do not drop
            # them as illegal — that would treat unread CI as a fail.
            if not _ci(card) and not _type_line(card):
                unknown_filter += 1
                # Keep name-only cards; filter happens when we can see CI.
                picked.append(card)
                seen.add(name)
                continue
            skipped_ci += 1
            continue
        price = _usd(card)
        if price is not None and price >= ceiling:
            skipped_budget += 1
            continue
        picked.append(card)
        seen.add(name)

    nonlands = [c for c in picked if not _is_land(c)]
    lands = [c for c in picked if _is_land(c)]
    # Name-only cards are neither — keep them in the spell window.
    nameless_type = [c for c in picked if not _type_line(c)]
    spells = [c for c in nonlands if c not in nameless_type] + nameless_type
    deck = spells[:NONLAND_N] + lands[:LANDS_N]
    if len(deck) < DECK_N:
        rest = [c for c in picked if c not in deck]
        deck.extend(rest[: DECK_N - len(deck)])
    deck = deck[:DECK_N]
    if len(deck) < 90:
        return BuildResponse(
            status=UNKNOWN,
            contestant_id="E",
            unknown_reason=(
                f"fixture too thin after CI/budget filter "
                f"({len(deck)} cards; skipped_ci={skipped_ci} "
                f"skipped_budget={skipped_budget} unread_ci={unknown_filter})"
            ),
            names=names_of(deck),
            cards=deck,
            wall_clock_s=time.perf_counter() - t0,
        )
    return BuildResponse(
        status=OK,
        contestant_id="E",
        names=names_of(deck),
        cards=deck,
        wall_clock_s=time.perf_counter() - t0,
        reasons={c.get("name") or "": "EDHREC harvested top-N" for c in deck},
    )


def build_http(
    req: BuildRequest, url: str, *, timeout: float = DEFAULT_BUILD_TIMEOUT,
) -> BuildResponse:
    t0 = time.perf_counter()
    payload = json.dumps({
        "commander": req.commander,
        "brief": req.brief,
    }).encode()
    endpoint = url.rstrip("/") + "/api/build"
    request = urllib.request.Request(
        endpoint, data=payload, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return BuildResponse(
            status=UNKNOWN, contestant_id="BLUE",
            unknown_reason=f"HTTP {e.code} from {endpoint}: {detail}",
            wall_clock_s=time.perf_counter() - t0,
        )
    except Exception as e:
        return BuildResponse(
            status=UNKNOWN, contestant_id="BLUE",
            unknown_reason=f"{type(e).__name__}: {e}",
            wall_clock_s=time.perf_counter() - t0,
        )
    deck = body.get("deck") or []
    if not deck:
        return BuildResponse(
            status=UNKNOWN, contestant_id="BLUE",
            unknown_reason="build returned no deck",
            wall_clock_s=time.perf_counter() - t0,
        )
    reasons = {}
    for c in deck:
        if isinstance(c, dict) and c.get("name") and c.get("why"):
            reasons[c["name"]] = c["why"]
    return BuildResponse(
        status=OK,
        contestant_id="BLUE",
        names=names_of(deck),
        cards=[normalize_card(c) for c in deck if isinstance(c, dict)],
        wall_clock_s=time.perf_counter() - t0,
        reasons=reasons,
        edhrec_seed=body.get("edhrec_seed") if isinstance(body.get("edhrec_seed"), dict) else None,
    )


def load_callable(target: str) -> BuildFn:
    if ":" not in target:
        raise ValueError(f"callable target must be module:function, got {target!r}")
    mod_name, func_name = target.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, func_name)
    if not callable(fn):
        raise ValueError(f"{target} is not callable")
    return fn


def wrap_callable(contestant_id: str, fn: BuildFn) -> BuildFn:
    def wrapped(req: BuildRequest) -> BuildResponse:
        t0 = time.perf_counter()
        try:
            resp = fn(req)
        except Exception as e:
            return BuildResponse(
                status=UNKNOWN, contestant_id=contestant_id,
                unknown_reason=f"{type(e).__name__}: {e}",
                wall_clock_s=time.perf_counter() - t0,
            )
        if not isinstance(resp, BuildResponse):
            return BuildResponse(
                status=UNKNOWN, contestant_id=contestant_id,
                unknown_reason="callable did not return BuildResponse",
                wall_clock_s=time.perf_counter() - t0,
            )
        if resp.wall_clock_s is None:
            resp.wall_clock_s = time.perf_counter() - t0
        resp.contestant_id = contestant_id
        return resp
    return wrapped


def http_build_url(
    row: dict,
    *,
    green_url: str | None = None,
    green_b_url: str | None = None,
    green_c_url: str | None = None,
    green_d_url: str | None = None,
) -> str:
    """Resolve one http_build row. CLI overrides are per-contestant (CON-330).

    `--green-url` applies to BLUE only. Never cross-apply to B/C/D — that is
    the CON-330 class bug that silently collapsed ports.
    """
    cid = row["id"]
    override = None
    if cid == "BLUE":
        override = green_url
    elif cid == "B":
        override = green_b_url
    elif cid == "C":
        override = green_c_url
    elif cid == "D":
        override = green_d_url
    return (
        override
        or os.environ.get(row.get("url_env") or "")
        or row.get("url_default")
        or ""
    )


def collect_http_urls(
    registry: dict,
    *,
    only: list[str] | None = None,
    green_url: str | None = None,
    green_b_url: str | None = None,
    green_c_url: str | None = None,
    green_d_url: str | None = None,
) -> dict[str, str]:
    """Resolved URLs for enabled http_build contestants in this run."""
    urls: dict[str, str] = {}
    wanted = set(only) if only else None
    for row in registry.get("contestants") or []:
        cid = row["id"]
        if wanted is not None and cid not in wanted:
            continue
        if row.get("kind") != "http_build" or not row.get("enabled"):
            continue
        urls[cid] = http_build_url(
            row,
            green_url=green_url,
            green_b_url=green_b_url,
            green_c_url=green_c_url,
            green_d_url=green_d_url,
        )
    return urls


def resolve_contestants(
    registry: dict,
    *,
    only: list[str] | None = None,
    extra: dict[str, BuildFn] | None = None,
    green_url: str | None = None,
    green_b_url: str | None = None,
    green_c_url: str | None = None,
    green_d_url: str | None = None,
    edhrec_dir: Path = EDHREC_DIR,
    build_timeout: float | None = None,
) -> dict[str, BuildFn]:
    """Build id → function. extra is how tests (and later A/B/C) plug in.

    extra wins over the registry for the same id so a test can stub BLUE
    without editing the frozen file.
    """
    out: dict[str, BuildFn] = {}
    wanted = set(only) if only else None
    http_timeout = (
        build_timeout if build_timeout is not None else default_build_timeout()
    )
    for row in registry.get("contestants") or []:
        cid = row["id"]
        if wanted is not None and cid not in wanted:
            continue
        if extra and cid in extra:
            out[cid] = wrap_callable(cid, extra[cid])
            continue
        if not row.get("enabled") and not (extra and cid in extra):
            continue
        kind = row.get("kind")
        if kind == "edhrec_fixture":
            out[cid] = lambda req, d=edhrec_dir: build_edhrec(req, directory=d)
        elif kind == "http_build":
            url = http_build_url(
                row,
                green_url=green_url,
                green_b_url=green_b_url,
                green_c_url=green_c_url,
                green_d_url=green_d_url,
            )
            out[cid] = lambda req, u=url, i=cid, t=http_timeout: _http_as(
                i, u, req, timeout=t,
            )
        elif kind == "callable":
            target = row.get("target")
            if not target:
                continue
            out[cid] = wrap_callable(cid, load_callable(target))
        else:
            raise ValueError(f"unknown contestant kind {kind!r} for {cid}")
    if extra:
        for cid, fn in extra.items():
            if wanted is not None and cid not in wanted:
                continue
            out[cid] = wrap_callable(cid, fn)
    return out


def _http_as(
    contestant_id: str,
    url: str,
    req: BuildRequest,
    *,
    timeout: float = DEFAULT_BUILD_TIMEOUT,
) -> BuildResponse:
    resp = build_http(req, url, timeout=timeout)
    resp.contestant_id = contestant_id
    return resp


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def validate_set(data: dict) -> list[str]:
    """Structural checks. Empty list means the freeze is well-formed."""
    errors = []
    commanders = data.get("commanders") or []
    n = len(commanders)
    if not (20 <= n <= 30):
        errors.append(f"commander count {n} is outside 20–30")
    names = [c.get("name") for c in commanders]
    if len(names) != len(set(names)):
        errors.append("duplicate commander names")
    forbidden = set(data.get("illustration_decks_excluded") or [])
    overlap = forbidden & set(names)
    if overlap:
        errors.append(f"illustration decks in set: {sorted(overlap)}")
    for c in commanders:
        if not (c.get("justification") or "").strip():
            errors.append(f"{c.get('name')}: missing justification")
        if c.get("spec_status") not in (OK, UNREAD):
            errors.append(f"{c.get('name')}: spec_status must be ok or unread")
    spread = [tag for c in commanders for tag in (c.get("spread") or [])]
    def need(tag, lo):
        got = spread.count(tag) if tag != "top_band" else sum(
            1 for c in commanders if rank_band(c.get("commander_rank")) == "top_100")
        if tag == "top_band":
            if got < lo:
                errors.append(f"need ≥{lo} top_100, have {got}")
            return
        if tag == "obscure":
            got = sum(1 for c in commanders if rank_band(c.get("commander_rank")) == "1001_plus")
            if got < lo:
                errors.append(f"need ≥{lo} 1001_plus, have {got}")
            return
        got = sum(1 for c in commanders if tag in (c.get("spread") or []))
        if got < lo:
            errors.append(f"need ≥{lo} tagged {tag}, have {got}")
    need("top_band", 3)
    need("obscure", 2)
    need("bound", 3)
    need("opponent_input", 2)
    need("unread", 2)
    need("mono", 1)
    need("four_color", 1)
    need("five_color", 1)
    unread_ok = all(
        (not c.get("inputs") and not c.get("outputs") and not c.get("bounds"))
        for c in commanders if c.get("spec_status") == UNREAD
    )
    if not unread_ok:
        errors.append("unread specs must have empty inputs/outputs/bounds")
    return errors


def mean(values: list[float | None]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def aggregate_band(rows: list[dict], contestant_id: str, band: str) -> dict:
    subset = [r for r in rows
              if r["contestant"] == contestant_id and r["band"] == band]

    def metric_mean(key, field="count"):
        vals = []
        unknowns = 0
        for r in subset:
            m = (r.get("metrics") or {}).get(key) or {}
            if m.get("status") != OK:
                unknowns += 1
                continue
            vals.append(m.get(field))
        return {"mean": mean(vals), "unknown": unknowns, "n": len(subset)}

    keep = metric_mean("keepable_hand", "pct")
    bounds = metric_mean("bound_violations", "count")
    feeders = metric_mean("input_feeder_density", "density")
    dead = metric_mean("dead_outputs", "dead")
    overlap = metric_mean("overlap_with_e", "pct_of_e")
    clocks = []
    clock_unk = 0
    for r in subset:
        w = r.get("wall_clock_s")
        if isinstance(w, (int, float)):
            clocks.append(w)
        else:
            clock_unk += 1
    return {
        "band": band,
        "contestant": contestant_id,
        "n": len(subset),
        "bound_violations": bounds,
        "input_feeder_density": feeders,
        "dead_outputs": dead,
        "keepable_hand_pct": keep,
        "overlap_with_e_pct": overlap,
        "wall_clock_s": {"mean": mean(clocks), "unknown": clock_unk, "n": len(subset)},
    }


def diff_view(
    left: BuildResponse,
    right: BuildResponse,
    left_id: str,
    right_id: str,
) -> dict:
    if left.status != OK or right.status != OK:
        return unknown_metric(
            "one or both builds unknown — not a card diff",
            added=[], cut=[],
            left=left_id, right=right_id,
        )
    d = list_divergence(left.names, right.names)
    reasons = []
    for name in d.get("added") or []:
        reasons.append({
            "name": name, "dir": f"added_to_{right_id}",
            "reason": right.reasons.get(name) or None,
        })
    for name in d.get("cut") or []:
        reasons.append({
            "name": name, "dir": f"cut_from_{right_id}",
            "reason": left.reasons.get(name) or None,
        })
    d["reasons"] = reasons
    d["left"] = left_id
    d["right"] = right_id
    return d


def emit_blind_pairs(
    per: list[dict],
    *,
    sample: int = 3,
    seed: int = SIM_SEED,
) -> tuple[list[dict], dict]:
    """Unlabelled pairs + key. Reviewer opens pairs first."""
    by_cmd: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in per:
        if row.get("status") != OK:
            continue
        by_cmd[row["commander"]][row["contestant"]] = row
    eligible = []
    for commander, slots in by_cmd.items():
        ids = sorted(slots)
        if len(ids) < 2:
            continue
        # Prefer BLUE vs first non-BLUE.
        if "BLUE" in slots:
            other = next((i for i in ids if i != "BLUE"), None)
            if other:
                eligible.append((commander, "BLUE", other))
                continue
        eligible.append((commander, ids[0], ids[1]))
    rng = random.Random(seed)
    rng.shuffle(eligible)
    eligible = eligible[:sample]
    pairs = []
    key = {"do_not_open_until_after_picks": True, "seed": seed, "pairs": []}
    for i, (commander, a, b) in enumerate(eligible, 1):
        order = [a, b]
        rng.shuffle(order)
        left, right = order
        pairs.append({
            "pair_id": f"pair-{i:02d}",
            "commander": commander,
            "list_1": slots_names(by_cmd[commander][left]),
            "list_2": slots_names(by_cmd[commander][right]),
        })
        key["pairs"].append({
            "pair_id": f"pair-{i:02d}",
            "commander": commander,
            "list_1": left,
            "list_2": right,
        })
    return pairs, key


def slots_names(row: dict) -> list[str]:
    return list(row.get("names") or [])


def contestant_urls(registry: dict) -> dict[str, str]:
    """HTTP build endpoints from registry/env — no CLI overrides.

    Prefer collect_http_urls() when CLI overrides may apply (CON-330).
    """
    out: dict[str, str] = {}
    for row in registry.get("contestants") or []:
        if row.get("kind") != "http_build":
            continue
        out[row["id"]] = http_build_url(row)
    return out


def render_report(doc: dict) -> str:
    lines = []
    h = doc["header"]
    contestants = h.get("contestants") or []
    seed_sweep = "D" in contestants
    lines.append("# V2 bake-off report (CON-319)")
    lines.append("")
    lines.append(f"- commit: `{h.get('commit')}`")
    lines.append(f"- generated: {h.get('generated_at')}")
    lines.append(f"- contestants: {', '.join(contestants)}")
    if h.get("green_urls"):
        lines.append(f"- green_urls: {json.dumps(h['green_urls'], sort_keys=True)}")
    if h.get("build_timeout_s") is not None:
        lines.append(f"- build_timeout_s: {h.get('build_timeout_s')}")
    lines.append(f"- sim seed: {h.get('sim_seed')} (goldfish is solitaire)")
    lines.append(f"- commanders: {h.get('commander_count')}")
    lines.append("")
    if seed_sweep:
        lines.append("**CON-336 seed sweep.** Judge on **input feeder density** (commander "
                     "serve quality) and **keepable hand %** vs BLUE (castability). "
                     "Gate C baselines in `top_100`: E ≈ 0.381, V1 (BLUE) ≈ 0.308. "
                     "A genuine win is D landing near E's feeder density while holding "
                     "~80% keepable — not circular with EDHREC's list.")
        lines.append("")
        lines.append("**Overlap with E** is a sanity check only (did the seed reach the "
                     "99?). D beating BLUE on overlap is expected by construction; do not "
                     "read it as \"D is better.\" Bound violations are secondary.")
    else:
        lines.append("A candidate is closer to intended state when it moves bound "
                     "violations, feeder density, and dead outputs without regressing "
                     "castability / land drops / keepable hands.")
    lines.append("")
    lines.append("## Per-band summary")
    lines.append("")
    for cid, bands in (doc.get("by_band") or {}).items():
        lines.append(f"### {cid}")
        lines.append("")
        if seed_sweep and cid != "E":
            hdr = ("| band | n | feeder density | keep% | overlap w/ E (sanity) | "
                   "bounds mean | wall-clock s |")
        else:
            hdr = ("| band | n | bounds mean | feeder density | dead outputs | "
                   "keep% | wall-clock s |")
        lines.append(hdr)
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for band, agg in bands.items():
            def cell(m):
                if not m:
                    return "—"
                if m.get("mean") is None:
                    return f"unknown ({m.get('unknown', 0)})"
                return f"{m['mean']} (unk {m.get('unknown', 0)})"
            if seed_sweep and cid != "E":
                lines.append(
                    f"| {band} | {agg.get('n')} | "
                    f"{cell(agg.get('input_feeder_density'))} | "
                    f"{cell(agg.get('keepable_hand_pct'))} | "
                    f"{cell(agg.get('overlap_with_e_pct'))} | "
                    f"{cell(agg.get('bound_violations'))} | "
                    f"{cell(agg.get('wall_clock_s'))} |"
                )
            else:
                lines.append(
                    f"| {band} | {agg.get('n')} | "
                    f"{cell(agg.get('bound_violations'))} | "
                    f"{cell(agg.get('input_feeder_density'))} | "
                    f"{cell(agg.get('dead_outputs'))} | "
                    f"{cell(agg.get('keepable_hand_pct'))} | "
                    f"{cell(agg.get('wall_clock_s'))} |"
                )
        lines.append("")
    lines.append("## Per commander")
    lines.append("")
    for row in doc.get("per") or []:
        st = row.get("status")
        lines.append(f"### {row['commander']} — {row['contestant']} ({row['band']}) [{st}]")
        if st != OK:
            lines.append(f"- unknown: {row.get('unknown_reason')}")
            lines.append("")
            continue
        m = row.get("metrics") or {}
        bv = m.get("bound_violations") or {}
        fd = m.get("input_feeder_density") or {}
        do = m.get("dead_outputs") or {}
        kh = m.get("keepable_hand") or {}
        ov = m.get("overlap_with_e") or {}
        lines.append(f"- bounds: {bv.get('status')} count={bv.get('count')} unknown={bv.get('unknown')}")
        lines.append(f"- feeders: {fd.get('status')} density={fd.get('density')} count={fd.get('count')}")
        lines.append(f"- dead outputs: {do.get('status')} dead={do.get('dead')}")
        lines.append(f"- keepable hand: {kh.get('status')} pct={kh.get('pct')}")
        if ov:
            lines.append(f"- overlap w/ E (sanity): {ov.get('status')} "
                           f"pct_of_e={ov.get('pct_of_e')} "
                           f"shared={ov.get('shared_n')}/{ov.get('e_n')}")
        if row.get("edhrec_seed"):
            es = row["edhrec_seed"]
            lines.append(f"- edhrec_seed: status={es.get('status')} "
                           f"num_decks={es.get('num_decks')} seed_cards={es.get('seed_cards')}")
        lines.append(f"- wall-clock: {row.get('wall_clock_s')}")
        lines.append("")
    lines.append("## Diffs vs BLUE and vs E")
    lines.append("")
    for d in doc.get("diffs") or []:
        if d.get("status") != OK:
            lines.append(f"- {d.get('commander')}: {d.get('left')} vs {d.get('right')} — {d.get('reason')}")
            continue
        lines.append(
            f"- {d.get('commander')}: {d.get('left')} → {d.get('right')} "
            f"divergence {d.get('pct')}% "
            f"(+{len(d.get('added') or [])} / -{len(d.get('cut') or [])})"
        )
    lines.append("")
    lines.append("Blind pairs are in `blind_pairs.json`. Do not open `blind_key.json` until after picking.")
    lines.append("")
    return "\n".join(lines)


def run_benchmark(
    *,
    contestant_fns: dict[str, BuildFn],
    commanders: list[dict],
    sim_runs: int = 80,
    green_urls: dict[str, str] | None = None,
    build_timeout_s: float | None = None,
) -> dict:
    per = []
    responses: dict[tuple[str, str], BuildResponse] = {}
    for spec in commanders:
        req = BuildRequest(
            commander=spec["name"],
            brief=spec.get("brief") or {},
            color_identity=list(spec.get("color_identity") or []),
            budget_total=float((spec.get("brief") or {}).get("budget_total") or 150),
            bracket=(spec.get("brief") or {}).get("bracket"),
            spec=spec,
        )
        band = rank_band(spec.get("commander_rank"))
        for cid, fn in contestant_fns.items():
            resp = fn(req)
            responses[(spec["name"], cid)] = resp
            row = {
                "commander": spec["name"],
                "commander_id": spec.get("id"),
                "contestant": cid,
                "band": band,
                "commander_rank": spec.get("commander_rank"),
                "spec_status": spec.get("spec_status"),
                "status": resp.status,
                "unknown_reason": resp.unknown_reason,
                "wall_clock_s": resp.wall_clock_s,
                "names": resp.names,
            }
            if resp.edhrec_seed:
                row["edhrec_seed"] = resp.edhrec_seed
            if resp.status == OK:
                row["metrics"] = score_build(spec, resp.cards, sim_runs=sim_runs)
            else:
                row["metrics"] = None
            per.append(row)

    attach_overlap_with_e(per)

    contestant_ids = list(contestant_fns)
    diffs = []
    for spec in commanders:
        name = spec["name"]
        for other in contestant_ids:
            if other == "BLUE":
                continue
            if ("BLUE" in contestant_ids) and (name, "BLUE") in responses and (name, other) in responses:
                d = diff_view(responses[(name, "BLUE")], responses[(name, other)], "BLUE", other)
                d["commander"] = name
                diffs.append(d)
            if ("E" in contestant_ids) and other != "E" and (name, "E") in responses and (name, other) in responses:
                d = diff_view(responses[(name, "E")], responses[(name, other)], "E", other)
                d["commander"] = name
                diffs.append(d)

    bands = ["top_100", "101_1000", "1001_plus", "no_rank"]
    by_band = {}
    for cid in contestant_ids:
        by_band[cid] = {b: aggregate_band(per, cid, b) for b in bands}

    header = {
        "commit": git_sha(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contestants": contestant_ids,
        "green_urls": green_urls or {},
        "build_timeout_s": build_timeout_s,
        "sim_seed": SIM_SEED,
        "sim_runs": sim_runs,
        "commander_count": len(commanders),
        "card": "CON-319",
        "solitaire": True,
    }
    return {
        "header": header,
        "per": per,
        "by_band": by_band,
        "diffs": diffs,
    }


def write_outputs(doc: dict, out_dir: Path, pairs: list, key: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(doc), encoding="utf-8")
    (out_dir / "blind_pairs.json").write_text(
        json.dumps(pairs, indent=2) + "\n", encoding="utf-8")
    (out_dir / "blind_key.json").write_text(
        json.dumps(key, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CON-319 V2 bake-off harness")
    p.add_argument("--validate", action="store_true",
                   help="check the frozen set only; no builds")
    p.add_argument("--contestants", default="",
                   help="comma-separated ids (default: enabled registry entries)")
    p.add_argument(
        "--green-url", default=None,
        help="override DECKWRIGHT_GREEN_URL for BLUE only (not B/C/D)",
    )
    p.add_argument(
        "--green-b-url", default=None,
        help="override DECKWRIGHT_GREEN_B_URL for B only",
    )
    p.add_argument(
        "--green-c-url", default=None,
        help="override DECKWRIGHT_GREEN_C_URL for C only",
    )
    p.add_argument(
        "--green-d-url", default=None,
        help="override DECKWRIGHT_GREEN_D_URL for D only",
    )
    p.add_argument("--sim-runs", type=int, default=80)
    p.add_argument("--out", default="",
                   help="report directory (default docs/benchmarks/runs/<utc>)")
    p.add_argument("--blind-sample", type=int, default=3)
    p.add_argument(
        "--build-timeout", type=float, default=None,
        help="HTTP build timeout in seconds (default: "
             "DECKWRIGHT_BUILD_TIMEOUT or 1800)",
    )
    args = p.parse_args(argv)

    data = load_commanders()
    errors = validate_set(data)
    if errors:
        print("FROZEN SET INVALID:")
        for e in errors:
            print(f"  - {e}")
        return 1
    if args.validate:
        n = len(data["commanders"])
        bands = defaultdict(int)
        for c in data["commanders"]:
            bands[rank_band(c.get("commander_rank"))] += 1
        registry = load_contestant_registry()
        reg_errors = validate_contestant_registry(registry)
        if reg_errors:
            print("CONTESTANT REGISTRY INVALID:")
            for e in reg_errors:
                print(f"  - {e}")
            return 1
        enabled = enabled_resolveable_ids(registry)
        print(f"CON-319 set ok: {n} commanders  bands={dict(bands)}")
        print(f"registry ok: enabled resolveable {','.join(enabled)}")
        print(f"DEFAULT_BUILD_TIMEOUT={DEFAULT_BUILD_TIMEOUT:g}")
        print(f"commit {git_sha()}")
        return 0

    registry = load_contestant_registry()
    only = [s.strip() for s in args.contestants.split(",") if s.strip()] or None
    build_timeout = (
        args.build_timeout if args.build_timeout is not None
        else default_build_timeout()
    )
    green_urls = collect_http_urls(
        registry,
        only=only,
        green_url=args.green_url,
        green_b_url=args.green_b_url,
        green_c_url=args.green_c_url,
        green_d_url=args.green_d_url,
    )
    fns = resolve_contestants(
        registry,
        only=only,
        green_url=args.green_url,
        green_b_url=args.green_b_url,
        green_c_url=args.green_c_url,
        green_d_url=args.green_d_url,
        build_timeout=build_timeout,
    )
    if not fns:
        print("no contestants enabled — pass --contestants E or register one")
        return 2
    print(f"contestants: {', '.join(fns)}")
    if green_urls:
        print(f"green_urls: {json.dumps(green_urls, sort_keys=True)}")
    print(
        f"note: server {LLM_TIMEOUT_ENV} default is 180s; "
        f"long builds need ≥{int(RECOMMENDED_LLM_TIMEOUT_S)}s on green ports "
        f"(HTTP build_timeout={build_timeout:g}s is separate)"
    )
    doc = run_benchmark(
        contestant_fns=fns,
        commanders=data["commanders"],
        sim_runs=args.sim_runs,
        green_urls=green_urls,
        build_timeout_s=build_timeout,
    )
    pairs, key = emit_blind_pairs(doc["per"], sample=args.blind_sample)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPORT_DIR / stamp
    write_outputs(doc, out_dir, pairs, key)
    print(render_report(doc))
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
