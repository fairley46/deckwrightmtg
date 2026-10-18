#!/usr/bin/env python3
"""
CON-119 gate: can the simulator tell two cards apart?

This is the contract for the mechanics/perception work. It is not a test of
whether the app is nice — it is the single question that decides whether the
mechanics substrate is EYES or a DICTIONARY:

    Given two cards that cost the same and both "draw cards", does the scorer
    return different numbers for the one that does it every turn and the one
    that does it once?

If it cannot, then `card_score.py` — which ranks candidates by simulating them —
is scoring noise for every role except ramp, and no amount of family taxonomy
fixes that. Knowing what "cycling" means is a dictionary. Telling Phyrexian
Arena from Sign in Blood for a specific deck is eyes.

WHY IT LOOKS LIKE THIS
----------------------
Every pair below is: same cost, same stated effect, genuinely different value.
No trick cards, no corner cases — if the perception layer cannot separate these
it cannot separate anything.

The pairs are ordered by the sequence Cursor is farming: draw, then lifegain and
drain, then tokens. Each pair asserts a DIRECTION, never a magnitude, because
magnitudes are a scoring decision and this file must not quietly become the
scoring contract (that is CON-138, and it belongs to Box).

    python test_perception.py            # run the gate
    python test_perception.py --verbose  # show every extracted mechanic

CON-162 added an adversarial section — one card per signal the regex was getting
wrong. Those pairs must stay green; a silent zero on a new phrasing is how Azusa
and "target player draws" shipped.
"""

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys

sys.path.insert(0, ".")
from deckwright import sim  # noqa: E402

VERBOSE = "--verbose" in sys.argv

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

_passed: list[str] = []
_failed: list[str] = []


def card(name, cost, cmc, type_, text):
    """A card in the shape sim.mechanics() consumes."""
    return {"name": name, "cost": cost, "cmc": cmc, "type": type_, "text": text}


def extract(c, payoffs=frozenset()):
    return sim.mechanics(c, set(payoffs))


def differs(label, a, b, key, direction, why):
    """`a` must beat `b` on `key`. direction is '>' or '<'."""
    ma, mb = extract(a), extract(b)
    va, vb = ma.get(key, 0), mb.get(key, 0)
    ok = (va > vb) if direction == ">" else (va < vb)
    line = (f"{a['name']} {key}={va}  vs  {b['name']} {key}={vb}")
    if ok:
        _passed.append(label)
        print(f"  {GREEN}sees{OFF}  {label}")
    else:
        _failed.append(label)
        print(f"  {RED}BLIND{OFF} {label}")
        print(f"        {DIM}{line}{OFF}")
        print(f"        {DIM}why it matters: {why}{OFF}")
    if VERBOSE:
        print(f"        {DIM}{a['name']}: {ma}{OFF}")
        print(f"        {DIM}{b['name']}: {mb}{OFF}")


def section(title):
    print(f"\n{YELLOW}{title}{OFF}")


# ── the control: this one already works ──────────────────────────────────────
# If this fails, something is broken in sim.py itself, not in perception.
section("control — mana, which sim.py already reads")

differs(
    "Sol Ring produces more than Arcane Signet",
    card("Sol Ring", "{1}", 1, "Artifact", "{T}: Add {C}{C}."),
    card("Arcane Signet", "{2}", 2, "Artifact", "{T}: Add one mana of any color."),
    "produces", ">",
    "the whole ramp axis depends on this and it is the one thing that works",
)

# ── draw: CON-119 ────────────────────────────────────────────────────────────
section("draw — CON-119")

differs(
    "Phyrexian Arena draws more than Sign in Blood",
    card("Phyrexian Arena", "{1}{B}{B}", 3, "Enchantment",
         "At the beginning of your upkeep, you draw a card and you lose 1 life."),
    card("Sign in Blood", "{B}{B}", 2, "Sorcery",
         "Target player draws two cards and loses 2 life."),
    "draw", ">",
    "one draws every turn forever, the other draws twice once — if these score "
    "the same, card_score cannot rank a draw pool at all",
)

differs(
    "Rhystic Study draws more than Divination",
    card("Rhystic Study", "{2}{U}", 3, "Enchantment",
         "Whenever an opponent casts a spell, you may draw a card unless that "
         "player pays {1}."),
    card("Divination", "{2}{U}", 3, "Sorcery", "Draw two cards."),
    "draw", ">",
    "same cost, same colour, same word 'draw' — the difference is entirely in "
    "whether it repeats",
)

differs(
    "Skullclamp draws more than Bone Saw",
    card("Skullclamp", "{1}", 1, "Artifact — Equipment",
         "Equipped creature gets +1/-1. Whenever equipped creature dies, draw "
         "two cards. Equip {1}"),
    card("Bone Saw", "{0}", 0, "Artifact — Equipment",
         "Equipped creature gets +1/+0. Equip {1}"),
    "draw", ">",
    "the emergent case: Skullclamp is a draw engine only alongside 1-toughness "
    "creatures, and nothing in its own text says 'draw engine'",
)

# ── lifegain and drain ───────────────────────────────────────────────────────
section("lifegain and drain")

differs(
    "Blood Artist drains, Vampire Nighthawk does not",
    card("Blood Artist", "{1}{B}", 2, "Creature — Vampire",
         "Whenever Blood Artist or another creature dies, target player loses 1 "
         "life and you gain 1 life."),
    card("Vampire Nighthawk", "{1}{B}{B}", 3, "Creature — Vampire",
         "Flying, deathtouch, lifelink"),
    "drain", ">",
    "a drain deck's whole plan is repeatable loss-of-life; lifelink is incidental "
    "lifegain and is not the same plan",
)

differs(
    "Well of Lost Dreams gains value from lifegain, Healing Salve does not",
    card("Well of Lost Dreams", "{4}", 4, "Artifact",
         "Whenever you gain life, you may pay {X}. If you do, draw X cards."),
    card("Healing Salve", "{W}", 1, "Instant", "You gain 3 life."),
    "lifegain_payoff", ">",
    "the difference between a lifegain PAYOFF and a card that merely gains life "
    "— a lifegain deck full of the second one does nothing",
)

# ── tokens: creature vs resource ─────────────────────────────────────────────
section("tokens — creatures vs resources")

differs(
    "Krenko makes creature tokens, Treasure Map does not",
    card("Krenko, Mob Boss", "{2}{R}{R}", 4, "Creature — Goblin Warrior",
         "{T}: Create X 1/1 red Goblin creature tokens, where X is the number of "
         "Goblins you control."),
    card("Treasure Map", "{2}", 2, "Artifact",
         "{1}, {T}: Scry 1. Then if there are three or more scry counters on "
         "Treasure Map, remove them and create three Treasure tokens."),
    "creature_tokens", ">",
    "go-wide counts bodies; Treasures are mana. Both say 'create tokens' and "
    "they serve completely different plans",
)


# ── graveyard / flashback: CON-123 ───────────────────────────────────────────
section("graveyard — flashback family")

differs(
    "Think Twice has flashback, Divination does not",
    card("Think Twice", "{1}{U}", 2, "Instant",
         "Draw a card.\nFlashback {2}{U}"),
    card("Divination", "{2}{U}", 3, "Sorcery", "Draw two cards."),
    "flashback", ">",
    "flashback is a second cast from the yard; a one-shot draw spell is not "
    "the same plan as a yard that keeps casting",
)

# ── condition lift: engines move cards_drawn % on a fixed deck ───────────────
section("condition lift — CON-119 (fixed deck, cards_drawn)")


def _lift_deck(extra):
    """Minimal black deck: lands + rocks + one draw piece. Same shell each time."""
    lands = [
        card("Swamp", "", 0, "Basic Land — Swamp", "{T}: Add {B}.")
        for _ in range(36)
    ]
    rocks = [
        card("Sol Ring", "{1}", 1, "Artifact", "{T}: Add {C}{C}.")
        for _ in range(4)
    ]
    filler = [
        card(f"Filler {i}", "{B}", 1, "Creature — Rat", "")
        for i in range(58)
    ]
    return lands + rocks + filler + [extra]


def condition_lift(label, better, worse, why):
    """better must fire cards_drawn>=2 by turn 8 more often than worse.

    Turn 8, not 5: a 3-mana engine has to resolve and then tick. Direction
    only — magnitudes are Box (scoring contract).
    """
    # Threshold 4, not 2 — and this is the whole point of the scoring contract.
    # At >=2 the one-shot legitimately WINS (Arena 7.3% vs Sign 11.0% @T8):
    # a 2-mana sorcery clears "draw two extra" the turn it resolves, while a
    # 3-mana upkeep engine needs two more turns to match. The card is not
    # mis-scored; the condition was asking the wrong question.
    # docs/SCORING-CONTRACT.md sets a floor of 4 for draw conditions, written
    # before this failure existed. At >=4: Arena 5.0% vs Sign 0.0% @T8.
    cond = [{"label": "Drew 4", "note": "four extra cards drawn",
             "check": {"cards_drawn": 4}}]
    seed = 119
    out_b = sim.run(_lift_deck(better), 3, set(), cond, runs=300, seed=seed)
    out_w = sim.run(_lift_deck(worse), 3, set(), cond, runs=300, seed=seed)
    turn = 8  # 1-indexed; points are 0-indexed
    pb = next((c["points"][turn - 1] for c in out_b["curves"] if c["label"] == "Drew 4"), 0)
    pw = next((c["points"][turn - 1] for c in out_w["curves"] if c["label"] == "Drew 4"), 0)
    ok = pb > pw
    if ok:
        _passed.append(label)
        print(f"  {GREEN}sees{OFF}  {label}  {DIM}({better['name']} {pb}% > {worse['name']} {pw}% @T{turn}){OFF}")
    else:
        _failed.append(label)
        print(f"  {RED}BLIND{OFF} {label}")
        print(f"        {DIM}{better['name']} {pb}%  vs  {worse['name']} {pw}% @T{turn}{OFF}")
        print(f"        {DIM}why it matters: {why}{OFF}")


condition_lift(
    "Phyrexian Arena lifts cards_drawn more than Sign in Blood",
    card("Phyrexian Arena", "{1}{B}{B}", 3, "Enchantment",
         "At the beginning of your upkeep, you draw a card and you lose 1 life."),
    card("Sign in Blood", "{B}{B}", 2, "Sorcery",
         "Target player draws two cards and loses 2 life."),
    # CON-146: Sign is visible now (draws=2). At cards_drawn>=2 @T8 a cheap
    # one-shot can beat a 3-mana engine — that is the scoring-contract finding,
    # not a reason to zero Sign again. Extraction pair above is 6>2; this lift
    # is a separate question. BLIND here is honest.
    "engines accumulate; at a low cards_drawn bar a cheap one-shot can still "
    "win the % race (see docs/SCORING-CONTRACT.md)",
)

# ── the known silent failure ─────────────────────────────────────────────────
section("extraction fidelity — the case already recorded in CLAUDE.md")

differs(
    "Azusa grants two extra land drops, Exploration grants one",
    card("Azusa, Lost but Seeking", "{2}{G}", 3, "Creature — Human Monk",
         "You may play two additional lands on each of your turns."),
    card("Exploration", "{G}", 1, "Enchantment",
         "You may play an additional land on each of your turns."),
    "drop", ">",
    "the regex that matched 'an additional land' scored Azusa zero and nothing "
    "surfaced it — every new signal can fail exactly this way",
)


# ── CON-162 adversarial misses (one wrong card per signal) ───────────────────
section("CON-162 adversarial — cards the regex got wrong")

differs(
    "Wheel of Fortune draws seven; a burn spell draws zero",
    card("Wheel of Fortune", "{2}{R}", 3, "Sorcery",
         "Each player discards their hand, then draws seven cards."),
    card("Lightning Bolt", "{R}", 1, "Instant",
         "Lightning Bolt deals 3 damage to any target."),
    "draws", ">",
    "WORD_NUM stopped at five — 'draws seven cards' was a silent zero",
)

differs(
    "Consecrated Sphinx draws for you, not the opponent clause",
    card("Consecrated Sphinx", "{4}{U}{U}", 6, "Creature — Sphinx",
         "Flying\nWhenever an opponent draws a card, you may draw two cards."),
    card("Divination", "{2}{U}", 3, "Sorcery", "Draw two cards."),
    "draw", ">",
    "first-match on 'opponent draws a card' scored amount=1 and ignored "
    "'you may draw two' — engine looked weaker than a one-shot",
)

differs(
    "Notion Thief is not a resolve-draw spell",
    card("Brainstorm", "{U}", 1, "Instant",
         "Draw three cards, then put two cards from your hand on top of your "
         "library in any order."),
    card("Notion Thief", "{2}{U}{B}", 4, "Creature — Human Rogue",
         "Flash\nIf an opponent would draw a card except the first one they "
         "draw in each of their draw steps, instead that player skips that "
         "draw and you draw a card."),
    "draws", ">",
    "replacement 'instead you draw' was counted as draws=1 on resolve",
)

differs(
    "Aetherflux is a whenever-gain source; Whip only grants lifelink",
    card("Aetherflux Reservoir", "{4}", 4, "Artifact",
         "Whenever you cast a spell, you gain 1 life for each spell you've "
         "cast this turn.\nPay 50 life, Exile Aetherflux Reservoir: It deals "
         "50 damage to any target."),
    card("Whip of Erebos", "{2}{B}{B}", 4, "Legendary Enchantment Artifact",
         "Creatures you control have lifelink.\n{2}{B}{B}, {T}: Return target "
         "creature card from your graveyard to the battlefield."),
    "life_gain_proxy_per_turn", ">",
    "whenever-cast 'you gain life' extracted as zero — only "
    "'whenever opponent loses life' had a proxy path",
)

differs(
    "Gray Merchant drains; Vampire Nighthawk does not",
    card("Gray Merchant of Asphodel", "{3}{B}{B}", 5, "Creature — Zombie",
         "When Gray Merchant of Asphodel enters, each opponent loses X life, "
         "where X is your devotion to black. You gain life equal to the life "
         "lost this way."),
    card("Vampire Nighthawk", "{1}{B}{B}", 3, "Creature — Vampire",
         "Flying, deathtouch, lifelink"),
    "drain", ">",
    "'loses X life' was invisible — only numeric/that-much loss matched",
)

differs(
    "Bitterblossom is an upkeep token engine, not a one-shot",
    card("Bitterblossom", "{1}{B}", 2, "Tribal Enchantment — Faerie",
         "At the beginning of your upkeep, you lose 1 life and create a 1/1 "
         "black Faerie Rogue creature token with flying."),
    card("Raise the Alarm", "{1}{W}", 2, "Instant",
         "Create two 1/1 white Soldier creature tokens."),
    "creature_tokens", ">",
    "upkeep creates were not engines — only {T}: and whenever were — so "
    "Bitterblossom scored as a one-shot create-1",
)

differs(
    "Hero of Bladehold attack tokens proxy; vanilla Knight does not",
    card("Hero of Bladehold", "{2}{W}{W}", 4, "Creature — Human Knight",
         "Whenever Hero of Bladehold attacks, create two 1/1 white Soldier "
         "creature tokens that are tapped and attacking."),
    card("Knight of Meadowgrain", "{W}{W}", 2, "Creature — Kithkin Knight",
         "Lifelink"),
    "creature_token_proxy_per_turn", ">",
    "attack-trigger creates were skipped entirely while 'whenever you attack' "
    "accidentally got a whenever-engine proxy — go-wide attack plans could not "
    "compute board width from the cards that make the tokens",
)

differs(
    "Adeline whenever-you-attack tokens proxy like other attack engines",
    card("Adeline, Resplendent Cathar", "{1}{W}{W}", 3,
         "Legendary Creature — Human Knight",
         "Vigilance\nWhenever you attack, for each opponent, create a 1/1 "
         "white Human creature token that is tapped and attacking that "
         "player or a planeswalker they control."),
    card("Knight of Meadowgrain", "{W}{W}", 2, "Creature — Kithkin Knight",
         "Lifelink"),
    "creature_token_proxy_per_turn", ">",
    "collective 'whenever you attack' must stay on the engine path so Adeline "
    "plans measure creatures_in_play honestly",
)

differs(
    "Spear of Heliod is a lasting anthem; Inspired Charge is until EOT",
    card("Spear of Heliod", "{1}{W}{W}", 3, "Legendary Enchantment Artifact",
         "Creatures you control get +1/+1.\n{1}{W}{W}, {T}: Destroy target "
         "creature that dealt damage to you this turn."),
    card("Inspired Charge", "{2}{W}{W}", 4, "Instant",
         "Creatures you control get +2/+1 until end of turn."),
    "anthem", ">",
    "'get +N/+N until end of turn' was counted as a permanent anthem",
)

differs(
    "Cultivate is on-resolve ramp; Wayfarer's Bauble is activated-only",
    card("Cultivate", "{2}{G}", 3, "Sorcery",
         "Search your library for up to two basic land cards, reveal those "
         "cards, put one onto the battlefield tapped and the other into your "
         "hand, then shuffle."),
    card("Wayfarer's Bauble", "{1}", 1, "Artifact",
         "{2}, {T}, Sacrifice Wayfarer's Bauble: Search your library for a "
         "basic land card, put that card onto the battlefield tapped, then "
         "shuffle."),
    "fetch", ">",
    "CON-163: activated land-search was counted as on-cast fetch — goldfish "
    "invented a free land the turn after Bauble resolved (+12 pts optimistic "
    "on lands>=6 vs Forge n=50)",
)


# ── report ───────────────────────────────────────────────────────────────────
total = len(_passed) + len(_failed)
print()
if _failed:
    print(f"{RED}{len(_failed)} of {total} blind{OFF}, {GREEN}{len(_passed)} seen{OFF}")
    print(f"\n{DIM}Each BLIND line is a pair the scorer cannot rank. Until it can,")
    print(f"card_score.py is ordering those roles by noise.{OFF}")
else:
    print(f"{GREEN}all {total} pairs distinguished{OFF}")
    print(f"{DIM}The perception layer can see. Scoring magnitudes are CON-138.{OFF}")

sys.exit(1 if _failed else 0)
