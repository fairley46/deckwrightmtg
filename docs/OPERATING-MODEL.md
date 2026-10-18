# The operating model

How work becomes a change, what stops a bad one, and who can reach this thing.

This is the document a person who was not here reads first. Everything below is
what we actually do, not what we would like to do — where the two differ, it
says so.

Companions: [README.md](README.md) (docs map · where things live) ·
[OPERATIONS.md](OPERATIONS.md) (dependencies, LLM adapter, cadence, sanctity) ·
[HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) (the code spine) ·
[CONTRIBUTING.md](CONTRIBUTING.md) (contributor rules and verify gate).

---

## 1. Who does what

One repo. Preserve the sacred core unless a change explicitly owns the tradeoff:
the three verifiers, the `INTERROGATOR` and `THESIS` prompts, the `ROLES`
skeleton and budget policy, and what `sim.py` models (as opposed to an
extraction fix against a stated expected value).

**The operator decides.** Anything that changes what the product *is*, what it says, or
what reaches the house is his call, not a runner's.

---

## 2. Change management — how work becomes a change

```
observation  →  card  →  branch  →  PR  →  review  →  main  →  the demo host
```

**Observation.** A playtest note, a defect, a measurement. Whatever the source,
it becomes a card before it becomes code — including findings a runner makes
about its own work. A fix that exists only in a commit message is invisible to
the board and unfindable later.

**The card carries the reasoning, not just the ask.** What was observed, what
was measured, what the constraint is, and what "done" means. Cards here are long
on purpose: the runner executing one usually was not present when it was
written.

**Branch and PR.** One card per PR. Cursor opens drafts; Box reviews and merges.

**Review means verification, not reading.** Every serious defect found on
2026-08-09 was green while it existed — sixteen unregistered test files, an
inert `data-axis`, a wordmark that vanished only in Safari, a save path
PATCHing a slug that never existed. A description is a claim; run it.

**Send defects back rather than merging to keep things moving**, and turn each
finding into a card or a card update. A PR thread is not farmable work.

---

## 3. Change control — what stops a bad change

### The gate

`python scripts/check_all.py --tier 1` is authoritative. **A SKIP fails the
tier** — a check that did not run is not a check that passed.

**CI runs that command** on every pull request and every push to `main`
(`.github/workflows/tier1.yml`). A red tier-1 check means the change does not
land.

**Branch protection on `main`:** required status check `check` (this workflow),
`strict: true`, force-pushes and deletions denied. Verified under CON-297 by a
real force-push rejection. **`enforce_admins` is deliberately `false`** — the operator
keeps an emergency hatch to push without the check. That is a household
escape, not a second merge path for ordinary work. Using it to land red Tier 1
reopens the social-gate failure mode on purpose; treat it as break-glass only.

**The operator is the human merge gate.** Draft PR → green Tier 1 → the maintainer merges. They
does not merge red. He does not merge on a description alone — review means
verification (see §2). When CI is unavailable, the same command run locally is
still the bar; absence of a check is not a pass.

Green is necessary and not sufficient. State what a green run does not prove.

### Guards that exist because the gate lied before

| Guard | Catches | Born from |
|---|---|---|
| `test_unit_registry.py` | A test file that exists and never runs | Tier 1 reported "26 files · PASS" while ~170 assertions never executed |
| `scripts/api_boundary.py` | Data emitted and never consumed, keys read and never sent | An inert `data-axis`, alternate images never persisted, a dead `saved_as` |
| `conflict_markers` | Merge markers reaching main | Committed `<<<<<<<` in a shipped file |

Each was written after the failure it now prevents. Adding a guard is the
standard response to a defect that the suite did not catch.

### Sacred — frozen without a named card and a stated tradeoff

Full list and reasoning in
[OPERATIONS.md § Sanctity and freeze](OPERATIONS.md#sanctity-and-freeze).

- The three verifiers (`/api/commanders`, `/api/build`, `/api/thesis`)
- `INTERROGATOR` and `THESIS` prompt text
- `ROLES` and budget policy
- What `sim.py` models

### The two house rules

**The model never writes a card name from memory.** It writes Scryfall
*queries*; every returned name is checked against the real response and
inventions are dropped. This is the only thing keeping output real.

**An unknown is not a zero, and a fallback is not a match.** Any layer that can
return empty must distinguish "there are none" from "I could not look." This one
decays silently and is worth re-checking at every review.

---

## 4. Impact — stating blast radius before it lands

Every PR says what it touches and how far the change reaches. Shared and central
files get named explicitly, because two runners work here and the collisions are
real: three PRs registering a test at the same line, and a masthead touched by
three separate changes in one afternoon.

**Concurrency is a live hazard, not a theoretical one.** On 2026-08-09 a merge
of #269 rewrote main from a base that predated #270, and #270's work — a hero
card, the tilt system, a placard removal — was silently absent from the code
while GitHub reported it merged. Confirm merged work is actually an ancestor of
main. A label is not evidence.

---

## 5. Access — who and what can reach this

| | |
|---|---|
| **Network** | Local by default. `run.sh` binds `127.0.0.1`. Set `HOST=0.0.0.0` only if you intentionally want other devices on your network to reach the app. |
| **People** | Single-user local install. **No auth, no accounts, no multi-user.** |
| **Saved decks** | One shelf under `~/.deckwright/`. |
| **Secrets** | `ANTHROPIC_API_KEY` in a local `.env` (never committed). Never a constant in the repo. |
| **LLM** | Needs an LLM. Ships with Anthropic Claude (API key or `claude` CLI). `run.sh` unsets `ANTHROPIC_API_KEY` when the CLI is present so calls cannot silently bill the API. |

### The external dependency — when Scryfall says no

Scryfall is the only runtime dependency that can take the product down. On
2026-08-09 it did twice — a Cloudflare challenge caused by our own request
volume, then Scryfall's own maintenance window.

**Corpus-first (CON-294) protects what she reads, not what she builds
(CON-303).** Card lookups serve from `~/.deckwright/oracle_cards.jsonl` first.
That is real resilience for named lookup, ordinary `q=` search, saved decks,
landing, and precons. It is **not** resilience for `/api/build`.

Role ladders lean on `otag:` (Tagger function tags). Those fields are not in
Scryfall bulk data, so `corpus.needs_live()` sends every one of them to the
live client by design — unsupported ≠ empty. Function tags are also the
correct index for roles (CON-113); that choice stands. The honest survival
table:

| Path | Survives Scryfall being down |
|---|---|
| Named lookup, ordinary `q=` search | **Yes** — local corpus when loaded |
| Opening a saved deck | **Yes** |
| Landing, precons, local pages | **Yes** |
| **Building a deck** | **No** — role ladders need `otag:` |

Source map (same fact, by query class):

| Path | Source |
|---|---|
| Ordinary `q=` search / named / collection | Local corpus when loaded |
| `otag:` / `art:` alone (Tagger — not in bulk) | Live only — **this is the build path** |
| Composite `t:… otag:…` (CON-308) | Local conjuncts from corpus; live for `otag:`/`art:`. Partial = incomplete, not a smaller match |
| Query the local index cannot parse | Live only |
| Images / set catalog / bulk refresh | Live |

A warm corpus and a stopped build on the same evening are not a contradiction.
The product path still needs live Scryfall until tag membership is cached
locally (backlog under CON-303 / option 1). Until then: say so at build time —
saved decks still open; a new build waits on the card source.

A missing corpus falls through to live unless `DECKWRIGHT_CORPUS=local-only`,
which raises unknown rather than inventing empty. Unsupported ≠ empty.

**Boot must fetch a missing corpus.** Grammar/KB can be fresh while
`oracle_cards.jsonl` was never downloaded — that left every lookup on the live
API (CON-296 re-sign-off). `boot._should_refresh()` is true when the bulk file
is absent, so launch cadence runs `python -m corpus` before anything else.
Sunday cadence still refreshes it weekly; first install no longer waits for Sunday.

**Named refusals (CON-295 / CON-296).** Failures are kinds, not a generic 500:

| Kind | What she sees | Retry? |
|---|---|---|
| `challenge` | Robot check — try again in a few minutes | **No** — more requests extend it |
| `maintenance` | Offline for maintenance — try later | **No** — planned outage |
| `rate_limit` | Rate-limiting — wait a few seconds | After `Retry-After` |
| `timeout` / `unreachable` / `transport` | Could not reach the card database | Brief wait, then retry |

Boot probes `card_source` and paints the note on landing; `ready` stays true so
saved decks still open. Build returns the same kinds and says plainly that
building needs live Scryfall. Do not read a green corpus status as "she can
build" — corpus age 0 still leaves every `otag:` rung on the live client.

Standing rules: **do not retry harder**, prefer local bulk, and never score a
failed look as zero cards. Do not silently descend a role ladder on refusal
(fallback is not a match).

---

## 6. Clarity — could someone else run this

The test is whether a person who was not here can operate it from the docs
alone. Read in this order:

1. [../README.md](../README.md). What it is, how to run it, the one rule
2. [README.md](README.md). Docs map and where files live
3. [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md). The spine, end to end
4. [ARCHITECTURE.md](ARCHITECTURE.md). Shape diagrams
5. **This file**. How work moves and what stops it
6. [OPERATIONS.md](OPERATIONS.md). Dependencies, meter, cadence, sanctity
7. . What the tag certifies
8. [CONTRIBUTING.md](CONTRIBUTING.md) / [CONTRIBUTING.md](CONTRIBUTING.md). Rules and the verify gate

Code carries its reasoning in comments: what was tried, what failed, and why the
shipped version is the one that is here. That is deliberate. Most of the traps in
this codebase are invisible from the code alone — the CSS comment explaining why
tone alone failed a squint test is worth more than the rule it sits above.

`docs/` holds 243 completed issues, archived out of Linear to
stay inside the free-plan limit. It is the ledger behind the board.

---

## 7. Release — how code reaches the house

**Settled (CON-296): the shared install tracks a release tag, not live `main`.**

`run.sh` → `scripts/pull_on_launch.sh` updates code on every launch (including
KeepAlive restarts). The script is careful: ff-only / fetch+checkout, skips a
dirty tree, skips without git, never blocks startup.

| Mode | When | Behaviour |
|---|---|---|
| **Release** | `DECKWRIGHT_RELEASE_REF=<tag>` set on the demo host (LaunchAgent env) | Fetch that tag and check it out detached. What she opens is a deliberate cut. |
| **Dev default** | Ref unset | `git pull --ff-only` on the current branch. Every push is a deploy — fine for Box, not for the shared install product channel. |

**GA / household procedure**

1. Tier 1 green on the tip you intend to ship — confirm the **commit SHA** has
   a green `check` run on GitHub (or a local `python scripts/check_all.py
   --tier 1` you just ran on that tree). The tag workflow does **not** enforce
   this; the human does (CON-297 residual).
2. Tag it: `git tag -a ga-YYYYMMDD -m "…" && git push origin ga-YYYYMMDD`.
3. **Cut a branch at the same commit and push it:**
   `git branch release/ga-YYYYMMDD ga-YYYYMMDD^{commit} && git push origin release/ga-YYYYMMDD`.
   Not optional. Review tooling checks out **branches**, so a tag alone makes
   the shipped artifact unreadable by the reviewer that gates it — CON-297's
   first session died on `Branch ga-20260810 does not exist` and reviewed
   nothing. While the tag equals `main` the gap is invisible; once `main`
   drifts, "review `main`" and "review what ships" are different questions and
   only the branch can answer the second.
4. Set `DECKWRIGHT_RELEASE_REF=ga-YYYYMMDD` on the demo host LaunchAgent (or export
   it before `./run.sh`), then restart so the pull checks out the tag.
5. Bump the ref only when you mean to ship again.

The tag is what the house runs; the branch is what a reviewer can read. Cut
both or the release is unreviewable.

Skip the pull entirely with `DECKWRIGHT_SKIP_PULL=1`. Work continues on `main`;
a release is a deliberate act. That is what change *control* means.

---

## Standing rules, in one place

- One card, one PR, Linear ID in the branch and the commit.
- Send defects back; turn findings into cards.
- Verify claims — including your own, and including a green suite.
- A SKIP fails the tier. Red Tier 1 does not merge.
- Never weaken a verifier, a sacred prompt, `ROLES`, or `validate_deck`.
- Unknown ≠ zero. Fallback ≠ match.
- Secrets live in Keychain.
- Do not retry a refusing dependency harder.
- The install tracks `DECKWRIGHT_RELEASE_REF` (a tag), not tip `main`.
- Local-only by default. A hostname leaves the local network only behind Cloudflare Access, and only when the operator runs local run.
