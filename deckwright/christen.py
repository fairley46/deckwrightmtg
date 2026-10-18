"""
christen.py — naming the deck and writing its story.

Everything else in Deckwright is about being correct. This is the part that
makes someone love the thing they built.

It runs *after* the simulation, and the placement is the point. Before the
numbers she doesn't know what she has. After them she does — she has just
watched the plan come together 65.9% of the time by turn five — and that is
the moment a pile of cards becomes a deck she has feelings about. Asked
earlier it is a form field; asked here it is a small ceremony.

Nothing here is imposed. Three names are offered because choosing between
options is easy and inventing one from nothing is not, and every one of them
is editable. The primer arrives as a draft for the same reason: a blank
textarea is intimidating and stays blank, while a draft she can rewrite is an
invitation. It is hers the second it appears.

## The one rule still applies

A primer may describe what the deck does. It may not name a card that isn't in
the 99 — `verify()` strips any that slip through. The model is writing flavour
here, which is exactly where it is most tempted to reach for a card it
remembers rather than one we fetched.
"""

import json
import re

# Roughly 20–45, playing a game they either love already or are just meeting.
# The register is Magic's own — the flavour text on the cards, not marketing.
CHRISTEN = """You are naming a Commander deck the player just built, and writing
the note they'd post about it.

You get FACTS: the commander, the strategy they chose, the deck's colours, what it
does, and the odds the plan comes together. Use them. A deck about angels gaining
life and a deck about stealing creatures should not get names from the same bag.

## The names

Offer three. They should sound like something a person named their own deck —
players do this, and the good ones are evocative and a little wry, never a product
name. Draw on the commander's own flavour and what the deck actually does.

## The theme

One line. What this deck is, said the way she'd say it when someone at the table
asks what she's playing.

## The primer

Three or four sentences, first person, the way someone writes about a deck they
like on a forum. What it wants to do, what a good game feels like, the turn it's
playing toward. Warm and specific. This is a draft she will rewrite — write it as
a real person would, not as a brochure.

## Tone — get this right

- Evocative and a little theatrical, in Magic's register: high fantasy, gothic,
  adventure. The game's own flavour text is the reference.
- Never childish. No exclamation marks doing the work, no "epic", no winking.
- Never edgy. Kids read over shoulders and play at the same tables. No gore, no
  cruelty for laughs, nothing crude. Menace is fine — Magic is full of it — but it
  is the menace of a story, not of a person.
- Never boastful on her behalf. The deck may be described as dangerous; she must
  not be told she is about to crush her friends. She might lose.

If a name or a primer would embarrass someone reading it aloud at a table, it is
wrong. Write it again.

Never name a card that is not in the decklist you were given.

Return only json:
{"names": ["...", "...", "..."],
 "theme": "one line",
 "primer": "three or four sentences, first person"}"""


def facts(commander: str, brief: dict, deck: list, overview: dict,
          curves: list | None = None) -> str:
    """The grounding block. Only things we actually know."""
    roles = {}
    for c in deck:
        roles[c.get("role", "?")] = roles.get(c.get("role", "?"), 0) + 1
    lines = [
        f"COMMANDER: {commander}",
        f"COLOURS: {''.join(brief.get('identity') or []) or 'colourless'}",
        f"THEME THEY ASKED FOR: {brief.get('theme') or 'unstated'}",
        f"STRATEGY THEY CHOSE: {brief.get('strategy') or brief.get('angle') or 'unstated'}",
        f"DECK SHAPE: " + ", ".join(f"{k} {v}" for k, v in roles.items()),
    ]
    if overview.get("how_it_works"):
        lines.append(f"HOW IT WORKS: {overview['how_it_works']}")
    for c in (curves or [])[:4]:
        lines.append(f"ODDS: {c.get('label')} — {c.get('points', [0]*5)[4]}% by turn 5")
    lines.append("CARDS IN THE DECK: " + ", ".join(c["name"] for c in deck))
    return "\n".join(lines)


# Two or more capitalised words in a row — the shape a card name takes in prose.
# Sentence-initial capitals are excluded by requiring the run to be internal or
# to survive the stoplist below.
_TITLE_RUN = re.compile(r"\b([A-Z][\w'’\-]+(?:\s+(?:of|the|to|and|in|a)\s+|\s+)"
                        r"[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)*)")

# Phrases that look like titles and aren't. Kept deliberately short: a longer
# list would start hiding real misses.
_NOT_CARDS = {"a good game", "commander", "magic"}


def unverified_cards(text: str, deck_names: set[str], commander: str) -> list[str]:
    """Capitalised phrases in the primer that are not cards in this deck.

    The primer is flavour writing, which is where the model is most tempted to
    reach for a card it remembers rather than one we fetched.

    This **reports**, it does not rewrite. Excising a phrase from the middle of
    a sentence leaves worse prose than the problem it fixes, and this heuristic
    will occasionally flag a turn of phrase that was never a card name. So the
    caller decides: the endpoint surfaces the list, and a primer that names
    something we cannot vouch for can be dropped whole rather than edited.
    """
    allowed = {n.lower() for n in deck_names} | {commander.lower()}
    out = []
    for m in _TITLE_RUN.findall(text or ""):
        cand = m.strip(" ,.")
        low = cand.lower()
        if low in _NOT_CARDS or low in allowed:
            continue
        # A partial match is fine — models shorten "Lyra Dawnbringer" to "Lyra".
        if any(low in n or n in low for n in allowed):
            continue
        if cand not in out:
            out.append(cand)
    return out


def verify(result: dict, deck_names: set[str], commander: str) -> dict:
    """Normalise the fields and flag anything we can't vouch for."""
    result["names"] = [n.strip() for n in (result.get("names") or [])
                       if isinstance(n, str) and n.strip()][:3]
    result["theme"] = (result.get("theme") or "").strip()
    primer = (result.get("primer") or "").strip()
    suspect = unverified_cards(primer, deck_names, commander)
    if suspect:
        # The one rule wins over the nice writing. A primer that names a card
        # she cannot buy is worse than a blank box she fills in herself.
        result["primer"] = ""
        result["dropped_primer"] = suspect
    else:
        result["primer"] = primer
    return result


async def christen(client, model: str, commander: str, brief: dict, deck: list,
                   overview: dict, curves: list | None, parse_json) -> dict:
    """Three names, a theme line, and a primer draft. Never fatal."""
    r = await client.messages.create(
        model=model, max_tokens=1200, system=CHRISTEN,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content":
                   facts(commander, brief, deck, overview, curves)}])
    out = parse_json(r, "christening", expect="object") or {}
    if not isinstance(out, dict):
        out = {}
    return verify(out, {c["name"] for c in deck}, commander)
