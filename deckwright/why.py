"""
why.py — why is this card in my deck?

The honest answer was already in the app and being thrown away. Every card in
the 99 got there because a specific Scryfall query returned it, and
`search_ladder()` reports which rung of the ladder landed. That query *is* the
reason. Recording it turns "why is this here" from something a model would have
to invent into something we can simply state.

So there is no model in this file either. A generated rationale for 99 cards
would be 99 chances to write something plausible and wrong, at the one moment
the player is deciding whether to trust the list. Reading back the query we
actually ran costs nothing and cannot be wrong about what happened.

## What it cannot tell you

It tells you why the card was *offered*, not why it was *chosen* — the model
picked from the pool the query returned. For a `theme` card those are close
enough to the same thing. For `spot` removal the honest answer is "the deck
needs removal and this is removal", which the role heading already says, so
those cards get no line at all rather than a hollow one.
"""

import re

# Boilerplate every role query carries. None of it is a reason.
_NOISE = re.compile(
    r"""\b(?:id<=\w+|f:commander|is:commander|usd<=[\d.]+|
        -is:\w+|legal:\w+|-t:basic|game:paper)\b""",
    re.X | re.I)

_QUOTED = r'"([^"]+)"|([^\s]+)'


def _term(raw: str) -> str:
    return (raw or "").strip('"').replace("-", " ")


def explain_group(query: str, role: str) -> str:
    """Why a whole role's cards were offered, phrased for the group.

    Same provenance as `explain()` — the query we actually ran — but said once
    about a set rather than once per card. This is the answer to "why these
    cards", and until now the app knew it and only whispered it on the few rows
    where it was not structural.
    """
    if not query:
        return ""
    q = _NOISE.sub(" ", query).strip()

    m = re.search(r"\bt:(?:%s)" % _QUOTED, q)
    if m:
        term = _term(m.group(1) or m.group(2))
        if term not in ("land", "creature", "instant", "sorcery", "artifact", "enchantment"):
            return f"Searched for {term}s \u2014 your theme."

    m = re.search(r"\bkeyword:(?:%s)" % _QUOTED, q)
    if m:
        return f"Searched for cards with {_term(m.group(1) or m.group(2))}."

    m = re.search(r"\botag:(?:%s)" % _QUOTED, q)
    if m:
        return f"Community-tagged as {_term(m.group(1) or m.group(2))}."

    for m in re.finditer(r"\bo:(?:%s)" % _QUOTED, q):
        t = _term(m.group(1) or m.group(2))
        if len(t) > 3:
            return f'Searched their rules text for \u201c{t}\u201d.'
    return ""


def explain(query: str, role: str) -> str:
    """One clause of a Scryfall query, in English. Empty when there's nothing
    worth saying — a card with no honest reason gets no line."""
    if not query:
        return ""
    q = _NOISE.sub(" ", query).strip()

    m = re.search(r"\bt:(?:%s)" % _QUOTED, q)
    if m:
        term = _term(m.group(1) or m.group(2))
        if term in ("land", "creature", "instant", "sorcery", "artifact", "enchantment"):
            return ""                      # structural, not a reason
        return f"It's {'an' if term[:1] in 'aeiou' else 'a'} {term} — your theme."

    m = re.search(r"\bkeyword:(?:%s)" % _QUOTED, q)
    if m:
        return f"It has {_term(m.group(1) or m.group(2))}, which is what you asked for."

    m = re.search(r"\botag:(?:%s)" % _QUOTED, q)
    if m:
        return f"Tagged by the community as {_term(m.group(1) or m.group(2))}."

    m = re.search(r"\bo:(?:%s)" % _QUOTED, q)
    if m:
        return f'Its rules text says "{_term(m.group(1) or m.group(2))}".'

    m = re.search(r"\b(?:ft|a|art):(?:%s)" % _QUOTED, q)
    if m:
        return f"Its art or flavour text mentions {_term(m.group(1) or m.group(2))}."
    return ""


# Roles whose answer is the role itself. Saying "this removal spell is here
# because the deck needs removal" is noise wearing an explanation's clothes,
# and the role heading already says it.
STRUCTURAL = {"lands", "ramp", "draw", "spot", "wipe", "basic"}


def explain_edges(card: dict, commander: dict, deck: list) -> str:
    """Why this card was CHOSEN -- the gap this file's docstring names.

    Reads the objects off real edges. No model: the object flowing across the
    edge is the reason, already in the data.

    unknown / unread with no edges say could-not-read — never look
    like a readable card with none. Real none stays silent (no invented claim).
    """
    from deckwright import synergy
    objs = sorted(set(synergy.edges(card, commander))
                  | set(synergy.edges(commander, card)))
    if objs:
        joined = ", ".join(objs)
        return f"connects to your commander through {joined}"
    note = synergy.synergy_read_note(card, commander)
    return note or ""


# Roles that get the chosen-via-edges clause when a commander is supplied.
# STRUCTURAL roles already skip; deepcut is a thematic pick, so it joins.
_EDGE_ROLES = frozenset({"theme", "plan", "deepcut"})


def annotate(
    deck: list,
    queries: dict[str, str],
    commander: dict | None = None,
) -> None:
    """Stamp each card with why it was offered (and chosen, when edges exist).

    `explain_edges` is the chosen half — wire it here. Does not change
    which cards are in the 99; only the why text.
    """
    for card in deck:
        role = card.get("role")
        if role in STRUCTURAL:
            continue
        reason = explain(queries.get(role, ""), role)
        edge = ""
        if commander and role in _EDGE_ROLES:
            edge = explain_edges(card, commander, deck)
        parts = [p for p in (reason, edge) if p]
        if parts:
            card["why"] = " ".join(parts)


# ---------------------------------------------------------------- accounting
# Third question: what *job* is this card doing? why.py already answers offered
# (annotate) and chosen (explain_edges). Buckets are provenance, not quality.
# Never pad. Unknown is its own line — never absorbed into curve_mana.

BUCKETS = (
    "theme_strategy",
    "theme_only",
    "strategy_only",
    "curve_mana",
    "unknown",
)

_CURVE_ROLES = frozenset({"lands", "ramp", "basic", "draw", "spot", "wipe"})


def _type_line(card: dict) -> str:
    return (card.get("type") or card.get("type_line") or "").lower()


def _oracle_text(card: dict) -> str | None:
    """None when unreadable — distinct from empty string."""
    if "oracle_text" in card and card.get("oracle_text") is None and card.get("text") is None:
        return None
    if "text" in card and card.get("text") is None and card.get("oracle_text") is None:
        return None
    raw = card.get("oracle_text")
    if raw is None:
        raw = card.get("text")
    if raw is None:
        return None
    return str(raw)


def _on_theme(
    card: dict,
    tribe: str | None,
    theme_label: str,
    tribes: list[str] | None = None,
) -> bool | None:
    """True / False when decidable; None when we could not look.

    sibling tribes (horse OR knight) must all count.
    `tribes` is the full list; `tribe` remains the first for older callers.
    """
    typ = _type_line(card)
    tribe_list = [t for t in (tribes or ([tribe] if tribe else [])) if t]
    if tribe_list:
        if typ:
            for t in tribe_list:
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", typ
                ):
                    return True
            # Type line present and no tribe word → not on-theme by tribe.
            return False
        return None
    label = (theme_label or "").strip().lower()
    if not label:
        return False
    # Non-tribe theme: look for the label in type line or oracle.
    oracle = _oracle_text(card)
    if oracle is None and not typ:
        return None
    hay = f"{typ} {oracle or ''}".lower()
    # Singular-ish: "pirates" → "pirate"
    stem = label.rstrip("s")
    if label in hay or (stem and stem in hay):
        return True
    return False if (typ or oracle is not None) else None


def _serves_strategy(card: dict, commander: dict | None) -> bool | None:
    """True / False when both profiles readable; None when either is unknown."""
    if not commander:
        # No commander to relate to — strategy fit is empty, not unknown.
        return False
    from deckwright import synergy
    pc = synergy.profile(card)
    pm = synergy.profile(commander)
    if pc["status"] == synergy.UNKNOWN or pm["status"] == synergy.UNKNOWN:
        return None
    objs = set(synergy.edges(card, commander)) | set(synergy.edges(commander, card))
    if objs:
        return True
    # Plan-role cards were selected for the strategy query. When synergy finds
    # nothing, the role is still provenance for "strategy only" — not a quality
    # claim, and not a reason to invent edges.
    if (card.get("role") or "") == "plan":
        return True
    return False


def _bucket_for(
    card: dict,
    *,
    on_theme: bool | None,
    serves: bool | None,
) -> str:
    """Exclusive assignment.

    `unknown` is only for an undecided axis (None) — we
    could not look. Both axes decided False is residual curve/mana fill, even
    when the card sat in a theme or deepcut *slot*. Hiding a real None inside
    curve_mana is forbidden; filing a decidable-neither theme filler as
    unknown was the Gate 1 false failure.
    """
    role = (card.get("role") or "").lower()

    if on_theme is True and serves is True:
        return "theme_strategy"
    if on_theme is True and serves is False:
        return "theme_only"
    if on_theme is False and serves is True:
        return "strategy_only"
    # Any undecided axis → unknown (could not look).
    if on_theme is None or serves is None:
        if on_theme is not True and serves is not True and role in _CURVE_ROLES:
            if on_theme is None and serves is None:
                return "unknown"
            if on_theme is False and serves is None and role in {"lands", "ramp", "basic"}:
                oracle = _oracle_text(card)
                if oracle is None:
                    return "unknown"
                return "curve_mana"
        return "unknown"
    # Both decided False — residual curve/mana, every role.
    return "curve_mana"


def theme_noun(theme_label: str = "", tribes: list[str] | None = None,
               tribe: str | None = None) -> str:
    """Short noun for bucket copy — never the model's full paraphrase.

    "strategy only, not pirate raiders — combat…"
    is broken English. Prefer tribe word(s), else first short token.
    """
    tribe_list = [t for t in (tribes or ([tribe] if tribe else [])) if t]
    if tribe_list:
        if len(tribe_list) == 1:
            return tribe_list[0]
        if len(tribe_list) == 2:
            return f"{tribe_list[0]} and {tribe_list[1]}"
        return ", ".join(tribe_list[:-1]) + f", and {tribe_list[-1]}"
    label = (theme_label or "").strip()
    if not label:
        return "theme"
    for raw in re.split(r"[^a-zA-Z0-9+]+", label):
        if len(raw) >= 4:
            return raw.lower()
    return label.split()[0].lower() if label.split() else "theme"


def account_deck(
    deck: list[dict],
    *,
    commander: dict | None,
    theme_label: str = "",
    tribe: str | None = None,
    tribes: list[str] | None = None,
    theme_legal_count: int | None = None,
) -> dict:
    """Partition the 99 into exclusive theme/strategy buckets.

    theme_legal_count is the composed Scryfall pool size (from readback), or
    None when unknown. Never invent a count. Stamps card["bucket"] in place.
    """
    cards_out: list[dict] = []
    counts = {b: 0 for b in BUCKETS}
    tribe_list = [t for t in (tribes or ([tribe] if tribe else [])) if t]
    primary = tribe_list[0] if tribe_list else tribe

    for card in deck or []:
        on_theme = _on_theme(card, primary, theme_label, tribes=tribe_list or None)
        serves = _serves_strategy(card, commander)
        bucket = _bucket_for(card, on_theme=on_theme, serves=serves)
        card["bucket"] = bucket
        counts[bucket] = counts.get(bucket, 0) + 1
        cards_out.append({
            "name": card.get("name") or "",
            "bucket": bucket,
            "role": card.get("role") or "",
        })

    noun = theme_noun(theme_label, tribes=tribe_list, tribe=primary)
    return {
        "theme_label": (theme_label or "").strip() or "theme",
        "theme_noun": noun,
        "theme_legal": theme_legal_count,  # None = unknown, never coerced to 0
        "tribe": primary,
        "tribes": tribe_list,
        "counts": counts,
        "cards": cards_out,
    }
