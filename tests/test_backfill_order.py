#!/usr/bin/env python3
"""offline tests for role backfill ordering and singleton placement."""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from collections import Counter

from deckwright import app
from deckwright import synergy
from deckwright.app import backfill_chosen, place_role_cards, validate_deck


def _card(name: str, **kw) -> dict:
    base = {
        "name": name,
        "type": kw.pop("type", kw.get("type_line", "Instant")),
        "type_line": kw.pop("type_line", "Instant"),
        "oracle_text": kw.pop("oracle_text", ""),
        "text": kw.pop("text", kw.get("oracle_text", "")),
        "usd": kw.pop("usd", "1.00"),
        "cmc": kw.pop("cmc", 2),
        "color_identity": kw.pop("color_identity", ["W"]),
        "legal": kw.pop("legal", "legal"),
        "gc": kw.pop("gc", False),
        "keywords": kw.pop("keywords", []),
        "edhrec_rank": kw.pop("edhrec_rank", 100),
    }
    base.update(kw)
    return base


def eq(label: str, got, want) -> None:
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


def _nonbasic_dupes(cards: list[dict]) -> list[str]:
    counts = Counter(c["name"] for c in cards)
    out = []
    for name, n in counts.items():
        if n < 2:
            continue
        sample = next(c for c in cards if c["name"] == name)
        if app._is_basic(sample):
            continue
        out.append(name)
    return sorted(out)


def test_backfill_prefers_glued_over_orphan():
    """Glued candidate wins backfill slot when both are in the pool."""
    print("\nbackfill — glued before orphan")
    hei = _card(
        "Hei Bai, Spirit of Balance",
        type_line="Legendary Creature — Spirit",
        oracle_text="Whenever a Spirit you control dies, draw a card.",
    )
    angel = _card(
        "Angel of Indemnity",
        type_line="Creature — Angel",
        oracle_text="Flying, lifelink",
    )
    shock = _card("Shock", oracle_text="Shock deals 2 damage to any target.")

    # Model under-delivered; pool order is orphan-first (EDHREC order stand-in).
    pool = [shock, angel]
    chosen = backfill_chosen([], pool, 1, hei, [])
    eq("glued card backfilled first", chosen[0]["name"], "Angel of Indemnity")

    # Model pick kept; backfill still prefers glue for the second slot.
    chosen2 = backfill_chosen([shock], pool, 2, hei, [])
    eq("model pick preserved", chosen2[0]["name"], "Shock")
    eq("second slot is glued", chosen2[1]["name"], "Angel of Indemnity")


def test_backfill_steers_off_preserves_pool_order():
    """STEER off leaves raw pool / EDHREC order as tiebreak."""
    print("\nbackfill — STEER off preserves pool order")
    hei = _card(
        "Hei Bai, Spirit of Balance",
        type_line="Legendary Creature — Spirit",
        oracle_text="Whenever a Spirit you control dies, draw a card.",
    )
    angel = _card("Angel of Indemnity", type_line="Creature — Angel")
    shock = _card("Shock", oracle_text="Shock deals 2 damage to any target.")
    pool = [shock, angel]

    prev = synergy.STEER
    try:
        synergy.STEER = False
        chosen = backfill_chosen([], pool, 1, hei, [])
        eq("pool order when STEER off", chosen[0]["name"], "Shock")
    finally:
        synergy.STEER = prev


def test_backfill_fail_open_unreadable_commander():
    """Unreadable commander does not crash; pool order stands."""
    print("\nbackfill — fail-open on unreadable commander")
    blank = {"name": "Mystery", "type_line": "", "oracle_text": ""}
    shock = _card("Shock", oracle_text="Shock deals 2 damage to any target.")
    angel = _card("Angel of Indemnity", type_line="Creature — Angel")
    pool = [shock, angel]
    chosen = backfill_chosen([], pool, 1, blank, [])
    eq("still fills from pool", len(chosen), 1)
    eq("first pool card", chosen[0]["name"], "Shock")


def test_backfill_dedupes_duplicate_pool_names():
    """prefer_glued can surface two copies of one name — backfill keeps one."""
    print("\nbackfill — singleton against duplicate pool")
    adeline = _card(
        "Adeline, Resplendent Cathar",
        type_line="Legendary Creature — Human Knight",
        oracle_text=(
            "Whenever you attack, for each opponent create a 1/1 white Human "
            "creature token that's tapped and attacking that player."
        ),
    )
    valor = _card(
        "Valor in Akros",
        type_line="Enchantment",
        oracle_text=(
            "Whenever a creature you control enters, creatures you control "
            "get +1/+1 until end of turn."
        ),
        usd="0.50",
    )
    orphans = [
        _card(f"Shock {i}", oracle_text="Shock deals 2 damage to any target.")
        for i in range(12)
    ]
    # Overlapping payoff searches put the same card in the pool twice.
    pool = orphans[:3] + [valor, dict(valor)] + orphans[3:]
    chosen = backfill_chosen([], pool, 8, adeline, [])
    names = [c["name"] for c in chosen]
    eq("backfill still fills target", len(chosen), 8)
    eq("Valor appears once in backfill", names.count("Valor in Akros"), 1)
    eq("no duplicate names in backfill", _nonbasic_dupes(chosen), [])


def test_place_role_cards_dedupes_at_entry():
    """cards enter the 99 once — duplicate chosen/pool names do not."""
    print("\nplace_role_cards — singleton at placement")
    adeline = _card(
        "Adeline, Resplendent Cathar",
        type_line="Legendary Creature — Human Knight",
        oracle_text=(
            "Whenever you attack, for each opponent create a 1/1 white Human "
            "creature token that's tapped and attacking that player."
        ),
    )
    aerith = _card(
        "Aerith Gainsborough",
        type_line="Legendary Creature — Human Cleric",
        oracle_text="Lifelink\nWhenever you gain life, put a +1/+1 counter on Aerith Gainsborough.",
        usd="3.01",
    )
    fillers = [
        _card(f"Knight {i}", type_line="Creature — Human Knight")
        for i in range(20)
    ]
    # Model (or a prior backfill) handed the same name twice.
    chosen = [aerith, dict(aerith)] + fillers[:3]
    pool = [aerith, dict(aerith)] + fillers
    deck: list[dict] = []
    seen: set[str] = set()
    place_role_cards(
        "theme", deck, chosen, pool, 5, seen,
        commander=adeline, scaffold=[],
    )
    names = [c["name"] for c in deck]
    eq("theme still fills five slots", len(deck), 5)
    eq("Aerith placed once", names.count("Aerith Gainsborough"), 1)
    eq("no duplicate non-basics in role", _nonbasic_dupes(deck), [])
    eq("every placed name is in seen", set(names) <= seen, True)


def test_assembled_deck_singleton_outside_basics():
    """assemble across roles; validate_deck sees no non-basic dupes.

    Basics may repeat. Non-basics must not — including when the theme pool
    carries the same glued card twice and prefer_glued promotes both copies.
    """
    print("\nassembled deck — singleton-outside-basics")
    cmdr = _card(
        "Adeline, Resplendent Cathar",
        type_line="Legendary Creature — Human Knight",
        oracle_text=(
            "Vigilance, lifelink\n"
            "Whenever you attack, for each opponent create a 1/1 white Human "
            "creature token that's tapped and attacking that player."
        ),
        color_identity=["W"],
    )
    valor = _card(
        "Valor in Akros",
        type_line="Enchantment",
        oracle_text=(
            "Whenever a creature you control enters, creatures you control "
            "get +1/+1 until end of turn."
        ),
        color_identity=["W"],
        usd="0.40",
    )
    deck: list[dict] = []
    seen: set[str] = {cmdr["name"]}

    # Lands first (skeleton stand-in), then a theme role fed a duplicated pool.
    land_pool = [
        _card(f"Utility Land {i}", type="Land", type_line="Land",
              oracle_text="{T}: Add {W}.", color_identity=["W"], cmc=0)
        for i in range(10)
    ]
    place_role_cards("lands", deck, land_pool[:8], land_pool, 8, seen)

    theme_pool = (
        [_card(f"Orphan {i}", oracle_text="Shock deals 2 damage to any target.",
               color_identity=["W"]) for i in range(6)]
        + [valor, dict(valor)]
        + [_card(f"Knight {i}", type_line="Creature — Human Knight",
                 color_identity=["W"]) for i in range(20)]
    )
    chosen = backfill_chosen([], theme_pool, 12, cmdr, deck)
    place_role_cards(
        "theme", deck, chosen, theme_pool, 12, seen,
        commander=cmdr, scaffold=deck,
    )

    # Pad to 99 with unique non-basics + repeated basics (legal).
    i = 0
    while len(deck) < 90:
        name = f"Filler {i}"
        if name not in seen:
            deck.append(_card(name, type_line="Creature — Human",
                              color_identity=["W"]))
            seen.add(name)
        i += 1
    while len(deck) < 99:
        deck.append(_card(
            "Plains", type="Basic Land — Plains",
            type_line="Basic Land — Plains",
            oracle_text="", color_identity=["W"], cmc=0, usd="0.01",
        ))

    eq("assembled 99", len(deck), 99)
    eq("no non-basic duplicates in assembled deck", _nonbasic_dupes(deck), [])
    plains = sum(1 for c in deck if c["name"] == "Plains")
    eq("basics still allowed to repeat", plains >= 2, True)

    report = validate_deck(cmdr, deck)
    singleton = [v for v in report["violations"] if v["code"] == "singleton"]
    eq("validate_deck reports no singleton violation", singleton, [])


def main() -> int:
    tests = [
        test_backfill_prefers_glued_over_orphan,
        test_backfill_steers_off_preserves_pool_order,
        test_backfill_fail_open_unreadable_commander,
        test_backfill_dedupes_duplicate_pool_names,
        test_place_role_cards_dedupes_at_entry,
        test_assembled_deck_singleton_outside_basics,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print()
    if failed:
        print(f"FAILED {failed}/{len(tests)}")
        return 1
    print(f"PASSED {len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
