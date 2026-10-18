"""
forge_log.py — turn a Forge game log into a per-turn timeline.

Forge plays real games. `sim.py` is a goldfish with no opponent, no removal
and no interaction. Every threshold in `docs/SCORING-CONTRACT.md` was measured
against the goldfish, so the numbers are self-consistent and unvalidated —
**the gap between the two is the correction signal.**

`pressure.py` had been receiving the whole log and throwing it
away one line into parsing:

    def parse_sim_output(text, hero_name):
        \"\"\"Pull winner + turn count from Forge stdout. Nothing else.\"\"\"

789 lines from one game, discarded at the door. Nothing needed building to
*get* this data — only to stop dropping it.

This module is a pure function: log text in, timeline out. No Java, no
subprocess, no network. `backend-review/fixtures/forge-game-log.txt` is a real
game so the parser can be tested and changed without Forge installed.

CALIBRATION, NOT SELECTION. A deliberate choice: tuning decks by
Forge win-rate would quietly replace her goal with the AI's. We are asking
whether our *math* is right, not whether the deck wins. Nothing here belongs
on the build path.

    python forge_log.py backend-review/fixtures/forge-game-log.txt
"""

from __future__ import annotations

import re
import sys
from typing import Any

# Forge prefixes every event line with its type. Counts from one real game:
#   Phase 385 · Mana 132 · Add To Stack 46 · Resolve Stack 45 · Turn 30
#   Damage 24 · Combat 20 · Land 19 · Zone Change 12 · Life 11
_TURN = re.compile(r"^Turn: Turn (\d+) \((.+)\)\s*$")
_LIFE = re.compile(r"^Life: Life: (.+?) (\d+) > (-?\d+)\s*$")
_LAND = re.compile(r"^Land: (.+?) played (.+?)(?: \(\d+\))?\s*$")
_ZONE = re.compile(r"^Zone Change: (.+?)(?: \(\d+\))? was put into (\w+) from (\w+)\.")
_RESOLVE = re.compile(r"^Resolve Stack: (.+?)(?: \(\d+\))? - (.+?) ")
_OUTCOME = re.compile(r"^Game Outcome: (.+?) has (won|lost)")

# THESIS_KEYS this log can and cannot support. Stated so a caller never
# silently reads a zero for something Forge does not report.
MEASURABLE = (
    "life_gained", "life_drained", "lands_in_play",
)
NOT_MEASURABLE = (
    # No "Draw:" event type in the log. Cards moving Library -> Hand are not
    # emitted as Zone Change either. An unknown is not a zero: a caller
    # comparing cards_drawn against the goldfish must skip it, not read 0.
    "cards_drawn", "cards_in_hand",
    # VERIFIED ABSENT, not assumed. Zone Change fires only for permanents
    # LEAVING the battlefield -- 12 lines in the fixture game, every one
    # "was put into Graveyard/Exile from Battlefield", zero entering. So a
    # board count is unmeasurable from a Forge log, and a parser that
    # reported one would report 0 forever. That is the failure this whole
    # effort keeps finding: an unknown dressed as a zero.
    "creatures_in_play", "payoffs_in_play",
    "mana_available",      # Mana: lines are activations, not a pool total
    "extra_land_drops", "land_drops_this_turn",
    "board_power",         # power/toughness are not in the log
)


def _blank_turn(n: int, player: str | None) -> dict[str, Any]:
    return {
        "turn": n, "player": player,
        "life": {}, "life_gained": {}, "life_drained": {},
        "lands_played": {}, "permanents_entered": [], "permanents_left": [],
    }


def parse(text: str, hero: str | None = None) -> dict[str, Any]:
    """Log text -> {hero, turns: [...], outcome, measurable, not_measurable}.

    `hero` is the deck name Forge printed (it names players Ai(1)-<deck>).
    When omitted, the first player seen on a Life: line is used, which is not
    always the hero — pass it explicitly for calibration runs.
    """
    turns: list[dict[str, Any]] = []
    cur = _blank_turn(0, None)
    outcome: dict[str, Any] = {"winner": None, "loser": None, "last_turn": None}

    for raw in text.splitlines():
        line = raw.strip()

        m = _TURN.match(line)
        if m:
            if cur["turn"] or cur["life"] or cur["permanents_entered"]:
                turns.append(cur)
            cur = _blank_turn(int(m.group(1)), m.group(2))
            outcome["last_turn"] = int(m.group(1))
            continue

        m = _LIFE.match(line)
        if m:
            who, before, after = m.group(1), int(m.group(2)), int(m.group(3))
            cur["life"][who] = after
            delta = after - before
            key = "life_gained" if delta > 0 else "life_drained"
            cur[key][who] = cur[key].get(who, 0) + abs(delta)
            if hero is None:
                hero = who
            continue

        m = _LAND.match(line)
        if m:
            who, land = m.group(1), m.group(2)
            cur["lands_played"].setdefault(who, []).append(land)
            continue

        m = _ZONE.match(line)
        if m:
            card, to_zone, from_zone = m.group(1), m.group(2), m.group(3)
            if to_zone == "Battlefield":
                cur["permanents_entered"].append(card)
            elif from_zone == "Battlefield":
                cur["permanents_left"].append(card)
            continue

        m = _OUTCOME.match(line)
        if m:
            outcome["winner" if m.group(2) == "won" else "loser"] = m.group(1)

    turns.append(cur)
    return {
        "hero": hero,
        "turns": [t for t in turns if t["turn"]],
        "outcome": outcome,
        "measurable": list(MEASURABLE),
        "not_measurable": list(NOT_MEASURABLE),
    }


def cumulative(timeline: dict[str, Any], player: str) -> list[dict[str, Any]]:
    """Per-turn running state for one player — the shape sim.run() curves use.

    Board counts are deliberately ABSENT -- see NOT_MEASURABLE. Forge logs
    permanents leaving the battlefield and never entering, so any count would
    be wrong in one direction forever.

    `life_drained` here matches THESIS_KEYS: life opponents lost (what you
    drained / damaged), not life this player lost. Per-turn `life_drained`
    dicts still key by the player who lost the life — we sum everyone else.
    In 1v1 that is the only signal the log gives for "drain dealt"; it also
    includes combat damage, which the goldfish does not model.
    """
    gained = drained = lands = 0
    board: set[str] = set()
    out = []
    for t in timeline["turns"]:
        gained += t["life_gained"].get(player, 0)
        for who, amt in t["life_drained"].items():
            if who != player:
                drained += amt
        lands += len(t["lands_played"].get(player, []))
        board -= set(t["permanents_left"])   # entering is never logged
        out.append({
            "turn": t["turn"],
            "life": t["life"].get(player),
            "life_gained": gained,
            "life_drained": drained,
            "lands_in_play": lands,
        })
    return out


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "backend-review/fixtures/forge-game-log.txt"
    tl = parse(open(path).read())
    print(f"hero: {tl['hero']}")
    print(f"turns: {len(tl['turns'])}   outcome: {tl['outcome']}")
    print(f"\nNOT measurable from a Forge log: {', '.join(tl['not_measurable'])}")
    print(f"\nper-turn for {tl['hero']}:")
    print(f"  {'T':>3} {'life':>5} {'gained':>7} {'drained':>8} {'lands':>6}")
    for row in cumulative(tl, tl["hero"]):
        print(f"  {row['turn']:>3} {str(row['life']):>5} {row['life_gained']:>7} "
              f"{row['life_drained']:>8} {row['lands_in_play']:>6}")
