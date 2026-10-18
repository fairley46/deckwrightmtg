# Know Magic → live app — how comprehension joins the funnel

This is the join document for CON-246's vocabulary work and the existing wish→99 path in [`DERIVATION.md`](DERIVATION.md).

Comprehension does **not** replace selection. It makes every later stage honest about what a card *is*.

## What we built (upstream)

| artifact | job |
|---|---|
| `data/mechanic_families.json` → `oracle_signals` | Regex vocabulary that reads oracle text into named signals + produce/consume objects |
| `synergy.profile(card)` | Card → `{signals, produces, consumes, keywords, status}` |
| `synergy.demand_profile(commander)` | What the commander asks the 99 to supply |
| `synergy.cohesion` / `prefer_glued` | Card–card glue from directed object edges |
| `scripts/oracle_coverage.py` | Full-corpus instrument: signal %, signal∨keyword %, consumes %, ranked unread |
| `data/unread_gap_research.json` | Researched mid-tail (CON-247) |
| `playstyles` + `scripts/check_playstyles.py` | Ways-to-play precision floor (88%) |

**Rules that stay:** model never invents card names; unknown ≠ zero; precision is not currency.

## Where it already feeds the app

```
Scryfall card
    │
    ▼
synergy.profile()          ← oracle_signals + keyword catalog
    │
    ├─► demand_profile(commander)     what the 99 must feed
    ├─► edges / cohesion              why two cards hold together
    └─► prefer_glued(...)             steers pool order in /api/build
              │
              ▼
         scaffold → 99 (verifiers untouched)
```

Concrete call sites today:

1. **`/api/build`** — `synergy.prefer_glued` ranks remaining candidates by directed edges to the commander/scaffold (`app.py`). Better reading → more real edges → fewer orphan fills. Selection queries and verifiers stay as they are.
2. **Commander ways-to-play** — `playstyles` detect from signals/keywords. Wider vocabulary shrinks "signals but no name" when the playstyle mapping exists; empty ways still need **sourced** playstyle rows, not bare ETB gates.
3. **Explain / cohesion surfaces** — edges are only as good as produce/consume. Lap work that adds honest `consumes` (bounce, shrink, discard cost, …) makes "why this card" cite real objects.
4. **Coverage honesty** — `oracle_coverage_report` is the QC for "do we know Magic?" before anyone claims a picker or plan improvement.
5. **Conversation layer (CON-255, shipped)** — `/api/chat` instrumented readback; brief `way_preference` → mapped `way_playstyle` soft-prefer; `/api/thesis` glued payoff allowlist; precon beef prefers readable+edged cards. Same rule: queries → Scryfall → verify. Spec: [SACRED-CORE-NEXT.md](SACRED-CORE-NEXT.md).

## The funnel with comprehension made explicit

```
her words
   │  INTERROGATOR                         (sacred — do not touch here)
   ▼
brief { theme, strategy, playstyle, … }
   │  grammar: theme_clauses / role_query  (Scryfall queries only)
   ▼
candidate pools  ── real cards, verified ──
   │
   │  ★ comprehension layer (Know Magic)
   │     profile each card; demand from commander;
   │     ways-to-play from detectable signals
   ▼
plan commitments + skeleton               (ROLES / budget — sacred)
   │  prefer_glued steers; EDHREC backfill
   ▼
99 → sim → thesis                         (verifiers / THESIS — sacred)
```

The star is the only new join. Everything sacred stays. Comprehension changes **what the math can see**, not who is allowed to name a card.

## How to integrate further — successful path (ordered)

Do these as small farmable cards. Each step is falsifiable. None rewrite verifiers.

### 1. Readback uses profile status (honesty) — CON-272

When a candidate is `status: unknown` (no oracle text) or unread (no signal ∧ no keyword), the UI/API must say **we could not read it**, not score it as zero synergy. Same house rule as coverage.

Surfaces: `synergy.card_read_status` / `synergy_read_note`, `why.explain_edges`,
`synergy.cohesion` (`unknown` + `unread`, never orphans), improve-candidate
ask copy, cohesion block on the deck page.

### 2. Ways-to-play only from mapped signals

For each researched gap in `unread_gap_research.json` that has a shipped signal:

- If a playstyle should name it, add a **sourced** detect rule + golden fixture row.
- Re-run `check_playstyles.py`. Precision must stay ≥ 88%.
- Empty-ways count drops only when a real name appears — never by inventing a style.

### 3. Plan commitments consume objects, not vibes

Where `PLAN-GRAPH-SCHEMA` conditions already key off objects (`creature`, `land`, `card`, …), new `consumes`/`produces` from lap signals start counting automatically once the plan keys exist. Add a `THESIS_KEYS` / plan key **only** with a matching `play_one()` computation — that is a separate card, not a silent widen.

### 4. Grammar queries stay Scryfall-shaped

Unread research that produced verified `o:` / `otag:` expressions belongs in `theme_clauses` / role ladders the same way CON-248 verified counts. The model still writes queries; the app still verifies names against the response.

### 5. Commander KB refresh stays weekly research

Know Magic reading and the commander knowledge base are parallel: KB says how a legend is played; signals say what lines of text we can detect. Refresh KB with community method; refresh signals with the maintenance loop. Do not merge them into one blob.

### 6. Print-wave cadence

On each major set: `python scripts/print_wave_reeval.py --refresh --fetch --wave <name>` ([`KNOW-MAGIC-MAINTENANCE.md`](KNOW-MAGIC-MAINTENANCE.md)). If unread spikes on a new pattern, one small signal PR — not a new epic. Card: CON-260.

## What "successful integration" looks like in product terms

| surface | before thin reading | after Know Magic |
|---|---|---|
| Why is this card here? | EDHREC / theme string | directed edge: produces X that commander consumes |
| Ways to play | 13 styles, many empty | same floor, fewer false empties where signals map |
| Build steer | orphans ≈ glued | `prefer_glued` sees more real glue |
| Coverage claim | anecdote | 73.4% signal / 85.0% signal∨keyword / 15% named unread |
| Failures | silent zero | unknown vs none distinguished |

## Out of scope for integration PRs

- Letting the model name cards from memory
- Lowering the playstyle precision floor
- Rewriting `INTERROGATOR` / `THESIS` / `ROLES` / sim model to "use the new signals"
- Claiming 100% reading

## Check before calling a join "done"

```
python scripts/oracle_coverage.py --write data/oracle_coverage_report.json
python scripts/check_playstyles.py
python test_synergy.py          # or the focused CON-* tests
python test_deckwright.py       # when touching query / build paths
```

If coverage did not move and precision did not hold, the join did not land — revert.
