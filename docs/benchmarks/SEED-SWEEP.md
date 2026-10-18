# Seed sweep — E / BLUE / D

Three-way bake-off over the frozen 24. **Not** a five-way — B and C are
disabled in `contestants.json` (measured no-better-than-V1).

Ship design: [`docs/product/SHIP-V2.md`](../product/SHIP-V2.md).

## Harness

On `main` after the build-timeout floor / the per-contestant URL scoping restore PR. `DEFAULT_BUILD_TIMEOUT` must be
**1800**. `--validate` prints it. `20260902T172439Z` is VOID — do not cite.

## Ports (do not move seed to :8001)

| Contestant | Port | Role |
|------------|------|------|
| E | fixture | Crowd ceiling (harvested lists) |
| BLUE | `:8001` | V1 control — **no** `EDHREC_SEED` |
| D | `:8004` | V1 + EDHREC pool seed |

## Primary metrics (how to judge)

1. **Input feeder density** — toward E vs BLUE (`top_100` baselines: E ≈ 0.381, V1 ≈ 0.308)
2. **Keepable hand %** — no regression vs BLUE (~80% bar)

**Overlap with E** — sanity only. High on D is expected. Bound violations secondary.

## Pre-flight (demo host)

**Memory:** 8GB demo host. Quit Chrome before a sweep (~4GB). Deckwright servers are
tiny; Chrome + a 650MB build forces paging and stretches builds past any timeout.
Reboot clears dirty swap if macOS has already paged hard. Stopping B/C does not
fix Chrome pressure.

```bash
cd ~/Projects/deckwright-green
source .venv/bin/activate
git pull origin main

# Confirm timeout floor restored
python scripts/v2_benchmark.py --validate | grep DEFAULT_BUILD_TIMEOUT
# Expect: DEFAULT_BUILD_TIMEOUT=1800

for p in 8001 8004; do
  PID=$(lsof -t -iTCP:$p -sTCP:LISTEN | head -1)
  echo "=== :$p ==="
  ps eww "$PID" | tr ' ' '\n' | grep -E 'DECKWRIGHT_(CANDIDATE|EDHREC_SEED|LLM_TIMEOUT)='
done

python test_v2_benchmark.py
```

## Launch (~5h, 24 × 3)

**Do not pass `--green-url`** (collapses ports). Per-contestant
overrides are `--green-b-url` / `--green-c-url` / `--green-d-url` only.

```bash
tmux new-session -d -s seed-sweep \
  'python scripts/v2_benchmark.py --contestants E,BLUE,D 2>&1 | tee /tmp/seed-sweep.log'

tail -f /tmp/seed-sweep.log
```

Reports → `docs/benchmarks/runs/<utc>/`. Header must show `build_timeout_s: 1800`
and distinct `green_urls` for BLUE and D.

## Reading the result

**D wins when** feeder density moves toward E on `top_100` / `101_1000` and
keepable does not regress vs BLUE. Then open the blue promotion PR in SHIP-V2.

**Does not prove:** fun at the table — play a few seeded lists after the numbers.
