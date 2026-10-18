# Status

DeckWright is a local Commander deckbuilding assistant. It runs on your machine via `./run.sh` and is not offered as a hosted service.

## What works today

- Conversation → real commanders → a Scryfall-grounded 99
- A stated plan for the deck, checked with solitaire goldfish simulation
- Saved decks under `~/.deckwright/`, export (text, Archidekt, shopping list), precon browsing (fetched from Archidekt at runtime), and pasted-list import
- An offline test gate: `python scripts/check_all.py --tier 1`

## Known limits

- Building a deck needs the live Scryfall API (see [OPERATING-MODEL.md](OPERATING-MODEL.md#the-external-dependency-when-scryfall-says-no)).
- The simulator plays goldfish games with no opponent, so interaction and opponent-dependent effects are reported as unmodelled.
- Only an Anthropic Claude adapter is included.

See the root [README](../README.md) for quick start, and [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md) for how the pieces join.
