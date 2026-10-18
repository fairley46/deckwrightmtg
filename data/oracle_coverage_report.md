# Oracle coverage report

Measured `2026-10-11` on Scryfall bulk `oracle_cards` (2026-10-10T21:01:55.638+00:00).

- Commander-legal universe: **32,116**
- Graded (looked): **32,116**
- Unknown (could not look): **0**
- `oracle_signals` loaded: **106**

## Coverage numbers

| metric | before | after | Δ |
|---|---:|---:|---:|
| at least one signal fires | 66.6% (21,200) | 73.6% (23,642) | 7.0 pp |
| signal OR keyword catalog | 80.5% (25,612) | 85.1% (27323) | ? pp |
| any `consumes` | 24.9% (7,918) | 28.9% (9,273) | 4.0 pp |
| playstyles precision | 89.9% | 89.9% | +0.0 pp |

Unread (no signal and no keyword): 4,793 (14.9%). Unknown is not unread.

## Signals shipped this card

- `grant_keyword_eot` — 1,669 commander-legal hits
- `tap_target` — 337 commander-legal hits
- `exile_target_creature` — 240 commander-legal hits
- `choose_creature_type` — 91 commander-legal hits
- `destroy_target_land` — 86 commander-legal hits
- `stays_tapped` — 278 commander-legal hits
- `activated_ping` — 93 commander-legal hits

CON-252 lap 3 mid-tail. Precision 89.9% held.

## Remaining unread tail (no signal and no keyword)

```
     12×  this creature can't attack unless defending player controls an island
      9×  cast this spell only during the declare attackers step and only if you've been attacked this step
      8×  draft this card face up
      7×  {M}, {M}: put a charge counter on this artifact
      6×  at the beginning of your upkeep, you may put a verse counter on this enchantment
      5×  destroy target attacking creature
      5×  when this creature becomes the target of a spell or ability, sacrifice it
      5×  change the target of target spell with a single target
      5×  destroy target tapped creature
      5×  counter target creature spell
      5×  exile target artifact or enchantment
      5×  you may have this creature assign its combat damage as though it weren't blocked
      5×  {M}: untap target land
      5×  {M}, remove any number of charge counters from this artifact: add {M}, then add an additional {M} for each charge counter removed this way
      5×  target creature deals damage to itself equal to its power
      4×  as an additional cost to cast this spell, pay x life
      4×  destroy all nonland permanents
      4×  all creatures able to block this creature do so
      4×  as an additional cost to cast this spell, sacrifice a land
      4×  target creature's owner puts it on their choice of the top or bottom of their library
      4×  you may play an additional land on each of your turns
      4×  {M}: you may put a land card from your hand onto the battlefield
      4×  whenever this creature becomes blocked, you may have it deal damage equal to its power to target creature. if you do, this creature assigns no combat damage this turn
      4×  take an extra turn after this one
      4×  during your turn, this creature has first strike
      4×  target creature you control deals damage equal to its power to target creature an opponent controls
      4×  each player can't cast more than one spell each turn
      4×  creatures your opponents control get -1/-0
      4×  sacrifice this creature: destroy target enchantment
      4×  skip your draw step
      4×  destroy target artifact or enchantment
      4×  when you control no islands, sacrifice this creature
      4×  this creature can't attack or block alone
      4×  sacrifice this creature: add {M}{M}
      4×  destroy all enchantments
      4×  at the beginning of the end step, if no creatures are on the battlefield, sacrifice this enchantment
      4×  you may cast this spell as though it had flash if you pay {M} more to cast it
      3×  search your library for a basic land card, put it onto the battlefield tapped, then shuffle
      3×  you have shroud
      3×  look at the top three cards of your library. put one of them into your hand and the rest into your graveyard
```

## Re-run

```
python scripts/oracle_coverage.py --write data/oracle_coverage_report.json
python scripts/check_playstyles.py   # precision must not drop
```
