# The Scryfall grammar, and how her words become a query

> Part of the derivation chain — see [`DERIVATION.md`](DERIVATION.md) for how this stage joins the others.

**Owner:** Box (the grammar and the precedence). Cursor implements.
Consumed by `role_query()`, `commander_ladder()`, `search_ladder()`, suggest, and any
future path that turns something she said into a search.

---

## The problem, stated once

We have been **guessing which operator a word belongs to.**

`t:pirate` works. `t:ships` returns nothing. `t:spiderman` returns HTTP 404 while the deck's
own commander comes from the Spider-Man set. Every one of these is the same bug wearing a
different hat:

| card | symptom | same disease |
|---|---|---|
| The basic-land padding fix | a theme Scryfall can't search becomes basic lands | guessed `t:` |
| The commander ladder fix | the ladder skips the rung that would have worked | no way to say what the rung is |
| The taxonomy query verification | 63 taxonomy queries named things Scryfall doesn't index | guessed `keyword:` |
| The set-search fix | "spiderman" finds 0 next to a Spider-Man commander | guessed `t:`, never tried `set:` |

Patching them one at a time is hunting in the dark. **The fix is to know the option space.**

## Scryfall publishes the dictionary

Every enumerable value Scryfall will match is available from `/catalog/*`.

> **Cache note (2026-08-08):** committed `data/scryfall_grammar.json` holds **16**
> catalogs / **61,250** values (fetched 2026-08-06). The table below is Scryfall’s
> published shape and may list catalogs we do not currently cache (e.g. flavor-words,
> powers). Prefer the on-disk file for live counts; re-run
> `scripts/refresh_scryfall_grammar.py` after a print wave.

Measured (illustrative / Scryfall-published):

| catalog | values | operator |
|---|---:|---|
| `card-names` | 34,987 | `name:` |
| `word-bank` | 22,894 | `o:` |
| `artist-names` | 2,404 | `a:` |
| `flavor-words` | 686 | `o:` |
| `creature-types` | 339 | `t:` |
| `keyword-abilities` | 220 | `keyword:` |
| `planeswalker-types` | 99 | `t:` |
| `keyword-actions` | 78 | `keyword:` |
| `watermarks` | 76 | `wm:` |
| `ability-words` | 69 | `keyword:` |
| `toughnesses` | 40 | `tou=` |
| `powers` | 36 | `pow=` |
| `artifact-types` | 20 | `t:` |
| `card-types` | 19 | `t:` |
| `land-types` | 18 | `t:` |
| `enchantment-types` | 13 | `t:` |
| `loyalties` | 12 | `loy=` |
| `supertypes` | 7 | `t:` |
| `spell-types` | 6 | `t:` |
| `battle-types` | 1 | `t:` |
| **total** | **62,024** | |

Plus `/sets` — **1,047 sets**, each with a name and a code.

**Resolution is therefore a lookup, not a judgement.** "pirate" is in `creature-types`, so it
is `t:pirate`. "lifelink" is in `keyword-abilities`, so it is `keyword:lifelink`. "spiderman"
is in no catalog but normalises onto a set name, so it is `set:spm`.

## The ambiguity is nine words

The obvious objection is that a word can live in several catalogs. Measured across all type
catalogs, keyword catalogs, watermarks and set names, **exactly 9 terms collide:**

```
conspiracy     card-types, watermarks, set-names
dungeon        planeswalker-types, card-types
food           artifact-types, keyword-actions
foretell       keyword-abilities, watermarks
hero           creature-types, card-types
phyrexian      creature-types, watermarks
planeswalker   card-types, watermarks
transformers   watermarks, set-names
treasure       artifact-types, keyword-actions
```

Nine. That is a **hand-written precedence table, not a heuristic** — and it is the single most
important consequence of doing this measurement instead of guessing. We were about to build
disambiguation machinery for a problem with nine instances.

Ruling for each, by what she would have meant:

| term | resolves as | because |
|---|---|---|
| `treasure`, `food` | `t:` (artifact type) | she means the tokens, not the rules action |
| `conspiracy`, `transformers` | `set:` | nobody asks for a watermark |
| `phyrexian` | `t:` (creature type) | the tribe, not the watermark |
| `hero`, `dungeon`, `planeswalker` | `t:` (card type) | the broader reading is the one she means |
| `foretell` | `keyword:` | the mechanic, not the watermark |

## Two vocabularies, not one — things and behaviours

The catalogs above name **things**: a pirate, an angel, an Equipment, a set. They do not name
**behaviours**: stealing, ramping, removing, tutoring. Those live in Scryfall's community
oracle tags, `otag:`, which is a separate vocabulary with no `/catalog` endpoint.

**"steal" is the proof that these must be kept apart**, and it is a trap the precedence order
below would otherwise walk straight into:

| expression | cards | what it is |
|---|---:|---|
| `keyword:steal` | **1** | Rikku, Resourceful Guardian — a Final Fantasy set keyword |
| `o:steal` | 8 | cards with the literal word |
| `o:"gain control"` | 261 | the effect, phrased one way |
| `otag:theft` | **716** | the concept |

A single precedence table that checks keyword catalogs before free text resolves "steal" to
**one card** and returns it with confidence. That is worse than finding nothing, because
nothing is visible and one wrong card is not.

So term resolution asks two questions, in this order:

1. **Is this a behaviour?** Check `otag:` first. If it resolves, use it.
2. **Is this a thing?** Fall through to the catalogs and sets below.

### Composition is the payoff

Because the two vocabularies are separate, they compose — which is how "pirates that steal"
becomes a real query instead of two half-answers:

```
t:pirate                 180 cards     the thing
otag:theft               716 cards     the behaviour
t:pirate otag:theft       15 cards     what she asked for
```

Those 15 are Admiral Beckett Brass, Breeches, Captivating Crew, Coercive Recruiter, Edward
Kenway — the actual steal-pirates deck. The `o:"gain control"` phrasing finds only 4 of them.

This is also where the mechanic taxonomy meets the grammar: `otag:` supplies the behaviour
term, and `plan_role` says whether that behaviour is an engine, a payoff or an enabler for her
plan. Grammar finds the cards; the math ranks them.

### `otag:` names are not guessable

Probed live — several plausible tags do not exist:

```
716   otag:theft            0   otag:carddraw
6408  otag:removal          0   otag:token-maker
2280  otag:ramp             0   otag:stax
2586  otag:lifegain         0   otag:control-changing
```

A wrong tag returns HTTP 404, which the app reads as an empty result and pads around. So every
`otag:` we emit needs the **same build-time verification as the taxonomy query verification** — probe it once, fail
loudly, cache the ones that work. Build the behaviour vocabulary by verifying candidates, not
by guessing them at query time.

## Precedence for everything else

First match wins. Order chosen so the most specific, most likely-intended reading is reached
first:

```
1.  exact set name (normalised)        -> set:<code>
2.  supertypes / card-types            -> t:
3.  creature/land/artifact/ench/pw/spell/battle types -> t:
4.  keyword-abilities / actions / ability-words -> keyword:
5.  watermarks                         -> wm:
6.  substring match on a set name      -> set:<code>          (largest set wins)
7.  word-bank                          -> o:"<term>"
8.  card-names                         -> name:/<term>/
9.  artist-names                       -> a:"<term>"
```

Set-name **exact** match sits above types deliberately: a set named after a thing is a much
stronger signal than a coincidental type. Set-name **substring** sits below keywords so
"angel" reaches `t:angel` and not some set with Angel in its title.

## Normalisation — her words are not Scryfall's words

She types "spiderman". Scryfall has "Marvel's Spider-Man". Lowercase, strip everything that is
not a letter or digit, then match. Also index each set under its name with a leading licensor
or article removed (`Marvel's `, `The `), so "spiderman" and "lord of the rings" both land.

Verified against the live set list:

| she types | resolves to |
|---|---|
| `spiderman` / `spider-man` / `Spider-Man` | Marvel's Spider-Man (`spm`) |
| `lord of the rings` | The Lord of the Rings: Tales of Middle-earth (`ltr`) |
| `fallout` / `fallouts` | Fallout (`pip`) |
| `hobbit` / `hobbits` | The Hobbit (`hob`) |
| `doctor who` | Doctor Who (`who`) |
| `final fantasy` | Final Fantasy (`fin`) |
| `ninja turtles` | Teenage Mutant Ninja Turtles (`tmt`) |
| `avatar` | Avatar: The Last Airbender (`tla`) |
| `warhammer` | Warhammer 40,000 Commander (`40k`) |
| `pirates` | **no set** — falls through to `t:pirate`, correctly |

That last row is the test that matters. The set rung must not swallow themes that genuinely
are creature types.

## Universes Beyond is first-class

`is:ub` is a real Scryfall predicate — **4,551 cards across 47 sets**. So a UB theme has two
expressions and they are not the same question:

- `set:spm` — *this product*. 286 cards.
- `is:ub` — *all crossover cards*. Use when she says "Universes Beyond" rather than naming one.

The 47 sets are enumerable in 60 paged requests, one time, cacheable. This is the "pull the
list down and convert it" move rather than a per-theme guess.

## The rule that must not be lost

**A term that resolves to nothing must say so.** Never silently degrade to a generic query and
present the result as if it matched. That is the basic-land padding fix's failure and the set-search fix's copy bug, and it
is the same shape as `scry()` returning empty for a throttled request:

> An unknown is not a zero, and a fallback is not a match.

When the grammar cannot place a word, the honest output is *"nothing in Scryfall matches
'heists' — here is what we did instead"*, scoped to the role it affected.

## What this replaces

`role_query()` currently interpolates the theme into hand-written query strings and hopes. It
should instead **resolve the term once** through this grammar, then build role queries around
the resolved expression. One term-resolution function, unit-testable against fixtures, with no
network call after the catalogs are cached.

This does not touch the verifier. Every card still comes back from a real Scryfall response
and every name is still checked against it.

## Caching

Catalogs and the set list change on set release, not daily. Fetch once, cache to `data/`,
refresh on a version stamp. The whole vocabulary is a few hundred KB and turns every future
term resolution into a dictionary hit — no network, no rate limit, no silent empty.
