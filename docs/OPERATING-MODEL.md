# Operating model

How a change is checked, what protects the product from a bad one, and how the
app behaves when its one critical dependency (Scryfall) is unavailable.

Companions: [README.md](README.md) (docs map) ·
[OPERATIONS.md](OPERATIONS.md) (dependencies, data refresh, state on disk) ·
[HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) (the code path end to end) ·
[CONTRIBUTING.md](CONTRIBUTING.md) (contributor rules).

---

## 1. The gate

`python scripts/check_all.py --tier 1` is authoritative. **A SKIP fails the
tier**: a check that did not run is not a check that passed.

CI runs that command on every pull request and every push to `main`
(`.github/workflows/tier1.yml`). A red tier-1 check means the change does not
land. When CI is unavailable, the same command run locally is still the bar.

Green is necessary, not sufficient. Review means running the change, not just
reading its description, and a pull request should say what a green run does
not prove.

### Guards added after real misses

| Guard | Catches | Added after |
|---|---|---|
| `test_unit_registry.py` | A test file that exists but never runs | Tier 1 reported PASS while about 170 assertions never executed |
| `scripts/api_boundary.py` | Data emitted and never consumed, keys read and never sent | An inert `data-axis` attribute and alternate images that were never persisted |
| `conflict_markers` | Merge markers reaching `main` | A committed `<<<<<<<` in a shipped file |

Adding a guard is the standard response to a defect the suite did not catch.

### Load-bearing parts

Change these deliberately, in their own pull request, with the tradeoff stated
(reasoning in [OPERATIONS.md](OPERATIONS.md#load-bearing-parts)):

- The three verifiers (`/api/commanders`, `/api/build`, `/api/thesis`)
- `INTERROGATOR` and `THESIS` prompt text
- `ROLES` and budget policy
- What `sim.py` models

### The two house rules

**The model never writes a card name from memory.** It writes Scryfall
*queries*; every returned name is checked against the real response and
inventions are dropped.

**An unknown is not a zero, and a fallback is not a match.** Any layer that can
return empty must distinguish "there are none" from "I could not look."

---

## 2. Access

| | |
|---|---|
| **Network** | Local by default. `run.sh` binds `127.0.0.1`. Set `HOST=0.0.0.0` only if you intentionally want other devices on your network to reach the app. |
| **People** | Single-user local install. **No auth, no accounts, no multi-user.** |
| **Saved decks** | One shelf under `~/.deckwright/`. |
| **Secrets** | `ANTHROPIC_API_KEY` in a local `.env` (never committed), or the `claude` CLI with no key at all. |
| **LLM** | Ships with an Anthropic Claude adapter (API key or `claude` CLI). `run.sh` unsets `ANTHROPIC_API_KEY` when the CLI is present so calls do not silently bill the API. |

### The external dependency: when Scryfall says no

Scryfall is the only runtime dependency that can stop the product. It can
refuse with a Cloudflare challenge (usually caused by request volume) or be
down for maintenance.

**Corpus-first protects reading, not building.**
Card lookups serve from `~/.deckwright/oracle_cards.jsonl` first.
That is real resilience for named lookup, ordinary `q=` search, saved decks,
the landing page, and precons. It is **not** resilience for `/api/build`.

Role ladders lean on `otag:` (Scryfall Tagger function tags). Those fields are
not in Scryfall bulk data, so `corpus.needs_live()` sends every one of them to
the live client by design (unsupported is not the same as empty). Function tags
are still the correct index for roles. The survival table:

| Path | Survives Scryfall being down |
|---|---|
| Named lookup, ordinary `q=` search | **Yes**, from the local corpus when loaded |
| Opening a saved deck | **Yes** |
| Landing, precons, local pages | **Yes** |
| **Building a deck** | **No** — role ladders need `otag:` |

Source map (same fact, by query class):

| Path | Source |
|---|---|
| Ordinary `q=` search / named / collection | Local corpus when loaded |
| `otag:` / `art:` alone (Tagger, not in bulk) | Live only — **this is the build path** |
| Composite `t:… otag:…` | Local conjuncts from the corpus; live for `otag:`/`art:`. A partial answer is incomplete, not a smaller match |
| Query the local index cannot parse | Live only |
| Images / set catalogue / bulk refresh | Live |

So a warm corpus and a stopped build at the same moment are not a
contradiction: building needs live Scryfall until tag membership is cached
locally. The app says so at build time: saved decks still open, and a new build
waits on the card source.

A missing corpus falls through to live unless `DECKWRIGHT_CORPUS=local-only`,
which raises an unknown rather than returning empty.

**Boot fetches a missing corpus.** The grammar and knowledge base can be fresh
while `oracle_cards.jsonl` was never downloaded. `boot._should_refresh()` is
true when the bulk file is absent, so launch runs `python -m corpus` first.

**Named refusals.** Failures are kinds, not a generic 500:

| Kind | What the user sees | Retry? |
|---|---|---|
| `challenge` | Robot check — try again in a few minutes | **No** — more requests extend it |
| `maintenance` | Offline for maintenance — try later | **No** — planned outage |
| `rate_limit` | Rate-limiting — wait a few seconds | After `Retry-After` |
| `timeout` / `unreachable` / `transport` | Could not reach the card database | Brief wait, then retry |

Boot probes `card_source` and shows a note on the landing page; `ready` stays
true so saved decks still open. Build returns the same kinds and says plainly
that building needs live Scryfall. A fresh corpus does not mean building will
work, because every `otag:` search still goes to the live API.

Standing rules: **do not retry harder**, prefer local bulk data, and never score
a failed lookup as zero cards. Do not silently descend a role ladder on a
refusal (a fallback is not a match).

---

## 3. Updates and releases

Pulling on launch is off by default. With `DECKWRIGHT_PULL_ON_LAUNCH=1`,
`run.sh` calls `scripts/pull_on_launch.sh`, which fast-forwards only, skips a
dirty tree, and never blocks startup.

| Mode | When | Behaviour |
|---|---|---|
| **Pinned** | `DECKWRIGHT_RELEASE_REF=<tag>` is set | Fetch that tag and check it out detached. |
| **Follow branch** | Ref unset | `git pull --ff-only` on the current branch. |

`DECKWRIGHT_SKIP_PULL=1` skips the pull entirely.

---

## Standing rules, in one place

- Verify claims, including your own and including a green suite.
- A SKIP fails the tier. Red tier 1 does not merge.
- Never weaken a verifier, a core prompt, `ROLES`, or `validate_deck` as a side effect.
- Unknown ≠ zero. Fallback ≠ match.
- Secrets stay out of the repository.
- Do not retry a refusing dependency harder.
- Local-only by default.
