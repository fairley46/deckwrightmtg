# Running DeckWright

Everything about keeping it alive: what it depends on, how to update it, what
breaks and how you find out.

Runs locally on your machine. Default bind is `127.0.0.1:8000`.

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
| **Scryfall** | Bulk corpus refresh; live `otag:`/`art:` (Tagger) and anything the local index cannot parse; images | Lookups and saved decks stay local with a warm corpus (CON-294). **Building a deck still needs the live API** — role ladders use `otag:`, which is not in bulk data (CON-303). Refusals are named (`challenge` / `maintenance` / …); the build stop says saved decks still open. See [OPERATING-MODEL.md § Access](OPERATING-MODEL.md#the-external-dependency--when-scryfall-says-no). |
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
[Archidekt_Precons](https://archidekt.com/) commander precon catalog and for
individual lists (`deckwright/precons.py`).

- **User-Agent:** `Deckwright/1.0 (local personal deckbuilder)`
- **Rate limit:** paced under ~8 req/s (semaphore + inter-request gap)
- **Catalog cache:** 30 days under `~/.deckwright/precons.json`
- **Attribution:** when you republish deck data, [link back to Archidekt](https://archidekt.com/)
- **Committed data:** baked `data/precons/*.json` files are not in this tree.
  Fetch at runtime; optionally bake with `python scripts/bake_precons.py`

**Google Fonts is the odd one out.** A local app on a local network reaches out
to Google on every page load, which means the typography — load-bearing in this
design — depends on someone else's uptime, and Google sees a request every time
she opens a deck. Self-hosting the three families would remove both problems.
Not done; recorded here so it is a decision rather than an oversight.

### Optional

**Forge** — the ten-game pressure test. A JVM plus a few hundred megabytes of
card data, and an explicit exception to CONTRIBUTING.md's "no Docker, no database,
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
python -m deckwright.check                  # preflight: Scryfall, model meter, simulator, engine
python -m deckwright.check expensive R      # with a theme you actually care about
```

`check.py` tests each layer independently so you learn *which* is broken rather
than staring at a blank page. It prints `model calls -> …` and probes that meter
(CON-262). It reports the pressure-test engine too — that belongs to whoever set
the machine up, not to someone building a deck.

```bash
python test_deckwright.py --unit   # offline suite, seconds
python test_deckwright.py --live   # adds live Scryfall checks
python test_deckwright.py --live --full   # every catalog term, ~880 queries, ~5 min
python test_prompts.py             # the golden-set prompt harness (CON-78)
python scripts/print_wave_reeval.py      # Know Magic print-wave path (CON-260)
```

**`--unit` green is not the same as working.** Nearly every real bug this
project has hit passed its own tests: `+0.0%` stamped on 79 cards, a singleton
guard that passed on a fixture holding one Island, ramp queries "verified" by
0/10 pool overlap where one pool was junk. Run the claim against a real deck.

---

## Updating

**Default (CON-285):** every desktop launch updates itself — catalogs **and**
understanding remeasure when data is stale. New *reading rules* and *strategies*
are not invented on boot; those stay an admit PR after the report names a gap.

| Tier | What moves | When |
|---|---|---|
| **1. Launch / weekly** | Local card corpus (`python -m corpus` → `oracle_cards` + prices); Scryfall grammar; `synergy_objects`; commanders KB mechanics; oracle coverage + playstyles precision remeasure | Stale boot (`boot.py`) or Sunday cadence (`--kb --reeval`) |
| **2. Named print wave** | Re-download oracle bulk; rewrite CON-260 report; list unread clusters | `./update.sh --print-wave NAME` after a set / precon dump |
| **3. Admit** | New `oracle_signals`, playstyle maps, grammar expressions, strategy commitments | Human / farmed PR — [KNOW-MAGIC-MAINTENANCE.md](KNOW-MAGIC-MAINTENANCE.md) |

Launch sequence:

1. `run.sh` → `scripts/pull_on_launch.sh` (fail-open; skips a dirty tree) —
   install: fetch+checkout `DECKWRIGHT_RELEASE_REF` (a tag); unset = pull
   current branch (dev). See [OPERATING-MODEL.md § Release](OPERATING-MODEL.md#7-release--how-code-reaches-the-house).
2. `boot.py` warms vocab; if grammar/commanders KB is stale (>14 days) **or**
   the local corpus is missing: cadence (starts with `python -m corpus`) + `--kb`,
   then `print_wave_reeval.py` (offline). A fresh grammar with no
   `~/.deckwright/oracle_cards.jsonl` still refreshes — otherwise every lookup
   hits live Scryfall.
3. Brand veil waits on `GET /api/boot` (`phase`: warming → refreshing → understanding → ready); **skipped** when KeepAlive is already warm

```bash
cd ~/Projects/deckwright
./config/install-desktop.sh              # once on the demo host
# Install: set DECKWRIGHT_RELEASE_REF=ga-YYYYMMDD on the LaunchAgent, then restart
# open http://127.0.0.1:8000 — launch path above runs on its own

./update.sh                       # force launchd restart (picks up next launch pull)
./update.sh --pull                # pull now, then restart
./update.sh --cadence             # out-of-band catalogs + --reeval
./update.sh --print-wave NAME     # major set / precon dump (fetch bulk)
```

`run.sh` runs `pip install -q -r requirements.txt` every start, so a dependency
change lands on the next restart with nothing else to do. Skip the launch pull
with `DECKWRIGHT_SKIP_PULL=1`. Boot knobs: `DECKWRIGHT_BOOT_REFRESH=1`,
`DECKWRIGHT_SKIP_BOOT_REFRESH=1`, `DECKWRIGHT_SKIP_BOOT_REEVAL=1`.

**Watch for on update:**

- **`sim.SIM_VERSION`** — bumping it invalidates every saved simulation, which
  is the point: a smarter simulator must not report old numbers.
- **`overview.ANALYSIS_VERSION`** — bumping it makes all 155 precon bakes stale.
  They recompute lazily, but a full `python scripts/bake_precons.py --stale` is faster
  and quieter than doing it under someone's first click.
- **`index.html`** is read from disk per request and served `no-store`, so UI
  changes are live on refresh with no restart.
- **Backend changes need a restart.** Never restart while someone is mid-build:
  it drops the in-flight request and the error blames Scryfall.

---

## Lifecycle

```bash
./config/install-desktop.sh               # CON-285: server + weekly cadence (macOS)
./config/install-launchd.sh               # server only
./config/install-cadence-launchd.sh       # cadence only
./config/install-launchd.sh --uninstall
./config/install-cadence-launchd.sh --uninstall
launchctl list | grep deckwright   # is it registered
tail -f /tmp/deckwright.log        # what it is doing
```

`./config/install-desktop.sh` is the one-command first-time wire-up on the demo host. It
calls the two helpers above. `KeepAlive` is on, so a crash restarts the server.
`RunAtLoad` brings it back after a reboot. Without this the server lives only as
long as the terminal that started it — which is how it has usually been run, and
is worth checking before anyone relies on it being there.

The demo host does not sleep on AC power (`pmset -c sleep 0`).

---

## Blue / green (CON-315)

Two processes. Blue is production. Green is staging. A second `PORT=8001`
without a separate data dir writes the shared install saved decks — that is the
failure this exists to prevent.

| | Blue | Green (`:8001`) | Green-b (`:8002`) | Green-c (`:8003`) |
|---|---|---|---|---|
| Label | `(local launchd label)` | `(local launchd label)` | `(local launchd label)` | `(local launchd label)` |
| URL | `http://127.0.0.1:8000/` (local network too) | `http://127.0.0.1:8001/` (loopback) | `http://127.0.0.1:8002/` (loopback) | `http://127.0.0.1:8003/` (loopback) |
| Checkout | install clone, release tag | **second worktree** of `v2` | same worktree as green | same worktree as green |
| Saved decks | `~/.deckwright/*.json` | `~/.deckwright-green/` | `~/.deckwright-green-b/` | `~/.deckwright-green-c/` |
| Corpus | `~/.deckwright/oracle_cards.jsonl` | same files, read-only | same files, read-only | same files, read-only |
| Candidate | n/a (refuses A/B/C) | `off` (BLUE in bake-off) | `B` (Gate 4 steer) | `C` (CardFacts ServeReport; not default) |
| Badge | none | **STAGING — do not buy this list** | **STAGING — do not buy this list** | **STAGING — do not buy this list** |

```bash
# From the blue clone, after branch v2 exists:
git fetch origin
git worktree add ../deckwright-green v2
cd ../deckwright-green
./config/install-green-launchd.sh          # :8001, candidate off
./config/install-green-b-launchd.sh        # :8002, candidate B (Gate 4 bake-off)
./config/install-green-c-launchd.sh        # :8003, candidate C (CON-322; opt-in)
# CON-334: templates carry DECKWRIGHT_LLM_TIMEOUT=600. Install prints it;
# missing → WARNING. Hand-edits under ~/Library/LaunchAgents/ alone do not
# survive the next reinstall (that is how CON-333's fix was wiped).
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/
# Uninstall green only:
./config/install-green-launchd.sh --uninstall
./config/install-green-b-launchd.sh --uninstall
./config/install-green-c-launchd.sh --uninstall
```

`DECKWRIGHT_CANDIDATE` (A/B/C) is green-only. Blue exits if it is set.
`python scripts/check_all.py --tier 1` on a V2 PR runs against the PR tree
with an ephemeral `HOME`; on the demo host, V2-in-flight checks are the green
worktree.

CON-323: `run.sh` prepends Homebrew to `PATH` **before** creating `.venv`,
and refuses a venv older than 3.10 instead of crash-looping. Cold re-test
on the demo host: `rm -rf .venv` in the green worktree, then restart green.

CON-324: `install-green-launchd.sh` refuses the primary clone (`.git` is a
directory) and refuses a `REPO_ROOT` that matches blue's installed
WorkingDirectory.

### Gate 2 — B clarity (CON-321)

Green on `:8001` keeps `DECKWRIGHT_CANDIDATE=off` so contestant **BLUE** stays V1.
The clarity audit block is visible on green-b (`:8002`, candidate `B`) or by
temporarily flipping the `:8001` plist — for bake-offs, run both processes.

`B` attaches `commander_strategy` + `strategy_audit` and paints `#clarity` on
the deck page. Soft combos and hidden power print “we do not score this.”
Unread commanders keep the V1 list and say so.

### Gate 4 — B steer (CON-321)

When `DECKWRIGHT_CANDIDATE=B` and the commander reads (`ok`/`partial`), the build
path soft-steers the 99: prefer cards feeding strategy inputs, deprioritise known
bound violations. `bound_ok == unknown` is neutral. Unread commanders do not steer.

**Bound vocabulary (CON-331):** `bounds[].op` is `leq` (ceiling), `geq`
(floor), `in_set` (subtype), or `forbid` (oracle ban). Static MV floors/ceilings
use `bound.mode=static` + numeric `value`. Life- and experience-capacity
ceilings stay `event_capacity` (experience capacity is unknown from the 99 —
unknown ≠ invent a typical-4). Also: `creature_power` static ceilings
(Shirei-shaped) and `draw`/`forbid` (Maralen-shaped). Audit and steer share
`strategy_audit.card_bound_ok` so type guards and op cannot drift.

Harness: contestant **B** → `http://127.0.0.1:8002` (`DECKWRIGHT_GREEN_B_URL`).
Contestant **C** → `http://127.0.0.1:8003` (`DECKWRIGHT_GREEN_C_URL`).
Contestant **BLUE** → `:8001` with candidate `off`. Never set candidate flags on
blue `:8000` — `instance.boot_error()` refuses it. See `docs/benchmarks/GATE4.md`
on the harness branch. Registry: `data/benchmarks/contestants.json`.

```bash
./config/install-green-b-launchd.sh
./config/install-green-c-launchd.sh   # only when measuring C; not required for B
python scripts/v2_benchmark.py --contestants E,BLUE,B     # harness checkout
python scripts/v2_benchmark.py --contestants E,BLUE,B,C   # after C registered
```

**Ship B (validated tip `69df005`):** merge order, caveats, and post-merge demo-host
deploy commands live in [`docs/benchmarks/SHIP-B.md`](benchmarks/SHIP-B.md).
Keep `:8001` candidate=`off`; do not flip blue. Do not make C the default.

### Candidate C — CardFacts ServeReport (CON-322)

When `DECKWRIGHT_CANDIDATE=C` (green-c `:8003`), build attaches B clarity +
steer **and** stamps per-card `serve_report` from `cardfacts.lookup` +
CommanderStrategy (`feeds_input`, `consumes_output`, `bound_ok`,
`concept_hits`, `weighted_serve`). Missing store or card → unknown fields,
never empty-as-zero. Bound math stays on `strategy_audit.card_bound_ok`.
`app.py` / `card_score` do not import CardFacts. Soft combo / hidden power
stay `computable: false`, weight 0.

Plan + remaining bake-off/UI work: [`docs/product/C-REMAINING.md`](product/C-REMAINING.md).

**LLM timeout (CON-334):** green and green-b plist templates set
`DECKWRIGHT_LLM_TIMEOUT=600`. Without it, `llm.py` defaults to 180s and Bello
(BLUE ~571s) fails again after any by-the-book reinstall. Blue's template is
unchanged — operator call. green-c (PR #314) must carry the same key when merged.

**Bake-off readiness (after CON-334 + CON-331):** see
[`docs/benchmarks/BAKEOFF-READY.md`](benchmarks/BAKEOFF-READY.md) and
[`docs/product/C-REMAINING.md`](product/C-REMAINING.md). Bound coverage must
be SEEN=6 before reading Gate 5/C bound columns. Do not pass `--green-url` on
multi-contestant sweeps (CON-330). Do not promote B; do not enable C as default.

---

## Data cadence

Grammar, commanders KB mechanics, and precon bakes rot without a schedule. CON-238
wires a chain separate from the KeepAlive server — do not overload
`(local launchd label)`.

### Manual run

```bash
source .venv/bin/activate   # optional; script activates when present
./scripts/refresh_data_cadence.sh              # corpus → grammar → synergy → staleness
./scripts/refresh_data_cadence.sh --kb         # + commanders mechanics-refresh
./scripts/refresh_data_cadence.sh --kb --reeval    # + understanding remeasure (CON-285)
./scripts/refresh_data_cadence.sh --kb --precons   # + rebake stale precons (needs model)
python -m corpus                               # corpus only (oracle_cards + prices)
python -m corpus --status                      # age / card count
python scripts/measure_corpus_calls.py         # local vs live query share for a brief
```

Required scripts must exist or the script exits 1 before any network call.
`--precons` runs `bake_precons.py --stale` only (skips fresh bakes).

Staleness is informational — `scripts/check_data_staleness.py` exits 0 with
`STALE` warnings; exit 1 only when a tracked artifact file is missing.

### Weekly launchd (macOS demo host)

```bash
./config/install-cadence-launchd.sh               # Sunday 04:00 local; --kb --reeval
./config/install-cadence-launchd.sh --uninstall
launchctl list | grep deckwright.cadence
tail -f /tmp/deckwright-cadence.log
```

Label: `(local launchd label)` — distinct from the server agent.
The installed plist passes `--kb --reeval` (catalogs + understanding pulse).
Add `--precons` to `ProgramArguments` in the template if you want weekly
precon rebakes (model required).

After a vocab or `overview.ANALYSIS_VERSION` bump, run `--precons` once by hand
even if it is not on the weekly schedule.

GA certifies this cadence exists; see `docs/`.

---

## CardFacts pipeline (CON-322) — bake offline; serve only on candidate C

`cardfacts.py` holds per-card clause facts. Path:
`{DECKWRIGHT_HOME}/cardfacts.json` (schema + `extract()` in the module
docstring). Missing file or missing card → unknown (`None`), never
empty-as-zero. A baked `status: unread` entry is known-unread, not a miss.

Serve path reads CardFacts **only** when `DECKWRIGHT_CANDIDATE=C`
(`serve_report` / CF-informed steer). BLUE (`off`) and B do not.

### Demo host — full-corpus bake (resume-safe)

Corpus must already be on disk (`~/.deckwright/oracle_cards.jsonl`). Bake does
not call the model and does not mass-scrape Scryfall.

```bash
source .venv/bin/activate
# once, if the local oracle jsonl is missing:
python -m corpus

# full bake — streams the corpus, skips names already in the store,
# checkpoints every 1000 newly extracted cards, progress every 500:
python scripts/bake_cardfacts.py

# interrupt-safe resume (same command; skip-existing is the default):
python scripts/bake_cardfacts.py

# status / sample lookup (exit 2 if store unknown):
python scripts/cardfacts_status.py
python scripts/cardfacts_status.py --lookup "Sol Ring"
python scripts/bake_cardfacts.py --status
```

Useful caps and overrides:

```bash
python scripts/bake_cardfacts.py --limit 100          # smoke
python scripts/bake_cardfacts.py --force               # re-extract names already baked
python scripts/bake_cardfacts.py --replace             # discard existing store
python scripts/bake_cardfacts.py --jsonl path/to/oracle_cards.jsonl --dry-run
```

`python scripts/stub_cardfacts.py` still writes an empty valid file (optional
`--fixture` Sol Ring) when you only need a placeholder.

**Gate rules that still hold:** do not promote B, do not make C the default,
do not import CardFacts into `app.py` / `card_score`. Contestant C is
opt-in on `:8003` only.

B-stamped deck vs CardFacts (observability):
`docs/benchmarks/B-VS-C.md` · `scripts/compare_b_vs_cardfacts.py`.
Remaining C plan: `docs/product/C-REMAINING.md` (full bake, bake-off, UI).

---

## State on disk

| Path | What | Safe to delete? |
|---|---|---|
| `~/.deckwright/*.json` | Saved decks — hers (exclude `vocab.json` / meta files) | **No.** This is the only irreplaceable thing here. |
| `~/.deckwright/cardfacts.json` | CON-322 CardFacts bake (read on candidate C only) | Yes; `scripts/bake_cardfacts.py` (resume-safe) or stub. |
| `~/.deckwright/bugs/*.json` | Footer bug reports (CON-293) | After triage — each file is one report, with `linear` or `linear_error`. |
| `~/.deckwright/oracle_cards.jsonl` | CON-294 local card corpus (from Scryfall bulk) | Yes; `python -m corpus` re-downloads. Without it every lookup hits the live API. |
| `~/.deckwright/prices.json` | Ballpark USD from `default_cards` | Yes; refreshed with the corpus. |
| `~/.deckwright/corpus.meta.json` | When the corpus was last fetched | Yes; staleness check falls back to oracle meta / mtime. |
| `data/precons/*.json` | 155 baked precons | Yes; recompute with `scripts/bake_precons.py`, slowly. |
| `~/Library/Application Support/Forge/decks/commander/dw-*.dck` | Pressure-test scratch | Yes. |
| `.venv/` | Python packages | Yes; `run.sh` rebuilds it. |

Saved decks carry their own simulation, stamped with the deck's hash and the
simulator version, so reopening one is a read rather than a recompute. Change
either and it recomputes on the next view.

There is no database and no migration story. A saved deck is a JSON file, and
older ones simply lack newer fields — the app checks rather than assuming.

---

## Secrets

Set `ANTHROPIC_API_KEY` in a local `.env` (gitignored), or install the `claude`
CLI. No key is ever committed. On the CLI path the server usually runs with no
API key at all.

Bug reports write to disk under `~/.deckwright/bugs/`. Remote issue-tracker
filing is disabled in this tree.

## Sanctity and freeze

What is not yours to touch casually, and why.

### Sacred — never edited as cleanup

| Thing | Why it is sacred |
|---|---|
| The three verifiers (`/api/commanders`, `/api/build`, `/api/thesis`) | The only thing keeping card names real. The model writes Scryfall *queries*; the verifier drops any name that was not in the response. Remove one and the app starts inventing cards, silently. |
| `INTERROGATOR` and `THESIS` prompt text | Product judgment expressed as prose. Changing a sentence changes what the app asks her and what it counts as working. Box only. |
| `ROLES` and budget policy | Deck-construction judgment — live targets lands 0 (curve-filled) / 10 / 9 / 7 / 3 / 10 / 15 / 5. Live skeleton may *shape* counts (CON-216); cleanup does not invent new floors. |
| What `sim.py` models | Goldfish, no opponent. Extraction fixes need a stated expected value; changing what is measured changes every probability downstream. |
| The #173 commander-flow seams | `strategy_fit` · `theme_fit` · `ways_to_play` → `way_to_play` → plan · `synergy_recall_queries` · `account_deck`. This is the judgment path, not plumbing. |

### Harness frozen

No new measurement scripts, no widening Tier 1, no retuning Forge calibration
targets, no fourth tier. The measurement ladder is finished until the product
work it grades is finished. Touching a harness file to unblock product
integration is fine; adding a new way to measure is not.

Frozen specifically: `scripts/check_all.py` tiers · the Hei Bai cluster
(extract → baseline → agreement / function_coverage) · `check_payoff_queries` ·
Forge calibration (`forge_log` + `con163`) · `differential_harness` ·
`check_playstyles` as a research instrument · fixtures under `testdata/review/`.

### Not frozen

The product spine: interrogation, resolve, commander ladder, `plan_graph`, role
recall, synergy gate, `card_score`, mana, why, thesis, sim. And the wiring gaps
the SYSTEM-MAP names. Those are product work.

### The two house rules, restated

**The model never writes a card name from memory.** Queries → Scryfall → choose
from real cards → verifier drops inventions. Applies to generated data and
fixtures exactly as hard as to output.

**An unknown is not a zero, and a fallback is not a match.** Any layer that can
return empty must distinguish "there are none" from "I could not look." This has
been violated and caught more times than any other rule in the repo — most
recently CON-188 F1, where `search()` swallowed transport failures into `[]` and
the commander ladder walked past rungs it never saw.

### How this list changes

It does not change inside a cleanup PR. A card that needs to break one of these
says so in the card, gets its own PR, and states the tradeoff in the PR body.

Read-only audit of the boundary after GA prep: [SACRED-CORE-REVIEW.md](SACRED-CORE-REVIEW.md).
**Backend GA wrapped 2026-08-09.** Post-tag Auto lane: (Linear CON-269). Freeze claims: .
