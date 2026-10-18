# The plan graph — schema and contract

> Part of the derivation chain — see [`DERIVATION.md`](DERIVATION.md) for how this stage joins the others.

**Consumed by:** the deterministic derivation pass, build selection (payoff pools),
scoring, the suggest path, and `/api/thesis` → goldfish
(`plan_graph.thesis_from_plan` — conditions measured on the 99; model thesis
only when no measurable plan exists).

---

## What a plan is, and what it is not

A plan is **the set of things that must be true for this deck to be doing the thing the player
pictured, and by when.**

It is not a theme. "Elves" is a theme; "eight or more mana available by turn five, with a
payoff on board" is a plan. Themes are unfalsifiable and plans are not, which is the whole
point — a plan can be measured, and therefore a card can be judged against it.

Two jobs, and the second is the one people forget:

1. **Selection.** Which cards measurably advance these conditions.
2. **Protection.** What a later suggestion is *not allowed to break*. "Make it faster" has
   a dumb answer — cut everything expensive — and a right one: cut the expensive cards
   that do not serve the plan. Without a plan the suggest path cannot tell those apart, and
   "improve my deck" quietly turns a lifegain deck into a goblin deck.

## The binding constraint

**Every condition must be expressible in `sim.THESIS_KEYS`.** If the simulator cannot
measure it, it is not a plan condition — it is a wish, and it will silently never be
checked.

Current vocabulary (`sim.THESIS_KEYS` — 12 keys):

```
commander_in_play      bool
mana_available         >= n
payoffs_in_play        >= n
extra_land_drops       >= n
lands_in_play          >= n
land_drops_this_turn   >= n
cards_in_hand          >= n
cards_drawn            >= n     (beyond the once-per-turn draw)
creatures_in_play      >= n     (tokens included)
board_power            >= n     (bodies scaled by anthems)
life_gained            >= n
life_drained           >= n
```

Adding a key means computing it in the `state` dict in `play_one()` **and** bumping
`SIM_VERSION`. A plan node that wants a key which does not exist yet is a card for the
perception queue, not something to fake with an adjacent key.

## Shape

```jsonc
{
  "version": 1,
  "derived_from": {
    "commander": "Mister Negative",
    "partner": null,
    "brief_fields": ["strategy", "mechanics", "playstyle"],
    "sim_version": 2
  },

  // What has to be true, and by when. Ordered by when they should land.
  "conditions": [
    {
      "id": "commander_out",
      "label": "Commander is out",           // shown to the player, in their language
      "why": "Nothing else in the plan works until he has resolved and stuck.",
      "check": { "commander_in_play": true },
      "by_turn": 6,
      "weight": "core"                        // core | supporting | nice
    },
    {
      "id": "engine_online",
      "label": "Two payoffs online",
      "why": "One payoff is a card. Two is an engine.",
      "check": { "payoffs_in_play": 2 },
      "by_turn": 5,
      "weight": "core"
    }
  ],

  // What the deck must keep doing. A cut that empties one of these is refused.
  "commitments": [
    {
      "id": "lifegain_density",
      "label": "enough lifegain to turn on the payoffs",
      "counts": { "role": "theme", "min": 10 },
      "why": "The payoffs are dead cards without triggers to feed them."
    }
  ],

  // Cards named by the commander's own text or the brief's mechanics. Never cut
  // by a suggestion without saying so explicitly.
  "load_bearing": ["Mister Negative"],

  "theme": {
    "label": "Spider-Man villains",           // the player's words, not ours
    "source": "brief.theme",
    "binding": false                          // flavour never overrides legality or the plan
  }
}
```

### Field notes

**`weight`** is the only lever the scoring contract needs from this file. `core`
conditions are what the deck IS; `supporting` conditions make them more likely; `nice` is
texture. A suggestion may trade a `nice` for a `core` and never the reverse.

**`by_turn`** is a target, not a promise. The simulation reports what actually happens; this
records what the plan was *aiming* at, which is what makes "your deck is slower than it
means to be" a statement instead of an opinion.

**`commitments` vs `conditions`.** A condition is a board state at a turn. A commitment is a
property of the 99 — a count that must not fall below a floor. Conditions answer *does the
plan happen*; commitments answer *is this still the same deck*. The suggest path reads
commitments; the simulator reads conditions.

**`theme.binding: false` is deliberate.** Flavour is why the player wants the deck and it must
never be allowed to make the deck illegal or incoherent. A Spider-Man deck that cannot cast
its commander is not a win for theme.

## How it is derived

**Deterministic. No model call.** Two inputs, joined by code:

| input | source | already verified? |
|---|---|---|
| commander mechanics | oracle text via `sim.mechanics()` | deterministic, no |
| strategy / mechanics / playstyle | `brief` — model-produced at interrogation | yes, downstream |

A model call here would be a third place the model can invent things, and it could not be
fixture-tested. The brief is already model output that has been through the interrogator;
re-deriving from it in code is free and repeatable.

**Derived twice, and the second one is the real one.** Once loosely from the ask, before a
commander exists — just enough to search commanders with. Then properly once the commander
is chosen, because the commander is what makes a plan possible at all. "Steal things and
make opponents discard" is a wish; Gonti turns it into a plan with turns in it.

## What this file does not decide

- **Magnitudes.** How much a card advancing a condition is worth is the scoring contract.
- **A coherence score.** Deliberately absent. Two axes — synergy plan and fantasy theme —
  is the right model, but a number invites the app to print it, and "coherence: 72%" is a
  claim we cannot defend. The axes rank and explain; they do not score.
- **Anything the player sees.** The plan graph is machinery. The player says "feels slow" and gets three
  specific cards and a reason. Count inside, meaning outside.

## The test that matters

Same one as everywhere else in this effort: **can a plan tell two cards apart?**

Given a plan with `engine_online: payoffs_in_play >= 2 by turn 5`, adding a repeatable
payoff must raise that condition's probability more than adding a one-shot. That is
`test_perception.py`'s condition-lift shape, applied to a plan rather than a bare deck.

If a plan cannot do that, it is a document, not a graph.
