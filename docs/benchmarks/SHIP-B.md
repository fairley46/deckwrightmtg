# Ship Candidate B — validated tip

**Tip:** `box/gate4b-integration-validated` @ `69df005` (includes `#311` the dual-floor and LLM-timeout fix Bello dual floors)  
**Ship branch:** `cursor/con-321-ship-b-3a49` (tip + merge of `69df005`; do not force-push `box/`)  
**Open draft:** https://github.com/fairley46/deckwright/compare/main...cursor/con-321-ship-b-3a49?expand=1  


## What Box proved on this tip

| Check | Result |
|---|---|
| Bello floor arm (`mv_geq_4`) | The dual-floor and LLM-timeout fix dual floors on `69df005`; pre-334 baseline was B **8** vs E **4** (see `BELLO-GAP.md`) |
| Staging smoke | `:8001` off = V1 keys only; `:8002` B = clarity + steer stamps; blue shelf clean |
| Isolation | Blue `:8000` untouched; greens badged; zero clarity/steer keys on blue shelf |

**Caveats (carry forward, do not paper over):**

- B still **trails E** on the bound metric (8 vs 4 on Bello).
- Feeder density slipped **0.343 → 0.303** on this commander (n=1); bound arm traded against input arm — do not assume arms compound.

**Deliberately not in this ship:** Candidate C, CardFacts wired into build, flipping production / blue, 24-commander promote decision.

## Merge path (Option A — preferred)

One draft PR: `cursor/con-321-ship-b-3a49` → `main`.

The tip is **0 behind / 16 ahead** of `main` and already contains the Gate 4 product line:

`#298` green sandbox → `#299` Gate 2 clarity → `#301` Gate 4 steer (+ the steer bound fix) → the MV floor bounds floors + coverage → `#304` steer vote stamps.

Stacked drafts `#298` / `#299` / `#301` (and follow-ons `#304` / `#305` / `#307`) are **superseded** by this tip once The maintainer merges the ship PR. Do not merge them separately after Option A lands.

### Option B (only if Brad wants reviewable slices)

Merge in order, then fast-forward or merge the validated tip last:

1. `#298` → `main` (green sandbox)
2. `#299` → `#298` tip (Gate 2 clarity)
3. `#301` → `#299` tip (Gate 4 steer + the steer bound fix)
4. Ship tip `69df005` (floors + coverage + vote stamps + the dual-floor and LLM-timeout fix) onto that line

Harness drafts (`#296` / `#300` / `#302` / `#308`) are a **parallel client track**. They are not ancestors of `69df005` and are not required to put B on green.

## After The maintainer merges to `main` — demo-host deploy (green only)

Do **not** flip blue. Keep `:8001` at `DECKWRIGHT_CANDIDATE=off` unless Brad explicitly says otherwise. Candidate B stays on `:8002`.

```bash
# Blue clone — leave on release tag / production. Do not set candidate flags.
# Green worktree (example paths from OPERATIONS.md):
cd ~/Projects/deckwright-green   # or wherever the green worktree lives
git fetch origin
git checkout main
git pull origin main             # now includes 69df005 content

# Confirm candidate defaults before restart:
#   install-green-launchd.sh    → :8001, candidate=off  (BLUE control)
#   install-green-b-launchd.sh  → :8002, candidate=B

./config/install-green-launchd.sh       # reinstall/refresh KeepAlive if needed
./config/install-green-b-launchd.sh

# Templates carry DECKWRIGHT_LLM_TIMEOUT=600. Reinstall must not
# silently fall back to llm.py's 180s default (that re-breaks Bello).
# Install scripts print the resolved timeout; confirm on the live process:
#   ps eww <pid> | tr ' ' '\n' | grep DECKWRIGHT_LLM_TIMEOUT
# Expect: DECKWRIGHT_LLM_TIMEOUT=600 on both green and green-b.

curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/   # expect 200
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/   # expect 200
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/   # expect 200
```

Smoke after pull:

1. `:8001` build/import — 99 cards, **no** `commander_strategy` / `strategy_audit` / steer stamps.
2. `:8002` build — 99 cards, clarity keys present, `steer=True` when commander reads, per-card `steer_vote` / `bound_ok` / `feeds_input`.
3. Blue shelf (`~/.deckwright`) — zero new clarity/steer keys from greens.
4. Both green processes show `DECKWRIGHT_LLM_TIMEOUT=600`. Hand-edits under `~/Library/LaunchAgents/` alone are not the fix — the repo templates must carry it.

If green's `.venv` was created under an old Python, cold re-test: `rm -rf .venv` then restart green.

**green-c note:** Candidate C's plist lives on the C-wire draft, not on `main` yet. When rebasing/merging #314, carry `DECKWRIGHT_LLM_TIMEOUT=600` into `(local launchd label)` the same way — otherwise `./config/install-green-c-launchd.sh` reintroduces the 180s cliff on `:8003`.

## Conflict notes (tip vs other cursor branches)

| Branch | vs tip (`left` behind / `right` tip-only) | Notes |
|---|---|---|
| `main` | 0 / 16 | Clean Option A |
| `cursor/con-315-green-sandbox-3a49` | 0 / 14 | Ancestor of tip |
| `cursor/con-321-b-clarity-3a49` | 0 / 11 | Ancestor of tip |
| `cursor/con-321-gate4-steer-3a49` | 0 / 6 | Ancestor of tip |
| `cursor/con-319-benchmark-harness-3a49` | 1 / 16 | Parallel harness; not in tip |
| `cursor/con-328-build-timeout-3a49` | 3 / 16 | Parallel harness |
| `cursor/con-321-gate4-harness-3a49` | 7 / 16 | Parallel harness registration |

No merge conflict expected for tip → `main`. Harness branches need their own rebase onto `main` after this ship if Brad wants bake-off client and product on one tree.
