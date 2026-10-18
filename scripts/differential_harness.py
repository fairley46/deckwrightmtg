#!/usr/bin/env python3
"""CON-168: differential harness — did the app listen?

Every other check asks whether the chain ran. This one asks whether two
adjacent wishes produce different decks *for the reason the plan claims*.

Tier 3 — real model quota, real Scryfall, several minutes. Before ship, not
per commit. See CON-169.

    # needs ANTHROPIC_API_KEY (or claude CLI) + network
    python scripts/differential_harness.py
    python scripts/differential_harness.py --pair tribe-angels-vs-vampires
    python test_differential_harness.py          # offline logic only

House rule (same as scripts/e2e_flow.py): an unknown is not a zero, and a
vacuous check is not a passing check. A wish that fails to produce a brief,
or that CON-167 correctly refuses, is UNKNOWN and excluded from comparison.
Never let a refused build count as "different".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Wish pairs — adjacent, not the same. Near-misses are where a lazy pipeline
# collapses into the same deck.
# ---------------------------------------------------------------------------

WISHES: dict[str, str] = {
    "angels-lifegain": (
        "I want a lifegain deck with angels — white, maybe black too. "
        "Angels that gain me life and keep me alive."
    ),
    "vampires-lifegain": (
        "I want a lifegain deck with vampires — black and white. "
        "Vampires that gain me life and drain the table."
    ),
    "drain-incremental": (
        "I want vampires that drain the table with lots of small life-loss "
        "triggers every turn, grinding opponents down over time and steadily "
        "— not a combo finish, not one big swing."
    ),
    "drain-burst": (
        "I want vampires that drain with one big life swing — a burst turn "
        "that kills from nowhere in one turn, not a slow grind every turn."
    ),
    "sac-incremental": (
        "I want a graveyard sacrifice deck — creatures I sacrifice for value "
        "over and over, grinding the table down a little every turn. No "
        "lifegain, no drain; the value comes from the deaths themselves."
    ),
    "sac-burst": (
        "I want a graveyard sacrifice deck that wins in one explosive turn — "
        "sacrifice my whole board at once for a kill out of nowhere, not a "
        "slow grind. No lifegain, no drain."
    ),
    # CON: this pair used to say "vampires that steal", and the app was RIGHT
    # to refuse it. Resolution was correct -- t:vampire otag:theft -- but that
    # is 11 cards in all of Magic and exactly 5 in WB identity, so readiness
    # reported pool:5 and the pair sat UNKNOWN for the whole session.
    #
    # A wish nobody could build is not a test of behaviour discrimination, it
    # is a test of refusal, which CON-167 already covers. Dropping the tribe
    # takes the pool from 5 to 88 (otag:theft t:creature id<=WB) and lets the
    # pair measure what it was written to measure.
    "creatures-steal": (
        "I want a deck about stealing — take my opponents' creatures and "
        "permanents and use them against them. Theft and control."
    ),
    "vampires-drain": (
        "I want vampires that drain — make opponents lose life whenever I "
        "gain life. Lifedrain, not stealing their cards."
    ),
    # CON-172: "cheap" means mana value, not dollars. Brad: price is hard to
    # control and Scryfall totals are ballparks, never receipts.
    "vampires-lowcurve": (
        "I want a vampire lifegain drain deck that is cheap and fast, low "
        "mana costs — I want to be doing things early."
    ),
    "vampires-highcurve": (
        "I want a vampire lifegain drain deck with big expensive spells — "
        "I don't mind waiting."
    ),
}

PAIRS: list[dict[str, Any]] = [
    {
        "id": "tribe-angels-vs-vampires",
        "a": "angels-lifegain",
        "b": "vampires-lifegain",
        "distinguishes": "same behaviour, different tribe",
        "expect": {
            "jaccard": True,
            "commanders_differ": True,
            "membership": {
                "a": {"query": "t:angel", "min": 5, "label": "angels"},
                "b": {"query": "t:vampire", "min": 5, "label": "vampires"},
            },
        },
    },
    {
        # CON-185: the archetype axis was gated on life_math and dead for
        # twelve of thirteen playstyles -- and EVERY pair here was lifegain-
        # flavoured, so it was only ever exercised where it worked. Every test
        # passed while the axis was inert. This pair is deliberately
        # NON-lifegain so the tempo axis is tested outside its old region.
        "id": "archetype-sacrifice-incremental-vs-burst",
        "a": "sac-incremental",
        "b": "sac-burst",
        "distinguishes": "tempo on a NON-lifegain theme (CON-185 blind spot)",
        "expect": {
            "jaccard": True,
            "archetypes": ("incremental", "burst"),
            "commitments_differ": True,
            "commitments_meet_min": True,
        },
    },
    {
        "id": "archetype-incremental-vs-burst",
        "a": "drain-incremental",
        "b": "drain-burst",
        "distinguishes": "same tribe, different archetype (CON-138 split)",
        "expect": {
            "jaccard": True,
            "archetypes": ("incremental", "burst"),
            "commitments_differ": True,
            "commitments_meet_min": True,
        },
    },
    {
        "id": "behaviour-steal-vs-drain",
        "a": "creatures-steal",
        "b": "vampires-drain",
        "distinguishes": "same tribe, different behaviour tag (otag:)",
        "expect": {
            "jaccard": True,
            "commanders_differ": True,
            "membership": {
                "a": {"query": "otag:theft", "min": 3, "label": "theft"},
                "b": {"query": "otag:lifedrain", "min": 3, "label": "lifedrain"},
            },
        },
    },
    {
        "id": "curve-low-vs-high",
        "a": "vampires-lowcurve",
        "b": "vampires-highcurve",
        "distinguishes": "curve actually binds (mana value, not dollars)",
        "expect": {
            "jaccard": True,
            "curve_binds": True,
        },
    },
]

# Jaccard: both directions matter. Identical decks (1.0) mean it did not
# listen; disjoint decks with the same commander mean it is random.
# CON-168: publish the measured number; do not invent an upper bar first.
# JACCARD_MAX is filled in after a measured run (or left None = report only).
JACCARD_MAX: float | None = None
JACCARD_MIN = 0.0  # exclusive — zero with shared commander fails separately

MAX_CHAT_TURNS = 5
SCRYFALL = "https://api.scryfall.com"
USER_AGENT = "DeckwrightDifferentialHarness/0.1 (CON-168; local)"


# ---------------------------------------------------------------------------
# Pure results — offline-testable
# ---------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | UNKNOWN
    detail: str = ""


@dataclass
class WishRun:
    wish_id: str
    wish: str
    status: str  # ok | unknown
    unknown_reason: str | None
    brief: dict | None
    commander_picks: list[str]
    commander_note: str
    commander_query: str
    commander: str | None
    deck_names: set[str]
    deck: list[dict]
    plan: dict | None
    price: float | None
    budget: dict | None
    elapsed_s: float = 0.0
    commander_card: dict | None = None


def jaccard(a: set[str], b: set[str]) -> float | None:
    """Overlap in [0, 1]. Empty sides are UNKNOWN, not zero."""
    if not a or not b:
        return None
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def commitment_actual(deck: list[dict], commitment: dict) -> int | None:
    """Count deck cards matching the commitment's stated role.

    Returns None when the commitment has no countable role — unknown, not zero.
    """
    counts = commitment.get("counts") or {}
    role = counts.get("role")
    if not role:
        return None
    return sum(1 for c in deck if (c.get("role") or "") == role)


def top_commitments(plan: dict | None, n: int = 3) -> str:
    if not plan:
        return "—"
    commits = plan.get("commitments") or []
    if not commits:
        return "(none)"
    parts = []
    for c in commits[:n]:
        mn = (c.get("counts") or {}).get("min")
        label = c.get("label") or c.get("id") or "?"
        parts.append(f"{label}≥{mn}" if mn is not None else str(label))
    return "; ".join(parts)


def archetype_label(plan: dict | None) -> str:
    if not plan:
        return "—"
    arch = plan.get("archetype") or {}
    aid = arch.get("id") or ""
    label = arch.get("label") or ""
    decided = arch.get("decided")
    if not aid and not label:
        return "undecided" if decided is False else "—"
    core = aid or label
    if decided is False:
        return f"{core} (undecided)"
    return f"{core}" + (f"/{label}" if label and label != core else "")


def _unknown_pair(reason: str, names: Iterable[str]) -> list[Check]:
    return [Check(n, "UNKNOWN", reason) for n in names]


def compare_pair(
    a: WishRun,
    b: WishRun,
    expect: dict[str, Any],
    membership_hits: dict[str, set[str] | None],
    jaccard_max: float | None = JACCARD_MAX,
) -> list[Check]:
    """Assert the difference is the one the plan claims.

    membership_hits maps Scryfall query → set of card names that matched, or
    None when the query could not be looked up (UNKNOWN, not zero).
    """
    out: list[Check] = []

    if a.status != "ok" or b.status != "ok":
        reasons = []
        if a.status != "ok":
            reasons.append(f"{a.wish_id}: {a.unknown_reason or 'unknown'}")
        if b.status != "ok":
            reasons.append(f"{b.wish_id}: {b.unknown_reason or 'unknown'}")
        detail = ("excluded from comparison — " + "; ".join(reasons)
                  + ". A refused or missing build is UNKNOWN, not 'different'.")
        wanted = []
        if expect.get("jaccard"):
            wanted.append("jaccard overlap")
        if expect.get("commanders_differ"):
            wanted.append("commander picks differ")
        if expect.get("archetypes"):
            wanted.append("archetype differs")
        if expect.get("commitments_differ"):
            wanted.append("commitments differ")
        if expect.get("commitments_meet_min"):
            wanted.append("commitments meet min")
        if expect.get("membership"):
            wanted.append("membership")
        if expect.get("curve_binds"):
            wanted.append("curve binds")
        return _unknown_pair(detail, wanted or ["pair"])

    if expect.get("jaccard"):
        out.append(_check_jaccard(a, b, jaccard_max))

    if expect.get("commanders_differ"):
        out.append(_check_commanders(a, b))

    if expect.get("archetypes"):
        out.append(_check_archetypes(a, b, tuple(expect["archetypes"])))

    if expect.get("commitments_differ"):
        out.append(_check_commitments_differ(a, b))

    if expect.get("commitments_meet_min"):
        out.append(_check_commitments_meet_min(a, b))

    if expect.get("membership"):
        out.extend(_check_membership(a, b, expect["membership"], membership_hits))

    if expect.get("curve_binds"):
        out.append(_check_curve(a, b))

    return out


def _check_jaccard(a: WishRun, b: WishRun, jaccard_max: float | None) -> Check:
    j = jaccard(a.deck_names, b.deck_names)
    if j is None:
        return Check("jaccard overlap", "UNKNOWN",
                     "nothing to check — empty 99 on at least one side")
    pct = f"{j * 100:.1f}%"
    if j >= 1.0 - 1e-12:
        return Check(
            "jaccard overlap", "FAIL",
            f"these two wishes produced the same deck (Jaccard {pct})")
    same_cmdr = bool(a.commander and a.commander == b.commander)
    if j <= JACCARD_MIN + 1e-12 and same_cmdr:
        return Check(
            "jaccard overlap", "FAIL",
            f"decks are disjoint under the same commander {a.commander!r} "
            f"(Jaccard {pct}) — looks random, not listening")
    if jaccard_max is not None and j > jaccard_max:
        return Check(
            "jaccard overlap", "FAIL",
            f"Jaccard {pct} exceeds stated max {jaccard_max * 100:.1f}%")
    detail = f"Jaccard {pct} (shared {len(a.deck_names & b.deck_names)} / "
    detail += f"union {len(a.deck_names | b.deck_names)})"
    if jaccard_max is None:
        detail += " — upper bar not yet calibrated (CON-168: publish first)"
    return Check("jaccard overlap", "PASS", detail)


def _check_commanders(a: WishRun, b: WishRun) -> Check:
    pa, pb = list(a.commander_picks), list(b.commander_picks)
    if not pa or not pb:
        return Check("commander picks differ", "UNKNOWN",
                     "nothing to check — empty commander list")
    if pa == pb:
        note = a.commander_note or b.commander_note or ""
        detail = ("these two wishes returned the same commander picks "
                  f"({', '.join(pa[:5])})")
        if note:
            detail += f" — note: {note[:160]}"
        else:
            detail += " — ladder may have fallen to a generic rung"
        return Check("commander picks differ", "FAIL", detail)
    overlap = set(pa) & set(pb)
    return Check(
        "commander picks differ", "PASS",
        f"{a.commander!r} vs {b.commander!r}; "
        f"pick overlap {len(overlap)}/{max(len(pa), len(pb))}")


def _check_archetypes(a: WishRun, b: WishRun,
                      expected: tuple[str, str]) -> Check:
    aid = ((a.plan or {}).get("archetype") or {}).get("id")
    bid = ((b.plan or {}).get("archetype") or {}).get("id")
    if not aid or not bid:
        return Check(
            "archetype differs", "UNKNOWN",
            f"archetype missing — a={aid!r} b={bid!r}")
    want_a, want_b = expected
    if aid == bid:
        return Check(
            "archetype differs", "FAIL",
            f"both sides chose {aid!r}; wanted {want_a!r} vs {want_b!r}")
    # Order-tolerant: the pair states the two ids, not which wish owns which,
    # once we know they differ. Still report if neither matches the claim.
    got = {aid, bid}
    want = {want_a, want_b}
    if got != want:
        return Check(
            "archetype differs", "FAIL",
            f"got {aid!r} vs {bid!r}; wanted {want_a!r} vs {want_b!r}")
    return Check("archetype differs", "PASS", f"{aid} vs {bid}")


def _check_commitments_differ(a: WishRun, b: WishRun) -> Check:
    # CON-186: compare (id, min), not id alone. Two plans that ask for the same
    # FAMILIES but different AMOUNTS are different plans — "13 lifegain cards"
    # and "7 lifegain cards" are not the same instruction.
    #
    # Ids-only could not see a floor change, so a tempo that correctly moved
    # every commitment's demand still read as "identical plans". The check was
    # measuring plan shape and calling it plan content.
    _ids = lambda run: [
        (c.get("id"), (c.get("counts") or {}).get("min"))
        for c in ((run.plan or {}).get("commitments") or [])
    ]
    ca, cb = _ids(a), _ids(b)
    if not ca and not cb:
        return Check("commitments differ", "UNKNOWN",
                     "nothing to check — both plans have zero commitments")
    if ca == cb:
        return Check(
            "commitments differ", "FAIL",
            f"both plans commit to the same ids: {ca}")
    return Check("commitments differ", "PASS",
                 f"{ca} vs {cb}")


def _check_commitments_meet_min(a: WishRun, b: WishRun) -> Check:
    misses: list[str] = []
    unknowns: list[str] = []
    for run in (a, b):
        commits = (run.plan or {}).get("commitments") or []
        if not commits:
            unknowns.append(f"{run.wish_id}: no commitments")
            continue
        for c in commits:
            mn = (c.get("counts") or {}).get("min")
            if mn is None:
                unknowns.append(f"{run.wish_id}:{c.get('id')} has no min")
                continue
            actual = commitment_actual(run.deck, c)
            if actual is None:
                unknowns.append(
                    f"{run.wish_id}:{c.get('id')} has no countable role")
                continue
            if actual < int(mn):
                misses.append(
                    f"{run.wish_id} {c.get('label') or c.get('id')}: "
                    f"has {actual}, min {mn}")
    if misses:
        return Check("commitments meet min", "FAIL", "; ".join(misses))
    if unknowns and not misses:
        # If every commitment was uncountable, that is UNKNOWN. Partial
        # unknowns with all countable ones met still PASS — say so.
        countable_ok = not unknowns or any(
            commitment_actual(run.deck, c) is not None
            and (c.get("counts") or {}).get("min") is not None
            for run in (a, b)
            for c in ((run.plan or {}).get("commitments") or [])
        )
        if not countable_ok:
            return Check("commitments meet min", "UNKNOWN",
                         "; ".join(unknowns))
    return Check("commitments meet min", "PASS",
                 "each side meets its own stated min"
                 + (f" (also: {'; '.join(unknowns)})" if unknowns else ""))


def _check_membership(
    a: WishRun,
    b: WishRun,
    spec: dict[str, dict],
    hits: dict[str, set[str] | None],
) -> list[Check]:
    out: list[Check] = []
    side_run = {"a": a, "b": b}
    for side, conf in spec.items():
        run = side_run[side]
        q = conf["query"]
        label = conf.get("label") or q
        mn = int(conf.get("min") or 1)
        if q not in hits:
            out.append(Check(
                f"membership:{label}", "UNKNOWN",
                f"query {q!r} was never resolved — could not look"))
            continue
        matched = hits[q]
        if matched is None:
            out.append(Check(
                f"membership:{label}", "UNKNOWN",
                f"query {q!r} unreachable — not a zero"))
            continue
        n = len(run.deck_names & matched)
        if n < mn:
            out.append(Check(
                f"membership:{label}", "FAIL",
                f"{run.wish_id} deck has {n} cards matching {q!r}, "
                f"want ≥{mn}"))
        else:
            out.append(Check(
                f"membership:{label}", "PASS",
                f"{run.wish_id}: {n} cards match {q!r}"))
    return out


def deck_curve_stats(deck: list[dict]) -> dict[str, float | int] | None:
    """Average mana value and MV≤2 count for non-land cards.

    Returns None when CMC is missing on any counted card — unknown, not zero.
    Lands (by role or type) are excluded; they are not the curve she asked about.
    """
    vals: list[float] = []
    for c in deck:
        role = (c.get("role") or "").lower()
        typ = (c.get("type") or c.get("type_line") or "").lower()
        head = typ.split("—")[0]
        if role in ("lands", "basic") or "land" in head.split():
            continue
        if "cmc" not in c and "mana_value" not in c:
            return None
        raw = c["cmc"] if "cmc" in c else c.get("mana_value")
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            return None
    if not vals:
        return None
    return {
        "avg_mv": sum(vals) / len(vals),
        "n_le_2": sum(1 for v in vals if v <= 2.0 + 1e-12),
        "n": len(vals),
    }


def _check_curve(a: WishRun, b: WishRun) -> Check:
    """CON-172/175: low-curve side (a) must have lower avg MV and more MV≤2 cards."""
    sa, sb = deck_curve_stats(a.deck), deck_curve_stats(b.deck)
    if sa is None or sb is None:
        return Check(
            "curve binds", "UNKNOWN",
            "mana value missing on at least one side — not a zero average")
    # CON-175: surface what the plan heard — if both are mid, the ear failed
    # upstream and the skeleton's fixed MV≤2 count is all that remains.
    pref_a = (a.plan or {}).get("curve")
    pref_b = (b.plan or {}).get("curve")
    heard = f"prefs {pref_a!r}/{pref_b!r}"
    problems = []
    if sa["avg_mv"] >= sb["avg_mv"]:
        problems.append(
            f"low-curve avg MV {sa['avg_mv']:.2f} is not below "
            f"high-curve {sb['avg_mv']:.2f}")
    if sa["n_le_2"] <= sb["n_le_2"]:
        problems.append(
            f"low-curve MV≤2 count {sa['n_le_2']} is not above "
            f"high-curve {sb['n_le_2']}")
    if problems:
        return Check("curve binds", "FAIL", "; ".join(problems) + f" ({heard})")
    return Check(
        "curve binds", "PASS",
        f"low avg MV {sa['avg_mv']:.2f} ({sa['n_le_2']}≤2) vs "
        f"high avg MV {sb['avg_mv']:.2f} ({sb['n_le_2']}≤2) ({heard})")


def format_report(
    runs: list[WishRun],
    pair_results: list[tuple[str, WishRun, WishRun, list[Check]]],
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 88)
    lines.append(f"{'wish':<22} {'commander':<28} {'archetype':<18} "
                 f"{'top commitments':<30} {'price':>8}")
    lines.append("-" * 88)
    for r in runs:
        if r.status != "ok":
            lines.append(f"{r.wish_id:<22} {'UNKNOWN':<28} "
                         f"{'—':<18} {(r.unknown_reason or '')[:30]:<30} {'—':>8}")
            continue
        lines.append(
            f"{r.wish_id:<22} {(r.commander or '—')[:28]:<28} "
            f"{archetype_label(r.plan)[:18]:<18} "
            f"{top_commitments(r.plan)[:30]:<30} "
            f"{('$' + format(r.price, '.0f')) if r.price is not None else '—':>8}"
        )
    lines.append("=" * 88)
    lines.append("")
    lines.append(f"{'pair':<32} {'overlap':>9}  result")
    lines.append("-" * 88)
    for pid, a, b, checks in pair_results:
        j = jaccard(a.deck_names, b.deck_names) if (
            a.status == "ok" and b.status == "ok") else None
        overlap = f"{j * 100:.1f}%" if j is not None else "UNKNOWN"
        statuses = [c.status for c in checks]
        if "FAIL" in statuses:
            result = "FAIL"
        elif statuses and all(s == "UNKNOWN" for s in statuses):
            result = "UNKNOWN"
        elif "PASS" in statuses:
            result = "PASS"
        else:
            result = "UNKNOWN"
        lines.append(f"{pid:<32} {overlap:>9}  {result}")
        for c in checks:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN"}[c.status]
            lines.append(f"    [{mark:7}] {c.name}  — {c.detail}")
    lines.append("=" * 88)
    return "\n".join(lines)


def exit_code(statuses: list[str]) -> int:
    """0 = proven green; 1 = something failed; 2 = nothing proven (all unknown)."""
    if not statuses:
        return 2
    if any(s == "FAIL" for s in statuses):
        return 1
    if any(s == "PASS" for s in statuses):
        return 0
    return 2


# ---------------------------------------------------------------------------
# Live chain — wish → chat → commanders → build
# ---------------------------------------------------------------------------

def _http_post(base: str, path: str, body: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _scryfall_headers() -> dict[str, str]:
    # Scryfall requires User-Agent AND Accept (400 without either).
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _scryfall_query_names(query: str, page_limit: int = 4) -> set[str] | None:
    """Return names matching query, or None if unreachable (not a zero)."""
    names: set[str] = set()
    url = (f"{SCRYFALL}/cards/search?"
           + urllib.parse.urlencode({"q": query, "unique": "cards"}))
    pages = 0
    while url and pages < page_limit:
        pages += 1
        req = urllib.request.Request(url, headers=_scryfall_headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return set()  # real zero — no cards matched
            return None
        except Exception:
            return None
        for card in data.get("data") or []:
            n = card.get("name")
            if n:
                names.add(n)
        url = data.get("next_page")
        time.sleep(0.1)
    return names


def deck_membership_via_query(deck_names: set[str], query: str) -> set[str] | None:
    """Intersect the 99 with a Scryfall query — the one rule applies here too.

    Chunks exact-name ORs so we never hand-write a card list to assert against.
    Returns None on transport failure (UNKNOWN), empty set on a real zero.
    """
    if not deck_names:
        return set()
    matched: set[str] = set()
    names = sorted(deck_names)
    # Keep URLs short; ~15 exact names per chunk.
    chunk_size = 15
    saw_success = False
    for i in range(0, len(names), chunk_size):
        chunk = names[i:i + chunk_size]
        clause = " or ".join(f'!"{n}"' for n in chunk)
        q = f"({query}) ({clause})"
        got = _scryfall_query_names(q, page_limit=1)
        if got is None:
            return None
        saw_success = True
        matched |= got
        time.sleep(0.15)
    return matched if saw_success else None


def format_readiness_blockers(blockers: list[dict] | None) -> list[str]:
    """CON-170: name the term, not just the signal category.

    'terms' alone hid whether vampire, drain the table, or stax blocked.
    """
    out: list[str] = []
    for b in blockers or []:
        if not isinstance(b, dict):
            continue
        sig = b.get("signal") or "unknown"
        detail = b.get("detail") or {}
        if sig == "terms":
            term = detail.get("term")
            out.append(f"terms:{term}" if term else "terms")
        elif sig == "pool":
            n = detail.get("count")
            out.append(f"pool:{n}" if isinstance(n, int) else "pool")
        else:
            out.append(str(sig))
    return out


async def _chat_until_brief(wish: str, chat_fn) -> tuple[dict | None, str]:
    """Drive interrogation until a ready brief, or UNKNOWN reason."""
    messages = [{"role": "user", "content": wish}]
    follow_ups = [
        "White and black is fine. Bracket 2, casual kitchen table.",
        "Yes — put that into the brief and we can pick a commander.",
        "That sounds right. Please emit the brief now.",
        "Go ahead and finalize the brief JSON.",
    ]
    readiness_asked = None
    last_reason = "no brief"
    for turn in range(MAX_CHAT_TURNS):
        payload = {"messages": messages}
        if readiness_asked:
            payload["readiness_asked"] = readiness_asked
        try:
            resp = await chat_fn(payload)
        except Exception as e:
            return None, _refuse_or_error("chat", e)
        text = resp.get("text") or ""
        brief = resp.get("brief")
        readiness = resp.get("readiness") or {}
        still = resp.get("still_asking", True)
        if brief and readiness.get("ready") is True:
            return brief, ""
        if brief and readiness.get("ready") is False:
            readiness_asked = readiness.get("asked_about") or readiness_asked
            last_reason = (
                f"brief not ready: blockers="
                f"{format_readiness_blockers(readiness.get('blockers') or [])}")
        elif brief:
            # No readiness object (older server) — accept the brief.
            return brief, ""
        else:
            last_reason = resp.get("brief_error") or "no brief in chat response"
        messages.append({"role": "assistant", "content": text or "(…)"})
        reply = follow_ups[turn] if turn < len(follow_ups) else follow_ups[-1]
        # If readiness asked a concrete question, answer it directly.
        q = (readiness.get("question") or "").strip()
        if q:
            # This used to answer every question with "black/white vampires or
            # angels as I said" — hardcoded from the original lifegain pairs.
            # For any other wish that is an incoherent answer, so the
            # interrogation never converged and the pair reported "no brief in
            # chat response". The harness was contradicting the very wish it
            # was testing.
            #
            # Same lifegain blind spot CON-185 found in archetypes_for, one
            # layer out: the instrument assumed the theme it was built on.
            # The answer must not assert a theme — the WISH already did.
            reply = (f"{q} — for my part: stick with what I already said, "
                     f"bracket 2, around $100 unless I asked for cheap. "
                     f"{reply}")
        messages.append({"role": "user", "content": reply})
        if not still and not brief:
            break
    return None, last_reason


async def run_wish(wish_id: str, wish: str, api) -> WishRun:
    """Walk wish → brief → commanders → build. UNKNOWN on refuse/miss."""
    t0 = time.time()
    print(f"\n── {wish_id} ──", flush=True)
    print(f"  wish: {wish[:100]}{'…' if len(wish) > 100 else ''}", flush=True)

    brief, reason = await _chat_until_brief(wish, api.chat)
    if not brief or not (brief.get("theme") or "").strip():
        print(f"  UNKNOWN: {reason or 'empty brief'}", flush=True)
        return WishRun(
            wish_id=wish_id, wish=wish, status="unknown",
            unknown_reason=reason or "empty brief",
            brief=brief, commander_picks=[], commander_note="",
            commander_query="", commander=None, deck_names=set(),
            deck=[], plan=None, price=None, budget=None,
            elapsed_s=time.time() - t0,
        )

    print(f"  brief: theme={brief.get('theme')!r} colors={brief.get('colors')!r} "
          f"budget={brief.get('budget_total')!r}", flush=True)

    try:
        cmd = await api.commanders(brief)
    except Exception as e:
        return WishRun(
            wish_id=wish_id, wish=wish, status="unknown",
            unknown_reason=_refuse_or_error("commanders", e),
            brief=brief, commander_picks=[], commander_note="",
            commander_query="", commander=None, deck_names=set(),
            deck=[], plan=None, price=None, budget=None,
            elapsed_s=time.time() - t0,
        )

    picks = [p.get("name") for p in (cmd.get("picks") or [])
             if isinstance(p, dict) and p.get("name")]
    note = str(cmd.get("note") or "")
    query = str(cmd.get("query") or "")
    print(f"  commanders: {', '.join(picks[:5]) or '(none)'}", flush=True)
    if note:
        print(f"  note: {note[:120]}", flush=True)

    if not picks:
        return WishRun(
            wish_id=wish_id, wish=wish, status="unknown",
            unknown_reason="no commander picks",
            brief=brief, commander_picks=[], commander_note=note,
            commander_query=query, commander=None, deck_names=set(),
            deck=[], plan=None, price=None, budget=None,
            elapsed_s=time.time() - t0,
        )

    commander = picks[0]
    try:
        build = await api.build({"brief": brief, "commander": commander})
    except Exception as e:
        return WishRun(
            wish_id=wish_id, wish=wish, status="unknown",
            unknown_reason=_refuse_or_error("build", e),
            brief=brief, commander_picks=picks, commander_note=note,
            commander_query=query, commander=commander, deck_names=set(),
            deck=[], plan=None, price=None, budget=None,
            elapsed_s=time.time() - t0,
        )

    deck = [c for c in (build.get("deck") or []) if isinstance(c, dict) and c.get("name")]
    names = {c["name"] for c in deck}
    plan = build.get("plan")
    price = build.get("price")
    budget = build.get("budget")
    commander_card = build.get("commander_card") if isinstance(
        build.get("commander_card"), dict) else None
    arch = (plan or {}).get("archetype") or {}
    # CON-176: source says whether strategy_shape or phrases decided.
    print(f"  build: {len(deck)} cards, ${price}, "
          f"archetype={arch.get('id')!r} decided={arch.get('decided')} "
          f"source={arch.get('source')!r} "
          f"strategy_shape={(brief or {}).get('strategy_shape')!r}",
          flush=True)

    # CON-173: cohesion is the horizontal clause — report it on every build.
    if deck and commander_card:
        try:
            from deckwright import synergy
            c = synergy.cohesion(deck, commander_card)
            print(f"  [cohesion] {c['edges']} edges · {c['orphans']} orphans · "
                  f"{c['unknown']} unreadable · hub {c['hub_dependence']:.0%}",
                  flush=True)
        except Exception as e:
            print(f"  [cohesion] unavailable: {e}", flush=True)

    if len(deck) < 90:
        return WishRun(
            wish_id=wish_id, wish=wish, status="unknown",
            unknown_reason=f"deck has {len(deck)} cards, not a 99",
            brief=brief, commander_picks=picks, commander_note=note,
            commander_query=query, commander=commander, deck_names=names,
            deck=deck, plan=plan, price=price, budget=budget,
            elapsed_s=time.time() - t0, commander_card=commander_card,
        )

    return WishRun(
        wish_id=wish_id, wish=wish, status="ok", unknown_reason=None,
        brief=brief, commander_picks=picks, commander_note=note,
        commander_query=query, commander=commander, deck_names=names,
        deck=deck, plan=plan, price=price, budget=budget,
        elapsed_s=time.time() - t0, commander_card=commander_card,
    )


def _refuse_or_error(stage: str, e: BaseException) -> str:
    """Classify a chain failure for UNKNOWN reporting.

    FastAPI HTTPException carries status_code + detail (CON-167 422).
    Anthropic SDK errors also carry status_code; those are transport/quota
    failures, not brief refusals — label them as errors so the report does
    not read like a successful CON-167 guard.
    """
    code = getattr(e, "status_code", None)
    detail = getattr(e, "detail", None)
    msg = str(detail or e)
    if "credit balance is too low" in msg.lower():
        msg = "Anthropic API credit balance too low"
    elif len(msg) > 200:
        msg = msg[:200] + "…"
    # App-level refusal (brief unusable) vs upstream model/HTTP failure.
    if code == 422 or (code in (400, 422) and "brief" in msg.lower()):
        return f"{stage} refused ({code}): {msg}"
    return f"{stage} error: {type(e).__name__}: {msg}"


class InProcessAPI:
    """Call app handlers directly — no server required."""

    def __init__(self):
        from deckwright import app as app_mod
        self.app = app_mod
        self.Turn = app_mod.Turn

    async def chat(self, payload: dict) -> dict:
        t = self.Turn(messages=payload["messages"],
                      readiness_asked=payload.get("readiness_asked"))
        return await self.app.chat(t)

    async def commanders(self, brief: dict) -> dict:
        return await self.app.commanders(brief)

    async def build(self, payload: dict) -> dict:
        return await self.app.build(payload)


class HttpAPI:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    async def chat(self, payload: dict) -> dict:
        return await asyncio.to_thread(_http_post, self.base, "/api/chat", payload)

    async def commanders(self, brief: dict) -> dict:
        return await asyncio.to_thread(_http_post, self.base, "/api/commanders", brief)

    async def build(self, payload: dict) -> dict:
        return await asyncio.to_thread(
            _http_post, self.base, "/api/build", payload, 1200)


def _collect_membership_queries(pairs: list[dict]) -> list[str]:
    qs: list[str] = []
    for p in pairs:
        mem = (p.get("expect") or {}).get("membership") or {}
        for conf in mem.values():
            q = conf.get("query")
            if q and q not in qs:
                qs.append(q)
    return qs


def load_runs_from_dump(path: Path, wish_ids: list[str]) -> dict[str, WishRun]:
    """Rebuild WishRun records from a prior --dump (no model calls)."""
    data = json.loads(path.read_text())
    raw = data.get("runs") or {}
    out: dict[str, WishRun] = {}
    for wid in wish_ids:
        r = raw.get(wid)
        if not r:
            out[wid] = WishRun(
                wish_id=wid, wish=WISHES.get(wid, wid), status="unknown",
                unknown_reason=f"missing from dump {path}",
                brief=None, commander_picks=[], commander_note="",
                commander_query="", commander=None, deck_names=set(),
                deck=[], plan=None, price=None, budget=None,
            )
            continue
        deck = r.get("deck") or [
            {"name": n, "role": "theme"} for n in (r.get("deck_names") or [])
        ]
        names = {c["name"] for c in deck if c.get("name")}
        if not names:
            names = set(r.get("deck_names") or [])
        out[wid] = WishRun(
            wish_id=r.get("wish_id") or wid,
            wish=r.get("wish") or WISHES.get(wid, wid),
            status=r.get("status") or "unknown",
            unknown_reason=r.get("unknown_reason"),
            brief=r.get("brief"),
            commander_picks=list(r.get("commander_picks") or []),
            commander_note=str(r.get("commander_note") or ""),
            commander_query=str(r.get("commander_query") or ""),
            commander=r.get("commander"),
            deck_names=names,
            deck=deck,
            plan=r.get("plan"),
            price=r.get("price"),
            budget=r.get("budget"),
            elapsed_s=float(r.get("elapsed_s") or 0),
        )
    return out


async def async_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CON-168 differential harness")
    ap.add_argument("--pair", action="append", dest="pairs",
                    help="Run only this pair id (repeatable)")
    ap.add_argument("--base", default="",
                    help="HTTP base (e.g. http://127.0.0.1:8130). "
                         "Default: in-process app handlers.")
    ap.add_argument("--jaccard-max", type=float, default=None,
                    help="Fail when Jaccard exceeds this (0-1). "
                         "Default: report only until calibrated.")
    ap.add_argument("--dump", type=Path, default=None,
                    help="Write raw run JSON to this path")
    ap.add_argument("--from-dump", type=Path, default=None,
                    help="Re-compare from a prior --dump JSON (no model calls)")
    args = ap.parse_args(argv)

    pairs = PAIRS
    if args.pairs:
        want = set(args.pairs)
        pairs = [p for p in PAIRS if p["id"] in want]
        missing = want - {p["id"] for p in pairs}
        if missing:
            print(f"unknown pair ids: {sorted(missing)}", file=sys.stderr)
            return 2

    jmax = args.jaccard_max if args.jaccard_max is not None else JACCARD_MAX

    wish_ids: list[str] = []
    for p in pairs:
        for k in ("a", "b"):
            if p[k] not in wish_ids:
                wish_ids.append(p[k])

    runs: dict[str, WishRun] = {}
    if args.from_dump:
        print(f"mode: from-dump {args.from_dump}", flush=True)
        runs = load_runs_from_dump(args.from_dump, wish_ids)
    elif args.base:
        api: Any = HttpAPI(args.base)
        print(f"mode: HTTP {args.base}", flush=True)
        for wid in wish_ids:
            runs[wid] = await run_wish(wid, WISHES[wid], api)
    else:
        api = InProcessAPI()
        await api.app.load_vocab()
        print("mode: in-process", flush=True)
        for wid in wish_ids:
            runs[wid] = await run_wish(wid, WISHES[wid], api)

    # Resolve membership queries once; intersect per-deck below.
    membership_hits: dict[str, set[str] | None] = {}
    for q in _collect_membership_queries(pairs):
        # Prefer per-deck intersection (exact), but pre-seed with None so a
        # missing key means "never resolved".
        membership_hits[q] = None

    pair_results: list[tuple[str, WishRun, WishRun, list[Check]]] = []
    all_statuses: list[str] = []

    for p in pairs:
        a, b = runs[p["a"]], runs[p["b"]]
        # Build per-pair membership hits from the two decks.
        hits: dict[str, set[str] | None] = {}
        mem = (p.get("expect") or {}).get("membership") or {}
        if a.status == "ok" and b.status == "ok":
            for side, conf in mem.items():
                q = conf["query"]
                run = a if side == "a" else b
                print(f"  membership query {q!r} against {run.wish_id}…",
                      flush=True)
                got = await asyncio.to_thread(
                    deck_membership_via_query, run.deck_names, q)
                hits[q] = got
                membership_hits[q] = got
        checks = compare_pair(a, b, p["expect"], hits, jaccard_max=jmax)
        pair_results.append((p["id"], a, b, checks))
        all_statuses.extend(c.status for c in checks)

    ordered_runs = [runs[w] for w in wish_ids]
    print(format_report(ordered_runs, pair_results))

    # Pair-level rollup for exit.
    pair_statuses = []
    for pid, a, b, checks in pair_results:
        st = [c.status for c in checks]
        if "FAIL" in st:
            pair_statuses.append("FAIL")
        elif st and all(s == "UNKNOWN" for s in st):
            pair_statuses.append("UNKNOWN")
        elif "PASS" in st:
            pair_statuses.append("PASS")
        else:
            pair_statuses.append("UNKNOWN")

    p = sum(1 for s in pair_statuses if s == "PASS")
    f = sum(1 for s in pair_statuses if s == "FAIL")
    u = sum(1 for s in pair_statuses if s == "UNKNOWN")
    print(f"\n  {p} pairs pass · {f} fail · {u} unknown")
    for pid, a, b, checks in pair_results:
        for c in checks:
            if c.status == "FAIL":
                print(f"    FAIL  {pid}: {c.name} — {c.detail}")

    if args.dump:
        payload = {
            "runs": {
                wid: {
                    "wish_id": r.wish_id,
                    "wish": r.wish,
                    "status": r.status,
                    "unknown_reason": r.unknown_reason,
                    "brief": r.brief,
                    "commander": r.commander,
                    "commander_picks": r.commander_picks,
                    "commander_note": r.commander_note,
                    "commander_query": r.commander_query,
                    "deck_names": sorted(r.deck_names),
                    "deck": [{"name": c.get("name"), "role": c.get("role"),
                              "cmc": c.get("cmc"), "type": c.get("type")}
                             for c in r.deck if c.get("name")],
                    "plan": r.plan,
                    "price": r.price,
                    "budget": r.budget,
                    "elapsed_s": r.elapsed_s,
                }
                for wid, r in runs.items()
            },
            "pairs": [
                {
                    "id": pid,
                    "checks": [{"name": c.name, "status": c.status,
                                "detail": c.detail} for c in checks],
                    "jaccard": jaccard(a.deck_names, b.deck_names),
                }
                for pid, a, b, checks in pair_results
            ],
            "jaccard_max": jmax,
        }
        args.dump.write_text(json.dumps(payload, indent=2))
        print(f"\n  wrote {args.dump}")

    return exit_code(pair_statuses)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    sys.exit(main())
