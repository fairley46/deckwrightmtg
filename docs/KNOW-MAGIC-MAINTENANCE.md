# Know Magic — maintenance (after lap 4)

Comprehension laps are closed. Coverage is **stated with a number**, unread is **named**, precision held. Not 100% — and that is the exit condition CON-246 asked for.

Broader post-GA Auto lane (picker, ways maps, honesty joins): .

## How this relates to desktop boot (CON-285)

| Automatic on stale launch / Sunday cadence | Still this doc (admit PR) |
|---|---|
| Grammar catalogs (new words, types, keywords, sets) | New `oracle_signals` for unread oracle lines |
| `synergy_objects` re-derive | New playstyle / strategy maps |
| Commanders KB `--mechanics-refresh` | New grammar expressions with verified counts |
| `print_wave_reeval.py` remeasure (coverage + precision) | Touching verifiers / INTERROGATOR / THESIS / ROLES / sim model |

Boot and weekly cadence **detect and refresh**; they do **not** invent reading rules
or ways-to-play. When the report’s `next_steps` or unread cluster moves after a
named set, run the admit loop below.

## When to re-run

After a major Magic print wave (set / commander precon dump), or when someone claims a new mechanic is "missing" from reading.

Linear card: CON-260. One command
(also `./update.sh --print-wave NAME` on the demo host):

```bash
source .venv/bin/activate
python scripts/print_wave_reeval.py                  # offline bulk + precision + spot-checks
python scripts/print_wave_reeval.py --refresh        # also run weekly cadence first
python scripts/print_wave_reeval.py --fetch --wave NAME   # new set dump: re-download bulk
python test_print_wave_reeval.py                     # offline harness for the path itself
```

Writes `data/print_wave_reeval.json` + `.md`. Still readable underneath:

- `data/oracle_coverage_report.md` — especially `unread_lines_ranked`
- `python scripts/check_playstyles.py` — precision floor 88% (also `--json`)

Gate 1 live Playwright retest stays Claude-owned; the script records the last retest doc (`0/0/0` as of 2026-08-08) and will not invent green.

## What to do with a new unread cluster

Same loop as CON-246, smaller:

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

**Restated 2026-08-08** via `scripts/print_wave_reeval.py` (`wave=baseline-reeval-2026-08-08`, overall `ok`). Same band as end of CON-253 — no new admit this run.

| metric | value |
|---|---:|
| at least one signal | 73.4% |
| signal ∨ keyword | 85.0% |
| any consumes | 28.8% |
| unread (no signal, no keyword) | 15.0% |
| playstyles precision | 89.9% |
| oracle_signals | 106 |

Top unread still thin (≤12 cards per line): islandwalk attack lock, declare-attackers timing, draft face-up, verse/charge oddities. Leave it until a named print wave moves the distribution. Report: `data/print_wave_reeval.md`.
