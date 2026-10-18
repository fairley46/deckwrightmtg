# EDHREC-seeded build

**Status:** V2 green product on `:8004`. Blue `:8000` stays V1 until the
promotion PR in [`SHIP-V2.md`](SHIP-V2.md).  
**Supersedes:** candidate B/C selection (measured; neither wins).

## Split

EDHREC knows which cards belong. V1 knows how to make them castable. Seed
from the crowd average deck; V1 still owns mana, curve, budget, collection,
and legality.

## Ports (do not break the harness)

| Port | Role | Flags |
|------|------|-------|
| `:8000` | Blue production | V1, no seed until promotion |
| `:8001` | **BLUE contestant** | `CANDIDATE=off`, **no** `EDHREC_SEED` |
| `:8002` | B | stopped (plist on disk) |
| `:8003` | C | stopped (plist on disk) |
| `:8004` | **V2 green product** | `CANDIDATE=off`, `EDHREC_SEED=1` |

Putting seed on `:8001` would silently change the frozen BLUE baseline and
invalidate every prior sweep (same class as the per-contestant URL scoping).

## Implementation

| Piece | Where |
|---|---|
| Thin live client (~2 GETs) | `edhrec_seed.py` |
| Pool injection before model shortlist | `app.py` `/api/build` |
| Provenance + inclusion on deck page | `index.html` |
| Green-d instance | `(local launchd label)` + `install-green-d-launchd.sh` |
| Harness contestant D | `data/benchmarks/contestants.json` |
| Ship gate | [`SHIP-V2.md`](SHIP-V2.md) |

## Deploy (demo host — green worktree)

```bash
cd ~/Projects/deckwright-green
git pull origin main

# BLUE baseline — reinstall only if plist changed; must stay seed-free
./config/install-green-launchd.sh          # :8001 V1 (BLUE)

# EDHREC seed product path
./config/install-green-d-launchd.sh          # :8004 seed

curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/   # BLUE
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8004/   # seed

PID=$(lsof -t -iTCP:8001 -sTCP:LISTEN | head -1)
ps eww "$PID" | tr ' ' '\n' | grep DECKWRIGHT_EDHREC_SEED || echo ':8001 has no seed (good)'

PID=$(lsof -t -iTCP:8004 -sTCP:LISTEN | head -1)
ps eww "$PID" | tr ' ' '\n' | grep DECKWRIGHT_EDHREC_SEED
# Expect DECKWRIGHT_EDHREC_SEED=1
```

Build on **`http://127.0.0.1:8004/`** for seeded decks. Use `:8001` only as the
harness V1 control — not for product testing seed.

## Page honesty (thin data)

Inclusion % and rising flags paint **only when `status=seeded`**. A thin
commander still shows the provenance line ("Not enough people…") but card rows
carry no percentage — 1.4% of 139 decks is two decks, not a finding.

## Outage probe

```bash
# Force unreachable copy on green-d (restart after):
# In green-d plist EnvironmentVariables, or one-shot:
DECKWRIGHT_EDHREC_BASE=http://127.0.0.1:9 \
  DECKWRIGHT_EDHREC_SEED=1 DECKWRIGHT_ENV=green \
  python -c "import asyncio, edhrec_seed as e; print(asyncio.run(e.load_for_commander('Krenko, Mob Boss')).status)"
# Expect: unreachable
```

## Fallback copy

| state | line |
|---|---|
| seeded | Built from what people play, tuned so you can cast it. |
| thin_data | Not enough people have played this commander — built from the card text. |
| unreachable | Couldn't reach EDHREC — built from the card text. |
