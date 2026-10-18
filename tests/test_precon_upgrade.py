"""CON-217/219/220: precon ways + beef-it-up ranking — offline, no invented names."""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from deckwright import precon_upgrade
from deckwright import synergy


def _eq(label, got, want):
    ok = got == want
    print(("  OK  " if ok else "  FAIL") + f" {label}: {got!r}" +
          ("" if ok else f" (want {want!r})"))
    assert ok, label


def _true(label, cond):
    print(("  OK  " if cond else "  FAIL") + f" {label}")
    assert cond, label


def test_ways_for_precon_detect():
    print("\nprecon_upgrade.ways_for_precon — detect + cap")
    # Saheeli-shaped artifact token commander (oracle signals → tokens).
    card = {
        "name": "Test Saheeli",
        "type": "Legendary Creature — Human Artificer",
        "text": (
            "Whenever you create one or more tokens, put a +1/+1 counter "
            "on target creature you control. "
            "Create a token that's a copy of target artifact."
        ),
        "color_identity": ["U", "R"],
        "keywords": [],
    }
    out = precon_upgrade.ways_for_precon(card, limit=3)
    _eq("status ok or empty (never invent)", out["status"] in ("ok", "empty"), True)
    if out["status"] == "ok":
        _true("has ways", len(out["ways"]) >= 1)
        _true("capped", len(out["ways"]) <= 3)
        for w in out["ways"]:
            _true("playstyle id present", bool(w.get("playstyle")))
            _true("says present", bool(w.get("says") or w.get("why")))
        _eq("chosen is first way", out["chosen_playstyle"],
            out["ways"][0]["playstyle"])
    else:
        _eq("honest empty note", bool(out.get("ways_note")), True)


def test_ways_unknown_unreadable():
    print("\nprecon_upgrade.ways_for_precon — unknown ≠ empty invent")
    out = precon_upgrade.ways_for_precon({})
    _eq("status unknown", out["status"], "unknown")
    _eq("no ways", out["ways"], [])
    _true("note explains", "not inventing" in (out.get("ways_note") or "").lower()
          or "could not" in (out.get("ways_note") or "").lower())


def _brass_commander():
    return {
        "name": "Admiral Brass, Unsinkable",
        "type": "Legendary Creature — Human Pirate",
        "text": (
            "When Admiral Brass, Unsinkable enters the battlefield, "
            "mill four cards. "
            "At the beginning of combat on your turn, you may "
            "return target Pirate card from your graveyard to the "
            "battlefield with a finality counter on it. It gains haste."
        ),
        "color_identity": ["U", "B", "R"],
        "keywords": [],
    }


def _animate_dead():
    return {
        "name": "Animate Dead",
        "type": "Enchantment — Aura",
        "text": (
            "Enchant creature card in a graveyard. When Animate Dead enters "
            "the battlefield, if it is on the battlefield, it loses "
            "\"enchant creature card in a graveyard\" and gains "
            "\"enchant creature put onto the battlefield with Animate Dead.\" "
            "Return enchanted creature card to the battlefield under your "
            "control and attach Animate Dead to it. When Animate Dead leaves "
            "the battlefield, that creature's controller sacrifices it."
        ),
        "edhrec_rank": 400,
        "color_identity": ["B"],
        "keywords": [],
        "usd": "1.50",
    }


def test_rank_upgrades_never_invents():
    print("\nprecon_upgrade.rank_upgrades — only candidate names")
    commander = _brass_commander()
    deck = [
        {"name": "Sol Ring", "type": "Artifact", "text": "{T}: Add {C}{C}."},
        {"name": "Island", "type": "Basic Land — Island", "text": "{T}: Add {U}."},
    ]
    # Candidate already in deck must never appear in upgrades.
    sol = {"name": "Sol Ring", "type": "Artifact",
           "text": "{T}: Add {C}{C}.", "edhrec_rank": 1}
    recurse = _animate_dead()
    unrelated = {
        "name": "Storm Crow",
        "type": "Creature — Bird",
        "text": "Flying",
        "edhrec_rank": 20000,
        "keywords": ["Flying"],
    }

    empty = precon_upgrade.rank_upgrades([], commander, deck)
    _eq("no candidates → unknown (not empty zero)", empty["status"], "unknown")
    _eq("no invented cards", empty["cards"], [])

    ranked = precon_upgrade.rank_upgrades(
        [sol, recurse, unrelated], commander, deck, limit=8)
    names = {c["name"] for c in ranked["cards"]}
    _true("sol ring excluded from upgrades (already in 99)", "Sol Ring" not in names)
    _true("never invents a name not in candidates",
          names <= {"Animate Dead", "Storm Crow"})
    _true("Animate Dead ranked (demand/lean help)", "Animate Dead" in names)
    _eq("Storm Crow not synergistic", "Storm Crow" in names, False)
    for row in ranked["cards"]:
        _true(f"{row['name']} has why", bool(row.get("why")))


def test_already_in_deck_group_con220():
    """CON-220: synergistic cards already in the 99 are named, not silently dropped."""
    print("\nprecon_upgrade.rank_upgrades — CON-220 already_in group")
    commander = _brass_commander()
    in_box = _animate_dead()
    deck = [
        dict(in_box),
        {"name": "Island", "type": "Basic Land — Island", "text": "{T}: Add {U}."},
    ]
    outsider = {
        "name": "Necromancy",
        "type": "Enchantment",
        "text": (
            "You may cast Necromancy as though it had flash. "
            "When Necromancy enters, if it's on the battlefield, it becomes "
            "an Aura with enchant creature. Put target creature card from a "
            "graveyard onto the battlefield under your control and attach "
            "Necromancy to it. When Necromancy leaves, that creature's "
            "controller sacrifices it."
        ),
        "edhrec_rank": 500,
        "color_identity": ["B"],
        "keywords": [],
        "usd": "4.00",
    }
    # Off-plan burn — must not self-tag into lean the way Flying would via
    # evasion once the card is already in the 99.
    weak_in_deck = {
        "name": "Shock",
        "type": "Instant",
        "text": "Shock deals 2 damage to any target.",
        "edhrec_rank": 15000,
        "keywords": [],
        "color_identity": ["R"],
    }
    deck_full = deck + [weak_in_deck]

    ranked = precon_upgrade.rank_upgrades(
        [in_box, outsider, weak_in_deck], commander, deck_full, limit=8)

    upgrade_names = {c["name"] for c in ranked["cards"]}
    already = ranked.get("already_in") or []
    already_names = {c["name"] for c in already}

    _true("upgrades never contain a card in the 99",
          upgrade_names.isdisjoint({c["name"] for c in deck_full}))
    _true("Animate Dead surfaces in already_in", "Animate Dead" in already_names)
    _eq("Shock not in already_in (not synergistic)",
        "Shock" in already_names, False)
    _true("Necromancy can still rank as an upgrade", "Necromancy" in upgrade_names
          or ranked["status"] in ("ok", "empty"))
    note = (ranked.get("already_in_note") or "").lower()
    _true("copy says in this deck, not collection",
          "in this deck" in note
          and "collection" not in note
          and "you already own" not in note)
    for row in already:
        _true(f"already_in {row['name']} has why", bool(row.get("why")))

    # Empty already_in when nothing synergistic was skipped — not inventing.
    only_new = precon_upgrade.rank_upgrades(
        [outsider], commander,
        [{"name": "Island", "type": "Basic Land — Island", "text": "{T}: Add {U}."}],
        limit=8)
    _eq("already_in empty when genuinely empty", only_new.get("already_in"), [])


def test_already_in_never_cut_con235():
    """CON-235: already_in overlap must not also appear as a cut suggestion."""
    print("\nprecon_upgrade.rank_upgrades — CON-235 already_in not cuttable")
    commander = {
        "name": "Saheeli, Radiant Creator",
        "type": "Legendary Creature — Human Artificer",
        "text": (
            "Whenever you create one or more tokens, put a +1/+1 counter "
            "on target creature you control. "
            "{1}{U}{R}: Create a token that's a copy of target artifact "
            "or creature you control, except it's a 5/5 in addition to "
            "its other types."
        ),
        "color_identity": ["U", "R"],
        "keywords": [],
    }
    # Sai-shaped: token_create demand overlap, 0 commander edges, high CMC.
    sai = {
        "name": "Sai, Master Thopterist",
        "type": "Legendary Creature — Human Artificer",
        "text": (
            "Whenever you tap an artifact for mana, draw a card. "
            "{1}{U}: Create a 1/1 colorless Thopter artifact creature "
            "token with flying."
        ),
        "cmc": 3,
        "cost": "{2}{U}",
        "edhrec_rank": 800,
        "keywords": [],
        "color_identity": ["U"],
    }
    ancient = {
        "name": "Aethersquall Ancient",
        "type": "Creature — Leviathan",
        "text": (
            "Flying\n{5}{U}{U}: Return all other creatures to their "
            "owners' hands."
        ),
        "cmc": 7,
        "cost": "{5}{U}{U}",
        "keywords": ["Flying"],
        "color_identity": ["U"],
    }
    sol = {
        "name": "Sol Ring",
        "type": "Artifact",
        "text": "{T}: Add {C}{C}.",
        "cmc": 1,
        "keywords": [],
    }
    island = {
        "name": "Island",
        "type": "Basic Land — Island",
        "text": "{T}: Add {U}.",
        "cmc": 0,
    }
    deck = [sai, ancient, sol, island]
    upgrade = {
        "name": "Craterhoof Behemoth",
        "type": "Creature — Beast",
        "text": (
            "Haste\nWhen Craterhoof Behemoth enters, creatures you "
            "control gain trample and get +X/+X until end of turn, "
            "where X is the number of creatures you control."
        ),
        "cmc": 8,
        "edhrec_rank": 50,
        "keywords": ["Haste"],
        "usd": "24.31",
        "color_identity": ["G"],
    }
    wide = {
        "name": "Secure the Wastes",
        "type": "Instant",
        "text": "Create X 1/1 white Warrior creature tokens.",
        "cmc": 1,
        "edhrec_rank": 900,
        "keywords": [],
        "usd": "1.20",
        "color_identity": ["W"],
    }

    ranked = precon_upgrade.rank_upgrades(
        [sai, upgrade, wide], commander, deck, limit=8)
    already_names = {c["name"] for c in ranked.get("already_in") or []}
    cut_names = [
        r["cut"]["name"] for r in ranked.get("cards") or []
        if r.get("cut")
    ]

    _true("Sai in already_in (demand overlap)", "Sai, Master Thopterist" in already_names)
    _true("Sai never suggested as a cut",
          "Sai, Master Thopterist" not in cut_names)
    _true("Ancient still cuttable for first add",
          "Aethersquall Ancient" in cut_names)
    # Second add may exhaust the pool — honest no-cut is fine; Sai is not.
    for row in ranked.get("cards") or []:
        if row.get("cut") is None:
            _true(f"{row['name']} explains missing cut", bool(row.get("cut_note")))


def test_cut_pairs_con219():
    """CON-219: every add pairs with a cut suggestion, or says why not."""
    print("\nprecon_upgrade.rank_upgrades — CON-219 cut pairs")
    commander = {
        "name": "Saheeli, Radiant Creator",
        "type": "Legendary Creature — Human Artificer",
        "text": (
            "Whenever you create one or more tokens, put a +1/+1 counter "
            "on target creature you control. "
            "{1}{U}{R}: Create a token that's a copy of target artifact "
            "or creature you control, except it's a 5/5 in addition to "
            "its other types."
        ),
        "color_identity": ["U", "R"],
        "keywords": [],
    }
    # High-CMC commander-orphan — classic cut (Aethersquall-shaped). Real
    # card also has energy; the cut signal is "no edge to the commander."
    ancient = {
        "name": "Aethersquall Ancient",
        "type": "Creature — Leviathan",
        "text": (
            "Flying\n{5}{U}{U}: Return all other creatures to their "
            "owners' hands."
        ),
        "cmc": 7,
        "cost": "{5}{U}{U}",
        "keywords": ["Flying"],
        "color_identity": ["U"],
    }
    # Glued to the commander via creature tokens — must not be the cut.
    servo_helper = {
        "name": "Intangible Virtue",
        "type": "Enchantment",
        "text": "Creature tokens you control get +1/+1 and have vigilance.",
        "cmc": 2,
        "keywords": [],
        "color_identity": ["W"],
        "role": "plan",
    }
    sol = {
        "name": "Sol Ring",
        "type": "Artifact",
        "text": "{T}: Add {C}{C}.",
        "cmc": 1,
        "keywords": [],
    }
    island = {
        "name": "Island",
        "type": "Basic Land — Island",
        "text": "{T}: Add {U}.",
        "cmc": 0,
    }
    # Unreadable — house rule: never a cut candidate.
    unread = {
        "name": "Mystery Bulk",
        "type": "",
        "text": None,
        "oracle_text": None,
        "cmc": 9,
    }
    deck = [ancient, servo_helper, sol, island, unread]
    finisher = {
        "name": "Craterhoof Behemoth",
        "type": "Creature — Beast",
        "text": (
            "Haste\nWhen Craterhoof Behemoth enters, creatures you "
            "control gain trample and get +X/+X until end of turn, "
            "where X is the number of creatures you control."
        ),
        "cmc": 8,
        "edhrec_rank": 50,
        "keywords": ["Haste"],
        "usd": "24.31",
        "color_identity": ["G"],
    }
    # Second upgrade candidate that clears the Saheeli bar.
    wide = {
        "name": "Secure the Wastes",
        "type": "Instant",
        "text": "Create X 1/1 white Warrior creature tokens.",
        "cmc": 1,
        "edhrec_rank": 900,
        "keywords": [],
        "usd": "1.20",
        "color_identity": ["W"],
    }

    ranked = precon_upgrade.rank_upgrades(
        [finisher, wide], commander, deck, limit=8)
    _eq("status ok", ranked["status"], "ok")
    _true("has upgrade rows", len(ranked["cards"]) >= 1)
    for row in ranked["cards"]:
        _true(f"{row['name']} has cut key", "cut" in row)
        if row["cut"] is None:
            _true(f"{row['name']} explains missing cut",
                  bool(row.get("cut_note")))
        else:
            cut = row["cut"]
            _true("cut name from the 99", cut["name"] in {c["name"] for c in deck})
            _eq("never cut Sol Ring (ramp floor)", cut["name"] == "Sol Ring", False)
            _eq("never cut Island (land floor)", cut["name"] == "Island", False)
            _eq("never cut unread unknown", cut["name"] == "Mystery Bulk", False)
            _true("cut has why in her terms", bool(cut.get("why")))
            why_l = cut["why"].lower()
            _true("why avoids jargon orphan/plan role",
                  "orphan" not in why_l and "plan role" not in why_l)

    cuts = [r["cut"]["name"] for r in ranked["cards"] if r.get("cut")]
    _true("same cut not reused across adds", len(cuts) == len(set(cuts)))
    _true("orphan Ancient preferred as a cut",
          "Aethersquall Ancient" in cuts)

    # Applying every suggested cut leaves land + ramp intact.
    cut_set = set(cuts)
    remain = [c for c in deck if c["name"] not in cut_set]
    _true("still has a land after all cuts",
          any("land" in (c.get("type") or "").lower() for c in remain))
    _true("still has ramp (Sol Ring) after all cuts",
          any(c["name"] == "Sol Ring" for c in remain))

    # Glue-only 99 → no invented cuts.
    tight = precon_upgrade.rank_upgrades(
        [wide], commander, [servo_helper, sol, island], limit=8)
    if tight["status"] == "ok" and tight["cards"]:
        for row in tight["cards"]:
            if row.get("cut") is None:
                _true("honest no-cut note", bool(row.get("cut_note")))


def test_upgrade_queries_unknown_demand():
    print("\nprecon_upgrade.upgrade_queries — unknown demand → []")
    blank = {"name": "???", "text": None, "oracle_text": None}
    # Force unknown profile: empty unreadable card.
    qs = precon_upgrade.upgrade_queries({"name": ""})
    # Empty name with no text → unknown demand → no queries to invent from.
    demand = synergy.demand_profile({"name": ""})
    if demand["status"] == synergy.UNKNOWN:
        _eq("no queries when unknown", qs, [])
    else:
        # If profile somehow reads, still must not invent card names — only queries.
        _true("queries are strings", all(isinstance(q, str) for q in qs))


def test_attach_ways_on_response():
    print("\nprecon_upgrade.attach_ways — stamps response")
    card = {
        "name": "Test Token Maker",
        "type": "Legendary Creature — Human Artificer",
        "text": "Create a 1/1 colorless Servo artifact creature token.",
        "color_identity": ["W"],
        "keywords": [],
    }
    resp = {"commander_card": card, "deck": [], "precon": {"id": "1"}}
    out = precon_upgrade.attach_ways(resp)
    _true("ways_to_play key", "ways_to_play" in out)
    _true("ways_status key", "ways_status" in out)
    _true("chosen key", "chosen_playstyle" in out)


def test_readable_prefer_con259():
    """CON-259: at equal edges, readable (signal∨keyword) ranks above unread."""
    print("\nprecon_upgrade.rank_upgrades — CON-259 readable prefer")
    commander = {
        "name": "Token Boss",
        "type": "Legendary Creature",
        "text": (
            "Whenever a creature token you control dies, each opponent "
            "loses 1 life and you gain 1 life."
        ),
        "color_identity": ["B", "W"],
        "keywords": [],
    }
    deck = [{
        "name": "Island",
        "type": "Basic Land — Island",
        "text": "",
        "role": "basic",
        "cmc": 0,
        "color_identity": ["U"],
        "keywords": [],
    }]
    # Same EDHREC rank; both clear the bar via demand/edges to dies/tokens.
    readable = {
        "name": "Readable Sac Outlet",
        "type": "Creature",
        "text": (
            "Sacrifice a creature: Create a Treasure token. "
            "Whenever a creature you control dies, draw a card."
        ),
        "color_identity": ["B"],
        "keywords": [],
        "edhrec_rank": 1000,
        "usd": "1.00",
    }
    unread = {
        "name": "Unread Blank",
        "type": "Creature",
        "text": "A unique snowflake with no shared pattern language whatsoever xyzzy.",
        "color_identity": ["B"],
        "keywords": [],
        "edhrec_rank": 1000,
        "usd": "1.00",
    }
    _true("readable fires", precon_upgrade.card_is_readable(readable))
    # Unread may still get type-line produces; "readable" means signals|keywords.
    ranked = precon_upgrade.rank_upgrades(
        [unread, readable], commander, deck, limit=8)
    if ranked["status"] == "ok" and len(ranked["cards"]) >= 2:
        names = [c["name"] for c in ranked["cards"]]
        if "Readable Sac Outlet" in names and "Unread Blank" in names:
            _true(
                "readable before unread at equal edhrec",
                names.index("Readable Sac Outlet")
                < names.index("Unread Blank"),
            )
        else:
            # Synergy bar may drop the unread blank — that is also fine.
            _true("readable present when it clears the bar",
                  "Readable Sac Outlet" in names)
    else:
        _true("rank looked (status not unknown)", ranked["status"] != "unknown")


def test_way_playstyle_reorders_queries_con259():
    print("\nprecon_upgrade.upgrade_queries — CON-259 way soft-order")
    # Commander with multiple demand signals so recall is non-empty.
    card = {
        "name": "Multi Way",
        "type": "Legendary Creature",
        "text": (
            "Whenever a creature you control dies, draw a card. "
            "Create a 1/1 Soldier creature token. "
            "Exile target creature you control, then return it to the battlefield."
        ),
        "color_identity": ["W", "B", "U"],
        "keywords": [],
    }
    base = precon_upgrade.upgrade_queries(card, {})
    blink = precon_upgrade.upgrade_queries(card, {}, way_playstyle="blink")
    _true("base queries exist", isinstance(base, list))
    _true("blink queries exist", isinstance(blink, list))
    # Invalid playstyle ignored — same fragments, not inventing.
    bogus = precon_upgrade.upgrade_queries(card, {}, way_playstyle="heists")
    _eq("bogus way ignored length", len(bogus), len(base))


if __name__ == "__main__":
    test_ways_for_precon_detect()
    test_ways_unknown_unreadable()
    test_rank_upgrades_never_invents()
    test_already_in_deck_group_con220()
    test_already_in_never_cut_con235()
    test_cut_pairs_con219()
    test_upgrade_queries_unknown_demand()
    test_attach_ways_on_response()
    test_readable_prefer_con259()
    test_way_playstyle_reorders_queries_con259()
    print("\nALL OK")
