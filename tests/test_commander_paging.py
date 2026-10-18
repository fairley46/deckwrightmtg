"""
test_commander_paging.py — show-me-more from the same rung.

Offline.

    python test_commander_paging.py
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


def test_true_total_not_fetch_cap():
    """rung_total is the real set size (479), not a fetch cap (85)."""
    print("\npaging — true total outranks fetch-cap length")
    pool = [{"name": f"C{i}"} for i in range(85)]
    page = app.page_commanders(pool, offset=0, n=5, rung_total=479)
    eq("rung_total is Scryfall total", page["rung_total"], 479)
    eq("shown_of uses true total", page["shown_of"], "5 of 479")
    eq("has_more when more exist beyond fetch", page["has_more"], True)
    capped = app.page_commanders(pool, offset=0, n=5)
    eq("without override, total is pool length (the old bug)", capped["rung_total"], 85)


def test_page_slice():
    print("\npaging — same pool, offset slices, count reported")
    pool = [{"name": f"C{i}", "edhrec_rank": i} for i in range(12)]
    page0 = app.page_commanders(pool, offset=0, n=5)
    page1 = app.page_commanders(pool, offset=5, n=5)
    page2 = app.page_commanders(pool, offset=10, n=5)
    eq("page0 size", len(page0["cards"]), 5)
    eq("page1 size", len(page1["cards"]), 5)
    eq("page2 size", len(page2["cards"]), 2)
    eq("rung_total", page0["rung_total"], 12)
    eq("shown_of", page0["shown_of"], "5 of 12")
    eq("page0 has_more", page0["has_more"], True)
    eq("page2 has_more", page2["has_more"], False)
    eq("page2 exhausted", page2["rung_exhausted"], True)
    names0 = {c["name"] for c in page0["cards"]}
    names1 = {c["name"] for c in page1["cards"]}
    eq("pages disjoint", names0 & names1, set())


def test_exhausted_past_end():
    print("\npaging — past the end says exhausted, does not invent cards")
    pool = [{"name": f"C{i}"} for i in range(5)]
    page = app.page_commanders(pool, offset=5, n=5)
    eq("empty past end", len(page["cards"]), 0)
    eq("exhausted", page["rung_exhausted"], True)
    eq("has_more false", page["has_more"], False)
    true("note names all/exhausted", bool(page.get("note")), page.get("note"))


def test_unknown_total():
    print("\npaging — unknown total is not zero")
    page = app.page_commanders(None, offset=0, n=5, rung_status="unknown")
    eq("status unknown", page["status"], "unknown")
    eq("rung_total is None", page["rung_total"], None)
    eq("has_more is None (unknown)", page["has_more"], None)
    true("note names unknown", "unknown" in (page.get("note") or "").lower()
         or "could not" in (page.get("note") or "").lower(), page.get("note"))


def test_rank_stable():
    print("\npaging — rank order is stable across calls")
    pool = [
        {"name": "A", "type": "Legendary Creature — Pirate", "text": "pirate",
         "edhrec_rank": 50, "color_identity": ["U", "B"]},
        {"name": "B", "type": "Legendary Creature — Human", "text": "draw a card",
         "edhrec_rank": 10, "color_identity": ["U", "B"]},
        {"name": "C", "type": "Legendary Creature — Pirate", "text": "sacrifice a creature",
         "edhrec_rank": 20, "color_identity": ["U", "B"]},
    ]
    brief = {"theme": "pirate", "strategy": "aristocrats",
             "angle": "pirates that sacrifice"}
    r1 = [c["name"] for c in app.rank_commanders_pool(pool, brief)]
    r2 = [c["name"] for c in app.rank_commanders_pool(pool, brief)]
    eq("stable order", r1, r2)
    eq("all present", sorted(r1), ["A", "B", "C"])


def main() -> int:
    test_true_total_not_fetch_cap()
    test_page_slice()
    test_exhausted_past_end()
    test_unknown_total()
    test_rank_stable()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
