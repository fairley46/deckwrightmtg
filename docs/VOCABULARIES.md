# Two playstyle vocabularies, and why they stay apart

Decision: keep both vocabularies, named apart.

Deckwright carries two things both called "playstyle." They are not redundant
and **they must not be merged.** They answer different questions, come from
different sources, and drive different machinery. Anyone who collapses them will
silently break either the curve or the plan.

## `brief.playstyle` — how fast she wants to act

| | |
|---|---|
| **Values** | `proactive` · `reactive` · `grindy` |
| **Source** | The interrogation. Her words about tempo, inferred by `infer_playstyle(brief)`. |
| **Consumes** | Nothing — it is an input. |
| **Drives** | `PLAYSTYLE_SHIFT` in `app.py`, which shifts the `ROLES` skeleton: a reactive deck wants more interaction, a grindy one more card advantage. |
| **Question it answers** | *How many of each kind of card?* |

## `brief.way_to_play` — what the deck actually does

| | |
|---|---|
| **Values** | Ids from `data/playstyles.json` — `aristocrats` · `blink` · `tokens` · `voltron` · `stax` · `counters` · `tribal` · `spellslinger` · `landfall` · `reanimator` · `lifegain` · `artifacts` · `group_hug` |
| **Source** | The commander KB (`ways_to_play`), chosen by her on the commander card (#173 / the ways-to-play work). Community research and the mechanics detector, method recorded. |
| **Consumes** | The chosen commander. |
| **Drives** | `plan_graph.derive` → plan commitments, and through them which cards the build recalls and scores. Switching it measurably changes the 99. |
| **Question it answers** | *Which cards, and why those?* |

## Why merging them breaks things

They are orthogonal. `proactive` + `aristocrats` is a real deck. So is
`grindy` + `aristocrats` — same engine, different speed, different role counts.
Collapse the two and you lose one axis: either every aristocrats deck gets the
same curve, or tempo silently starts picking the archetype.

The failure would be quiet. Nothing would error; the decks would just stop
differing along one axis, which is exactly the class of bug the archetype-axis fix took a
week to find — a dead axis that passed every test because the tests only
exercised the region where it worked.

## Naming

The collision is historical: `playstyle` was named before the KB existed. We
document it rather than rename, because `brief.playstyle` is in saved decks on
disk and `playstyles.json` is the vocabulary file both the detector and the
picker read. Renaming either would be a migration, not a cleanup.

When writing about them, say **tempo** for `brief.playstyle` and **way to play**
for `brief.way_to_play`. The UI already says "Ways to play" on the commander
card and never shows the word `playstyle` to her.
