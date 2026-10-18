"""
test_synergy.py — the synergy graph (spec 2026-08-06-synergy-graph-design).

Offline. No model calls, no network. Run:

    python test_synergy.py
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

from deckwright import synergy

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def fail(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        fail(label, f"got {got!r} want {want!r}")


FIXTURES = Path(__file__).resolve().parents[1] / "data" / "synergy_fixtures.json"


def _card(name):
    return json.loads(FIXTURES.read_text())["cards"][name]


def test_objects_are_derived():
    print("\nsynergy — the object set is closed and derived")
    objs = synergy.load_objects()
    eq("object set is non-empty", len(objs) > 0, True)
    # Every object must name the catalog it came from. A hand-authored
    # object has no provenance and is a design violation.
    unsourced = [n for n, o in objs.items() if not o.get("from_catalog")]
    eq("every object cites a catalog", unsourced, [])
    # Objects the video's example needs.
    for needed in ("artifact", "creature", "treasure"):
        eq(f"object set contains {needed}", needed in objs, True)


def test_profile():
    print("\nsynergy — card profiles")
    hei = synergy.profile(_card("Hei Bai, Spirit of Balance"))
    eq("Hei Bai profile is readable", hei["status"], "ok")
    eq("Hei Bai consumes creature", "creature" in hei["consumes"], True)
    eq("Hei Bai consumes artifact", "artifact" in hei["consumes"], True)
    eq("Hei Bai produces +1/+1 counter",
       "+1/+1 counter" in hei["produces"], True)

    plunderer = synergy.profile(_card("Pitiless Plunderer"))
    eq("Plunderer produces treasure", "treasure" in plunderer["produces"], True)

    # An unreadable card is UNKNOWN, never an empty profile.
    blank = synergy.profile({"name": "Mystery", "oracle_text": None})
    eq("missing oracle text is unknown", blank["status"], synergy.UNKNOWN)
    eq("unknown has no produces", blank["produces"], set())

    # Scryfall returns oracle_text="" for vanilla legendaries
    # (Isamaru, Barktooth Warbeard, …). Present empty string means we looked
    # and the card has no rules text — not "could not look".
    vanilla = synergy.profile({
        "name": "Isamaru, Hound of Konda",
        "type_line": "Legendary Creature — Dog",
        "oracle_text": "",
    })
    eq("vanilla empty oracle is readable", vanilla["status"], "ok")
    eq("vanilla has no signals", vanilla["signals"], [])


def test_unknown_is_not_a_zero():
    print("\nsynergy — an unknown is not a zero")
    blank = synergy.profile({"name": "Mystery", "oracle_text": None})
    sol = synergy.profile(_card("Sol Ring"))
    # Sol Ring is readable and simply has no synergy objects. That is a real
    # zero. The blank card is an unknown. They must not look the same.
    eq("Sol Ring is readable", sol["status"], "ok")
    eq("readable-but-empty differs from unknown",
       sol["status"] == blank["status"], False)
    vanilla = synergy.profile({
        "name": "Isamaru, Hound of Konda", "oracle_text": "",
    })
    eq("vanilla empty string is also a real zero", vanilla["status"], "ok")
    eq("vanilla is not unknown", vanilla["status"] == blank["status"], False)


def test_edges_are_directed_and_object_typed():
    print("\nsynergy — edges")
    plunderer = _card("Pitiless Plunderer")
    hei = _card("Hei Bai, Spirit of Balance")

    # The video's own example, and it CROSSES families:
    #   Plunderer produces treasure (family tokens)
    #   Hei Bai consumes artifact   (family dies_triggers)
    # A family-implied edge model cannot see this.
    forward = synergy.edges(plunderer, hei)
    eq("Plunderer -> Hei Bai has an edge", len(forward) > 0, True)
    eq("the edge is carried by artifact/treasure",
       bool({"artifact", "treasure"} & set(forward)), True)

    # Direction matters. Hei Bai does not make treasures for the Plunderer.
    back = synergy.edges(hei, plunderer)
    eq("Hei Bai -> Plunderer has no treasure edge",
       "treasure" in back, False)


def test_shared_static_keyword_is_not_synergy():
    print("\nsynergy — sharing a keyword is not synergy")
    crow, drake = _card("Storm Crow"), _card("Wind Drake")
    eq("two flyers have no edge", synergy.edges(crow, drake), [])


def test_unknown_forms_no_edge():
    print("\nsynergy — an unknown forms no edge, in either direction")
    blank = {"name": "Mystery", "oracle_text": None}
    hei = _card("Hei Bai, Spirit of Balance")
    eq("unknown -> known", synergy.edges(blank, hei), [])
    eq("known -> unknown", synergy.edges(hei, blank), [])


# The tutorial (YJ8X8bO8jeY) states these aloud. We did not choose them, which
# is the point -- an expectation we wrote ourselves would just agree with our
# own extractor. Same reason Forge, not an internal audit, caught sim.py
# inventing free lands.
HEI_BAI_KEYWORDS = {
    "enters", "attacks", "sacrifice creature", "sacrifice artifact",
    "+1/+1 counters", "leaves the battlefield", "counters",
    "target creature you control",
}


def test_hei_bai_ground_truth():
    print("\nsynergy — Hei Bai, against the tutorial's stated keywords")
    hei = _card("Hei Bai, Spirit of Balance")
    demand = synergy.demand_profile(hei)
    # We do not require our internal names to equal his words. We require the
    # same FACTS to be present: what he eats, what he makes, when he triggers.
    eq("consumes creature", "creature" in demand["consumes"], True)
    eq("consumes artifact", "artifact" in demand["consumes"], True)
    eq("produces +1/+1 counter", "+1/+1 counter" in demand["produces"], True)
    for sig in ("enters", "attacks", "leaves_battlefield"):
        eq(f"signal {sig} detected", sig in demand["signals"], True)


def test_angel_of_indemnity_has_multiple_edges():
    print("\nsynergy — Angel of Indemnity, five stated synergies")
    angel = _card("Angel of Indemnity")
    hei = _card("Hei Bai, Spirit of Balance")
    both = set(synergy.edges(angel, hei)) | set(synergy.edges(hei, angel))
    # The tutorial counts five hooks. We assert the card is MULTIPLY connected
    # rather than asserting exactly five, because our object names and his
    # phrasing do not map one-to-one. Multiplicity is the claim that matters:
    # "not one synergy, but multiple overlapping synergies".
    eq("angel is multiply connected to Hei Bai", len(both) >= 2, True)
    eq("angel is a sacrifice target (creature)",
       "creature" in synergy.edges(angel, hei), True)


def test_generality_across_unlike_commanders():
    print("\nsynergy — unlike commanders (guard against a Hei-Bai-shaped extractor)")
    # If the extractor only lights up on sacrifice decks, Hei Bai alone would
    # never tell us. Each of these must yield a NON-EMPTY, DISTINCT demand.
    expectations = {
        "Krenko, Mob Boss": ("produces", "creature"),      # tokens
        "Omnath, Locus of Rage": ("consumes", "land"),      # landfall
        "Brago, King Eternal": ("produces", "creature"),    # blink
        "Kess, Dissident Mage": ("consumes", "card"),       # spellslinger
    }
    seen = {}
    for name, (side, obj) in expectations.items():
        d = synergy.demand_profile(_card(name))
        eq(f"{name} is readable", d["status"], "ok")
        eq(f"{name} {side} {obj}", obj in d[side], True)
        seen[name] = (frozenset(d["produces"]), frozenset(d["consumes"]))
    # Distinctness: four unlike commanders must not collapse to one profile.
    eq("unlike commanders have distinct demands", len(set(seen.values())), 4)


def test_cohesion():
    print("\nsynergy — deck cohesion")
    hei = _card("Hei Bai, Spirit of Balance")
    # Keyword-readable instants with no object overlap — true orphans.
    # Shock/Negate without keywords are unread; Flash makes these ok.
    # No Angel in the deck: draw cards peer-glue to Angel via `card`.
    bolt = {
        "name": "Lightning Bolt",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "keywords": ["Flash"],
    }
    negate = {
        "name": "Negate",
        "type_line": "Instant",
        "oracle_text": "Counter target noncreature spell.",
        "keywords": ["Flash"],
    }
    deck = [_card("Pitiless Plunderer"), _card("Angel of Indemnity"),
            bolt, negate]
    c = synergy.cohesion(deck, hei)
    eq("counts edges", c["edges"] > 0, True)
    eq("orphans are counted", c["orphans"] >= 2, True)
    eq("hub_dependence is a fraction",
       0.0 <= c["hub_dependence"] <= 1.0, True)


def test_cohesion_reports_unknown_separately():
    print("\nsynergy — unknowns are reported, never counted as orphans")
    hei = _card("Hei Bai, Spirit of Balance")
    deck = [_card("Pitiless Plunderer"), {"name": "Mystery", "oracle_text": None}]
    c = synergy.cohesion(deck, hei)
    eq("unknown counted", c["unknown"], 1)
    # An unknown is not an orphan. We do not know that it fails to connect --
    # we could not look. Counting it as an orphan would be a fallback dressed
    # as a match, the exact thing DERIVATION.md forbids.
    eq("unknown is not an orphan", c["orphans"], 0)


def test_annotate_wires_explain_edges():
    """annotate calls explain_edges for theme/plan; unknown ≠ orphan."""
    print("\nwhy.annotate — wires explain_edges into why text")
    from deckwright import why
    hei = {**_card("Hei Bai, Spirit of Balance"),
           "name": "Hei Bai, Spirit of Balance"}
    connected = {**_card("Angel of Indemnity"),
                 "name": "Angel of Indemnity", "role": "theme"}
    # Keyword-readable, no Hei Bai edges — real none (not unread Shock).
    orphan = {
        "name": "Negate",
        "type_line": "Instant",
        "oracle_text": "Counter target noncreature spell.",
        "keywords": ["Flash"],
        "role": "theme",
    }
    unknown = {"name": "Mystery", "oracle_text": None, "role": "theme"}
    deck = [connected, orphan, unknown]
    names_before = [c["name"] for c in deck]
    why.annotate(deck, {"theme": "id<=WB t:angel"}, commander=hei)
    eq("selection unchanged", [c["name"] for c in deck], names_before)
    edge = why.explain_edges(connected, hei, deck)
    (ok if edge and edge in (connected.get("why") or "") else bad)(
        "connected theme card why includes edge clause",
        f"why={connected.get('why')!r} edge={edge!r}")
    (ok if "angel" in (connected.get("why") or "").lower() else bad)(
        "offer reason still present", connected.get("why"))
    # Orphan: offer reason may exist; edge clause must not invent a connection.
    orphan_edge = why.explain_edges(orphan, hei, deck)
    eq("orphan has no edge clause", orphan_edge, "")
    # unknown must say could-not-read — never look like zero synergy.
    unk_edge = why.explain_edges(unknown, hei, deck)
    (ok if unk_edge and "could not read" in unk_edge.lower() else bad)(
        "unknown edge note says could not read", f"got {unk_edge!r}")
    (ok if "not the same" in unk_edge.lower() else bad)(
        "unknown note does not claim zero synergy", f"got {unk_edge!r}")
    c = synergy.cohesion(deck, hei)
    eq("unknown still counted separately from orphans", c["unknown"], 1)


def test_orphan_gate():
    print("\nsynergy — the orphan gate")
    hei = _card("Hei Bai, Spirit of Balance")
    scaffold = [_card("Pitiless Plunderer")]
    eq("a connected card is eligible",
       synergy.eligible(_card("Angel of Indemnity"), hei, scaffold), True)
    # Shock shares nothing with Hei Bai. Storm Crow would be eligible — it is
    # a creature, and Hei Bai eats creatures.
    eq("an orphan is not eligible",
       synergy.eligible(_card("Shock"), hei, scaffold), False)
    # An unknown must neither qualify nor disqualify. We let it through --
    # refusing a card we could not read would silently shrink the pool.
    eq("an unknown is not blocked",
       synergy.eligible({"name": "M", "oracle_text": None}, hei, scaffold), True)


def test_expert_hei_bai_deck_is_coherent():
    print("\nsynergy — expert Hei Bai 99 (validation, never training)")
    # Bracket-2 list from the tutorial. If OUR rules call this sparse, the
    # rules are wrong — do not move the bar.
    payload = json.loads(FIXTURES.read_text())["moxfield_hei_bai_bracket2"]
    by_name = {c["name"]: c for c in payload["cards"]}
    deck = []
    for name, qty in payload["quantities"].items():
        card = by_name[name]
        for _ in range(qty):
            deck.append(card)
    hei = _card("Hei Bai, Spirit of Balance")
    c = synergy.cohesion(deck, hei)
    eq("expert deck has edges", c["edges"] > 0, True)
    eq("fewer orphans than half the deck",
       c["orphans"] < len(deck) / 2, True)
    eq("no unknowns in a fully-read list", c["unknown"], 0)


def test_no_weighted_sum_exists():
    print("\nsynergy — the design forbids a coefficient")
    # edge_count returns a COUNT for tiebreaking, never a weighted score.
    hei = _card("Hei Bai, Spirit of Balance")
    n = synergy.edge_count(_card("Angel of Indemnity"), hei, [])
    eq("edge_count is an integer", isinstance(n, int), True)
    eq("edge_count is non-negative", n >= 0, True)


def test_blink_patterns_match_real_oracle():
    print("\nsynergy — blink patterns match real oracle text")
    # The first draft required "then return it/them". Real cards say
    # "return that card" (Cloudshift, Felidar Guardian, Conjurer's Closet)
    # and often delay the return to the end step. A pattern that misses those
    # reports blink as absent while the cards sit in print.
    for name in ("Cloudshift", "Felidar Guardian", "Conjurer's Closet",
                 "Teleportation Circle", "Ephemerate", "Airbender Ascension"):
        prof = synergy.profile(_card(name))
        eq(f"{name} matches blink", "blink" in prof["signals"], True)
        eq(f"{name} produces exile", "exile" in prof["produces"], True)


def test_hei_bai_demands_leave_causes():
    print("\nsynergy — Hei Bai consumes leave-causes, not the leave phrase")
    # He triggers WHEN he leaves. Sac outlets and blink CAUSE leaving.
    # The phrase "leaves the battlefield" is on him, not on the enablers.
    hei_card = _card("Hei Bai, Spirit of Balance")
    hei = synergy.demand_profile(hei_card)
    eq("leaves_battlefield signal present",
       "leaves_battlefield" in hei["signals"], True)
    eq("consumes exile (blink)", "exile" in hei["consumes"], True)
    eq("consumes sacrifice (outlet)", "sacrifice" in hei["consumes"], True)

    blink = _card("Cloudshift")
    sac = _card("Deadly Dispute")
    eq("blink -> Hei Bai carries exile",
       "exile" in synergy.edges(blink, hei_card), True)
    eq("sac outlet -> Hei Bai carries sacrifice",
       "sacrifice" in synergy.edges(sac, hei_card), True)
    # Blink must beat a plain creature on edge count: same sac-fodder creature
    # edge, plus the exile edge the leave trigger actually wants.
    crow_n = synergy.edge_count(_card("Storm Crow"), hei_card, [])
    blink_n = synergy.edge_count(blink, hei_card, [])
    eq("blink outranks a plain creature", blink_n > crow_n, True)


def test_leave_enabler_queries():
    print("\nsynergy — leave-enabler recall queries")
    # Same failure mode as the piece exists and the graph can see it,
    # but the role ladder never searched for it. Ranking cannot fix absence.
    hei = _card("Hei Bai, Spirit of Balance")
    qs = synergy.leave_enabler_queries(hei)
    eq("Hei Bai asks for blink search", any("blink" in q for q in qs), True)
    # A commander with no leave trigger must not pull blink into every deck.
    crow = _card("Storm Crow")
    eq("Storm Crow asks for no leave enablers",
       synergy.leave_enabler_queries(crow), [])


def test_synergy_neighbors():
    print("\nsynergy — one-hop relation neighbors for recall")
    # demand signals → families → one-hop relations → queries.
    # Recall into the card pool, never into plan commitments.
    hei = _card("Hei Bai, Spirit of Balance")
    neighbors = synergy.synergy_neighbors(hei)
    fams = {n["family"] for n in neighbors}
    eq("Hei Bai neighbors include tokens (sac fodder)", "tokens" in fams, True)
    eq("Hei Bai neighbors include life_math (relation hop)",
       "life_math" in fams, True)
    # Every neighbor carries at least one Scryfall query — taxonomy supports
    # and/or the family payoff pattern. An empty-query neighbor is a dead hop.
    for n in neighbors:
        eq(f"{n['family']} has recall queries",
           isinstance(n.get("queries"), list) and len(n["queries"]) > 0, True)
    # tokens feeds dies_triggers — an enabler the commander needs. Floor it.
    tok = next(n for n in neighbors if n["family"] == "tokens")
    eq("tokens neighbor is an enabler floor", tok.get("enabler_floor"), True)
    # dies_triggers→life_math is a payoff expansion, not an enabler Hei Bai
    # needs. Recall yes; floor no — otherwise we force lifegain into the 99.
    life = next(n for n in neighbors if n["family"] == "life_math")
    eq("life_math neighbor is not an enabler floor",
       life.get("enabler_floor"), False)

    crow = _card("Storm Crow")
    eq("Storm Crow has no synergy neighbors",
       synergy.synergy_neighbors(crow), [])
    blank = {"name": "Mystery", "oracle_text": None}
    eq("unknown oracle yields empty neighbors (not a fake zero)",
       synergy.synergy_neighbors(blank), [])


def test_synergy_recall_queries():
    print("\nsynergy — flattened recall queries")
    hei = _card("Hei Bai, Spirit of Balance")
    qs = synergy.synergy_recall_queries(hei)
    joined = " ".join(qs)
    eq("Hei Bai recall includes blink", "blink" in joined, True)
    # Sac fodder: tokens family payoff and/or taxonomy token queries.
    eq("Hei Bai recall includes sac-fodder/token related query",
       ("token" in joined.lower()
        or "create" in joined.lower()
        or "creature you control enters" in joined.lower()), True)
    # leave_enabler_queries must still be reachable through this path.
    for bq in synergy.leave_enabler_queries(hei):
        eq(f"leave enabler {bq!r} is in recall", bq in qs, True)
    # Deduped.
    eq("recall queries are unique", len(qs), len(set(qs)))

    crow = _card("Storm Crow")
    eq("Storm Crow recall is empty", synergy.synergy_recall_queries(crow), [])
    blank = {"name": "Mystery", "oracle_text": None}
    eq("unknown oracle yields empty recall",
       synergy.synergy_recall_queries(blank), [])


def test_prefer_glued_steers_without_dropping():
    """edged cards before orphans; orphans stay; STEER off = identity."""
    print("\nsynergy — prefer_glued soft steer")
    hei = {**_card("Hei Bai, Spirit of Balance"),
           "name": "Hei Bai, Spirit of Balance"}
    angel = {**_card("Angel of Indemnity"), "name": "Angel of Indemnity"}
    shock = {**_card("Shock"), "name": "Shock"}
    blink = {**_card("Cloudshift"), "name": "Cloudshift"}
    # Orphan first in input — steer must promote glued cards, not drop Shock.
    pool = [shock, angel, blink]
    steered = synergy.prefer_glued(pool, hei, [])
    eq("same length — never drops", len(steered), len(pool))
    eq("Shock still present", any(c["name"] == "Shock" for c in steered), True)
    eq("first pick is not the orphan", steered[0]["name"] != "Shock", True)
    # Blink/Angel both edge Hei Bai; Shock does not.
    eq("orphan sorts last", steered[-1]["name"], "Shock")

    prev = synergy.STEER
    try:
        synergy.STEER = False
        off = synergy.prefer_glued(pool, hei, [])
        eq("STEER off preserves input order", [c["name"] for c in off],
           [c["name"] for c in pool])
    finally:
        synergy.STEER = prev

    # On vs off: measurable name-order difference (Jaccard on prefixes).
    synergy.STEER = True
    on_names = [c["name"] for c in synergy.prefer_glued(pool, hei, [])]
    synergy.STEER = False
    off_names = [c["name"] for c in synergy.prefer_glued(pool, hei, [])]
    synergy.STEER = True
    eq("on vs off order differs", on_names != off_names, True)
    # Scaffold: a glued card already in the deck can promote a neighbor.
    with_scaffold = synergy.prefer_glued(
        [shock, angel], hei, [blink])
    eq("scaffold does not drop orphan", len(with_scaffold), 2)


def test_prefer_glued_cheaper_among_equal_edges():
    """equal edge votes break on lower usd; priced before missing."""
    print("\nsynergy — prefer_glued price tertiary")
    hei = {**_card("Hei Bai, Spirit of Balance"),
           "name": "Hei Bai, Spirit of Balance"}
    cheap = {**_card("Shock"), "name": "Cheap Bolt", "usd": "0.25"}
    dear = {**_card("Shock"), "name": "Dear Bolt", "usd": "25.00"}
    steered = synergy.prefer_glued([dear, cheap], hei, [])
    eq("cheaper first among equal 0-edge votes", steered[0]["name"], "Cheap Bolt")
    eq("still same length", len(steered), 2)

    no_price = {**_card("Shock"), "name": "No Price"}
    priced = {**_card("Shock"), "name": "Priced", "usd": "5.00"}
    steered2 = synergy.prefer_glued([no_price, priced], hei, [])
    eq("priced before missing usd", steered2[0]["name"], "Priced")

    angel = {**_card("Angel of Indemnity"), "name": "Angel of Indemnity", "usd": "50.00"}
    shock = {**_card("Shock"), "name": "Shock", "usd": "0.10"}
    steered3 = synergy.prefer_glued([shock, angel], hei, [])
    eq("edge vote still primary over price", steered3[0]["name"], "Angel of Indemnity")


def _full_throttle() -> dict:
    """Full Throttle-shaped oracle — two additional combat phases."""
    return {
        "name": "Full Throttle",
        "type_line": "Sorcery",
        "oracle_text": (
            "After this main phase, there are two additional combat phases "
            "followed by an additional main phase. At the beginning of each "
            "of those combat phases, untap all creatures that attacked this turn."
        ),
    }


def _unstoppable_plan() -> dict:
    return {
        "name": "Unstoppable Plan",
        "type_line": "Enchantment",
        "oracle_text": (
            "At the beginning of your end step, untap all nonland permanents "
            "you control."
        ),
    }


def test_timing_multiplier_exempt_from_steer_edge_key():
    """timing cards are unrepresentable, not orphans under STEER."""
    print("\nsynergy — timing_multiplier STEER carve-out")
    hei = {**_card("Hei Bai, Spirit of Balance"),
           "name": "Hei Bai, Spirit of Balance"}
    throttle = _full_throttle()
    plan = _unstoppable_plan()
    extra_turn = {
        "name": "Time Walkish",
        "type_line": "Sorcery",
        "oracle_text": "Take an extra turn after this one.",
    }
    shock = {**_card("Shock"), "name": "Shock"}
    angel = {**_card("Angel of Indemnity"), "name": "Angel of Indemnity"}

    eq("Full Throttle is timing_multiplier",
       synergy.timing_multiplier(throttle), True)
    eq("Unstoppable Plan is timing_multiplier",
       synergy.timing_multiplier(plan), True)
    eq("extra turn is timing_multiplier",
       synergy.timing_multiplier(extra_turn), True)
    eq("Shock is not timing_multiplier",
       synergy.timing_multiplier(shock), False)
    eq("Full Throttle has 0 material edges",
       synergy.edge_count(throttle, hei, []), 0)
    eq("edge_steer_vote is None (no vote), not 0",
       synergy.edge_steer_vote(throttle, hei, []), None)
    eq("orphan still votes 0",
       synergy.edge_steer_vote(shock, hei, []), 0)
    eq("edged card keeps positive vote",
       synergy.edge_steer_vote(angel, hei, []) > 0, True)

    # Orphan first, then timing — STEER must not leave timing last as if
    # both were equal zeros. Neutral band puts timing above true orphans.
    pool = [shock, throttle]
    steered = synergy.prefer_glued(pool, hei, [])
    eq("timing card not after 0-edge vanilla",
       steered[0]["name"], "Full Throttle")
    eq("orphan still present", steered[-1]["name"], "Shock")

    # Edged still outranks timing on prefer_glued (material edges win the
    # band); the carve-out is exemption from the orphan zero, not a weight.
    with_edged = synergy.prefer_glued([throttle, angel, shock], hei, [])
    eq("edged still first", with_edged[0]["name"], "Angel of Indemnity")
    names = [c["name"] for c in with_edged]
    eq("timing before orphan when both unscored on objects",
       names.index("Full Throttle") < names.index("Shock"), True)

    # No combat/turn objects invented — profile stays empty on those axes.
    prof = synergy.profile(throttle)
    eq("timing card readable", prof["status"], "ok")
    eq("no invented combat object", "combat" in prof["produces"], False)
    eq("no invented turn object", "turn" in prof["produces"], False)


PRECON_LIVING_ENERGY = (
    Path(__file__).resolve().parents[1] / "testdata" / "precons" / "11035094.json"
)


def _living_energy_card(name: str) -> dict:
    """Real oracle from the Living Energy precon."""
    payload = json.loads(PRECON_LIVING_ENERGY.read_text())
    if name == payload.get("commander") or (
            (payload.get("commander_card") or {}).get("name") == name):
        card = dict(payload["commander_card"])
        # profile() accepts type_line or type; precon uses type.
        return card
    for card in payload.get("deck") or []:
        if card.get("name") == name:
            return dict(card)
    raise KeyError(name)


def test_energy_object_and_edges():
    """energy is a produce/consume object; Living Energy is visible."""
    print("\nsynergy — energy object (Living Energy precon)")
    synergy.clear_cache()

    objs = synergy.load_objects()
    eq("object set contains energy", "energy" in objs, True)
    eq("energy is a resource", (objs.get("energy") or {}).get("kind"),
       "resource")

    saheeli = _living_energy_card("Saheeli, Radiant Creator")
    solar = _living_energy_card("Solar Transformer")
    hub = _living_energy_card("Aether Hub")
    crow = _card("Storm Crow")

    she = synergy.profile(saheeli)
    eq("Saheeli is readable", she["status"], "ok")
    eq("Saheeli produces energy", "energy" in she["produces"], True)
    eq("Saheeli consumes energy", "energy" in she["consumes"], True)

    for name, card in (("Solar Transformer", solar), ("Aether Hub", hub)):
        prof = synergy.profile(card)
        eq(f"{name} produces energy", "energy" in prof["produces"], True)
        eq(f"{name} -> Saheeli carries energy",
           "energy" in synergy.edges(card, saheeli), True)

    crow_p = synergy.profile(crow)
    eq("Storm Crow produces no energy", "energy" in crow_p["produces"], False)
    eq("Storm Crow consumes no energy", "energy" in crow_p["consumes"], False)
    eq("Storm Crow -> Saheeli has no energy edge",
       "energy" in synergy.edges(crow, saheeli), False)
    eq("Saheeli -> Storm Crow has no energy edge",
       "energy" in synergy.edges(saheeli, crow), False)


def test_resource_tokens_are_honest():
    """Resource makers produce their subtype, not a blanket treasure."""
    print("\nsynergy — honest resource-token produces")
    synergy.clear_cache()

    food_maker = {
        "name": "Gilded Goose",
        "type_line": "Creature — Bird",
        "oracle_text": (
            "Flying\n"
            "When Gilded Goose enters, create a Food token.\n"
            "{1}{G}, {T}: Create a Food token.\n"
            "{T}, Sacrifice a Food: Add one mana of any color."
        ),
    }
    food = synergy.profile(food_maker)
    eq("Food maker produces food", "food" in food["produces"], True)
    eq("Food maker produces artifact", "artifact" in food["produces"], True)
    eq("Food maker does not produce treasure",
       "treasure" in food["produces"], False)

    plunderer = synergy.profile(_card("Pitiless Plunderer"))
    eq("Treasure maker still produces treasure",
       "treasure" in plunderer["produces"], True)
    eq("Treasure maker produces artifact",
       "artifact" in plunderer["produces"], True)


def test_player_resource_pack():
    """poison/rad/experience/speed/monarch/initiative/blessing/ring."""
    print("\nsynergy — player-resource pack")
    synergy.clear_cache()

    objs = synergy.load_objects()
    for name in (
        "poison", "rad", "experience", "speed", "monarch", "initiative",
        "city's blessing", "the ring",
    ):
        eq(f"object set contains {name}", name in objs, True)
        eq(f"{name} is a resource", (objs.get(name) or {}).get("kind"),
           "resource")
        eq(f"{name} is rules-structural",
           (objs.get(name) or {}).get("from_catalog"), "rules-structural")

    crow = _card("Storm Crow")

    # Poison: toxic produces; Corrupted cares; bare proliferate does not consume.
    sentry = synergy.profile(_card("Annex Sentry"))
    apostle = synergy.profile(_card("Apostle of Invasion"))
    vraska = synergy.profile(_card("Vraska, Betrayal's Sting"))
    eq("Annex Sentry produces poison", "poison" in sentry["produces"], True)
    eq("Apostle consumes poison", "poison" in apostle["consumes"], True)
    eq("Annex Sentry -> Apostle carries poison",
       "poison" in synergy.edges(_card("Annex Sentry"),
                                 _card("Apostle of Invasion")), True)
    eq("Vraska proliferate does not consume poison",
       "poison" in vraska["consumes"], False)
    eq("Vraska ultimate produces poison", "poison" in vraska["produces"], True)

    # Rad: get produces; lose-all / has-rad consume.
    drink = synergy.profile(_card("Contaminated Drink"))
    medkit = synergy.profile(_card("Survivor's Med Kit"))
    nightkin = synergy.profile(_card("Nightkin Ambusher"))
    eq("Contaminated Drink produces rad", "rad" in drink["produces"], True)
    eq("Med Kit consumes rad", "rad" in medkit["consumes"], True)
    eq("Drink -> Med Kit carries rad",
       "rad" in synergy.edges(_card("Contaminated Drink"),
                              _card("Survivor's Med Kit")), True)
    eq("Nightkin produces and consumes rad",
       "rad" in nightkin["produces"] and "rad" in nightkin["consumes"], True)

    # Experience: get / counters you have.
    ezuri = synergy.profile(_card("Ezuri, Claw of Progress"))
    daxos = synergy.profile(_card("Daxos the Returned"))
    eq("Ezuri produces experience", "experience" in ezuri["produces"], True)
    eq("Ezuri consumes experience", "experience" in ezuri["consumes"], True)
    eq("Daxos -> Ezuri carries experience",
       "experience" in synergy.edges(_card("Daxos the Returned"),
                                     _card("Ezuri, Claw of Progress")), True)

    # Speed: Start your engines produces; max-speed dash / your speed / have max
    # speed consume. Reminder 'Max speed is 4' alone must not consume.
    raceway = synergy.profile(_card("Amonkhet Raceway"))
    point = synergy.profile(_card("Point the Way"))
    hazoret = synergy.profile(_card("Hazoret, Godseeker"))
    eq("Raceway produces speed", "speed" in raceway["produces"], True)
    eq("Raceway consumes speed via Max speed —",
       "speed" in raceway["consumes"], True)
    eq("Point the Way consumes via your speed",
       "speed" in point["consumes"], True)
    eq("Hazoret consumes via have max speed",
       "speed" in hazoret["consumes"], True)
    start_only = {
        "name": "Start Only",
        "type_line": "Enchantment",
        "oracle_text": (
            "Start your engines! (If you have no speed, it starts at 1. "
            "It increases once on each of your turns when an opponent loses "
            "life. Max speed is 4.)"
        ),
    }
    so = synergy.profile(start_only)
    eq("reminder Max speed is 4 does not consume",
       "speed" in so["consumes"], False)
    eq("Start Only still produces speed", "speed" in so["produces"], True)

    # Monarch / initiative: become/take vs if-you-are.
    aragorn = synergy.profile(_card("Aragorn, King of Gondor"))
    admiral = synergy.profile(_card("Azure Fleet Admiral"))
    eq("Aragorn produces monarch", "monarch" in aragorn["produces"], True)
    eq("Aragorn consumes monarch", "monarch" in aragorn["consumes"], True)
    eq("Admiral produces monarch", "monarch" in admiral["produces"], True)
    eq("Admiral does not consume via 'the monarch controls'",
       "monarch" in admiral["consumes"], False)
    eq("Admiral -> Aragorn carries monarch",
       "monarch" in synergy.edges(_card("Azure Fleet Admiral"),
                                  _card("Aragorn, King of Gondor")), True)

    fey = synergy.profile(_card("Feywild Caretaker"))
    imoen = synergy.profile(_card("Imoen, Mystic Trickster"))
    eq("Feywild produces initiative", "initiative" in fey["produces"], True)
    eq("Feywild consumes initiative", "initiative" in fey["consumes"], True)
    eq("Imoen consumes initiative only",
       ("initiative" in imoen["consumes"]
        and "initiative" not in imoen["produces"]), True)
    eq("Feywild -> Imoen carries initiative",
       "initiative" in synergy.edges(_card("Feywild Caretaker"),
                                     _card("Imoen, Mystic Trickster")), True)

    # City's blessing: Ascend get / have the blessing.
    arch = synergy.profile(_card("Arch of Orazca"))
    eq("Arch produces city's blessing",
       "city's blessing" in arch["produces"], True)
    eq("Arch consumes city's blessing",
       "city's blessing" in arch["consumes"], True)

    # The Ring: effect tempts produces; whenever-tempts / Ring-bearer care consume.
    escape = synergy.profile(_card("Birthday Escape"))
    call = synergy.profile(_card("Call of the Ring"))
    company = synergy.profile(_card("Aragorn, Company Leader"))
    eq("Birthday Escape produces the ring",
       "the ring" in escape["produces"], True)
    eq("Birthday Escape does not consume the ring",
       "the ring" in escape["consumes"], False)
    eq("Call produces and consumes the ring",
       ("the ring" in call["produces"] and "the ring" in call["consumes"]),
       True)
    eq("Company Leader consumes the ring only",
       ("the ring" in company["consumes"]
        and "the ring" not in company["produces"]), True)
    eq("Escape -> Company Leader carries the ring",
       "the ring" in synergy.edges(_card("Birthday Escape"),
                                   _card("Aragorn, Company Leader")), True)

    crow_p = synergy.profile(crow)
    for obj in ("poison", "rad", "experience", "speed", "monarch",
                "initiative", "city's blessing", "the ring"):
        eq(f"Storm Crow produces no {obj}", obj in crow_p["produces"], False)
        eq(f"Storm Crow consumes no {obj}", obj in crow_p["consumes"], False)


def test_mana_land_signals():
    """land/mana reading — exemplar fires, near-miss does not."""
    print("\nsynergy — mana/land oracle_signals")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    # enters_tapped — Path of Ancestry / guildgate shape.
    path = card(
        "Path of Ancestry",
        "Land",
        "This land enters tapped.\n"
        "{T}: Add one mana of any color in your commander's color identity. "
        "When that mana is spent to cast a creature spell that shares a "
        "creature type with your commander, scry 1.",
    )
    island = card("Island", "Basic Land — Island", "")
    etb_counter = card(
        "Walking Ballista",
        "Artifact Creature — Construct",
        "Walking Ballista enters with X +1/+1 counters on it.\n"
        "{2}: Put a +1/+1 counter on Walking Ballista.",
    )
    path_p = synergy.profile(path)
    eq("Path of Ancestry fires enters_tapped",
       "enters_tapped" in path_p["signals"], True)
    eq("basic Island does not fire enters_tapped",
       "enters_tapped" in synergy.profile(island)["signals"], False)
    eq("enters-with-counters is not enters_tapped",
       "enters_tapped" in synergy.profile(etb_counter)["signals"], False)

    # Conditional ETB tapped still enters tapped (fast/check/battle lands).
    fast = card(
        "Botanical Sanctum",
        "Land",
        "This land enters tapped unless you control two or more basic lands.\n"
        "{T}: Add {G} or {U}.",
    )
    eq("fast land fires enters_tapped",
       "enters_tapped" in synergy.profile(fast)["signals"], True)

    # pay_life_enter — shock lands. Activation-cost pay-life must not match.
    crypt = card(
        "Blood Crypt",
        "Land — Swamp Mountain",
        "({T}: Add {B} or {R}.)\n"
        "As this land enters, you may pay 2 life. If you don't, it enters tapped.",
    )
    pay_activate = card(
        "Shadowcloak Vampire",
        "Creature — Vampire",
        "Pay 2 life: This creature gains flying until end of turn.",
    )
    crypt_p = synergy.profile(crypt)
    eq("Blood Crypt fires pay_life_enter",
       "pay_life_enter" in crypt_p["signals"], True)
    eq("Blood Crypt consumes life", "life" in crypt_p["consumes"], True)
    eq("Blood Crypt also fires enters_tapped (if you don't)",
       "enters_tapped" in crypt_p["signals"], True)
    eq("Pay-2-life activation is not pay_life_enter",
       "pay_life_enter" in synergy.profile(pay_activate)["signals"], False)

    # pain_mana — Karplusan Forest / talisman shape. "to any target" must not.
    forest = card(
        "Karplusan Forest",
        "Land",
        "{T}: Add {C}.\n"
        "{T}: Add {R} or {G}. This land deals 1 damage to you.",
    )
    bolt = card(
        "Lightning Bolt",
        "Instant",
        "Lightning Bolt deals 3 damage to any target.",
    )
    eq("Karplusan Forest fires pain_mana",
       "pain_mana" in synergy.profile(forest)["signals"], True)
    eq("Lightning Bolt does not fire pain_mana",
       "pain_mana" in synergy.profile(bolt)["signals"], False)

    # any_color_mana vs commander_identity_mana — mutual near-misses.
    tower = card(
        "Command Tower",
        "Land",
        "{T}: Add one mana of any color in your commander's color identity.",
    )
    spire = card(
        "Spire of Industry",
        "Land",
        "{T}: Add {C}.\n"
        "{T}, Pay 1 life: Add one mana of any color. "
        "Activate only if you control an artifact.",
    )
    tower_p = synergy.profile(tower)
    spire_p = synergy.profile(spire)
    eq("Command Tower fires commander_identity_mana",
       "commander_identity_mana" in tower_p["signals"], True)
    eq("Command Tower does not fire any_color_mana",
       "any_color_mana" in tower_p["signals"], False)
    eq("Spire fires any_color_mana",
       "any_color_mana" in spire_p["signals"], True)
    eq("Spire does not fire commander_identity_mana",
       "commander_identity_mana" in spire_p["signals"], False)
    dual = card(
        "Temple of Epiphany",
        "Land",
        "This land enters tapped.\n"
        "When this land enters, scry 1.\n"
        "{T}: Add {U} or {R}.",
    )
    eq("dual tap-for-two is not any_color_mana",
       "any_color_mana" in synergy.profile(dual)["signals"], False)

    # land_sacrifice_value — Horizon Canopy draw / Evolving Wilds fetch.
    # "Sacrifice a land" on a spell is the near-miss; pay-life draw is not sac.
    canopy = card(
        "Horizon Canopy",
        "Land",
        "{T}, Pay 1 life: Add {G} or {W}.\n"
        "{1}, {T}, Sacrifice this land: Draw a card.",
    )
    fetch = card(
        "Evolving Wilds",
        "Land",
        "{T}, Sacrifice this land: Search your library for a basic land "
        "card, put it onto the battlefield tapped, then shuffle.",
    )
    sac_spell = card(
        "Harrow",
        "Instant",
        "As an additional cost to cast this spell, sacrifice a land.\n"
        "Search your library for up to two basic land cards, put them "
        "onto the battlefield, then shuffle.",
    )
    war_room = card(
        "War Room",
        "Land",
        "{T}: Add {C}.\n"
        "{3}, {T}, Pay life equal to the number of colors in your "
        "commanders' color identity: Draw a card.",
    )
    canopy_p = synergy.profile(canopy)
    eq("Horizon Canopy fires land_sacrifice_value",
       "land_sacrifice_value" in canopy_p["signals"], True)
    eq("Horizon Canopy consumes land", "land" in canopy_p["consumes"], True)
    eq("Horizon Canopy produces card", "card" in canopy_p["produces"], True)
    eq("Evolving Wilds fetch fires land_sacrifice_value",
       "land_sacrifice_value" in synergy.profile(fetch)["signals"], True)
    eq("Harrow sacrifice-a-land cost does not",
       "land_sacrifice_value" in synergy.profile(sac_spell)["signals"], False)
    eq("War Room pay-life draw does not fire land_sacrifice_value",
       "land_sacrifice_value" in synergy.profile(war_room)["signals"], False)


def test_commander_and_aura_signals():
    """batch 2: commander-matters + enchant creature + hand size."""
    print("\nsynergy — commander-matters / aura / hand size")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    # Deadly Rollick is the named free-cast exemplar in print.
    rollick = card(
        "Deadly Rollick",
        "Instant",
        "If you control a commander, you may cast this spell without "
        "paying its mana cost.\n"
        "Exile target creature.",
    )
    can_be = card(
        "Can Be Commander Shape",
        "Legendary Artifact",
        "This card can be your commander.",
    )
    eq("Deadly Rollick fires commander_free_cast",
       "commander_free_cast" in synergy.profile(rollick)["signals"], True)
    eq("can-be-your-commander does not fire commander_free_cast",
       "commander_free_cast" in synergy.profile(can_be)["signals"], False)

    tower = card(
        "Command Tower",
        "Land",
        "{T}: Add one mana of any color in your commander's color identity.",
    )
    # commander_matters: cast-from-command-zone / command-zone care — not
    # color identity alone, and not "can be your commander".
    cares = card(
        "Commander Matters Shape",
        "Enchantment",
        "Creatures you control get +1/+1 for each time you've cast your "
        "commander from the command zone this game.",
    )
    eq("cast-from-command-zone fires commander_matters",
       "commander_matters" in synergy.profile(cares)["signals"], True)
    eq("Command Tower color-identity is not commander_matters",
       "commander_matters" in synergy.profile(tower)["signals"], False)
    eq("can-be-your-commander is not commander_matters",
       "commander_matters" in synergy.profile(can_be)["signals"], False)

    aura = card(
        "Pacifism",
        "Enchantment — Aura",
        "Enchant creature\nEnchanted creature can't attack or block.",
    )
    enchant_land = card(
        "Wild Growth",
        "Enchantment — Aura",
        "Enchant land\nWhenever enchanted land is tapped for mana, "
        "its controller adds an additional {G}.",
    )
    eq("Pacifism fires enchant_creature",
       "enchant_creature" in synergy.profile(aura)["signals"], True)
    eq("Pacifism consumes creature",
       "creature" in synergy.profile(aura)["consumes"], True)
    eq("Enchant land does not fire enchant_creature",
       "enchant_creature" in synergy.profile(enchant_land)["signals"], False)
    # "enchanted creature" body text alone is not the Enchant keyword line.
    body_only = card(
        "Body Only",
        "Enchantment",
        "Enchanted creature gets +1/+1.",
    )
    eq("enchanted-creature body text is not enchant_creature",
       "enchant_creature" in synergy.profile(body_only)["signals"], False)

    tower_hand = card(
        "Reliquary Tower",
        "Land",
        "You have no maximum hand size.\n{T}: Add {C}.",
    )
    hand_other = card(
        "Thought Vessel",
        "Artifact",
        "You have no maximum hand size.\n{T}: Add {C}.",
    )
    eq("Reliquary Tower fires no_max_hand_size",
       "no_max_hand_size" in synergy.profile(tower_hand)["signals"], True)
    eq("Thought Vessel also fires no_max_hand_size",
       "no_max_hand_size" in synergy.profile(hand_other)["signals"], True)
    eq("Storm Crow does not fire no_max_hand_size",
       "no_max_hand_size" in synergy.profile(_card("Storm Crow"))["signals"],
       False)


def test_unread_gap_signals():
    """Phase 3 signals for researched unread gaps.

    Exemplar fires; near-miss does not. Keywords stay on Scryfall catalogs —
    these are the non-catalog lines Phase 2 recommended for oracle_signals.
    """
    print("\nsynergy — unread-gap signals")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    # card_draw — Mind Stone vs Path of Ancestry (scry, no draw).
    mind = card(
        "Mind Stone",
        "Artifact",
        "{T}: Add {C}.\n"
        "{2}, {T}, Sacrifice this artifact: Draw a card.",
    )
    path = card(
        "Path of Ancestry",
        "Land",
        "This land enters tapped.\n"
        "{T}: Add one mana of any color in your commander's color identity. "
        "When that mana is spent to cast a creature spell that shares a "
        "creature type with your commander, scry 1.",
    )
    eq("Mind Stone fires card_draw",
       "card_draw" in synergy.profile(mind)["signals"], True)
    eq("Mind Stone produces card",
       "card" in synergy.profile(mind)["produces"], True)
    eq("Path of Ancestry scry is not card_draw",
       "card_draw" in synergy.profile(path)["signals"], False)

    # tap_add_colored vs dual vs any-color — mutual near-misses.
    hinter = card(
        "Hinterland Harbor",
        "Land",
        "This land enters tapped unless you control a Forest or an Island.\n"
        "{T}: Add {G} or {U}.",
    )
    # Fixed single color — Boseiju-class / basic-shaped.
    boseiju = card(
        "Boseiju, Who Endures",
        "Legendary Land",
        "{T}: Add {G}.\n"
        "Channel — {1}{G}, Discard this card: Destroy target "
        "artifact, enchantment, or nonbasic land an opponent controls.",
    )
    tower = card(
        "Command Tower",
        "Land",
        "{T}: Add one mana of any color in your commander's color identity.",
    )
    spire = card(
        "Spire of Industry",
        "Land",
        "{T}: Add {C}.\n"
        "{T}, Pay 1 life: Add one mana of any color. Activate only if "
        "you control an artifact.",
    )
    eq("Boseiju fires tap_add_colored",
       "tap_add_colored" in synergy.profile(boseiju)["signals"], True)
    eq("Hinterland Harbor does not fire tap_add_colored",
       "tap_add_colored" in synergy.profile(hinter)["signals"], False)
    eq("Command Tower does not fire tap_add_colored",
       "tap_add_colored" in synergy.profile(tower)["signals"], False)
    eq("Hinterland Harbor fires dual_mana_ability",
       "dual_mana_ability" in synergy.profile(hinter)["signals"], True)
    eq("Boseiju does not fire dual_mana_ability",
       "dual_mana_ability" in synergy.profile(boseiju)["signals"], False)
    eq("Spire any-color is not dual_mana_ability",
       "dual_mana_ability" in synergy.profile(spire)["signals"], False)

    # enchant_land — Wild Growth; Animate Dead / Pacifism near-miss.
    wild = card(
        "Wild Growth",
        "Enchantment — Aura",
        "Enchant land\nWhenever enchanted land is tapped for mana, "
        "its controller adds an additional {G}.",
    )
    pacifism = card(
        "Pacifism",
        "Enchantment — Aura",
        "Enchant creature\nEnchanted creature can't attack or block.",
    )
    eq("Wild Growth fires enchant_land",
       "enchant_land" in synergy.profile(wild)["signals"], True)
    eq("Wild Growth consumes land",
       "land" in synergy.profile(wild)["consumes"], True)
    eq("Pacifism does not fire enchant_land",
       "enchant_land" in synergy.profile(pacifism)["signals"], False)

    # cant_be_blocked — Rogue's Passage; flying reminder must not fire.
    passage = card(
        "Rogue's Passage",
        "Land",
        "{T}: Add {C}.\n"
        "{4}, {T}: Target creature can't be blocked this turn.",
    )
    birds = card(
        "Birds of Paradise",
        "Creature — Bird",
        "Flying\n{T}: Add one mana of any color.",
    )
    # Explicit flying reminder shape that contains "can't be blocked except".
    flyer_reminder = card(
        "Flyer Reminder Shape",
        "Creature — Bird",
        "Flying (This creature can't be blocked except by creatures with "
        "flying or reach.)",
    )
    eq("Rogue's Passage fires cant_be_blocked",
       "cant_be_blocked" in synergy.profile(passage)["signals"], True)
    eq("Birds of Paradise does not fire cant_be_blocked",
       "cant_be_blocked" in synergy.profile(birds)["signals"], False)
    eq("flying reminder is not cant_be_blocked",
       "cant_be_blocked" in synergy.profile(flyer_reminder)["signals"], False)

    # uncounterable — Cavern of Souls; Counterspell near-miss.
    cavern = card(
        "Cavern of Souls",
        "Land",
        "As this land enters, choose a creature type.\n"
        "{T}: Add {C}.\n"
        "{T}: Add one mana of any color. Spend this mana only to cast a "
        "creature spell of the chosen type, and that spell can't be countered.",
    )
    counterspell = card(
        "Counterspell",
        "Instant",
        "Counter target spell.",
    )
    eq("Cavern of Souls fires uncounterable",
       "uncounterable" in synergy.profile(cavern)["signals"], True)
    eq("Counterspell does not fire uncounterable",
       "uncounterable" in synergy.profile(counterspell)["signals"], False)

    # modal_choose_one — Abrade; Austere Command (choose two) near-miss.
    abrade = card(
        "Abrade",
        "Instant",
        "Choose one —\n"
        "• Abrade deals 3 damage to target creature.\n"
        "• Destroy target artifact.",
    )
    austere = card(
        "Austere Command",
        "Sorcery",
        "Choose two —\n"
        "• Destroy all artifacts.\n"
        "• Destroy all enchantments.\n"
        "• Destroy all creatures with mana value 3 or less.\n"
        "• Destroy all creatures with mana value 4 or greater.",
    )
    eq("Abrade fires modal_choose_one",
       "modal_choose_one" in synergy.profile(abrade)["signals"], True)
    eq("Austere Command choose-two is not modal_choose_one",
       "modal_choose_one" in synergy.profile(austere)["signals"], False)

    # firebreathing — +1/+0 activation; +1/+1 near-miss.
    hellkite = card(
        "Steel Hellkite",
        "Artifact Creature — Dragon",
        "Flying\n"
        "{2}: This creature gets +1/+0 until end of turn.\n"
        "{X}: Destroy each nonland permanent with mana value X "
        "whose controller was dealt combat damage by this creature "
        "this turn. Activate only once each turn.",
    )
    nirkana = card(
        "Nirkana Revenant",
        "Creature — Vampire Shade",
        "Whenever you tap a Swamp for mana, add an additional {B}.\n"
        "{B}: This creature gets +1/+1 until end of turn.",
    )
    eq("Steel Hellkite fires firebreathing",
       "firebreathing" in synergy.profile(hellkite)["signals"], True)
    eq("+1/+1 pump is not firebreathing",
       "firebreathing" in synergy.profile(nirkana)["signals"], False)

    # must_attack — Toski; Propaganda near-miss.
    toski = card(
        "Toski, Bearer of Secrets",
        "Legendary Creature — Squirrel",
        "This spell can't be countered.\n"
        "Indestructible\n"
        "Toski attacks each combat if able.\n"
        "Whenever a creature you control deals combat damage to a player, "
        "draw a card.",
    )
    propaganda = card(
        "Propaganda",
        "Enchantment",
        "Creatures can't attack you unless their controller pays {2} "
        "for each creature they control that's attacking you.",
    )
    eq("Toski fires must_attack",
       "must_attack" in synergy.profile(toski)["signals"], True)
    eq("Propaganda is not must_attack",
       "must_attack" in synergy.profile(propaganda)["signals"], False)


def test_keyword_catalog_on_profile():
    """Scryfall keywords surface on profile, never as signals."""
    print("\nsynergy — keyword catalog on profile")
    synergy.clear_cache()
    crow = {
        "name": "Storm Crow",
        "type_line": "Creature — Bird",
        "oracle_text": "Flying",
        "keywords": ["Flying"],
    }
    p = synergy.profile(crow)
    eq("Storm Crow keywords include Flying", "Flying" in p["keywords"], True)
    eq("Flying keyword is not an oracle_signal",
       "flying" in p["signals"] or "Flying" in p["signals"], False)


def test_residual_unread_signals():
    """residual non-keyword unread patterns — exemplar / near-miss."""
    print("\nsynergy — residual unread signals")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    wastes = card("Wastes", "Basic Land", "{T}: Add {C}.")
    boseiju = card(
        "Boseiju, Who Endures",
        "Legendary Land",
        "{T}: Add {G}.\nChannel — {1}{G}, Discard this card: Destroy target "
        "artifact, enchantment, or nonbasic land an opponent controls.",
    )
    eq("Wastes fires colorless_mana",
       "colorless_mana" in synergy.profile(wastes)["signals"], True)
    eq("Boseiju colored is not colorless_mana",
       "colorless_mana" in synergy.profile(boseiju)["signals"], False)

    agent = card(
        "Agent of Stromgald",
        "Creature — Human Knight",
        "{R}: Add {B}.",
    )
    eq("Agent of Stromgald fires filter_mana",
       "filter_mana" in synergy.profile(agent)["signals"], True)
    eq("Wastes is not filter_mana",
       "filter_mana" in synergy.profile(wastes)["signals"], False)

    glass = card(
        "Glass Casket Shape",
        "Creature — Beast",
        "This creature can't block.",
    )
    wall = card(
        "Wall of Omens",
        "Creature — Wall",
        "Defender\nWhen this creature enters, draw a card.",
    )
    eq("can't-block fires cant_block",
       "cant_block" in synergy.profile(glass)["signals"], True)
    eq("defender wall is not cant_block",
       "cant_block" in synergy.profile(wall)["signals"], False)

    both = card(
        "Fire // Ice Shape",
        "Instant",
        "Choose one or both —\n"
        "• Fire // Ice Shape deals 2 damage to target creature.\n"
        "• Tap target creature.",
    )
    one = card(
        "Abrade",
        "Instant",
        "Choose one —\n"
        "• Abrade deals 3 damage to target creature.\n"
        "• Destroy target artifact.",
    )
    eq("choose one or both fires modal_choose_one_or_both",
       "modal_choose_one_or_both" in synergy.profile(both)["signals"], True)
    eq("choose one is not modal_choose_one_or_both",
       "modal_choose_one_or_both" in synergy.profile(one)["signals"], False)

    top = card(
        "Oracle of Mul Daya",
        "Creature — Elf Shaman",
        "You may look at the top card of your library any time.\n"
        "You may play lands from the top of your library.\n"
        "Play an additional land on each of your turns.",
    )
    scry_only = card(
        "Path of Ancestry",
        "Land",
        "This land enters tapped.\n"
        "{T}: Add one mana of any color in your commander's color identity. "
        "When that mana is spent to cast a creature spell that shares a "
        "creature type with your commander, scry 1.",
    )
    eq("Oracle of Mul Daya fires top_library_look",
       "top_library_look" in synergy.profile(top)["signals"], True)
    eq("scry land is not top_library_look",
       "top_library_look" in synergy.profile(scry_only)["signals"], False)

    doom = card(
        "Doom Blade",
        "Instant",
        "Destroy target nonblack creature.",
    )
    wrath = card(
        "Wrath of God",
        "Sorcery",
        "Destroy all creatures. They can't be regenerated.",
    )
    eq("Doom Blade fires destroy_target_creature",
       "destroy_target_creature" in synergy.profile(doom)["signals"], True)
    eq("Wrath mass destroy is not destroy_target_creature",
       "destroy_target_creature" in synergy.profile(wrath)["signals"], False)

    pump = card(
        "Nirkana Revenant",
        "Creature — Vampire Shade",
        "Whenever you tap a Swamp for mana, add an additional {B}.\n"
        "{B}: This creature gets +1/+1 until end of turn.",
    )
    fire = card(
        "Steel Hellkite",
        "Artifact Creature — Dragon",
        "Flying\n"
        "{2}: This creature gets +1/+0 until end of turn.",
    )
    eq("Nirkana fires activated_pump_plus",
       "activated_pump_plus" in synergy.profile(pump)["signals"], True)
    eq("firebreathing +1/+0 is not activated_pump_plus",
       "activated_pump_plus" in synergy.profile(fire)["signals"], False)



def test_midtail_signals():
    """mid-tail grant/tap/exile/type/freeze — exemplar / near-miss."""
    print("\nsynergy — mid-tail signals")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    grant = card(
        "Grant Flying Shape",
        "Creature",
        "{1}{U}: This creature gains flying until end of turn.",
    )
    static = {
        "name": "Storm Crow",
        "type_line": "Creature — Bird",
        "oracle_text": "Flying",
        "keywords": ["Flying"],
    }
    eq("grant flying fires grant_keyword_eot",
       "grant_keyword_eot" in synergy.profile(grant)["signals"], True)
    eq("static Flying keyword text is not grant_keyword_eot",
       "grant_keyword_eot" in synergy.profile(static)["signals"], False)

    tap_act = card(
        "Tapper Shape",
        "Creature",
        "{T}: Tap target creature.",
    )
    bolt = card(
        "Lightning Bolt",
        "Instant",
        "Lightning Bolt deals 3 damage to any target.",
    )
    eq("activated tap-target fires tap_target",
       "tap_target" in synergy.profile(tap_act)["signals"], True)
    eq("Lightning Bolt is not tap_target",
       "tap_target" in synergy.profile(bolt)["signals"], False)

    exile = card(
        "Path to Exile",
        "Instant",
        "Exile target creature. Its controller may search their library for a "
        "basic land card, put that card onto the battlefield tapped, then "
        "shuffle.",
    )
    destroy = card(
        "Doom Blade",
        "Instant",
        "Destroy target nonblack creature.",
    )
    eq("Path fires exile_target_creature",
       "exile_target_creature" in synergy.profile(exile)["signals"], True)
    eq("Doom Blade destroy is not exile_target_creature",
       "exile_target_creature" in synergy.profile(destroy)["signals"], False)

    choose = card(
        "Coat of Arms Shape",
        "Enchantment",
        "As this enchantment enters, choose a creature type.\n"
        "Each creature gets +1/+1 for each other creature on the battlefield "
        "that shares a creature type with it.",
    )
    tribal = card(
        "Realmwalker",
        "Creature — Shapeshifter",
        "Changeling\n"
        "As this creature enters, choose a creature type.\n"
        "You may look at the top card of your library any time.",
    )
    eq("choose-type enchantment fires choose_creature_type",
       "choose_creature_type" in synergy.profile(choose)["signals"], True)
    # Realmwalker also chooses — should fire; near-miss is changeling-only
    no_choose = card(
        "Changeling Outcast",
        "Creature — Shapeshifter",
        "Changeling\n"
        "This creature can't block and can't be blocked.",
    )
    eq("changeling without choose-type is not choose_creature_type",
       "choose_creature_type" in synergy.profile(no_choose)["signals"], False)

    landkill = card(
        "Strip Mine Shape",
        "Land",
        "{T}: Add {C}.\n"
        "{T}, Sacrifice this land: Destroy target land.",
    )
    eq("destroy target land fires destroy_target_land",
       "destroy_target_land" in synergy.profile(landkill)["signals"], True)
    eq("Doom Blade is not destroy_target_land",
       "destroy_target_land" in synergy.profile(destroy)["signals"], False)

    freeze = card(
        "Claustrophobia Shape",
        "Enchantment — Aura",
        "Enchant creature\n"
        "When this Aura enters, tap enchanted creature.\n"
        "Enchanted creature doesn't untap during its controller's untap step.",
    )
    eq("doesn't-untap fires stays_tapped",
       "stays_tapped" in synergy.profile(freeze)["signals"], True)
    eq("Storm Crow is not stays_tapped",
       "stays_tapped" in synergy.profile(static)["signals"], False)

    ping = card(
        "Prodigal Sorcerer Shape",
        "Creature — Human Wizard",
        "{T}: This creature deals 1 damage to any target.",
    )
    bolt = card(
        "Lightning Bolt",
        "Instant",
        "Lightning Bolt deals 3 damage to any target.",
    )
    eq("activated ping fires activated_ping",
       "activated_ping" in synergy.profile(ping)["signals"], True)
    eq("Lightning Bolt is not activated_ping",
       "activated_ping" in synergy.profile(bolt)["signals"], False)


def test_cleanup_signals():
    """thin unread cleanup — exemplar / near-miss."""
    print("\nsynergy — cleanup signals")
    synergy.clear_cache()

    def card(name, type_line, oracle_text):
        return {"name": name, "type_line": type_line, "oracle_text": oracle_text}

    pump = card(
        "Pump Shape",
        "Creature",
        "{R}: This creature gets +2/+0 until end of turn.",
    )
    shrink = card(
        "Throttle",
        "Instant",
        "Target creature gets -4/-4 until end of turn.",
    )
    eq("asymmetric +2/+0 fires stat_pump_eot",
       "stat_pump_eot" in synergy.profile(pump)["signals"], True)
    eq("Throttle is not stat_pump_eot",
       "stat_pump_eot" in synergy.profile(shrink)["signals"], False)
    eq("Throttle fires shrink_target",
       "shrink_target" in synergy.profile(shrink)["signals"], True)
    growth = card("Giant Growth", "Instant", "Target creature gets +3/+3 until end of turn.")
    eq("Giant Growth is not shrink_target",
       "shrink_target" in synergy.profile(growth)["signals"], False)

    bounce = card(
        "Unsummon", "Instant", "Return target creature to its owner's hand."
    )
    exile = card(
        "Path to Exile",
        "Instant",
        "Exile target creature. Its controller may search their library for a "
        "basic land card, put that card onto the battlefield tapped, then shuffle.",
    )
    eq("Unsummon fires bounce_to_hand",
       "bounce_to_hand" in synergy.profile(bounce)["signals"], True)
    eq("Path is not bounce_to_hand",
       "bounce_to_hand" in synergy.profile(exile)["signals"], False)

    fog = card(
        "Fog", "Instant",
        "Prevent all combat damage that would be dealt this turn.",
    )
    life = card("Stream of Life", "Sorcery", "Target player gains X life.")
    eq("Fog fires prevent_damage",
       "prevent_damage" in synergy.profile(fog)["signals"], True)
    eq("Stream of Life is not prevent_damage",
       "prevent_damage" in synergy.profile(life)["signals"], False)

    color = card(
        "Caged Sun",
        "Artifact",
        "As this artifact enters, choose a color.\n"
        "Creatures you control of the chosen color get +1/+1.",
    )
    ctype = card(
        "Door of Destinies",
        "Artifact",
        "As this artifact enters, choose a creature type.",
    )
    eq("Caged Sun fires choose_color",
       "choose_color" in synergy.profile(color)["signals"], True)
    eq("Door of Destinies is not choose_color",
       "choose_color" in synergy.profile(ctype)["signals"], False)

    lib = card(
        "Fallow Earth",
        "Sorcery",
        "Put target land on top of its owner's library.",
    )
    eq("Fallow Earth fires put_on_library",
       "put_on_library" in synergy.profile(lib)["signals"], True)
    eq("Unsummon is not put_on_library",
       "put_on_library" in synergy.profile(bounce)["signals"], False)

    discard = card(
        "Firestorm",
        "Instant",
        "As an additional cost to cast this spell, discard X cards.\n"
        "Firestorm deals X damage to any target.",
    )
    pay_life = card(
        "Fire Covenant",
        "Instant",
        "As an additional cost to cast this spell, pay X life.\n"
        "Fire Covenant deals X damage divided as you choose among any number "
        "of target creatures.",
    )
    eq("Firestorm fires discard_additional_cost",
       "discard_additional_cost" in synergy.profile(discard)["signals"], True)
    eq("Fire Covenant is not discard_additional_cost",
       "discard_additional_cost" in synergy.profile(pay_life)["signals"], False)

    two = card(
        "Austere Command",
        "Sorcery",
        "Choose two —\n• Destroy all artifacts.\n• Destroy all enchantments.",
    )
    one = card(
        "Abrade",
        "Instant",
        "Choose one —\n• Abrade deals 3 damage to target creature.\n"
        "• Destroy target artifact.",
    )
    eq("Austere Command fires choose_two",
       "choose_two" in synergy.profile(two)["signals"], True)
    eq("Abrade is not choose_two",
       "choose_two" in synergy.profile(one)["signals"], False)

    leyline = card(
        "Leyline of Sanctity",
        "Enchantment",
        "If this card is in your opening hand, you may begin the game with it "
        "on the battlefield.\nYou have hexproof.",
    )
    prison = card(
        "Ghostly Prison",
        "Enchantment",
        "Creatures can't attack you unless their controller pays {2} for each "
        "creature they control that's attacking you.",
    )
    eq("Leyline fires leyline_start",
       "leyline_start" in synergy.profile(leyline)["signals"], True)
    eq("Ghostly Prison is not leyline_start",
       "leyline_start" in synergy.profile(prison)["signals"], False)

    upkeep = card(
        "Justice",
        "Enchantment",
        "At the beginning of your upkeep, sacrifice this enchantment unless "
        "you pay {W}{W}.",
    )
    rhystic = card(
        "Rhystic Study",
        "Enchantment",
        "Whenever an opponent casts a spell, you may draw a card unless that "
        "player pays {1}.",
    )
    eq("Justice fires upkeep_sac_unless",
       "upkeep_sac_unless" in synergy.profile(upkeep)["signals"], True)
    eq("Rhystic Study is not upkeep_sac_unless",
       "upkeep_sac_unless" in synergy.profile(rhystic)["signals"], False)

    gy_hand = card(
        "Sanguine Indulgence",
        "Sorcery",
        "Return up to two target creature cards from your graveyard to your hand.",
    )
    eq("multi return-to-hand fires graveyard_recursion",
       "graveyard_recursion" in synergy.profile(gy_hand)["signals"], True)


def test_readback_honesty_unknown_vs_none():
    """unknown/unread copy says could-not-read; readable none stays none."""
    print("\nsynergy/why — readback honesty unknown vs none")
    from deckwright import why

    hei = _card("Hei Bai, Spirit of Balance")
    eq("unknown status",
       synergy.card_read_status({"name": "Mystery", "oracle_text": None}),
       synergy.UNKNOWN)
    # Shock: oracle present, no signal ∧ no keyword → unread (not a real none).
    shock = _card("Shock")
    eq("shock is unread", synergy.card_read_status(shock), synergy.UNREAD)
    # Keyword-readable instant, no Hei Bai edges → real none.
    negate = {
        "name": "Negate",
        "type_line": "Instant",
        "oracle_text": "Counter target noncreature spell.",
        "keywords": ["Flash"],
    }
    eq("negate is readable", synergy.card_read_status(negate), "ok")
    eq("negate has no edges to Hei Bai",
       synergy.edge_count(negate, hei, []), 0)

    unk_note = why.explain_edges(
        {"name": "Mystery", "oracle_text": None, "role": "theme"}, hei, [])
    unread_note = why.explain_edges(
        {**shock, "role": "theme"}, hei, [])
    none_note = why.explain_edges({**negate, "role": "theme"}, hei, [])
    (ok if unk_note and "could not read" in unk_note.lower() else fail)(
        "unknown explain_edges says could not read", f"got {unk_note!r}")
    (ok if unread_note and "could not read" in unread_note.lower() else fail)(
        "unread explain_edges says could not read", f"got {unread_note!r}")
    eq("readable none has empty edge clause (real none)", none_note, "")

    # Cohesion: unread-without-edges are not orphans; unknown stays its own count.
    deck = [
        _card("Pitiless Plunderer"),
        shock,
        negate,
        {"name": "Mystery", "oracle_text": None},
    ]
    c = synergy.cohesion(deck, hei)
    eq("unknown counted", c["unknown"], 1)
    eq("unread without edges counted", c.get("unread"), 1)
    eq("readable none is the orphan", c["orphans"], 1)

    # Synergy note helper for API/UI copy.
    note = synergy.synergy_read_note(shock, hei)
    (ok if note and "could not read" in note.lower() else fail)(
        "synergy_read_note for unread", f"got {note!r}")
    none_api = synergy.synergy_read_note(negate, hei)
    eq("readable none note is empty", none_api, "")


def main():
    test_objects_are_derived()
    test_profile()
    test_unknown_is_not_a_zero()
    test_edges_are_directed_and_object_typed()
    test_shared_static_keyword_is_not_synergy()
    test_unknown_forms_no_edge()
    test_hei_bai_ground_truth()
    test_angel_of_indemnity_has_multiple_edges()
    test_generality_across_unlike_commanders()
    test_cohesion()
    test_cohesion_reports_unknown_separately()
    test_annotate_wires_explain_edges()
    test_readback_honesty_unknown_vs_none()
    test_orphan_gate()
    test_no_weighted_sum_exists()
    test_expert_hei_bai_deck_is_coherent()
    test_blink_patterns_match_real_oracle()
    test_hei_bai_demands_leave_causes()
    test_leave_enabler_queries()
    test_synergy_neighbors()
    test_synergy_recall_queries()
    test_prefer_glued_steers_without_dropping()
    test_prefer_glued_cheaper_among_equal_edges()
    test_timing_multiplier_exempt_from_steer_edge_key()
    test_energy_object_and_edges()
    test_resource_tokens_are_honest()
    test_player_resource_pack()
    test_mana_land_signals()
    test_commander_and_aura_signals()
    test_unread_gap_signals()
    test_keyword_catalog_on_profile()
    test_residual_unread_signals()
    test_midtail_signals()
    test_cleanup_signals()
    print(f"\n{passed} passed" + (f", {RED}{failed} failed{OFF}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
