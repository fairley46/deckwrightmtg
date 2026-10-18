# Bake-off readiness — E / BLUE / B / C

What the maintainer does on the demo host **after** the dual-floor and LLM-timeout fix the MV floor bounds land on `main`,
**before** treating the four-way sweep as a fair contest.



## Product prereqs (repo)

| # | Check | Evidence |
|---|---|---|
| 1 | The dual-floor and LLM-timeout fix merged | green + green-b templates include `DECKWRIGHT_LLM_TIMEOUT=600` |
| 2 | The MV floor bounds merged | `python scripts/bound_coverage.py` → `SEEN=6 NONE=0` |
| 3 | #314 rebased onto that tip | green-c plist also carries `DECKWRIGHT_LLM_TIMEOUT=600` |
| 4 | #314 merged only when Brad says wire C | draft until then — do not promote |

## Demo host deploy (green worktree only)

```bash
cd ~/Projects/deckwright-green   # green worktree — not blue
git fetch origin && git checkout main && git pull origin main

./config/install-green-launchd.sh       # :8001 candidate=off (BLUE)
./config/install-green-b-launchd.sh     # :8002 candidate=B
# After #314 merges:
./config/install-green-c-launchd.sh     # :8003 candidate=C

# Confirm timeout on every green process:
#   ps eww <pid> | tr ' ' '\n' | grep DECKWRIGHT_LLM_TIMEOUT
# Expect 600 on :8001, :8002, and :8003.

curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/   # blue — leave alone
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/   # after C install
```

## Bound coverage

```bash
source .venv/bin/activate
python scripts/bound_coverage.py
```

Expect all six bound-declaring commanders **SEEN**. Meren's experience
capacity remains unknown at audit time (honest); extract is still SEEN.

## CardFacts coverage (before re-sweep)

```bash
python scripts/cardfacts_coverage.py --reextract
# ONLY bake into green-c home — never bare ~/.deckwright (blue home):
DECKWRIGHT_HOME=~/.deckwright-green-c python scripts/bake_cardfacts.py --force
python scripts/cardfacts_coverage.py --store ~/.deckwright-green-c/cardfacts.json
# or: DECKWRIGHT_HOME=~/.deckwright-green-c python scripts/cardfacts_status.py
```

Target before another four-way sweep: **`ok` well above the Gate C ~6% deck
slice** (post-#317 floor ~30% ok / ~54% steer-usable; coverage PRs raise that
further). Vanilla creatures and lands stay `unread` by design — not failures.
**Do not** run `bake_cardfacts.py --force` without `DECKWRIGHT_HOME` — that
writes into blue's `~/.deckwright`.

Bound column for ranking: use `violation_count_payoff`, not raw
`violation_count` (ramp rocks count as floor violations but are load-bearing).

## Sweep

Contestants: **E, BLUE, B, C**.

- BLUE → `:8001` off
- B → `:8002`
- C → `:8003`
- **No `--green-url` on multi-contestant sweeps** — that collapses
  ports onto one process and invents agreement.

Harness checkout (line) once product + C wire are on one tree:

```bash
python scripts/v2_benchmark.py --contestants E,BLUE,B,C
```

## Explicit non-goals

- Do not flip blue / promote B
- Do not set `DECKWRIGHT_CANDIDATE=C` as the shared install default
- Do not full-sweep unless needed for verification
- Do not score unknown capacity as zero violations
