# Ship V2 — locked design

**Status:** design locked 2026-09-06. Product code for the seed path is on
`main`. Blue `:8000` stays V1 until the promotion PR below lands.

## What V2 is

**EDHREC-seeded V1.** Crowd average deck seeds the role pools; V1 still owns
lands, curve, budget, legality, and the three verifiers.

One sentence for her: *built from what people play, tuned so you can cast it.*

This is **not** candidate B (clarity/steer) or C (CardFacts). Both lost measured
bake-offs. Do not revive them for the product path.

## Ports (frozen)

| Port | Role | Seed? |
|------|------|-------|
| `:8000` | Blue production | **no** until promotion PR |
| `:8001` | Harness BLUE (V1 control) | **never** |
| `:8002` / `:8003` | B / C | stopped; plists on disk (stop ≠ retirement) |
| `:8004` | **V2 green product** | `DECKWRIGHT_EDHREC_SEED=1` |

Putting seed on `:8001` invalidates every prior sweep (CON-330 class).

## How we judge (one clean sweep, then stop)

Harness: `scripts/v2_benchmark.py --contestants E,BLUE,D`.

| Signal | Role |
|--------|------|
| **Input feeder density** | Primary — toward E vs BLUE |
| **Keepable hand %** | Primary — no regression vs BLUE (~80% bar) |
| Overlap with E | Sanity only — D high is expected |
| Fun at table | Manual play of a few seeded lists after numbers |

`docs/benchmarks/runs/20260902T172439Z` is **VOID** (timeout regression). Do not
cite it.

## Gate to flip blue

Ship to `:8000` when **either**:

1. **One clean E/BLUE/D sweep** after harness CON-328/330 restore — D moves
   feeder density toward E on `top_100` / `101_1000` without keepable regression, **or**
2. **Explicit human call** — "ship without sweep" — then skip the bake-off.

Promotion is a **separate one-purpose PR**:

1. `edhrec_seed.enabled()` allows blue when `DECKWRIGHT_EDHREC_SEED=1`
2. Blue plist sets the flag
3. Tests updated (`test_enabled_green_only` → env-gated, not green-only forever)
4. Docs: this file → shipped; `EDHREC-SEED.md` ports table

Do not flip blue as a side effect of a harness or docs PR.

## What is done (do not rebuild)

- `edhrec_seed.py` + `/api/build` pool injection + UI provenance
- Green-d launchd on `:8004`
- Thin-data honesty (no inclusion % when not `seeded`)
- Harness contestant D + feeder/keepable report framing
- CON-328 timeout ≥ 1800 + CON-330 per-contestant URL scoping + regression tests

## What is closed

- B/C as product candidates
- Overlap-with-E as a win metric
- Five-way sweeps by default
- Rewriting `scripts/v2_benchmark.py` from scratch (edit only; see
  `REGRESSION-322-harness-rewrite.md`)

## Demo host re-run checklist (before promotion gate 1)

1. Quit Chrome (or reboot) — 8GB demo host cannot sweep under ~4GB Chrome pressure
2. Confirm `:8001` has no `EDHREC_SEED`; `:8004` has `=1`
3. `python scripts/v2_benchmark.py --validate` prints `DEFAULT_BUILD_TIMEOUT=1800`
4. `python test_v2_benchmark.py` green (includes CON-328/330 pins)
5. Launch without `--green-url`:

```bash
tmux new-session -d -s seed-sweep \
  'python scripts/v2_benchmark.py --contestants E,BLUE,D 2>&1 | tee /tmp/seed-sweep.log'
```

Then read feeder + keepable. If D wins, open the blue promotion PR. If not, V2
stays on `:8004` and blue stays V1 — that is still an honest product.
