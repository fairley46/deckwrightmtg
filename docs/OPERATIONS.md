# Running DeckWright

What DeckWright depends on, how to check it works, how to keep its data fresh,
and what it stores on disk.

It runs locally on your machine. The default bind is `127.0.0.1:8000`.

---

## What it depends on

### Runtime

| | Version | Notes |
|---|---|---|
| Python | 3.12+ | Same floor as `README.md` and `run.sh`. |
| Packages | see `requirements.txt` | Pinned below the next major. |
| A model | Anthropic Claude (CLI or API key) | See below. |

Needs an LLM. Ships with an Anthropic Claude adapter (API key or `claude` CLI);
all model calls go through `deckwright/llm.py`, so another frontier model is one
adapter away. This tree does not ship other adapters.

`llm.py` prefers the `claude` CLI when it is on PATH. `run.sh` **unsets**
`ANTHROPIC_API_KEY` in that case so a local key cannot silently bill the API.
Force the API with `DECKWRIGHT_LLM=api`. `run.sh` refuses to start when *neither*
CLI nor key is available. Git pull on launch is off by default; set
`DECKWRIGHT_PULL_ON_LAUNCH=1` to opt in.

### External services, at runtime

These are called while someone is using the app. Each one is a way it can break
that has nothing to do with the code.

| Service | Used for | If it's down |
|---|---|---|
| **Scryfall** | Bulk corpus refresh; live `otag:`/`art:` (Tagger) and anything the local index cannot parse; images | Lookups and saved decks stay local with a warm corpus. **Building a deck still needs the live API** — role ladders use `otag:`, which is not in bulk data. Refusals are named (`challenge` / `maintenance` / …); the build stop says saved decks still open. See [OPERATING-MODEL.md § Access](OPERATING-MODEL.md#the-external-dependency-when-scryfall-says-no). |
| **Archidekt** | Precon catalogue and lists (public read JSON API) | Precons fail; building and saved decks are fine. See below. |
| **EDHREC** (`json.edhrec.com`) | Popularity, backfilling invented names | Deep cuts get less accurate; nothing fails. |
| **Google Fonts** | Three typefaces | **Typography falls back to system fonts.** See below. |
| **TCGplayer** | One outbound link | A link 404s. Nothing else. |
| **Anthropic API** | Only if `claude` CLI absent or `DECKWRIGHT_LLM=api` | Model endpoints fail. On the CLI path, no API key is needed. |

Scryfall asks for a descriptive `User-Agent` and under 10 requests a second.
Both are handled in `scry()` and `scry_post()`. Do not remove them, and do not
call Scryfall from the browser — route new lookups through the backend so they
keep inheriting that gate (`/api/cards` is the example to copy).

### Archidekt precons

DeckWright reads Archidekt's public JSON API for the
Archidekt_Precons commander precon catalogue and for
individual lists (`deckwright/precons.py`).

- **User-Agent:** `Deckwright/1.0 (local personal deckbuilder)`
- **Rate limit:** paced under ~8 req/s (semaphore + inter-request gap)
- **Catalog cache:** 30 days under `~/.deckwright/precons.json`
- **Attribution:** when you republish deck data, [link back to Archidekt](https://archidekt.com/)
- **Committed data:** baked `data/precons/*.json` files are not in this tree.
  Fetch at runtime; optionally bake with `python scripts/bake_precons.py`


**Google Fonts.** The page loads three typefaces from Google Fonts, so a page
load makes a request to Google and the typography falls back to system fonts
when you are offline. Self-hosting the fonts would remove that dependency; it
has not been done yet.

### Optional

**Forge** — the ten-game pressure test. A JVM plus a few hundred megabytes of
card data, and an explicit exception to the project's "no Docker, no database,
no build step".

It earns the exception by being optional at runtime: nothing in building,
simulating, pricing or printing a deck touches it, and the app hides the feature
when it is absent. **If any part of the core flow ever starts requiring Forge,
the dependency has failed its terms and comes out.**

```
Java     brew install openjdk          → /opt/homebrew/opt/openjdk/bin/java
Forge    github.com/Card-Forge/forge   → ~/Applications/forge/
         (release tarball, ~300MB; there is no Homebrew cask)
```

`run.sh` finds both automatically. Override with `DECKWRIGHT_FORGE`.

Two things about Forge that are easy to get wrong, both found by running it:

- On macOS it reads decks from `~/Library/Application Support/Forge`, not
  `~/.forge`. Writing to the wrong one is silent — it starts, plays nothing, and
  produces output with no result line in it.
- It resolves `res/` relative to its working directory, so it must be run from
  beside its own jar.

---

## Checking it works

```bash
python -m deckwright.check                  # preflight: Scryfall, model, simulator, Forge
python -m deckwright.check expensive R      # with a theme of your choice
```

`check.py` tests each layer on its own, so you learn *which* part is broken
rather than staring at a blank page.

```bash
python scripts/check_all.py --tier 1        # the offline merge gate (what CI runs)
python tests/test_deckwright.py --unit      # offline suite, seconds
python tests/test_deckwright.py --live      # adds live Scryfall checks
python tests/test_deckwright.py --live --full   # every catalogue term, ~880 queries, ~5 min
python tests/test_prompts.py                # the golden-set prompt harness
python scripts/print_wave_reeval.py         # card-understanding re-evaluation report
```

**Green unit tests are not the same as working.** Several real bugs in this
project passed their own tests (a lift of `+0.0%` stamped on 79 cards, a
singleton guard that passed on a fixture holding one Island). Check a claim
against a real deck as well.

---

## Keeping data fresh

| Tier | What moves | When |
|---|---|---|
| **1. Launch / weekly** | Local card corpus (`python -m corpus` → `oracle_cards` + prices); Scryfall grammar; `synergy_objects`; commander knowledge base; oracle coverage and playstyle precision | On a stale launch (`boot.py`) or a scheduled `refresh_data_cadence.sh --kb --reeval` |
| **2. Print wave** | Re-download oracle bulk data, rewrite the print-wave report, list unread card clusters | `./update.sh --print-wave NAME` after a new set or precon release |
| **3. Admit** | New `oracle_signals`, playstyle maps, grammar expressions, strategy commitments | A reviewed pull request — see [KNOW-MAGIC-MAINTENANCE.md](KNOW-MAGIC-MAINTENANCE.md) |

On launch, `boot.py` warms the vocabularies. If the grammar or commander
knowledge base is older than 14 days, or the local corpus is missing, it runs
the refresh chain and then the offline re-evaluation. The loading screen waits
on `GET /api/boot` (`phase`: warming → refreshing → understanding → ready).

`run.sh` runs `pip install -q -r requirements.txt` on every start, so dependency
changes land on the next restart. Git pull on launch is off unless you set
`DECKWRIGHT_PULL_ON_LAUNCH=1`. Boot knobs: `DECKWRIGHT_BOOT_REFRESH=1`,
`DECKWRIGHT_SKIP_BOOT_REFRESH=1`, `DECKWRIGHT_SKIP_BOOT_REEVAL=1`.

### Manual refresh

```bash
./scripts/refresh_data_cadence.sh                  # corpus → grammar → synergy → staleness
./scripts/refresh_data_cadence.sh --kb             # + commander knowledge base
./scripts/refresh_data_cadence.sh --kb --reeval    # + understanding re-evaluation
./scripts/refresh_data_cadence.sh --kb --precons   # + rebake stale precons (needs the model)
python -m corpus                                   # corpus only (oracle_cards + prices)
python -m corpus --status                          # age and card count
./update.sh --print-wave NAME                      # after a major set or precon release
```

The script exits 1 before any network call if a required script is missing.
Staleness is informational: `scripts/check_data_staleness.py` exits 0 with
`STALE` warnings and exits 1 only when a tracked artifact file is missing.
After a vocabulary or `overview.ANALYSIS_VERSION` bump, run `--precons` once.

**Watch for when updating code:**

- **`sim.SIM_VERSION`**: bumping it invalidates every saved simulation, which
  is the point. A smarter simulator must not report old numbers.
- **`overview.ANALYSIS_VERSION`**: bumping it marks baked precon analyses stale.
  They recompute lazily; `python scripts/bake_precons.py --stale` does it up front.
- **`index.html`** is read from disk per request and served `no-store`, so UI
  changes show on refresh with no restart.
- **Backend changes need a restart.** Restarting mid-build drops the in-flight
  request.

---

## State on disk

| Path | What | Safe to delete? |
|---|---|---|
| `~/.deckwright/*.json` | Saved decks (plus `vocab.json` and other meta files) | **No.** Saved decks are the only irreplaceable thing here. |
| `~/.deckwright/oracle_cards.jsonl` | The local card corpus (from Scryfall bulk data) | Yes; `python -m corpus` re-downloads it. Without it every lookup hits the live API. |
| `~/.deckwright/prices.json` | Ballpark USD prices from `default_cards` | Yes; refreshed with the corpus. |
| `~/.deckwright/corpus.meta.json` | When the corpus was last fetched | Yes. |
| `~/.deckwright/precons.json` | Cached Archidekt precon catalogue | Yes; refetched after 30 days or when missing. |
| `~/.deckwright/cardfacts.json` | Optional per-card clause facts (experimental) | Yes; rebuild with `scripts/bake_cardfacts.py`. |
| `~/.deckwright/bugs/*.json` | Bug reports from the footer form | Yes, once read. |
| `~/Library/Application Support/Forge/decks/commander/dw-*.dck` | Forge pressure-test scratch decks | Yes. |
| `.venv/` | Python packages | Yes; `run.sh` rebuilds it. |

Set `DECKWRIGHT_HOME` to keep this state somewhere other than `~/.deckwright/`.

Saved decks carry their own simulation, stamped with the deck's hash and the
simulator version, so reopening one is a read rather than a recompute. Change
either and it recomputes on the next view.

There is no database and no migration step. A saved deck is a JSON file, and
older ones simply lack newer fields; the app checks rather than assuming.

---

## Secrets

Set `ANTHROPIC_API_KEY` in a local `.env` (gitignored), or install the `claude`
CLI. No key is committed. On the CLI path the server runs with no API key.

Bug reports are written to disk under `~/.deckwright/bugs/`. Filing them to a
remote issue tracker is disabled in this repository.

---

## Load-bearing parts

Some parts of the code carry product judgment rather than plumbing. Change them
deliberately, in their own pull request, with the tradeoff stated.

| Part | Why it matters |
|---|---|
| The three verifiers (`/api/commanders`, `/api/build`, `/api/thesis`) | They keep card names real. The model writes Scryfall *queries*; the verifier drops any name that was not in the response. Remove one and the app starts inventing cards. |
| `INTERROGATOR` and `THESIS` prompt text | Product judgment written as prose. Changing a sentence changes what the app asks and what it counts as working. |
| `ROLES` and budget policy | Deck-construction targets per role. The live skeleton may *shape* counts; a cleanup should not invent new floors. |
| What `sim.py` models | Goldfish games with no opponent. Extraction fixes need a stated expected value, because changing what is measured changes every probability downstream. |
| The commander-flow seams | `strategy_fit` · `theme_fit` · `ways_to_play` → `way_to_play` → plan · `synergy_recall_queries` · `account_deck`. |

### The two house rules

**The model never writes a card name from memory.** Queries → Scryfall → choose
from real cards → the verifier drops inventions. This applies to generated data
and fixtures as strictly as to output.

**An unknown is not a zero, and a fallback is not a match.** Any layer that can
return empty must distinguish "there are none" from "I could not look." For
example, `search()` once swallowed transport failures into `[]`, and the
commander ladder walked past searches it never actually saw.

Historical notes on the staging setup and experimental candidates are in
[archive/STAGING-NOTES.md](archive/STAGING-NOTES.md).
