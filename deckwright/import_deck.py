"""
import_deck.py — parse pasted decklists and resolve names against Scryfall.

second way in alongside build. Accepts Archidekt/Moxfield/plain text,
bulk-resolves via /cards/collection, reports unresolved lines by line number.
"""

import re
from typing import Any, Callable, Awaitable

from deckwright import sim

COLLECTION_BATCH = 75

# iOS/macOS/Word/web paste emit smart punctuation; Scryfall names use
# ASCII. Map for lookup only — never rewrite the typed entry or the display name.
_LOOKUP_PUNCT = str.maketrans({
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u2013": "-",  # EN DASH
    "\u2014": "-",  # EM DASH
})


def normalize_name_for_lookup(name: str) -> str:
    """ASCII-ise smart punctuation for Scryfall /cards/collection matching.

    Targeted map, not NFKC: NFKC leaves U+2019 alone, and a broader fold would
    risk touching legitimate non-ASCII in print names (Æther, Lim-Dûl, Jötun).
    """
    return name.translate(_LOOKUP_PUNCT)

# Section headers: // Commander, //Lands, SIDEBOARD:, etc.
_SECTION_RE = re.compile(
    r"^\s*(?://\s*)?(commander|maindeck|main|deck|lands?|creatures?|"
    r"artifacts?|enchantments?|planeswalkers?|spells?|instants?|sorceries?|"
    r"sideboard|maybeboard)\s*(?::\s*)?$",
    re.I,
)
_SIDEBOARD_SECTIONS = frozenset({"sideboard", "maybeboard"})

# 1 Sol Ring | 1x Sol Ring | 18 Island | - 1 Sol Ring
# Applied to a stripped line: optional bullet, then an optional quantity.
_QTY_PREFIX_RE = re.compile(
    r"^(?:[-*]\s*)?"
    r"(?:(\d+)(?:\s*x\s+|\s+))?",   # "2 ", "2x ", "2 x "
    re.I,
)
_SET_CODE_RE = re.compile(r"[A-Za-z0-9]+")


def _set_suffix_start(s: str, allow_start: bool) -> int | None:
    """Index where a trailing "(SET)" or "(SET) collector" begins in `s`.

    `s` has no trailing whitespace. Returns the earliest such position, or
    None. With allow_start False the suffix may not begin at index 0 (a name
    must be left in front of it).
    """
    heads = [s]                      # "... (SET)"
    parts = s.rsplit(None, 1)
    if len(parts) == 2:
        heads.append(parts[0])       # "... (SET) collector"
    found: list[int] = []
    for head in heads:
        if head.endswith(")"):
            o = head.rfind("(")
            if o >= 0 and _SET_CODE_RE.fullmatch(head, o + 1, len(head) - 1):
                found.append(o)
    found = [o for o in found if allow_start or o > 0]
    return min(found) if found else None


def _strip_set_suffix(name: str, allow_start: bool = True) -> str:
    """Drop one trailing "(SET)" / "(SET) collector" from a card name."""
    name = name.rstrip()
    at = _set_suffix_start(name, allow_start)
    return name[:at] if at is not None else name


def _split_qty_line(raw: str) -> tuple[str | None, str]:
    """(quantity digits or None, card name) for one decklist line.

    Gives the same answer as the old single regex
    ^\\s*(?:[-*]\\s*)?(?:(\\d+)\\s*x?\\s+)?(.+?)(?:\\s*\\(SET\\)(?:\\s+\\S+)?)?\\s*$,
    including its handling of lines that are only a bullet or a number
    followed by spaces, without that pattern's backtracking.
    """
    s = raw.lstrip()
    m = _QTY_PREFIX_RE.match(s)
    rest = s[m.end():]
    if rest:
        return m.group(1), _strip_set_suffix(rest, allow_start=False)
    # The prefix swallowed the whole line. The old pattern then gave back
    # characters so the name had at least one: a trailing space when there
    # was one to spare, otherwise the quantity or bullet itself.
    trailing = len(s) - len(s.rstrip())
    if m.group(1) is not None:
        if trailing >= 2:
            return m.group(1), ""
        # "2 x ": drop the "x" reading and keep it as the name instead.
        after = s[m.end(1):]
        name_at = len(after) - len(after.lstrip())
        if name_at and after[name_at:]:
            return m.group(1), _strip_set_suffix(after[name_at:], allow_start=False)
        dash = re.match(r"^[-*]\s*", s)
        remainder = s[dash.end():] if dash else s
        return None, _strip_set_suffix(remainder, allow_start=False)
    if trailing >= 1:
        return None, ""
    return None, s


def parse_decklist(text: str) -> dict:
    """Parse a pasted list into line entries. Does not call Scryfall.

    Returns:
        entries: [{line, name, qty, section}]
        sections: set of section names seen
    """
    entries: list[dict] = []
    sections_seen: set[str] = set()
    section = "main"
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _SECTION_RE.match(line):
            section = _SECTION_RE.match(line).group(1).lower()
            sections_seen.add(section)
            continue
        if section in _SIDEBOARD_SECTIONS:
            continue
        qty_digits, name = _split_qty_line(raw)
        name = name.strip()
        # Strip trailing set notation left on unusual formats.
        name = _strip_set_suffix(name).strip()
        qty = int(qty_digits or 1)
        entries.append({"line": i, "name": name, "qty": qty, "section": section})
        if section == "commander":
            # Archidekt often has one commander under the header; remaining lines are the 99.
            section = "main"
    return {"entries": entries, "sections": sections_seen}


def _is_legendary_commander(card: dict) -> bool:
    t = (card.get("type") or "").lower()
    text = (card.get("text") or "").lower()
    if "legendary" in t and "creature" in t:
        return True
    return "can be your commander" in text


def infer_role(card: dict) -> str:
    """Heuristic role for an imported card — feeds signals and overview sim."""
    tl = (card.get("type") or "").lower()
    t = (card.get("text") or "").lower()
    if "basic" in tl:
        return "basic"
    if "land" in tl:
        return "lands"
    if "destroy all" in t:
        return "wipe"
    if re.search(r"(destroy|exile) target", t):
        return "spot"
    if re.search(r"draw.{0,40}card|draw.{0,20}hand|draws? (a |two |three )?cards?", t):
        return "draw"
    m = sim.mechanics(card, set())
    if m["produces"] or m["fetch"]:
        return "ramp"
    if "artifact" in tl and m["cmc"] <= 3 and "mana" in t:
        return "ramp"
    return "theme"


def _unique_legendaries(resolved: dict[str, dict]) -> list[dict]:
    """Legendary commander-legal cards in the resolved map, one per name."""
    by_name: dict[str, dict] = {}
    for c in resolved.values():
        if _is_legendary_commander(c) and c.get("name"):
            by_name[c["name"]] = c
    return list(by_name.values())


def _deck_cards_for_identity(
    entries: list[dict],
    resolved: dict[str, dict],
) -> list[dict]:
    """Non-commander-section cards used for colour-identity covering checks."""
    deck_names = {e["name"] for e in entries if e.get("section") != "commander"}
    return [resolved[n] for n in deck_names if n in resolved]


def _covers_ci(deck_cards: list[dict], ci: set[str]) -> bool:
    for c in deck_cards:
        if "basic" in (c.get("type") or "").lower():
            continue
        card_ci = set(c.get("color_identity") or [])
        if not card_ci <= ci:
            return False
    return True


def _found_solo(card: dict) -> dict:
    return {
        "status": "found",
        "commander": card["name"],
        "partner": None,
        "commanders": [card["name"]],
        "candidates": [],
    }


def _found_pair(a: dict, b: dict) -> dict:
    """Stable commander/partner order — alphabetical by name."""
    first, second = sorted([a, b], key=lambda c: c["name"])
    return {
        "status": "found",
        "commander": first["name"],
        "partner": second["name"],
        "commanders": [first["name"], second["name"]],
        "candidates": [],
    }


def _pair_candidate(a: dict, b: dict) -> dict:
    first, second = sorted([a, b], key=lambda c: c["name"])
    return {
        "name": f"{first['name']} + {second['name']}",
        "commander": first["name"],
        "partner": second["name"],
    }


def _covering_partner_pairs(
    legendaries: list[dict],
    deck_cards: list[dict],
) -> list[tuple[dict, dict]]:
    """Legal Partner / Partner with / Friends forever pairs that cover deck CI.

    Uses app.partners_compatible — one source of truth with /api/build.
    Background / Doctor's companion stay out of scope.
    """
    # Late import: app imports this module at load time.
    from deckwright.app import partners_compatible, union_color_identity

    pairs: list[tuple[dict, dict]] = []
    seen: set[frozenset[str]] = set()
    for i, a in enumerate(legendaries):
        for b in legendaries[i + 1:]:
            if not partners_compatible(a, b):
                continue
            ci = set(union_color_identity(a, b))
            if not _covers_ci(deck_cards, ci):
                continue
            key = frozenset([a["name"], b["name"]])
            if key in seen:
                continue
            seen.add(key)
            pairs.append(tuple(sorted([a, b], key=lambda c: c["name"])))
    pairs.sort(key=lambda p: (p[0]["name"], p[1]["name"]))
    return pairs


def pick_commander(
    entries: list[dict],
    resolved: dict[str, dict],
    explicit: str | None = None,
    explicit_partner: str | None = None,
) -> dict:
    """Choose commander (or partner pair) from section, explicit name, or list.

    Returns {status, commander, partner, commanders, candidates, reason}.
    status: found | ambiguous | missing

    when no single legendary covers colour identity, look for exactly
    one legal Partner / Partner with / Friends forever pair whose combined
    identity does. Multiple covering pairs are ambiguous — candidates are the
    pairs, not the individuals.
    """
    legendaries = _unique_legendaries(resolved)
    deck_cards = _deck_cards_for_identity(entries, resolved)

    def covers_all(cmdr: dict) -> bool:
        return _covers_ci(deck_cards, set(cmdr.get("color_identity") or []))

    if explicit:
        key = explicit.strip()
        card = resolved.get(key)
        if not card:
            # Case-insensitive fallback — paste re-picks may not match the key.
            for c in resolved.values():
                if (c.get("name") or "").lower() == key.lower():
                    card = c
                    break
        if not card:
            return {
                "status": "missing",
                "commander": None,
                "partner": None,
                "candidates": [],
                "reason": f"Could not resolve commander name: {key}",
            }
        if not _is_legendary_commander(card):
            return {
                "status": "missing",
                "commander": None,
                "partner": None,
                "candidates": [],
                "reason": f"{key} is not a legal commander",
            }
        if explicit_partner:
            pkey = explicit_partner.strip()
            partner = resolved.get(pkey)
            if not partner:
                for c in resolved.values():
                    if (c.get("name") or "").lower() == pkey.lower():
                        partner = c
                        break
            if not partner or not _is_legendary_commander(partner):
                return {
                    "status": "missing",
                    "commander": None,
                    "partner": None,
                    "candidates": [],
                    "reason": f"Could not resolve partner name: {pkey}",
                }
            from deckwright.app import partners_compatible, union_color_identity
            if not partners_compatible(card, partner):
                return {
                    "status": "missing",
                    "commander": None,
                    "partner": None,
                    "candidates": [],
                    "reason": (f"{card['name']} and {partner['name']} "
                               f"cannot share the command zone"),
                }
            if not _covers_ci(deck_cards, set(union_color_identity(card, partner))):
                return {
                    "status": "missing",
                    "commander": None,
                    "partner": None,
                    "candidates": [],
                    "reason": ("Partner pair does not cover the full "
                               "color identity"),
                }
            return _found_pair(card, partner)
        if covers_all(card):
            return _found_solo(card)
        # Named half of a pair: find the unique covering partner in the list.
        covering = []
        for a, b in _covering_partner_pairs(legendaries, deck_cards):
            if card["name"] == a["name"]:
                covering.append((card, b))
            elif card["name"] == b["name"]:
                covering.append((card, a))
        if len(covering) == 1:
            return _found_pair(covering[0][0], covering[0][1])
        if len(covering) > 1:
            return {
                "status": "ambiguous",
                "commander": None,
                "partner": None,
                "candidates": [_pair_candidate(a, b) for a, b in covering],
                "reason": "Multiple partner pairs fit the color identity",
            }
        # Explicit legal commander still wins — validate_deck reports CI.
        return _found_solo(card)

    commander_entries = [e for e in entries if e.get("section") == "commander"]
    section_cmdrs: list[dict] = []
    seen_section: set[str] = set()
    for e in commander_entries:
        card = resolved.get(e["name"])
        if not card or not _is_legendary_commander(card):
            continue
        if card["name"] in seen_section:
            continue
        seen_section.add(card["name"])
        section_cmdrs.append(card)

    if len(section_cmdrs) >= 2:
        pairs = _covering_partner_pairs(section_cmdrs, deck_cards)
        if len(pairs) == 1:
            return _found_pair(pairs[0][0], pairs[0][1])
        if len(pairs) > 1:
            return {
                "status": "ambiguous",
                "commander": None,
                "partner": None,
                "candidates": [_pair_candidate(a, b) for a, b in pairs],
                "reason": "Multiple partner pairs fit the color identity",
            }
        # Section named two legendaries that are not a covering pair — fall
        # through to whole-list logic rather than guessing one.

    if len(section_cmdrs) == 1:
        card = section_cmdrs[0]
        if covers_all(card):
            return _found_solo(card)
        covering = []
        for a, b in _covering_partner_pairs(legendaries, deck_cards):
            if card["name"] == a["name"]:
                covering.append((card, b))
            elif card["name"] == b["name"]:
                covering.append((card, a))
        if len(covering) == 1:
            return _found_pair(covering[0][0], covering[0][1])
        if len(covering) > 1:
            return {
                "status": "ambiguous",
                "commander": None,
                "partner": None,
                "candidates": [_pair_candidate(a, b) for a, b in covering],
                "reason": "Multiple partner pairs fit the color identity",
            }
        return _found_solo(card)

    if len(legendaries) == 1:
        return _found_solo(legendaries[0])

    fitting = [c for c in legendaries if covers_all(c)]
    if len(fitting) == 1:
        return _found_solo(fitting[0])
    if len(fitting) > 1:
        names = sorted(c["name"] for c in fitting)
        return {
            "status": "ambiguous",
            "commander": None,
            "partner": None,
            "candidates": names,
            "reason": "Multiple legendary creatures fit the color identity",
        }

    pairs = _covering_partner_pairs(legendaries, deck_cards)
    if len(pairs) == 1:
        return _found_pair(pairs[0][0], pairs[0][1])
    if len(pairs) > 1:
        return {
            "status": "ambiguous",
            "commander": None,
            "partner": None,
            "candidates": [_pair_candidate(a, b) for a, b in pairs],
            "reason": "Multiple partner pairs fit the color identity",
        }
    if legendaries:
        names = sorted(c["name"] for c in legendaries)
        return {
            "status": "ambiguous",
            "commander": None,
            "partner": None,
            "candidates": names,
            "reason": "Legendary creatures found but none cover the full color identity",
        }
    return {
        "status": "missing",
        "commander": None,
        "partner": None,
        "candidates": [],
        "reason": "No commander section and no legendary creature in the list",
    }


def build_deck_list(
    entries: list[dict],
    resolved: dict[str, dict],
    commander_name: str | None,
    partner_name: str | None = None,
) -> list[dict]:
    """Expand qty entries into a 99- or 98-card deck list with inferred roles."""
    deck: list[dict] = []
    exclude = {
        n.lower()
        for n in (commander_name, partner_name)
        if n
    }
    for e in entries:
        if e.get("section") == "commander":
            continue
        card = resolved.get(e["name"])
        if not card:
            continue
        if card["name"].lower() in exclude:
            continue
        role = infer_role(card)
        for _ in range(e["qty"]):
            deck.append({**card, "role": role})
    return deck


async def resolve_names(
    names: list[str],
    scry_post: Callable[..., Awaitable[dict]],
    trim_card: Callable[[dict], dict],
) -> tuple[dict[str, dict], list[str]]:
    """Bulk-resolve unique names via /cards/collection. Returns map and not_found.

    Double-faced cards are looked up by their front face. Scryfall writes them
    as "Witch Enchanter // Witch-Blessed Meadow" and every exporter — including
    ours — writes that full string to a decklist, but `/cards/collection` only
    matches the front face and reports the combined form as not-found.

    Without this, Deckwright cannot re-import its own export: the Lyra deck
    written by this app contains two such lines and silently lost both.
    """
    unique = list(dict.fromkeys(names))
    # request-name -> name actually sent to Scryfall (front face + punct fold)
    lookup: dict[str, str] = {}
    for n in unique:
        front = n.split("//")[0].strip() if "//" in n else n
        lookup[n] = normalize_name_for_lookup(front)
    found: dict[str, dict] = {}
    not_found: list[str] = []

    for start in range(0, len(unique), COLLECTION_BATCH):
        batch = unique[start:start + COLLECTION_BATCH]
        body = {"identifiers": [{"name": lookup[n]} for n in batch]}
        j = await scry_post("/cards/collection", json=body)
        nf_set = {
            (nf.get("name") or "").lower()
            for nf in (j.get("not_found") or [])
        }
        returned = [trim_card(c) for c in (j.get("data") or [])]
        by_lower = {c["name"].lower(): c for c in returned}

        for req in batch:
            asked = lookup[req]
            if asked.lower() in nf_set:
                not_found.append(req)
                continue
            if req in found:
                continue
            card = by_lower.get(asked.lower()) or by_lower.get(req.lower())
            if card:
                found[req] = card
                found[card["name"]] = card
                # so a later line written either way resolves to the same card
                found[asked] = card
            else:
                not_found.append(req)

    return found, not_found


def unresolved_lines(
    entries: list[dict],
    resolved: dict[str, dict],
    missing_names: list[str],
) -> list[dict]:
    """Per-line unresolved report — never silently drop cards."""
    missing_set = set(missing_names)
    out: list[dict] = []
    for e in entries:
        name = e["name"]
        if name in resolved:
            continue
        if name in missing_set or e.get("parse_error"):
            out.append({
                "line": e["line"],
                "text": name,
                "reason": "not found on Scryfall" if not e.get("parse_error")
                else "could not parse line",
            })
    return out


async def resolve_decklist(
    text: str,
    scry_post: Callable[..., Awaitable[dict]],
    trim_card: Callable[[dict], dict],
    explicit_commander: str | None = None,
    explicit_partner: str | None = None,
) -> dict[str, Any]:
    """Parse and resolve a pasted list. Analysis hooks live in app.py."""
    parsed = parse_decklist(text)
    entries = parsed["entries"]
    if not entries:
        return {
            "entries": [],
            "resolved": {},
            "unresolved": [],
            "commander_pick": {
                "status": "missing",
                "commander": None,
                "partner": None,
                "candidates": [],
                "reason": "empty list",
            },
            "deck": [],
        }

    names = [e["name"] for e in entries]
    resolved, missing = await resolve_names(names, scry_post, trim_card)
    # Also key by requested name when Scryfall returns a different printing name.
    for e in entries:
        if e["name"] in resolved:
            continue
        for card in resolved.values():
            if card["name"].lower() == e["name"].lower():
                resolved[e["name"]] = card
                break

    unresolved = unresolved_lines(entries, resolved, missing)
    commander_pick = pick_commander(
        entries, resolved, explicit_commander, explicit_partner)
    deck = []
    cmdr_name = commander_pick.get("commander")
    partner_name = commander_pick.get("partner")
    if commander_pick["status"] == "found":
        deck = build_deck_list(
            entries, resolved, cmdr_name, partner_name=partner_name)

    cmdr_card = resolved.get(cmdr_name) if cmdr_name else None
    partner_card = resolved.get(partner_name) if partner_name else None
    zone = [c for c in (cmdr_card, partner_card) if c]
    return {
        "entries": entries,
        "resolved": resolved,
        "unresolved": unresolved,
        "commander_pick": commander_pick,
        "deck": deck,
        "commander_card": cmdr_card,
        "partner_card": partner_card,
        "commander_cards": zone,
    }
