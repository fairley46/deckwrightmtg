# Bello gap — B 8 vs E 4 (CON-334)

Source measurement: `GATE5B-BELLO-20260825T003009Z.md` on tip
`box/gate4b-integration-validated` @ `e44e92d`. Harness metric: `mv_geq_4` on
`bello_permanent` (non-Equipment artifacts and non-Aura enchantments).

| contestant | violations | feeder density |
|---|---|---|
| E | 4 | 0.192 |
| BLUE | 17 | 0.343 |
| B | 8 | 0.303 |

Build lists for that run live under `docs/benchmarks/runs/20260825T003009Z`
(gitignored). They are not in this repo checkout. Classification below uses the
frozen EDHREC fixture (same matcher) plus extractor/steer behaviour on tip.

## What E still ships (fixture, Scryfall-enriched)

Recomputed against the frozen E list + harness `bello_permanent` matcher
(counts can be 4 or 5 depending on filter/unknowns; shape is stable):

| card | MV | type | class |
|---|---|---|---|
| Garruk's Uprising | 3 | Enchantment | **ok-to-differ** — EDHREC keeps a cheap non-Aura enchantment |
| Sanctum Weaver | 2 | Enchantment Creature | **ok-to-differ** — same |
| Destiny Spinner | 2 | Enchantment Creature | **ok-to-differ** — same |
| Weaver of Harmony | 2 | Enchantment Creature | **ok-to-differ** — same |
| Burnished Hart | 3 | Artifact Creature | **ok-to-differ** — cheap animate-shaped ramp EDHREC keeps |

These are real harness violations. E is tighter than a perfect-zero floor on
purpose: the average list still runs a few sub-4 enchantment pieces. **Do not
fake B to match E's 4.**

## Why B landed at 8 (pre-CON-334)

Bello's oracle:

> each **non-Equipment artifact** and **non-Aura enchantment** you control with
> mana value 4 or greater …

On `e44e92d`, `derive` emitted a single `artifact_mv geq 4` bound (enchantment
collapsed away; Equipment not excluded). Consequences:

1. **True miss / coverage gap (enchantments)** — cheap non-Aura enchantments
   were invisible to steer/audit. Harness still counted them. That alone keeps
   B above E even after rocks are cleared.
2. **Bello exception (Equipment)** — sub-4 Equipment was incorrectly
   `bound_ok=false`. Harness exempts Equipment; product demotion was a false
   bound. That is the bound-vs-feeder tug on this commander: false demotions
   displace list slots without helping the harness bound column.
3. **Harness–E mismatch** — not the issue. E and B are graded by the same
   frozen matcher. The gap is product coverage vs EDHREC taste.
4. **E is tighter** — the leftover 4–5 on E are intentional EDHREC inclusions,
   mostly cheap enchantments. Closing B→0 would overfit.

Nine cards B *did* correctly remove (from the GATE5B note): Commander's Sphere,
Everflowing Chalice, Mind Stone, Ichor Wellspring, Herd Heirloom, Klothys,
Anax, Azusa's Many Journeys, Jugan Defends the Temple.

## CON-334 code response (this branch)

- Extract dual floors + `excludes: equipment|aura` from non-X phrasing
  (`commander_strategy`).
- Honour excludes and `enchantment_mv` in `strategy_audit` / steer.
- `strategy_steer.cmp_votes`: unknown bound sorts as neutral; feeder (+1) beats
  unknown/neutral in `card_score` (caveat 2). Hard `bound_ok=false` still
  demotes.

This narrows the *false* part of the gap. Remaining B>E on a future Bello run
is **E is tighter**, not a fake match target.

## Feeder density 0.343 → 0.303

Harness feeders are `animate_targets` / `bello_permanent`. Strategy inputs on
Bello extract as `combat_damage`. Bound demotion of false Equipment (and
replacement by non-animate cards) traded against the harness feeder column.
Caveat 2 keeps real feeders ranked above unknown/neutral once bounds are
honest; it does not invent animate-target inputs.
