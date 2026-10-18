# B vs CardFacts — what C changes

Offline compare of a **Candidate B** stamped build against **CardFacts**
extract. Contestant **C** is flag-gated on green-c `:8003`
(`DECKWRIGHT_CANDIDATE=C`) — not the default. BLUE / B paths unchanged.

B tip validated: `box/gate4b-integration-validated` @ `e44e92d`
(Bello BLUE 17 → B 8 violations; steer removed cheap rocks).

CardFacts tip: bake pipeline + ready harden
(`scripts/bake_cardfacts.py`, `cardfacts.extract` / `lookup`).
Serve wiring: `serve_report.py` behind candidate C.

## Demo host — run against a real Bello B deck

On the demo host (local corpus + green B build JSON):

```bash
source .venv/bin/activate

# 1) Build Bello on green B (:8002) and save the JSON, or copy a stamped
#    /api/build response that includes per-card steer_vote / bound_ok /
#    Feeds_input (stamp). Example path:
#    ~/Downloads/bello_b_build.json

# 2) Bake CardFacts for those names (full corpus preferred):
python scripts/bake_cardfacts.py --names "$(
  python -c 'import json,sys; d=json.load(open(sys.argv[1]));
print(",".join(c["name"] for c in d["deck"]))' ~/Downloads/bello_b_build.json
)"

# 3) Compare:
python scripts/compare_b_vs_cardfacts.py \
  --deck ~/Downloads/bello_b_build.json \
  --json-out /tmp/bello_b_vs_cf.json
```

If the CardFacts store is cold and you only want the deck slice:

```bash
python scripts/compare_b_vs_cardfacts.py \
  --deck ~/Downloads/bello_b_build.json \
  --bake-deck \
  --json-out /tmp/bello_b_vs_cf.json
```

`--bake-deck` without `--jsonl` reads `~/.deckwright/oracle_cards.jsonl`.
Missing corpus → exit 2 **UNKNOWN** (not an empty agree/disagree table).

## Fixture (cloud / offline)

No real Bello B artifact is in-repo. Use the stamped demo-host deck:

```bash
source .venv/bin/activate
python scripts/compare_b_vs_cardfacts.py \
  --deck testdata/benchmarks/bello_b_fixture.json \
  --bake-deck \
  --jsonl testdata/benchmarks/bello_b_fixture_cards.jsonl \
  --json-out /tmp/bello_fixture_b_vs_cf.json
```

**Re-run on the demo host with a real B build JSON** before reading Gate 5 numbers off
this fixture. The fixture is ~15 names (cheap rocks + on-plan MV≥4 + filler)
stamped under `DECKWRIGHT_CANDIDATE=B` from the validated B tip — shape check
only.

## Sample output shape

Human table (stdout):

```
commander='Bello, Bard of the Brambles' deck=15 cf_store=ok strategy=ok
B violations: 4  CF preview violations: 4  CF unknown cards: 0
  B∩CF agree: 4  B vs CF disagree: 0  CF-only flags: 0
feeders B=0 CF_preview=4 both=0 b_only=0 cf_only=4
--- per card ---
Sol Ring: B vote=-1 bound=false feed=False | CF status=partial preview_bound=false preview_feed=False outputs=[mana]
Esika's Chariot: B vote=0 bound=ok feed=False | CF status=… preview_bound=ok preview_feed=True outputs=[…]
…
```

JSON (`--json-out`) keys:

| Key | Meaning |
|---|---|
| `summary.b_violations` | Cards B stamped `bound_ok=false` |
| `summary.b_violations_cf_agrees` | B violation ∩ CF preview `false` |
| `summary.b_violations_cf_disagrees` | B violation ∩ CF preview `ok` |
| `summary.b_violations_cf_unknown` | B violation but CF missing/unread capacity — **not** scored as agree |
| `summary.cf_flags_b_did_not` | CF preview violation that B did not stamp false |
| `summary.feeders` | `b` / `cf_preview` / `both` / `b_only` / `cf_only` |
| `cards[].b` | `steer_vote`, `bound_ok`, `feeds_input` when present |
| `cards[].cardfacts` | extract shell + `preview_bound_ok` / `preview_feeds_input` |

House rule: missing store or missing card → `status: "unknown"`,
`preview_* = null`. Never treated as “0 violations agreed.”

## What C wiring does (flag-gated)

See [`docs/product/C-REMAINING.md`](../product/C-REMAINING.md):

1. **Bake** full corpus CardFacts (already scriptable; preferred before C builds).
2. **Wire lookup** into audit/steer behind `DECKWRIGHT_CANDIDATE=C` —
   ServeReport fields from Layer-1 facts + Layer-2 strategy (`serve_report.py`).
3. **Harness** registers contestant C on `:8003` (`DECKWRIGHT_GREEN_C_URL`).
4. **Bake-off** `E,BLUE,B,C` and **UI** per-concept ServeReport — still open.

Until you install green-c, B’s validated steer is the live selection path on
`:8002`. CardFacts explain *why* B was right on Bello’s MV floor and where C
can still diverge (richer feeders via `trigger.supplier` / CF outputs,
concept weights). Selection on C is opt-in only.
