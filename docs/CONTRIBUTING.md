# Contributing

## Start here

1. The one rule: the model writes Scryfall *queries*, never card names from memory. The app runs those queries, verifies every returned name against the Scryfall response, and drops inventions. Preserve the verifier in `/api/commanders`, `/api/build`, and `/api/thesis`.
2. House rule: an unknown is not a zero, and a fallback is not a match.
3. Skim the [documentation map](README.md), then [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md).

## Verify before review

```bash
python scripts/check_all.py --tier 1
```

Register each new root `tests/test_*.py` in `_UNIT_COMMANDS` or `_UNIT_EXCLUDED` in `scripts/check_all.py`, or tier 1 fails.

Also useful:

```bash
python -m deckwright.check pirate UB
python tests/test_deckwright.py --unit
```

Leave the sacred core alone unless a change explicitly owns the tradeoff: the three verifiers, the `INTERROGATOR` and `THESIS` prompts, the `ROLES` skeleton and budget policy, and what `sim.py` models (as opposed to an extraction fix against a stated expected value).

How work becomes a change: [OPERATING-MODEL.md](OPERATING-MODEL.md).
