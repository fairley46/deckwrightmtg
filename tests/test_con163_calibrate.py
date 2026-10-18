#!/usr/bin/env python3
"""CON-163: calibration harness compares MEASURABLE keys only, on hero turns."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from deckwright import forge_log
import con163_calibrate_forge as cal

G, R, OFF = "\033[32m", "\033[31m", "\033[0m"
_p, _f = [], []
def ok(m, d=""): _p.append(m); print(f"  {G}pass{OFF} {m}")
def bad(m, d=""): _f.append(m); print(f"  {R}FAIL{OFF} {m}  {d}")
def eq(m, got, want): (ok if got == want else bad)(m, f"got {got!r}, want {want!r}")

print("\ncon163 calibrate — fixture, no Java")

# Every condition key must be MEASURABLE.
for c in cal.CONDITIONS:
    for k in c["check"]:
        (ok if k in forge_log.MEASURABLE else bad)(
            f"{c['label']} key {k} is MEASURABLE", k)
        (ok if k not in forge_log.NOT_MEASURABLE else bad)(
            f"{c['label']} key {k} not NOT_MEASURABLE", k)

_FIXTURE = _REPO / "testdata" / "review" / "forge-game-log.txt"
tl = forge_log.parse(_FIXTURE.read_text())
rows = cal.hero_timeline(tl)
(ok if rows else bad)("hero timeline non-empty", str(rows[:1]))
# Fixture: hero goes second — Forge turn 2 is hero turn 1.
eq("first hero forge_turn", rows[0]["forge_turn"], 2)
eq("first hero_turn index", rows[0]["hero_turn"], 1)
# Hero turns are contiguous 1..N
eq("hero turns contiguous",
   [r["hero_turn"] for r in rows],
   list(range(1, len(rows) + 1)))

# life_drained is opponent loss (THESIS), not damage hero took.
(ok if rows[-1]["life_drained"] >= 20 else bad)(
    "fixture life_drained is opponent loss", str(rows[-1]))

# Hit detection on lands.
ht = cal.hit_turn(rows, {"lands_in_play": 5})
(ok if ht is not None and ht <= 10 else bad)("lands>=5 hits", str(ht))

# Curve shape: once hit, stays hit.
hits = [3, 5, None, 2]
curve = cal.forge_curve(hits, max_turn=6)
eq("forge_curve T1", curve[0], 0.0)          # nobody hit by 1
eq("forge_curve T2", curve[1], 25.0)         # 1/4 hit by 2
eq("forge_curve T5", curve[4], 75.0)         # 3/4
eq("forge_curve T6", curve[5], 75.0)

n = len(_p) + len(_f)
print()
if _f:
    print(f"{R}{len(_f)} of {n} failed{OFF}"); sys.exit(1)
print(f"{G}{n} passed{OFF}")
