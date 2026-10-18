"""
test_commanders_kb.py — CON-190 / CON-194 commander knowledge base contract.

Offline. No model. No Scryfall (build script may use network; this file does not).

    python test_commanders_kb.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import commanders  # noqa: E402
from deckwright import playstyles  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

FIXTURE = ROOT / "testdata" / "review" / "commander-playstyles.json"
VOCAB = ROOT / "data" / "playstyles.json"
KB_PATH = ROOT / "data" / "commanders.json"
GRAMMAR = ROOT / "data" / "scryfall_grammar.json"

# App's own legal-commander definition — matches legal_commanders_only() / CON-194.
LEGAL_QUERY = '((t:legendary t:creature) or o:"can be your commander") f:commander'


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


def test_themes_from_type_line_con229():
    """CON-229: drop human / // / legendary and card-type junk from KB themes."""
    print("\ncommanders.themes_from_type_line — CON-229 pollution filter")
    eq("creature types kept",
       commanders.themes_from_type_line("Legendary Creature — Bear Spirit"),
       ["bear", "spirit"])
    eq("human dropped (GA path)",
       commanders.themes_from_type_line("Legendary Creature — Human Artificer"),
       ["artificer"])
    eq("legendary and creature not themes",
       commanders.themes_from_type_line("Legendary Creature — Human"),
       [])
    eq("DFC // fragment dropped",
       commanders.themes_from_type_line(
           "Legendary Creature — Human // Spirit"),
       ["spirit"])
    eq("em-dash punctuation token dropped",
       commanders.themes_from_type_line("Legendary Creature — — Elf"),
       ["elf"])
    eq("card-type tokens on right side dropped",
       commanders.themes_from_type_line("Legendary Creature — Artifact Elf"),
       ["elf"])
    eq("no em-dash → []",
       commanders.themes_from_type_line("Legendary Creature Human"),
       [])
    eq("filter_theme_tokens is reusable",
       commanders.filter_theme_tokens(["human", "pirate", "//", "legendary"]),
       ["pirate"])


def test_edhrec_tag_mapping_allowlist_only():
    """CON-196: EDHREC tags map onto playstyles.json — never invent ids."""
    print("\ncommanders.playstyles_from_edhrec_tags — allowlist + min count")
    tags = [
        {"value": "Blink", "count": 260, "slug": "blink"},
        {"value": "Lifegain", "count": 122, "slug": "lifegain"},
        {"value": "Equipment", "count": 80, "slug": "equipment"},  # → voltron
        {"value": "Flying", "count": 61, "slug": "flying"},
        {"value": "Tokens", "count": 5, "slug": "tokens"},  # at min_count=5
        {"value": "Treasure", "count": 40, "slug": "treasure"},  # → artifacts
        {"value": "Populate", "count": 8, "slug": "populate"},  # → tokens
        {"value": "Made Up Strategy", "count": 999, "slug": "made-up"},
        {"value": "+1/+1 Counters", "count": 14, "slug": "plus-1-plus-1-counters"},
        {"value": "Elves", "count": 5000, "slug": "elves"},  # type ≠ playstyle
        {"value": "Auras", "count": 2, "slug": "auras"},  # below min_count=5
        {"value": "Voltron", "count": 4, "slug": "voltron"},  # Akroma-class: drops at 5
    ]
    got = commanders.playstyles_from_edhrec_tags(tags)
    eq("mapped allowlisted tags only (strongest first)",
       got, ["blink", "lifegain", "voltron", "artifacts", "counters", "tokens"])
    true("auras below min_count dropped", "voltron" in got)
    # Auras=2 is below floor; Equipment already supplied voltron — no second row.
    eq("voltron once (equipment, not sub-min auras)",
       got.count("voltron"), 1)
    # CON-239: counts 3–4 drop at restored floor=5 (Akroma Voltron=4, Arcades Auras=3).
    eq("voltron=4 alone dropped at min_count=5",
       commanders.playstyles_from_edhrec_tags(
           [{"value": "Voltron", "count": 4, "slug": "voltron"}]), [])
    eq("tokens=5 kept at min_count=5",
       commanders.playstyles_from_edhrec_tags(
           [{"value": "Tokens", "count": 5, "slug": "tokens"}]), ["tokens"])
    eq("slug for accented name",
       commanders.edhrec_slug("Adéwalé, Breaker of Chains"),
       "adewale-breaker-of-chains")
    eq("slug strips apostrophe (not hyphen)",
       commanders.edhrec_slug("Amber Gristle O'Maul"),
       "amber-gristle-omaul")
    eq("slug Wind's → winds",
       commanders.edhrec_slug("Kykar, Wind's Fury"),
       "kykar-winds-fury")
    eq("slug uses front face of DFC",
       commanders.edhrec_slug("Budoka Pupil // Ichiga, Who Topples Oaks"),
       "budoka-pupil")
    eq("slug back face option",
       commanders.edhrec_slug(
           "Akki Lavarunner // Tok-Tok, Volcano Born", face="back"),
       "tok-tok-volcano-born")
    # Empty / unreadable input is empty list, not a default playstyle.
    eq("no tags → []", commanders.playstyles_from_edhrec_tags(None), [])
    eq("empty tags → []", commanders.playstyles_from_edhrec_tags([]), [])


def test_research_grow_merges_community_onto_derived():
    """CON-196 B grow: EDHREC tags merge onto derived-only ways as both."""
    print("\ncommanders.apply_edhrec_community — grow research onto derived")
    vocab = playstyles.load_playstyles()
    entry = {
        "color_identity": ["U", "B", "R"],
        "ways_to_play": [{
            "playstyle": "reanimator",
            "confidence": "medium",
            "method": "derived",
            "why": "Graveyard return.",
            "source": "detect",
        }],
        "signals": ["graveyard_recursion"],
        "mechanics_status": "ok",
        "method": "derived",
    }
    out = commanders.apply_edhrec_community(
        entry,
        community=["reanimator", "tokens", "artifacts"],
        community_source="EDHREC /commanders/admiral-brass-unsinkable",
        community_why="EDHREC tag_counts: Reanimator 435, Tokens 44, Treasure 1085",
        researched_at="2026-08-07",
        vocab=vocab,
    )
    by = {w["playstyle"]: w for w in out["ways_to_play"]}
    eq("reanimator becomes both", by["reanimator"]["method"], "both")
    eq("tokens community-only", by["tokens"]["method"], "community")
    eq("artifacts community-only", by["artifacts"]["method"], "community")
    eq("entry method both", out["method"], "both")
    true("researched stamp", out.get("researched_at") == "2026-08-07")
    true("no invent", set(by) <= set(vocab))

    targets = commanders.research_targets(
        {
            "Empty": {"ways_to_play": []},
            "Derived": entry,
            "Community": {
                "ways_to_play": [{
                    "playstyle": "tokens", "method": "community",
                }],
            },
        },
        include_empty=True,
        include_derived=True,
    )
    eq("targets empties then derived, skips community",
       targets, ["Empty", "Derived"])


def test_merge_ways_records_disagreement():
    """Both sources kept; disagreements labelled, never dropped."""
    print("\ncommanders.merge_ways — both / community / derived")
    vocab = playstyles.load_playstyles()
    ways = commanders.merge_ways(
        community=["aristocrats", "blink"],
        derived=["aristocrats", "counters"],
        scores={"aristocrats": 2, "counters": 0},
        vocab=vocab,
        community_source="EDHREC fixture",
        community_why="Sac outlet + leave trigger",
    )
    by = {w["playstyle"]: w for w in ways}
    eq("aristocrats is both", by["aristocrats"]["method"], "both")
    eq("aristocrats confidence high", by["aristocrats"]["confidence"], "high")
    eq("blink is community-only", by["blink"]["method"], "community")
    eq("blink community-only confidence medium (CON-239)",
       by["blink"]["confidence"], "medium")
    true("blink notes mechanics miss",
         "mechanics" in (by["blink"].get("note") or "").lower())
    eq("counters is derived-only", by["counters"]["method"], "derived")
    true("counters notes no community source",
         "community" in (by["counters"].get("note") or "").lower())
    # Invented playstyle ids are dropped — vocabulary is the allowlist.
    ways2 = commanders.merge_ways(
        community=["aristocrats", "made_up_style"],
        derived=["made_up_style"],
        scores={},
        vocab=vocab,
        community_source="x",
        community_why="y",
    )
    eq("invented playstyle dropped",
       [w["playstyle"] for w in ways2], ["aristocrats"])


def test_absent_vs_empty():
    """A missing entry is absent; an empty ways list is looked-and-none."""
    print("\ncommanders.lookup — absent ≠ empty")
    store = {
        "version": 1,
        "legal_estimate": 3321,
        "commanders": {
            "Researched Empty": {
                "color_identity": ["U"],
                "ways_to_play": [],
                "themes": [],
                "signals": [],
                "mechanics_status": "ok",
                "method": "derived",
                "researched_at": "2026-08-07",
            },
            "Has Ways": {
                "color_identity": ["R"],
                "ways_to_play": [{
                    "playstyle": "tokens",
                    "confidence": "high",
                    "method": "community",
                    "why": "Make tokens.",
                    "source": "fixture",
                }],
                "themes": [],
                "signals": ["token_create"],
                "mechanics_status": "ok",
                "method": "both",
                "researched_at": "2026-08-07",
            },
        },
    }
    eq("unknown name is absent",
       commanders.lookup("Never Heard Of Them", store=store), None)
    empty = commanders.lookup("Researched Empty", store=store)
    true("researched-empty is a dict", isinstance(empty, dict))
    eq("researched-empty ways is []", empty["ways_to_play"], [])
    eq("empty ways still tagged derived", empty.get("method"), "derived")
    hit = commanders.lookup("Has Ways", store=store)
    eq("researched hit has one way", len(hit["ways_to_play"]), 1)


def test_merge_ways_caps_at_five():
    """CON-239: keep both+derived, then top community by EDHREC order."""
    print("\ncommanders.merge_ways — cap at 5")
    vocab = playstyles.load_playstyles()
    # community list is EDHREC count order (strongest first).
    community = ["blink", "tokens", "lifegain", "stax", "voltron", "artifacts"]
    ways = commanders.merge_ways(
        community=community,
        derived=["aristocrats", "counters"],
        scores={"aristocrats": 3, "counters": 1},
        vocab=vocab,
        community_source="fixture",
        community_why="counts",
    )
    eq("capped at 5", len(ways), 5)
    pids = [w["playstyle"] for w in ways]
    eq("both+derived kept", set(pids) & {"aristocrats", "counters"},
       {"aristocrats", "counters"})
    eq("top community by count fill remainder",
       pids, ["aristocrats", "counters", "blink", "tokens", "lifegain"])
    for w in ways:
        true(f"{w['playstyle']} keeps method label",
             w.get("method") in ("both", "derived", "community"))
        if w["method"] == "community":
            true(f"{w['playstyle']} community note kept", bool(w.get("note")))


def test_disagreement_rate():
    """Disagreement rate is measured, not collapsed into accuracy."""
    print("\ncommanders.disagreement_rate — community↔derived gaps")
    store = {
        "commanders": {
            "A": {"ways_to_play": [
                {"playstyle": "aristocrats", "method": "both"},
                {"playstyle": "blink", "method": "derived"},
            ]},
            "B": {"ways_to_play": [
                {"playstyle": "stax", "method": "community"},
            ]},
            "C": {"ways_to_play": []},
        }
    }
    rate = commanders.disagreement_rate(store)
    eq("derived_only count", rate["derived_only"], 1)
    eq("community_only count", rate["community_only"], 1)
    eq("both count", rate["both"], 1)
    eq("commanders_with_ways", rate["commanders_with_ways"], 2)


def test_coverage_reports_both_halves():
    """CON-194/196: mechanics, ways-to-play, and research — three numbers, always."""
    print("\ncommanders.coverage — three coverage numbers")
    store = {
        "legal_estimate": 3321,
        "mechanics": 3321,
        "researched": 98,
        "commanders": {
            f"C{i}": {
                "ways_to_play": (
                    [{"playstyle": "tokens", "method": "derived",
                      "confidence": "low", "why": "x", "source": "y"}]
                    if i < 1628 else []
                ),
                "mechanics_status": "ok",
                "method": "derived",
                **({"researched_at": "2026-08-07"} if i < 98 else {}),
            }
            for i in range(3321)
        },
    }
    cov = commanders.coverage(store)
    eq("mechanics count", cov["mechanics"], 3321)
    eq("ways_to_play count", cov["ways_to_play"], 1628)
    eq("research count", cov["researched"], 98)
    eq("legal total", cov["legal_estimate"], 3321)
    true("mechanics label is exact fraction",
         cov["mechanics_label"] == "3321 of 3321")
    true("ways label includes count and percent",
         "1628 of 3321" in cov["ways_to_play_label"]
         and "%" in cov["ways_to_play_label"])
    true("research label includes percent",
         "98 of 3321" in cov["research_label"] and "%" in cov["research_label"])
    # Three labels always — never collapse ways into mechanics or research.
    true("three labels present",
         "mechanics_label" in cov
         and "ways_to_play_label" in cov
         and "research_label" in cov)


def test_kb_file_contract():
    """Generated KB: full mechanics, partial research, verified names."""
    print("\ndata/commanders.json — CON-194 contract when present")
    if not KB_PATH.is_file():
        bad("data/commanders.json missing — run scripts/build_commanders_kb.py")
        return
    payload = json.loads(KB_PATH.read_text())
    entries = payload.get("commanders") or {}
    legal = int(payload.get("legal_estimate") or 0)
    mechanics = int(payload.get("mechanics")
                    or sum(1 for e in entries.values()
                           if e.get("mechanics_status") == "ok"))
    researched = int(payload.get("researched") or 0)

    true("has commanders", len(entries) >= 57, str(len(entries)))
    true("legal_estimate present", legal > 0)
    eq("legal_estimate_query matches app definition",
       payload.get("legal_estimate_query"), LEGAL_QUERY)
    eq("mechanics field matches ok entries",
       payload.get("mechanics"),
       sum(1 for e in entries.values() if e.get("mechanics_status") == "ok"))
    eq("entry count equals legal_estimate (full census)",
       len(entries), legal)
    eq("mechanics coverage is 100%", mechanics, legal)
    true("research is a counted cohort",
         0 < researched <= len(entries))
    # CON-196 B grow may fetch EDHREC for every commander (research → 100%).
    # That is not a collapse into mechanics — the three fields stay separate,
    # and ways-to-play remains a different number (allowlist hits ≠ fetches).
    ways_n = int(payload.get("ways_to_play")
                 or sum(1 for e in entries.values() if e.get("ways_to_play")))
    true("three coverage fields present",
         "mechanics" in payload and "ways_to_play" in payload
         and "researched" in payload)
    true("ways-to-play is not silently equal to research fetches",
         ways_n != researched or researched == 0)

    catalog = {}
    names = (json.loads(GRAMMAR.read_text()).get("catalogs") or {}).get("card-names") or []
    for n in names:
        catalog[commanders.squash(str(n))] = str(n)
    vocab = set(playstyles.load_playstyles())

    community_without_mechanics = []
    missing_method_on_empty = []
    for name, entry in entries.items():
        true(f"{name} verified against Scryfall catalog",
             commanders.squash(name) in catalog)
        true(f"{name} has color_identity list",
             isinstance(entry.get("color_identity"), list))
        true(f"{name} has mechanics_status",
             entry.get("mechanics_status") in ("ok", "unknown"))
        ways = entry.get("ways_to_play") or []
        if not ways:
            # Looked and found none — tagged derived, not absent.
            if entry.get("method") != "derived":
                missing_method_on_empty.append(name)
        for way in ways:
            true(f"{name} way in vocabulary", way.get("playstyle") in vocab)
            for k in ("playstyle", "confidence", "method", "why", "source"):
                true(f"{name} way has {k}", k in way)
            true(f"{name} method allowed",
                 way.get("method") in ("both", "community", "derived"))
        has_community = any(
            w.get("method") in ("community", "both") for w in ways
        )
        if has_community and entry.get("mechanics_status") != "ok":
            community_without_mechanics.append(name)

    eq("zero community-without-mechanics (CON-194)",
       community_without_mechanics, [])
    eq("empty ways tagged method=derived", missing_method_on_empty, [])

    # Fixture agreement: every fixture commander present; community playstyles
    # appear as community or both (never silently dropped).
    fixture = json.loads(FIXTURE.read_text()).get("commanders") or []
    missing = []
    dropped_community = []
    for row in fixture:
        name = row["commander"]
        if name not in entries:
            missing.append(name)
            continue
        community = set(row.get("playstyles") or []) & vocab
        got = {
            w["playstyle"]
            for w in (entries[name].get("ways_to_play") or [])
            if w.get("method") in ("community", "both")
        }
        lost = community - got
        if lost:
            dropped_community.append((name, sorted(lost)))
    eq("every fixture commander is in the KB", missing, [])
    eq("no community playstyle silently dropped", dropped_community, [])


def test_con241_empty_ways_signals_offline_count():
    """CON-241: offline empty_ways_signals_no_playstyle from stored KB signals."""
    print("\ncommanders KB — CON-241 empty_ways_signals_no_playstyle offline count")
    if not KB_PATH.is_file():
        bad("commanders.json present", "missing")
        return
    payload = json.loads(KB_PATH.read_text())
    entries = payload.get("commanders") or {}
    header = int(payload.get("empty_ways_signals_no_playstyle") or 0)
    offline = 0
    stale_would_match = 0
    for entry in entries.values():
        if entry.get("ways_to_play"):
            continue
        sigs = entry.get("signals") or []
        if not sigs:
            continue
        if playstyles.match_signals(sigs)["matched"]:
            stale_would_match += 1
            continue
        offline += 1
    true("offline count ≤ KB header (fetch_miss may lag)",
         offline <= header, f"offline={offline} header={header}")
    # CON-241 cut the pre-Know-Magic floor 195→165. CON-249 then taught more
    # residual signals: 44 commanders moved no-signal → signals-no-playstyle
    # (header 165→209). That is honesty, not a detection regression —
    # requires_any was deliberately not widened for bare enters/attacks.
    # Freeze the post-Know-Magic floor; further drops need new community
    # playstyles, not invented bare-signal matches.
    KNOW_MAGIC_SIGNALS_NO_PLAYSTYLE_FLOOR = 209
    true("CON-241 signals-no-playstyle at post-Know-Magic floor",
         header <= KNOW_MAGIC_SIGNALS_NO_PLAYSTYLE_FLOOR,
         f"header={header}")
    true("CON-241 offline count at post-Know-Magic floor",
         offline <= KNOW_MAGIC_SIGNALS_NO_PLAYSTYLE_FLOOR,
         f"offline={offline}")
    if stale_would_match:
        ok("fetch_miss rows with stale signals noted",
           f"{stale_would_match} would match on stored signals")


def main() -> int:
    test_themes_from_type_line_con229()
    test_edhrec_tag_mapping_allowlist_only()
    test_research_grow_merges_community_onto_derived()
    test_merge_ways_records_disagreement()
    test_merge_ways_caps_at_five()
    test_absent_vs_empty()
    test_disagreement_rate()
    test_coverage_reports_both_halves()
    test_kb_file_contract()
    test_con241_empty_ways_signals_offline_count()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
