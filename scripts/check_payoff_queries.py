#!/usr/bin/env python3
"""Gate every payoff pattern against live Scryfall (shape).

A payoff query that returns nothing fails the build. But returning SOMETHING
is not enough — these are hand-written patterns and four of the original nine
returned lands and cantrips instead of payoffs. This also prints the top hits
so a wrong pattern is visible in review rather than discovered in a deck.

    python scripts/check_payoff_queries.py
"""
import json, sys, time, urllib.parse, urllib.request
sys.path.insert(0, ".")
from deckwright import plan_graph

UA = {"User-Agent": "Deckwright/1.0 (payoff gate)", "Accept": "application/json"}
FLOOR = 20          # a payoff FAMILY thinner than this is not worth a rung
ARCH_FLOOR = 2      # an archetype extra may be tiny and still decisive:
                    # o:"whenever an opponent loses life" o:"gain that much
                    # life" is 2 cards, and one of them is Exquisite Blood.

def probe(q):
    u = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": q, "order": "edhrec"})
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
        d = json.load(r)
    return d.get("total_cards", 0), [c["name"] for c in d["data"][:3]]

# Archetype extras were NOT gated when they first shipped, and it showed: the
# burst set included o:"you win the game", which returns Thassa's Oracle and
# Laboratory Maniac — alt-wincons unrelated to the plan — and produced a deck
# with one payoff and zero combo pieces. Same rule as the families: a pattern
# is gated on what it RETURNS, not on returning something.
TARGETS = list(plan_graph._PAYOFF_QUERIES.items())
for arch, spec in plan_graph._ARCHETYPES.items():
    for i, q in enumerate(spec.get("extra_payoffs", ()) or ()):
        TARGETS.append((f"{arch}[{i}]", q))

# Probe each pattern BOTH bare and as the build actually uses it — wrapped and
# colour-scoped. Cursor found that `q OR r id<=WB` binds the identity filter to
# r alone under Scryfall precedence, so one arm returned colour-illegal cards
# and Thassa's Oracle reached a white-black deck. The bare probe cannot see
# that: the bug only exists once id<= is appended. A gate that tests a query in
# a shape the app never sends is testing the wrong thing.
SCOPED_CI = "WB"          # any two-colour identity exercises the precedence

dead = []
scoped_bad = []
throttled = []
for fam, q in TARGETS:
    try:
        n, names = probe(q)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Scryfall throttled us. That is NOT a dead query, and reporting it
            # as one is the exact lie this gate exists to prevent: on a rate
            # limit every pattern reads 0 and the whole file looks broken.
            # Back off and retry rather than emit a confident zero.
            time.sleep(3.0)
            try:
                n, names = probe(q)
            except Exception:
                throttled.append(fam)
                print(f"  SKIP     ?  {fam:<22} Scryfall 429 — not measured")
                continue
        else:
            n, names = 0, [f"HTTP {e.code}"]
    except Exception as e:
        n, names = 0, [f"{type(e).__name__}"]
    flag = "ok " if n >= (ARCH_FLOOR if "[" in fam else FLOOR) else "DEAD"
    floor = ARCH_FLOOR if "[" in fam else FLOOR
    if n < floor:
        dead.append((fam, n))
    print(f"  {flag} {n:>5}  {fam:<22} {', '.join(names)}")
    time.sleep(0.12)

    # The shape the build sends: wrapped, then colour-scoped.
    if " or " in q.lower():
        try:
            sn, _ = probe(f"({q}) id<={SCOPED_CI}")
            un, _ = probe(f"{q} id<={SCOPED_CI}")
        except Exception:
            sn = un = 0
        if un > sn:
            scoped_bad.append((fam, sn, un))
            print(f"       PRECEDENCE  wrapped={sn} vs bare={un} "
                  f"-> bare id<= leaks colour-illegal cards")
        time.sleep(0.24)

if throttled:
    print(f"\n{len(throttled)} pattern(s) NOT MEASURED — Scryfall rate limit:")
    for fam in throttled:
        print(f"   {fam}")
    print("An unknown is not a zero. Re-run when the limit clears.")

if scoped_bad:
    print(f"\n{len(scoped_bad)} pattern(s) leak colour-illegal cards when id<= is "
          f"appended without wrapping:")
    for fam, sn, un in scoped_bad:
        print(f"   {fam}: wrapped {sn} vs bare {un}")

if dead:
    print(f"\n{len(dead)} payoff pattern(s) under the floor of {FLOOR}:")
    for fam, n in dead:
        print(f"   {fam} -> {n}")
    sys.exit(1)

# Incomplete measurement is not a green gate. check_all maps exit 2 → SKIP
# so a 429 cannot look like "all patterns live".
if throttled:
    sys.exit(2)
print(f"\nall {len(TARGETS)} payoff patterns live")
