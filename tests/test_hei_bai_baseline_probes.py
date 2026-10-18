"""
Offline probes for scripts/hei_bai_baseline.py.

The live script needs a running server. These assert the measurement itself:
causation of leaving, not the leave phrase.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "hei_bai_baseline", ROOT / "scripts" / "hei_bai_baseline.py")
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def fail(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        fail(label, f"got {got!r} want {want!r}")


def _count(label, cards):
    pattern = dict(mod.PROBES)[label]
    rx = re.compile(pattern, re.I)
    return sum(1 for c in cards
               if rx.search(c.get("text") or c.get("oracle_text") or ""))


def main() -> int:
    print("\nhei_bai_baseline probes — causation, not phrasing")
    labels = [p[0] for p in mod.PROBES]
    eq("has sac-outlet cause probe",
       "CAUSES leaving: sac outlet" in labels, True)
    eq("has blink cause probe",
       "CAUSES leaving: blink/flicker" in labels, True)
    eq("keeps phrase probe as separate line",
       "TRIGGERS on leaving (phrase)" in labels, True)
    eq("old phrase-only leave probe is gone",
       "leaves the battlefield" in labels, False)

    deck = [
        {"name": "Deadly Dispute",
         "text": ("As an additional cost to cast this spell, sacrifice an "
                  "artifact or creature. Draw two cards and create a Treasure "
                  "token.")},
        {"name": "Cloudshift",
         "text": ("Exile target creature you control, then return that card "
                  "to the battlefield under your control.")},
        {"name": "Hei Bai, Spirit of Balance",
         "text": ("Whenever Hei Bai enters or attacks, you may sacrifice "
                  "another creature or artifact. If you do, put two +1/+1 "
                  "counters on Hei Bai.\nWhen Hei Bai leaves the battlefield, "
                  "put its counters on target creature you control.")},
    ]
    eq("sac outlet counted as cause",
       _count("CAUSES leaving: sac outlet", deck) >= 1, True)
    eq("blink counted as cause",
       _count("CAUSES leaving: blink/flicker", deck), 1)
    # Hei Bai himself triggers on leaving — phrase probe, not a cause probe.
    eq("commander leave phrase is not a blink cause",
       _count("CAUSES leaving: blink/flicker",
              [c for c in deck if c["name"].startswith("Hei")]), 0)
    eq("phrase probe sees the commander",
       _count("TRIGGERS on leaving (phrase)", deck), 1)

    print(f"\n{passed} passed" + (f", {RED}{failed} failed{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
