"""
test_commander_difficulty.py — CON-134: commander difficulty as one plain sentence.

    python test_commander_difficulty.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from deckwright import commander_difficulty

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


# Fixed commander fixtures — oracle text is abbreviated only where the rules
# patterns do not need the full line.
FIXTURES = [
    (
        "Yorion, Sky Nomad",
        {
            "name": "Yorion, Sky Nomad",
            "type": "Legendary Creature — Bird Serpent",
            "keywords": ["Companion", "Flash", "Flying"],
            "text": (
                "Companion — Your starting deck contains at least twenty cards "
                "more than the minimum deck size.\n"
                "Flying\n"
                "When Yorion enters, if there are four or more cards in your "
                "graveyard, return up to two target nonland permanent cards "
                "with mana value 3 or less from your graveyard to the battlefield."
            ),
        },
        "You can hold it up and cast at instant speed when the board is right.",
    ),
    (
        "Atraxa, Praetors' Voice",
        {
            "name": "Atraxa, Praetors' Voice",
            "type": "Legendary Creature — Phyrexian Angel Horror",
            "keywords": ["Flying", "Vigilance", "Deathtouch", "Lifelink"],
            "text": (
                "Flying, vigilance, deathtouch, lifelink\n"
                "At the beginning of your end step, proliferate."
            ),
        },
        "You track counters across the board and choose when to proliferate.",
    ),
    (
        "Kinnan, Bonder Prodigy",
        {
            "name": "Kinnan, Bonder Prodigy",
            "type": "Legendary Creature — Human Druid",
            "keywords": [],
            "text": (
                "Whenever you tap a noncreature permanent for mana, add one mana "
                "of any type that permanent produced.\n"
                "{1}{G}{U}: Look at the top five cards of your library. You may "
                "put a non-Human creature card from among them onto the "
                "battlefield. Put the rest on the bottom of your library in a "
                "random order."
            ),
        },
        "You track which permanents to tap and when to spend extra mana on creatures.",
    ),
    (
        "Kaalia of the Vast",
        {
            "name": "Kaalia of the Vast",
            "type": "Legendary Creature — Human Cleric",
            "keywords": ["Flying"],
            "text": (
                "Flying\n"
                "Whenever Kaalia of the Vast attacks, if that player has no "
                "Angels, Demons, or Dragons in play, you may put the top card "
                "of your library into your hand. If it's a creature card with "
                "mana value 4 or less and it has one of those creature types, "
                "you may cast it without paying its mana cost."
            ),
        },
        "You attack — big cheat-in creatures land when this commander connects.",
    ),
    (
        "Yuriko, the Tiger's Shadow",
        {
            "name": "Yuriko, the Tiger's Shadow",
            "type": "Legendary Creature — Human Ninja",
            "keywords": [],
            "text": (
                "Whenever a creature you control deals combat damage to a "
                "player, if it isn't this creature, reveal the top card of "
                "that player's library and put it into your hand. Each opponent "
                "loses life equal to that card's mana value.\n"
                "Yuriko and other creatures you control with ninja have base "
                "power and toughness 1/1."
            ),
        },
        "You keep attacking — ninja combat damage is how the deck stacks value.",
    ),
    (
        "Thrasios, Triton Hero",
        {
            "name": "Thrasios, Triton Hero",
            "type": "Legendary Creature — Merfolk Wizard",
            "keywords": ["Partner"],
            "text": (
                "{4}, {T}: Draw a card, then you may put a land card from your "
                "hand onto the battlefield tapped."
            ),
        },
        "You hold mana open to sink into drawing and extra lands instead of tapping out on curve.",
    ),
    (
        "Grand Arbiter Augustin IV",
        {
            "name": "Grand Arbiter Augustin IV",
            "type": "Legendary Creature — Human Advisor",
            "keywords": [],
            "text": (
                "Spells your opponents cast cost {1} more to cast.\n"
                "Whenever an opponent casts a spell, you may draw a card "
                "unless that player pays {1}."
            ),
        },
        "You hold mana for interaction while their spells cost more.",
    ),
    (
        "Talrand, Sky Summoner",
        {
            "name": "Talrand, Sky Summoner",
            "type": "Legendary Creature — Merfolk Wizard",
            "keywords": ["Flying"],
            "text": (
                "Flying\n"
                "Whenever you cast an instant or sorcery spell, create a 1/1 "
                "blue Djinn creature token with flying."
            ),
        },
        "You cast an instant or sorcery every turn to keep djinn production going.",
    ),
    (
        "Baral, Chief of Compliance",
        {
            "name": "Baral, Chief of Compliance",
            "type": "Legendary Creature — Human Wizard",
            "keywords": [],
            "text": (
                "Instant and sorcery spells you cast cost {1} less to cast.\n"
                "Whenever a spell or ability you control deals damage to an "
                "opponent, you may draw a card."
            ),
        },
        "You chain instants and sorceries — drawing cards depends on spell order, not combat.",
    ),
    (
        "Meren of Clan Nel Toth",
        {
            "name": "Meren of Clan Nel Toth",
            "type": "Legendary Creature — Human Shaman",
            "keywords": [],
            "text": (
                "Whenever you cast a creature spell, return target creature "
                "card with mana value X or less from your graveyard to the "
                "battlefield tapped, where X is the number of times you've cast "
                "this card from the command zone this game."
            ),
        },
        "You track what's in your graveyard and how many times you've recast this commander.",
    ),
    (
        "Azusa, Lost but Seeking",
        {
            "name": "Azusa, Lost but Seeking",
            "type": "Legendary Creature — Human Monk",
            "keywords": [],
            "text": "You may play an additional land on each of your turns.",
        },
        "You cast it and play a normal curve — no special hold-up or tracking.",
    ),
]


def test_fixed_commanders():
    print("\ncommander difficulty  — CON-134 fixed sentences")
    for name, card, want in FIXTURES:
        got = commander_difficulty.difficulty_sentence(card)
        eq(name, got, want)


def test_empty_card():
    print("\ncommander difficulty  — empty input")
    eq("empty card", commander_difficulty.difficulty_sentence({}),
       "You cast it and play a normal curve — no special hold-up or tracking.")


def main() -> int:
    test_fixed_commanders()
    test_empty_card()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
