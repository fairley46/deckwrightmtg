"""
test_way_to_play.py — CON-179 chosen way binds the plan; no invented ways.

Offline.

    python test_way_to_play.py
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

from deckwright import plan_graph  # noqa: E402
from deckwright import commanders  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

HEI = {
    "name": "Hei Bai, Spirit of Balance",
    "type": "Legendary Creature — Bear Spirit",
    "cmc": 3,
    "text": (
        "Whenever Hei Bai enters or attacks, you may sacrifice another creature "
        "or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
        "When Hei Bai leaves the battlefield, put its counters on target "
        "creature you control."
    ),
    "oracle_text": (
        "Whenever Hei Bai enters or attacks, you may sacrifice another creature "
        "or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
        "When Hei Bai leaves the battlefield, put its counters on target "
        "creature you control."
    ),
    "color_identity": ["W", "B"],
}


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


def test_way_changes_commitments():
    print("\nway_to_play — aristocrats vs blink change plan families")
    base = {"theme": "spirits", "strategy": "sacrifice", "colors": "WB"}
    arist = plan_graph.derive({**base, "way_to_play": "aristocrats"}, HEI)
    blink = plan_graph.derive({**base, "way_to_play": "blink"}, HEI)
    a_ids = {c.get("id") for c in (arist.get("commitments") or [])}
    b_ids = {c.get("id") for c in (blink.get("commitments") or [])}
    true("aristocrats plan has sacrifice commitment",
         any("sac" in (i or "") or "death" in (i or "") or "token" in (i or "")
             for i in a_ids),
         str(sorted(a_ids)))
    true("aristocrats and blink plans differ",
         a_ids != b_ids, f"arist={sorted(a_ids)} blink={sorted(b_ids)}")
    # Invented way id adds nothing beyond the no-way baseline.
    bogus = plan_graph.derive({**base, "way_to_play": "made_up_style"}, HEI)
    none = plan_graph.derive(base, HEI)
    eq("invented way adds no commitments beyond baseline",
       {c.get("id") for c in (bogus.get("commitments") or [])},
       {c.get("id") for c in (none.get("commitments") or [])})


def test_preselect_from_strategy_match():
    print("\npreselect — matched strategy way wins")
    brief = {"theme": "spirits", "playstyle": "aristocrats",
             "strategy": "sacrifice for value"}
    strat = commanders.strategy_fit(HEI, brief)
    matched = strat.get("matched") or []
    ways = [w.get("playstyle") for w in (strat.get("ways") or [])]
    true("aristocrats is a way", "aristocrats" in ways, str(ways))
    true("aristocrats is matched", "aristocrats" in matched, str(matched))
    chosen = matched[0] if matched else (ways[0] if ways else None)
    eq("preselect is aristocrats", chosen, "aristocrats")


def test_picker_surfaces_way_source():
    """CON-240: ways show community vs derived quietly on the card."""
    print("\npicker — way source honesty (CON-240)")
    html = (ROOT / "index.html").read_text()
    start = html.find("function wayHonesty")
    snippet = html[start:start + 600] if start >= 0 else ""
    true("wayHonesty helper exists", "function wayHonesty" in html)
    true("waysBlock references wayHonesty", "wayHonesty(w)" in html)
    true("precon path references wayHonesty",
         html.count("wayHonesty(w)") >= 2)
    true("labels community source", "community" in snippet)
    true("labels derived source", "derived" in snippet)
    true("wtp-meta carries source line",
         'wtp-meta">${esc(mark)}${esc(pid)}${src ?' in html)


def test_picker_shows_says_not_machine_id():
    """CON-179: card rows are player-language says; CSS must not collide with nav."""
    print("\npicker — ways on the card (CON-179 placement)")
    html = (ROOT / "index.html").read_text()
    true("waysBlock exists", "function waysBlock" in html)
    true("says is the primary row text", "wtp-says" in html)
    true("CSS uses .wtp (not nav .ways)", ".wtp{" in html)
    true("waysBlock radiogroup is .wtp", 'class="wtp"' in html)
    true("row class is .wtp-row", "wtp-row" in html)
    # Nav still owns .ways — playstyle rules must not redefine it to column.
    nav_ways = html.find(".ways{display:flex;gap:var(--s1)")
    playstyle_ways_column = html.find(".ways{display:flex;flex-direction:column")
    true("playstyle CSS does not redefine nav .ways as column",
         playstyle_ways_column < 0, f"nav@{nav_ways} conflict@{playstyle_ways_column}")
    true("cap constant is 3", "WAYS_ON_CARD = 3" in html)
    true("empty ways say not inventing", "No ways detected" in html)
    true("ways sit before write-up in the pick card",
         html.find("waysBlock(p)") < html.find("The deck you'd build"))


def test_treasure_maker_not_offered_as_creature_tokens():
    """CON-213 / CON-208: Lannery Treasure ≠ creature-tokens way on the card."""
    print("\npicker precision — Treasure is not the tokens way")
    from deckwright import playstyles
    lannery = {
        "name": "Captain Lannery Storm",
        "type_line": "Legendary Creature — Human Pirate",
        "oracle_text": (
            "Haste\n"
            "Whenever Captain Lannery Storm attacks, create a Treasure token.\n"
            "Whenever you sacrifice a Treasure, Captain Lannery Storm gets "
            "+1/+0 until end of turn."
        ),
    }
    r = playstyles.detect(lannery)
    true("status ok", r.get("status") == "ok", str(r))
    true("not labelled creature-tokens", "tokens" not in (r.get("matched") or []),
         str(r.get("matched")))


def main() -> int:
    test_way_changes_commitments()
    test_preselect_from_strategy_match()
    test_picker_surfaces_way_source()
    test_picker_shows_says_not_machine_id()
    test_treasure_maker_not_offered_as_creature_tokens()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
