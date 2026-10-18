# SYSTEM-MAP — what exists, what it consumes, what is harness

> **Secondary map.** For the live connection spine (chat → build → thesis → ask,
> through-line, verifiers, frozen vs recomputed) read
> **[`HOW-IT-CONNECTS.md`](HOW-IT-CONNECTS.md)** first. This file is a deep
> inventory / harness catalogue. Where it disagrees with HOW-IT-CONNECTS or the
> code, **the code wins**, then HOW-IT-CONNECTS.

Read from the code on 2026-08-07; **re-check code before trusting a row**.

> **2026-08-09 note.** Backend GA is **wrapped**. Wiring gaps that still say
> “NOT wired / KB unused / `SKELETON_LIVE=False` / explain_edges unwired” are
> **stale**. Corrections table and the CLOSED list later in this file are right:
> KB on-path, cohesion / `explain_edges` wired, `SKELETON_LIVE=True`,
> and the glue steer is on the build path.
> Prefer [`HOW-IT-CONNECTS.md`](HOW-IT-CONNECTS.md) for the live spine.

Related docs (do not duplicate them): [`ARCHITECTURE.md`](ARCHITECTURE.md) shape,
[`DERIVATION.md`](DERIVATION.md) stage joins, [`SCORING-CONTRACT.md`](SCORING-CONTRACT.md)
scoring math, [`PLAN-GRAPH-SCHEMA.md`](PLAN-GRAPH-SCHEMA.md) plan shape.

---

## Actual main path (frontend → API)

```
/api/chat  →  /api/readback  →  /api/commanders  →  (/api/partners)
         →  /api/build  →  /api/thesis  →  /api/simulate  →  /api/christen
```

Corrections to the original phase list:

| Proposed | Code says |
|---|---|
| 2. resolution as a standalone step | Embedded in readiness, commander ladder, role queries; UI surface is `/api/readback`. `/api/resolve` is **decklist import**, not theme resolution. |
| 4. commander knowledge | **On the main path since #173.** `app.py` imports both; the picker scores theme/strategy and offers `ways_to_play`. (This row read "off the main path" correct when written, stale after #173.) |
| 9→10 curve after simulation | Curve report is computed **inside `/api/build`**, after mana + why, before persist. Thesis sim is post-build. |
| (missing) mana base | In the build path, between scoring and overview. |
| (missing) thesis | Separate post-build endpoint; feeds `/api/simulate`. |

---

# Part A — product pipeline, phase by phase

## 1. Interrogation (+ readiness gate)

**What it is for.** Turns her wish into a structured brief, and refuses to
proceed while the brief is unusable.

**Input.** `Turn { messages: list[dict], readiness_asked: str | None }` from
the chat UI → `POST /api/chat`.

**Output.**
```
{
  text, brief | null, still_asking,
  brief_error: "truncated"|"unparseable"|"missing_brief"|null,
  readiness: {
    ready, blockers[{signal, detail}], warnings[...],
    question, asked_about, stuck, pool_count, pool_unknown, pool_floor
  } | null
}
```
Brief keys (schema): `theme`, `angle`, `colors`, `mechanics`, `budget_total`,
`bracket`, `commander_query`, `flavor_terms`, `fantasy`, `playstyle`,
`strategy_shape`, `commander_name`, `table`.

**Where it lives.** `app.py`: `chat()`, `INTERROGATOR`, `extract_brief_payload()`,
`check_readiness()`, `CHAT_OUTPUT_SCHEMA`. Model via `llm.py`.

**Consumed by.** Frontend holds `brief` for readback / commanders / build.
Readiness `question` stays in the chat when `ready` is false.

**Governing rule.** Model never names Magic cards (except `commander_name` when
she named one). `commander_query` is Scryfall syntax. No confidence percentages —
blockers are countable facts. Unknown pool depth warns; it does not become "thin."

**How it fails.** Truncation / bad JSON → `brief_error` set, brief null (not a
silent null). Unready brief → `still_asking=true` + named `question`. Colors
never block. Missing API/CLI client → HTTP 500 on model endpoints.

---

## 2. Resolution (embedded; also readback)

**What it is for.** Maps her words onto Scryfall operators so queries hit real
catalogs instead of invented `o:"heists"`.

**Input.** Theme / content words (string). From brief fields, or
`POST /api/readback` `{theme, colors?}`.

**Output of `resolve_theme`.**
```
{
  parts: [{operator, value, catalog, confidence}, ...],
  expression: "t:pirate otag:theft" | null,
  unresolved: [str, ...]
}
```
Unresolved term: `{operator: null, value: null, catalog: null, confidence: "unresolved"}`.

Readback adds live counts:
```
{ theme, colors, terms[{said, resolved, operator, query, count, note}],
  composed{said, query, count}|null, thin, thin_threshold: 10 }
```
Failed Scryfall count → `count=null`, never `0`.

**Where it lives.** `resolve.py`: `resolve_term()`, `resolve_theme()`,
`expression()`, `compose_expressions()`. Callers in `app.py`:
`theme_clauses()`, `commander_ladder()`, `check_readiness()`, `build_readback()`.
Grammar cache: `data/scryfall_grammar.json`.

**Consumed by.** Readiness pool gate; commander ladder rungs; role queries;
UI confirmation before commanders. **Not a separate gate before build** — build
re-resolves via `theme_clauses` / `resolve_theme`.

**Governing rule.** Unresolved ≠ invent an oracle phrase. Unknown ≠ zero.
Dead behaviours stop before the word-bank.

**How it fails.** Unresolved parts listed honestly. Network failure on counts →
`null` / readiness `pool_unknown`.

**Finding.** `POST /api/resolve` is `import_deck.resolve_decklist` (paste a list).
Name collision with theme resolution.

---

## 3. Commander search

**What it is for.** Finds legal commanders that match the brief and returns five
writable options she can pick from.

**Input.** Brief dict → `POST /api/commanders`. Must pass `brief_is_usable`
(else HTTP 422).

**Output.**
```
{
  picks: [{ name, does, strategy, fits, mechanics, pros, cons, catch,
            reason, partner, color_identity, cost, type, text, usd,
            image, art, keywords, power, toughness }, ...]  # ≤5
  note, query   # rung that filled the pool
}
```

**Where it lives.** `app.py`: `commander_ladder()`, `search_ladder()`,
`label_commander_picks()`, `commanders()`. Ladder order: named `!"…"` → composed
theme expression → behaviour-bearing model query + relaxations → bare type →
`theme_clauses` → generic `is:commander`. Colors use `id<=` only if brief has them.

**Consumed by.** Frontend pick object → `/api/build` as `commander` + `pick`
(strategy becomes the build spec). Optional `/api/partners` between.

**Governing rule.** Verifier: only enrich names present in the Scryfall
candidate set. Write-up failure keeps the card with `plain_commander_reason`.

**How it fails.** Empty ladder → empty `picks` + note. Unusable brief → 422.
Write-up miss → plain reason, card kept.

---

## 4. Commander knowledge — ON THE MAIN PATH

**Status changed.** This section said "OFF THE MAIN PATH" and that `app.py`
imported neither module. That was true when the map was read on 2026-08-07
morning and false since **#173**, which wired the KB into the picker. The method
was right — read the code, not the card — the fact simply moved. Re-verified at
`main` 966f91b.

**What it is for.** Per-commander `ways_to_play` — how a commander is actually
played — from community research and the mechanics detector, kept as separate
methods so disagreement stays visible.

**Input / output.** `commanders.lookup(name)` → KB entry;
`commanders.theme_fit(card, brief)` / `strategy_fit(card, brief)` →
`{status, score, matched, ways}`; `playstyles.detect(commander)` →
`{status, matched, scores, signals}`. Data: `data/commanders.json` (3,321
entries), `testdata/review/commander-playstyles.json`. Built by
`scripts/build_commanders_kb.py`.

**Where it lives.** `commanders.py`, `playstyles.py`. Tests:
`test_commanders_kb.py`, `test_playstyles.py`, `test_commander_fit.py`,
`test_way_to_play.py`, `scripts/check_playstyles.py`.

**Consumed by — product path.** `app.py` imports both modules.
`/api/commanders` scores every pick on theme and strategy, attaches
`ways_to_play`, and renders them as a radiogroup on the commander card. Her
choice becomes `brief.way_to_play`, which `plan_graph.derive` turns into plan
commitments — so switching the way measurably changes the 99. `label_commander_picks`
earns a "best fit for your strategy" seat from `strategy_fit`.

**Coverage, two numbers, never one.** Mechanics is free and must be 100%
(offline, no model, one bulk Scryfall fetch — a missing mechanics profile is a
bug). Research is expensive and legitimately partial. At `966f91b`: mechanics
3321/3321, research 3321/3321, **ways-to-play 2933/3321 (88%)**. The gap is
commanders whose text carries no signal our vocabulary can name, and it is
reported as absent, never filled with an invented way.

**Governing rule.** Absences recorded honestly; detector/community disagreement
is data, not something to resolve silently into a fake match. Playstyles come
from `data/playstyles.json` and are never invented.

**How it fails.** Missing KB entry → the picker falls back to `playstyles.detect`
on the card text, and says the way was derived rather than researched. Detector
miss → `status` reports it; a commander whose ways we cannot detect shows fewer,
or none, and says so. `check_playstyles.py` always exits 0 (research instrument).

---

## 5. Plan

**What it is for.** States what has to be true for this commander + brief to be
"working" — conditions, commitments, archetype, curve preference — with no model.

**Input.** `plan_graph.derive(brief, commander_card, partner_card=None)` from
`/api/build` (and again inside `card_score.rank_for_role` for scoring conditions).

**Output (core).**
```
{
  version: 1,
  archetype: { id: "incremental"|"burst"|"aggro"|null, label, candidates,
               decided, source: "strategy_shape"|"phrases"|"none" },
  curve: "low"|"mid"|"high"|"unknown",
  derived_from: { commander, partner, brief_fields, sim_version },
  conditions: [{ id, label, why, check, by_turn, weight, ... }],
  commitments: [{ id, label, role, counts: {min}, family, why, ... }],
  load_bearing: [str, ...],
  theme: { label }
}
```

**Where it lives.** `plan_graph.py`: `derive()`, `derive_provisional()`,
`payoff_searches()`, `conditions_for_scoring()`, `plan_is_success()`,
`archetypes_for()`.

**Consumed by.**
| Output piece | Consumer | Live? |
|---|---|---|
| `payoff_searches` / archetype extras | theme pool + payoff floors in `build()` | yes |
| `conditions_for_scoring` | `card_score` sim targets | yes |
| `thesis_from_plan` | `/api/thesis` → goldfish ("Does the plan happen?") | yes |
| `curve` / `curve.preference` | CMC **tiebreak** in scoring | yes |
| archetype `id` as numeric weight | — | **no** |
| full `plan` object | build response → UI display + thesis input | yes |
| `commitments` floors | suggest / floors enforcement | partial |

**Governing rule.** No model. Conditions only use `sim.THESIS_KEYS`.
`decided:true` without a usable label/commitments is refused
(`plan_is_success` / HTTP 422).

**How it fails.** Exception in build’s try → payoff pool skipped, roles still
fill (`plan` key omitted — unknown, not invented). Decided-but-empty → 422.

**Finding (historical, fixed in code).** `archetypes_for` used to
return `[]` unless `life_math` was in families — archetype axis dead for twelve
of thirteen playstyles. Current code opens on any non-empty `families` list.

---

## 6. Candidate recall

**What it is for.** Builds the real card pools the model is allowed to choose from.

**Input.** Brief + commander color identity + plan payoff searches + role
skeleton (`ROLES` shifted by brief `playstyle` via `PLAYSTYLE_SHIFT`).

**Output.** Per-role Scryfall card lists in `role_pools[role]`;
`role_query_used[role] = query string`. Model returns a JSON array of names →
intersected with the pool.

**Where it lives.** `app.py`: `role_query()`, `_role_query_base()`,
`search_ladder()`, `theme_clauses()`, `skeleton_for()` / `ROLES`;
`plan_graph.payoff_searches()`; `synergy.leave_enabler_queries()`. Loop inside
`build()`.

Skeleton (defaults): lands→sized later, ramp 10, draw 9, spot 7, wipe 3,
plan 10, theme 15, deepcut 5.

**Consumed by.** Scoring / placement; later swap · suggest · tweak via persisted
`role_pools`.

**Governing rule.** Model never invents a name that sticks — verifier intersects
with pool; miss → EDHREC-rank backfill. Empty theme ladder does not silently pad
with fake matches.

**How it fails.** Empty role → generic on-color search, then flex shortfall
(never basics). Model JSON fail → `names=[]` → pool-order backfill. Notes list
shortfalls.

---

## 7. Synergy (gate + graph)

**What it is for.** Asks whether theme/plan candidates connect to the commander
(produce/consume objects), and recalls leave-enablers the theme search misses.

**Input.** Card oracle (`text` or `oracle_text`) + commander card. Objects from
`data/synergy_objects.json`; signals from `data/mechanic_families.json`.

**Output.**
- `profile(card)` → produces/consumes or `UNKNOWN`
- `edges(a, b)` → set of object strings
- `eligible(card, commander)` → bool (UNKNOWN stays eligible)
- `cohesion(deck, commander)` → `{edges, orphans, unknown, hub_dependence}`
- `leave_enabler_queries(commander)` → Scryfall query strings
- `timing_multiplier(card)` → bool (extra combat / extra turn / untap-all)
- `edge_steer_vote` → `int | None` (None = no STEER edge vote)

**Where it lives.** `synergy.py`. Wired from `card_score.rank_for_role`
(`GATED_ROLES = {"theme", "plan"}`) and build’s leave-enabler merge.

**Consumed by.**
| Output | Consumer | Live? |
|---|---|---|
| `eligible` gate | scoring shortlist | yes (fail-open) |
| `edge_count` / `edge_steer_vote` | scoring **tiebreak** + `prefer_glued` | yes |
| `leave_enabler_queries` | theme-facing payoff pool | yes |
| `cohesion` summary | computed into `score_info` | **NOT consumed by `build()`** — build only reads `skipped` / `reason`. Not on build response. Differential harness prints it. |
| `why.explain_edges` | — | **NOT wired** into `annotate()` |

**Governing rule.** No weighted sum into sim score. Unknown ≠ orphan. Gate
emptying the pool → fail-open (`synergy_gate_failed_open=True` on score_info).

**Timing blindness.** The graph is object-typed: an edge exists when
`A.produces ∩ B.consumes`. Extra combat phases, extra turns, and untap-all
multiply *how often* abilities fire without producing a material object — so
they correctly score 0 edges. That zero means “cannot see,” not “no synergy.”
`combat` / `turn` are not synergy objects and must not be invented. Since
made `edge_count` a live STEER key, a carve-out applies: timing
multipliers with 0 material edges cast **no vote** on the edge sort key
(`edge_steer_vote` → `None`) rather than sorting last as orphans. Activation
multipliers that matter to a combat-triggered commander live in `plan_graph`
(`wants_activation_multiplier` / `serves_activation_multiplier`), not as
graph edges.

**How it fails.** Unreadable oracle → UNKNOWN (passes gate). Fail-open keeps
all candidates.

---

## 8. Scoring

**What it is for.** Picks which pool cards enter the 99 by marginal goldfish lift
on a plan condition that still has headroom.

**Input.** `card_score.rank_for_role(deck_so_far, pool, role, target, model_picks,
cmdr_cmc, cmdr_card, ci, role_pools, spell_roles, seen, brief=, land_pool=)`.

**Output.** `(chosen: list[card], sim_meta: {name → {delta_pct, condition, turn, …}},
latency_ms, score_info)`.

Sort keys (no weighted sum):
`(round(delta/0.5), edge_count, cmc_sort_key(pref), -model_rank)`.

**Where it lives.** `card_score.py`: `rank_for_role()`, `build_scaffold()`,
`pick_scoring_target()`, `provisional_conditions()`. Floors in `app.py`:
`enforce_type_targets`, `enforce_payoff_floors`, `enforce_tribe_floor`.

Constants: `SCORE_RUNS=400`, `SAMPLE_SIZE=20`, `TARGET_TURN=10`.

**Consumed by.** `place_role_cards` / `stamp_sim_pick` → deck cards may get
`sim_pick`. Build aggregates `sim_scoring` latency / skips onto the response.

**Governing rule.** Zero delta keeps model order. No inventing weights.
Saturated baselines skip scoring rather than emit +0.0 as signal.

**How it fails.** Saturated → skip + `sim_skips` note; order by curve CMC +
model rank. Plan-derive exception → `provisional_conditions(brief)`.

**History.** the scoring-math QC documented engine signal lost at T5; the later-turn scoring change raised
`TARGET_TURN` to 10 so ranking sees engines that come online later.

---

## 8b. Mana base (in build path)

**What it is for.** Sizes the land count and fills colored sources so the spells
can be cast.

**Input.** Spell deck so far + commander(s) + color identity → `mana.plan()`,
then `finish_mana_base()` / `on_color_staples()`.

**Output.** `{total_lands, nonbasic_lands, avg_mv, basis}` plus build `lands`
block (basics / flex / surplus / shortfall / supported / shortages / sources_*).

**Where it lives.** `mana.py`: `plan()`, `analyze_sources()`,
`total_lands_target()`. `app.py`: `finish_mana_base()`, land role fill inside
`build()`.

**Consumed by.** Land slot size; land scoring pass; `deck_signals`; notes;
legality.

**Governing rule.** Never pad basics past the land target.

**How it fails.** Shortfall reported in notes; possible legality warning.
Does not invent off-identity lands.

---

## 9. Explanation (build-time)

**What it is for.** Stamps each non-structural card with why it was offered —
English from the Scryfall query that filled the role.

**Input.** Finished spell+land deck + `role_query_used` dict.

**Output.** Mutates `card["why"]` for theme / plan / deepcut. Build also returns
`role_queries` / `role_queries_raw`.

**Where it lives.** `why.py`: `explain()`, `explain_group()`, `annotate()`.
Called from `build()` after deck assembly.

**Consumed by.** UI card rows; `/api/ask` via `deck_context`.

**Governing rule.** No model. Structural roles (`lands/ramp/draw/spot/wipe`)
stay silent — the role heading is the answer.

**How it fails.** Empty query → no `why`. **`explain_edges` exists and is never
called by `annotate`** — synergy reasons do not appear on cards.

---

## 10. Build packaging (overview + build-time sim + curve report)

These finish `/api/build` in one request; they are not separate API phases.

### 10a. Build-time simulation

**What.** `overview.run_build_sim(deck, cmdr_cmc)` → `sim.run` with generic
`BUILD_CONDITIONS` (Commander out / Five mana / Three payoffs / Seven mana),
~1500 runs. Feeds overview facts only.

**Consumed by.** Overview prose. **Not** the UI thesis curves (those need
`/api/thesis` + `/api/simulate`).

### 10b. Overview

**What.** Model prose over FACTS + build sim; combo names verified against the
deck. `overview.generate_overview`. Soft-fail → stub empty fields + note.

### 10c. Curve report

**What it is for.** Measures castability / missed lands / ramp split and derives
a skeleton — **reporting only**.

**Input.** Deck + brief / plan curve preference.

**Output.**
```
{
  preference, castable_curve: {status, by_turn, runs} | {status:"unknown", reason},
  missed_land_drops, ramp_split,
  skeleton: { roles, reporting_only, preference, commander_cmc, live }
}
```

**Where it lives.** `curve.py`: `preference()`, `castable_curve()`,
`missed_land_drops()`, `ramp_split()`, `skeleton_for()`. `SKELETON_LIVE = True`.

**Consumed by.** Build response / UI. Selection impact: `preference` as CMC
**tiebreak** in scoring only. **`skeleton_for` does not replace `ROLES`.**

**Governing rule.** Never silently reshape the 99 from a tutorial’s authority
until the derived skeleton beats fixed ROLES on measured castability.

**How it fails.** Unmeasurable → `status: "unknown"`. Exception → note only.

---

## 11. Thesis (post-build)

**What it is for.** Model states what "working" means for *this* deck —
payoff names + measurable conditions.

**Input.** `POST /api/thesis` with deck / brief / pick.

**Output.** Thesis object: payoffs (names) + conditions (`check` dicts using
`THESIS_KEYS`).

**Where it lives.** `app.py` `thesis()` + `THESIS` prompt. Verifier on payoff
names against the deck and on condition keys against `sim.THESIS_KEYS`.

**Consumed by.** `/api/simulate`. Frontend re-runs thesis if simulate lacks
conditions.

**Governing rule.** Verifier drops invented payoff names and illegal keys.
Model does not emit executable code.

**How it fails.** Parse failure → `{"error": "Could not parse thesis."}`.

**Finding.** `THESIS` prompt text lists a **subset** of `sim.THESIS_KEYS`; the
verifier allows the full allowlist. Prompt drift is real.

---

## 12. Simulation (thesis-conditioned, post-build)

**What it is for.** Plays thousands of goldfish games and reports per-turn odds
that the thesis conditions happen.

**Input.** `POST /api/simulate`: deck, commander, thesis conditions / payoffs;
optional composition tweak via `role_pools`.

**Output.**
```
{
  runs,
  curves: [{ label, note, points[10], median_turn }, ...],
  diagnostics: { lands, mana_rocks, land_ramp, extra_drops, payoffs, avg_cmc },
  assumptions,
  good_hand?, bad_hand?,
  review?,   # thesis_check.review
  tweak? / composition? / deck?
}
```

**Where it lives.** `sim.py`: `run()`, `mechanics()`, `play_one()`, `diagnose()`,
`THESIS_KEYS`, `SIM_VERSION`. `app.py` `simulate()`. Quality caveats:
`thesis_check.review` (never fatal). Optional composition: `tweak.py`.

**Consumed by.** UI curves; christen/ask context; saved `simulation` baseline
(stamped with `SIM_VERSION`).

**Governing rule.** Goldfish only — no opponent. Unknown extraction ≠ invent
mechanics. An unknown is not a zero.

**How it fails.** Missing thesis conditions → frontend re-fetches thesis.
Missing `commander` → 500 today (backend-review finding: should be 422). Soft
extraction misses shift probabilities silently — check diagnostics first.

---

## Post-build side paths (not phases of the 99)

| Piece | Endpoint / module | Notes |
|---|---|---|
| Christen | `/api/christen` | Names / theme / primer after sim; soft-fail → empty fields |
| Ask | `/api/ask` | Post-deck Q&A; may emit a new brief fence |
| Swap | `/api/swap` + `swap.py` | Pool-verified; re-sims; clears overview |
| Suggest | `/api/suggest` + `suggest.py` | Goal swaps → `deck_state` pending; does not apply |
| Pressure | `/api/pressure*` + `pressure.py` | Opt-in Forge AI; never on build |
| Coverage | `/api/coverage` + `coverage.py` | Counted pool facts before rebuild (**product**, name collision with code coverage) |
| Import | `/api/import` (canonical) · `/api/resolve` (kept) | Paste list → shared analyze tail. One handler, two routes. |
| Precons | `/api/precons*` | Baked overview path |
| Sheet | `sheet.py` | Offline HTML sheet; no HTTP API |
| Partners | `/api/partners` | Between commanders and build |

---

## Wiring gaps (the valuable surface)

1. ~~**Commander KB unused on product path**~~ — **CLOSED by #173.** `app.py`
   imports `commanders` and `playstyles`; phase 4 feeds phase 5 through
   `theme_fit` / `strategy_fit` / `ways_to_play` → `brief.way_to_play` → plan.
2. ~~**`cohesion` computed, dropped**~~ — **CLOSED by #182.** Cohesion
   is on the build response. Note it is *visible*, not yet *steering*: orphans
   still do not block selection. That is the cohesion steer, and it is open.
3. ~~**`why.explain_edges` unwired**~~ — **CLOSED by #182.** `annotate` calls it,
   so card `why` carries synergy reasons and not only query provenance.
4. ~~**Curve skeleton reporting-only**~~ — **CLOSED by #186.**
   `SKELETON_LIVE=True`; the derived skeleton shapes role counts.
5. **Two sims, now named apart.** `overview.run_overview_sim` uses
   `OVERVIEW_CONDITIONS` (generic, ~1500 runs) and feeds the overview prose
   only. The curves she sees come from `/api/simulate` against the thesis
   conditions for that deck. Same engine, different question — an overview
   percentage is not a thesis percentage and must never be quoted as one.
   Old names `BUILD_CONDITIONS` / `run_build_sim` are aliases for one release.
6. **Two playstyle vocabularies — both live, deliberately not merged.**
   See `docs/VOCABULARIES.md`. `brief.playstyle` (proactive/reactive/grindy)
   shifts role counts; `brief.way_to_play` (aristocrats/blink/…) binds plan
   commitments. Different questions, different sources, different consumers.
   This is a documented split, not a gap to close.
7. ~~**`/api/resolve` name collision**~~ — **CLOSED.** `/api/import`
   is the canonical route for decklist import; `/api/resolve` stays registered
   on the same handler so nothing breaks. Theme resolution is `resolve.py` via
   `/api/readback`.
8. **THESIS prompt ⊂ `THESIS_KEYS`** — verifier is wider than the prompt.
9. ~~**`search()` non-Scryfall exceptions → `[]`**~~ — **CLOSED.** The system audit found that
   transport failures raise `ScryfallError` (unknown), not `[]`. Ladder and
   count paths treat that as unknown, never a real zero.

```
wish → brief + readiness(+resolve)
     → readback(counts)
     → commander_ladder(+resolve) → write-ups[verifier]
     → build: plan.derive → payoff/leave recall
            → role pools → model names[verifier]
            → synergy.eligible → card_score(Δ + edges + CMC)
            → floors → mana → why.annotate
            → overview(+build sim) → curve_report → persist
     → thesis[verifier] → simulate(+thesis_check)
     → christen / ask / swap / suggest
```

---

# Part B — harness inventory

Code that exists to **measure**, not to hand her a shopping list. Keep/drop is
an opinion for freeze decisions, not a delete order.

## Orchestrators

### `scripts/check_all.py` — **KEEP**
- **Measures.** Three tiers: T1 offline units, T2 network (payoffs + Playwright
  + Schemathesis), T3 ship (e2e + differential + Forge calibration). SKIP fails
  the tier.
- **Found.** Recursion trap if `test_check_all.py` is inside `_UNIT_COMMANDS`
  (~10 min hang). Exit-2 for incomplete.
- **Cost.** T1 seconds; T2 minutes + network; T3 model + Forge, tens of minutes.
- **Finding vs the system audit D2.** Audit claimed the duplicate
  `test_expert_hei_bai_split.py` was dropped; **code still lists it twice**
  (lines 121 and 125). `test_differential_harness.py` *was* added.
- **Redundancy.** Wraps most tests; does **not** run `check.py`, Hei Bai live
  scripts, `function_coverage`, `check_playstyles`, or `audit_scoring_math`.

### `check.py` — **KEEP**
- **Measures.** Preflight for one theme/CI: live `role_query` / commander ladder
  depth, API-key ping, mechanics catalog, toy `sim.run`, optional Forge presence.
- **Found.** Thin/empty role pools for abstract themes (`heists`/`ships` vs
  `pirate`) — the CONTRIBUTING.md first-task loop. Nothing else probes live role
  ladders this way.
- **Cost.** Network ~30–60s; optional key ping.
- **Redundancy.** Overlaps `test_deckwright --live` and `check_payoff_queries`
  on Scryfall, but not on `role_query` ladders. Not a CI substitute.

---

## Ship / behaviour scripts

### `scripts/differential_harness.py` — **KEEP**
- **Measures.** Adjacent wishes → different decks *for the plan’s claimed
  reason* (Jaccard, archetype, commitments, membership, budget). Unknown if
  brief/build refuses.
- **Found.** Fixture `differential-run-2026-08-06.json`: angels/steal/budget
  pairs UNKNOWN (`brief not ready: blockers=['terms']`); incremental-vs-burst
  both chose `incremental` (FAIL) at Jaccard 41.7%. Cited as why name-overlap
  alone is the wrong metric.
- **Cost.** Model + Scryfall; several minutes (check_all timeout 1800s).
- **Redundancy.** Superset of `e2e_flow`. Offline twin: `test_differential_harness.py`.

### `scripts/e2e_flow.py` — **KEEP as cheap Tier-3 smoke; merge candidate**
- **Measures.** One wish → chat → commanders → build against DERIVATION checks.
  UNKNOWN ≠ vacuous PASS.
- **Found.** Vacuous-`all([])` class (house rule). No unique product bug beyond
  "chain works" documented in-repo.
- **Cost.** Model + Scryfall; minutes.
- **Redundancy.** High with differential. Prefer differential for ship signal;
  keep e2e only if Tier 3 needs a cheaper single-wish smoke.

### `scripts/function_coverage.py` — **KEEP**
- **Measures.** Synergy *function* coverage vs expert Hei Bai `chosen`
  (produces/consumes), not name overlap. `FRAGILE_AT=2`.
- **Found.** Cache bug that scored 100% by including his cards in "ours."
  Fragile `lifegain_trigger` flipped the headline ~6 points across identical
  runs. Nothing else measures function gap vs name-recall.
- **Cost.** Offline if oracle cache warm; else Scryfall. Seconds–1 min. No model.
- **Redundancy.** Complements `hei_bai_agreement` — different question; do not merge.

### `scripts/hei_bai_agreement.py` — **KEEP**
- **Measures.** Close (name overlap + connected-not-picked) and Better (extras
  ≥ his median edges, on-plan). Three-run stability band.
- **Found.** Contaminated-50 (named-in-video ≠ in-deck) understated us — score
  only `chosen`. Scoring contract documented; published %s are run-dependent.
- **Cost.** Offline with cache; Scryfall for missing oracle. Needs prior baseline decks.
- **Redundancy.** Shares fixtures with function_coverage; keep both.

### `scripts/hei_bai_baseline.py` — **KEEP (capture step)**
- **Measures.** Full chat+build for Hei Bai; oracle-text probe counts.
- **Found.** Fixture `hei-bai-baseline-2026-08-06.json`: attacks trigger 0,
  leave phrase 0, plan skewed to lifegain/token while commander is sac/counters.
  leave-*phrase* ≠ leave-*cause*. Snapshot probe keys lag current PROBES.
- **Cost.** Running server + model + Scryfall; several minutes.
- **Redundancy.** Overlaps e2e on the build path; unique as fixed-commander
  ground truth + probes. Offline probe contracts live in
  `test_hei_bai_baseline_probes.py`.

### `scripts/extract_expert_deck.py` — **KEEP (fixture factory)**
- **Measures / builds.** Transcript n-grams ∩ Scryfall names →
  `chosen`/`mentioned`. Writes `expert-hei-bai-cards.json`.
- **Found.** Subtitle mangling; Faithless Looting counter-example; **45 chosen /
  11 mentioned** — without the split, agreement was contaminated.
- **Cost.** Offline seconds.
- **Redundancy.** Feeds agreement / function_coverage; not a gate.

### `scripts/check_playstyles.py` — **KEEP as research; do not gate**
- **Measures.** Community fixture (57) vs `playstyles.detect()` — hit/miss/extra.
  Exit 0 always; no pass bar.
- **Found.** Gap to the "95%" named in CURSOR-BRIEF (Wyleth, Winota, Purphoros,
  …). The playstyle signal work false "NOT DETECTABLE." exemplars ≠ acceptance tests.
- **Cost.** Offline seconds.
- **Redundancy.** `test_playstyles.py` = vocabulary contracts; this = community
  falsification; `build_commanders_kb.py` records the same disagreement into data.

### `scripts/check_payoff_queries.py` — **KEEP (Tier-2 gate)**
- **Measures.** Every `_PAYOFF_QUERIES` + archetype `extra_payoffs` against live
  Scryfall (floors, top hits, `id<=` precedence).
- **Found.** Four of nine original patterns returned lands/cantrips; burst
  included alt-wincons; `q OR r id<=WB` precedence bug. count gate can
  pass on wrong *kind*. non-429 HTTP still scored as dead.
- **Cost.** Network ~1–3 min.
- **Redundancy.** Pair with `audit_scoring_math --payoffs-only` for kind audit; this is the build gate.

### `scripts/audit_scoring_math.py` — **KEEP as research; not a gate**
- **Measures.** SCORING-CONTRACT remeasure, adversarial extracts, THESIS_KEYS
  reachability, live payoff kind audit.
- **Found.** Documented in the scoring-math QC notes (not in this tree): extract misses;
  **the old `TARGET_TURN=5` killed engine signal**; Exquisite Blood Δ=0;
  hollow gates. Live constant is 10.
- **Cost.** Offline heavy sim; `--payoffs-only` network.
- **Redundancy.** Extract pairs overlap `test_perception`; the tables are unique here.

### `scripts/calibrate_forge.py` — **KEEP**
- **Measures.** sim vs Forge on `forge_log.MEASURABLE` only (lands, life_gained,
  life_drained), hero turns.
- **Found.** Forge calibration notes (not in this tree): Wayfarer’s Bauble free-land
  bug (`SIM_VERSION` 8); life_drained combat confound (**not Forge-calibratable**);
  16/50 games reached hero T10; lands @T6 confirmed.
- **Cost.** Java + Forge; tens of minutes (or `--from-logs` offline).
- **Redundancy.** Only external arm on goldfish math. Uses `pressure` + `forge_log`.

---

## Substrate builders (write product data; verify catalogs)

| Script | What | Unique find | Cost | Opinion |
|---|---|---|---|---|
| `refresh_scryfall_grammar.py` | Refreshes `data/scryfall_grammar.json` | `heists` → None (honest miss) | network | **KEEP** |
| `build_behaviour_tags.py` | Probes otag: tags; fails on 404/0/unreachable | guessed otag → empty | network | **KEEP** |
| `build_mechanic_taxonomy.py` | Emits taxonomy; fails if keyword absent | The taxonomy apostrophe fix emission discipline | network | **KEEP** |
| `build_commanders_kb.py` | Community + derived playstyles into KB | Disagreements recorded, not resolved | network optional | **KEEP** (product data; unused on main path today) |
| `derive_synergy_objects.py` | Closed object set from catalogs | Spec forbids hand-authoring | offline | **KEEP** |

---

## Root libraries that look like product

| Module | Classification | Why |
|---|---|---|
| `sim.py` | **Ambiguous — product primary** | Build scoring + `/api/simulate` + overview. Also the thing harnesses calibrate. |
| `coverage.py` | **Product** | `/api/coverage` + ask-box pool arithmetic. Not the test runner. |
| `pressure.py` | **Product (opt-in)** | `/api/pressure*`. Harness also shells it for Forge calibration. |
| `forge_log.py` | **Harness library** | Docstring: "Nothing here belongs on the build path." Not imported by `app.py`. |

---

## Unit / gate tests (`test_*.py`)

| File | Measures | Unique / note | Cost | Opinion |
|---|---|---|---|---|
| `test_deckwright.py` | Broad offline + opt-in live vocab | The full-sweep vocabulary grading unreachable ≠ 0; kitchen-sink gate | offline s; `--live` ~3–4 min | **KEEP** |
| `test_perception.py` | Engine vs one-shot direction; adversarial extracts | Sacred perception gate | offline | **KEEP** |
| `test_prompts.py` | Golden prompt eval | Verifier offline; live costs model | offline / model | **KEEP**; run on prompt changes |
| `test_mechanics.py` | Families / signals / near-misses | The system audit D1 lifelink false positive | offline | **KEEP** |
| `test_plan_graph.py` | Plan derive goldens | Watch hollow goldens | offline | **KEEP** |
| `test_synergy.py` | Edges / objects / Hei Bai cohesion | Object-typed edges; expert falsification | offline | **KEEP** |
| `test_curve.py` | Castability / playability | Expert Hei Bai reference band; asserts `SKELETON_LIVE=False` | offline | **KEEP** |
| `test_resolve.py` | `resolve_term` fixtures + live ≥1 | Grammar verified when written | offline / live | **KEEP** |
| `test_readiness.py` | Blockers / pool depth honesty | Unknown ≠ zero; empty brief blocked | offline / live | **KEEP** |
| `test_readback.py` | Readback before commanders | Unresolved never surfaces as 0 | offline / live | **KEEP** (same honesty bucket as readiness) |
| `test_playstyles.py` | Vocab + detector offline | ≠ `check_playstyles` | offline | **KEEP** |
| `test_behaviour_tags.py` | Build-gate shape + behaviour branch | Live probes stay in build script | offline | **KEEP** |
| `test_commanders_kb.py` | KB contract | Offline only | offline | **KEEP** |
| `test_commander_difficulty.py` | One plain difficulty sentence | Thin; merge candidate into spine | offline | **KEEP or merge** |
| `test_backend_spine.py` | Suggest/swap/deck_state/precon_delta | Backend grab bag | offline | **KEEP** (sprawling) |
| `test_empty_brief_refusal.py` | Empty brief must refuse | Tier 1 once missed this | offline | **KEEP** |
| `test_differential_harness.py` | Comparison / UNKNOWN rules | Was missing from Tier 1 | offline | **KEEP** |
| `test_expert_hei_bai_split.py` | chosen/mentioned + Close/Better | Contaminated-50 fix; **still duplicated in check_all** | offline | **KEEP**; drop the duplicate list entry |
| `test_hei_bai_baseline_probes.py` | Leave-cause vs leave-phrase | The leave-causation probe without server | offline | **KEEP** |
| `test_forge_log.py` | Parse fixture log without Java | Zone Change never for ETB → board counts NOT_MEASURABLE | offline | **KEEP** |
| `test_forge_calibrate.py` | Calibration compares MEASURABLE only | Guards against calibrating NOT_MEASURABLE | offline | **KEEP** |
| `test_check_all.py` | Tier house rules / SKIP≠PASS | Must not run inside unit list | offline | **KEEP**; run separately |

---

## Fixtures and audit artifacts

| Path | Role | Opinion |
|---|---|---|
| API property audit artifacts | API property audit: `/api/simulate` 500 on missing commander; 22 untyped bodies; local-network+no-auth | **KEEP** artifact; re-run via check_all T2 |
| `testdata/review/*` | Hei Bai / differential / Forge / playstyles ground truth | **KEEP** |
| `docs/calibration/` | Forge delta table | **KEEP** |
| `docs/review/design-review/REPORT.md` | UI layout/token measurement | **KEEP** as UI harness (T2 Playwright); not deck math |

---

## Blunt redundancy map

```
check_all ──┬─ T1: almost all test_*.py (+ perception, node)
            ├─ T2: check_payoff_queries · playwright · schemathesis
            └─ T3: e2e_flow · differential · calibrate_forge

Perception math:  test_perception ≈ audit_scoring_math --extract  ⊂ audit_scoring_math full
                  calibrate_forge = only external (Forge) arm

Payoffs:          check_payoff_queries (gate) + audit_scoring_math --payoffs (kind audit)

Wish→99:          e2e_flow ⊂ differential_harness ⊂ (manual Hei Bai baseline)

Hei Bai cluster:  extract → fixtures → agreement | function_coverage | baseline probes
                  test_synergy / test_curve also use expert ground truth

Playstyles:       test_playstyles (contract) ≠ check_playstyles (community)
                  ≠ build_commanders_kb (writes data; unused on main path)

Scryfall depth:   check.py (roles) ≠ test_deckwright --live (vocab)
                  ≠ check_payoff_queries (payoff shapes)
```

**Operator-manual gap.** Tier 1 does not run Hei Bai *live* scripts,
`check_playstyles`, `function_coverage`, or `check.py`. "Did we listen / cover
his functions / hit playstyles" is still a checklist, not `check_all`.

**Nothing safe to delete outright.** Closest demotions: treat `audit_scoring_math` and Hei
Bai live scripts as research (already mostly true); fix the check_all duplicate;
refresh hei-bai-baseline probe keys to match the leave-causation probe PROBES.

---

# Part C — honest assessment

## 1. Product machinery

Code a deck could not be built without on the main path:

- `app.py` — chat, commanders, build, thesis, simulate, verifiers, role ladders
- `llm.py` — model client
- `resolve.py` + `data/scryfall_grammar.json` (+ behaviour tags)
- `plan_graph.py` + mechanic family / taxonomy data
- `synergy.py` + `data/synergy_objects.json` (gate + leave recall)
- `card_score.py`
- `sim.py` (scoring + thesis curves)
- `mana.py`
- `why.py` (`annotate` path)
- `overview.py` (build packaging)
- `curve.py` (`preference` tiebreak; report)
- `thesis_check.py` (simulate review)
- `substitution.py`, `tweak.py`, `swap.py`, `suggest.py`, `deck_state.py`
- `coverage.py` (ask / rebuild arithmetic)
- `index.html`
- Scryfall / optional EDHREC network path inside `app.py`

## 2. Harness

Code that only ever runs to check the product:

- `scripts/check_all.py`, `check.py`
- `scripts/differential_harness.py`, `e2e_flow.py`
- `scripts/function_coverage.py`, `hei_bai_agreement.py`, `hei_bai_baseline.py`,
  `extract_expert_deck.py`
- `scripts/check_playstyles.py`, `check_payoff_queries.py`
- `scripts/audit_scoring_math.py`, `calibrate_forge.py`
- `forge_log.py`
- All `test_*.py` listed above
- `testdata/review/*`, `_(removed)_` (measurement artifacts)
- Substrate builders when run as refresh jobs:
  `refresh_scryfall_grammar.py`, `build_behaviour_tags.py`,
  `build_mechanic_taxonomy.py`, `build_commanders_kb.py`,
  `derive_synergy_objects.py`

## 3. Ambiguous

| Piece | Why both |
|---|---|
| `sim.py` | Selects cards (marginal lift) **and** is the calibration target |
| `pressure.py` | Product button for her **and** Forge arm for calibrate_forge |
| ~~`commanders.py` / `playstyles.py` / `data/commanders.json`~~ | **No longer ambiguous — product.** #173 gave them callers on the product path (picker fit, ways, plan binding). Kept in this table only to record the move. |
| `curve.py` | Tiebreak in scoring (product) + castability report that does not reshape the 99 |
| `synergy.cohesion` | Computed on the product path; only the differential harness reads the summary |
| `overview.run_build_sim` | Product packaging; uses a different condition set than the thesis sim she sees |

---

## What is safe to freeze

**"The harness" means:** the measurement ladder in `scripts/check_all.py` (T1–T3),
the Hei Bai cluster (extract → baseline → agreement / function_coverage),
`check_payoff_queries`, Forge calibration (`forge_log` + `calibrate_forge`),
`differential_harness` / `e2e_flow`, `check_playstyles` as a research instrument,
and the offline unit files those tiers already run — plus the audit fixtures
under `testdata/review/`. Freeze means: do not add new measurement
scripts, do not widen Tier 1, do not retune Forge calibration targets, until the
commander flow ships and actually **calls** `commanders.py` / `playstyles.py`.
**That condition is now met** (#173) — the freeze held through the commander
flow and stays in force; see `OPERATIONS.md` sanctity section.

**Not frozen:** the product spine on the main path — interrogation, resolve,
commander ladder, plan_graph, role recall, synergy gate, card_score, mana, why,
thesis, sim. Also not frozen: wiring gaps this map names (KB unused, cohesion
dropped — **closed**: `explain_edges` wired, `SKELETON_LIVE=True`). Historical note;
see banner. Those were product work,
not harness work. Touching harness files to unblock commander-flow integration
is allowed; inventing a fourth measurement tier is not.
