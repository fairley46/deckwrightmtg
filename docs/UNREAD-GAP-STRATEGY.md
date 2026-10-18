# Unread gap strategy (CON-249)

Phase 4 of CON-246 — Know Magic.

**Artifact:** [`data/unread_gap_strategy.json`](../data/unread_gap_strategy.json)

## What this is

Every researched mechanic gets a strategy hook:

- existing playstyle in `data/playstyles.json`, or
- synergy object / plan commitment, or
- explicit `none_yet` with reason

No new playstyle names without community sources. Precision floor 88%.

## Playstyle edits this card

| playstyle | change | why |
|---|---|---|
| `voltron` | boost `cant_be_blocked`, `firebreathing` | ranks already-matched voltron; not a match gate |
| `landfall` | boost `enchant_land` | land auras feed land plans; not landfall alone |

`requires_any` was **not** widened for `enters` / `attacks` / bare evasion — that would manufacture ways-to-play and burn precision.

## Empty ways

| | before | after | Δ |
|---|---:|---:|---:|
| empty ways | 386 | 386 | 0 |
| no signals | 221 | 177 | −44 |
| signals, no playstyle | 165 | 209 | +44 |
| precision | 89.9% | 89.9% | 0 |

New signals made 44 more commanders **readable** without naming a way to play.
Total empty ways did not shrink on purpose: inventing a playstyle for
`enters`/`attacks`/`card_draw` alone would burn precision. Next shrink needs
sourced playstyle names or real composition gates — not a fallback.

## Check

```
python -m unittest test_unread_gap_strategy.py
python scripts/check_playstyles.py
python scripts/build_commanders_kb.py --mechanics-refresh
```
