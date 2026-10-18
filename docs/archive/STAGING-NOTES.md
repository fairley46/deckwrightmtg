# Staging and bake-off notes (historical)

These notes describe the multi-process staging setup (blue/green ports, candidate B and C flags) and the CardFacts bake used during development. The launchd installers they mention are not shipped in this repository.

## Blue / green

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
./config/install-green-c-launchd.sh        # :8003, candidate C (opt-in)
# Templates carry DECKWRIGHT_LLM_TIMEOUT=600. Install prints it;
# missing → WARNING. Hand-edits under ~/Library/LaunchAgents/ alone do not
# Survive the next reinstall (that is how the floor-case measurement's fix was wiped).
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

`run.sh` prepends Homebrew to `PATH` **before** creating `.venv`,
and refuses a venv older than 3.10 instead of crash-looping. Cold re-test
on the demo host: `rm -rf .venv` in the green worktree, then restart green.

`install-green-launchd.sh` refuses the primary clone (`.git` is a
directory) and refuses a `REPO_ROOT` that matches blue's installed
WorkingDirectory.

### Gate 2 — B clarity

Green on `:8001` keeps `DECKWRIGHT_CANDIDATE=off` so contestant **BLUE** stays V1.
The clarity audit block is visible on green-b (`:8002`, candidate `B`) or by
temporarily flipping the `:8001` plist — for bake-offs, run both processes.

`B` attaches `commander_strategy` + `strategy_audit` and paints `#clarity` on
the deck page. Soft combos and hidden power print “we do not score this.”
Unread commanders keep the V1 list and say so.

### Gate 4 — B steer

When `DECKWRIGHT_CANDIDATE=B` and the commander reads (`ok`/`partial`), the build
path soft-steers the 99: prefer cards feeding strategy inputs, deprioritise known
bound violations. `bound_ok == unknown` is neutral. Unread commanders do not steer.

**Bound vocabulary:** `bounds[].op` is `leq` (ceiling), `geq`
(floor), `in_set` (subtype), or `forbid` (oracle ban). Static MV floors/ceilings
use `bound.mode=static` + numeric `value`. Life- and experience-capacity
ceilings stay `event_capacity` (experience capacity is unknown from the 99 —
unknown ≠ invent a typical-4). Also: `creature_power` static ceilings
(Shirei-shaped) and `draw`/`forbid` (Maralen-shaped). Audit and steer share
`strategy_audit.card_bound_ok` so type guards and op cannot drift.

Harness: contestant **B** → `http://127.0.0.1:8002` (`DECKWRIGHT_GREEN_B_URL`).
Contestant **C** → `http://127.0.0.1:8003` (`DECKWRIGHT_GREEN_C_URL`).
Contestant **BLUE** → `:8001` with candidate `off`. Never set candidate flags on
blue `:8000` — `instance.boot_error()` refuses it.
Registry: `data/benchmarks/contestants.json`.

```bash
./config/install-green-b-launchd.sh
./config/install-green-c-launchd.sh   # only when measuring C; not required for B
python scripts/v2_benchmark.py --contestants E,BLUE,B     # harness checkout
python scripts/v2_benchmark.py --contestants E,BLUE,B,C   # after C registered
```

Keep `:8001` candidate=`off`; do not flip blue. Do not make C the default.

### Candidate C — CardFacts ServeReport

When `DECKWRIGHT_CANDIDATE=C` (green-c `:8003`), build attaches B clarity +
steer **and** stamps per-card `serve_report` from `cardfacts.lookup` +
CommanderStrategy (`feeds_input`, `consumes_output`, `bound_ok`,
`concept_hits`, `weighted_serve`). Missing store or card → unknown fields,
never empty-as-zero. Bound math stays on `strategy_audit.card_bound_ok`.
`app.py` / `card_score` do not import CardFacts. Soft combo / hidden power
stay `computable: false`, weight 0.

Plan + remaining bake-off/UI work: [`docs/product/C-REMAINING.md`](product-C-REMAINING.md).

**LLM timeout:** green and green-b plist templates set
`DECKWRIGHT_LLM_TIMEOUT=600`. Without it, `llm.py` defaults to 180s and Bello
(BLUE ~571s) fails again after any by-the-book reinstall. Blue's template is
unchanged. green-c must carry the same key.

**Bake-off readiness:** see
[`docs/benchmarks/BAKEOFF-READY.md`](benchmarks-BAKEOFF-READY.md) and
[`docs/product/C-REMAINING.md`](product-C-REMAINING.md). Bound coverage must
be SEEN=6 before reading Gate 5/C bound columns. Do not pass `--green-url` on
multi-contestant sweeps. Do not promote B; do not enable C as default.

---
## CardFacts pipeline — bake offline; serve only on candidate C

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

