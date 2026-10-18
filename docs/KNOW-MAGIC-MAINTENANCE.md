# Card understanding ("Know Magic") — maintenance

"Know Magic" is the project's name for how well DeckWright can read card text: which oracle lines map to a known signal or keyword, and which are still unread. Coverage is **stated with a number**, unread lines are **named**, and playstyle precision is held above a floor. It is not 100%, and the docs say so.

## How this relates to boot

| Automatic on a stale launch or scheduled refresh | Still this doc (admit PR) |
|---|---|
| Grammar catalogs (new words, types, keywords, sets) | New `oracle_signals` for unread oracle lines |
| `synergy_objects` re-derive | New playstyle / strategy maps |
| Commanders KB `--mechanics-refresh` | New grammar expressions with verified counts |
| `print_wave_reeval.py` remeasure (coverage + precision) | Touching verifiers / INTERROGATOR / THESIS / ROLES / sim model |

Boot and the scheduled refresh **detect and refresh**; they do **not** invent reading rules
or ways-to-play. When the report’s `next_steps` or unread cluster moves after a
named set, run the admit loop below.

## When to re-run

After a major Magic print wave (set / commander precon dump), or when someone claims a new mechanic is "missing" from reading.

One command (or `./update.sh --print-wave NAME`):

```bash
source .venv/bin/activate
python scripts/print_wave_reeval.py                  # offline bulk + precision + spot-checks
python scripts/print_wave_reeval.py --refresh        # run the data refresh first
python scripts/print_wave_reeval.py --fetch --wave NAME   # new set dump: re-download bulk
python tests/test_print_wave_reeval.py               # offline harness for the path itself
```

Writes `data/print_wave_reeval.json` + `.md`. Still readable underneath:

- `data/oracle_coverage_report.md` — especially `unread_lines_ranked`
- `python scripts/check_playstyles.py` — precision floor 88% (also `--json`)

The report also reads the last recorded live UI retest (`testdata/review/2026-08-08-gate1-RETEST.md`) and will not report green that was never measured.

## What to do with a new unread cluster

The same loop used to build card understanding, at a smaller scale:

1. **Name the gap** — count + exemplar line from the report.
2. **Research** — what it does, who plays it (`method: both | community | derived`).
3. **Grammar** — Scryfall expression with a verified count, or an explicit known limit.
4. **Strategy** — only if it should become a way-to-play / plan commitment; do not invent playstyles to shrink empty-ways.
5. **Admit** — exemplar + near-miss test in `test_synergy.py`; remeasure; precision must not drop.

Refuse:

- Inventing `oracle_signals` for Scryfall **keyword catalog** lines (those are already readable via `signal ∨ keyword`).
- Treating an unreachable Scryfall request as "0 cards".
- Touching selection, the three verifiers, `INTERROGATOR` / `THESIS`, `ROLES`, or the sim model under the banner of "coverage".

## Current band

Last measured with `scripts/print_wave_reeval.py` (`wave=baseline-reeval-2026-08-08`, overall `ok`):

| metric | value |
|---|---:|
| at least one signal | 73.4% |
| signal ∨ keyword | 85.0% |
| any consumes | 28.8% |
| unread (no signal, no keyword) | 15.0% |
| playstyles precision | 89.9% |
| oracle_signals | 106 |

Top unread still thin (≤12 cards per line): islandwalk attack lock, declare-attackers timing, draft face-up, verse/charge oddities. Leave it until a named print wave moves the distribution. Report: `data/print_wave_reeval.md`.
