#!/usr/bin/env python3
"""CON-160: forge_log parses a real game without Java in the loop."""

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
from pathlib import Path

from deckwright import forge_log
from deckwright import sim

G, R, OFF = "\033[32m", "\033[31m", "\033[0m"
_p, _f = [], []
def ok(m, d=""): _p.append(m); print(f"  {G}pass{OFF} {m}")
def bad(m, d=""): _f.append(m); print(f"  {R}FAIL{OFF} {m}  {d}")
def eq(m, got, want): (ok if got == want else bad)(m, f"got {got!r}, want {want!r}")

LOG = str(_REPO / "testdata" / "review" / "forge-game-log.txt")
tl = forge_log.parse(open(LOG).read())

print("\nforge_log — real game fixture")
eq("hero detected", tl["hero"], "Ai(1)-Abzan Siege")
eq("turns parsed", len(tl["turns"]), 30)
eq("winner", tl["outcome"]["winner"], "Ai(1)-Abzan Siege")
eq("loser", tl["outcome"]["loser"], "Ai(2)-Aerodoom")

rows = forge_log.cumulative(tl, tl["hero"])
eq("one row per turn", len(rows), 30)
(ok if rows[-1]["life_gained"] > 0 else bad)("life gained moves", str(rows[-1]))
(ok if rows[-1]["life_drained"] > 0 else bad)("life drained moves", str(rows[-1]))
# CON-163: life_drained is opponent life lost (THESIS), not damage the hero took.
# Hero won this fixture; opponent lost far more than the 6 life the hero lost.
(ok if rows[-1]["life_drained"] >= 20 else bad)(
    "life_drained is opponent loss, not hero damage taken", str(rows[-1]))
(ok if rows[-1]["lands_in_play"] >= 8 else bad)("lands accumulate", str(rows[-1]))
# monotonic: cumulative counters never go backwards
mono = all(rows[i]["lands_in_play"] <= rows[i+1]["lands_in_play"]
           for i in range(len(rows)-1))
(ok if mono else bad)("lands are monotonic", "")

print("\nhonesty — an unknown is not a zero")
# The whole point. A key Forge cannot report must be declared unmeasurable,
# never returned as 0. Board counts read 0 forever because Zone Change only
# fires on permanents LEAVING the battlefield.
for key in ("creatures_in_play", "payoffs_in_play", "cards_drawn"):
    (ok if key in tl["not_measurable"] else bad)(
        f"{key} declared unmeasurable", str(tl["not_measurable"]))
    (ok if key not in rows[-1] else bad)(
        f"{key} absent from the timeline, not zero", str(rows[-1]))
for key in tl["measurable"]:
    (ok if key in sim.THESIS_KEYS else bad)(
        f"{key} is a live THESIS_KEY", key)
# every claimed key must actually appear
for key in ("life_gained", "life_drained", "lands_in_play"):
    (ok if key in rows[-1] else bad)(f"{key} present in rows", str(rows[-1]))

n = len(_p) + len(_f)
print()
if _f:
    print(f"{R}{len(_f)} of {n} failed{OFF}"); sys.exit(1)
print(f"{G}{n} passed{OFF}")
