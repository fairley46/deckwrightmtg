# Security policy

## Supported versions

Security fixes go into the latest release and `main`.

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| Older commits | No, please update |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
**[Report a vulnerability](https://github.com/fairley46/deckwrightmtg/security/advisories/new)**
(Security tab → Advisories → Report a vulnerability).

Do not open a public issue for a security problem. Helpful details:

- what an attacker can do, and what they need first
- steps or a request that reproduces it
- the version or commit you tested

Reports are reviewed as soon as possible; there is no fixed response time. Fixes
are credited in the advisory and the changelog unless you would rather stay anonymous.

## Scope

DeckWright is a local app: it binds to `127.0.0.1` by default, has no accounts,
and stores decks under `~/.deckwright/`. In scope:

- the FastAPI server in `deckwright/` and the page in `index.html`
- anything that lets a request read or write files outside the data directory
- leaking secrets such as `ANTHROPIC_API_KEY` or the contents of `.env`
- injection through pasted decklists, deck names, or card data

Out of scope: problems that need an attacker who already controls your machine,
and exposure you opt into by setting `HOST=0.0.0.0` on an untrusted network.
CodeQL code scanning runs on every pull request, and Dependabot checks dependencies weekly.
