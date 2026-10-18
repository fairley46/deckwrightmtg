"""
resolve.py — one function that turns her words into the right Scryfall operator.

CON-149 / docs/SCRYFALL-GRAMMAR.md

Scryfall publishes ~62k enumerable catalog terms and ~1k sets. Resolution is a
lookup against that vocabulary, not a guess at whether a word is a creature
type, a keyword, or a set. Precedence is a hand-written table (nine collisions
exist; do not build disambiguation machinery).

    resolve_term("spiderman")  -> {operator: set, value: spm, ...}
    resolve_term("pirates")    -> {operator: t, value: pirate, ...}
    resolve_term("heists")     -> unresolved

Cache lives in data/scryfall_grammar.json. Refresh on version stamp or force.
After the cache is warm, resolve_term() is a dictionary hit — no network.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
GRAMMAR_PATH = REPO_DIR / "data" / "scryfall_grammar.json"
BEHAVIOURS_PATH = REPO_DIR / "data" / "otag_behaviours.json"

# Bump when the on-disk shape changes. Stale caches refetch.
GRAMMAR_VERSION = 1

# Catalogs → operator, in precedence order within each tier.
# Rule 2: supertypes / card-types
_RULE2 = (
    ("supertypes", "t"),
    ("card-types", "t"),
)
# Rule 3: creature / land / artifact / enchantment / pw / spell / battle types
_RULE3 = (
    ("creature-types", "t"),
    ("land-types", "t"),
    ("artifact-types", "t"),
    ("enchantment-types", "t"),
    ("planeswalker-types", "t"),
    ("spell-types", "t"),
    ("battle-types", "t"),
)
# Rule 4: keywords
_RULE4 = (
    ("keyword-abilities", "keyword"),
    ("keyword-actions", "keyword"),
    ("ability-words", "keyword"),
)
# Rule 5: watermarks
_RULE5 = (
    ("watermarks", "wm"),
)

_ALL_CATALOGS = (
    [c for c, _ in _RULE2]
    + [c for c, _ in _RULE3]
    + [c for c, _ in _RULE4]
    + [c for c, _ in _RULE5]
    + ["word-bank", "card-names", "artist-names"]
)

# Sets you can put in a deck. Art series / tokens / promos match names and lie.
# `eternal` is in: Transformers (bot) and Spider-Man Eternal are real cards,
# and the collision table says "transformers" resolves as a set, not a watermark.
_PLAYABLE_SET_TYPES = frozenset({
    "core", "expansion", "masters", "draft_innovation", "commander",
    "starter", "box", "duel_deck", "from_the_vault", "premium_deck",
    "arsenal", "spellbook", "eternal",
})

# Leading licensor / article stripped so "spiderman" hits "Marvel's Spider-Man"
# and "lord of the rings" can substring-match the LTR full name.
_SET_PREFIXES = ("marvel's ", "the ")

_UNRESOLVED = {
    "operator": None,
    "value": None,
    "catalog": None,
    "confidence": "unresolved",
}

# In-memory indexes, built once from the on-disk cache.
_grammar: dict[str, Any] | None = None
_type_index: dict[str, tuple[str, str]] = {}       # norm -> (canonical, catalog)
_keyword_index: dict[str, tuple[str, str]] = {}
_wm_index: dict[str, tuple[str, str]] = {}
_word_bank: set[str] = set()
_card_names: set[str] = set()
_artists: set[str] = set()
# exact set keys -> list of set dicts (playable preferred at resolve time)
_set_exact: dict[str, list[dict]] = {}
_set_list: list[dict] = []                         # for substring
# Behaviour aliases (she says "steal") → verified otag tag ("theft").
# Checked *before* catalogs — keyword:steal is one Final Fantasy card.
_behaviours: dict[str, str] = {}
_behaviour_verified: set[str] = set()
# Beginner strategy phrases → catalogued keywords ("go fast" → haste).
# CON-212: strategy colour, not a tribe and not an invented otag.
_keyword_aliases: dict[str, str] = {}
# Beginner vocabulary → creature type when Scryfall has no catalog entry (CON-234).
_type_aliases: dict[str, str] = {
    "cavalry": "Horse",
}
# Probed as behaviours and found dead. Must not fall through to word-bank
# and present o:stax as if the strategy matched (CON-149 fixture).
# Also beginner phrases with no verified tag (go wide, grow, aggro).
_dead_behaviours: set[str] = set()


def normalize(word: str) -> str:
    """Lowercase, strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]+", "", (word or "").lower())


def _morph_singulars(term: str) -> list[str]:
    """Candidate singular forms. Types need this (pirates→pirate); keywords do not."""
    t = term.strip().lower()
    if not t:
        return []
    out = [t]
    if t.endswith("ies") and len(t) > 4:
        out += [t[:-3] + "y", t[:-1]]
    if t.endswith("ves") and len(t) > 4:
        out += [t[:-3] + "f", t[:-3] + "fe"]
    if t.endswith("es") and len(t) > 3:
        out.append(t[:-2])
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        out.append(t[:-1])
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def expression(resolved: dict[str, Any]) -> str | None:
    """Scryfall clause for a resolved term, or None when unresolved."""
    op = resolved.get("operator")
    val = resolved.get("value")
    if not op or val is None or val == "":
        return None
    v = str(val)
    if op == "otag":
        return f"otag:{_quote(v.lower())}"
    if op == "set":
        return f"set:{v.lower()}"
    if op == "t":
        return f"t:{_quote(v.lower())}"
    if op == "keyword":
        return f"keyword:{_quote(v.lower())}"
    if op == "wm":
        return f"wm:{_quote(v.lower())}"
    if op == "o":
        return f"o:{_quote(v)}"
    if op == "name":
        # Regex name search — term as she typed it, punctuation-safe.
        safe = re.sub(r"[/\\]", "", v)
        return f"name:/{safe}/"
    if op == "a":
        return f"a:{_quote(v)}"
    return None


def compose_expressions(parts: list[dict[str, Any]]) -> str | None:
    """Compose resolved parts into one Scryfall clause.

    Sibling creature types OR together — "horses and knights" is
    `(t:horse OR t:knight)`, not the empty intersection `t:horse t:knight`.
    One behaviour ANDs with that group (thing + behaviour composition).
    Keywords alone pass through; they do not AND onto a type pool.
    None if any part cannot express.
    """
    types: list[str] = []
    behaviours: list[str] = []
    keywords: list[str] = []
    other: list[str] = []
    seen_types: set[str] = set()
    seen_keywords: set[str] = set()
    for p in parts:
        e = expression(p)
        if not e:
            return None
        op = p.get("operator")
        if op == "t":
            # CON-308: "pirates pirates and treasure" must not emit
            # (t:pirate OR t:pirate OR t:treasure).
            if e not in seen_types:
                seen_types.add(e)
                types.append(e)
        elif op == "otag":
            behaviours.append(e)
        elif op == "keyword":
            if e not in seen_keywords:
                seen_keywords.add(e)
                keywords.append(e)
        else:
            other.append(e)

    chunks: list[str] = []
    if types:
        chunks.append(types[0] if len(types) == 1
                      else f"({' OR '.join(types)})")
    if behaviours:
        # One behaviour — the composition payoff with the type group.
        chunks.append(behaviours[0])
    if not chunks and keywords:
        chunks.append(keywords[0] if len(keywords) == 1
                      else f"({' OR '.join(keywords)})")
    if not chunks and other:
        chunks.append(other[0])
    return " ".join(chunks) if chunks else None


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s


def _set_index_keys(name: str) -> list[str]:
    """Normalised keys for a set name, including leading-article variants."""
    keys: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        n = normalize(s)
        if n and n not in seen:
            seen.add(n)
            keys.append(n)

    add(name)
    lower = name.lower()
    for prefix in _SET_PREFIXES:
        if lower.startswith(prefix):
            add(name[len(prefix):])
    return keys


def _pick_set(candidates: list[dict]) -> dict | None:
    """Playable first, then largest card_count. Never an art series."""
    if not candidates:
        return None
    scored = []
    for st in candidates:
        playable = 1 if st.get("set_type") in _PLAYABLE_SET_TYPES else 0
        scored.append((playable, int(st.get("card_count") or 0), st))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    if not scored[0][0]:
        return None
    return scored[0][2]


def _build_indexes(payload: dict[str, Any]) -> None:
    global _type_index, _keyword_index, _wm_index
    global _word_bank, _card_names, _artists, _set_exact, _set_list

    catalogs = payload.get("catalogs") or {}
    _type_index = {}
    # Rule 2 then rule 3 — setdefault so card-types win over creature-types
    # for the nine collisions (hero, dungeon, planeswalker, …).
    for cat, _op in list(_RULE2) + list(_RULE3):
        for term in catalogs.get(cat) or []:
            _type_index.setdefault(normalize(term), (term, cat))

    _keyword_index = {}
    for cat, _op in _RULE4:
        for term in catalogs.get(cat) or []:
            _keyword_index.setdefault(normalize(term), (term, cat))

    _wm_index = {}
    for cat, _op in _RULE5:
        for term in catalogs.get(cat) or []:
            _wm_index.setdefault(normalize(term), (term, cat))

    _word_bank = {normalize(t) for t in (catalogs.get("word-bank") or []) if t}
    _card_names = {normalize(t) for t in (catalogs.get("card-names") or []) if t}
    _artists = {normalize(t) for t in (catalogs.get("artist-names") or []) if t}
    # Keep a norm→canonical map for word-bank so o: uses the catalog spelling.
    # Stash on the payload-derived dicts via side tables:
    global _word_bank_canon, _card_name_canon, _artist_canon
    _word_bank_canon = {normalize(t): t for t in (catalogs.get("word-bank") or []) if t}
    _card_name_canon = {normalize(t): t for t in (catalogs.get("card-names") or []) if t}
    _artist_canon = {normalize(t): t for t in (catalogs.get("artist-names") or []) if t}

    _set_exact = {}
    _set_list = []
    for st in payload.get("sets") or []:
        if not isinstance(st, dict) or not st.get("code"):
            continue
        entry = {
            "code": str(st["code"]).lower(),
            "name": str(st.get("name") or ""),
            "card_count": int(st.get("card_count") or 0),
            "set_type": str(st.get("set_type") or ""),
        }
        _set_list.append(entry)
        for key in _set_index_keys(entry["name"]):
            _set_exact.setdefault(key, []).append(entry)
        # She typed the set code itself.
        _set_exact.setdefault(normalize(entry["code"]), []).append(entry)


_word_bank_canon: dict[str, str] = {}
_card_name_canon: dict[str, str] = {}
_artist_canon: dict[str, str] = {}


def load_behaviours(path: pathlib.Path | None = None) -> dict[str, str]:
    """Load verified otag: aliases. Missing/corrupt → empty (no invented tags)."""
    global _behaviours, _behaviour_verified, _dead_behaviours, _keyword_aliases
    path = path or BEHAVIOURS_PATH
    _behaviours = {}
    _behaviour_verified = set()
    _keyword_aliases = {}
    _dead_behaviours = set()
    if not path.is_file():
        return _behaviours
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _behaviours
    verified = data.get("verified") or {}
    _behaviour_verified = {str(k).lower() for k in verified if verified.get(k)}
    aliases = data.get("aliases") or {}
    for alias, tag in aliases.items():
        tag_l = str(tag).lower()
        # Only keep aliases whose target was probed and returned cards.
        if tag_l in _behaviour_verified:
            _behaviours[normalize(str(alias))] = tag_l
    # Strategy phrases → real keyword catalog entries only (CON-212).
    for alias, kw in (data.get("keyword_aliases") or {}).items():
        kn = normalize(str(kw))
        if kn and kn in _keyword_index:
            canon, _cat = _keyword_index[kn]
            _keyword_aliases[normalize(str(alias))] = canon
    _dead_behaviours = {normalize(str(x)) for x in (data.get("dead") or []) if x}
    return _behaviours


def load_grammar(path: pathlib.Path | None = None, force: bool = False) -> dict[str, Any]:
    """Load the cached vocabulary into memory. No network."""
    global _grammar
    path = path or GRAMMAR_PATH
    if _grammar is not None and not force:
        return _grammar
    if not path.is_file():
        _grammar = {"version": GRAMMAR_VERSION, "catalogs": {}, "sets": []}
        _build_indexes(_grammar)
        load_behaviours()
        return _grammar
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        _grammar = {"version": GRAMMAR_VERSION, "catalogs": {}, "sets": []}
        _build_indexes(_grammar)
        load_behaviours()
        return _grammar
    if not isinstance(data, dict):
        _grammar = {"version": GRAMMAR_VERSION, "catalogs": {}, "sets": []}
        _build_indexes(_grammar)
        load_behaviours()
        return _grammar
    _grammar = data
    _build_indexes(data)
    load_behaviours()
    return _grammar


def grammar_is_fresh(path: pathlib.Path | None = None) -> bool:
    """True when the on-disk cache matches this code's version and has sets."""
    path = path or GRAMMAR_PATH
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if data.get("version") != GRAMMAR_VERSION:
        return False
    catalogs = data.get("catalogs") or {}
    sets = data.get("sets") or []
    return bool(sets) and bool(catalogs.get("creature-types")) and bool(
        catalogs.get("keyword-abilities")
    )


async def refresh_grammar(
    scry: Callable[..., Awaitable[dict]],
    path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Fetch catalogs + /sets from Scryfall and write data/scryfall_grammar.json."""
    path = path or GRAMMAR_PATH
    catalogs: dict[str, list[str]] = {}
    for cat in _ALL_CATALOGS:
        try:
            j = await scry(f"/catalog/{cat}")
        except Exception:
            continue
        names = [str(v) for v in (j.get("data") or []) if isinstance(v, str)]
        if names:
            catalogs[cat] = names

    sets: list[dict] = []
    try:
        j = await scry("/sets")
        for st in j.get("data") or []:
            if not isinstance(st, dict) or not st.get("code"):
                continue
            sets.append({
                "code": str(st["code"]).lower(),
                "name": str(st.get("name") or ""),
                "card_count": int(st.get("card_count") or 0),
                "set_type": str(st.get("set_type") or ""),
            })
    except Exception:
        pass

    # Never overwrite a good cache with a thin fetch.
    if not (catalogs.get("creature-types") and catalogs.get("keyword-abilities") and sets):
        if path.is_file():
            return load_grammar(path, force=True)
        raise RuntimeError("Scryfall grammar fetch returned empty catalogs/sets")

    payload = {
        "version": GRAMMAR_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "catalogs": catalogs,
        "sets": sets,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    return load_grammar(path, force=True)


async def ensure_grammar(
    scry: Callable[..., Awaitable[dict]] | None = None,
    path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Load cache, or refresh if missing/stale and a scry callable is provided."""
    path = path or GRAMMAR_PATH
    if grammar_is_fresh(path):
        return load_grammar(path)
    if scry is None:
        return load_grammar(path)
    return await refresh_grammar(scry, path)


def resolve_term(word: str) -> dict[str, Any]:
    """Map one theme word to a Scryfall operator.

    Returns {operator, value, catalog, confidence}. confidence is 'unresolved'
    when the grammar cannot place the word — that is not a zero, and callers
    must not invent a generic query and present it as a match.

    Two vocabularies (docs/SCRYFALL-GRAMMAR.md at b629459):
      0. behaviour — verified otag: aliases (steal→theft). Checked first so
         keyword:steal cannot trap us on one Final Fantasy card.
      1–9. things — sets, types, keywords, watermarks, word-bank, names, artists.
    """
    if _grammar is None:
        load_grammar()

    raw = (word or "").strip()
    if not raw:
        return dict(_UNRESOLVED)

    needle = normalize(raw)
    if not needle:
        return dict(_UNRESOLVED)

    # 0. Behaviour — before catalogs. "steal" must not become keyword:steal.
    for form in _morph_singulars(raw):
        n = normalize(form)
        tag = _behaviours.get(n)
        if tag:
            return {
                "operator": "otag",
                "value": tag,
                "catalog": "otag",
                "confidence": "exact" if n == needle else "singular",
            }
    if needle in _behaviours:
        return {
            "operator": "otag",
            "value": _behaviours[needle],
            "catalog": "otag",
            "confidence": "exact",
        }
    # 0b. Beginner strategy → keyword (CON-212). Before sets so "go fast"
    # cannot fall through, and before type catalogs so it stays strategy.
    if needle in _keyword_aliases:
        canon = _keyword_aliases[needle]
        cat = (_keyword_index.get(normalize(canon)) or (canon, "keyword-abilities"))[1]
        return {
            "operator": "keyword",
            "value": canon,
            "catalog": cat,
            "confidence": "exact",
        }
    # Known-dead behaviour words: stop here. Falling through to word-bank
    # would dress o:stax up as a match when otag:stax does not exist.
    if needle in _dead_behaviours or any(
        normalize(f) in _dead_behaviours for f in _morph_singulars(raw)
    ):
        return dict(_UNRESOLVED)

    # 1. Exact set name (normalised), including stripped licensor/article keys.
    # CON-292: try morphological singulars too — "hobbits" must hit "The Hobbit"
    # the same way "pirates" hits t:pirate. Types still win collisions below
    # only when no set key matches any form.
    for form in _morph_singulars(raw):
        n = normalize(form)
        if not n:
            continue
        hit = _pick_set(_set_exact.get(n) or [])
        if hit:
            return {
                "operator": "set",
                "value": hit["code"],
                "catalog": "sets",
                "confidence": "exact" if n == needle else "singular",
            }

    # Type candidates: original + morphological singulars (pirates→pirate).
    # Keyword/watermark candidates: exact normalised form only — "heists" is
    # not "heist", and pretending otherwise is how an unresolved theme gets
    # dressed up as a keyword match.
    type_candidates: list[tuple[str, str]] = []  # (norm, confidence)
    seen_c: set[str] = set()
    for form in _morph_singulars(raw):
        n = normalize(form)
        if not n or n in seen_c:
            continue
        seen_c.add(n)
        conf = "exact" if n == needle else "singular"
        type_candidates.append((n, conf))
    # Also try the fully normalised phrase as a single token (first strike).
    if needle not in seen_c:
        type_candidates.insert(0, (needle, "exact"))

    # 2–3. Types
    for n, conf in type_candidates:
        if n in _type_index:
            canon, cat = _type_index[n]
            return {
                "operator": "t",
                "value": canon,
                "catalog": cat,
                "confidence": conf,
            }

    # 3b. Type aliases — before word-bank so "cavalry" is t:horse, not o:cavalry.
    for form in _morph_singulars(raw):
        n = normalize(form)
        if n in _type_aliases:
            canon = _type_aliases[n]
            cat = (_type_index.get(normalize(canon)) or (canon, "creature-types"))[1]
            return {
                "operator": "t",
                "value": canon,
                "catalog": cat,
                "confidence": "exact" if n == needle else "singular",
            }
    if needle in _type_aliases:
        canon = _type_aliases[needle]
        cat = (_type_index.get(normalize(canon)) or (canon, "creature-types"))[1]
        return {
            "operator": "t",
            "value": canon,
            "catalog": cat,
            "confidence": "exact",
        }

    # 4. Keywords — exact only
    if needle in _keyword_index:
        canon, cat = _keyword_index[needle]
        return {
            "operator": "keyword",
            "value": canon,
            "catalog": cat,
            "confidence": "exact",
        }

    # 5. Watermarks
    if needle in _wm_index:
        canon, cat = _wm_index[needle]
        return {
            "operator": "wm",
            "value": canon,
            "catalog": cat,
            "confidence": "exact",
        }

    # 6. Substring on a set name — largest playable set wins.
    # Require a meaningful needle so "a" / "the" residue cannot match.
    # CON-292: singular forms too (fallouts → Fallout) — after types/keywords
    # so angels/ninjas/doctors stay t:.
    seen_sub: set[str] = set()
    for form in _morph_singulars(raw):
        n = normalize(form)
        if not n or len(n) < 4 or n in seen_sub:
            continue
        seen_sub.add(n)
        sub_hits = [
            st for st in _set_list
            if n in normalize(st["name"])
            or any(n in k for k in _set_index_keys(st["name"]))
        ]
        hit = _pick_set(sub_hits)
        if hit:
            return {
                "operator": "set",
                "value": hit["code"],
                "catalog": "sets",
                "confidence": "substring" if n == needle else "singular",
            }

    # 7. Word-bank — exact normalised form of what she typed, not a singular.
    if needle in _word_bank:
        return {
            "operator": "o",
            "value": _word_bank_canon.get(needle, raw.lower()),
            "catalog": "word-bank",
            "confidence": "exact",
        }

    # 8. Card-names
    if needle in _card_names:
        return {
            "operator": "name",
            "value": _card_name_canon.get(needle, raw),
            "catalog": "card-names",
            "confidence": "exact",
        }

    # 9. Artist-names
    if needle in _artists:
        return {
            "operator": "a",
            "value": _artist_canon.get(needle, raw),
            "catalog": "artist-names",
            "confidence": "exact",
        }

    return dict(_UNRESOLVED)


def type_vocab() -> dict[str, str]:
    """lower→canonical type map for app.load_vocab compatibility."""
    if _grammar is None:
        load_grammar()
    return {canon.lower(): canon for canon, _cat in _type_index.values()}


def keyword_vocab() -> dict[str, str]:
    """lower→canonical keyword map for app.load_vocab compatibility."""
    if _grammar is None:
        load_grammar()
    return {canon.lower(): canon for canon, _cat in _keyword_index.values()}


# Content-word stop list for phrase themes — kept here so resolve_theme does
# not import app.py (app imports resolve).
_THEME_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "that", "this", "these", "those", "who", "which", "into", "from", "by",
    "their", "its", "your", "my", "our", "be", "is", "are", "was", "were",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "not", "no", "as",
    "at", "than", "then", "them", "they", "we", "you", "it", "i", "me",
    "us", "him", "her", "his", "hers", "when", "where", "while", "about",
    "over", "under", "again", "more", "most", "some", "any", "all", "own",
    "onto", "out", "up", "down", "off", "so", "if", "but",
})

_BROAD_TYPES = frozenset({
    "creature", "artifact", "enchantment", "land", "instant", "sorcery",
    "planeswalker", "battle", "kindred", "tribal", "legendary", "basic",
    "snow", "world", "token", "permanent", "spell", "card",
})


def content_words(theme: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (theme or "").lower())
    return [w for w in words if w not in _THEME_STOP and len(w) > 1]


def resolve_theme(theme: str) -> dict[str, Any]:
    """Resolve a full theme, composing thing + behaviour when both appear.

    Returns {
      parts: [resolve_term hits used in the primary expression],
      expression: "t:pirate otag:theft" | None,
      unresolved: [words that did not resolve],
    }

    "pirates that steal" → t:pirate otag:theft (15 cards), not two half-answers.
    """
    if _grammar is None:
        load_grammar()

    raw = (theme or "").strip()
    if not raw:
        return {"parts": [], "expression": None, "unresolved": []}

    words = content_words(raw)
    # Whole phrase as one unit, FIRST and regardless of word count. A
    # multi-word catalog hit is always stronger than splitting: "first strike"
    # is a keyword, and splitting it gives o:first. "double strike" is worse —
    # splitting resolves "double" to the keyword ACTION Double, a different
    # thing entirely. Composed themes are unaffected because they do not match
    # any catalog as a phrase ("pirates that steal" -> None), so they still
    # fall through to per-word resolution below.
    whole = resolve_term(raw)
    if whole.get("operator"):
        return {
            "parts": [whole],
            "expression": expression(whole),
            "unresolved": [],
        }

    if len(words) <= 1:
        if whole.get("operator"):
            return {
                "parts": [whole],
                "expression": expression(whole),
                "unresolved": [],
            }
        return {"parts": [], "expression": None, "unresolved": [raw.lower()]}

    behaviours: list[dict[str, Any]] = []
    type_things: list[dict[str, Any]] = []
    broad: list[dict[str, Any]] = []
    keywords: list[dict[str, Any]] = []
    sets: list[dict[str, Any]] = []
    weak: list[tuple[str, dict[str, Any]]] = []
    unresolved: list[str] = []
    used = [False] * len(words)

    # Multi-word spans before per-word lookup — "gain life" is otag:lifegain,
    # "go fast" is keyword:haste, "go wide" is dead/ask (CON-157 / CON-212).
    # Longest match first so "card draw" beats "card".
    for length in range(len(words), 1, -1):
        for i in range(len(words) - length + 1):
            if any(used[j] for j in range(i, i + length)):
                continue
            span = " ".join(words[i:i + length])
            if normalize(span) in _dead_behaviours:
                unresolved.append(span)
                for j in range(i, i + length):
                    used[j] = True
                continue
            rw = resolve_term(span)
            if rw.get("operator") == "otag":
                behaviours.append(rw)
                for j in range(i, i + length):
                    used[j] = True
            elif rw.get("operator") == "keyword":
                keywords.append(rw)
                for j in range(i, i + length):
                    used[j] = True

    for i, w in enumerate(words):
        if used[i]:
            continue
        rw = resolve_term(w)
        op = rw.get("operator")
        if op == "otag":
            behaviours.append(rw)
        elif op == "t" and str(rw.get("value") or "").lower() in _BROAD_TYPES:
            broad.append(rw)
        elif op == "t":
            type_things.append(rw)
        elif op == "keyword":
            # Combat-keyword menus ("trample, first strike, haste") are
            # strategy colour, not a second tribe. Keep them out of the
            # type pool so they cannot AND the theme to zero (CON-209 / CON-212).
            keywords.append(rw)
        elif op in ("set", "wm"):
            sets.append(rw)
        elif op:
            weak.append((w, rw))
        else:
            unresolved.append(w)

    # CON-209: every named tribe stays in parts — compose_expressions ORs them.
    # CON-212 kept [primary_types[0]] here; that is the pre-CON-209 behaviour
    # and would drop sibling tribes ("horses and knights" -> horses only), so
    # main's list form wins. The alias pack feeds this, it does not narrow it.
    primary_types = type_things or broad
    parts: list[dict[str, Any]] = []
    if primary_types and behaviours:
        # Every named thing + one behaviour — the composition payoff.
        parts = list(primary_types) + [behaviours[0]]
    elif primary_types:
        parts = list(primary_types)
    elif behaviours:
        parts = [behaviours[0]]
    elif sets:
        parts = [sets[0]]
    elif keywords:
        # Strategy alone is still a real resolve — just not AND'd onto a tribe.
        parts = [keywords[0]]
    elif weak:
        parts = [weak[0][1]]
    elif whole.get("operator"):
        parts = [whole]

    # CON-234: weak hits dropped for a type pool are not silent.
    if primary_types and weak:
        for w, _rw in weak:
            if w not in unresolved:
                unresolved.append(w)

    return {
        "parts": parts,
        "expression": compose_expressions(parts) if parts else None,
        "unresolved": unresolved,
        # CON-212: callers that want strategy colour read this; theme pool
        # expression above deliberately omits keywords when a type is present.
        "keywords": keywords,
    }
