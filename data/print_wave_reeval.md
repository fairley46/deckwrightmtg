# Print-wave re-eval — 2026-10-11

**Issue:** CON-260 · **wave:** `cadence-reeval-2026-10-11` · **overall:** `ok`

## Coverage

| metric | value |
|---|---:|
| at least one signal | 73.6% |
| signal ∨ keyword | 85.1% |
| any consumes | 28.9% |
| unread (no signal, no keyword) | 14.9% |
| oracle_signals | 106 |
| graded / universe | 32116 / 32116 |
| unknown unreadable | 0 |

### Top unread lines

```
     12×  this creature can't attack unless defending player controls an island
      9×  cast this spell only during the declare attackers step and only if you've been attacked this step
      8×  draft this card face up
      7×  {M}, {M}: put a charge counter on this artifact
      6×  at the beginning of your upkeep, you may put a verse counter on this enchantment
      5×  destroy target attacking creature
      5×  when this creature becomes the target of a spell or ability, sacrifice it
      5×  change the target of target spell with a single target
      5×  destroy target tapped creature
      5×  counter target creature spell
      5×  exile target artifact or enchantment
      5×  you may have this creature assign its combat damage as though it weren't blocked
```

## Playstyles

- precision: **89.9%** (floor 88%; ok=True)
- recall: 82.6%
- HIT/MISS/EXTRA: 71 / 15 / 8
- scored / excluded: 57 / 0 (excluded ≠ miss)

## Product spot-checks (offline)

- `test_instrument_chat.py`: ok
- `test_way_preference.py`: ok
- `test_thesis_glued.py`: ok
- `test_precon_upgrade.py`: ok
- `test_saved_ask_instrument.py`: ok

## Gate 1

- status: `green`
- unclassifiable: 0/0/0
- source: `backend-review/playtests/2026-08-08-gate1-RETEST.md`
- note: last recorded retest — live Playwright re-run is Claude-owned

## Next actions

- Name the gap: 12× unread line "this creature can't attack unless defending player controls an island" (see unread_lines_top).
- If a real unread cluster moved after a named set dump: one small admit PR (exemplar + near-miss in test_synergy.py). Refuse keyword-catalog oracle_signals.
- Update docs/KNOW-MAGIC-MAINTENANCE.md and docs/GA-FREEZE.md with these dated numbers — do not silently bump.

## Re-run

```bash
source .venv/bin/activate
python scripts/print_wave_reeval.py
python scripts/print_wave_reeval.py --refresh   # after a real set dump
```
