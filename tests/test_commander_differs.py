"""
test_commander_differs.py — CON-286 picker rows must differ without model prose.

Offline.

Paged commanders skip write-ups (CON-189). The fallback used to be KB fit
lines that only swap the name — five angels all read the same. Differentiation
comes from data we already have (type, colours, ways, themes, signals), and
missing write-ups must not render as prose.

    python test_commander_differs.py
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

from deckwright import app  # noqa: E402
from deckwright import commanders  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


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


ANGEL_A = {
    "name": "Giada, Font of Hope",
    "type": "Legendary Creature — Angel",
    "type_line": "Legendary Creature — Angel",
    "text": "Flying, vigilance\nEach other Angel you control enters with an "
            "additional +1/+1 counter on it for each Angel you already control.",
    "oracle_text": "Flying, vigilance\nEach other Angel you control enters with "
                   "an additional +1/+1 counter on it for each Angel you already control.",
    "color_identity": ["W"],
    "cost": "{1}{W}",
}
ANGEL_B = {
    "name": "Lyra Dawnbringer",
    "type": "Legendary Creature — Angel",
    "type_line": "Legendary Creature — Angel",
    "text": "Flying, first strike, lifelink\nOther Angels you control get "
            "+1/+1 and have lifelink.",
    "oracle_text": "Flying, first strike, lifelink\nOther Angels you control "
                   "get +1/+1 and have lifelink.",
    "color_identity": ["W"],
    "cost": "{3}{W}{W}",
}
ANGEL_C = {
    "name": "Kaalia of the Vast",
    "type": "Legendary Creature — Human Cleric",
    "type_line": "Legendary Creature — Human Cleric",
    "text": "Whenever Kaalia of the Vast attacks an opponent, you may put an "
            "Angel, Demon, or Dragon creature card from your hand onto the "
            "battlefield tapped and attacking that opponent.",
    "oracle_text": "Whenever Kaalia of the Vast attacks an opponent, you may "
                   "put an Angel, Demon, or Dragon creature card from your hand "
                   "onto the battlefield tapped and attacking that opponent.",
    "color_identity": ["W", "B", "R"],
    "cost": "{1}{W}{B}{R}",
}

BRIEF = {
    "theme": "angel",
    "strategy": "tokens",
    "angle": "angels that go wide",
    "playstyle": "tokens",
}


def test_picker_differs_varies_by_card():
    """Five (or three) same-theme commanders must not share one fingerprint."""
    print("\npicker_differs — fingerprints vary by construction (CON-286)")
    if (ROOT / "data" / "commanders.json").is_file():
        commanders.clear_cache()
    prints = []
    for card in (ANGEL_A, ANGEL_B, ANGEL_C):
        theme = commanders.theme_fit(card, BRIEF)
        strat = commanders.strategy_fit(card, BRIEF)
        ways = list(strat.get("ways") or [])[:3]
        d = commanders.picker_differs(card, theme, strat, ways)
        true(f"{card['name']} has type", bool(d.get("type")), str(d))
        true(f"{card['name']} has color_identity",
             isinstance(d.get("color_identity"), list), str(d))
        fp = commanders.picker_differs_fingerprint(d)
        true(f"{card['name']} fingerprint non-empty", bool(fp), fp)
        prints.append(fp)
    true("fingerprints are not all identical",
         len(set(prints)) == len(prints), str(prints))


def test_enrich_pick_no_fake_fits():
    """fits is model prose only. Template/KB lines are not a write-up."""
    print("\nenrich_commander_pick — no fake fits prose (CON-286)")
    theme = commanders.theme_fit(ANGEL_A, BRIEF)
    strat = commanders.strategy_fit(ANGEL_A, BRIEF)
    ways = []
    for w in (strat.get("ways") or [])[:3]:
        pid = w.get("playstyle")
        if pid:
            ways.append({"playstyle": pid, "says": pid, "method": w.get("method")})

    # Ways present, no model → differs yes, fits empty (ways are the differentiator).
    with_ways = app.enrich_commander_pick(
        ANGEL_A, BRIEF, theme, strat, ways,
        model=None, reason="on theme", query='t:angel',
    )
    eq("ways path: fits empty", with_ways.get("fits") or "", "")
    true("ways path: differs present", bool(with_ways.get("differs")), str(with_ways))
    eq("ways path: writeup_missing false", with_ways.get("writeup_missing"), False)

    # No ways, no model → writeup_missing, fits empty (not plain_commander_reason).
    bare = app.enrich_commander_pick(
        ANGEL_A, BRIEF, theme, {"status": "ok", "score": 0, "matched": [], "ways": []},
        [], model=None, reason="also on this rung", query='t:angel',
    )
    eq("bare path: fits empty", bare.get("fits") or "", "")
    eq("bare path: writeup_missing", bare.get("writeup_missing"), True)
    true("bare path: differs still has type/colours",
         bool((bare.get("differs") or {}).get("type"))
         and isinstance((bare.get("differs") or {}).get("color_identity"), list),
         str(bare.get("differs")))
    # Must not look like the old template that only swapped the name.
    fits = bare.get("fits") or ""
    true("bare path: not plain_commander_reason shape",
         "Shown as" not in fits and not fits.startswith(ANGEL_A["name"]),
         fits)

    # Model write-up → fits kept.
    written = app.enrich_commander_pick(
        ANGEL_A, BRIEF, theme, strat, [],
        model={"name": ANGEL_A["name"], "fits": "This deck stacks angels and swings.",
               "does": "Make angels.", "strategy": "Go wide with angels."},
        reason="on theme", query='t:angel',
    )
    eq("model path: fits kept", written.get("fits"),
       "This deck stacks angels and swings.")
    eq("model path: writeup_missing false", written.get("writeup_missing"), False)


def test_paged_skip_still_differentiates():
    """offset>0 skips model calls; enrichment must still yield distinct rows."""
    print("\npaged picks — differs without write-ups (CON-286)")
    rows = []
    for card in (ANGEL_A, ANGEL_B, ANGEL_C):
        theme = commanders.theme_fit(card, BRIEF)
        strat = commanders.strategy_fit(card, BRIEF)
        ways = [{"playstyle": w.get("playstyle"), "says": w.get("playstyle")}
                for w in (strat.get("ways") or [])[:3] if w.get("playstyle")]
        row = app.enrich_commander_pick(
            card, BRIEF, theme, strat, ways,
            model=None, reason="on theme", query='t:angel',
        )
        rows.append(row)
    fps = [commanders.picker_differs_fingerprint(r["differs"]) for r in rows]
    true("three paged rows have distinct fingerprints",
         len(set(fps)) == 3, str(fps))
    true("no paged row invents fits prose",
         all(not (r.get("fits") or "") for r in rows),
         [r.get("fits") for r in rows])


def test_ui_surfaces_differs_not_missing_prose():
    """Picker card shows type/colours/differs; gates fits on real write-up."""
    print("\nUI — differs visible, missing write-up not faked (CON-286)")
    html = (ROOT / "index.html").read_text()
    true("renderCommanders shows type via differs or ctype",
         "differsBlock" in html or ("p.type" in html and "ctype" in html))
    true("differsBlock helper exists", "function differsBlock" in html)
    true("fits gated on write-up", "p.fits && !p.writeup_missing" in html
         or "writeup_missing" in html and "p.fits" in html)
    # The old "Why this is your deck" must not print when writeup_missing.
    start = html.find("function renderCommanders")
    snippet = html[start:start + 4500] if start >= 0 else ""
    true("writeup_missing consulted in commander card",
         "writeup_missing" in snippet, "missing from renderCommanders")
    true("differsBlock called on pick card", "differsBlock(p)" in snippet)


def main() -> int:
    test_picker_differs_varies_by_card()
    test_enrich_pick_no_fake_fits()
    test_paged_skip_still_differentiates()
    test_ui_surfaces_differs_not_missing_prose()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
