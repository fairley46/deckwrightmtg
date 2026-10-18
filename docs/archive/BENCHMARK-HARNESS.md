# V2 bake-off

The harness is frozen. Candidates A/B/C register; they do not edit this test.

## One command

```bash
source .venv/bin/activate
python scripts/v2_benchmark.py --validate          # freeze check, no builds
python scripts/v2_benchmark.py --contestants E     # EDHREC fixtures only
python scripts/v2_benchmark.py --contestants E,BLUE,D   # seed sweep
python test_v2_benchmark.py                        # offline logic
```

Default enabled registry (2026-09): **E, BLUE, D**. B and C are disabled — see [SEED-SWEEP.md](benchmarks-SEED-SWEEP.md).

Reports land in `docs/benchmarks/runs/<utc>/`: `report.md`, `report.json`, `blind_pairs.json`, `blind_key.json`.

BLUE → `:8001` (V1, no seed). D → `:8004` (`DECKWRIGHT_EDHREC_SEED=1`). Production `:8000` is never the experiment until the promotion PR in [SHIP-V2.md](product-SHIP-V2.md).

`--validate` must print `DEFAULT_BUILD_TIMEOUT=1800`. Do not pass `--green-url` for multi-contestant runs. Voided run: `runs/20260902T172439Z`.

## What "closest to intended state" means

**The EDHREC seed sweep (E / BLUE / D):** judge on **input feeder density** (toward E, vs BLUE) and **keepable hand %** (no regression). Overlap with E is a sanity check only — high on D is expected, not a product win. See [SEED-SWEEP.md](benchmarks-SEED-SWEEP.md).

**Earlier B/C sweeps:** a candidate is closer when it moves (1) commander-input feeder density, (2) dead outputs, (3) bound violations, without regressing (4) castability / keepable-hand %.

## Freeze rules

- The commander set in `data/benchmarks/commanders.json` does not grow without re-running every contestant.
- Bands use harvested **EDHREC commander rank**, not Scryfall card-level `edhrec_rank` (staples would invert the top/tail prediction in v2-design §B.9).
- No runtime EDHREC. E reads local fixtures from `data/benchmarks/edhrec/<slug>.json`; they are gitignored, not committed, and no script in this tree generates them. A missing fixture makes E `unknown` for that commander, never zero. Name-only fixtures still produce E lists; text metrics stay UNKNOWN until a local corpus is present.
- Adding A/B/C is flipping `enabled` and setting `target` in `data/benchmarks/contestants.json`. If a PR touches `scripts/v2_benchmark.py` to help a candidate, the test is being fitted to the contestant — reject it.
- Goldfish seed is 319. Solitaire. Not a win rate.
- Unknown ≠ zero. Unread specs, missing fixtures, failed HTTP, and unreadable cards are UNKNOWN.

## Blind comparison

Open `blind_pairs.json` first. Pick which list you would rather play. Then open `blind_key.json`.
