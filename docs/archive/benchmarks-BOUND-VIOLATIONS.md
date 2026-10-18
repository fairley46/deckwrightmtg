# Bound violations: ramp vs payoff

Gate C bake-off showed the raw `violation_count` column is not rankable:
cheap mana rocks count as MV-floor violations on Bello even though steer
correctly demotes them as load-bearing ramp.

## Fix

`strategy_audit.audit()` now tags each MV violation with `kind`:

| kind | meaning |
|---|---|
| `ramp` | geq MV floor on a card that produces mana (tap-Add or sim `produces`) |
| `payoff` | everything else |

New fields on the audit dict:

- `bound_violations_payoff` / `violation_count_payoff` — use for bake-off ranking
- `bound_violations_ramp` / `violation_count_ramp` — informational; steer still demotes

Steer and `bound_ok` per card are unchanged. This only splits the audit column
so harness reports do not conflate ramp with wrong payoffs.
