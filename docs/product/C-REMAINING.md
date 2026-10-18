# Candidate C — remaining implementation plan

Ceiling rung of the three-version bake-off. Design authority:
commander-driven evaluation in the v2 design (Part B) — CardFacts →
CommanderStrategy → ServeReport.

**Status now**

| Piece | State |
|---|---|
| CardFacts scaffold + extract + offline bake | Landed (pipeline / ready) |
| Candidate B (strategy + steer + stamps) | Shipped on `main` (Gate 4B) |
| CardFacts → audit/steer behind `DECKWRIGHT_CANDIDATE=C` | **This PR — flag-gated only** |
| Contestant C in harness registry | Registered (`:8003` / `DECKWRIGHT_GREEN_C_URL`) — **not default** |
| B-vs-C observability | `scripts/compare_b_vs_cardfacts.py` |
| Full-corpus bake on the demo host | operator / demo host (resume-safe bake) |
| Bake-off `E,BLUE,B,C` | **Not run yet** |
| UI per-concept breakdown | **Not started** |

Gate 5 still stands: do not promote B, do not flip blue, do not make C the
default path. C runs only when green-c is installed or
`DECKWRIGHT_CANDIDATE=C` is set on a green process.

---

## Remaining work

### 1. Bake the full corpus

```bash
source .venv/bin/activate
python scripts/bake_cardfacts.py          # resume/skip-existing by default
python scripts/bake_cardfacts.py --force  # after extract changes
python scripts/cardfacts_status.py        # ok / partial / unread / unknown
python scripts/cardfacts_coverage.py --reextract   # live extract tallies
```

Publish coverage: **`nonempty_coverage` must be 100%** — every card with
oracle text is `ok` or `partial`. Empty-oracle cards stay `unread` by design
(~10% of the dump). Also report `ok` / `steer_usable` for C quality.
100% *ok* on the full dump is not a goal — vanilla keywords stay partial.

Share `otag_membership` harvest with the live-Scryfall build note rather than a second tag cache
(tier-1 rollups + tier-3 consistency).

### 2. Wire lookup into audit / steer (flag-gated) — done in this PR

Behind `DECKWRIGHT_CANDIDATE=C` only:

* `serve_report.for_card` → `feeds_input`, `consumes_output`, `bound_ok`,
  `concept_hits`, `weighted_serve` from CardFacts + CommanderStrategy.
* Soft steer uses CF-informed feeder votes (union with B oracle feeders).
* Bound math shared via `strategy_audit.card_bound_ok` (no fork).
* **Default / BLUE / B paths unchanged.** Missing facts → `unknown`, never
  silent pass or empty-as-zero.

Model never authors CardFacts. Deterministic extractors only.

### 3. Harness — register contestant C — done in this PR

`data/benchmarks/contestants.json` slot `C` is `http_build` →
`http://127.0.0.1:8003` (`DECKWRIGHT_GREEN_C_URL`). Green-c dual instance:
`./config/install-green-c-launchd.sh`. Do not retune harness grading to flatter C.
When the V2 benchmark harness branch merges, add `--green-c-url` the same way
scoped `--green-b-url` (CLI override for C only).

### 4. Bake-off: `E,BLUE,B,C`

Same commanders and metrics as Gate 4/5. Report wall-clock build cost, bake
cost, cold-start when facts are missing, and bound-violation columns with
unknown counted separately. Bello remains the illustration for MV-floor
violation — not a named branch in code. Bound coverage must be SEEN before
reading the bound-violation column.

### 5. UI

Per-concept breakdown on the clarity / audit surface. No single composite
“grade.” Concepts 4 and 6 stay `computable: false`, weight 0 — surfaced as
“we do not score this.” Concepts 1 and 8 stay structural/solitaire until
a table model exists.

---

## Risks

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Unknown ≠ zero** | Missing store, unread card, or failed bake scored as “0 violations” manufactures agreement with B | `lookup` → `None`; ServeReport CF fields `None`; compare script buckets `b_violations_cf_unknown` |
| **Cost** | Full-corpus extract + larger serve path | Bake offline; resume/skip; report bake + build wall-clock in the bake-off |
| **Unread long-tail** | Reminder-only / cryptic oracle → `unread` | Keep `status: unread` + honesty; never invent empty facts |
| **Rules-engine creep** | Encoding Bello-granted abilities onto every MV≥4 artifact | Stay on “what the card says”; structural floor satisfaction only |
| **Drift from B** | Duplicate bound/feeder logic | Share `card_bound_ok`; C adds CF fields, does not fork MV math |
| **Harness pressure** | Temptation to change the V2 benchmark harness to fit C | Register C; do not modify harness grading |
| **Timeout wipe** | Reinstall green without the dual-floor and LLM-timeout fix → 180s again | Templates carry 600; install prints it |

---

## Ports / env (green dual-instance)

| Contestant | Port | Flag | Home | Install |
|---|---|---|---|---|
| BLUE | `:8001` | `off` | `~/.deckwright-green` | `./config/install-green-launchd.sh` |
| B | `:8002` | `B` | `~/.deckwright-green-b` | `./config/install-green-b-launchd.sh` |
| C | `:8003` | `C` | `~/.deckwright-green-c` | `./config/install-green-c-launchd.sh` |

Blue `:8000` refuses any `DECKWRIGHT_CANDIDATE`. C is never default.

---

## Explicit non-goals (still)

* Promoting B to blue / default
* Making C the default candidate
* Importing `cardfacts` from `app.py` / `card_score` (steer/audit may, behind C only)
* Changing sacred prompts, verifiers, or what the sim models
* UI per-concept paint (section 5)
* Running the full `E,BLUE,B,C` bake-off in this PR
