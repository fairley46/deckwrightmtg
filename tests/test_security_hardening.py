#!/usr/bin/env python3
"""Input validation, safe error bodies, and regex performance. Offline.

- Saved-deck and precon ids must stay inside their directory.
- Error bodies carry fixed copy, never raw exception text.
- Regexes that read pasted text finish quickly on hostile input and keep
  their old results on normal input.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import os
import tempfile
import time

from deckwright import app
from deckwright import import_deck
from deckwright import precon_bake
from deckwright import safe_paths
from deckwright import sim
from deckwright import swap

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
passed = failed = 0

TRAVERSAL = ["../escape", "..", "a/b", "/etc/passwd", "a\\b", "a.b", "",
             "x\x00y", "deck/../../escape", "%2e%2e"]


def ok(label):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {extra}")


def true(label, cond, extra=""):
    (ok(label) if cond else bad(label, extra))


def eq(label, got, want):
    true(label, got == want, f"got {got!r}, want {want!r}")


def fast(label, fn, limit=2.0):
    t0 = time.perf_counter()
    fn()
    took = time.perf_counter() - t0
    true(f"{label} ({took:.3f}s)", took < limit, f"took {took:.2f}s")


def test_safe_child_path():
    print("\nsafe_paths.safe_child_path")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        for name in TRAVERSAL:
            eq(f"rejects {name!r}", safe_paths.safe_child_path(base, name), None)
        good = safe_paths.safe_child_path(base, "Niv-Mizzet_2026")
        true("accepts a hyphenated id", good is not None)
        true("valid id stays inside base",
             good is not None and good.parent == Path(os.path.realpath(d)))
        eq("valid id keeps the .json suffix", good.name, "Niv-Mizzet_2026.json")
        # A symlink inside base that points outside must not be followed.
        with tempfile.TemporaryDirectory() as outside:
            os.symlink(os.path.join(outside, "x.json"), base / "link.json")
            eq("rejects a symlink that leaves base",
               safe_paths.safe_child_path(base, "link"), None)


def test_saved_deck_path():
    print("\napp.saved_deck_path / load_saved_deck")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        (base / "my-deck.json").write_text(json.dumps({"commander": "X", "deck": []}))
        Path(d, "..", "outside.json").resolve().write_text("{}")
        try:
            true("valid saved id resolves",
                 app.saved_deck_path("my-deck", base) is not None)
            true("valid saved id loads",
                 app.load_saved_deck("my-deck", base) is not None)
            for name in TRAVERSAL + ["../outside"]:
                eq(f"saved id {name!r} is not found",
                   app.saved_deck_path(name, base), None)
            eq("traversal delete refused", app.delete_saved_deck("../outside", base), False)
        finally:
            Path(d, "..", "outside.json").resolve().unlink()


def test_precon_bake_ids():
    print("\nprecon_bake ids")
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        precon_bake.write_bake({"id": 4242, "deck": []}, base)
        eq("numeric precon id round-trips",
           (precon_bake.read_bake("4242", base) or {}).get("id"), 4242)
        for name in TRAVERSAL:
            eq(f"read_bake {name!r} is None", precon_bake.read_bake(name, base), None)
        try:
            precon_bake.write_bake({"id": "../escape"}, base)
            bad("write_bake refuses a traversal id")
        except ValueError:
            ok("write_bake refuses a traversal id")


def test_refusal_body_has_no_raw_error():
    print("\nScryfall refusal body")
    err = app.ScryfallError(
        0, "transport: ConnectError: [Errno 111] connect to internal-host:443",
        kind="transport")
    body = app.scryfall_refusal_payload(err)
    eq("kind kept", body["kind"], "transport")
    eq("error code kept", body["error"], "scryfall_refused")
    true("detail has no exception text", "internal-host" not in json.dumps(body))
    limited = app.scryfall_refusal_payload(app.ScryfallError(429, kind="rate_limit"))
    eq("status kept", limited["status"], 429)
    eq("rate limit copy", limited["what"], app.scryfall_refusal_copy("rate_limit")["what"])


def test_swap_problem_matches_refusal():
    print("\nswap problem text")
    deck = [{"name": "Sol Ring", "role": "ramp"}, {"name": "Arcane Signet", "role": "ramp"}]
    pools = {"ramp": [{"name": "Mind Stone", "color_identity": []}]}
    eq("missing card sentence",
       swap.swap_problem(deck, "Nope", "Mind Stone", pools), "Nope is not in the deck")
    eq("singleton sentence",
       swap.swap_problem(deck, "Sol Ring", "Arcane Signet", pools),
       "Arcane Signet is already in the deck (singleton)")
    eq("valid swap has no problem",
       swap.swap_problem(deck, "Sol Ring", "Mind Stone", pools), None)
    try:
        swap.apply_swap(deck, "Nope", "Mind Stone", pools)
        bad("apply_swap still raises")
    except swap.SwapError as e:
        eq("exception text matches the check", str(e), "Nope is not in the deck")


def test_decklist_parse_results():
    print("\ndecklist lines parse as before")
    cases = {
        "1 Sol Ring": (1, "Sol Ring"),
        "2x Sol Ring (CMM) 123": (2, "Sol Ring"),
        "3X Island": (3, "Island"),
        "- 1 Sol Ring": (1, "Sol Ring"),
        "* Lightning Bolt (2X2)": (1, "Lightning Bolt"),
        "Sol Ring": (1, "Sol Ring"),
        "2 x ": (2, "x"),
        "-": (1, "-"),
        "10": (1, "10"),
    }
    for line, (qty, name) in cases.items():
        e = import_deck.parse_decklist(line)["entries"][0]
        eq(f"{line!r}", (e["qty"], e["name"]), (qty, name))
    secs = import_deck.parse_decklist("Commander:\n1 Atraxa\nSIDEBOARD\n1 Opt")
    eq("sections still recognised", sorted(secs["sections"]), ["commander", "sideboard"])


def test_regexes_are_fast_on_hostile_input():
    print("\nhostile input finishes quickly")
    spaces = " " * 50_000
    # Each of these took seconds with the old patterns (quadratic in the run).
    fast("card name with a long run of spaces",
         lambda: import_deck.parse_decklist("1 a" + spaces + "b"))
    fast("section header of spaces", lambda: import_deck.parse_decklist("main" + spaces + "x"))
    fast("theme of spaces", lambda: app.theme_segments("a" + spaces + "b,c"))
    fast("unclosed mana braces", lambda: sim.parse_cost("{" * 50_000))
    fast("repeated activation costs",
         lambda: sim._activated_library_search("{t}:" * 20_000))
    fast("repeated put one",
         lambda: sim.mechanics({"name": "X", "type": "Sorcery", "text":
                                "search your library for a land onto the battlefield "
                                + "put one " * 20_000}, set()))
    eq("theme spans unchanged",
       app.theme_segments("pirates that steal, heists"),
       ["pirates", "that steal", "heists"])


def main() -> int:
    print("security hardening")
    test_safe_child_path()
    test_saved_deck_path()
    test_precon_bake_ids()
    test_refusal_body_has_no_raw_error()
    test_swap_problem_matches_refusal()
    test_decklist_parse_results()
    test_regexes_are_fast_on_hostile_input()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
