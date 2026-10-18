"""
test_deck_accounting.py — theme/strategy buckets on the 99.

Offline. Every card lands in exactly one bucket; unknown is never curve;
counts are never padded.

    python test_deck_accounting.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import why  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

BUCKETS = ("theme_strategy", "theme_only", "strategy_only", "curve_mana", "unknown")


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


def _cmdr():
    # Draw-shaped — does not consume creatures, so a plain pirate is theme-only.
    return {
        "name": "Test Commander",
        "type": "Legendary Creature — Spirit",
        "text": "At the beginning of your upkeep, draw a card.",
        "oracle_text": "At the beginning of your upkeep, draw a card.",
    }


def _cmdr_aristocrats():
    return {
        "name": "Aristocrats Commander",
        "type": "Legendary Creature — Spirit",
        "text": "Sacrifice a creature: You gain 1 life.",
        "oracle_text": "Sacrifice a creature: You gain 1 life.",
    }


def _pirate(name="Corsair", role="theme"):
    return {
        "name": name,
        "type": "Creature — Pirate",
        "text": "Whenever this creature deals combat damage to a player, draw a card.",
        "oracle_text": "Whenever this creature deals combat damage to a player, draw a card.",
        "role": role,
    }


def _fodder(name="Fodder", role="plan"):
    # Creature that dies for value — strategy for aristocrats, not a pirate.
    return {
        "name": name,
        "type": "Creature — Human",
        "text": "When this creature dies, create a Treasure token.",
        "oracle_text": "When this creature dies, create a Treasure token.",
        "role": role,
    }


def _outlet(name="Outlet", role="plan"):
    return {
        "name": name,
        "type": "Artifact",
        "text": "{T}, Sacrifice a creature: Add {C}.",
        "oracle_text": "{T}, Sacrifice a creature: Add {C}.",
        "role": role,
    }


def _sol_ring():
    return {
        "name": "Sol Ring",
        "type": "Artifact",
        "text": "{T}: Add {C}{C}.",
        "oracle_text": "{T}: Add {C}{C}.",
        "role": "ramp",
    }


def _swamp():
    return {
        "name": "Swamp",
        "type": "Basic Land — Swamp",
        "text": "{T}: Add {B}.",
        "oracle_text": "{T}: Add {B}.",
        "role": "basic",
    }


def _blank_oracle(name="Blank", role="theme"):
    # Unreadable oracle — house rule: unknown, not curve.
    return {
        "name": name,
        "type": "Creature — Horror",
        "text": None,
        "oracle_text": None,
        "role": role,
    }


def test_exclusive_buckets():
    print("\naccounting — every card exactly one bucket; sum is the 99")
    deck = (
        [_pirate(f"P{i}") for i in range(5)]
        + [_fodder(f"F{i}") for i in range(4)]
        + [_outlet()]
        + [_sol_ring()]
        + [_swamp() for _ in range(3)]
        + [_blank_oracle()]
    )
    # Pad to 14 cards for a small fixture; exclusivity is what matters.
    acct = why.account_deck(
        deck,
        commander=_cmdr(),
        theme_label="pirates",
        tribe="pirate",
        theme_legal_count=61,
    )
    by_name = {c["name"]: c["bucket"] for c in acct["cards"]}
    eq("card count matches deck", len(acct["cards"]), len(deck))
    eq("every card has a bucket", all(b in BUCKETS for b in by_name.values()), True)
    # Counts sum to deck size — never pad.
    total = sum(acct["counts"].get(b, 0) for b in BUCKETS)
    eq("counts sum to deck size", total, len(deck))
    for b in BUCKETS:
        named = sum(1 for x in acct["cards"] if x["bucket"] == b)
        eq(f"count[{b}] matches named", acct["counts"].get(b, 0), named)


def test_exemplars():
    print("\naccounting — exemplar + near-miss per bucket")
    deck = [
        # Pirate that also feeds aristocrats (creature permanent → sacrifice).
        {
            "name": "Viscera Pirate",
            "type": "Creature — Pirate",
            "text": "When this creature dies, each opponent loses 1 life.",
            "oracle_text": "When this creature dies, each opponent loses 1 life.",
            "role": "theme",
        },
        _pirate("Plain Pirate"),          # theme only vs draw commander
        _outlet("Pure Outlet"),           # strategy only (plan role)
        _sol_ring(),                      # curve/mana
        _blank_oracle("Mystery"),         # unknown
    ]
    # Draw commander: plain pirate is theme_only; outlet is strategy via plan role.
    acct = why.account_deck(
        deck, commander=_cmdr(), theme_label="pirates", tribe="pirate",
        theme_legal_count=5,
    )
    by = {c["name"]: c["bucket"] for c in acct["cards"]}
    eq("plain pirate theme_only", by["Plain Pirate"], "theme_only")
    eq("outlet strategy_only", by["Pure Outlet"], "strategy_only")
    eq("sol ring curve_mana", by["Sol Ring"], "curve_mana")
    eq("blank oracle unknown", by["Mystery"], "unknown")
    eq("theme_legal reported", acct.get("theme_legal"), 5)
    eq("theme_label reported", acct.get("theme_label"), "pirates")

    # Near-miss / exemplar: same pirate under aristocrats commander → also strategy.
    acct2 = why.account_deck(
        [deck[0]], commander=_cmdr_aristocrats(),
        theme_label="pirates", tribe="pirate",
    )
    eq("pirate creature under aristocrats → theme_strategy",
       acct2["cards"][0]["bucket"], "theme_strategy")


def test_never_pad():
    print("\naccounting — never pad a thin theme bucket")
    deck = [_pirate("Only One")] + [_sol_ring()] + [_swamp() for _ in range(5)]
    acct = why.account_deck(
        deck, commander=_cmdr(), theme_label="pirates", tribe="pirate",
        theme_legal_count=5,
    )
    on_theme = (acct["counts"].get("theme_strategy", 0)
                + acct["counts"].get("theme_only", 0))
    eq("exactly one on-theme card", on_theme, 1)
    eq("theme_legal stays 5 (not padded to deck size)", acct["theme_legal"], 5)


def test_unknown_not_curve():
    print("\naccounting — unknown is its own bucket, never curve")
    deck = [_blank_oracle("X", role="ramp"), _sol_ring()]
    acct = why.account_deck(
        deck, commander=_cmdr(), theme_label="pirates", tribe="pirate",
    )
    by = {c["name"]: c["bucket"] for c in acct["cards"]}
    eq("blank not filed under curve", by["X"], "unknown")
    eq("sol ring still curve", by["Sol Ring"], "curve_mana")
    true("unknown count > 0", acct["counts"].get("unknown", 0) >= 1)


def test_stamp_mutates_cards():
    print("\naccounting — stamps bucket on each card")
    deck = [_pirate(), _sol_ring()]
    why.account_deck(
        deck, commander=_cmdr(), theme_label="pirates", tribe="pirate",
    )
    eq("pirate has bucket field", "bucket" in deck[0], True)
    eq("sol ring has bucket field", "bucket" in deck[1], True)


def test_decidable_neither_is_curve():
    """theme-slot filler with both axes False is curve,
    not unclassifiable. Unknown stays reserved for None (could not look)."""
    print("\naccounting — decidable-neither theme filler → curve_mana")
    filler = {
        "name": "Rhystic Study",
        "type": "Enchantment",
        "text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
        "oracle_text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
        "role": "theme",
    }
    acct = why.account_deck(
        [filler], commander=_cmdr(), theme_label="pirates", tribe="pirate",
    )
    eq("theme filler is curve_mana", acct["cards"][0]["bucket"], "curve_mana")
    eq("unknown stays 0", acct["counts"].get("unknown", 0), 0)


def test_sibling_tribes_count():
    """knight counts when tribes are horse+knight."""
    print("\naccounting — sibling tribes OR")
    knight = {
        "name": "Worthy Knight",
        "type": "Creature — Human Knight",
        "text": "Whenever you cast a Knight spell, create a 1/1 white Human Soldier.",
        "oracle_text": "Whenever you cast a Knight spell, create a 1/1 white Human Soldier.",
        "role": "theme",
    }
    miss = why.account_deck(
        [knight], commander=_cmdr(), theme_label="horses and knights",
        tribe="horse",
    )
    eq("knight misses horse-only tribe", miss["cards"][0]["bucket"], "curve_mana")
    hit = why.account_deck(
        [knight], commander=_cmdr(), theme_label="horses and knights",
        tribes=["horse", "knight"],
    )
    eq("knight hits sibling tribes", hit["cards"][0]["bucket"], "theme_only")
    eq("theme_noun names both", hit.get("theme_noun"), "horse and knight")


def test_theme_noun_short():
    print("\naccounting — theme_noun is short")
    eq("tribe wins", why.theme_noun(
        "pirate raiders — combat damage steals permanents", tribe="pirate"),
       "pirate")
    eq("long paraphrase shortens", why.theme_noun(
        "pirate raiders — combat damage steals permanents"), "pirate")


def main() -> int:
    test_exclusive_buckets()
    test_exemplars()
    test_never_pad()
    test_unknown_not_curve()
    test_stamp_mutates_cards()
    test_decidable_neither_is_curve()
    test_sibling_tribes_count()
    test_theme_noun_short()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
