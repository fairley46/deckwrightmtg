# Unread gap grammar

Phase 3 of Know Magic.

**Artifact:** [`data/unread_gap_grammar.json`](../../data/unread_gap_grammar.json)  
**Research:** [`data/unread_gap_research.json`](../../data/unread_gap_research.json)

## What this is

Every Phase 2 grammar candidate verified against Scryfall (`game:commander`):

- 404 → real zero
- other HTTP failures → `searchable: false` + reason (unknown ≠ zero)
- All 38 patterns in this batch: **searchable**, counts recorded

Keywords stay on `keyword:` catalogs — no invented `oracle_signals`.

## Signals shipped (exemplar + near-miss)

| signal | exemplar | near-miss |
|---|---|---|
| `card_draw` | Mind Stone | Path of Ancestry |
| `tap_add_colored` | Boseiju, Who Endures | Hinterland Harbor / Command Tower |
| `dual_mana_ability` | Hinterland Harbor | Boseiju / Spire of Industry |
| `enchant_land` | Wild Growth | Pacifism |
| `cant_be_blocked` | Rogue's Passage | flying reminder |
| `uncounterable` | Cavern of Souls | Counterspell |
| `modal_choose_one` | Abrade | Austere Command |
| `firebreathing` | Steel Hellkite | Nirkana Revenant |
| `must_attack` | Toski, Bearer of Secrets | Propaganda |

## Check

```
python -c "import test_synergy; test_synergy.test_unread_gap_signals()"
python -m unittest test_unread_gap_research.py
python scripts/check_playstyles.py          # precision must hold ≥88%
python scripts/oracle_coverage.py --offline --write data/oracle_coverage_report.json
```
