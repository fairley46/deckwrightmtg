"""
hei_bai_baseline.py — build the tutorial's commander with the system we have.

Commander Challenge, "How to Build a Commander Deck from Scratch" (YJ8X8bO8jeY)
builds Hei Bai, Spirit of Balance and publishes the result. That gives us
something this project has been short of: a 99 chosen by an expert, for a
commander we did not pick ourselves.

This captures OUR answer for the same commander, so a change to the scoring
layer can be measured rather than asserted. Run it before and after.

Counts what the tutorial says the deck needs, by probing the deck's own oracle
text -- never a hand-written card list:

    sacrifice fodder / outlets   the commander eats a creature or artifact
    counter payoffs              it makes +1/+1 counters
    enters / attacks triggers    it triggers on both
    CAUSES leaving               its second ability needs him to leave
                                 (sac outlet / blink — not the leave phrase)

    python scripts/hei_bai_baseline.py [--base URL] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

CMDR = "Hei Bai, Spirit of Balance"
WISH = ("I want to build Hei Bai, Spirit of Balance. Sacrifice creatures and "
        "artifacts to make him giant with counters, then move those counters "
        "somewhere else when he leaves the battlefield.")

# What the tutorial derives from the card, as oracle-text probes.
#
# CON-174: "leaves the battlefield" greps cards that TRIGGER on leaving.
# Hei Bai needs cards that CAUSE him to leave. Measure causation separately;
# keep the phrase probe so a real leave-trigger in the 99 is still visible.
PROBES = [
    ("sacrifice outlet/fodder",        r"sacrific"),
    ("plus-one counters",              r"\+1/\+1 counter"),
    ("enters trigger",                 r"when(ever)? [^.]{0,40}enters"),
    ("attacks trigger",                r"when(ever)? [^.]{0,40}attacks"),
    ("dies trigger",                   r"when(ever)? [^.]{0,40}dies"),
    ("CAUSES leaving: sac outlet",     r"sacrifice (a|an|another) (creature|permanent|artifact)"),
    ("CAUSES leaving: blink/flicker",  r"exile [\s\S]{0,120}?return .{0,40}to the battlefield"),
    ("TRIGGERS on leaving (phrase)",   r"leaves the battlefield"),
    ("token maker",                    r"create (a|x|two|three|\d+) [^.]{0,60}token"),
    ("graveyard recursion",            r"from your graveyard"),
    ("card draw",                      r"draw (a card|two|three|\d+)"),
]


def post(base, path, body, timeout=1200):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8140")
    ap.add_argument("--out", default="/tmp/hei_bai_baseline.json")
    args = ap.parse_args()

    print(f"commander: {CMDR}\n")

    msgs = [{"role": "user", "content": WISH}]
    answers = ["Black and white. Around 150 dollars.",
               "Just my husband and two friends, nothing sweaty.",
               "Whatever you think fits best."]
    brief = None
    for turn in range(4):
        d = post(args.base, "/api/chat", {"messages": msgs})
        brief = d.get("brief")
        print(f"  turn {turn}: still_asking={d.get('still_asking')} "
              f"brief={'yes' if brief else 'no'} err={d.get('brief_error')}")
        if brief:
            break
        if turn >= len(answers):
            print("  did not converge")
            return 1
        msgs = msgs + [{"role": "assistant", "content": d.get("text") or ""},
                       {"role": "user", "content": answers[turn]}]

    if not brief:
        return 1
    print(f"  brief: theme={brief.get('theme')!r} colors={brief.get('colors')!r}\n")

    build = post(args.base, "/api/build", {"brief": brief, "commander": CMDR})
    deck = build.get("deck") or []
    plan = build.get("plan") or {}
    arch = plan.get("archetype") or {}

    print(f"  deck: {len(deck)} cards")
    print(f"  archetype: {arch.get('label')!r} decided={arch.get('decided')}")
    for c in (plan.get("commitments") or [])[:6]:
        want = (c.get("counts") or {}).get("min")
        print(f"     {str(c.get('label', '?')):<42} wants {want}")

    print("\n  what the tutorial says this commander needs:")
    counts = {}
    for label, pattern in PROBES:
        rx = re.compile(pattern, re.I)
        # This app's cards carry oracle text as `text`, not `oracle_text`.
        # Reading the wrong key returns 0 for every probe -- which it did, and
        # "0 of 99 card draw" is not a finding, it is a broken measurement.
        n = sum(1 for c in deck
                if rx.search(c.get("text") or c.get("oracle_text") or ""))
        counts[label] = n
        print(f"     {label:<26} {n:>3} of {len(deck)}")

    payload = {"commander": CMDR, "brief": brief,
               "deck": [c.get("name") for c in deck],
               "archetype": arch, "probe_counts": counts,
               "commitments": plan.get("commitments") or []}
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
