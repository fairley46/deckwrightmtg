# The scoring contract — what "good" means

> Part of the derivation chain — see [`DERIVATION.md`](DERIVATION.md) for how this stage joins the others.

**Consumed by:** the perception fixtures, build selection (payoff pools), and
the plan graph.

Every number in this file was **measured**, not chosen. The measurement shell is stated so
the numbers can be reproduced and argued with. Where perception cannot yet see a role, this
file says so instead of inventing a target.

---

## The finding that changes how plans are written

A condition's **threshold** decides which kind of card is better. Not the card. The threshold.

Measured on a fixed shell (36 Swamps, 6 Sol Ring, filler to 99, `cmdr_cmc=3`, 600 runs,
seed 138), comparing **8 × Phyrexian Arena** (a 3-mana engine, 1 card per upkeep) against
**8 × Night's Whisper** (a 2-mana one-shot, 2 cards now):

| condition | one-shots @T6 | engines @T6 | one-shots @T10 | engines @T10 | engine edge @T10 |
|---|---|---|---|---|---|
| `cards_drawn >= 2` | **58.2%** | 42.0% | **71.0%** | 64.0% | **−7.0 pts** |
| `cards_drawn >= 4` | 26.2% | 6.7% | 38.8% | **58.2%** | **+19.4 pts** |
| `cards_drawn >= 6` | 8.8% | 1.2% | 16.3% | **50.2%** | **+33.9 pts** |
| `cards_drawn >= 8` | 2.8% | 0.0% | 6.5% | **31.7%** | **+25.2 pts** |

Read the first row carefully: **at a threshold of 2, one-shots beat engines.** That is not a
simulator bug. It is correct. "Draw two extra cards" is a bar a 2-mana sorcery clears the turn
it is cast, and a 3-mana upkeep engine needs two turns after resolving to match. Cheap and
immediate genuinely is better *for that goal*.

The crossover sits between 2 and 4.

### What follows from it

**A plan condition with a lazy threshold will systematically prefer the wrong cards.** Write
`cards_drawn >= 2` into a plan graph and the scorer will rank Night's Whisper over Rhystic
Study and be right to, given what it was asked. The card is not mis-scored; the plan is
mis-stated.

So the contract's first rule is about plans, not cards:

> **A condition threshold must be set high enough that only the intended kind of card can
> reach it.** If a one-shot clears the bar in a single cast, the condition is measuring
> "did I play a card", not "is my engine online".

Practical floors, from the table above:
- **Draw conditions: threshold ≥ 4.** Below that, one-shots dominate and the condition
  cannot distinguish an engine deck from a pile of cantrips.
- **Prefer ≥ 6 when the plan is genuinely about card advantage.** That is where the engine
  edge is largest (+33.9 pts) and where the ranking is least ambiguous.

**And `by_turn` must clear the setup cost.** At `>= 4`, one-shots lead at T6 (26.2% vs 6.7%)
and engines overtake by T10 (58.2% vs 38.8%). Judge that condition at turn 6 and you conclude
the opposite of judging it at turn 10. A `by_turn` earlier than the engine's cast-plus-tick
time measures speed, not the plan.

---

## Expected values — what a good deck looks like

These are **acceptance bands for the perception fixtures**, on the shell described above.
They are direction-and-margin, deliberately: absolute percentages move with land count,
commander cost, and run count, and a fixture asserting `56.5%` would be asserting the shell.

### Draw (perception has landed)

Condition `cards_drawn >= 6`, measured at T10:

| deck | measured | band | verdict |
|---|---|---|---|
| bare shell, no draw | 0.0% | `== 0` | must be exactly zero — a floor test for the sim itself |
| 8 one-shots | 16.3% | `10–25%` | real but weak; this is what "some card draw" looks like |
| 8 engines | 50.2% | `≥ 45%` | this is what a draw plan looks like |
| 12 engines | — | `> 8 engines` | density must monotonically help |

**The gate:** engines must beat one-shots by **≥ 20 points** at T10 on a `>= 6` condition.
Measured margin is 33.9, so 20 is a floor with real headroom, not a number tuned to pass.

### Lifegain and drain (perception landed)

Shell: 36 Swamps, 6 Sol Ring, filler to 99, `cmdr_cmc=3`, 600 runs, seed 121.
Enabler = castable `{B}` "You gain 3 life." Engine = Blood Artist (whenever-proxy
gain/drain 1 per turn). Blank = non-life enchantment.

#### `life_gained` — crossover between 3 and 6

| condition | 8 enablers @T10 | 8 Artist @T10 | engine edge @T10 |
|---|---|---|---|
| `life_gained >= 1` | **77.2%** | 72.3% | **−4.9 pts** |
| `life_gained >= 3` | **77.2%** | 66.2% | **−11.0 pts** |
| `life_gained >= 6` | 36.8% | **54.8%** | **+18.0 pts** |
| `life_gained >= 9` | 11.2% | **27.3%** | **+16.1 pts** |

Same lesson as draw: a lazy threshold prefers Healing-Salve-class one-shots.
**Practical floor: `life_gained >= 6`.** Below that the condition cannot tell a
lifegain engine from a pile of Salves.

**The gate:** engines beat enablers by **≥ 15 points** at T10 on `>= 6`
(measured 18.0 — floor with headroom; life margins are tighter than draw's 33.9).

| deck | measured | band | verdict |
|---|---|---|---|
| bare shell, no life | 0.0% | `== 0` | floor test |
| 8 enablers @ `>= 6` | 36.8% | `25–45%` | real but weak |
| 8 Artist @ `>= 6` | 54.8% | `≥ 50%` | lifegain plan online |
| 12 Artist | — | `> 8` | density monotonic |

#### `life_drained` — Artist / Bond vs blank

| deck | `life_drained >= 4` @T10 | notes |
|---|---|---|
| bare / blank | **0.0%** | exact zero |
| 4 Artist | 37.0% | |
| 8 Artist | 61.2% | |
| 12 Artist | 75.2% | monotonic |
| 8 Sanguine Bond | 43.3% | drain proxy; needs gain in real games |

**The gate:** drain engines beat a blank enchantment by **≥ 20 points** at T10 on
`life_drained >= 4`. Lifelink alone (Nighthawk) extracts `drain=0` — not a drain plan.

Sulfuric Vortex extracts all life fields as 0 (negation ≠ gain).

### Tokens (perception landed)

Shell: 36 Mountains, 6 Sol Ring, enchantment filler to 99, `cmdr_cmc=3`,
600 runs, seed 122. Engine = Krenko (`{T}: Create X` goblin tokens). One-shot =
sorcery "Create three 1/1 … creature tokens." Treasure Map = resource tokens only.

#### `creatures_in_play` — crossover between 2 and 4

| condition | 8 create-3 @T10 | 8 Krenko @T10 | engine edge @T10 |
|---|---|---|---|
| `creatures_in_play >= 2` | **72.2%** | 67.8% | **−4.4 pts** |
| `creatures_in_play >= 4` | 33.0% | **63.8%** | **+30.8 pts** |
| `creatures_in_play >= 6` | 33.0% | **60.2%** | **+27.2 pts** |
| `creatures_in_play >= 8` | 8.7% | **60.2%** | **+51.5 pts** |

*(remeasure: create-3 cells moved 0.1–0.2 pts from the original
shell run — within 600-run noise. Gates and floors unchanged.)*

Same lesson as draw/life: a lazy threshold prefers one-shot token bursts.
**Practical floor: `creatures_in_play >= 4`.** Below that the condition cannot
tell a token engine from a pile of Secure-the-Wastes-class spells.

**The gate:** engines beat one-shots by **≥ 20 points** at T10 on `>= 4`
(measured 30.6 — floor with headroom). At `>= 8` the margin is larger (51.4)
and Treasure-only / blank shells score **exactly 0.0%**.

| deck | `creatures_in_play >= 8` @T10 | band | verdict |
|---|---|---|---|
| bare / Treasure Map package | **0.0%** | `== 0` | resources are not bodies |
| 8 create-3 one-shots | 8.7% | `< 15%` | bursts rarely stack to 8 |
| 8 Krenko | 60.2% | `≥ 55%` | go-wide plan online |
| 12 Krenko | 75.7% | `> 8` | density monotonic |

Extraction: Treasure/Clue/Food → `resource_tokens` (bodies stay 0). Creature-token
creates → `creature_tokens` + bodies (one-shot) or `{T}`/whenever proxy (engine).
`Create X` floors to 1 for rate; Krenko-class uses `creature_token_x_proxy`
(each turn: create ≈ current bodies).

Wide-vs-tall (`board_power` disagreeing with `creatures_in_play`) remains a
**deck** property for the plan graph, not a per-card taxonomy lookup.

### Graveyard / flashback (perception landed)

Shell: 36 Mountains, 6 Sol Ring, enchantment filler to 99, `cmdr_cmc=3`,
600 runs, seed 123. Engine = Faithless Looting (`{R}`, draw 2 + flashback
draw 2). One-shot = sorcery `{R}` "Draw two cards." with no flashback
(same CMC as Looting — a `{1}{R}` one-shot understates the table by ~3 pts).

**No new `THESIS_KEYS`.** Delirium type-counts would need a new, reviewed `THESIS_KEY` first.
Flashback's second cast feeds existing `cards_drawn`. Extraction rates
(`flashback`, `self_mill`, `gy_return`, `delirium`) are eyes for ranking.

#### `cards_drawn` via flashback — crossover between 2 and 4

| condition | 8 draw-2 @T10 | 8 Looting @T10 | engine edge @T10 |
|---|---|---|---|
| `cards_drawn >= 2` | **73.2%** | 73.2% | **0.0 pts** |
| `cards_drawn >= 4` | 41.7% | **71.7%** | **+30.0 pts** |
| `cards_drawn >= 6` | 15.7% | **45.5%** | **+29.8 pts** |
| `cards_drawn >= 8` | 5.5% | **43.8%** | **+38.3 pts** |

At `>= 2` both fire from the first cast — flashback is invisible. **Practical
floor for a flashback plan: `cards_drawn >= 4`.**

**The gate:** flashback engines beat one-shot draw-2 by **≥ 20 points** at T10
on `>= 4` (measured 30.0). Bare shells score **exact 0.0%**.

| deck | `cards_drawn >= 4` @T10 | band | verdict |
|---|---|---|---|
| bare shell | **0.0%** | `== 0` | floor |
| 8 draw-2 one-shots | 41.7% | `35–50%` | first cast only |
| 8 Faithless Looting | 71.7% | `≥ 65%` | yard casts again |
| 12 Looting | 86.3% | `> 8` | density monotonic |

Extraction: `flashback` / `flashback_draws` / `self_mill` / `gy_return` /
`delirium`. `gy_return` goldfish-proxies +1 body when cast. Delirium is a rate
for eyes only until a graveyard-type `THESIS_KEY` is added.

## Rules that hold regardless of role

**1. Direction and margin, never a target percentage.** A fixture asserting `50.2%` breaks
when the shell changes and teaches nothing when it fails. Assert "engines beat one-shots by
≥ 20 points at T10". That survives a land-count change and names the actual claim.

**2. Zero must mean zero.** A deck with no cards serving a condition must score exactly 0.0%,
not "low". This is the cheapest available check that perception is wired to the condition at
all, and it is the one that would have caught a signal extracted but never fed into `state`.

**3. Monotonicity.** More of a good thing must not score worse. 12 engines > 8 engines > 4.
Cheap to assert, and it catches saturation bugs that direction tests miss.

**4. Saturation is not a score.** `card_score.py` already skips scoring above 90% or below
10% headroom. Keep that. A condition every card satisfies ranks nothing.

**5. The margin must exceed run noise.** At 600 runs, turn-to-turn wobble is a couple of
points. Any asserted margin should be ≥ 10 points, or raise the run count. A 3-point
"difference" is a coin flip wearing a lab coat.

---

## What this file does not decide

- **A single card score.** There isn't one. A card's value is its lift on *this plan's*
  conditions, and the same card is excellent or worthless depending on the threshold — which
  is the whole finding above.
- **A coherence number.** Still deliberately absent.
- **Anything the player sees.** Percentages are machinery. The player gets "adding this raises 'engine
  online by turn 6' from 61% to 68%" — a sentence, with the number as evidence rather than
  as the point.

---

## Reproducing the table

```python
# 36 Swamps + 6 Sol Ring + N payload + filler to 99; cmdr_cmc=3, runs=600, seed=138
cond = [{"label": "Drew 6", "note": "", "check": {"cards_drawn": 6}}]
out  = sim.run(deck, 3, set(), cond, runs=600, seed=138)
pts  = next(c["points"] for c in out["curves"] if c["label"] == "Drew 6")
# pts is 0-indexed by turn; pts[9] is turn 10
```

**One caveat worth keeping.** An early version of this measurement used Divination (`{2}{U}`)
as the one-shot in a mono-black shell. It scored 0.0% and looked like proof that one-shot
draw was invisible to the simulator. It was uncastable. The sim was right and the measurement
was wrong — which is the ordinary case, and the reason every number here names its shell.

---

## Forge calibration

Every table above was measured against `sim.py` (goldfish). The scoring-math QC confirmed they
reproduce. The Forge calibration asked Forge whether they are true. Data:
[`calibration/`](calibration/). Harness:
`scripts/calibrate_forge.py`. Only `forge_log.MEASURABLE` keys
(`life_gained`, `life_drained`, `lands_in_play`) can be compared; everything in
`NOT_MEASURABLE` is skipped, not zeroed.

### What moved (Forge-corrected)

| change | detail |
|---|---|
| Activated land-search `fetch=0` | Wayfarer's Bauble / Burnished Hart were counted as on-cast ramp. Goldfish has no activation step — that invented a free land. Drove `lands_in_play >= 6` @T6 **+12 pts** optimistic vs Forge (Blood Rites, n=50). `SIM_VERSION` 8. |
| `forge_log.cumulative` `life_drained` | Was life *you* lost (combat damage taken). Now life *opponents* lost — matches THESIS_KEYS. Still includes combat damage the goldfish does not model. |

### What was confirmed against Forge, n=50

Blood Rites (Clavileño) vs Ahoy Mateys; hero-turn clock; after SIM_VERSION 8:

| claim | result |
|---|---|
| `lands_in_play >= 5` @T6/T10 | **confirmed** (Δ +5.5 / −3.5) |
| mean lands @T6 | **confirmed** (sim 4.93 vs Forge 4.90) |
| `lands_in_play >= 6` @T10 among games that reached T10 | **confirmed** (87.5 vs 85.7) — unconditional T10 gap is early game-ends |
| `life_gained >= 6` @T10 among survivors | **confirmed** (43.8 vs 44.3) |

### What stays goldfish-internal (silence ≠ validation)

| claim | why untouched |
|---|---|
| Draw / token / flashback tables | `cards_drawn` / `creatures_in_play` are NOT_MEASURABLE from a Forge log |
| `life_drained` gate (`>= 4`, ≥20 pts over blank) | Forge signal includes combat; +60 pts at T6 is expected, not a drain-proxy bug. **Not Forge-validated.** |
| Absolute % bands on the Artist / Krenko / Looting shells | Those shells were not the Forge hero this pass; margins stay the scoring-math QC self-consistent |
| `by_turn` / T10 judgment horizon | Reasoned, not remeasured as a new number. Forge note: only 16/50 games reached hero-turn 10 under aggro — T10 bands are a goldfish horizon |
