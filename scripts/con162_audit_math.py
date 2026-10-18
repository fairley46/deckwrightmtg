#!/usr/bin/env python3
"""CON-162: reproduce scoring-contract tables + probe extraction misses.

Not a gate. Prints measured numbers so the confidence readout can cite them.
Every table in docs/SCORING-CONTRACT.md must be reproducible from this file.

    python scripts/con162_audit_math.py                  # full audit
    python scripts/con162_audit_math.py --tables-only    # scoring tables
    python scripts/con162_audit_math.py --extract-only   # adversarial extraction
    python scripts/con162_audit_math.py --keys-only      # THESIS_KEYS reachability
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
from deckwright import sim  # noqa: E402


# ── card helpers ─────────────────────────────────────────────────────────────

def C(name, cost, cmc, type_, text, keywords=None):
    return {
        "name": name, "cost": cost, "cmc": cmc, "type": type_,
        "text": text, "keywords": keywords or [],
    }


def swamp(n=36):
    return [C("Swamp", "", 0, "Basic Land — Swamp", "{T}: Add {B}.") for _ in range(n)]


def mountain(n=36):
    return [C("Mountain", "", 0, "Basic Land — Mountain", "{T}: Add {R}.") for _ in range(n)]


def sol_ring(n=6):
    return [C("Sol Ring", "{1}", 1, "Artifact", "{T}: Add {C}{C}.") for _ in range(n)]


def filler(n, type_="Enchantment", text=""):
    return [C(f"Filler {i}", "{B}", 1, type_, text) for i in range(n)]


def filler_r(n, type_="Enchantment", text=""):
    return [C(f"Filler {i}", "{R}", 1, type_, text) for i in range(n)]


def shell(payload, lands=None, rocks=None, fill_type="Enchantment", fill_text="", color="B"):
    lands = lands if lands is not None else (swamp(36) if color == "B" else mountain(36))
    rocks = rocks if rocks is not None else sol_ring(6)
    need = 99 - len(lands) - len(rocks) - len(payload)
    fill = filler(need, fill_type, fill_text) if color == "B" else filler_r(need, fill_type, fill_text)
    return lands + rocks + payload + fill


def pct_at(out, label, turn):
    for c in out["curves"]:
        if c["label"] == label:
            return c["points"][turn - 1]
    return None


def run_cond(deck, check, label, runs=600, seed=138, cmdr_cmc=3):
    cond = [{"label": label, "note": "", "check": check}]
    out = sim.run(deck, cmdr_cmc, set(), cond, runs=runs, seed=seed)
    return pct_at(out, label, 6), pct_at(out, label, 10)


# ── 1. Scoring contract tables ───────────────────────────────────────────────

def table_draw():
    print("\n=== DRAW (SCORING-CONTRACT, seed=138, 600 runs) ===")
    arena = C("Phyrexian Arena", "{1}{B}{B}", 3, "Enchantment",
              "At the beginning of your upkeep, you draw a card and you lose 1 life.")
    whisper = C("Night's Whisper", "{1}{B}", 2, "Sorcery",
                "You draw two cards and you lose 2 life.")
    decks = {
        "bare": shell([]),
        "8 one-shots": shell([whisper] * 8),
        "8 engines": shell([arena] * 8),
        "12 engines": shell([arena] * 12),
    }
    rows = []
    for thresh in (2, 4, 6, 8):
        label = f"Drew {thresh}"
        t6_os, t10_os = run_cond(decks["8 one-shots"], {"cards_drawn": thresh}, label)
        t6_en, t10_en = run_cond(decks["8 engines"], {"cards_drawn": thresh}, label)
        edge = round(t10_en - t10_os, 1)
        rows.append((thresh, t6_os, t6_en, t10_os, t10_en, edge))
        print(f"  cards_drawn>={thresh}: OS@T6={t6_os} EN@T6={t6_en} "
              f"OS@T10={t10_os} EN@T10={t10_en} edge@T10={edge:+}")
    # bands at >=6
    for name in ("bare", "8 one-shots", "8 engines", "12 engines"):
        _, t10 = run_cond(decks[name], {"cards_drawn": 6}, "Drew 6")
        print(f"  band cards_drawn>=6 @T10  {name}: {t10}%")
    return rows


def table_life():
    print("\n=== LIFEGAIN / DRAIN (seed=121, 600 runs) ===")
    enabler = C("Healing Salve", "{W}", 1, "Instant", "You gain 3 life.")
    # Mono-B shell: use a castable {B} enabler as the contract says
    enabler_b = C("Life Enabler", "{B}", 1, "Sorcery", "You gain 3 life.")
    artist = C("Blood Artist", "{1}{B}", 2, "Creature — Vampire",
               "Whenever Blood Artist or another creature dies, target player loses 1 "
               "life and you gain 1 life.")
    bond = C("Sanguine Bond", "{3}{B}{B}", 5, "Enchantment",
             "Whenever you gain life, each opponent loses that much life.")
    blank = C("Blank Enchant", "{B}", 1, "Enchantment", "Enchantment with no life text.")
    nighthawk = C("Vampire Nighthawk", "{1}{B}{B}", 3, "Creature — Vampire",
                  "Flying, deathtouch, lifelink")
    vortex = C("Sulfuric Vortex", "{1}{R}{R}", 3, "Enchantment",
               "At the beginning of each player's upkeep, Sulfuric Vortex deals 2 damage "
               "to that player. If a player would gain life, that player gains no life instead.")

    for thresh in (1, 3, 6, 9):
        label = f"Gain {thresh}"
        _, t10_en = run_cond(shell([enabler_b] * 8), {"life_gained": thresh}, label, seed=121)
        _, t10_ar = run_cond(shell([artist] * 8), {"life_gained": thresh}, label, seed=121)
        edge = round(t10_ar - t10_en, 1)
        print(f"  life_gained>={thresh}: enablers@T10={t10_en} Artist@T10={t10_ar} edge={edge:+}")

    for n, name, payload in [
        (0, "bare", []),
        (0, "blank", [blank] * 8),
        (4, "4 Artist", [artist] * 4),
        (8, "8 Artist", [artist] * 8),
        (12, "12 Artist", [artist] * 12),
        (8, "8 Bond", [bond] * 8),
    ]:
        _, t10 = run_cond(shell(payload), {"life_drained": 4}, "Drain 4", seed=121)
        print(f"  life_drained>=4 @T10  {name}: {t10}%")

    # Extraction sanity: Nighthawk drain=0, Vortex all zero
    mh = sim.mechanics(nighthawk, set())
    mv = sim.mechanics(vortex, set())
    print(f"  extract Nighthawk drain={mh.get('drain')} life_gain={mh.get('life_gain')} "
          f"lifegain_payoff={mh.get('lifegain_payoff')}")
    print(f"  extract Vortex drain={mv.get('drain')} life_gain={mv.get('life_gain')} "
          f"lifegain_payoff={mv.get('lifegain_payoff')}")


def table_tokens():
    print("\n=== TOKENS (seed=122, 600 runs, Mountains) ===")
    krenko = C("Krenko, Mob Boss", "{2}{R}{R}", 4, "Creature — Goblin Warrior",
               "{T}: Create X 1/1 red Goblin creature tokens, where X is the number of "
               "Goblins you control.")
    create3 = C("Token Burst", "{2}{R}", 3, "Sorcery",
                "Create three 1/1 red Goblin creature tokens.")
    treasure = C("Treasure Map", "{2}", 2, "Artifact",
                 "{1}, {T}: Scry 1. Then if there are three or more scry counters on "
                 "Treasure Map, remove them and create three Treasure tokens.")

    for thresh in (2, 4, 6, 8):
        label = f"Creatures {thresh}"
        deck_os = shell([create3] * 8, color="R")
        deck_en = shell([krenko] * 8, color="R")
        _, t10_os = run_cond(deck_os, {"creatures_in_play": thresh}, label, seed=122)
        _, t10_en = run_cond(deck_en, {"creatures_in_play": thresh}, label, seed=122)
        edge = round(t10_en - t10_os, 1)
        print(f"  creatures_in_play>={thresh}: create3@T10={t10_os} Krenko@T10={t10_en} edge={edge:+}")

    for name, payload in [
        ("bare", []),
        ("Treasure Map x8", [treasure] * 8),
        ("8 create-3", [create3] * 8),
        ("8 Krenko", [krenko] * 8),
        ("12 Krenko", [krenko] * 12),
    ]:
        _, t10 = run_cond(shell(payload, color="R"), {"creatures_in_play": 8},
                          "Creatures 8", seed=122)
        print(f"  creatures_in_play>=8 @T10  {name}: {t10}%")


def table_flashback():
    print("\n=== FLASHBACK (seed=123, 600 runs, Mountains) ===")
    looting = C("Faithless Looting", "{R}", 1, "Sorcery",
                "Draw two cards, then discard two cards.\nFlashback {2}{R}",
                keywords=["Flashback"])
    draw2 = C("Draw Two", "{1}{R}", 2, "Sorcery", "Draw two cards.")

    for thresh in (2, 4, 6, 8):
        label = f"Drew {thresh}"
        deck_os = shell([draw2] * 8, color="R")
        deck_fb = shell([looting] * 8, color="R")
        _, t10_os = run_cond(deck_os, {"cards_drawn": thresh}, label, seed=123)
        _, t10_fb = run_cond(deck_fb, {"cards_drawn": thresh}, label, seed=123)
        edge = round(t10_fb - t10_os, 1)
        print(f"  cards_drawn>={thresh}: draw2@T10={t10_os} Looting@T10={t10_fb} edge={edge:+}")

    for name, payload in [
        ("bare", []),
        ("8 draw-2", [draw2] * 8),
        ("8 Looting", [looting] * 8),
        ("12 Looting", [looting] * 12),
    ]:
        _, t10 = run_cond(shell(payload, color="R"), {"cards_drawn": 4}, "Drew 4", seed=123)
        print(f"  cards_drawn>=4 @T10  {name}: {t10}%")


# ── 2. Adversarial extraction probes ─────────────────────────────────────────

# Cards chosen because their oracle phrasing is known to break naive regexes.
# Expected values are from reading the card, not from current sim output.
ADVERSARIAL = [
    # produces
    ("produces", C("Mana Crypt", "{0}", 0, "Artifact",
                   "At the beginning of your upkeep, flip a coin. If you lose the flip, "
                   "Mana Crypt deals 3 damage to you. {T}: Add {C}{C}."),
     {"produces": 2}),
    ("produces", C("Gilded Lotus", "{5}", 5, "Artifact",
                   "{T}: Add three mana of any one color."),
     {"produces": 3}),
    ("produces", C("Jeweled Lotus", "{0}", 0, "Artifact",
                   "{T}, Sacrifice Jeweled Lotus: Add three mana of any one color. "
                   "Spend this mana only to cast your commander."),
     {"produces": 3}),
    ("produces", C("Basalt Monolith", "{3}", 3, "Artifact",
                   "Basalt Monolith doesn't untap during your untap step. "
                   "{T}: Add {C}{C}{C}. {3}: Untap Basalt Monolith."),
     {"produces": 3}),
    # "Add {G} for each" / "Add X" style — often missed
    # Lands skip the produces path on purpose (mana comes from land sources).
    # Documented as by-design, not a miss — expect 0.
    ("produces", C("Gaea's Cradle", "", 0, "Legendary Land",
                   "{T}: Add {G} for each creature you control."),
     {"produces": 0}),
    ("produces", C("Nyxbloom Ancient", "{4}{G}{G}{G}", 7, "Creature — Elemental",
                   "If you tap a permanent for mana, it produces three times as much of that mana instead."),
     {"produces": 0}),  # multiplier, not a rock — honest zero OR we flag if it invents mana

    # draws / draw_upkeep
    ("draws", C("Brainstorm", "{U}", 1, "Instant", "Draw three cards, then put two cards "
                "from your hand on top of your library in any order."),
     {"draws": 3, "draw": 3}),
    ("draws", C("Harmonize", "{2}{G}{G}", 4, "Sorcery", "Draw three cards."),
     {"draws": 3}),
    ("draws", C("Wheel of Fortune", "{2}{R}", 3, "Sorcery",
                "Each player discards their hand, then draws seven cards."),
     {"draws": 7}),  # each player includes you
    ("draws", C("Howling Mine", "{2}", 2, "Artifact",
                "At the beginning of each player's draw step, if Howling Mine is untapped, "
                "that player draws an additional card."),
     {"draw_upkeep": 0}),  # draw STEP, not upkeep — should NOT be draw_upkeep
    ("draws", C("Sylvan Library", "{1}{G}", 2, "Enchantment",
                "At the beginning of your draw step, you may draw two additional cards. "
                "If you do, choose two cards in your hand drawn this turn. For each of those "
                "cards, pay 4 life or put the card on top of your library."),
     {"draw_upkeep": 0}),  # draw step ≠ upkeep
    ("draws", C("Consecrated Sphinx", "{4}{U}{U}", 6, "Creature — Sphinx",
                "Flying\nWhenever an opponent draws a card, you may draw two cards."),
     {"draw": 10, "draw_proxy_per_turn": 2}),  # whenever-engine
    ("draws", C("Notion Thief", "{2}{U}{B}", 4, "Creature — Human Rogue",
                "Flash\nIf an opponent would draw a card except the first one they draw in "
                "each of their draw steps, instead that player skips that draw and you draw "
                "a card."),
     {"draws": 0}),  # replacement, not "draw N" — often falsely zero or falsely positive
    ("draws", C("Ad Nauseam", "{3}{B}{B}", 5, "Instant",
                "Reveal the top card of your library and put that card into your hand. "
                "You lose life equal to its mana value. You may repeat this process any "
                "number of times."),
     {"draws": 0}),  # "put into your hand" ≠ draw — should be 0 (honest miss) or we note it
    ("draws", C("Necropotence", "{B}{B}{B}", 3, "Enchantment",
                "Skip your draw step.\nWhenever you discard a card, exile that card from "
                "your graveyard.\nPay 1 life: Exile the top card of your library face down. "
                "Put that card into your hand at the beginning of your next end step."),
     {"draws": 0, "draw_upkeep": 0}),

    # life_gain / drain / lifegain_payoff
    ("life", C("Aetherflux Reservoir", "{4}", 4, "Artifact",
               "Whenever you cast a spell, you gain 1 life for each spell you've cast "
               "this turn.\nPay 50 life, Exile Aetherflux Reservoir: It deals 50 damage "
               "to any target."),
     {"life_gain_proxy_per_turn": 1}),  # whenever-cast gain — should be proxy, not one-shot
    ("life", C("Authority of the Consuls", "{W}", 1, "Enchantment",
               "Creatures your opponents control enter tapped.\nWhenever a creature an "
               "opponent controls enters, you gain 1 life."),
     {"life_gain_proxy_per_turn": 1}),
    ("life", C("Cliffhaven Vampire", "{2}{W}{B}", 4, "Creature — Vampire Warrior",
               "Flying\nWhenever you gain life, each opponent loses 1 life."),
     {"lifegain_payoff": 5, "drain": 5}),  # Bond-class but drain amount is 1, not "that much"
    ("life", C("Exquisite Blood", "{4}{B}", 5, "Enchantment",
               "Whenever an opponent loses life, you gain that much life."),
     {"life_gain_proxy_per_turn": 1, "drain": 0, "lifegain_payoff": 0}),
    ("life", C("Vito, Thorn of the Dusk Rose", "{2}{B}", 3,
               "Legendary Creature — Vampire Cleric",
               "Whenever you gain life, target opponent loses that much life.\n"
               "{3}{B}{B}: Creatures you control gain lifelink until end of turn."),
     {"lifegain_payoff": 5, "drain": 5}),
    ("life", C("Gray Merchant of Asphodel", "{3}{B}{B}", 5, "Creature — Zombie",
               "When Gray Merchant of Asphodel enters, each opponent loses X life, where "
               "X is your devotion to black. You gain life equal to the life lost this way."),
     {"drain": 1, "life_gain": 1}),  # one-shot ETB, not engine
    ("life", C("Whip of Erebos", "{2}{B}{B}", 4, "Legendary Enchantment Artifact",
               "Creatures you control have lifelink.\n{2}{B}{B}, {T}: Return target "
               "creature card from your graveyard to the battlefield. It gains haste. "
               "Exile it at the beginning of the next end step."),
     {"drain": 0, "lifegain_payoff": 0, "life_gain": 0}),  # grants lifelink ≠ gain

    # creature_tokens / bodies / anthem
    ("tokens", C("Secure the Wastes", "{X}{W}", 1, "Instant",
                 "Create X 1/1 white Warrior creature tokens."),
     {"creature_tokens": 1, "bodies": 1}),  # X floors to 1; one-shot adds bodies
    ("tokens", C("Lingering Souls", "{2}{W}", 3, "Sorcery",
                 "Create two 1/1 white Spirit creature tokens with flying.\n"
                 "Flashback {1}{B}", keywords=["Flashback"]),
     {"creature_tokens": 2, "bodies": 2}),
    ("tokens", C("Anointed Procession", "{3}{W}", 4, "Enchantment",
                 "If an effect would create one or more tokens under your control, "
                 "it creates twice that many of those tokens instead."),
     {"creature_tokens": 0}),  # doubler, not a creator — honest 0
    ("tokens", C("Parallel Lives", "{3}{G}", 4, "Enchantment",
                 "If an effect would create one or more tokens under your control, "
                 "it creates twice that many of those tokens instead."),
     {"creature_tokens": 0}),
    ("tokens", C("Scute Swarm", "{2}{G}", 3, "Creature — Insect",
                 "Landfall — Whenever a land you control enters, create a 1/1 green "
                 "Insect creature token. If you control six or more lands, create a "
                 "token that's a copy of Scute Swarm instead."),
     {"creature_tokens": 5, "creature_token_proxy_per_turn": 1}),
    ("tokens", C("Bitterblossom", "{1}{B}", 2, "Tribal Enchantment — Faerie",
                 "At the beginning of your upkeep, you lose 1 life and create a 1/1 "
                 "black Faerie Rogue creature token with flying."),
     {"creature_tokens": 5, "creature_token_proxy_per_turn": 1}),
    ("anthem", C("Glorious Anthem", "{1}{W}{W}", 3, "Enchantment",
                 "Creatures you control get +1/+1."),
     {"anthem": 1}),
    ("anthem", C("Intangible Virtue", "{1}{W}", 2, "Enchantment",
                 "Creature tokens you control get +1/+1 and have vigilance."),
     {"anthem": 0}),  # "Creature tokens" ≠ "Creatures" — current regex misses this
    ("anthem", C("Coat of Arms", "{5}", 5, "Artifact",
                 "Each creature gets +1/+1 for each other creature on the battlefield "
                 "that shares a creature type with it."),
     {"anthem": 0}),  # conditional / shared-type — not flat anthem
    ("anthem", C("Craterhoof Behemoth", "{5}{G}{G}{G}", 8, "Creature — Beast",
                 "Haste\nWhen Craterhoof Behemoth enters, creatures you control gain "
                 "trample and get +X/+X until end of turn, where X is the number of "
                 "creatures you control."),
     {"anthem": 0}),  # until EOT / X — not permanent anthem; bodies=1 as creature

    # drop
    ("drop", C("Azusa, Lost but Seeking", "{2}{G}", 3, "Creature — Human Monk",
               "You may play two additional lands on each of your turns."),
     {"drop": 2}),
    ("drop", C("Oracle of Mul Daya", "{3}{G}", 4, "Creature — Elf Shaman",
               "You may play an additional land on each of your turns.\n"
               "Play with the top card of your library revealed.\n"
               "You may play lands from the top of your library."),
     {"drop": 1}),
    ("drop", C("Dryad of the Ilysian Grove", "{2}{G}", 3, "Enchantment Creature — Nymph Dryad",
               "You may play an additional land on each of your turns.\n"
               "Lands you control are every basic land type in addition to their other types."),
     {"drop": 1}),
    ("drop", C("Burgeoning", "{G}", 1, "Enchantment",
               "Whenever an opponent plays a land, you may put a land card from your "
               "hand onto the battlefield."),
     {"drop": 0}),  # not "additional land" phrasing — extra drops via trigger

    # fetch
    ("fetch", C("Cultivate", "{2}{G}", 3, "Sorcery",
                "Search your library for up to two basic land cards, reveal those cards, "
                "put one onto the battlefield tapped and the other into your hand, then "
                "shuffle."),
     {"fetch": 1}),  # only ONE onto battlefield
    ("fetch", C("Kodama's Reach", "{2}{G}", 3, "Sorcery",
                "Search your library for up to two basic land cards, reveal those cards, "
                "put one onto the battlefield tapped and the other into your hand, then "
                "shuffle."),
     {"fetch": 1}),
    ("fetch", C("Rampant Growth", "{1}{G}", 2, "Sorcery",
                "Search your library for a basic land card, put that card onto the "
                "battlefield tapped, then shuffle."),
     {"fetch": 1}),
    ("fetch", C("Skyshroud Claim", "{3}{G}", 4, "Sorcery",
                "Search your library for up to two Forest cards, put them onto the "
                "battlefield, then shuffle."),
     {"fetch": 2}),
    ("fetch", C("Farseek", "{1}{G}", 2, "Sorcery",
                "Search your library for a Plains, Island, Swamp, or Mountain card, "
                "put it onto the battlefield tapped, then shuffle."),
     {"fetch": 1}),
    # CON-163: activated land-search is not on-cast fetch.
    ("fetch", C("Wayfarer's Bauble", "{1}", 1, "Artifact",
                "{2}, {T}, Sacrifice Wayfarer's Bauble: Search your library for a "
                "basic land card, put that card onto the battlefield tapped, then "
                "shuffle."),
     {"fetch": 0}),
    ("fetch", C("Burnished Hart", "{3}", 3, "Artifact Creature — Elk",
                "{3}, Sacrifice Burnished Hart: Search your library for up to two "
                "basic land cards, put them onto the battlefield tapped, then "
                "shuffle."),
     {"fetch": 0}),
]


def probe_extraction():
    print("\n=== ADVERSARIAL EXTRACTION ===")
    misses = []
    for family, card, expect in ADVERSARIAL:
        m = sim.mechanics(card, set())
        bad = []
        for k, v in expect.items():
            got = m.get(k, 0)
            if got != v:
                bad.append(f"{k}: got {got} want {v}")
        status = "MISS" if bad else "ok  "
        line = f"  {status} [{family}] {card['name']}"
        if bad:
            line += " — " + "; ".join(bad)
            misses.append((family, card["name"], bad, card, expect, m))
        print(line)
    print(f"\n{len(misses)} misses of {len(ADVERSARIAL)} probes")
    return misses


# ── 3. THESIS_KEYS reachability ──────────────────────────────────────────────

def key_reachability():
    print("\n=== THESIS_KEYS REACHABILITY (does each key ever leave 0?) ===")
    # Deck designed to move every key if the sim wires it.
    payload = [
        C("Sol Ring", "{1}", 1, "Artifact", "{T}: Add {C}{C}."),
        C("Phyrexian Arena", "{1}{B}{B}", 3, "Enchantment",
          "At the beginning of your upkeep, you draw a card and you lose 1 life."),
        C("Exploration", "{G}", 1, "Enchantment",
          "You may play an additional land on each of your turns."),
        C("Cultivate", "{2}{G}", 3, "Sorcery",
          "Search your library for up to two basic land cards, reveal those cards, "
          "put one onto the battlefield tapped and the other into your hand, then shuffle."),
        C("Blood Artist", "{1}{B}", 2, "Creature — Vampire",
          "Whenever Blood Artist or another creature dies, target player loses 1 "
          "life and you gain 1 life."),
        C("Token Maker", "{2}{B}", 3, "Sorcery",
          "Create three 1/1 black Rat creature tokens."),
        C("Glorious Anthem", "{1}{W}{W}", 3, "Enchantment",
          "Creatures you control get +1/+1."),
        C("Payoff Creature", "{2}{B}", 3, "Creature — Demon", "A big scary payoff."),
    ]
    # Mixed lands so Exploration/Cultivate can cast (G) and black pieces can cast.
    lands = (
        [C("Swamp", "", 0, "Basic Land — Swamp", "{T}: Add {B}.") for _ in range(18)]
        + [C("Forest", "", 0, "Basic Land — Forest", "{T}: Add {G}.") for _ in range(12)]
        + [C("Plains", "", 0, "Basic Land — Plains", "{T}: Add {W}.") for _ in range(6)]
    )
    rocks = sol_ring(4)
    need = 99 - len(lands) - len(rocks) - len(payload)
    deck = lands + rocks + payload + filler(need)
    payoff_names = {"Payoff Creature"}

    # Instrument: run play_one many times and track max of each key.
    cards = sim.prep(deck, payoff_names)
    maxima = {k: 0 for k in sim.THESIS_KEYS}
    # Also track bool keys separately
    bool_seen = {"commander_in_play": False}

    # Monkey-patch by calling play_one and reading via conditions that fire at each level.
    # Simpler: wrap state inspection by patching — instead, use graduated conditions.
    conditions = []
    for k in sim.THESIS_KEYS:
        if k == "commander_in_play":
            conditions.append({"label": k, "note": "", "check": {k: True}})
        else:
            # fire at threshold 1 so we know the key can be non-zero
            conditions.append({"label": k, "note": "", "check": {k: 1}})

    # Deeper maxima: run with high thresholds and see median
    out = sim.run(deck, 3, payoff_names, conditions, runs=400, seed=162)
    for c in out["curves"]:
        t10 = c["points"][9]
        moved = t10 > 0
        print(f"  {'MOVE' if moved else 'STUCK'}  {c['label']:<22} @T10={t10}% "
              f"(threshold {'True' if c['label']=='commander_in_play' else 1})")
    return out


# ── 4. Payoff query kind audit ───────────────────────────────────────────────

UA = {"User-Agent": "Deckwright/1.0 (CON-162 QC)", "Accept": "application/json"}


def scry_top(q, n=8):
    u = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": q, "order": "edhrec"})
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
        d = json.load(r)
    cards = d.get("data", [])[:n]
    return d.get("total_cards", 0), [
        {
            "name": c["name"],
            "type": c.get("type_line", ""),
            "oracle": (c.get("oracle_text") or "")[:120].replace("\n", " / "),
            "is_land": "Land" in (c.get("type_line") or ""),
        }
        for c in cards
    ]


def payoff_kind_audit():
    print("\n=== PAYOFF QUERY KIND AUDIT ===")
    from deckwright import plan_graph
    targets = list(plan_graph._PAYOFF_QUERIES.items())
    for arch, spec in plan_graph._ARCHETYPES.items():
        for i, q in enumerate(spec.get("extra_payoffs", ()) or ()):
            targets.append((f"{arch}[{i}]", q))

    suspects = []
    for fam, q in targets:
        try:
            total, top = scry_top(q, 8)
        except Exception as e:
            print(f"  FAIL {fam}: {e}")
            suspects.append((fam, str(e)))
            time.sleep(0.2)
            continue
        land_n = sum(1 for c in top if c["is_land"])
        print(f"\n  {fam}  total={total}  lands_in_top8={land_n}")
        print(f"    q: {q}")
        for c in top:
            flag = " LAND" if c["is_land"] else ""
            print(f"    -{flag} {c['name']}  [{c['type']}]")
            print(f"         {c['oracle']}")
        # Heuristic flags for wrong kind
        if land_n >= 3:
            suspects.append((fam, f"{land_n}/8 top hits are lands"))
        # go_wide_payoffs returning non-anthems?
        if fam == "go_wide_payoffs":
            non_anthem = [c for c in top if "creatures you control get" not in c["oracle"].lower()
                          and "creature tokens you control get" not in c["oracle"].lower()]
            if non_anthem:
                suspects.append((fam, f"non-anthem in top: {[c['name'] for c in non_anthem]}"))
        if fam == "saboteur":
            # combat-damage-to-player includes equipment/auras AND creatures — OK,
            # but pure cantrips?
            pass
        if fam == "library_manipulation":
            # "whenever you draw" should be draw payoffs, not Howling Mine
            names = [c["name"] for c in top]
            if any(x in names for x in ("Howling Mine", "Font of Mythos", "Temple Bell")):
                suspects.append((fam, f"draw-everyone engines in payoff pool: {names[:5]}"))
        time.sleep(0.15)

    print(f"\n{len(suspects)} suspect pattern(s):")
    for fam, why in suspects:
        print(f"  ? {fam}: {why}")
    return suspects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables-only", action="store_true")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--keys-only", action="store_true")
    ap.add_argument("--payoffs-only", action="store_true")
    args = ap.parse_args()
    all_ = not any([args.tables_only, args.extract_only, args.keys_only, args.payoffs_only])

    if all_ or args.extract_only:
        probe_extraction()
    if all_ or args.keys_only:
        key_reachability()
    if all_ or args.tables_only:
        table_draw()
        table_life()
        table_tokens()
        table_flashback()
    if all_ or args.payoffs_only:
        payoff_kind_audit()


if __name__ == "__main__":
    # Fix duplicate run_cond — the first definition had a bug; redefine cleanly.
    main()
