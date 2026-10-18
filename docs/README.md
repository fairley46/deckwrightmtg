# Documentation map

This is the index for living docs. Historical plans are stamped **ARCHIVED** at
the top of each file. Do not treat those as current system-of-record.

**Project status:** [STATUS.md](STATUS.md). Local tool; not a hosted service.

## New reader path

Read in this order. Each step has one job.

| Step | Doc | Job |
|---|---|---|
| 1 | [../README.md](../README.md) | What it is, why it exists, how to run, honest limits |
| 2 | **This file** | Docs map, where files live, where to contribute |
| 3 | [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) | Connection spine: journey, modules, verifiers, frozen vs recomputed |
| 4 | [ARCHITECTURE.md](ARCHITECTURE.md) | Shape diagrams (points at HOW-IT-CONNECTS for detail) |
| 5 | [OPERATING-MODEL.md](OPERATING-MODEL.md) | How work becomes a change, what stops a bad one |
| 6 | [CONTRIBUTING.md](CONTRIBUTING.md) | Contributor rules and the verify gate |

Then open [OPERATIONS.md](OPERATIONS.md) when you need dependencies, the LLM
adapter, cadence, or sanctity.

## Where things live

### Top-level folders

| Path | Job |
|---|---|
| `docs/` | Living docs, contracts, product notes, screenshots under `docs/assets/` |
| `data/` | Checked-in bakes and reports (grammar, coverage, playstyles). Precon and EDHREC benchmark JSON are regenerated locally |
| `scripts/` | Gates and offline jobs (`check_all.py`, print-wave, bakes, refresh) |
| `deckwright/` | Application package (FastAPI app and supporting modules) |
| `tests/` | Offline and live `test_*.py` files |
| `config/` | Optional helpers |
| `prompts/` | Golden-set fixtures for `test_prompts.py` |
| `testdata/` | Fixtures for offline tests (corpus samples, benchmarks, review fixtures) |
| `.github/` | CI workflows (tier 1 on every PR) |

Runtime state stays under `~/.deckwright/` (saved decks, local Scryfall corpus,
prices, precon catalog cache). That path is not in the repo. See
[OPERATIONS.md](OPERATIONS.md).

### Load-bearing files

| File | Job |
|---|---|
| `deckwright/app.py` | FastAPI routes, query ladders, the three verifiers, persistence |
| `index.html` | Single-page frontend, vanilla JS, no build step |
| `deckwright/llm.py` | Anthropic Claude adapter (CLI or API); other models are one adapter away |
| `deckwright/corpus.py` | Local Scryfall bulk index; `scry()` reads here first |
| `deckwright/resolve.py` / `synergy.py` / `plan_graph.py` / `playstyles.py` / `commanders.py` | Grammar → edges → plan → ways |
| `deckwright/curve.py` / `mana.py` | Live skeleton shaping and mana base |
| `deckwright/card_score.py` | Ranks role pools by simulated lift |
| `deckwright/sim.py` | Oracle-text extraction and goldfish Monte Carlo |
| `deckwright/check.py` / `run.sh` | Preflight (`python -m deckwright.check`) and launch |
| `scripts/check_all.py` | Merge gate (`--tier 1`) |

Public entry and run instructions: [../README.md](../README.md). Hop-by-hop spine:
[HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md).

## Start here (ops)

| Doc | Job |
|---|---|
| [STATUS.md](STATUS.md) | What the project is today |
| [OPERATIONS.md](OPERATIONS.md) | Dependencies, LLM adapter, Archidekt precon fetch, cadence |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Short contributor start and the verify gate |

## Product / understanding

| Doc | Job |
|---|---|
| [product/SHIP-V2.md](product/SHIP-V2.md) | V2 design notes |
| [product/EDHREC-SEED.md](product/EDHREC-SEED.md) | Seed product path |
| [DERIVATION.md](DERIVATION.md) | Wish → deck stages + QC checks |
| [KNOW-MAGIC-MAINTENANCE.md](KNOW-MAGIC-MAINTENANCE.md) | Coverage maintenance + print-wave |
| [KNOW-MAGIC-INTEGRATION.md](KNOW-MAGIC-INTEGRATION.md) | How understanding joins the product |

## Contracts

| Doc | Job |
|---|---|
| [SCRYFALL-CONTRACT.md](SCRYFALL-CONTRACT.md) | Scryfall honesty |
| [SCRYFALL-GRAMMAR.md](SCRYFALL-GRAMMAR.md) | Grammar / catalogs |
| [SCORING-CONTRACT.md](SCORING-CONTRACT.md) | Scoring math |
| [PLAN-GRAPH-SCHEMA.md](PLAN-GRAPH-SCHEMA.md) | Plan shape + `THESIS_KEYS` |
| [VOCABULARIES.md](VOCABULARIES.md) | Playstyle / signal vocabularies |

## Maps

| Doc | Job |
|---|---|
| [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) | Prefer this for "how does A call B?" |
| [SYSTEM-MAP.md](SYSTEM-MAP.md) | Deep inventory / harness notes. Secondary; code wins |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Shape diagrams |
