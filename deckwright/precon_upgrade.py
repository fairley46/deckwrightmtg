"""
precon_upgrade.py — ways-to-play + synergistic beef-it-up for bought precons.

Someone who bought a precon already owns the 99. They need the same computed
ways the commander picker shows, plus upgrade candidates that connect to the
commander — Scryfall-verified names only. Never invent a card name. An unknown
(could not look) is never reported as an empty match.

every add pairs with a cut suggestion (never auto-applied). Unknown
is never a cut. Lands and ramp are never cut, so role floors survive.
synergistic skips already in the 99 are disclosed, not silent.
"""

from __future__ import annotations

from typing import Any

from deckwright import commanders as cmd_kb
from deckwright import playstyles
from deckwright import precon_delta
from deckwright import sim
from deckwright import synergy
from deckwright import why as why_card

# Conventional floors — never suggest cutting these roles for an upgrade.
# Applying every suggested swap must leave land and ramp counts intact.
_CUT_PROTECTED_ROLES = frozenset({"lands", "basic", "ramp"})


def ways_for_precon(commander_card: dict | None, *, limit: int = 3) -> dict[str, Any]:
    """Ways-to-play for a precon commander — same merge/detect path as the ways-to-play work.

    Returns:
      {
        "status": "ok" | "unknown" | "empty",
        "ways": [{playstyle, says, why, method, confidence, source, note?}, …],
        "chosen_playstyle": id | None,
        "ways_note": str | None,   # honesty when empty/unknown
      }

    Cap is display-only (picker also shows a short list). Never pads with
    invented playstyles.
    """
    card = commander_card or {}
    if not card.get("name") and not (card.get("text") or card.get("oracle_text")):
        return {
            "status": "unknown",
            "ways": [],
            "chosen_playstyle": None,
            "ways_note": "Could not read the commander — not inventing ways.",
        }

    status, raw = cmd_kb._ways_for_card(card)
    if status == "unknown":
        return {
            "status": "unknown",
            "ways": [],
            "chosen_playstyle": None,
            "ways_note": "Could not read how this commander wants to be played.",
        }

    vocab = playstyles.load_playstyles()
    ways: list[dict[str, Any]] = []
    for w in raw:
        pid = w.get("playstyle")
        if not pid or pid not in vocab:
            continue  # never invent a row to fill space
        entry = vocab[pid]
        ways.append({
            "playstyle": pid,
            "says": entry.get("says") or "",
            "confidence": w.get("confidence"),
            "method": w.get("method"),
            "why": w.get("why") or entry.get("says") or pid,
            "source": w.get("source"),
            "note": w.get("note"),
        })
        if len(ways) >= limit:
            break

    if not ways:
        return {
            "status": "empty",
            "ways": [],
            "chosen_playstyle": None,
            "ways_note": "No ways detected — not inventing any.",
        }

    return {
        "status": "ok",
        "ways": ways,
        "chosen_playstyle": ways[0]["playstyle"],
        "ways_note": None,
    }


def card_is_readable(card: dict) -> bool:
    """Know Magic readability: at least one oracle_signal or catalog keyword.

    soft-prefer these among upgrade hits. Unreadable cards stay in
    the pool — never dropped to manufacture a number.
    same gate as synergy.card_read_status == ok.
    """
    return synergy.card_read_status(card) == "ok"


def _families_for_way_playstyle(way_playstyle: str | None) -> set[str]:
    """Map a detectable playstyle id → mechanic family names via oracle_signals."""
    pid = (way_playstyle or "").strip()
    if not pid:
        return set()
    entry = playstyles.load_playstyles().get(pid)
    if not entry:
        return set()
    det = entry.get("detect") or {}
    sigs = (
        set(det.get("requires") or [])
        | set(det.get("requires_any") or [])
        | set(det.get("boosts") or [])
    )
    fams: set[str] = set()
    for name, meta in synergy.load_signals().items():
        if name not in sigs:
            continue
        fam = meta.get("family")
        if isinstance(fam, str) and fam.strip():
            fams.add(fam.strip())
    return fams


def upgrade_queries(commander: dict, brief: dict | None = None,
                    *, max_queries: int = 5,
                    way_playstyle: str | None = None) -> list[str]:
    """Scryfall query fragments for beef-it-up recall. Never invents names.

    Demand → synergy neighbors + leave-enablers, plus a tribal type line when
    the precon brief carries a creature-type theme. Empty when we could not
    look (unknown demand) — caller must not treat that as "no upgrades exist".

    optional `way_playstyle` (one of the 13 detectable ids) soft-orders
    recall queries so fragments that touch that style's detect signals lead.
    Unknown / invalid ids are ignored — never invent a 14th playstyle.
    """
    brief = brief or {}
    out: list[str] = []
    seen: set[str] = set()

    demand = synergy.demand_profile(commander)
    if demand["status"] == synergy.UNKNOWN:
        return []

    # Tribe first when the precon brief names a creature type — that is the
    # "beef up the pirates" ask. Abstract themes stay out of t: (role_query trap).
    theme = brief.get("theme")
    if isinstance(theme, str) and theme.strip():
        t = theme.strip().lower()
        if t.isalpha() and len(t) >= 3:
            q = f"t:{t}"
            seen.add(q)
            out.append(q)

    recall = list(synergy.synergy_recall_queries(commander))
    prefer_fams = _families_for_way_playstyle(way_playstyle)
    if prefer_fams:
        # Neighbors in families the chosen playstyle detects lead the search
        # list — same Scryfall fragments, different order.
        boosted: list[str] = []
        rest: list[str] = []
        for neigh in synergy.synergy_neighbors(commander):
            hit = (neigh.get("family") or "") in prefer_fams
            for q in synergy.recall_queries_for_neighbor(neigh):
                (boosted if hit else rest).append(q)
        ordered = boosted + rest
        if ordered:
            recall = ordered + [q for q in recall if q not in ordered]

    for q in recall:
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
        if len(out) >= max_queries:
            break

    return out


def rank_upgrades(
    candidates: list[dict],
    commander: dict,
    deck: list[dict],
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Rank Scryfall-verified candidates. Names only from `candidates`.

    status:
      ok      — ranked some cards (or looked and none cleared the bar → empty list)
      empty   — had candidates but none synergistic / all already in the 99
      unknown — no candidates handed in (caller could not search); not a zero

    Each card row: name, usd, edhrec_rank, why, edges, verdict.

    synergistic candidates already in the 99 return as `already_in`
    — their own group — so a silent exclusion cannot send her to buy a card
    that is already in the box. Copy says "in this deck", never collection.
    """
    if not candidates:
        return {
            "status": "unknown",
            "cards": [],
            "already_in": [],
            "already_in_note": None,
            "note": "Could not look up upgrade candidates — not inventing any.",
        }

    in_deck = {c.get("name") for c in deck if c.get("name")}
    cmdr_name = commander.get("name")
    if cmdr_name:
        in_deck.add(cmdr_name)

    from deckwright import mechanics

    tax = mechanics.load_families()
    # Serve the commander first, then the precon's existing lean. A thin or
    # mis-tagged 99 must not erase demand the commander already states.
    lean = precon_delta._related_set(
        _lean_from_deck(deck, tax) | _lean_from_commander(commander),
        tax,
    )

    scored: list[tuple[int, int, int, int, int, dict]] = []
    already_scored: list[tuple[int, int, int, int, int, dict]] = []
    for card in candidates:
        name = card.get("name")
        if not name:
            continue
        owned = name in in_deck
        score_row = _score_upgrade_candidate(
            card, commander, deck, lean, tax)
        if score_row is None:
            continue
        sort_key, row = score_row
        if owned:
            # name it — do not mix into buys, do not drop.
            already_scored.append((*sort_key, row))
        else:
            scored.append((*sort_key, row))

    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]), reverse=True)
    already_scored.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]), reverse=True)

    cards = _dedupe_rows(scored, limit)
    # Already-in is disclosure, not a buy list — show every synergistic hit
    # we skipped, capped so a huge overlap does not bury the page.
    already_in = _dedupe_rows(already_scored, limit=max(limit, 12))
    already_note = (
        "Already in this deck — you don't need to buy these."
        if already_in else None
    )

    if not cards:
        return {
            "status": "empty",
            "cards": [],
            "already_in": already_in,
            "already_in_note": already_note,
            "note": (
                "No synergistic upgrades found outside the 99 — not inventing any."
                if not already_in
                else "The strongest fits are already in this deck."
            ),
        }
    # every add is a swap — pair a cut, or say why we could not.
    _pair_cuts(cards, deck, commander)
    return {
        "status": "ok",
        "cards": cards,
        "already_in": already_in,
        "already_in_note": already_note,
        "note": None,
    }


def _score_upgrade_candidate(
    card: dict,
    commander: dict,
    deck: list[dict],
    lean: set[str],
    tax: dict,
) -> tuple[tuple[int, int, int, int, int], dict] | None:
    """Synergy bar + row, or None when the card does not clear it.

    Shared by upgrade ranking and the already-in disclosure so a card
    skipped only because it is in the 99 is judged by the same bar.

    Sort (desc): edges → readable → helps → demand overlap → EDHREC.
    """
    name = card.get("name")
    if not name:
        return None
    edge_objs = sorted(set(synergy.edges(card, commander))
                       | set(synergy.edges(commander, card)))
    help_row = precon_delta.card_help_hurt(card, lean, tax)
    verdict = help_row.get("verdict") or "neutral"
    demand_overlap = _demand_signal_overlap(card, commander)
    if not edge_objs and verdict != "helps" and not demand_overlap:
        return None
    why = why_card.explain_edges(card, commander, deck)
    if not why and demand_overlap:
        why = ("Shares plan signals with your commander: "
               + ", ".join(demand_overlap) + ".")
    if not why:
        why = help_row.get("reason") or ""
    rank = card.get("edhrec_rank")
    rank_n = int(rank) if isinstance(rank, int) else 10**9
    readable = card_is_readable(card)
    row = {
        "name": name,
        "usd": card.get("usd"),
        "edhrec_rank": rank,
        "why": why,
        "edges": edge_objs,
        "verdict": verdict if verdict != "neutral" or not demand_overlap
                   else "helps",
        "type": card.get("type") or card.get("type_line") or "",
        "image": card.get("image"),
        "purchase_uris": card.get("purchase_uris") or {},
        "readable": readable,
    }
    sort_key = (
        len(edge_objs),
        1 if readable else 0,
        1 if row["verdict"] == "helps" else 0,
        len(demand_overlap),
        -rank_n,
    )
    return sort_key, row


def _dedupe_rows(
    scored: list[tuple], limit: int,
) -> list[dict]:
    seen: set[str] = set()
    cards: list[dict] = []
    for tup in scored:
        row = tup[-1]
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        cards.append(row)
        if len(cards) >= limit:
            break
    return cards


def _pair_cuts(cards: list[dict], deck: list[dict], commander: dict) -> None:
    """Stamp cut / cut_note on each upgrade row. Suggestion only — never applied."""
    pool = rank_cut_candidates(deck, commander)
    claimed: set[str] = set()
    for row in cards:
        cut = None
        for cand in pool:
            name = cand["name"]
            if name in claimed:
                continue
            claimed.add(name)
            cut = cand
            break
        if cut:
            row["cut"] = cut
            row["cut_note"] = None
        else:
            row["cut"] = None
            row["cut_note"] = (
                "No weak cut left without touching lands or ramp — not inventing one."
            )


def rank_cut_candidates(deck: list[dict], commander: dict) -> list[dict]:
    """Weak cards to cut for an upgrade — commander-orphan ∧ high CMC.

    "Orphan" here means no produce↔consume edge to the commander. Peer-only
    glue does not save a seven-mana blank that never touches the plan — that
    is the Aethersquall Ancient case. Cohesion's stricter peer-orphan count
    stays for deck-level reporting; cuts answer "what comes out for this add."

    Never returns unknown profiles (could not read ≠ does nothing). Never
    returns lands or ramp. Ranking reuses edge counts + CMC; no new weights.

    cards sharing commander demand signals are not cuttable — same
    overlap that puts them in already_in when they are upgrade candidates.
    """
    cmdr_name = commander.get("name")
    scored: list[tuple[int, float, dict]] = []
    for card in deck:
        name = card.get("name")
        if not name or name == cmdr_name:
            continue
        # unknown and unread are not cuts — could not read ≠ no glue.
        if synergy.card_read_status(card) in (synergy.UNKNOWN, synergy.UNREAD):
            continue
        if _cut_protected(card):
            continue
        # Plan/theme role stamps — keep; flex blanks without commander edges go.
        role = (card.get("role") or "").lower()
        if role in ("plan", "theme"):
            continue
        if _edge_count(card, commander):
            continue  # glued to the commander — not a cut
        if _demand_signal_overlap(card, commander):
            continue  # shares commander plan signals — not a cut
        cmc = _card_cmc(card)
        row = {
            "name": name,
            "cmc": cmc,
            "cost": card.get("cost") or card.get("mana_cost") or "",
            "type": card.get("type") or card.get("type_line") or "",
            "why": _cut_why(card, cmc),
        }
        scored.append((1, cmc, row))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for _o, _c, row in scored:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        out.append(row)
    return out


def _cut_protected(card: dict) -> bool:
    """Lands and ramp are never cut candidates — floors must survive every swap."""
    role = (card.get("role") or "").lower()
    if role in _CUT_PROTECTED_ROLES:
        return True
    m = sim.mechanics(card, set())
    if m.get("land"):
        return True
    if m.get("produces") or m.get("fetch") or m.get("drop"):
        return True
    t = (card.get("type") or card.get("type_line") or "").lower()
    return "land" in t


def _edge_count(a: dict, b: dict) -> int:
    return len(synergy.edges(a, b)) + len(synergy.edges(b, a))


def _card_cmc(card: dict) -> float:
    cmc = card.get("cmc")
    if isinstance(cmc, (int, float)):
        return float(cmc)
    m = sim.mechanics(card, set())
    return float(m.get("cmc") or 0)


_MANA_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve",
)


def _cut_why(card: dict, cmc: float) -> str:
    """Plain-language cut reason — never 'orphan' / 'plan role' jargon.

    Only called for readable cut candidates. Unknown/unread never reach here
   ; if they did, say could-not-read rather than no connection.
    """
    if synergy.card_read_status(card) in (synergy.UNKNOWN, synergy.UNREAD):
        return synergy.COULD_NOT_READ + "."
    n = int(cmc) if float(cmc).is_integer() else cmc
    if isinstance(n, int) and 0 <= n < len(_MANA_WORDS):
        mana = f"{_MANA_WORDS[n]} mana"
    else:
        mana = f"{n} mana"
    bits = [mana, "no connection to your commander"]
    role = (card.get("role") or "").lower()
    if role and role not in ("plan", "theme") and role not in _CUT_PROTECTED_ROLES:
        bits.append("does not advance the plan")
    elif not role:
        bits.append("does not advance the plan")
    return ", ".join(bits) + "."


def _lean_from_deck(deck: list[dict], tax: dict) -> set[str]:
    from deckwright import mechanics

    tagged = mechanics.tag_deck(deck, tax)
    return precon_delta._lean_families(tagged)


def _lean_from_commander(commander: dict) -> set[str]:
    """Families the commander demands — upgrade lean when the 99 is thin."""
    demand = synergy.demand_profile(commander)
    if demand["status"] == synergy.UNKNOWN:
        return set()
    signals_meta = synergy.load_signals()
    out: set[str] = set()
    for sig in demand.get("signals") or []:
        fam = (signals_meta.get(sig) or {}).get("family")
        if fam:
            # Same alias map synergy uses for relation hops.
            out.add(synergy._normalize_family(str(fam)))
    return out


def _demand_signal_overlap(card: dict, commander: dict) -> list[str]:
    """Signals both the card and commander carry — a COUNT, not a weight."""
    cd = synergy.demand_profile(commander)
    cp = synergy.profile(card)
    if cd["status"] == synergy.UNKNOWN or cp["status"] == synergy.UNKNOWN:
        return []
    overlap = set(cd.get("signals") or []) & set(cp.get("signals") or [])
    return sorted(overlap)


def attach_ways(response: dict) -> dict:
    """Stamp ways_to_play / chosen_playstyle onto a precon API response."""
    payload = ways_for_precon(response.get("commander_card"))
    response["ways_to_play"] = payload["ways"]
    response["chosen_playstyle"] = payload["chosen_playstyle"]
    response["ways_status"] = payload["status"]
    if payload.get("ways_note"):
        response["ways_note"] = payload["ways_note"]
    return response
