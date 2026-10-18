# Unread gap research

Phase 2 of Know Magic.

**Artifact:** [`data/unread_gap_research.json`](../data/unread_gap_research.json)

Source instrument: [`data/oracle_coverage_report.md`](../data/oracle_coverage_report.md).

## What this is

For each high-frequency silent oracle line after the mana/land + commander-matters
batch, a research record with:

- mechanical what / what deck wants it / community name
- `method`: `both` | `community` | `derived`
- named exemplar + near-miss (Scryfall-resolved)
- grammar candidate (unverified count — Phase 3)
- strategy candidate (playstyle / synergy object / `none_yet`)

**No `oracle_signals` in this card.** Keywords admit via Scryfall catalogs.

## Priority into Phase 3

1. `draw_a_card` — highest-value non-catalog gap
2. `tap_add_colored` / `dual_mana_ability` — finish the mana reading
3. `enchant_land`, `cant_be_blocked`, `uncounterable`, `modal_choose_one`, `firebreathing`, `must_attack`
4. Verify `keyword:` counts for the silent keyword mountain (`flying`, …)

## Check

```
python -m unittest test_unread_gap_research.py
```
