# How a wish becomes a deck

**QC frame for the stage joins** — falsifiable checks per stage. For the **runtime
connection spine** (which route calls which module, verifiers, frozen vs recomputed)
see [`HOW-IT-CONNECTS.md`](HOW-IT-CONNECTS.md).

The four stages were designed separately; this says how the *ideas* join:

| stage | owns | document |
|---|---|---|
| 1. what the player wants | their words → real cards | [`SCRYFALL-GRAMMAR.md`](SCRYFALL-GRAMMAR.md) |
| 2. what a card *is* | mechanic → role in a plan | `data/mechanic_taxonomy.json` + `mechanic_families.json` |
| 3. what has to be true | plan → measurable conditions | [`PLAN-GRAPH-SCHEMA.md`](PLAN-GRAPH-SCHEMA.md) |
| 4. which card is better | lift on those conditions | [`SCORING-CONTRACT.md`](SCORING-CONTRACT.md) |

Related but different: [`SCRYFALL-CONTRACT.md`](SCRYFALL-CONTRACT.md) is the *card object*
projection — which fields we keep off a Scryfall card. `SCRYFALL-GRAMMAR.md` is the *query*
side. Similar names, unrelated jobs.

---

## The one sentence

**The grammar decides what the player could have. The plan decides what they need. The math decides
which of those two sets overlap best. The graph decides which of them hold together. The curve
decides whether they can actually play them.**

Everything below is that sentence with the joins made explicit.

## The chain

```
  the player's words
     │  INTERROGATOR                      model interprets
     ▼
  brief { theme, strategy, mechanics, playstyle, colors }
     │  term resolution (grammar)         deterministic lookup
     ▼
  query expression: t:pirate otag:theft
     │  Scryfall                          real cards, verified
     ▼
  candidate pool  ─── 15 cards
     │
     │  commander chosen ────────┐
     ▼                           ▼
  plan graph                  taxonomy
  { conditions, commitments }  { plan_role, math_kind }
     │                           │
     └──────────┬────────────────┘
                ▼
         sim measures lift          deterministic
                ▼
         scoring contract           what "good" means
                ▼
              the 99
```

The model may speak at several stages (brief, commander blurbs, role picks, thesis,
overview, ask) — but it **never invents a card name**. It writes queries or chooses from
a provided Scryfall list; verifiers drop inventions. That is what makes the one rule in
CONTRIBUTING.md enforceable rather than aspirational.

## Stage 1 — the player's words become real cards

Two vocabularies, because a word is either a **thing** or a **behaviour**:

```
pirates      thing       t:pirate        180 cards    catalog lookup
that steal   behaviour   otag:theft      716 cards    verified tag list
                         composed         15 cards
```

The composition is where a wish first becomes falsifiable. 15 says this is a real deck. 2 says
it will be thin. Nothing says we did not understand the word — and that must be said, not
padded around.

**This stage cannot fail silently.** It is the entry point, so a wrong answer here corrupts
everything downstream and does it invisibly.

## Stage 2 — the taxonomy says how a card participates

The grammar found 15 cards. It says nothing about what they *do for the plan*. That is the
taxonomy's job, and it is the bridge between "on theme" and "useful":

| field | answers |
|---|---|
| `plan_role` | is this an **engine**, a **payoff**, an **enabler**, a **condition**, **protection**? |
| `math_kind` | what shape of number does it move — `resource_loop`, `board_count`, `life_rate`? |

Coverage today: **207 of 391 mechanics (52%)** carry a `plan_role`. That number is the honest
ceiling on how much of a pool we can currently classify, and it rises with every triage card.

**Why the engine/enabler split is load-bearing** — from `SCORING-CONTRACT.md`, measured:

| condition | enablers @T10 | engines @T10 |
|---|---|---|
| `cards_drawn >= 2` | **71.0%** | 64.0% |
| `cards_drawn >= 6` | 16.3% | **50.2%** |

They are not interchangeable, and which one the player wants depends entirely on where the plan sets
its bar. A taxonomy that only said "these are card draw" would rank them identically and be
wrong half the time.

## Stage 3 — the plan says what has to be true

Derived twice, and the second one is the real one:

1. **Loosely, from the ask** — enough to search commanders with.
2. **Properly, once a commander exists** — because the commander is what makes a plan possible
   at all. "Steal things and make opponents discard" is a wish; Gonti turns it into a plan
   with turns in it.

Two structures, and the second is the one people forget:

| | is | read by |
|---|---|---|
| `conditions` | a board state at a turn — *does the plan happen* | the simulator |
| `commitments` | a floor on the 99 — *is this still the same deck* | suggest |

**Binding constraint:** every condition must be expressible in `sim.THESIS_KEYS`. If the
simulator cannot measure it, it is not a condition — it is a wish that will silently never be
checked.

## Stage 4 — the math picks

`card_score.py` simulates the deck with and without a candidate and measures the change in
each condition's probability. That architecture was always right; it was **blind**, because
`sim.py` could only reliably extract mana.

Fixing perception was measured against a fixed set of card pairs:

```
starting point                    3 of  9 blind
after the target-player draw fix  4 of  9 blind   <- got WORSE, on purpose: a false green was exposed
after graveyard perception        0 of 10 blind   <- with the threshold corrected
```

The middle row matters more than the last. The Arena-vs-Sign-in-Blood pair had been passing by
comparing a card the sim understood against one it could not read at all. Fixing extraction
made the number worse and the machine honest.

### The trap this stage sets for stage 3

A condition's **threshold decides which kind of card wins.** Set `cards_drawn >= 2` and the
scorer will rank a cheap one-shot above an engine — and be right, given what it was asked. The
card is not mis-scored; the plan is mis-stated.

So `SCORING-CONTRACT.md` sets floors (draw: ≥ 4, ≥ 6 when the plan is really about card
advantage), and **stage 3 must respect them**. This is the tightest coupling between any two
stages in the chain, and the only one where a mistake looks like success.

## The rule that runs through all four stages

> **An unknown is not a zero, and a fallback is not a match.**

It appeared independently on four surfaces:

| surface | the lie it prevents |
|---|---|
| `scry()` throttled | "no cards match" when Scryfall just refused |
| unreleased set | "0 cards for your deck" when the set is not spoiled |
| unresolved term | "nothing matched your theme" when we mis-parsed the word |
| unclassified mechanic | "3 engines" read as "only 3 exist" |

Four separate fixes were made for the same defect before anyone noticed it was one defect. **Treat it as
a house rule, not four fixes.** Any layer that returns an empty result must distinguish
*"there are none"* from *"I could not look."*

---

# The bar

**This document is the QC standard for the mechanics/perception effort.** When the work is
done, it is judged against the checks below — not against a green test suite, and not against
whether the deck looks reasonable. Each check is falsifiable and runnable.

A stage passes only if its check passes *and* it does not break the house rule.

## Stage 1 — the grammar

| # | check | how |
|---|---|---|
| 1.1 | Every term the player can say resolves to a real operator or is reported unresolved | fixture set; `pirates`→`t:`, `steal`→`otag:theft`, `spiderman`→`set:spm`, `heists`→unresolved |
| 1.2 | No emitted expression returns zero from live Scryfall | build-time gate, same as the taxonomy query check (394/394) |
| 1.3 | `steal` never resolves to `keyword:steal` | the 1-card trap; behaviour is checked before things |
| 1.4 | A theme that *is* a creature type still reaches `t:` | `pirates` must not be swallowed by a set-name match |

**Fails if:** a wrong query returns nothing and the app pads around it without saying so.

## Stage 2 — the taxonomy

| # | check | how |
|---|---|---|
| 2.1 | Every taxonomy query is live | 394/394 today; regression = build failure |
| 2.2 | `plan_role` coverage is stated, never implied complete | 207/391 (52%) today; the UI must say so |
| 2.3 | A found card can be classified as engine / payoff / enabler | the join taxonomy → family → `plan_role` |

**Fails if:** "3 engines" is read as "only 3 engines exist."

## Stage 3 — the plan

| # | check | how |
|---|---|---|
| 3.1 | Every condition is expressible in `sim.THESIS_KEYS` | parse the graph, assert each `check` key is live |
| 3.2 | Thresholds respect the floors in `SCORING-CONTRACT.md` | draw ≥ 4; others from their measured crossover |
| 3.3 | `by_turn` clears the engine's cast-plus-tick time | else it measures speed, not the plan |
| 3.4 | `commitments` prevent a suggestion emptying the deck's identity | "make it faster" must not cut every expensive card |

**Fails if:** a condition is written that the simulator silently never checks.

## Stage 4 — the math

| # | check | how |
|---|---|---|
| 4.1 | **`test_perception.py` reads 0 blind** | the gate |
| 4.2 | A deck with none of a thing scores exactly `0.0%` | not "low" — exactly zero |
| 4.3 | Density is monotonic | 12 > 8 > 4 |
| 4.4 | Every asserted margin exceeds run noise | ≥ 10 points at 600 runs |
| 4.5 | `SIM_VERSION` bumped whenever perception changed | saved decks recompute rather than lie |

**Fails if:** a pair passes because one card is invisible rather than because the other is
better. That happened tonight and the gate caught it — the number got *worse* and that was the
correct outcome.

## The house rule — checked at every stage

> **An unknown is not a zero, and a fallback is not a match.**

Every layer that can return an empty result must distinguish *"there are none"* from *"I could
not look."* Throttled `scry()`, unreleased sets, unresolved terms, unclassified mechanics —
four cards were cut for this same defect in one session before anyone noticed it was one
defect.

**Any surface that fails this fails QC regardless of its own stage check.**

## The end-to-end test

One case, run whole:

> **"pirates that steal, blue-black, about $150"**

1. resolves to `t:pirate otag:theft id<=UB` — not zero, not padded
2. the readback places both terms with live counts (empty/unknown/thin labelled;
   healthy pool sizes suppressed) and a commander-narrowing beat
   before the player picks
3. the plan's conditions are all in `THESIS_KEYS`, thresholds above the floors
4. the 99 is chosen by measured lift; every model-chosen name passed a verifier
5. every card in the deck came back from a real Scryfall response

If that runs clean end to end, the effort is done. If any stage silently degrades, it is not —
however green the suite looks.

---

## What the player sees

None of this. The player says *"feels slow"* and gets three specific cards and a reason.

The percentages, roles, thresholds and tags are how we reason — not what we show. Count inside,
meaning outside. The one number that reaches the player is the kind they can act on: *"adding this
raises 'engine online by turn 5' from 61% to 68%."*
