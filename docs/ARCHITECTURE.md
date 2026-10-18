# How DeckWright is put together

One FastAPI process, one HTML file, no build step, no database. Everything below
is a consequence of that.

**Docs map (entry order, where files live):** [README.md](README.md).

**Connection spine (journey, modules, verifiers, persistence):**
**[HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md)**. Read that when you need "how A joins B."

Needs an LLM. Ships with an Anthropic Claude adapter (API key or `claude` CLI);
all model calls go through `deckwright/llm.py`, so another frontier model is one
adapter away. See [OPERATIONS.md](OPERATIONS.md). Contributor rules:
[CONTRIBUTING.md](CONTRIBUTING.md).

---

## The shape

```mermaid
flowchart TB
    subgraph browser["index.html — one file, vanilla JS, no build"]
        chat["interrogation + instrument readback"]
        pick["commander picker · ways · way_preference"]
        deck["deck page · simulation · sliders · the 99 · ask"]
        rules["how to play · precons · calendar"]
    end

    subgraph server["app.py — FastAPI"]
        api["/api/chat · readback · commanders · build · thesis<br>simulate · ask · swap · pressure · precons · saved · cards"]
    end

    subgraph engine["the parts that decide things"]
        resolve["resolve.py · theme_clauses"]
        synergy["synergy.py · playstyles.py · commanders.py"]
        plan["plan_graph.py · curve.py"]
        sim["sim.py<br>3,000 goldfish games"]
        score["card_score.py"]
        mana["mana.py"]
        why["why.py · thesis_check.py"]
    end

    subgraph model["the model"]
        llm["llm.py<br>Anthropic Claude CLI or API"]
    end

    subgraph out["outside"]
        scry["Scryfall<br>every card, price, image"]
        arch["Archidekt<br>precon catalogue"]
        edh["EDHREC<br>popularity"]
        forge["Forge optional<br>real games"]
    end

    subgraph disk["on disk"]
        saved[("~/.deckwright/<br>saved decks + sim baselines")]
        bakes[("data/precons/<br>optional local precon bakes")]
    end

    browser <--> api
    api --> resolve & synergy & plan & sim & score & mana & why
    api --> llm
    api --> scry & arch & edh
    api --> forge
    api <--> saved & bakes
    score --> sim
```

## The one rule

```mermaid
flowchart LR
    A["the model writes<br>a Scryfall <b>query</b>"] --> B["we run it"]
    B --> C["real cards come back"]
    C --> D["the model picks<br>from those cards"]
    D --> E{"is every name<br>in the response?"}
    E -->|yes| F["into the deck"]
    E -->|no| G["dropped, backfilled<br>by EDHREC rank"]
```

**The model never writes a card name from memory.** It writes queries; we run
them; every name it returns is checked against the actual response. This happens
in `/api/commanders`, `/api/build` and `/api/thesis`, and it is the only thing
keeping the output real. If you refactor, keep the verifier in all three.

**An unknown is not a zero.** Empty means "none" or "could not look" — never collapse
a failed look into a real zero.

## A deck, end to end

Detail and module table: [HOW-IT-CONNECTS.md](HOW-IT-CONNECTS.md). Short sequence:

```mermaid
sequenceDiagram
    participant P as player
    participant A as app.py
    participant M as model
    participant S as Scryfall

    P->>A: "I want a goblin deck"
    A->>M: interrogate
    M-->>A: brief · theme, mechanics, playstyle, way_preference
    A->>S: instrument / readback counts
    A->>S: commander query, relaxed down a ladder
    A->>M: write up five candidates from real cards
    M-->>A: five, each with ways + strategy
    P->>A: picks one
    Note over A: strategy + way become the spec
    A->>S: role queries shaped by strategy + playstyle
    A->>M: choose from these real cards prefer glued/readable
    M-->>A: names, all verified against the response
    Note over A: mana base sized from the curve
    A->>M: what would "working" mean? glued payoffs preferred
    M-->>A: conditions + payoff cards from allowlist
    Note over A: 3,000 goldfish games measure exactly those
    A-->>P: the 99, the odds, a shopping list
```

## What decides what

The pipeline has one spine: **the strategy chosen at commander-pick time is the
spec for everything after it.**

| Stage | Reads | Decides |
|---|---|---|
| Interrogation | the player's words | theme, mechanics, playstyle, **way_preference**, table |
| Instrument / readback | theme spans | live Scryfall counts (ok / empty / unknown) |
| Commander | brief + ways | five candidates; soft-prefer mapped way |
| Skeleton | playstyle + curve | role slot counts (`SKELETON_LIVE`) |
| Role queries | strategy, mechanics | which cards are even offered |
| Card choice | pool + glue | which of those make it (prefer readable / glued) |
| Mana base | the finished curve | land count, basics as residue |
| Thesis | strategy + glued payoffs | what "working" means for *this* deck |
| Simulation | thesis | how often it happens, by which turn |
| Ask (incl. saved) | deck + recomputed instrument | improve answers from real candidates |

Colour is not on that list on purpose. **Colour identity is an output of
choosing a commander**, never an input — that is Commander's own rule, and it
runs opposite to every other format.

## Where the bodies are buried

Three failure modes this codebase keeps producing. All three shipped at least
once, and all three passed their tests.

**Silent extraction.** `sim.py` reads rules text with regexes. A miss doesn't
raise, it just scores a card zero and shifts every probability downstream.
Azusa's "play two additional lands" slipped past a regex matching only "an
additional land". When a number looks wrong, check the diagnostics line under
the chart before suspecting the simulation.

**Player words that aren't card words.** "Ramp", "removal", "wipe" describe what
a card does *to a player*; no card prints them. `o:ramp` returns Embercleave.
Anything derived from prose must resolve through `classify()` against Scryfall's
real vocabulary before it becomes a query.

**Wrong meter.** A Keychain API key with no credits, or a launchd PATH that hides
the `claude` CLI, makes every model call look broken. `./run.sh` prefers the CLI, and
`python -m deckwright.check` prints which one is in use.

**A backend with no caller.** Features have landed complete and done nothing
because the frontend never called them. Green tests, merged change, no effect.
Check the call site.

## Deliberately absent

No database, no auth, no Docker, no frontend build, no multi-user. It is a
single-user local app; each of those would add work for users it does not have.

A saved deck is a JSON file. Older ones simply lack newer fields, and the app
checks rather than assuming — which is the entire migration story. Ask recomputes
instrument counts; it does not freeze them as truth on disk.
