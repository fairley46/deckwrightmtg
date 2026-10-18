#!/usr/bin/env python3
"""curly / smart punctuation must not drop import names.

iOS, macOS, Word, and most web paste use U+2019 instead of ASCII apostrophe.
Scryfall names use the straight form. Normalise for lookup only; the card
shown back is whatever Scryfall returned.
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import sys

from deckwright import import_deck

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

CURLY_APOS = "\u2019"  # RIGHT SINGLE QUOTATION MARK
LEFT_APOS = "\u2018"   # LEFT SINGLE QUOTATION MARK
LEFT_DQUOTE = "\u201c"
RIGHT_DQUOTE = "\u201d"
EN_DASH = "\u2013"
EM_DASH = "\u2014"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        bad(label, extra or "expected truthy")


# A real-world paste where every line that vanished had a curly apostrophe.
WILD_CURLY = [
    f"Angrath{CURLY_APOS}s Marauders",
    f"Trailblazer{CURLY_APOS}s Boots",
    f"Brass{CURLY_APOS}s Bounty",
    f"An Offer You Can{CURLY_APOS}t Refuse",
    f"Brotherhood{CURLY_APOS}s End",
    f"Commander{CURLY_APOS}s Sphere",
    f"Rogue{CURLY_APOS}s Passage",
]

WILD_STRAIGHT = [
    "Angrath's Marauders",
    "Trailblazer's Boots",
    "Brass's Bounty",
    "An Offer You Can't Refuse",
    "Brotherhood's End",
    "Commander's Sphere",
    "Rogue's Passage",
]

NON_ASCII = [
    "Æther Vial",
    "Lim-Dûl's Vault",
    "Jötun Grunt",
]


def _catalog_card(name: str, typ: str = "Artifact") -> dict:
    return {"name": name, "type": typ, "text": "", "cmc": 0,
            "color_identity": [], "usd": None}


def _catalog() -> dict[str, dict]:
    cards = [_catalog_card(n) for n in WILD_STRAIGHT]
    cards += [_catalog_card(n) for n in NON_ASCII]
    # Synthetic names that only match after quote/dash normalisation.
    cards.append(_catalog_card('Kongming, "Sleeping Dragon"'))
    cards.append(_catalog_card("Sword of Feast-and-Famine"))
    return {c["name"].lower(): c for c in cards}


def _make_scry_post(catalog: dict[str, dict]):
    async def scry_post(path, json=None):
        data, nf = [], []
        for ident in (json or {}).get("identifiers") or []:
            name = ident.get("name") or ""
            card = catalog.get(name.lower())
            if card:
                data.append(dict(card))
            else:
                nf.append({"name": name})
        return {"data": data, "not_found": nf}
    return scry_post


def test_normalize_name_for_lookup():
    print("\nnormalize_name_for_lookup()  — smart punctuation → ASCII")
    n = import_deck.normalize_name_for_lookup
    eq("curly apostrophe", n(f"Rogue{CURLY_APOS}s Passage"), "Rogue's Passage")
    eq("left single quote", n(f"Rogue{LEFT_APOS}s Passage"), "Rogue's Passage")
    eq("straight apostrophe unchanged", n("Rogue's Passage"), "Rogue's Passage")
    eq("smart double quotes",
       n(f"Kongming, {LEFT_DQUOTE}Sleeping Dragon{RIGHT_DQUOTE}"),
       'Kongming, "Sleeping Dragon"')
    eq("en dash", n(f"Sword of Feast{EN_DASH}and{EN_DASH}Famine"),
       "Sword of Feast-and-Famine")
    eq("em dash", n(f"Sword of Feast{EM_DASH}and{EM_DASH}Famine"),
       "Sword of Feast-and-Famine")
    for name in NON_ASCII:
        eq(f"non-ASCII preserved: {name}", n(name), name)


def test_parser_keeps_typed_form():
    """Normalisation is for matching only — parse keeps her characters."""
    print("\nparse_decklist()  — typed curly form preserved on the entry")
    typed = f"1 Rogue{CURLY_APOS}s Passage"
    entry = import_deck.parse_decklist(typed)["entries"][0]
    eq("entry name is typed form", entry["name"], f"Rogue{CURLY_APOS}s Passage")


async def test_resolve_curly_and_straight():
    print("\nresolve_names()  — curly and straight both hit Scryfall")
    catalog = _catalog()
    scry_post = _make_scry_post(catalog)

    found_c, missing_c = await import_deck.resolve_names(
        WILD_CURLY, scry_post, lambda c: c)
    eq("curly: none missing", missing_c, [])
    for typed, straight in zip(WILD_CURLY, WILD_STRAIGHT):
        card = found_c.get(typed)
        true(f"curly resolves {typed!r}", card is not None)
        if card:
            eq(f"display is Scryfall form for {typed!r}",
               card["name"], straight)
            true(f"also keyed by Scryfall name {straight!r}",
                 found_c.get(straight) is card)

    found_s, missing_s = await import_deck.resolve_names(
        WILD_STRAIGHT, scry_post, lambda c: c)
    eq("straight: none missing", missing_s, [])
    for name in WILD_STRAIGHT:
        card = found_s.get(name)
        true(f"straight resolves {name!r}", card is not None)
        if card:
            eq(f"straight display {name!r}", card["name"], name)


async def test_resolve_quotes_dashes_and_non_ascii():
    print("\nresolve_names()  — quotes, dashes, legitimate non-ASCII")
    catalog = _catalog()
    scry_post = _make_scry_post(catalog)

    smart_q = f"Kongming, {LEFT_DQUOTE}Sleeping Dragon{RIGHT_DQUOTE}"
    smart_en = f"Sword of Feast{EN_DASH}and{EN_DASH}Famine"
    smart_em = f"Sword of Feast{EM_DASH}and{EM_DASH}Famine"
    names = NON_ASCII + [smart_q, smart_en, smart_em]

    found, missing = await import_deck.resolve_names(
        names, scry_post, lambda c: c)
    eq("none missing", missing, [])
    for name in NON_ASCII:
        card = found.get(name)
        true(f"non-ASCII resolves {name!r}", card is not None)
        if card:
            eq(f"non-ASCII display {name!r}", card["name"], name)

    card_q = found.get(smart_q)
    true("smart double quotes resolve", card_q is not None)
    if card_q:
        eq("smart quotes display Scryfall form",
           card_q["name"], 'Kongming, "Sleeping Dragon"')

    for typed, want in (
        (smart_en, "Sword of Feast-and-Famine"),
        (smart_em, "Sword of Feast-and-Famine"),
    ):
        card = found.get(typed)
        true(f"dash form resolves {typed!r}", card is not None)
        if card:
            eq(f"dash display for {typed!r}", card["name"], want)


async def test_resolve_decklist_shows_scryfall_name():
    print("\nresolve_decklist()  — display name is Scryfall's, not typed")
    catalog = _catalog()
    brass = _catalog_card(
        "Admiral Beckett Brass", typ="Legendary Creature — Human Pirate")
    brass["color_identity"] = ["U", "B", "R"]
    catalog[brass["name"].lower()] = brass
    catalog["sol ring"] = _catalog_card("Sol Ring")
    scry_post = _make_scry_post(catalog)
    curly = f"Rogue{CURLY_APOS}s Passage"
    text = (
        "// Commander\n1 Admiral Beckett Brass\n"
        f"1 {curly}\n1 Sol Ring\n"
    )
    result = await import_deck.resolve_decklist(text, scry_post, lambda c: c)
    eq("curly entry still recorded as typed",
       result["entries"][1]["name"], curly)
    card = result["resolved"].get(curly)
    true("resolved under typed curly key", card is not None)
    if card:
        eq("resolved card name is Scryfall form", card["name"], "Rogue's Passage")
    deck_names = [c["name"] for c in result["deck"]]
    true("deck list uses Scryfall name", "Rogue's Passage" in deck_names)
    true("deck list omits curly typed form", curly not in deck_names)
    eq("no unresolved lines", result["unresolved"], [])


def main() -> int:
    print("import punctuation")
    test_normalize_name_for_lookup()
    test_parser_keeps_typed_form()
    asyncio.run(test_resolve_curly_and_straight())
    asyncio.run(test_resolve_quotes_dashes_and_non_ascii())
    asyncio.run(test_resolve_decklist_shows_scryfall_name())
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
