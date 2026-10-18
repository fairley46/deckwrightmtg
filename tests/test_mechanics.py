"""
test_mechanics.py — mechanics substrate.

Offline unit tests. No Scryfall network. Run:

    python test_mechanics.py
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from deckwright import mechanics


def _load_taxonomy_builder():
    """Load scripts/build_mechanic_taxonomy.py (not a package)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_mechanic_taxonomy.py"
    spec = importlib.util.spec_from_file_location("build_mechanic_taxonomy", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def test_catalog_round_trip():
    """catalogs persist with fetch date; empty write refused."""
    print("\ncatalog round-trip")
    payload = {
        "fetched_at": "2026-08-06T00:00:00Z",
        "keyword_abilities": ["Flying", "Lifelink"],
        "keyword_actions": ["Scry", "Surveil"],
        "ability_words": ["Landfall", "Raid"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mechanics.json"
        mechanics.write_catalog(payload, path)
        loaded = mechanics.load_catalog(path)
        eq("fetched_at", loaded["fetched_at"], payload["fetched_at"])
        eq("abilities", loaded["keyword_abilities"], ["Flying", "Lifelink"])
        eq("actions", loaded["keyword_actions"], ["Scry", "Surveil"])
        eq("words", loaded["ability_words"], ["Landfall", "Raid"])
        eq("all_names includes Flying", "Flying" in mechanics.all_catalog_names(loaded), True)
        eq("missing file is empty shape",
           mechanics.load_catalog(Path(tmp) / "nope.json")["keyword_abilities"], [])


def test_families_load_and_lookup():
    """family taxonomy carries structural math roles."""
    print("\nfamilies  — structural roles")
    fam = mechanics.load_families()
    eq("has families", isinstance(fam.get("families"), dict) and bool(fam["families"]), True)
    # Thresholds are conditions — the math is "are we over N?"
    del_meta = mechanics.mechanic_meta("Delirium", fam)
    eq("Delirium family", del_meta["family"], "thresholds_graveyard")
    eq("Delirium plan_role", del_meta["plan_role"], "condition")
    eq("Delirium math_kind", del_meta["math_kind"], "threshold_count")
    # Flashback is a recursion engine — resource from yard.
    fb = mechanics.mechanic_meta("Flashback", fam)
    eq("Flashback family", fb["family"], "cast_from_graveyard")
    eq("Flashback plan_role", fb["plan_role"], "engine")
    # Lifelink is a rate/modifier on combat math.
    ll = mechanics.mechanic_meta("Lifelink", fam)
    eq("Lifelink family", ll["family"], "damage_modification")
    eq("Lifelink plan_role", ll["plan_role"], "modifier")
    # Unknown mechanic is honest about it.
    unk = mechanics.mechanic_meta("Not A Real Mechanic", fam)
    eq("unknown family", unk["family"], None)
    eq("unknown reviewed", unk["reviewed"], False)


def test_unreviewed_diff():
    """Catalog names missing from the family file are unreviewed, not failures."""
    print("\nunreviewed diff")
    catalog = {
        "keyword_abilities": ["Flying", "Brand New Keyword"],
        "keyword_actions": ["Scry"],
        "ability_words": [],
    }
    families = {
        "families": {
            "evasion": {
                "plan_role": "modifier",
                "math_kind": "combat_filter",
                "mechanics": ["Flying"],
            },
            "library_manipulation": {
                "plan_role": "engine",
                "math_kind": "information",
                "mechanics": ["Scry"],
            },
        },
        "lineage": [],
    }
    missing = mechanics.unreviewed(catalog, families)
    eq("new keyword flagged", missing, ["Brand New Keyword"])


def test_tag_deck_density_and_roles():
    """density by mechanic + family; role buckets for plan math."""
    print("\ntag_deck  — density + plan roles")
    families = mechanics.load_families()
    deck = [
        {"name": "Serra Angel", "type": "Creature — Angel",
         "keywords": ["Flying", "Vigilance"], "text": "Flying, vigilance"},
        {"name": "Ajani's Pridemate", "type": "Creature — Cat Soldier",
         "keywords": [], "text": "Whenever you gain life, put a +1/+1 counter on this."},
        {"name": "Vampire Nighthawk", "type": "Creature — Vampire Shaman",
         "keywords": ["Flying", "Deathtouch", "Lifelink"], "text": "Flying, deathtouch, lifelink"},
        {"name": "Fog Bank", "type": "Creature — Wall",
         "keywords": ["Flying", "Defender"], "text": "Flying, defender"},
        {"name": "Plains", "type": "Basic Land — Plains", "keywords": [], "text": ""},
    ]
    tagged = mechanics.tag_deck(deck, families)
    eq("nonland denominator", tagged["nonlands"], 4)
    fly = next(m for m in tagged["mechanics"] if m["name"] == "Flying")
    eq("Flying count", fly["count"], 3)
    eq("Flying density", fly["density"], 0.75)
    # Family rollup — evasion should include Flying.
    eva = next(f for f in tagged["families"] if f["family"] == "evasion")
    eq("evasion count is Flying cards", eva["count"], 3)
    # Role buckets exist for downstream plan math.
    eq("has role bucket modifier", "modifier" in tagged["by_role"], True)
    (ok if tagged["by_role"]["modifier"] >= 1 else bad)(
        "modifier cards counted", str(tagged["by_role"]))
    # Lands do not inflate density.
    eq("lands excluded from nonlands", tagged["lands"], 1)


def test_attach_on_persist():
    """Saved decks carry a mechanics block; expose keeps it."""
    print("\nattach on persist")
    from deckwright import app

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {
            "commander": "Test Commander",
            "deck": [
                {"name": "Serra Angel", "type": "Creature — Angel",
                 "keywords": ["Flying", "Vigilance"], "text": "Flying"},
                {"name": "Island", "type": "Basic Land — Island",
                 "keywords": [], "text": ""},
            ],
            "price": 1.0,
            "buyable": True,
            "card_pool": "tournament_legal",
        }
        app.persist_deck(out, d)
        loaded = app.load_saved_deck(out["id"], d)
        block = loaded.get("mechanics") or {}
        eq("mechanics block present", "mechanics" in loaded, True)
        eq("nonlands stamped", block.get("nonlands"), 1)
        fly = next((m for m in block.get("mechanics", []) if m["name"] == "Flying"), None)
        eq("Flying on saved deck", fly is not None and fly["count"] == 1, True)
        exposed = app.expose_saved_deck(loaded)
        eq("expose keeps mechanics", "mechanics" in exposed, True)


def test_lineage_edges():
    """Lineage is data, not prose — Storied/Ascend style edges load."""
    print("\nlineage")
    fam = mechanics.load_families()
    edges = fam.get("lineage") or []
    (ok if any(e.get("from") and e.get("to") for e in edges) else bad)(
        "at least one lineage edge", f"{len(edges)} edges")


def test_pairing_relations():
    """play-derived pairing edges expand goals and related()."""
    print("\npairing relations")
    fam = mechanics.load_families()
    pair_edges = [
        e for e in (fam.get("relations") or [])
        if isinstance(e, dict) and e.get("relation") == "pairs"
    ]
    (ok if pair_edges else bad)("at least one pairs edge", str(len(pair_edges)))
    skull = next(
        (e for e in pair_edges
         if e.get("from") == "equipment_aura" and e.get("to") == "tokens"),
        None,
    )
    (ok if skull else bad)("Skullclamp-class equipment_aura pairs tokens", str(pair_edges))
    eq("pair note names Skullclamp play pattern",
       "skullclamp" in (skull or {}).get("note", "").lower()
       or "1-toughness" in (skull or {}).get("note", "").lower(), True)

    equip_rel = mechanics.family_relations("equipment_aura", fam)
    tok_pair = next(
        (e for e in equip_rel
         if e.get("family") == "tokens" and e.get("relation") == "pairs"), None)
    (ok if tok_pair else bad)("family_relations sees tokens pair", str(equip_rel))

    expanded = mechanics.expand_goal_families(["equipment_aura"], fam, depth=1)
    (ok if "tokens" in expanded and "equipment_aura" in expanded else bad)(
        "expand equipment_aura pulls token bodies", str(expanded))

    rel = mechanics.related("Equip", fam)
    fam_rels = rel.get("family_relations") or []
    (ok if any(e.get("family") == "tokens" and e.get("relation") == "pairs"
               for e in fam_rels) else bad)(
        "related(Equip) exposes token pairing", str(fam_rels))


def test_legacy_saved_deck_loads_without_mechanics():
    """Old saves without a mechanics stamp must still load."""
    print("\nlegacy saved deck  — no mechanics stamp")
    import tempfile
    from pathlib import Path
    from deckwright import app

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        slug = "Legacy_20260101T0000001Z"
        bare = {
            "id": slug,
            "name": "Old Deck",
            "commander": "Legacy Commander",
            "deck": [{"name": "Sol Ring", "type": "Artifact", "keywords": [],
                      "text": "{T}: Add {C}{C}."}],
            "saved_at": "2026-01-01T00:00:00Z",
        }
        (d / f"{slug}.json").write_text(__import__("json").dumps(bare))
        loaded = app.load_saved_deck(slug, d)
        eq("loads without mechanics key", loaded is not None, True)
        eq("no mechanics required", "mechanics" in (loaded or {}), False)
        exposed = app.expose_saved_deck(loaded)
        eq("expose survives", exposed.get("commander"), "Legacy Commander")
        # Rewrite stamps density; first load must not have crashed.
        app.write_saved_deck(slug, loaded, d)
        again = app.load_saved_deck(slug, d)
        eq("rewrite stamps mechanics", "mechanics" in (again or {}), True)


def test_signal_fixtures_known_tricky():
    """Every signal needs a known-tricky fixture — Azusa-class misses.

    These are the silent-wrong cases: near-miss wording, negations, and
    Treasure-vs-creature splits.
    """
    print("\nsignal fixtures  — known-tricky cards")
    cases = [
        # (label, card, signal, want_hit)
        ("Exquisite Blood gains that much",
         {"name": "Exquisite Blood", "type": "Enchantment", "keywords": [],
          "text": "Whenever an opponent loses life, you gain that much life."},
         "lifegain_source", True),
        ("Sanguine Bond drains that much",
         {"name": "Sanguine Bond", "type": "Enchantment", "keywords": [],
          "text": "Whenever you gain life, target opponent loses that much life."},
         "drain", True),
        ("Negation is not lifegain",
         {"name": "Sulfuric Vortex", "type": "Enchantment", "keywords": [],
          "text": "If a player would gain life, that player gains no life instead."},
         "lifegain_source", False),
        # "gains lifelink" is not a lifegain trigger. An earlier lifelink fix covered the
        # greedy .* pattern; the two literal patterns still prefix-matched.
        ("gains lifelink is not a lifegain trigger",
         {"name": "False Lifegain", "type": "Creature", "keywords": [],
          "text": "Whenever you gain lifelink until end of turn, draw a card."},
         "lifegain_trigger", False),
        ("a player gains lifelink is not a lifegain trigger",
         {"name": "False Player Gain", "type": "Enchantment", "keywords": [],
          "text": "Whenever a player gains lifelink, you draw a card."},
         "lifegain_trigger", False),
        ("real gain-life trigger still fires",
         {"name": "Ajani's Pridemate", "type": "Creature", "keywords": [],
          "text": "Whenever you gain life, put a +1/+1 counter on Ajani's Pridemate."},
         "lifegain_trigger", True),
        ("Treasure is resource not creature token",
         {"name": "Pirate's Prize", "type": "Sorcery", "keywords": [],
          "text": "Draw a card. Create a Treasure token."},
         "creature_tokens", False),
        ("Treasure hits resource_tokens",
         {"name": "Pirate's Prize", "type": "Sorcery", "keywords": [],
          "text": "Draw a card. Create a Treasure token."},
         "resource_tokens", True),
        ("1/1 creature tokens hit creature_tokens",
         {"name": "Secure the Wastes", "type": "Instant", "keywords": [],
          "text": "Create X 1/1 white Warrior creature tokens."},
         "creature_tokens", True),
        ("Exile a token is not create",
         {"name": "Abolish Token", "type": "Instant", "keywords": [],
          "text": "Exile target token."},
         "token_create", False),
        ("for each creature you control is go-wide",
         {"name": "Coat of Arms", "type": "Artifact", "keywords": [],
          "text": "Each creature gets +1/+1 for each other creature on the battlefield that shares at least one creature type with it."},
         "go_wide_payoff", True),
        ("Hardened Scales is go-tall",
         {"name": "Hardened Scales", "type": "Enchantment", "keywords": [],
          "text": "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead."},
         "go_tall_payoff", True),
        # enters-with is counter placement, not only go_tall.
        ("Giada enters-with is counter_placement",
         {"name": "Giada, Font of Hope", "type": "Legendary Creature — Angel",
          "keywords": ["Flying", "Vigilance"],
          "text": (
              "Flying, vigilance\n"
              "Each other Angel you control enters with an additional +1/+1 "
              "counter on it for each Angel you already control.\n"
              "{T}: Add {W}. Spend this mana only to cast an Angel spell."
          )},
         "counter_placement", True),
        ("Hangarback enters-with is counter_placement",
         {"name": "Hangarback Walker", "type": "Artifact Creature",
          "keywords": [],
          "text": (
              "Hangarback Walker enters with X +1/+1 counters on it. "
              "When Hangarback Walker dies, create a 1/1 colorless Thopter "
              "artifact creature token with flying for each +1/+1 counter "
              "on it."
          )},
         "counter_placement", True),
        ("charge counters are not counter_placement",
         {"name": "Everflowing Chalice", "type": "Artifact", "keywords": [],
          "text": (
              "Multikicker {2}\n"
              "This artifact enters with a charge counter on it for each "
              "time it was kicked.\n"
              "{T}: Add {C} for each charge counter on this artifact."
          )},
         "counter_placement", False),
    ]
    for label, card, signal, want in cases:
        tagged = mechanics.tag_deck([card])
        hit = {s["name"]: s["count"] for s in tagged["signals"]}.get(signal, 0) > 0
        (ok if hit == want else bad)(label, f"signal={signal} hit={hit} want={want}")


def test_token_wide_vs_tall():
    """Tokens change board-width and board-height math differently."""
    print("\ntoken math  — go-wide vs go-tall")
    wide = [
        {"name": "Hanweir Garrison", "type": "Creature", "keywords": [],
         "text": "Whenever Hanweir Garrison attacks, create two 1/1 red Human creature tokens that are tapped and attacking."},
        {"name": "Intangible Virtue", "type": "Enchantment", "keywords": [],
         "text": "Creature tokens you control get +1/+1 and have vigilance."},
        {"name": "Coat of Arms", "type": "Artifact", "keywords": [],
         "text": "Each creature gets +1/+1 for each other creature on the battlefield that shares at least one creature type with it."},
        {"name": "Island", "type": "Basic Land — Island", "keywords": [], "text": ""},
    ]
    tall = [
        {"name": "Hangarback Walker", "type": "Artifact Creature", "keywords": [],
         "text": "Hangarback Walker enters with X +1/+1 counters on it. When Hangarback Walker dies, create a 1/1 colorless Thopter artifact creature token with flying for each +1/+1 counter on it."},
        {"name": "Hardened Scales", "type": "Enchantment", "keywords": [],
         "text": "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead."},
        {"name": "Branching Evolution", "type": "Enchantment", "keywords": [],
         "text": "If one or more +1/+1 counters would be put on a creature you control, twice that many +1/+1 counters are put on it instead."},
        {"name": "Island", "type": "Basic Land — Island", "keywords": [], "text": ""},
    ]
    treasure = [
        {"name": "Dockside but legal", "type": "Creature", "keywords": [],
         "text": "When this enters, create three Treasure tokens."},
        {"name": "Island", "type": "Basic Land — Island", "keywords": [], "text": ""},
    ]
    w = mechanics.tag_deck(wide)
    t = mechanics.tag_deck(tall)
    r = mechanics.tag_deck(treasure)
    wsig = {s["name"]: s["count"] for s in w["signals"]}
    tsig = {s["name"]: s["count"] for s in t["signals"]}
    rsig = {s["name"]: s["count"] for s in r["signals"]}
    (ok if wsig.get("creature_tokens", 0) >= 1 else bad)(
        "wide deck makes creature tokens", str(wsig))
    (ok if wsig.get("go_wide_payoff", 0) >= 1 else bad)(
        "wide deck has width payoffs", str(wsig))
    (ok if tsig.get("go_tall_payoff", 0) >= 1 else bad)(
        "tall deck has height payoffs", str(tsig))
    (ok if rsig.get("resource_tokens", 0) >= 1 else bad)(
        "treasure is resource tokens not just 'tokens'", str(rsig))
    # Resource tokens should not be mistaken for the only story — creature token
    # decks need width/height role separation in by_role or signals.
    eq("wide has payoff role", w["by_role"]["payoff"] >= 1, True)
    eq("tall has payoff role", t["by_role"]["payoff"] >= 1, True)


def test_oracle_signals_life_math():
    """Non-keyword plan nodes: lifegain + drain must be countable for synergy math."""
    print("\noracle signals  — life math plan nodes")
    deck = [
        {"name": "Vampire Nighthawk", "type": "Creature",
         "keywords": ["Flying", "Deathtouch", "Lifelink"],
         "text": "Flying, deathtouch, lifelink"},
        {"name": "Ajani's Pridemate", "type": "Creature", "keywords": [],
         "text": "Whenever you gain life, put a +1/+1 counter on Ajani's Pridemate."},
        {"name": "Sanguine Bond", "type": "Enchantment", "keywords": [],
         "text": "Whenever you gain life, target opponent loses that much life."},
        {"name": "Exquisite Blood", "type": "Enchantment", "keywords": [],
         "text": "Whenever an opponent loses life, you gain that much life."},
        {"name": "Island", "type": "Basic Land — Island", "keywords": [], "text": ""},
    ]
    tagged = mechanics.tag_deck(deck)
    names = {s["name"]: s for s in tagged["signals"]}
    eq("lifegain_trigger seen", "lifegain_trigger" in names, True)
    eq("drain seen", "drain" in names, True)
    # Pridemate + Bond trigger on gain; Bond also drains; Exquisite is drain-adjacent
    # via "opponent loses life" in the other direction — patterns must catch Bond's
    # "target opponent loses" and Exquisite's "opponent loses life".
    (ok if names.get("lifegain_trigger", {}).get("count", 0) >= 2 else bad)(
        "lifegain triggers ≥2", str(names.get("lifegain_trigger")))
    (ok if names.get("drain", {}).get("count", 0) >= 1 else bad)(
        "drain ≥1", str(names.get("drain")))
    # Role buckets pick up payoffs from signals, not only keywords.
    (ok if tagged["by_role"]["payoff"] >= 1 else bad)(
        "payoff role from signals", str(tagged["by_role"]))


def test_taxonomy_map_load_and_reason():
    """play taxonomy is a reasoning map — what / how / supports."""
    print("\ntaxonomy map  — reasoning substrate")
    tax = mechanics.load_taxonomy()
    eq("version present", isinstance(tax.get("version"), int) and tax["version"] >= 1, True)
    leaves = mechanics.taxonomy_leaves(tax)
    (ok if len(leaves) >= 200 else bad)("≥200 named mechanics", str(len(leaves)))
    by_name = {m["name"].lower(): m for m in leaves}
    fb = by_name.get("flashback")
    eq("Flashback in map", fb is not None, True)
    if fb:
        eq("Flashback has path", isinstance(fb.get("path"), list) and bool(fb["path"]), True)
        eq("Flashback has how", isinstance(fb.get("how"), str) and len(fb["how"]) > 8, True)
        supports = fb.get("supports") or {}
        eq("Flashback supports.enablers", isinstance(supports.get("enablers"), list) and bool(supports["enablers"]), True)
        eq("Flashback supports.queries", isinstance(supports.get("queries"), list) and bool(supports["queries"]), True)
        # No card names in supports — shapes and queries only.
        blob = json.dumps(supports).lower()
        eq("no invented card names in Flashback supports",
           all(x not in blob for x in ("brainstorm", "entomb", "mulch")), True)
    # Evergreen / deciduous markers survive parse.
    fly = by_name.get("flying")
    eq("Flying evergreen", (fly or {}).get("evergreen"), True)
    # Composite menu lines split into separate leaves.
    eq("Reach is its own leaf", "reach" in by_name, True)


def test_merge_preserves_reviewed_and_adds_map():
    """map merges into families; reviewed plan_role/math_kind win."""
    print("\nmerge taxonomy  — adapter, no replace")
    reviewed = mechanics.load_families()
    merged = mechanics.merge_families(reviewed)
    # Reviewed Delirium placement must not move or lose role.
    del_meta = mechanics.mechanic_meta("Delirium", merged)
    eq("Delirium family preserved", del_meta["family"], "thresholds_graveyard")
    eq("Delirium plan_role preserved", del_meta["plan_role"], "condition")
    eq("Delirium math_kind preserved", del_meta["math_kind"], "threshold_count")
    # oracle_signals / relations stay from reviewed file.
    eq("signals preserved",
       set((merged.get("oracle_signals") or {}).keys()),
       set((reviewed.get("oracle_signals") or {}).keys()))
    eq("relations preserved",
       len(merged.get("relations") or []),
       len(reviewed.get("relations") or []))
    # Taxonomy reasoning attached on lookup without inventing cards.
    meta = mechanics.mechanic_meta("Flashback", merged)
    eq("Flashback still engine", meta["plan_role"], "engine")
    eq("Flashback has how from map", isinstance(meta.get("how"), str) and bool(meta.get("how")), True)
    eq("Flashback has supports from map",
       isinstance(meta.get("supports"), dict) and bool(meta.get("supports")), True)
    # A map leaf missing from the thin reviewed fixture still lands via merge.
    thin = {
        "version": 1,
        "families": {
            "evasion": {
                "plan_role": "modifier",
                "math_kind": "combat_filter",
                "mechanics": ["Flying"],
            },
        },
        "lineage": [],
        "oracle_signals": {"keep": {"family": "evasion", "patterns": ["x"]}},
        "relations": [{"from": "evasion", "to": "evasion", "relation": "composes"}],
    }
    tax = {
        "version": 1,
        "mechanics": [
            {
                "name": "Flying",
                "path": ["Can / Can't Attack / Block", "Evasion"],
                "how": "Can only be blocked by flying or reach",
                "supports": {"enablers": [], "payoffs": ["connect triggers"], "queries": ["keyword:flying"]},
                "evergreen": True,
                "deciduous": False,
            },
            {
                "name": "Skulk",
                "path": ["Can / Can't Attack / Block", "Evasion"],
                "how": "Can't be blocked by higher-power creatures",
                "supports": {"enablers": [], "payoffs": ["saboteur"], "queries": ["keyword:skulk"]},
                "evergreen": False,
                "deciduous": True,
            },
        ],
    }
    m2 = mechanics.merge_families(thin, tax)
    eq("thin signal kept", "keep" in (m2.get("oracle_signals") or {}), True)
    eq("Flying stays reviewed family",
       mechanics.mechanic_meta("Flying", m2)["family"], "evasion")
    sk = mechanics.mechanic_meta("Skulk", m2)
    eq("Skulk placed from map", sk["family"], "evasion")
    eq("Skulk reviewed after merge", sk["reviewed"], True)
    # load_families(merge=True) is the default path for tag_deck consumers.
    default = mechanics.load_families()
    eq("default load merges map",
       "how" in mechanics.mechanic_meta("Flashback", default)
       or bool(mechanics.mechanic_meta("Flashback", default).get("how")), True)


def test_taxonomy_query_emit_and_gate():
    """Taxonomy query emit: right operator, apostrophes kept, heal path exists."""
    print("\ntaxonomy query emit  — build gate")
    mod = _load_taxonomy_builder()
    vocab = {
        "keyword": {
            "flying": "Flying",
            "council's dilemma": "Council's dilemma",
            "doctor's companion": "Doctor's companion",
            "play": "Play",
        },
        "type": {
            "aura": "Aura",
            "battle": "Battle",
            "class": "Class",
            "curse": "Curse",
            "contraption": "Contraption",
            "attraction": "Attraction",
        },
    }
    # Card types → t:, not keyword:
    eq("Aura → t:", mod.default_queries("Aura", vocab), ["t:aura"])
    eq("Battle → t:", mod.default_queries("Battle", vocab), ["t:battle"])
    eq("Class → t:", mod.default_queries("Class", vocab), ["t:class"])
    eq("Curse → t:", mod.default_queries("Curse", vocab), ["t:curse"])
    eq("Contraption → t:", mod.default_queries("Contraption", vocab), ["t:contraption"])
    eq("Attraction → t:", mod.default_queries("Attraction", vocab), ["t:attraction"])
    # Non-keywords → o:
    eq("Devotion → o:", mod.default_queries("Devotion", vocab), ["o:devotion"])
    eq("Descent → o:", mod.default_queries("Descent", vocab), ["o:descent"])
    eq("Day → o:", mod.default_queries("Day", vocab), ["o:day"])
    eq("Draw → o:", mod.default_queries("Draw", vocab), ["o:draw"])
    # Apostrophes preserved (old bug: keyword:councils dilemma).
    eq("Council's Dilemma keeps apostrophe",
       mod.default_queries("Council's Dilemma", vocab),
       ['keyword:"council\'s dilemma"'])
    eq("Doctor's Companion keeps apostrophe",
       mod.default_queries("Doctor's Companion", vocab),
       ['keyword:"doctor\'s companion"'])
    eq("stripped apostrophe is catalog-dead",
       mod.dead_indexed_queries(["keyword:councils dilemma"], vocab),
       ['keyword:"councils dilemma"'])
    eq("heal keyword:play → o:play",
       mod.heal_candidates("keyword:play")[0], "o:play")
    eq("heal t:gold → oracle",
       mod.heal_candidates("t:gold")[0].startswith("o:"), True)

    # Checked-in map matches the three named classes.
    tax = mechanics.load_taxonomy()
    by = {m["name"].lower(): m for m in mechanics.taxonomy_leaves(tax)}
    for name, prefix in (
        ("aura", "t:"), ("attraction", "t:"),
        ("devotion", "o:"), ("draw", "o:"),
    ):
        qs = (by[name].get("supports") or {}).get("queries") or []
        (ok if qs and qs[0].startswith(prefix) else bad)(
            f"{name} starts with {prefix}", str(qs))
    cd = (by["council's dilemma"].get("supports") or {}).get("queries") or []
    eq("Council's Dilemma query keeps apostrophe",
       any("council's" in q for q in cd), True)
    eq("no stripped councils dilemma",
       any("councils dilemma" in q for q in cd), False)
# chunk 1 — Scryfall catalog casing, placed into existing families only.
FIRST_CHUNK_PLACED = [
    "Absorb", "Regenerate", "Hexproof from", "Umbra armor",
    "Afflict", "Banding", "Bushido", "Flanking", "Frenzy", "Rampage", "Decayed",
    "Provoke", "Firebending", "Exert", "Boast",
    "Landwalk", "Desertwalk", "Forestwalk", "Islandwalk", "Mountainwalk",
    "Plainswalk", "Swampwalk", "Nonbasic landwalk", "Legendary landwalk",
    "Basic landcycling", "Landcycling", "Typecycling", "Forestcycling",
    "Islandcycling", "Mountaincycling", "Plainscycling", "Swampcycling",
    "Wizardcycling", "Slivercycling",
    "Awaken", "Compleated", "Undaunted", "Impending", "Fuse",
    "More Than Meets the Eye",
]


# chunk 2 — next 40 unreviewed catalog names (alphabetical order in queue).
SECOND_CHUNK_PLACED = [
    "Abandon", "Activate", "Adamant", "Airbend", "Assemble", "Assimilate", "Attach",
    "Augment", "Aura Swap", "Behold", "Blight", "Cast", "Champion", "Changeling",
    "Choose a background", "Cohort", "Commander ninjutsu", "Companion", "Conjure",
    "Converge", "Convert", "Council's dilemma", "Counter", "Covercast", "Craft",
    "Create", "Crew", "Cumulative upkeep", "Destroy", "Detain", "Devoid",
    "Disappear", "Discard", "Doctor's companion", "Double", "Double agenda",
    "Double team", "Draft from a spellbook", "Earthbend", "Echo",
]


# remainder — final 91 unreviewed catalog names (alphabetical queue tail).
REMAINDER_PLACED = [
    "Eminence", "Enchant", "Enrage", "Exchange", "Exhaust", "Exile", "Exploit",
    "Face a dilemma", "Fading", "Fateful hour", "Fight", "Food", "Freerunning",
    "Friends forever", "Goad", "Grandeur", "Harness", "Heal", "Heist",
    "Hero's Reward", "Heroic", "Hidden agenda", "Hideaway", "Imprint", "Incorporate",
    "Increment", "Infusion", "Inspired", "Intensity", "Join forces", "Kinfall",
    "Kinship", "Landship", "Learn", "Legacy", "Lieutenant", "Living metal",
    "Max speed", "Meld", "Mill", "Open an Attraction", "Opus", "Paradigm",
    "Partner", "Partner with", "Phasing", "Planeswalk", "Play", "Power-up",
    "Prepared", "Radiance", "Read Ahead", "Repartee", "Reveal", "Riot",
    "Role token", "Roll to Visit Your Attractions", "Sacrifice", "Saddle",
    "Secret council", "Seek", "Set in motion", "Shuffle", "Sneak", "Solved",
    "Specialize", "Splice", "Station", "Sunburst", "Survival", "Suspect",
    "Sweep", "Tap", "Teamwork", "Tempting offer", "Time Travel", "Transfigure",
    "Transform", "Treasure", "Tribute", "Triple", "Underdog", "Unleash",
    "Untap", "Valiant", "Vanishing", "Vote", "Waterbend", "Web-slinging",
    "Will of the council", "Will of the Planeswalkers",
]


def test_triage_remainder():
    """remainder catalog names placed; catalog fully reviewed."""
    print("\ntriage remainder")
    eq("remainder size 91", len(REMAINDER_PLACED), 91)
    fam = mechanics.load_families()
    known = {n.lower() for n in mechanics.reviewed_names(fam)}
    for n in REMAINDER_PLACED:
        eq(f"{n} reviewed", n.lower() in known, True)
    # Spot-check placements; structural roles must not drift.
    eq("Delirium still condition",
       mechanics.mechanic_meta("Delirium", fam)["plan_role"], "condition")
    eq("Partner is new_components",
       mechanics.mechanic_meta("Partner", fam)["family"], "new_components")
    eq("Mill is library_manipulation",
       mechanics.mechanic_meta("Mill", fam)["family"], "library_manipulation")
    eq("Treasure is tokens",
       mechanics.mechanic_meta("Treasure", fam)["family"], "tokens")
    eq("Play is timing",
       mechanics.mechanic_meta("Play", fam)["family"], "timing")
    # Live catalog (when present): unreviewed queue cleared.
    cat = mechanics.load_catalog()
    if mechanics.all_catalog_names(cat):
        miss = mechanics.unreviewed(cat, fam)
        still = [n for n in REMAINDER_PLACED if n in miss]
        eq("none of remainder still unreviewed", still, [])
        eq("unreviewed cleared", miss, [])
    else:
        ok("no catalog cache — skipped live unreviewed count")


def test_triage_second_chunk():
    """~40 catalog names placed; no invented names; roles intact."""
    print("\ntriage chunk")
    eq("chunk size 40", len(SECOND_CHUNK_PLACED), 40)
    fam = mechanics.load_families()
    known = {n.lower() for n in mechanics.reviewed_names(fam)}
    for n in SECOND_CHUNK_PLACED:
        eq(f"{n} reviewed", n.lower() in known, True)
    # Spot-check placements; structural roles must not drift.
    eq("Delirium still condition",
       mechanics.mechanic_meta("Delirium", fam)["plan_role"], "condition")
    eq("Adamant is variables",
       mechanics.mechanic_meta("Adamant", fam)["family"], "variables")
    eq("Crew is equipment_aura",
       mechanics.mechanic_meta("Crew", fam)["family"], "equipment_aura")
    eq("Echo is additional_costs",
       mechanics.mechanic_meta("Echo", fam)["family"], "additional_costs")
    eq("Companion is new_components",
       mechanics.mechanic_meta("Companion", fam)["family"], "new_components")
    # Live catalog (when present): unreviewed shrinks by this chunk.
    cat = mechanics.load_catalog()
    if mechanics.all_catalog_names(cat):
        miss = mechanics.unreviewed(cat, fam)
        still = [n for n in SECOND_CHUNK_PLACED if n in miss]
        eq("none of chunk still unreviewed", still, [])
        (ok if len(miss) <= 100 else bad)(
            "unreviewed ≤100 after chunk", str(len(miss)))
    else:
        ok("no catalog cache — skipped live unreviewed count")


def test_triage_first_chunk():
    """~40 catalog names placed; no invented names; roles intact."""
    print("\ntriage chunk")
    eq("chunk size 40", len(FIRST_CHUNK_PLACED), 40)
    fam = mechanics.load_families()
    known = {n.lower() for n in mechanics.reviewed_names(fam)}
    for n in FIRST_CHUNK_PLACED:
        eq(f"{n} reviewed", n.lower() in known, True)
    # Structural roles must not drift while placing names.
    eq("Delirium still condition",
       mechanics.mechanic_meta("Delirium", fam)["plan_role"], "condition")
    eq("Absorb is protection",
       mechanics.mechanic_meta("Absorb", fam)["family"], "protection")
    eq("Landwalk is evasion",
       mechanics.mechanic_meta("Landwalk", fam)["family"], "evasion")
    eq("Awaken is alternate_costs",
       mechanics.mechanic_meta("Awaken", fam)["family"], "alternate_costs")
    # Live catalog (when present): unreviewed shrinks by this chunk.
    cat = mechanics.load_catalog()
    if mechanics.all_catalog_names(cat):
        miss = mechanics.unreviewed(cat, fam)
        still = [n for n in FIRST_CHUNK_PLACED if n in miss]
        eq("none of chunk still unreviewed", still, [])
        (ok if len(miss) <= 140 else bad)(
            "unreviewed ≤140 after chunk", str(len(miss)))
    else:
        ok("no catalog cache — skipped live unreviewed count")


def test_plan_role_coverage_and_query_match():
    """coverage is countable; taxonomy queries classify cards locally."""
    print("\nplan_role coverage + query match")
    cov = mechanics.plan_role_coverage()
    eq("taxonomy leaves > 0", cov["taxonomy"] > 0, True)
    eq("mapped + unmapped = taxonomy",
       cov["mapped"] + cov["unmapped"], cov["taxonomy"])
    (ok if 0 < cov["pct"] <= 100 else bad)("pct in (0,100]", cov["pct"])
    (ok if cov["unmapped"] >= 0 else bad)("unmapped stated", cov["unmapped"])

    by_role = mechanics.queries_by_plan_role()
    eq("engine queries present", len(by_role.get("engine") or []) > 0, True)
    eq("payoff queries present", len(by_role.get("payoff") or []) > 0, True)
    eq("enabler queries present", len(by_role.get("enabler") or []) > 0, True)

    flash = {
        "name": "Test Flash", "keywords": ["Flashback"],
        "oracle_text": "Flashback {2}", "type_line": "Sorcery", "layout": "normal",
    }
    eq("keyword:flashback matches",
       mechanics.card_matches_taxonomy_query(flash, 'keyword:flashback'), True)
    eq("wrong keyword misses",
       mechanics.card_matches_taxonomy_query(flash, 'keyword:flying'), False)
    eq("unknown shape is not a match",
       mechanics.card_matches_taxonomy_query(flash, 'otag:flashback'), False)

    roles = mechanics.classify_card_roles(flash)
    eq("flashback → engine", "engine" in roles, True)

    mdfc = {"name": "Door", "keywords": [], "oracle_text": "",
            "type_line": "Land", "layout": "modal_dfc",
            "card_faces": [{"oracle_text": "add g", "type_line": "Land"}]}
    eq("is:mdfc matches modal_dfc",
       mechanics.card_matches_taxonomy_query(mdfc, "is:mdfc"), True)
    eq("is:split misses modal_dfc",
       mechanics.card_matches_taxonomy_query(mdfc, "is:split"), False)

    line = mechanics.format_role_counts({
        "engine": 3, "payoff": 6, "enabler": 12, "modifier": 0,
    })
    eq("format role counts", line, "3 engines · 6 payoffs · 12 enablers")
    eq("empty roles format blank", mechanics.format_role_counts({}), "")


def main() -> int:
    test_catalog_round_trip()
    test_families_load_and_lookup()
    test_unreviewed_diff()
    test_tag_deck_density_and_roles()
    test_attach_on_persist()
    test_lineage_edges()
    test_pairing_relations()
    test_oracle_signals_life_math()
    test_token_wide_vs_tall()
    test_legacy_saved_deck_loads_without_mechanics()
    test_signal_fixtures_known_tricky()
    test_taxonomy_map_load_and_reason()
    test_merge_preserves_reviewed_and_adds_map()
    test_taxonomy_query_emit_and_gate()
    test_triage_first_chunk()
    test_triage_second_chunk()
    test_triage_remainder()
    test_plan_role_coverage_and_query_match()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
