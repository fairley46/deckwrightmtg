"""
coverage.py — can this deck actually be built from what she asked for?

She names a constraint on the deck page: "only Spider-Man cards", "nothing over
five dollars". The commander is already chosen, so the question is never "which
commander suits this" — it is arithmetic inside a fixed colour identity:

    how many cards does that pool have
    how many survive her commander's colours
    how many of the 99 does that fill, and what fills the rest

**No model in this file.** The app once offered to rebuild a deck as a
Spider-Man villains tribal list. That deck cannot exist: 70 legal cards in
white-black against 99 slots, on four ramp pieces. It sounded completely
reasonable, because prose about a card pool is easy and counting it is not.
Every number here comes from Scryfall or from the deck itself.

## The rule this exists to enforce

Coverage is stated *before* a rebuild, never after. If she presses the button
and gets 70 of 99, she should have known it was 70 of 99 before she pressed.

## What it does not do

It does not build anything and it does not judge. "Four ramp is thin" is a
reading of the numbers; this file only reports that there are four.
"""

import re

# Sets you can actually put in a deck. Scryfall also publishes art series,
# tokens, promos and memorabilia under names that match a search perfectly —
# "Marvel's Spider-Man Art Series" is 54 cards of artwork, and offering it as a
# card pool would be a confident, checkable lie.
PLAYABLE_SET_TYPES = {
    "core", "expansion", "masters", "draft_innovation", "commander",
    "starter", "box", "duel_deck", "from_the_vault", "premium_deck",
    "arsenal", "spellbook",
}

# Basics are always allowed and always available. Strictly, "only cards from
# this set" makes almost any deck unbuildable, because a mana base needs more
# basics than any set prints. Saying so once is kinder than failing.
BASIC_NAMES = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


# Words that appear in dozens of set names and identify nothing on their own.
# "Commander" matches ninety sets; "Marvel's Spider-Man" is distinctive because
# of "spider man", not because of "marvel".
_WEAK_SET_WORDS = {"the", "of", "and", "a", "s", "commander", "edition",
                   "set", "magic", "gathering", "series", "tokens", "promos",
                   "art", "marvel", "universes", "beyond"}

# Things people say around a set name that are not part of any set name.
_ASK_FILLER = {"i", "want", "wanted", "to", "use", "using", "only", "just",
               "can", "could", "we", "you", "please", "make", "build", "rebuild",
               "deck", "cards", "card", "booster", "boosters", "pack", "packs",
               "set", "sets", "from", "with", "the", "my", "a", "all", "in", "it"}


def resolve_set(text: str, sets: list[dict]) -> dict | None:
    """Find the set she means, from Scryfall's own catalogue.

    Matches on the set's own distinctive words appearing in what she said,
    rather than requiring her sentence to be a substring of a set name. People
    write "I want to use only Spider-Man booster pack cards", and an exact
    containment test finds nothing in that — which is silently the same as her
    not having asked.

    Never invents a code. Prefers a playable set over its art series and token
    companions, and the larger candidate when both are playable: "Spider-Man"
    means the 286-card expansion, not the 26-card Eternal supplement.
    """
    words = [w for w in _norm(text).split() if w not in _ASK_FILLER]
    q = " ".join(words)
    if len(q) < 3:
        return None

    scored: list[tuple[int, int, int, dict]] = []
    for st in sets or []:
        name = _norm(st.get("name"))
        code = _norm(st.get("code"))
        nwords = name.split()
        strong = [w for w in nwords if w not in _WEAK_SET_WORDS and len(w) > 2]
        if not strong:
            continue

        if code and code in words:
            hit = 100                       # she typed the set code itself
        elif name and name in q:
            hit = 90                        # the whole name is in there
        else:
            # The longest run of the set's own strong words she actually said.
            run = 0
            for i in range(len(strong)):
                for j in range(len(strong), i, -1):
                    phrase = " ".join(strong[i:j])
                    if phrase in q:
                        run = max(run, j - i)
                        break
            if not run:
                continue
            # One weak-ish word is not enough to name a set — "eternal" alone
            # should not pick "Marvel's Spider-Man Eternal" out of 1047 sets.
            if run < 2 and len(strong) > 1:
                continue
            hit = run * 10
        playable = 1 if st.get("set_type") in PLAYABLE_SET_TYPES else 0
        scored.append((playable, hit, int(st.get("card_count") or 0), st))

    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    best = scored[0]
    if not best[0]:                          # only unplayable matches
        return None
    st = best[3]
    return {"code": st["code"], "name": st["name"],
            "card_count": st.get("card_count"), "set_type": st.get("set_type")}


def deck_from_pool(deck: list[dict], pool_names: set[str]) -> list[str]:
    """Cards already in the 99 that come from this set.

    Matched by name against the set's own card list, because deck entries do
    not carry a set code — trim_card() never kept one. Matching on a field that
    is always absent returned zero every time, which is a wrong number that
    looks exactly like a right one.
    """
    have = {str(n).lower() for n in pool_names or ()}
    return [c["name"] for c in deck or []
            if str(c.get("name") or "").lower() in have]


def role_targets(deck: list[dict]) -> dict[str, int]:
    """What the deck is made of now, by role — the thing a constraint changes."""
    out: dict[str, int] = {}
    for c in deck or []:
        r = c.get("role")
        if r:
            out[r] = out.get(r, 0) + 1
    return out


def summarise(pool_total: int, in_identity: int, deck_size: int,
              already: int, role_now: dict, role_pool: dict,
              land_count: int = 0) -> dict:
    """The arithmetic she needs before deciding. Nothing here is a judgement.

    Only the spells are up for trade. The mana base is sized from the deck's own
    curve and almost no set prints enough lands to cover it, so counting land
    slots as fillable overstates what a rebuild can do: this promised 70 of 99
    from Marvel's Spider-Man and delivered 40, because 38 of those 99 were
    lands. A number stated before the rebuild has to survive the rebuild.
    """
    spell_slots = max(0, deck_size - int(land_count or 0))
    # Bounded per role, not in aggregate. A set holding 3 ramp cards cannot
    # fill 10 ramp slots however many cards it prints overall, and the ladder
    # will quietly take the other 7 from her colours. Counting only the total
    # promised 61 and delivered 40; this is the number that survives contact
    # with the rebuild.
    fillable = 0
    for role, want in (role_now or {}).items():
        if role in ("basic", "lands"):
            continue
        fillable += min(int(want or 0), int((role_pool or {}).get(role, 0)))
    # No prediction at all. This has now been stated four ways — 70, 61, 24,
    # then a 24-61 range — against actuals of 40 and 23, and the range failed
    # too: the second rebuild came in below its own floor. The build walks a
    # ladder that tops up from her colours rather than exhausting the set per
    # role, so how much of the set it uses is not derivable from the counts.
    #
    # What is certain: the set has this many legal cards, the deck has this
    # many spell slots, and the mana base does not move. The real split is
    # reported after the rebuild, which is the only moment it is a fact.
    ceiling = min(in_identity, spell_slots)
    from_pool = ceiling
    shortfall = max(0, spell_slots - ceiling)
    # The mana base is not part of this trade. Lands come from the deck's own
    # curve and basics are unlimited, so reporting "lands 20 -> 0" says only
    # that the set prints few nonbasic lands — and reads as "nothing works".
    # It made a reader conclude the whole constraint was impossible when 70 of
    # 99 slots were fillable.
    deltas = {}
    for role in sorted(set(role_now) | set(role_pool)):
        if role in ("basic", "lands"):
            continue
        now, then = int(role_now.get(role, 0)), int(role_pool.get(role, 0))
        if now != then:
            deltas[role] = {"now": now, "available": then}
    return {
        "pool_total": pool_total,
        "in_identity": in_identity,
        "deck_size": deck_size,
        "already_from_pool": already,
        "spell_slots": spell_slots,
        "land_slots": int(land_count or 0),
        "from_pool_at_most": ceiling,
        "from_pool": from_pool,
        "shortfall": shortfall,
        "buildable": from_pool + shortfall >= spell_slots,
        "role_deltas": deltas,
        # Said once, plainly, because it is the difference between "impossible"
        # and "fine" for almost every constraint anyone will name.
        "basics_note": "Basic lands are always allowed — no set prints enough.",
    }
