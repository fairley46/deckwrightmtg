# Documentation

Start with the [project README](../README.md) for what DeckWright is and how to
run it. The docs below go deeper. Code is the final word; if a doc disagrees
with it, the doc is out of date.

## Reading order

| Step | Doc | What it covers |
|---|---|---|
| 1 | [../README.md](../README.md) | What it is, how it works, quick start, configuration |
| 2 | [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) | The request path from the UI through each API route to the modules behind it |
| 3 | [ARCHITECTURE.md](ARCHITECTURE.md) | Shape diagrams and the one rule |
| 4 | [DERIVATION.md](DERIVATION.md) | How a wish becomes a deck, stage by stage, with the checks for each stage |
| 5 | [CONTRIBUTING.md](CONTRIBUTING.md) | Contributor rules and the test gate |

## Running it

| Doc | What it covers |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | Dependencies, health checks, data refresh, state on disk, load-bearing parts |
| [OPERATING-MODEL.md](OPERATING-MODEL.md) | The test gate, access, and what happens when Scryfall is unavailable |
| [STATUS.md](STATUS.md) | What works today |

## Design contracts

| Doc | What it covers |
|---|---|
| [SCRYFALL-GRAMMAR.md](SCRYFALL-GRAMMAR.md) | How words become Scryfall queries |
| [SCRYFALL-CONTRACT.md](SCRYFALL-CONTRACT.md) | Which fields are kept from a Scryfall card object |
| [PLAN-GRAPH-SCHEMA.md](PLAN-GRAPH-SCHEMA.md) | The shape of a deck plan and `THESIS_KEYS` |
| [SCORING-CONTRACT.md](SCORING-CONTRACT.md) | How cards are scored by simulated lift, with the measurements behind it |
| [VOCABULARIES.md](VOCABULARIES.md) | The two playstyle vocabularies and why they stay separate |
| [KNOW-MAGIC-INTEGRATION.md](KNOW-MAGIC-INTEGRATION.md) | How card-text understanding feeds the build |
| [KNOW-MAGIC-MAINTENANCE.md](KNOW-MAGIC-MAINTENANCE.md) | Keeping card understanding current after new sets |

## Other folders

| Path | What it holds |
|---|---|
| `assets/` | Screenshots used in the README |
| `calibration/` | Forge calibration data referenced by the scoring contract |
| [archive/](archive/README.md) | Historical development notes (benchmarks, bake-off plans, research logs); not current documentation |

## Where things live in the code

| File | Job |
|---|---|
| `deckwright/app.py` | FastAPI routes, query ladders, the three verifiers, persistence |
| `index.html` | Single-page frontend, vanilla JS, no build step |
| `deckwright/llm.py` | Anthropic Claude adapter (CLI or API) |
| `deckwright/corpus.py` | Local Scryfall bulk index; `scry()` reads here first |
| `deckwright/resolve.py` / `synergy.py` / `plan_graph.py` / `playstyles.py` / `commanders.py` | Grammar → edges → plan → ways to play |
| `deckwright/curve.py` / `mana.py` | Deck skeleton and mana base |
| `deckwright/card_score.py` | Ranks role pools by simulated lift |
| `deckwright/sim.py` | Oracle-text extraction and goldfish simulation |
| `deckwright/check.py` / `run.sh` | Preflight (`python -m deckwright.check`) and launch |
| `scripts/check_all.py` | The test gate (`--tier 1`) |

Saved decks and caches live under `~/.deckwright/` (or `DECKWRIGHT_HOME`), not
in the repository. See [OPERATIONS.md](OPERATIONS.md#state-on-disk).
