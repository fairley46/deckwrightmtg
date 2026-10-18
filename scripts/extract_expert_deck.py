"""
extract_expert_deck.py — the expert's card list, from his own words, verified.

Commander Challenge (YJ8X8bO8jeY) builds Hei Bai on camera and names roughly
thirty-five cards, each with a stated reason. That is the ground truth this
project has been short of: a 99 chosen by a human expert for a commander we
did not pick ourselves.

His Moxfield links return HTTP 403 behind Cloudflare, so the transcript is the
source. The transcript is auto-generated and mangles names badly -- Hei Bai
appears as "Hayabusa", "Haywire" and "Heiwei" in different places. So a name in
the transcript is a CANDIDATE, never a fact.

THE ONE RULE APPLIES HERE. No card name is written from memory. Candidates are
n-grams lifted from the transcript and matched against Scryfall's own
`card-names` catalog, which we already hold in data/scryfall_grammar.json.
Scryfall supplies every name; the transcript only chooses among them. Anything
that does not match a real card is dropped, not guessed.

CON-177: the raw verified list is "cards named in the video", not "cards in
his deck". It includes rejected commanders and Faithless Looting (named as a
counter-example). Split into `chosen` / `mentioned` from transcript position
plus card type, and record which rule classified each card. Score only against
`chosen`.

    python scripts/extract_expert_deck.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GRAMMAR = REPO / "data" / "scryfall_grammar.json"
TRANSCRIPT = REPO / "docs" / "review" / "backend-review" / "fixtures" / "hei-bai-transcript.txt"
ORACLE = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-oracle.json"
OUT = REPO / "docs" / "review" / "backend-review" / "fixtures" / "expert-hei-bai-cards.json"

COMMANDER = "Hei Bai, Spirit of Balance"
COMMANDER_COLORS = ["W", "B"]

# Video description: commander-selection runs 0:37–5:49. Auto-subs have no
# reliable timestamps in this environment, so the same window is recovered from
# the transcript's own section phrases — step one through the handoff to step
# two. Position is a character offset in the committed transcript.
CMDR_START_PHRASE = "step one"
CMDR_END_PHRASE = "now that we have our commander"

# Words that start an n-gram but are never part of a card name. Without this
# every "the card" and "this deck" becomes a candidate.
STOP = {
    "the", "this", "that", "a", "an", "and", "or", "but", "so", "then", "now",
    "i", "you", "we", "it", "my", "your", "our", "is", "was", "are", "have",
    "has", "get", "got", "put", "play", "played", "deck", "card", "cards",
    "mana", "value", "turn", "game", "of", "to", "in", "on", "at", "for",
    "with", "from", "into", "out", "up", "down", "if", "when", "because",
    "also", "just", "like", "really", "going", "want", "need", "think",
}

RULES = [
    {
        "id": "rejected_commander",
        "description": (
            "First named inside the commander-selection section "
            "(step one → 'now that we have our commander') and the card is a "
            "legendary creature other than the chosen commander."
        ),
    },
    {
        "id": "counter_example",
        "description": (
            "Named near 'this is not … advantage' — the video's own "
            "counter-example (Faithless Looting), not a pick."
        ),
    },
    {
        "id": "explicit_cut",
        "description": (
            "This card is the object of a cut/replace in the 50 characters "
            "before its name ('slot it in for this …', 'end up cutting …'). "
            "A wider window would also claim neighbouring keeps."
        ),
    },
    {
        "id": "off_identity",
        "description": (
            "Color identity is not a subset of the commander's. Cannot legally "
            "sit in his 99; the transcript match is a concept mention or a "
            "rejected legend already caught above."
        ),
    },
    {
        "id": "color_has_example",
        "description": (
            "Named as a per-color example ('white has Smothering Tithe'), not "
            "as a card going into this deck."
        ),
    },
    {
        "id": "shadowed_name",
        "description": (
            "A longer verified name occupies the same transcript locus "
            "(e.g. 'Nine Lives' inside 'Nine-Lives Familiar'). The short "
            "match is an n-gram shadow, not a second card."
        ),
    },
    {
        "id": "building_section",
        "description": (
            "First named at or after the end of commander selection — the "
            "building steps. Default bucket for a verified name that no "
            "rejection rule claimed."
        ),
    },
    {
        "id": "pre_build_mention",
        "description": (
            "Appears only before building starts and is not a rejected "
            "legend. Mentioned, not chosen."
        ),
    },
]


def squash(s: str) -> str:
    """Strip everything but letters and digits, lowercased.

    Two measured gaps made this necessary, and both were mine rather than the
    catalog's:

      commas    3,258 catalog names contain one ("Braids, Arisen Nightmare").
                An n-gram built from stripped words can never match them.
      spelling  The auto-subs write "Air Bender's Ascension"; the real card is
                "Airbender Ascension". Word boundaries and apostrophes differ.

    Squashing both sides removes punctuation and spacing from the comparison
    while leaving the letters intact, so a mangled transcript still resolves to
    a real Scryfall name -- and only to a real one.
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_card_names() -> dict[str, str]:
    """squashed -> canonical Scryfall name. Scryfall's catalog, not ours."""
    grammar = json.loads(GRAMMAR.read_text())
    names = (grammar.get("catalogs") or {}).get("card-names") or []
    out: dict[str, str] = {}
    for raw in names:
        n = str(raw).strip()
        if n:
            out.setdefault(squash(n), n)
    return out


def candidates(text: str, max_words: int = 5) -> set[str]:
    """Every plausible n-gram, squashed. Over-generates -- Scryfall filters."""
    out: set[str] = set()
    for line in text.splitlines() or [text]:
        # Auto-subs have no reliable capitalisation, so cannot filter on case.
        words = [w for w in re.findall(r"[A-Za-z0-9'’\-]+", line) if w]
        for i in range(len(words)):
            if words[i].lower() in STOP:
                continue
            for n in range(1, max_words + 1):
                if i + n > len(words):
                    break
                gram = squash(" ".join(words[i:i + n]))
                if len(gram) < 8:      # short squashes collide with common words
                    continue
                out.add(gram)
    return out


def section_bounds(transcript: str) -> tuple[int, int]:
    """Character offsets of the commander-selection window."""
    low = transcript.lower()
    start = low.find(CMDR_START_PHRASE)
    end = low.find(CMDR_END_PHRASE)
    if start < 0 or end < 0 or end <= start:
        raise ValueError(
            f"commander-selection bounds missing: start={start} end={end}")
    return start, end


def first_pos(name: str, transcript: str) -> int | None:
    """Earliest character offset where the card is recognisable in the text."""
    low = transcript.lower()
    forms = [name, name.replace(",", ""), name.split(",")[0]]
    # Auto-subs split Airbender; keep the measured mangling recoverable.
    if "Airbender" in name:
        forms += ["Air Bender's Ascension", "Air Bender Ascension",
                  "Er Bender's Ascension", "Er Bender Ascension"]
    if name.startswith("Nine-Lives"):
        forms += ["Nine Lives Familiar"]
    best: int | None = None
    for form in forms:
        i = low.find(form.lower())
        if i >= 0 and (best is None or i < best):
            best = i
    if best is not None:
        return best
    # Fallback: longest token ≥ 6 chars from the catalog name.
    parts = re.findall(r"[A-Za-z0-9']+", name)
    for part in sorted(parts, key=len, reverse=True):
        if len(part) < 6:
            continue
        i = low.find(part.lower())
        if i >= 0:
            return i
    return None


def _near(transcript: str, pos: int, pattern: str, window: int = 220) -> bool:
    if pos is None or pos < 0:
        return False
    lo = max(0, pos - window)
    hi = min(len(transcript), pos + window)
    return re.search(pattern, transcript[lo:hi], re.I) is not None


def _is_explicit_cut(transcript: str, pos: int | None) -> bool:
    """True only when THIS card is the object of a cut/replace.

    A ±220 window around 'end up cutting Suki' also covers Martyr's Cause and
    Nine-Lives Familiar in the same final-reveal paragraph — those are keeps.
    Require the cut verb to sit in the 50 characters immediately before the
    name so only the object of the cut matches.
    """
    if pos is None or pos < 0:
        return False
    pre = transcript[max(0, pos - 50):pos]
    return re.search(
        r"(?:end up cutting|cutting)\s+(?:this\s+)?$"
        r"|slot it in[\s\S]{0,40}\bfor(?:\s+this)?\s+$",
        pre, re.I,
    ) is not None


def classify(
    names: list[str],
    transcript: str,
    oracle: dict[str, dict],
    *,
    commander: str = COMMANDER,
    commander_colors: list[str] | None = None,
) -> list[dict]:
    """Assign each verified name to chosen/mentioned with the firing rule.

    Rules are checked in order; the first match wins and is recorded. No name
    list is consulted — only transcript position, type_line, color identity,
    and local phrasing.
    """
    colors = set(commander_colors or COMMANDER_COLORS)
    _cmdr_start, cmdr_end = section_bounds(transcript)
    positions = {n: first_pos(n, transcript) for n in names}

    # Longer names that share a locus with a shorter verified name claim the
    # locus; the short name is an n-gram shadow.
    shadows: set[str] = set()
    for short in names:
        sq = squash(short)
        spos = positions.get(short)
        if spos is None:
            continue
        for longer in names:
            if longer == short:
                continue
            lq = squash(longer)
            if len(lq) <= len(sq):
                continue
            if sq not in lq:
                continue
            lpos = positions.get(longer)
            if lpos is None:
                continue
            if abs(lpos - spos) <= 8:
                shadows.add(short)
                break

    rows: list[dict] = []
    for name in names:
        pos = positions.get(name)
        meta = oracle.get(name) or {}
        type_line = meta.get("type_line") or ""
        card_ci = set(meta.get("color_identity") or [])
        # Oracle cache from /cards/collection may store CI under the live card
        # shape; accept both.
        if not card_ci and isinstance(meta.get("ci"), (list, set)):
            card_ci = set(meta["ci"])

        rule = None
        bucket = None

        in_cmdr = pos is not None and pos < cmdr_end
        is_legend = "legendary creature" in type_line.lower()
        if (in_cmdr and is_legend and name != commander):
            rule, bucket = "rejected_commander", "mentioned"
        elif pos is not None and _near(
                transcript, pos,
                # Faithless: "But this is not card advantage". Broader
                # "it's not card advantage" later describes Body Launderer's
                # connive — that is a chosen card, not a counter-example.
                r"this is not.{0,40}advantage"):
            rule, bucket = "counter_example", "mentioned"
        elif _is_explicit_cut(transcript, pos):
            rule, bucket = "explicit_cut", "mentioned"
        elif card_ci and not card_ci <= colors:
            rule, bucket = "off_identity", "mentioned"
        elif pos is not None and re.search(
                r"\b(?:white|blue|black|red|green) has\s+$",
                transcript[max(0, pos - 40):pos], re.I):
            rule, bucket = "color_has_example", "mentioned"
        elif name in shadows:
            rule, bucket = "shadowed_name", "mentioned"
        elif pos is not None and pos >= cmdr_end:
            rule, bucket = "building_section", "chosen"
        else:
            rule, bucket = "pre_build_mention", "mentioned"

        rows.append({
            "name": name,
            "bucket": bucket,
            "rule": rule,
            "transcript_pos": pos,
            "type_line": type_line,
            "color_identity": sorted(card_ci),
        })
    return rows


def _oracle_for(names: list[str]) -> dict[str, dict]:
    """CI + type_line for classification. Prefer the committed cache."""
    cached = {}
    if ORACLE.exists():
        cached = json.loads(ORACLE.read_text())
    out: dict[str, dict] = {}
    missing = []
    for n in names:
        hit = cached.get(n)
        if not hit:
            missing.append(n)
            continue
        # Older cache rows lack color_identity — fill from a live fetch below.
        if "color_identity" not in hit:
            missing.append(n)
        out[n] = dict(hit)
    if not missing:
        return out

    # Live fill for CI when the cache predates CON-177. Uses the repo client.
    import asyncio
    sys.path.insert(0, str(REPO))
    from deckwright import app  # noqa: WPS433

    async def go():
        for i in range(0, len(missing), 75):
            chunk = missing[i:i + 75]
            res = await app.scry_post(
                "/cards/collection",
                {"identifiers": [{"name": n} for n in chunk]},
            )
            for card in res.get("data") or []:
                name = card["name"]
                row = out.get(name) or {
                    "name": name,
                    "text": card.get("oracle_text") or "",
                    "type_line": card.get("type_line") or "",
                    "card_faces": card.get("card_faces") or [],
                }
                row["type_line"] = card.get("type_line") or row.get("type_line") or ""
                row["color_identity"] = list(card.get("color_identity") or [])
                if not row.get("text"):
                    row["text"] = card.get("oracle_text") or ""
                out[name] = row
            for miss in res.get("not_found") or []:
                print(f"  UNRESOLVED {miss.get('name')}")

    asyncio.run(go())
    # Persist CI back into the oracle cache so offline classify stays offline.
    merged = dict(cached)
    for n, row in out.items():
        prev = dict(merged.get(n) or {})
        prev.update({k: row[k] for k in ("name", "text", "type_line",
                                         "card_faces", "color_identity")
                     if k in row})
        if "text" not in prev and row.get("text") is not None:
            prev["text"] = row["text"]
        merged[n] = prev
    ORACLE.write_text(json.dumps(merged, indent=2) + "\n")
    return out


def main() -> int:
    if not TRANSCRIPT.exists():
        print(f"missing {TRANSCRIPT} — commit the transcript first")
        return 1

    catalog = load_card_names()
    print(f"Scryfall card-names catalog: {len(catalog):,} names")

    transcript = TRANSCRIPT.read_text()
    grams = candidates(transcript)
    print(f"transcript n-grams: {len(grams):,}")

    hits = {catalog[g] for g in grams if g in catalog}

    # A one-word match is almost always a common noun that happens to be a card
    # ("Ponder", "Opt", "Counterspell"). Two-word-plus names are the signal.
    multi = sorted(n for n in hits if len(n.split()) >= 2)
    single = sorted(n for n in hits if len(n.split()) == 1)

    print(f"\nverified against Scryfall: {len(hits)} names "
          f"({len(multi)} multi-word, {len(single)} single-word)")

    oracle = _oracle_for(multi)
    rows = classify(multi, transcript, oracle,
                    commander=COMMANDER, commander_colors=COMMANDER_COLORS)
    chosen = [r for r in rows if r["bucket"] == "chosen"]
    mentioned = [r for r in rows if r["bucket"] == "mentioned"]

    print(f"\nchosen:    {len(chosen)}")
    for r in chosen:
        print(f"  {r['name']:<40} rule={r['rule']}")
    print(f"\nmentioned: {len(mentioned)}")
    for r in mentioned:
        print(f"  {r['name']:<40} rule={r['rule']}")

    cmdr_start, cmdr_end = section_bounds(transcript)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "youtube YJ8X8bO8jeY transcript, Commander Challenge",
        "commander": COMMANDER,
        "commander_colors": COMMANDER_COLORS,
        "bracket": 2,
        "method": ("n-grams from the transcript matched against Scryfall's "
                   "card-names catalog. Names come from Scryfall; the "
                   "transcript only selects. Unmatched candidates dropped."),
        "caveat": ("Auto-generated subtitles mangle names (Hei Bai appears as "
                   "Hayabusa/Haywire/Heiwei). This is a VERIFIED SUBSET of his "
                   "deck, not the whole 99, and absence here is not evidence "
                   "he omitted a card."),
        "sections": {
            "commander_selection": {
                "start_phrase": CMDR_START_PHRASE,
                "end_phrase": CMDR_END_PHRASE,
                "start": cmdr_start,
                "end": cmdr_end,
                "video_window": "0:37-5:49",
            },
        },
        "rules": RULES,
        "chosen": [{"name": r["name"], "rule": r["rule"],
                    "transcript_pos": r["transcript_pos"]}
                   for r in chosen],
        "mentioned": [{"name": r["name"], "rule": r["rule"],
                       "transcript_pos": r["transcript_pos"]}
                      for r in mentioned],
        "verified_multi_word": multi,
        "verified_single_word": single,
    }, indent=2) + "\n")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
