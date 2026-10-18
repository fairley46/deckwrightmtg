# Changelog

All notable changes to DeckWright are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-17

First public release.

### Added

- **Conversation to deck.** Describe a Commander deck in plain English; the app
  asks follow-up questions until it can search for the theme, then reads back
  what it understood, term by term, with the Scryfall search behind each one.
- **Real commanders.** Five legal commanders that fit the brief, with colours,
  themes, and ways to play, paging for more, and partner pairs where the rules
  allow.
- **A Scryfall-grounded 99.** Built role by role (lands, ramp, draw, removal,
  plan, theme, deep cuts). Every card comes from a Scryfall search result; names
  the model suggests that are not in those results are dropped.
- **A stated plan, checked by simulation.** A short thesis names the cards that
  carry the plan, and solitaire goldfish games estimate how often it comes
  together and by which turn. What the simulator cannot model is called out.
- **Deck page and editing.** Commander, legality, price, plan, odds, and the 99
  by role; ask questions about the deck, stage swaps from the same verified card
  pools, approve or reject them, and keep notes and versions.
- **Precons and imports.** Browse preconstructed Commander decks (fetched from
  Archidekt's public API and cached) or paste a list you own, with suggested
  upgrades.
- **Export.** Plain text, Archidekt import, and a shopping list.
- **Local by design.** One FastAPI process and one HTML page; decks are saved
  under `~/.deckwright/`; binds to `127.0.0.1` by default.
- **Offline test gate.** `python scripts/check_all.py --tier 1`, run in CI on
  every pull request.
- Project metadata in `pyproject.toml`, with the version kept in
  `deckwright/__init__.py`.
- Contributor docs: code of conduct, security policy, issue and pull request
  templates, CODEOWNERS, Dependabot configuration, and `.editorconfig`.

### Security

- Saved-deck and precon ids are validated, and their file paths must resolve
  inside the data directory; anything else is treated as not found.
- API error responses return fixed messages and error codes instead of raw
  exception text; full errors go to the server log.
- Regular expressions that read pasted decklists, themes, and card text were
  rewritten to avoid heavy backtracking on hostile input, with the same results
  on real input.
- Regular expressions that could backtrack heavily on crafted input were
  rewritten so matching time stays linear.
- The CI workflow token is limited to read-only repository contents.

[Unreleased]: https://github.com/fairley46/deckwrightmtg/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/fairley46/deckwrightmtg/releases/tag/v1.0.0
