# Bound coverage (CON-331)

Sign-off before reading Gate 5 `E,BLUE,B` bound-violation columns.

```bash
source .venv/bin/activate
python scripts/bound_coverage.py
# or, if frozen set is only on the harness branch:
python scripts/bound_coverage.py --commanders /path/to/data/benchmarks/commanders.json
```

**When:** AFTER CON-331 bound extract lands. Floor commanders (Bello MV ≥ 4,
Tayam MV ≤ 3) and the remaining four (Shirei power ≤ 1, Maralen forbid-draw,
Sliver subtype, Meren experience capacity) should show `SEEN`.

**Vocabulary (extract, pattern-only — no commander-name branches):**

| Commander | Extracted bound |
|-----------|-----------------|
| Bello | `artifact_mv` / `enchantment_mv` `geq` static 4 |
| Tayam | `card_mv` `leq` static 3 |
| Shirei | `creature_power` `leq` static 1 |
| Maralen | `draw` `op: forbid` |
| Sliver Overlord | `creature_subtype` `in_set` sliver |
| Meren | `creature_mv` `leq` `event_capacity` resource=experience |

Meren's experience capacity is **unknown** at audit time (no static magnitude
in the 99) — that is honest unknown ≠ invent typical-4. Extract still `SEEN`
so Gate 5 can separate coverage from steer failure.

**Reading:**

| status | meaning |
|--------|---------|
| `SEEN` | `derive().bounds` nonempty — steer can engage (or capacity unknown) |
| `NONE` | oracle was read; extractor returned no bounds — coverage gap, not a steer miss |
| `UNKNOWN` | local corpus miss — could not look (unknown ≠ zero) |

This script only *reads* `commander_strategy.derive`. It does not edit the
extractor, steer, or audit.

**Supersedes** stacked drafts `#305` (floor bounds) / `#307` (coverage script)
once this extract lands on `main` — those PRs targeted pre-ship Gate 4 tips.
