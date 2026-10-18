"""
sim.py — instrumented goldfish. No opponent.

Two jobs:
  1. Read Scryfall oracle text and work out what a card mechanically DOES.
  2. Play thousands of solitaire games and report how often the deck's stated
     plan actually comes together, and by which turn.

The conditions come from a constrained DSL so the model can define the thesis
without emitting code. See THESIS_KEYS for what's checkable.
"""

import re, random

MAX_TURN = 10
SAMPLE_DRAW_CAP = 5  # opening seven plus draws through this turn for sample hands

# bump when play_one / mechanics / THESIS_KEYS change what the numbers
# mean. Saved decks stamp their baseline with this; a mismatch forces recompute
# so we never serve odds from an engine we no longer run.
SIM_VERSION = 11  # attack-trigger token creates get a once-per-turn goldfish proxy

THESIS_KEYS = [
    "commander_in_play",     # bool
    "mana_available",        # >= n
    "payoffs_in_play",       # >= n
    "extra_land_drops",      # >= n
    "lands_in_play",         # >= n
    "land_drops_this_turn",  # >= n
    "cards_in_hand",         # >= n
    "cards_drawn",           # >= n  cards drawn beyond the once-per-turn draw
    "creatures_in_play",     # >= n  bodies on the battlefield, tokens included
    "board_power",           # >= n  bodies scaled by anthem effects
    "life_gained",           # >= n  life you gained (sources + goldfish proxies)
    "life_drained",          # >= n  opponent life lost to drain effects
]

# stated next to the curves. We do not model commander tax — nothing
# removes the commander in a goldfish, so a recast never happens. Modelling the
# tax without a threat would invent a punishment the simulation cannot produce.
# Colour requirements ARE modelled; do not claim "no colour screw."
ASSUMPTIONS = (
    "No opponent, no removal, no blockers. Your commander is cast once and "
    "stays — real games tax every recast by 2 more. On cast, extractable "
    "commander effects fire the same way as the 99 (draws, drain proxies, "
    "bodies). Attack-trigger token makers create once per your turn once in "
    "play — the goldfish assumes you attack; it does not resolve combat "
    "damage. Life exchange with an opponent is not modelled — there is no "
    "opponent. Activated land-search (Wayfarer's Bauble) is not counted as "
    "free ramp. This answers whether the deck gets there on its own, not "
    "whether it survives contact."
)

WUBRG = ("W", "U", "B", "R", "G")

# ---------------------------------------------------------------- extraction

WORD_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "x": 1,
}
# "that many" floors to 1 — same Azusa-class rule as X. Magnitude is
# game-state-dependent; silently returning 0 because the regex missed the
# phrase invents a dead engine (Mister Negative, variable ETB draw).
_DRAW_NUM = (
    r"(a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that many)"
)


def parse_cost(cost: str) -> dict:
    """Parse Scryfall mana_cost into colored pips and generic."""
    pips = {c: 0 for c in WUBRG}
    pips["generic"] = 0
    if not cost:
        return pips
    for sym in re.findall(r"\{([^}]+)\}", cost):
        if sym in pips:
            pips[sym] += 1
        elif sym.isdigit():
            pips["generic"] += int(sym)
        elif sym == "C":
            pips["generic"] += 1
    return pips


def effective_cost(card: dict) -> dict:
    """Pips to pay. Cards without a cost string fall back to generic CMC."""
    pips = dict(card.get("cost_pips") or parse_cost(""))
    if sum(pips.values()) == 0 and card.get("cmc", 0) > 0:
        pips["generic"] = int(card["cmc"])
    return pips


def source_colors(card: dict) -> set:
    """Colors a land or rock can produce when tapped."""
    pm = card.get("produced_mana")
    if pm:
        if pm == ["C"] or pm == ["c"]:
            return {"C"}
        return set(pm)
    t = (card.get("text") or "").lower()
    if "any color" in t or "commander's color" in t:
        return {"ANY"}
    colors = set()
    for sym, c in [("{w}", "W"), ("{u}", "U"), ("{b}", "B"), ("{r}", "R"), ("{g}", "G")]:
        if sym in t:
            colors.add(c)
    if "{c}" in t and not colors:
        colors.add("C")
    return colors


def mechanics(card: dict, payoff_names: set) -> dict:
    """Derive simulation-relevant mechanics from oracle text."""
    t = (card.get("text") or "").lower()
    tl = (card.get("type") or "").lower()
    m = {"land": "land" in tl, "cmc": int(card.get("cmc") or 0),
         "produces": 0, "fetch": 0, "drop": 0,
         "payoff": card["name"] in payoff_names,
         "cost_pips": parse_cost(card.get("cost") or "")}

    if not m["land"]:
        # "Add {C}{C}" -> count the symbols in the largest single Add clause
        syms = re.findall(r"add ((?:\{[^}]+\}\s*)+)", t)
        if syms:
            m["produces"] = max(len(re.findall(r"\{[^}]+\}", s)) for s in syms)
        else:
            w = re.search(r"add (one|two|three) mana", t)
            if w:
                m["produces"] = WORD_NUM[w.group(1)]

        # land ramp: lands that enter the battlefield from a library search
        # on resolve / ETB. Activated-only searches ({T}/Sacrifice:…) are not
        # on-cast — the goldfish has no activation step, and counting them on
        # cast invents a free land (Wayfarer's Bauble drove lands>=6
        # +12 pts optimistic vs Forge n=50).
        land_search = (
            re.search(r"search your library for .{0,80}?land", t)
            or re.search(
                r"search your library for (?:up to )?(?:\w+ ){0,4}"
                r"(?:forest|island|plains|swamp|mountain|wastes)",
                t,
            )
        )
        activated_fetch = bool(re.search(
            r"(?:\{t\}|sacrifice [^.:]{1,60}):[^.]*search your library for",
            t,
        ))
        if land_search and "onto the battlefield" in t and not activated_fetch:
            fetch = 0
            for put in re.finditer(
                r"put (one|two|three|four) (?:\w+ )*onto the battlefield", t
            ):
                fetch += WORD_NUM[put.group(1)]
            if re.search(r"put them onto the battlefield", t):
                fetch = max(fetch, 2)
            if re.search(r"put (?:that card|it) onto the battlefield", t):
                fetch = max(fetch, 1)
            m["fetch"] = fetch

    # extra land drops. Note the plural: Azusa reads "play two additional lands",
    # so matching only "an additional land" silently misses the best enablers.
    d = re.search(r"play (an|one|two|three|four) additional lands?", t)
    if d:
        m["drop"] = WORD_NUM.get(d.group(1), 1)

    # Card draw — eyes, not a dictionary; includes "target player draws" phrasing.
    #
    # Three fields:
    #   draws        — cards drawn on resolution (Divination, cantrips)
    #   draw_upkeep  — cards drawn each upkeep while this permanent is in play
    #   draw         — perception *rate* for ranking engines vs one-shots
    #                  (test_perception.py key). Upkeep/triggers beat one-shots.
    #
    # Goldfish ownership:
    #   - "You draw" / bare "Draw N cards" → yours.
    #   - "Target player draws" → yours. You cast it; you choose yourself.
    #     Black's draw suite is written this way (Sign in Blood). Scoring 0
    #     made Arena-vs-Sign pass as 6>0 — wrong reason.
    #   - "Each player draws" → yours for your seat.
    #   - "Target opponent draws" / "each opponent draws" → not yours (0).
    #
    # X: floor of 1. Magnitude is mana-dependent; silently returning 0 because
    # the regex missed X is the Azusa class of bug. Floor marks "this draws"
    # without inventing X=3.
    m["draws"] = 0
    m["draw_upkeep"] = 0
    m["draw_proxy_per_turn"] = 0
    m["draw"] = 0

    def _word_n(raw: str) -> int:
        if raw in ("x", "that many"):
            return 1  # deliberate floor, not a miss
        return WORD_NUM.get(raw, 1)

    def _owned_draw_amount(text: str) -> int:
        """Best draw count that is yours — not an opponent's clause.

        Consecrated Sphinx says "whenever an opponent draws a card,
        you may draw two cards." Taking the first `draws?` hit scored the
        opponent's one-card draw and ignored yours. Scan every clause.
        """
        best = 0
        for hit in re.finditer(
                rf"\bdraws? {_DRAW_NUM} cards?\b", text):
            # Prefix of this match decides ownership.
            prefix = text[max(0, hit.start() - 48):hit.start()]
            if re.search(r"(?:target |each |an? )?opponent\s*$", prefix):
                continue
            # Replacement effects (Notion Thief): both the "would draw" clause
            # and the "instead … you draw" clause are not resolve draws.
            if re.search(r"would\s*$", prefix):
                continue
            window = text[max(0, hit.start() - 80):hit.end()]
            if re.search(r"\binstead\b", window) and re.search(
                    r"\bwould draw\b", text):
                continue
            best = max(best, _word_n(hit.group(1)))
        return best

    amount = _owned_draw_amount(t)
    if amount:
        upkeep = bool(re.search(
            r"at the beginning of (?:your |each (?:player's |opponent's )?)?upkeep",
            t,
        ))
        triggered = bool(re.search(r"\bwhenever\b", t))
        # Death/equip convert (Skullclamp-class) — still a whenever-engine.
        if upkeep:
            m["draw_upkeep"] = amount
            # Rate ≈ one upkeep per turn for the rest of a 10-turn goldfish.
            m["draw"] = amount * 6
        elif triggered:
            # Goldfish has no opponent/combat; perception still ranks the engine
            # above a one-shot of the same word. Proxy rate is conservative.
            m["draw"] = amount * 5
            m["draw_proxy_per_turn"] = amount
        else:
            m["draws"] = amount
            m["draw"] = amount


    # Graveyard / flashback-family. Extraction rates for eyes;
    # flashback's second cast feeds existing cards_drawn (no new THESIS_KEY —
    # delirium type-counts need a reviewed THESIS_KEY before they become conditions).
    kw = " ".join(card.get("keywords") or []).lower()
    m["flashback"] = 0
    m["flashback_draws"] = 0
    m["self_mill"] = 0
    m["gy_return"] = 0
    m["delirium"] = 0
    has_fb = "flashback" in kw or bool(re.search(r"\bflashback\b", t))
    if has_fb:
        m["flashback"] = 5
        # One-shot draw spells with flashback cast twice in a goldfish.
        if amount and not m.get("draw_upkeep") and not m.get("draw_proxy_per_turn"):
            m["flashback_draws"] = amount
            m["draw"] = amount * 2
    mill = re.search(
        r"\b(?:mill (a|one|two|three|four|five|\d+) cards?"
        r"|put the top (a|one|two|three|four|five|\d+) cards? of "
        r"(?:your|their|his or her) library into (?:your|their) graveyard)",
        t,
    )
    if mill:
        raw = mill.group(1) or mill.group(2)
        if raw and str(raw).isdigit():
            m["self_mill"] = int(raw)
        else:
            m["self_mill"] = WORD_NUM.get(raw or "a", 1)
    if re.search(
            r"return .{0,80} from (?:your )?graveyard to the battlefield", t):
        m["gy_return"] = 1
    if ("delirium" in kw or "delirium" in t
            or re.search(
                r"four or more card types among cards in (?:your )?graveyard",
                t)):
        m["delirium"] = 5

    # Lifegain / drain. Oracle text, not taxonomy keywords.
    # Taxonomy only has extort/lifelink; Blood Artist vs Nighthawk and Well vs
    # Salve are invisible to a keyword map. Perception keys:
    #   drain            — rate: repeatable opponent life loss (Artist, Bond)
    #   lifegain_payoff  — rate: value WHEN you gain life (Well, Bond)
    #   life_gain        — one-shot gain on resolve (Salve)
    #   life_gain_proxy_per_turn / drain_proxy_per_turn — goldfish whenever stand-ins
    #
    # Lifelink alone is NOT drain and NOT a lifegain_payoff (no combat modelled).
    # Sulfuric Vortex ("would gain life… gains no life") must stay 0 — match
    # positive shapes only, never bare "gain life".
    # Exquisite Blood watches loss → gains life: source/proxy, not drain.
    m["drain"] = 0
    m["lifegain_payoff"] = 0
    m["life_gain"] = 0
    m["life_gain_proxy_per_turn"] = 0
    m["drain_amount"] = 0
    m["drain_proxy_per_turn"] = 0

    if not re.search(r"would gain life|gains? no life instead", t):
        whenever = bool(re.search(r"\bwhenever\b", t))
        # One-shot gain only — not text inside a whenever trigger.
        if not whenever:
            g = re.search(
                r"you gain (\d+) life|you gain (that much) life|you gain life equal",
                t,
            )
            if g:
                if g.group(1):
                    m["life_gain"] = int(g.group(1))
                else:
                    m["life_gain"] = 1  # that much / equal — floor 1
            else:
                g2 = re.search(r"\bgain (\d+) life\b", t)
                if g2 and "opponent" not in t:
                    m["life_gain"] = int(g2.group(1))

        # Payoff: fires when life is gained (Well of Lost Dreams, Sanguine Bond).
        is_gain_payoff = bool(re.search(r"whenever (?:you|a player) gains? life", t))
        if is_gain_payoff:
            m["lifegain_payoff"] = 5

        # Exquisite Blood-class: whenever an opponent loses life, you gain…
        if re.search(r"whenever (?:an? )?opponent loses life", t):
            m["life_gain_proxy_per_turn"] = 1
            # Not drain — this card does not cause the loss.

        # whenever-triggered gain that is NOT a lifegain-payoff watch
        # (Aetherflux, Authority of the Consuls). Without this, engines that
        # say "whenever you cast / a creature enters, you gain life" extract
        # as zero — the Azusa miss for life sources.
        if whenever and not is_gain_payoff and not m["life_gain_proxy_per_turn"]:
            if re.search(r"you gain (?:\d+|that much) life|you gain life equal", t):
                m["life_gain_proxy_per_turn"] = 1

        # Drain: you cause opponent/player life loss (Artist, Bond, Extort…).
        # Exclude pure "whenever opponent loses life, you gain" watchers.
        # "loses X life" (Gray Merchant) is a real drain amount — X
        # floors to 1 the same way draw-X does.
        causes_loss = bool(re.search(
            r"(?:target (?:player|opponent)|each opponent|opponent) loses?"
            r"(?: \d+| that much| x)? life"
            r"|loses (?:that much|\d+|x) life"
            r"|loses life equal",
            t,
        )) and not re.search(
            r"whenever (?:an? )?opponent loses life, you gain",
            t,
        )
        if causes_loss:
            lose_n = re.search(r"loses? (?:that much|(\d+)|x|life equal)", t)
            if lose_n and lose_n.group(1):
                n = int(lose_n.group(1))
            else:
                n = 1
            if whenever:
                m["drain"] = n * 5
                m["drain_proxy_per_turn"] = n
                # Artist: "loses 1 life and you gain 1 life" on the same trigger.
                if re.search(r"loses?\b.{0,40}\byou gain\b", t):
                    m["life_gain_proxy_per_turn"] = n
            else:
                m["drain"] = n
                m["drain_amount"] = n

    # Bodies on the battlefield. The smallest possible board — a count, not a
    # combat model. Most Commander decks are built around having creatures, and
    # the simulation had no concept of one.
    #
    # Treasure/Clue/Food are resource tokens — mana/card/life, not
    # bodies. Creature-token creation feeds creatures_in_play; resource tokens
    # must not.
    #
    # Attack-trigger creates ("whenever ~ attacks" / "whenever you attack")
    # used to be skipped as "needs combat." That left collective "whenever you
    # attack" cards accidentally on the whenever-engine path while "whenever
    # this attacks" cards contributed only their own body — so Adeline's plan
    # could compute board width and Hero of Bladehold's could not. Goldfish
    # assumption: once the card is in play, you attack each turn. Proxy N
    # tokens per turn; do not resolve combat damage or blockers.
    #
    # Perception keys (rates for ranking, like drain / lifegain_payoff):
    #   creature_tokens / resource_tokens
    # Goldfish stand-ins:
    #   creature_token_proxy_per_turn — fixed N per turn once in play
    #   creature_token_x_proxy — Krenko-class: create X where X ≈ bodies
    m["bodies"] = 1 if ("creature" in (card.get("type") or "").lower()
                        and "land" not in (card.get("type") or "").lower()) else 0
    m["creature_tokens"] = 0
    m["resource_tokens"] = 0
    m["creature_token_proxy_per_turn"] = 0
    m["creature_token_x_proxy"] = 0

    activated = bool(re.search(r"\{t\}:", t))
    whenever = bool(re.search(r"\bwhenever\b", t))
    # upkeep creates (Bitterblossom) are engines, not one-shots.
    # Matching only {T}/whenever scored Bitterblossom as a one-shot create-1.
    upkeep = bool(re.search(
        r"at the beginning of (?:your |each (?:player's )?)?upkeep", t))
    engine = activated or whenever or upkeep
    # Keep this as a plain r-string: an rf-string would interpolate
    # `{0,80}` as a Python tuple and silently kill every token match.
    _tok_num = (
        r"a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|\d+"
    )
    for tok in re.finditer(
            r"creates? (?:(" + _tok_num + r") )?"
            r"([^.]{0,80}?tokens?)",
            t):
        raw = (tok.group(1) or "a").lower()
        rest = tok.group(2).lower()
        if raw.isdigit():
            n = int(raw)
        else:
            n = WORD_NUM.get(raw, 1)
        if n < 1:
            n = 1
        is_resource = bool(re.search(
            r"\b(?:treasure|clue|food|gold|blood)\b", rest))
        is_creature = (not is_resource) and bool(
            re.search(r"\bcreature\b", rest) or re.search(r"\d+/\d+", rest)
        )
        if is_resource:
            m["resource_tokens"] += n * (5 if engine else 1)
            continue
        if not is_creature:
            # "create a token" with no type — refuse to guess; not bodies.
            continue
        if engine:
            m["creature_tokens"] += n * 5
            if raw == "x" or re.search(r"\bwhere x is\b", t):
                m["creature_token_x_proxy"] = 1
            else:
                m["creature_token_proxy_per_turn"] += n
        else:
            m["creature_tokens"] += n
            m["bodies"] += n

    # Anthems. Worth nothing alone and a lot beside bodies, which is exactly
    # the interaction a card-by-card score cannot see — so it is the mechanic
    # that most repays teaching.
    #
    # "Creatures you control get +N/+N" only. A pump aimed at one creature is a
    # combat trick, and combat is not modelled.
    # "until end of turn" anywhere in the same clause is a combat
    # trick (Elesh Norn activated), not a lasting anthem. Negative lookahead
    # right after +N/+N misses "get +1/+1 and have flying until end of turn".
    m["anthem"] = 0
    for a in re.finditer(r"creatures you control get \+(\d+)/\+\d+", t):
        end = t.find(".", a.start())
        clause = t[a.start(): end if end >= 0 else len(t)]
        if "until end of turn" in clause:
            continue
        m["anthem"] = max(m["anthem"], int(a.group(1)))

    m["mana_colors"] = source_colors(card) if m["land"] or m["produces"] else set()
    return m


def prep(deck: list, payoff_names: set) -> list:
    return [dict(c, **mechanics(c, payoff_names)) for c in deck]


# ---------------------------------------------------------------- mana matching

def _source_pays_pip(source: set, color: str) -> bool:
    return "ANY" in source or color in source


def can_pay(pips: dict, sources: list) -> bool:
    """Can a subset of sources pay this mana cost? Each source taps once."""
    avail = list(sources)
    for color in WUBRG:
        for _ in range(pips.get(color, 0)):
            i = next((i for i, s in enumerate(avail) if _source_pays_pip(s, color)), None)
            if i is None:
                return False
            avail.pop(i)
    for _ in range(pips.get("generic", 0)):
        if not avail:
            return False
        avail.pop()
    return True


def consume(pips: dict, sources: list) -> list | None:
    """Tap sources to pay. Returns remaining sources, or None if unpayable."""
    avail = list(sources)
    for color in WUBRG:
        for _ in range(pips.get(color, 0)):
            i = next((i for i, s in enumerate(avail) if _source_pays_pip(s, color)), None)
            if i is None:
                return None
            avail.pop(i)
    for _ in range(pips.get("generic", 0)):
        if not avail:
            return None
        avail.pop()
    return avail


def _fetch_land_colors(card: dict, deck_lands: list) -> set:
    """Best guess at what a fetched land produces."""
    t = (card.get("text") or "").lower()
    for name, color in [("forest", "G"), ("island", "U"), ("plains", "W"),
                        ("swamp", "B"), ("mountain", "R")]:
        if name in t:
            return {color}
    if deck_lands:
        return source_colors(deck_lands[0])
    return {"ANY"}


# ---------------------------------------------------------------- the game

def _keep(hand):
    l = sum(1 for c in hand if c["land"])
    return 2 <= l <= 5


def _card_entry(card, draw_turn):
    return {"name": card["name"], "draw_turn": draw_turn, "played_turn": None,
            "_land": card["land"]}


def _public_hand(log):
    """Opening seven plus draws through SAMPLE_DRAW_CAP, in deal order."""
    out = []
    for i, e in enumerate(log):
        if i >= 7 and e["draw_turn"] > SAMPLE_DRAW_CAP:
            break
        out.append({"name": e["name"], "draw_turn": e["draw_turn"],
                    "played_turn": e["played_turn"]})
    return out


def _earliest_hit(fired):
    turns = [t for t in fired.values() if t is not None]
    return min(turns) if turns else None


def _stall_score(log):
    """Higher = a more honest bad hand (few lands, nothing castable)."""
    opening = log[:7]
    lands = sum(1 for e in opening if e["_land"])
    spells_by_5 = sum(1 for e in log
                      if not e["_land"] and e["played_turn"] is not None
                      and e["played_turn"] <= 5)
    score = 0
    if lands == 2:
        score += 1000
    elif lands in (1, 3):
        score += 600 - abs(lands - 2) * 150
    score += (5 - spells_by_5) * 50
    return score


def play_one(cards, cmdr_cmc, conditions, commander_mech=None):
    """Play one goldfish game.

    Returns (fired, deal_log, curve_trace). curve_trace feeds curve.py —
    per-turn "did she act" and missed land drops — without changing what the
    thesis curves mean. Third element is additive; callers that only need
    fired/log keep working by ignoring it.

    when `commander_mech` is the precomputed mechanics() profile for
    the commander, on-cast extractable effects apply the same as casting a 99
    card. Opponent-only text (life exchange) stays inert — no opponent invented.
    """
    lib = cards[:]
    random.shuffle(lib)
    hand, mulls = [], 0
    while True:
        random.shuffle(lib)
        hand = lib[:7]
        if _keep(hand) or mulls >= 3:
            break
        mulls += 1
    lib = lib[7:]
    for _ in range(mulls):                       # London: bottom the worst card
        nonland = [c for c in hand if not c["land"]]
        hand.remove(max(nonland, key=lambda c: c["cmc"]) if nonland else hand[0])

    deal_log = [_card_entry(c, 1) for c in hand]
    by_id = {id(c): deal_log[i] for i, c in enumerate(hand)}
    board_land_sources = []
    board_rock_sources = []
    pending_land_sources = []                    # fetched; enter tapped
    extra = payoffs = drawn_extra = bodies = anthem = 0
    life_gained = life_drained = 0
    cmdr = False
    # Permanents that draw on upkeep / goldfish proxies for whenever-engines.
    board_draw_upkeep: list[int] = []
    board_draw_proxy: list[int] = []
    board_life_gain_proxy: list[int] = []
    board_drain_proxy: list[int] = []
    board_lifegain_payoffs = 0  # Well-class: draw when life is gained
    board_creature_token_proxy: list[int] = []
    board_creature_token_x = 0  # Krenko-class makers in play
    # Flashback second casts: fire once on a later turn (goldfish always pays).
    pending_flashback_draws: list[int] = []
    fired = {c["label"]: None for c in conditions}
    deck_lands = [c for c in cards if c["land"]]
    # curve measurement. Land-drop opportunities = drops she was
    # entitled to; missed = entitlement she could not take (no land in hand).
    # acted_turns = turns she cast a non-land spell (or the commander).
    missed_land_drops = 0
    land_drop_opportunities = 0
    acted_turns: list[int] = []

    def _take_draws(n: int, turn: int) -> None:
        nonlocal drawn_extra
        for _ in range(n):
            if not lib:
                return
            got = lib.pop()
            hand.append(got)
            entry = _card_entry(got, turn)
            deal_log.append(entry)
            by_id[id(got)] = entry
            drawn_extra += 1

    def _resolve_cast(c: dict, turn: int, *, count_payoff: bool = True) -> None:
        """Apply on-cast / ETB extractable fields for a card or the commander."""
        nonlocal extra, life_gained, life_drained, bodies, anthem, payoffs
        nonlocal board_lifegain_payoffs, board_creature_token_x
        if c.get("produces"):
            board_rock_sources.append(c["mana_colors"])
        for _ in range(int(c.get("fetch") or 0)):
            pending_land_sources.append(_fetch_land_colors(c, deck_lands))
        extra += int(c.get("drop") or 0)
        _take_draws(int(c.get("draws") or 0), turn)
        if c.get("draw_upkeep"):
            board_draw_upkeep.append(int(c["draw_upkeep"]))
        if c.get("draw_proxy_per_turn"):
            board_draw_proxy.append(int(c["draw_proxy_per_turn"]))
        gained = int(c.get("life_gain") or 0)
        life_gained += gained
        if gained and board_lifegain_payoffs:
            _take_draws(board_lifegain_payoffs, turn)
        life_drained += int(c.get("drain_amount") or 0)
        if c.get("life_gain_proxy_per_turn"):
            board_life_gain_proxy.append(int(c["life_gain_proxy_per_turn"]))
        if c.get("drain_proxy_per_turn"):
            board_drain_proxy.append(int(c["drain_proxy_per_turn"]))
        if c.get("lifegain_payoff"):
            board_lifegain_payoffs += 1
        if c.get("creature_token_proxy_per_turn"):
            board_creature_token_proxy.append(
                int(c["creature_token_proxy_per_turn"]))
        if c.get("creature_token_x_proxy"):
            board_creature_token_x += 1
        if c.get("flashback_draws"):
            pending_flashback_draws.append(int(c["flashback_draws"]))
        if c.get("gy_return"):
            bodies += 1
        bodies += int(c.get("bodies") or 0)
        anthem += int(c.get("anthem") or 0)
        if count_payoff and c.get("payoff"):
            payoffs += 1

    for turn in range(1, MAX_TURN + 1):
        if turn > 1:
            board_land_sources.extend(pending_land_sources)
            pending_land_sources = []
            if lib:
                drawn = lib.pop()
                hand.append(drawn)
                entry = _card_entry(drawn, turn)
                deal_log.append(entry)
                by_id[id(drawn)] = entry
            # Upkeep draw engines (Arena) fire each turn they stay in play.
            for n in board_draw_upkeep:
                _take_draws(n, turn)
            # Whenever-engines: goldfish stand-in (no opponent/combat modelled).
            for n in board_draw_proxy:
                _take_draws(n, turn)
            for n in board_life_gain_proxy:
                life_gained += n
                if board_lifegain_payoffs:
                    _take_draws(board_lifegain_payoffs, turn)
            for n in board_drain_proxy:
                life_drained += n
                # Artist also gains on drain proxy when life_gain_proxy is set.
            for n in board_creature_token_proxy:
                bodies += n
            for _ in range(board_creature_token_x):
                # Create X where X ≈ bodies you control (goblins ≈ board).
                bodies += max(1, bodies)
            # Flashback from yesterday's cast — one second cast per queued spell.
            if pending_flashback_draws:
                for n in pending_flashback_draws:
                    _take_draws(n, turn)
                pending_flashback_draws.clear()

        drops = 1 + extra
        land_drop_opportunities += drops
        played = 0
        played_land_sources = []
        acted_this_turn = False
        while drops > 0:
            nxt = next((c for c in hand if c["land"]), None)
            if not nxt:
                missed_land_drops += drops
                break
            hand.remove(nxt)
            by_id[id(nxt)]["played_turn"] = turn
            played_land_sources.append(nxt["mana_colors"])
            played += 1
            drops -= 1
            if nxt["payoff"]:
                payoffs += 1

        available = board_land_sources + played_land_sources + board_rock_sources
        cmdr_pips = {"generic": cmdr_cmc, **{c: 0 for c in WUBRG}}

        # cast phase: acceleration first, then the plan. Dead cards stay in hand.
        while True:
            opts = [c for c in hand if not c["land"]
                    and (c["produces"] or c["fetch"] or c["drop"] or c["payoff"]
                         or c["draws"] or c.get("draw_upkeep")
                         or c.get("draw_proxy_per_turn")
                         or c.get("life_gain") or c.get("lifegain_payoff")
                         or c.get("drain") or c.get("drain_amount")
                         or c.get("life_gain_proxy_per_turn")
                         or c.get("drain_proxy_per_turn")
                         or c.get("creature_tokens")
                         or c.get("creature_token_proxy_per_turn")
                         or c.get("creature_token_x_proxy")
                         or c.get("flashback")
                         or c.get("flashback_draws")
                         or c.get("gy_return")
                         or c["bodies"] or c["anthem"])
                    and can_pay(effective_cost(c), available)]
            if not cmdr and can_pay(cmdr_pips, available):
                available = consume(cmdr_pips, available)
                cmdr = True
                payoffs += 1  # commander itself always counts as a payoff body
                acted_this_turn = True
                # run extractable commander oracle (draws/bodies/…);
                # count_payoff=False — the +1 above already covers the commander.
                if commander_mech:
                    _resolve_cast(commander_mech, turn, count_payoff=False)
                continue
            if not opts:
                break
            # rank: mana acceleration before payoffs, cheapest first
            opts.sort(key=lambda c: (0 if (c["produces"] or c["fetch"]
                                           or c["drop"] or c["draws"]
                                           or c.get("draw_upkeep")
                                           or c.get("draw_proxy_per_turn")
                                           or c.get("life_gain")
                                           or c.get("lifegain_payoff")
                                           or c.get("drain")
                                           or c.get("drain_amount")
                                           or c.get("life_gain_proxy_per_turn")
                                           or c.get("drain_proxy_per_turn")
                                           or c.get("creature_token_proxy_per_turn")
                                           or c.get("creature_token_x_proxy")) else 1,
                                     c["cmc"]))
            c = opts[0]
            hand.remove(c)
            by_id[id(c)]["played_turn"] = turn
            acted_this_turn = True
            available = consume(effective_cost(c), available)
            _resolve_cast(c, turn, count_payoff=True)

        board_land_sources.extend(played_land_sources)

        lands = len(board_land_sources) + len(pending_land_sources)
        rocks = len(board_rock_sources)
        state = {"commander_in_play": cmdr, "mana_available": lands + rocks,
                 "payoffs_in_play": payoffs, "extra_land_drops": extra,
                 "lands_in_play": lands, "land_drops_this_turn": played,
                 "cards_in_hand": len(hand), "cards_drawn": drawn_extra,
                 "creatures_in_play": bodies,
                 # Each body is worth 1 plus whatever the anthems add to it.
                 # This is a scale, not a damage number — there is no combat.
                 "board_power": bodies + bodies * anthem,
                 "life_gained": life_gained,
                 "life_drained": life_drained}

        if acted_this_turn:
            acted_turns.append(turn)

        for cond in conditions:
            if fired[cond["label"]] is not None:
                continue
            if all(state.get(k, 0) >= v if not isinstance(v, bool) else bool(state.get(k)) == v
                   for k, v in cond["check"].items()):
                fired[cond["label"]] = turn

    return fired, deal_log, {
        "acted_turns": acted_turns,
        "missed_land_drops": missed_land_drops,
        "land_drop_opportunities": land_drop_opportunities,
    }


def run(deck, cmdr_cmc, payoff_names, conditions, runs=3000, seed=None,
        commander_card=None):
    """Goldfish N games. seed makes the shuffle sequence reproducible.

    pass `commander_card` (Scryfall-shaped dict) so on-cast commander
    oracle applies. Omit it and commander cast only flips commander_in_play —
    the earlier behaviour, kept for callers that have no oracle.
    """
    rng_state = None
    if seed is not None:
        rng_state = random.getstate()
        random.seed(seed)
    try:
        payoffs = set(payoff_names or ())
        cards = prep(deck, payoffs)
        commander_mech = None
        if isinstance(commander_card, dict) and (
                commander_card.get("oracle_text") or commander_card.get("text")
                or commander_card.get("name")):
            # App cards use text/type; raw Scryfall uses oracle_text/type_line.
            # mechanics() only reads text/type — normalize so on-cast is not a
            # silent miss when a caller hands us the Scryfall shape.
            cmdr = dict(commander_card)
            if not (cmdr.get("text") or "").strip() and cmdr.get("oracle_text"):
                cmdr["text"] = cmdr["oracle_text"]
            if not (cmdr.get("type") or "").strip() and cmdr.get("type_line"):
                cmdr["type"] = cmdr["type_line"]
            names = set(payoffs)
            if cmdr.get("name"):
                names.add(cmdr["name"])
            commander_mech = mechanics(cmdr, names)
        hits = {c["label"]: [0] * (MAX_TURN + 1) for c in conditions}
        best_good, best_bad = None, None
        for _ in range(runs):
            f, log, _trace = play_one(
                cards, cmdr_cmc, conditions, commander_mech=commander_mech)
            for label, t in f.items():
                if t is not None:
                    for k in range(t, MAX_TURN + 1):
                        hits[label][k] += 1
            hit = _earliest_hit(f)
            if hit is not None and (best_good is None or hit < best_good[0]):
                best_good = (hit, log)
            stall = _stall_score(log)
            if best_bad is None or stall > best_bad[0]:
                best_bad = (stall, log)

        curves = []
        for c in conditions:
            pts = [round(hits[c["label"]][t] / runs * 100, 1)
                   for t in range(1, MAX_TURN + 1)]
            med = next((i + 1 for i, p in enumerate(pts) if p >= 50), None)
            row = {"label": c["label"], "note": c.get("note", ""),
                   "points": pts, "median_turn": med}
            by_turn = c.get("by_turn")
            if isinstance(by_turn, int) and 1 <= by_turn <= MAX_TURN:
                row["by_turn"] = by_turn
            curves.append(row)
        out = {"runs": runs, "curves": curves,
               "diagnostics": diagnose(cards),
               "assumptions": ASSUMPTIONS}
        if best_good:
            out["good_hand"] = _public_hand(best_good[1])
        if best_bad:
            out["bad_hand"] = _public_hand(best_bad[1])
        return out
    finally:
        if rng_state is not None:
            random.setstate(rng_state)


def diagnose(cards):
    """The counts that actually move the curves."""
    return {
        "lands": sum(1 for c in cards if c["land"]),
        "mana_rocks": sum(1 for c in cards if c["produces"]),
        "land_ramp": sum(1 for c in cards if c["fetch"]),
        "extra_drops": sum(1 for c in cards if c["drop"]),
        "payoffs": sum(1 for c in cards if c["payoff"]),
        "avg_cmc": round(sum(c["cmc"] for c in cards if not c["land"]) /
                         max(1, sum(1 for c in cards if not c["land"])), 2),
    }
