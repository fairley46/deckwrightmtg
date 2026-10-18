"""End-to-end: a wish -> a deck, checked against docs/DERIVATION.md.

Not a unit test. This spends real model quota and hits real Scryfall, on
purpose -- it is the only check that exercises the whole chain at once:

    wish -> chat/brief -> commanders -> build -> plan + 99 cards

Every assertion is one of DERIVATION.md's falsifiable checks. The house rule
applies to this script too: where something cannot be measured it is reported
UNKNOWN, never PASS and never a zero. A check with nothing to check on is
UNKNOWN -- an empty list must never satisfy an "all(...)" assertion.
"""
import json, sys, urllib.request

BASE = "http://127.0.0.1:8130"
WISH = ("I want a deck about vampires that drain the table -- lots of small "
        "life loss triggers rather than one big swing, and I like it when the "
        "life I gain does something.")

def post(path, body, timeout=900):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{ {True:'PASS',False:'FAIL',None:'UNKNOWN'}[ok] :7}] {name}"
          + (f"  -- {detail}" if detail else ""), flush=True)

def all_of(name, seq, pred, detail=""):
    """all() over an empty list is True -- that is the vacuous pass this whole
    session has been hunting. Nothing to check means UNKNOWN, not PASS."""
    if not seq:
        return check(name, None, "nothing to check -- vacuous, not passing")
    check(name, all(pred(x) for x in seq), detail)

print("\n1. INTERROGATION  (the wish becomes stated intent)", flush=True)
chat = post("/api/chat", {"messages": [{"role": "user", "content": WISH}]})
text, brief = chat.get("text") or "", chat.get("brief") or {}
readiness = chat.get("readiness") or {}
check("chat answers", bool(text), f"{len(text)} chars")
check("chat extracts a brief", bool(brief.get("theme")),
      f"theme={brief.get('theme')!r} colors={brief.get('colors')!r}")
check("readiness is stated", "ready" in readiness,
      f"ready={readiness.get('ready')} blockers={len(readiness.get('blockers') or [])}")

print("\n2. COMMANDERS  (grammar decides what she could have)", flush=True)
cmd = post("/api/commanders", brief)
cands = cmd.get("picks") or []
check("commanders returned", len(cands) > 0, f"{len(cands)} picks")
names = [c.get("name") for c in cands if isinstance(c, dict) and c.get("name")]
all_of("every commander is a real named card", cands,
       lambda c: isinstance(c, dict) and bool(c.get("name")),
       ", ".join(names[:3]))
# DERIVATION: falling back to a generic pool is allowed; hiding it is not.
check("the query that produced these is reported",
      bool(cmd.get("query")), f"query={str(cmd.get('query'))[:70]}")
check("a fallback says so", "note" in cmd, f"note={str(cmd.get('note'))[:60]!r}")

if not names:
    print("\nno commander -> cannot build. stopping.", flush=True)
    sys.exit(1)

pick = names[0]
print(f"\n3. BUILD  ({pick})", flush=True)
build = post("/api/build", {"brief": brief, "commander": pick})
deck = build.get("deck") or []
check("deck is 99 cards", len(deck) == 99, f"{len(deck)} cards")
distinct = len({c.get("name") for c in deck if isinstance(c, dict)})
check("deck is not one card repeated", distinct > 60, f"{distinct} distinct names")
all_of("every deck card is a real named card", deck,
       lambda c: isinstance(c, dict) and bool(c.get("name")))

print("\n4. THE PLAN  (the plan decides what she needs)", flush=True)
plan = build.get("plan")
check("build returns a plan", plan is not None, "the plan-on-build work")
if plan:
    arch = plan.get("archetype") or {}
    check("archetype present", bool(arch.get("label") or arch.get("candidates")),
          f"decided={arch.get('decided')} label={arch.get('label')!r}")
    commits = plan.get("commitments") or []
    check("plan states commitments", len(commits) > 0, f"{len(commits)} commitments")
    missing = [c.get("label", "?") for c in commits
               if (c.get("counts") or {}).get("min") is None]
    all_of("every commitment carries a count", commits,
           lambda c: (c.get("counts") or {}).get("min") is not None,
           "; ".join(missing) or "all counted")
    for c in commits[:6]:
        print(f"        {c.get('label','?'):<28} wants {(c.get('counts') or {}).get('min')}",
              flush=True)

print("\n5. THE ONE RULE  (every name verified against Scryfall)", flush=True)
for key in ("invented", "dropped", "backfilled"):
    if key in build:
        check(f"verifier reports {key}", True, f"{len(build[key] or [])}")
        break
else:
    check("verifier count surfaced on the response", None,
          "not exposed by /api/build -- covered by test_deckwright.py, not measurable here")

print("\n" + "=" * 66)
p = sum(1 for _, o, _ in results if o is True)
f = sum(1 for _, o, _ in results if o is False)
u = sum(1 for _, o, _ in results if o is None)
print(f"  {p} pass · {f} fail · {u} unknown")
for n, o, d in results:
    if o is False:
        print(f"    FAIL  {n}  {d}")
print("=" * 66)
sys.exit(1 if f else 0)
