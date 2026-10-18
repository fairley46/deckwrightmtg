# Gate C — Candidate C dual instance

Flag-gated CardFacts ServeReport + CF-informed steer. **Not the default.**
Do not promote B. Do not flip blue. Do not set `DECKWRIGHT_CANDIDATE=C` on
`:8001` (BLUE) or `:8000` (production).

## Ports / env

| Contestant | URL | Env | Candidate |
|---|---|---|---|
| BLUE | `http://127.0.0.1:8001` | `DECKWRIGHT_GREEN_URL` | `off` |
| B | `http://127.0.0.1:8002` | `DECKWRIGHT_GREEN_B_URL` | `B` |
| C | `http://127.0.0.1:8003` | `DECKWRIGHT_GREEN_C_URL` | `C` |

Registry: `data/benchmarks/contestants.json` (C is `http_build` → `:8003`).

## Install (green worktree)

```bash
./config/install-green-launchd.sh          # :8001 off
./config/install-green-b-launchd.sh        # :8002 B
./config/install-green-c-launchd.sh        # :8003 C

curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/
```

Data: `~/.deckwright-green-c/`. Corpus: shared read-only `~/.deckwright`.
Bake CardFacts into the C home (or shared corpus dir as configured) before
expecting CF-informed stamps:

```bash
DECKWRIGHT_HOME=~/.deckwright-green-c python scripts/bake_cardfacts.py
```

## Smoke

```bash
curl -sS -X POST http://127.0.0.1:8003/api/build \
  -H 'Content-Type: application/json' \
  -d '{"commander":"Bello, Bard of the Brambles","brief":{"theme":"artifacts","budget_total":150}}' \
  | python -c "import sys,json; d=json.load(sys.stdin); c=d['deck'][0]; print('cards',len(d.get('deck',[])),'steer',d.get('strategy_audit',{}).get('steer'),'serve', 'serve_report' in c)"
```

Expect `serve_report` on cards when C is deployed and the commander reads.
Missing CardFacts → `serve_report.cardfacts_status == "unknown"` (not zero).

## Bake-off

Full `E,BLUE,B,C` is a separate Gate run — not part of this wiring PR.
Harness checkout needs `--green-c-url` scoped like the per-contestant URL scoping's `--green-b-url`
when that branch merges onto a tip that has this registry.

**Prereqs:** the dual-floor and LLM-timeout fix (`DECKWRIGHT_LLM_TIMEOUT=600` on all green plists, including
this green-c template) and the MV floor bounds (bound coverage SEEN=6). See
`docs/benchmarks/BAKEOFF-READY.md` / `docs/product/C-REMAINING.md`. Do not pass
`--green-url` on multi-contestant sweeps.

See `docs/product/C-REMAINING.md`.
