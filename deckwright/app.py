"""
Deckwright — a local Commander deckbuilding assistant.

Flow:  vibe -> interrogation -> commander -> Scryfall-grounded 99 -> Archidekt paste

The model never writes card names from memory. It writes Scryfall *queries*.
Every card in the final list was read off a real Scryfall response and
validated against it before it made the deck. That's the whole trick.

Run:  ./run.sh   (Anthropic Claude via `claude` CLI or API key; see llm.py)
      python -m deckwright
"""

import os
import shutil, json, time, asyncio, pathlib, re, random
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from deckwright import sim
from deckwright import mana
from deckwright import overview
from deckwright import plan_graph
from deckwright import llm
from deckwright import import_deck
from deckwright import substitution
from deckwright import card_score
from deckwright import precons
from deckwright import christen as christening
from deckwright import thesis_check
from deckwright import why as why_card
from deckwright import coverage
from deckwright import bake
from deckwright import precon_bake
from deckwright import tweak as tweak_mod
from deckwright import swap
from deckwright import pressure
from deckwright import mechanics
from deckwright import suggest
from deckwright import deck_state
from deckwright import precon_delta
from deckwright import precon_upgrade
from deckwright import resolve
from deckwright import release_calendar
from deckwright import synergy
from deckwright import boot
from deckwright import bug_report
from deckwright import commanders as cmd_kb
from deckwright import playstyles
from deckwright import corpus
from deckwright import commander_strategy
from deckwright import strategy_audit
from deckwright import strategy_steer
from deckwright import edhrec_seed
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from deckwright import instance
from deckwright import demo_guard
from deckwright import safe_paths
from deckwright import errors

MODEL = "claude-sonnet-5"
SCRYFALL = "https://api.scryfall.com"
UA = {"User-Agent": "Deckwright/1.0 (local personal deckbuilder)", "Accept": "application/json"}
SAVE_DIR = instance.home_dir()
SAVE_DIR.mkdir(exist_ok=True)

# LLM adapter: Anthropic Claude via CLI by default, Messages API with
# DECKWRIGHT_LLM=api. All model calls go through llm.py.
client, LLM_VIA = llm.make_client()


@asynccontextmanager
async def _lifespan(app_: FastAPI):
    instance.require_boot()
    # load the local card corpus before anything asks Scryfall.
    # Missing file → empty Corpus; lookups fall through to live until boot
    # cadence runs `python -m corpus` (missing bulk triggers refresh)
    # or Sunday cadence refreshes it.
    try:
        corp = corpus.load_default_corpus()
        st = corp.status()
        if st.get("loaded"):
            print(
                f"  corpus -> {st.get('card_count')} cards "
                f"(updated {st.get('updated_at') or 'UNKNOWN'})"
            )
            if st.get("stale"):
                print(
                    f"  corpus STALE age={st.get('age_days')}d — "
                    "run: python -m corpus   (or wait for Sunday cadence)"
                )
        else:
            print(
                "  corpus -> not loaded "
                f"({st.get('path')}) — live Scryfall until refreshed"
            )
    except Exception as e:
        print(f"  corpus -> load failed: {type(e).__name__}: {e}")
    # warm vocab (and refresh stale data) while the brand veil waits.
    # probe Scryfall once so a challenged card source is named before
    # she types a wish. Local pages stay ready either way.
    boot.start_background(
        asyncio.get_running_loop(),
        warm=load_vocab,
        card_source_probe=probe_card_source,
    )
    yield


app = FastAPI(lifespan=_lifespan)
demo_guard.install_demo_guards(app)

# Scryfall asks for <=10 req/sec. We stay well under.
_gate = asyncio.Semaphore(4)
_last = [0.0]
_SCRYFALL_MIN_GAP = 0.2          # headroom under 10 req/s under full-suite load
_SCRYFALL_RETRIES = 5

# named refusals. Challenge / rate limit / timeout are different
# sentences — do not collapse them. Copy is declarative; the page paints it.
SCRYFALL_REFUSAL_COPY = {
    "challenge": {
        "what": "Scryfall is asking us to verify we're not a robot.",
        "how": "This usually clears on its own — try again in a few minutes.",
    },
    "rate_limit": {
        "what": "Scryfall is rate-limiting us.",
        "how": "Wait a few seconds and try again — nothing you typed was lost.",
    },
    "maintenance": {
        "what": "Scryfall is offline for maintenance.",
        "how": "That's planned downtime on their side, not your deck. Try again later.",
    },
    "timeout": {
        "what": "Scryfall timed out before answering.",
        "how": "The card database did not respond in time. Try again.",
    },
    "unreachable": {
        "what": "Scryfall didn't answer.",
        "how": "That's the card database, not your deck. Wait a moment and try again.",
    },
    "transport": {
        "what": "Scryfall couldn't be reached.",
        "how": "That's the card database, not your deck. Wait a moment and try again.",
    },
}


class ScryfallError(Exception):
    """Scryfall request failed — not a zero-result query.

    `kind` names the refusal so the page can say the right sentence:
      challenge | maintenance | rate_limit | timeout | unreachable | transport
    """

    def __init__(self, status: int, detail: str = "", *, kind: str = "unreachable"):
        self.status = status
        self.kind = kind or "unreachable"
        msg = f"Scryfall HTTP {status}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


def scryfall_refusal_copy(kind: str) -> dict[str, str]:
    """User-facing what/how for a Scryfall refusal kind."""
    return dict(SCRYFALL_REFUSAL_COPY.get(kind) or SCRYFALL_REFUSAL_COPY["unreachable"])


def scryfall_refusal_payload(err: "ScryfallError") -> dict:
    """Structured body for API + UI. Distinct kinds, never a generic 500."""
    # The raw error text (transport messages, URLs) goes to the server log
    # only; the body carries the known kind, the HTTP status and fixed copy.
    errors.log_exception("scryfall", err)
    kind = errors.known_kind(err.kind, SCRYFALL_REFUSAL_COPY, default="unreachable")
    status = errors.plain_status(err.status)
    copy = scryfall_refusal_copy(kind)
    return {
        "ok": False,
        "error": "scryfall_refused",
        "kind": kind,
        "status": status,
        "what": copy["what"],
        "how": copy["how"],
        "detail": f"Scryfall refused ({kind}, HTTP {status})" if status
                  else f"Scryfall refused ({kind})",
    }


def _http_status_for_refusal(kind: str) -> int:
    return {
        "challenge": 503,
        "maintenance": 503,
        "rate_limit": 429,
        "timeout": 504,
        "unreachable": 502,
        "transport": 502,
    }.get(kind, 502)


def _is_maintenance(response) -> bool:
    """Scryfall's own maintenance page: 503 whose body says so.

    Added during review while Scryfall was genuinely in a maintenance window —
    /cards/named answered 200 while /cards/search returned 503 with a page
    reading "Offline for Maintenance". Worth its own kind for two reasons.

    It is knowable. The body says exactly what is wrong, and "Scryfall is
    offline for maintenance" is a better sentence than "Scryfall didn't
    answer" — one tells her to come back later, the other invites her to
    hammer retry.

    And it is not worth retrying. 503 sits in the retry list, so a maintenance
    window costs three attempts and the full backoff before failing anyway.
    A planned outage will not clear inside seven seconds.

    match only Scryfall's phrase. A bare "maintenance" anywhere in the
    body (CDN boilerplate, upstream 503 pages) must still retry — wrongly
    refusing a two-second blip costs her the build; wrongly retrying a real
    outage costs seven seconds.
    """
    if getattr(response, "status_code", None) != 503:
        return False
    try:
        text = (response.text or "")[:2000].lower()
    except Exception:
        return False
    return "offline for maintenance" in text


def _is_cloudflare_challenge(response) -> bool:
    """Cloudflare bot-check: 403 + cf-mitigated:challenge, or 'Just a moment'."""
    if getattr(response, "status_code", None) != 403:
        return False
    headers = getattr(response, "headers", None) or {}
    mitigated = str(
        headers.get("cf-mitigated") or headers.get("CF-Mitigated") or ""
    ).lower()
    if "challenge" in mitigated:
        return True
    try:
        text = response.text or ""
    except Exception:
        return False
    return "just a moment" in text.lower()


async def _scryfall_request(method: str, path: str, **kwargs) -> dict:
    """Rate-gated Scryfall call. 404 → empty data; 429/5xx → retry then raise.

    a Cloudflare challenge raises immediately as kind=challenge.
    More requests extend a challenge — do not retry it. Rate limit and
    timeout keep today's budget and get their own kinds.
    """
    url = f"{SCRYFALL}{path}"
    delay = 1.0
    last_status = 0
    for _ in range(_SCRYFALL_RETRIES):
        async with _gate:
            gap = time.time() - _last[0]
            if gap < _SCRYFALL_MIN_GAP:
                await asyncio.sleep(_SCRYFALL_MIN_GAP - gap)
            _last[0] = time.time()
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    if method == "GET":
                        r = await c.get(url, headers=UA, **kwargs)
                    else:
                        r = await c.post(url, headers=UA, **kwargs)
            except httpx.TimeoutException as e:
                # No new retries — timeouts were never retried.
                raise ScryfallError(0, "timed out", kind="timeout") from e
            except httpx.HTTPError as e:
                raise ScryfallError(
                    0, f"transport: {type(e).__name__}: {e}",
                    kind="transport",
                ) from e
        if r.status_code == 404:
            return {"data": []}
        if r.status_code == 200:
            return r.json()
        last_status = r.status_code
        if _is_cloudflare_challenge(r):
            raise ScryfallError(403, "cloudflare challenge", kind="challenge")
        if _is_maintenance(r):
            raise ScryfallError(503, "offline for maintenance", kind="maintenance")
        if r.status_code in (429, 500, 502, 503, 504):
            wait = delay
            if r.status_code == 429:
                try:
                    wait = max(wait, float(r.headers.get("Retry-After", wait)))
                except (TypeError, ValueError):
                    pass
            await asyncio.sleep(wait)
            delay = min(delay * 2, 30)
            continue
        # keeps the taxonomy: a caller has to be able to tell a
        # challenge from a rate limit from a plain unreachable, because they
        # clear on completely different timescales and deserve different words.
        # The corpus path's separate 403 branch is not needed — _is_cloudflare_challenge
        # above already catches cf-mitigated before this point.
        raise ScryfallError(
            r.status_code, f"HTTP {r.status_code}", kind="unreachable")
    kind = "rate_limit" if last_status == 429 else "unreachable"
    raise ScryfallError(
        last_status, f"unreachable after {_SCRYFALL_RETRIES} tries", kind=kind)


def _corpus_refuse(detail: str) -> None:
    """Raise when local-only mode cannot answer — unknown, not empty."""
    raise ScryfallError(0, f"corpus: {detail}")


async def scry(path: str, **params) -> dict:
    """GET card data — local corpus first, live Scryfall as fallback.

    otag:/art: and any query the index cannot parse stay on the live path.
    a composite with local AND-conjuncts serves those locally; live is
    required only for the otag:/art: remainder. A partial local answer is
    incomplete — never returned as a complete smaller set when live refuses.
    A missing corpus falls through to live unless DECKWRIGHT_CORPUS=local-only.
    """
    if not corpus.corpus_disabled():
        r = corpus.route_get(
            corpus.get_corpus(), path, params, meters=corpus.meters(),
        )
        if r.status in (corpus.HIT, corpus.EMPTY):
            return r.payload
        # INCOMPLETE: local conjuncts were served (meters already counted).
        # Completing the AND still needs live — never return the local subset
        # as a match. local-only cannot finish either.
        if corpus.local_only_mode():
            _corpus_refuse(r.detail or r.status)
        # UNSUPPORTED / INCOMPLETE / MISSING → live path below.
    else:
        corpus.meters().live_needed += 1
    result = await _scryfall_request("GET", path, params=params)
    corpus.meters().live_done += 1
    return result


async def scry_post(path: str, json: dict) -> dict:
    """POST to Scryfall (e.g. /cards/collection). Local corpus first."""
    if not corpus.corpus_disabled():
        r = corpus.route_post(
            corpus.get_corpus(), path, json, meters=corpus.meters(),
        )
        if r.status in (corpus.HIT, corpus.EMPTY):
            return r.payload
        if corpus.local_only_mode():
            _corpus_refuse(r.detail or r.status)
    else:
        corpus.meters().live_needed += 1
    result = await _scryfall_request("POST", path, json=json)
    corpus.meters().live_done += 1
    return result


async def probe_card_source() -> dict:
    """One light Scryfall look for boot preflight.

    Saved decks and local pages stay available either way — this only names
    whether live card lookups will work before she types a wish.
    """
    try:
        j = await scry("/cards/named", exact="Sol Ring")
    except ScryfallError as e:
        payload = scryfall_refusal_payload(e)
        return {
            "ok": False,
            "kind": payload["kind"],
            "status": payload["status"],
            "what": payload["what"],
            "how": payload["how"],
            "error": payload["error"],
        }
    except Exception as e:
        errors.log_exception("scryfall probe", e)
        return {
            "ok": False,
            "kind": "transport",
            "status": 0,
            "what": scryfall_refusal_copy("transport")["what"],
            "how": scryfall_refusal_copy("transport")["how"],
            "error": "scryfall_refused",
            "detail": f"Scryfall probe failed ({type(e).__name__})",
        }
    name = (j or {}).get("name") if isinstance(j, dict) else None
    if not name:
        return {
            "ok": False,
            "kind": "unreachable",
            "status": 0,
            "what": scryfall_refusal_copy("unreachable")["what"],
            "how": scryfall_refusal_copy("unreachable")["how"],
            "error": "scryfall_refused",
        }
    return {"ok": True, "kind": None, "status": 200}


@app.exception_handler(ScryfallError)
async def _scryfall_error_handler(_request, exc: ScryfallError):
    """a refused card source is a named state, not a generic 500."""
    return JSONResponse(
        scryfall_refusal_payload(exc),
        status_code=_http_status_for_refusal(exc.kind),
    )


# ---------------------------------------------------------------- vocabulary
#
# resolution is resolve_term() against Scryfall's published catalogs
# and set list (docs/SCRYFALL-GRAMMAR.md). Guessing `t:` for every theme is how
# "spiderman" found 0 cards next to a Spider-Man commander.
#
# TYPE_CATALOGS / KEYWORD_CATALOGS remain as the type/keyword tiers of that
# grammar — kept for tests and any caller that still reads the names.
TYPE_CATALOGS = ["creature-types", "card-types", "supertypes", "artifact-types",
                 "land-types", "enchantment-types", "planeswalker-types",
                 "spell-types", "battle-types"]
KEYWORD_CATALOGS = ["keyword-abilities", "keyword-actions", "ability-words"]

VOCAB_CACHE = SAVE_DIR / "vocab.json"
VOCAB_MAX_AGE = 7 * 86400          # catalogs only change when a set releases
_vocab: dict[str, dict[str, str]] = {}

# verified otag: behaviour vocabulary (no Scryfall /catalog for these).
BEHAVIOUR_TAGS_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "behaviour_tags.json"
_behaviour_index: dict[str, dict[str, Any]] = {}  # norm_term → {tag, cards, …}
_behaviour_loaded = False


async def load_vocab(force: bool = False) -> dict[str, dict[str, str]]:
    """Load type/keyword maps from the grammar cache (refreshing if needed)."""
    global _vocab
    if _vocab and not force:
        return _vocab

    if force:
        await resolve.refresh_grammar(scry)
    else:
        await resolve.ensure_grammar(scry)

    built = {
        "type": resolve.type_vocab(),
        "keyword": resolve.keyword_vocab(),
    }
    if built["type"] and built["keyword"]:
        _vocab = built
        try:
            VOCAB_CACHE.write_text(json.dumps(built))
        except OSError:
            pass
    elif VOCAB_CACHE.exists():
        try:
            _vocab = json.loads(VOCAB_CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            _vocab = built
    else:
        _vocab = built
    return _vocab


def norm_term(word: str) -> str:
    """Lowercase and strip non-alphanumerics — shared with the grammar's set lookup."""
    return "".join(ch for ch in (word or "").lower() if ch.isalnum())


def load_behaviour_tags(force: bool = False) -> dict[str, dict[str, Any]]:
    """Load verified otag: behaviour vocabulary into a synonym → tag index."""
    global _behaviour_index, _behaviour_loaded
    if _behaviour_loaded and not force and _behaviour_index:
        return _behaviour_index
    index: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(BEHAVIOUR_TAGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        _behaviour_index = {}
        _behaviour_loaded = True
        return _behaviour_index
    for entry in payload.get("tags") or []:
        tag = (entry.get("tag") or "").strip()
        if not tag:
            continue
        meta = {
            "tag": tag,
            "cards": int(entry.get("cards") or 0),
            "aliases": list(entry.get("aliases") or []),
            "synonyms": list(entry.get("synonyms") or []),
        }
        keys = [tag, *meta["aliases"], *meta["synonyms"]]
        for key in keys:
            n = norm_term(key)
            if n and n not in index:
                index[n] = meta
    _behaviour_index = index
    _behaviour_loaded = True
    return _behaviour_index


def resolve_term(word: str) -> dict[str, Any]:
    """Map one of her words to a Scryfall operator.

    Returns {operator, value, catalog, confidence, …}. Behaviour (otag:) is
    checked *before* type/keyword catalogs — otherwise "steal" becomes the
    one-card Final Fantasy keyword instead of otag:theft.

    Catalog fall-through beyond type/keyword is resolve_term() in resolve.py; without it an unknown
    is unresolved, not a guessed o:/otag:.
    """
    load_behaviour_tags()
    raw = (word or "").strip()
    if not raw:
        return {
            "operator": None, "value": None, "catalog": None,
            "confidence": "unresolved",
        }

    # 1. Behaviour vocabulary — verified otag: only.
    for cand in _morph_singulars(raw):
        hit = _behaviour_index.get(norm_term(cand))
        if hit and hit.get("tag"):
            return {
                "operator": "otag",
                "value": hit["tag"],
                "catalog": "behaviour",
                "confidence": "verified",
                "cards": hit.get("cards") or 0,
            }

    # 2. Thing catalogs currently loaded (type / keyword). Full precedence
    #    table (sets, watermarks, …) is handled by resolve_term().
    for cand in _morph_singulars(raw):
        if cand in _vocab.get("type", {}):
            return {
                "operator": "t",
                "value": _vocab["type"][cand].lower(),
                "catalog": "type",
                "confidence": "exact",
            }
        # Keywords match on EXACT form only — never a morphological variant.
        # "heists" must not collapse onto keyword:heist, an 8-card set-specific
        # keyword. Same trap as keyword:steal (1 card) wearing a plural: a
        # confident wrong answer is worse than an unresolved one, because
        # unresolved is visible and one wrong pool is not.
        if cand == norm_term(raw) and cand in _vocab.get("keyword", {}):
            return {
                "operator": "keyword",
                "value": _vocab["keyword"][cand].lower(),
                "catalog": "keyword",
                "confidence": "exact",
            }

    # Fall through to the full grammar. This function knows
    # behaviours, types and keywords; resolve.py additionally knows SETS,
    # watermarks, card names and artists. Without this hand-off "spiderman"
    # reports "no match in Scryfall" while resolve.resolve_term places it on
    # set:spm — two resolvers disagreeing, which is what docs/SCRYFALL-GRAMMAR.md says not to
    # build. Behaviour-first ordering is preserved because it is checked above.
    # Only catalogs THIS function does not cover. Types and keywords are
    # handled above by exact form on purpose: resolve.py matches morphological
    # variants, so "heists" would collapse onto keyword:heist — an 8-card
    # set-specific keyword — which is the keyword:steal trap wearing a plural.
    _HANDOFF = {"sets", "watermarks", "card-names", "artist-names", "word-bank"}
    try:
        full = resolve.resolve_term(raw)
    except Exception:
        full = None
    if isinstance(full, dict) and full.get("operator") and full.get("catalog") in _HANDOFF:
        return full

    return {
        "operator": None, "value": None, "catalog": None,
        "confidence": "unresolved",
    }


# Words that carry no card-type signal in a conversational theme phrase.
# "angels that gain life" must resolve "angel", not the filler around it.
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

# Card/supertypes too broad to treat as a phrase's head noun.
_BROAD_TYPES = frozenset({
    "creature", "artifact", "enchantment", "land", "instant", "sorcery",
    "planeswalker", "battle", "kindred", "tribal", "legendary", "basic",
    "snow", "world", "token", "permanent", "spell", "card",
})

# Player-strategy words: they name what a card does for a player, not text it
# prints. Bare o:ramp matches incidental substrings (Embercleave) and stops the
# ladder before otag:ramp. Function tags are the right index.
# Verified behaviours also live in data/behaviour_tags.json; this set
# still blocks bare o: for a few unverified player words (aggro/control).
_FUNCTION_TERMS = frozenset({
    "ramp", "draw", "removal", "wipe", "aggro", "control",
})


def _morph_singulars(term: str) -> list[str]:
    """Candidate singular forms of one token/phrase as a unit."""
    t = term.strip().lower()
    if not t:
        return []
    out = [t]
    if t.endswith("ies") and len(t) > 4:
        out += [t[:-3] + "y", t[:-1]]           # allies->ally, faeries->faerie
    if t.endswith("ves") and len(t) > 4:
        out += [t[:-3] + "f", t[:-3] + "fe"]    # elves->elf, knives->knife
    if t.endswith("es") and len(t) > 3:
        out.append(t[:-2])                      # foxes->fox
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        out.append(t[:-1])                      # dragons->dragon
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def theme_content_words(theme: str) -> list[str]:
    """Content words from a conversational theme, order preserved."""
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (theme or "").lower())
    return [w for w in words if w not in _THEME_STOP and len(w) > 1]


def singulars(term: str) -> list[str]:
    """Candidate singular forms, widest net. The catalog decides which is real.

    Generating candidates and testing them against the catalog beats writing
    pluralization rules: `elves`->`elve` is wrong, but `elves`->`elf` is one of
    the candidates and it's the one that's actually in the creature-type list.

    conversational themes are phrases. Morphological variants of the
    whole string come first (so "first strike" / "red dragons" still work), then
    each content word — that is what surfaces "angel" from "angels that gain life".

    non-strings (None) are absent. Never str(None) → "None".
    """
    if not isinstance(term, str):
        return []
    t = term.strip().lower()
    if not t:
        return []
    seen, uniq = set(), []
    for c in _morph_singulars(t):
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    for w in theme_content_words(t):
        if w == t:
            continue
        for c in _morph_singulars(w):
            if c not in seen:
                seen.add(c)
                uniq.append(c)
    return uniq


def classify(term: str) -> tuple[str, str]:
    """Resolve a theme word against Scryfall's vocabulary.

    Returns (kind, canonical) where kind is 'type', 'keyword', 'set', or 'text'.
    'text' means the grammar could not place the word — that is not a match,
    and callers must not invent a query and present it as one.

    Classifies one unit at a time. Phrase head-noun extraction lives in
    theme_clauses() — classify("angels that gain life") stays 'text' so a
    multi-word keyword like "first strike" is not shadowed by a contained word.
    """
    if resolve._grammar is None:
        resolve.load_grammar()
    r = resolve.resolve_term(term)
    op = r.get("operator")
    val = r.get("value")
    if op == "t" and val:
        return "type", str(val)
    if op == "keyword" and val:
        return "keyword", str(val)
    if op == "set" and val:
        return "set", str(val)
    if op == "otag" and val:
        return "otag", str(val).lower()
    if op in ("o", "wm", "name", "a") and val:
        # Still a real catalog hit — surface as text kind with the catalog form
        # so older callers keep working; theme_clauses reads resolve_term itself.
        return "text", str(val).lower()
    return "text", term.strip().lower()


def scryterm(s: str) -> str:
    """Quote a term if it contains spaces, so Scryfall reads it as one value."""
    return f'"{s}"' if " " in s else s


def _clauses_from_resolution(r: dict) -> list[str]:
    """Role-query rungs for one resolve_term() hit, most precise first."""
    op = r.get("operator")
    val = r.get("value")
    if not op or val is None or val == "":
        return []
    clauses: list[str] = []
    expr = resolve.expression(r)
    if not expr:
        return []
    low = str(val).lower()
    if op == "otag":
        clauses.append(expr)
    elif op == "t":
        clauses += [expr, f"otag:{scryterm(low)}", f"o:{scryterm(low)}"]
    elif op == "keyword":
        # `keyword:` is precise, but Scryfall rejects some catalogued terms
        # (`keyword:play` → 400) while `o:play` finds thousands.
        clauses += [expr, f"o:{scryterm(low)}"]
    elif op == "set":
        clauses.append(expr)
    elif op == "wm":
        clauses.append(expr)
    elif op == "o":
        # Bare word-bank hit. Never promote an unverified otag: — dead tags
        # (otag:aggro) 404 and look like empty themes.
        if low not in _FUNCTION_TERMS:
            clauses.append(expr)
        else:
            # Function word that somehow missed the behaviour map: stay quiet
            # rather than invent a tag. Behaviour aliases cover ramp/draw/….
            pass
    elif op in ("name", "a"):
        clauses.append(expr)
    # Dedupe, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for c in clauses:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def theme_clauses(theme: str) -> list[str]:
    """Scryfall clauses for a theme, most precise first.

    Resolves through resolve_theme() first: behaviours (otag:) and
    things (t:/set:/keyword:) compose, so "pirates that steal" becomes
    `t:pirate otag:theft`. When the resolver finds nothing, fall through to the
    per-word ladder, which carries the Scryfall quirks the resolver
    does not model — `keyword:play` returns HTTP 400 while `o:play` finds
    thousands, so a keyword rung always carries its rules-text twin.

    A term that resolves to nothing yields no clauses at all. It does not get
    an invented o:"..." rung — search_ladder reports the empty theme rather
    than padding around it.

    non-strings are absent — never interpolate the literal "None".
    """
    if not isinstance(theme, str):
        theme = ""
    theme = theme.strip()
    if not theme:
        return []
    if resolve._grammar is None:
        resolve.load_grammar()

    load_behaviour_tags()
    clauses: list[str] = []
    seen: set[str] = set()

    def add(c: str) -> None:
        if c and c not in seen:
            seen.add(c)
            clauses.append(c)


    def add_behaviour(term: str) -> bool:
        """Emit verified otag: if term is a known behaviour. True when emitted."""
        r = resolve_term(term)
        if r.get("operator") == "otag" and r.get("catalog") == "behaviour":
            add(f"otag:{scryterm(r['value'])}")
            return True
        return False

    def add_resolved(kind: str, canon: str) -> None:
        # Behaviour before type/keyword — steal must not become keyword:steal.
        if add_behaviour(canon):
            return
        low = canon.lower()
        if kind == "type":
            add(f"t:{scryterm(low)}")
            add(f"otag:{scryterm(low)}")
            add(f"o:{scryterm(low)}")
        elif kind == "keyword":
            # `keyword:` is the precise operator, but Scryfall's keyword index
            # does not accept every term in its own keyword catalogs —
            # `keyword:play` and `keyword:shuffle` return HTTP 400 while
            # `o:play` finds thousands. Always carry the rules-text rung.
            add(f"keyword:{scryterm(low)}")
            add(f"o:{scryterm(low)}")
        else:
            # Function-shaped unresolved terms: otag: before o:. Player vocab
            # ("ramp", "draw", …) never gets bare o: — that is not printed text.
            # Verified behaviours already returned above; this is the unverified
            # remnant (aggro/control) plus genuine free text.
            # An unresolved word earns NOTHING. Not an otag: (otag:aggro and
            # otag:control 400, which reads as "no cards" and pads silently —
            # one layer up), and not an o: either.
            #
            # An earlier integration ruled the other way, keeping an o: attempt on
            # the grounds that search_ladder reports which rung it landed on.
            # That was wrong twice over: it contradicted the grammar's own tests,
            # and o:"heists" matches whatever happens to print that word, which
            # is not what she asked for. An unknown is not a zero AND a
            # fallback is not a match — the second half is this line.
            return

    # resolver first — it is the only path that composes a thing with
    # a behaviour ("pirates that steal" -> t:pirate otag:theft).
    hit = resolve.resolve_theme(theme)
    if hit.get("expression"):
        add(hit["expression"])
        parts = hit.get("parts") or []
        if len(parts) > 1:
            for p in parts:
                e = resolve.expression(p)
                if e:
                    add(e)
        return clauses

    # Whole theme may be a behaviour phrase ("life gain", "board wipe").
    if add_behaviour(theme):
        return clauses

    kind, canon = classify(theme)
    if kind != "text":
        add_resolved(kind, canon)
        return clauses

    words = theme_content_words(theme)
    if len(words) > 1:
        # Prefer real creature/other types and keywords over card types like
        # "creature" that would make every commander match.
        specific: list[tuple[str, str]] = []
        broad: list[tuple[str, str]] = []
        text_words: list[str] = []
        for w in words:
            if resolve_term(w).get("catalog") == "behaviour":
                text_words.append(w)
                continue
            k, c = classify(w)
            if k == "type" and c.lower() in _BROAD_TYPES:
                broad.append((k, c))
            elif k != "text":
                specific.append((k, c))
            else:
                text_words.append(w)
        for k, c in (specific or broad):
            add_resolved(k, c)
        # Always search the leftover words individually — never the whole
        # phrase as one quoted o: term (that is the empty-rung bug).
        for w in text_words:
            add_resolved("text", w)
        if clauses:
            return clauses

    # Single unresolved word — quote it as o:/otag:.
    add_resolved("text", canon)
    return clauses


# ---------------------------------------------------------------- readback
#
# Intent verifier before commander selection. Her words on the left, the operator
# we chose underneath, a live Scryfall count on the right. Unresolved says
# "no match in Scryfall" — never 0, never silently dropped.
#
# Placement prefers resolve_term when present. Without it, a provisional
# placer covers types, exact keywords, and a small behaviour alias map so this
# screen stays honest without owning role_query / theme_clauses / INTERROGATOR.

READBACK_THIN = 10

# Connectors that introduce a second clause in a conversational theme.
# Kept on the display span; stripped before lookup ("that steal" → steal).
_READBACK_CONNECTORS = frozenset({"that", "who", "which", "and", "with"})

# Provisional behaviour aliases (to be replaced by a verified file).
# Checked before catalogs so "steal" reaches otag:theft, not keyword:steal.
_BEHAVIOUR_ALIASES = {
    "steal": "theft", "stealing": "theft", "theft": "theft",
    "ramp": "ramp",
    "draw": "draw",
    "removal": "removal",
    "lifegain": "lifegain",
    "mill": "mill",
    "tutor": "tutor",
    "reanimate": "reanimate",
    "discard": "discard",
    "counterspell": "counterspell",
    "wipe": "boardwipe", "wrath": "boardwipe", "boardwipe": "boardwipe",
    "sweeper": "sweeper",
    "burn": "burn",
    "evasion": "evasion",
    "recursion": "recursion",
    "blink": "blink", "flicker": "flicker",
    "landfall": "landfall",
    "cantrip": "cantrip",
}

_COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}


def theme_segments(theme: str) -> list[str]:
    """Split her theme into display spans. Commas and clause connectors only.

    'pirates that steal, heists' → ['pirates', 'that steal', 'heists'].
    Her words stay intact — we never replace them with our jargon.
    """
    theme = (theme or "").strip()
    if not theme:
        return []
    # Plain string splitting and a word scan instead of regex splits with
    # \s* / \s+ on both sides, which were quadratic on long runs of spaces.
    chunks = [c.strip() for c in theme.split(",") if c.strip()]
    out: list[str] = []
    for chunk in chunks:
        out.extend(p.strip() for p in _split_on_connectors(chunk) if p.strip())
    return out


# A new span starts at one of these words when it sits between two others.
_THEME_CONNECTORS = frozenset({"that", "who", "which", "and", "with"})


def _split_on_connectors(chunk: str) -> list[str]:
    """Split before each connector word that has a word before and after it."""
    words = list(re.finditer(r"\S+", chunk))
    cuts = [w.start() for i, w in enumerate(words)
            if 0 < i < len(words) - 1 and w.group().lower() in _THEME_CONNECTORS]
    bounds = [0, *cuts, len(chunk)]
    return [chunk[a:b] for a, b in zip(bounds, bounds[1:])]


def color_phrase(ci: str) -> str:
    """Human colour names for the composed row: 'blue – black'."""
    names = [_COLOR_NAMES[c] for c in (ci or "").upper() if c in _COLOR_NAMES]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    # Hair space + en-dash + hair space, matching the approved mock.
    sep = "\u2002–\u2002"
    if len(names) == 2:
        return sep.join(names)
    return sep.join(names)


def _lookup_text(said: str) -> str:
    """Strip leading connectors before placing a span."""
    words = (said or "").strip().split()
    while words and words[0].lower() in _READBACK_CONNECTORS:
        words.pop(0)
    return " ".join(words)


def _place_provisional(word: str) -> dict | None:
    """Provisional placement, used when resolve_term is unavailable.

    Returns {operator, query} or None. Behaviour first, then types (with morph),
    then keywords by exact form only — so 'heists' stays unresolved rather than
    collapsing onto the Final Fantasy set keyword Heist.
    """
    word = (word or "").strip()
    if not word:
        return None

    for cand in _morph_singulars(word):
        tag = _BEHAVIOUR_ALIASES.get(cand)
        if tag:
            op = f"otag:{tag}"
            return {"operator": op, "query": op, "catalog": "behaviour"}

    for cand in _morph_singulars(word):
        if cand in _vocab.get("type", {}):
            canon = _vocab["type"][cand].lower()
            op = f"t:{scryterm(canon)}"
            return {"operator": op, "query": op, "catalog": "type"}

    # Exact keyword only — no plural morph. 'lifelink' matches; 'heists' does not.
    low = word.lower()
    if low in _vocab.get("keyword", {}):
        canon = _vocab["keyword"][low].lower()
        op = f"keyword:{scryterm(canon)}"
        return {"operator": op, "query": op, "catalog": "keyword"}
    return None


def _is_dead_behaviour(word: str) -> bool:
    """True when the word was probed as a behaviour and found absent (stax)."""
    try:
        resolve.load_behaviours()
    except Exception:
        return False
    dead = getattr(resolve, "_dead_behaviours", None) or set()
    if not dead:
        return False
    raw = (word or "").strip()
    if not raw:
        return False
    needles = {resolve.normalize(raw)}
    for form in resolve._morph_singulars(raw):
        needles.add(resolve.normalize(form))
    return any(n in dead for n in needles if n)


def _theme_parts_are_real(parts: list | None) -> bool:
    """True when resolve_theme placed a catalog hit, not bare word-bank o:."""
    for p in parts or []:
        op = p.get("operator")
        cat = str(p.get("catalog") or "").lower()
        if op in ("t", "otag", "keyword", "set", "wm"):
            return True
        if cat and cat not in ("word-bank", "card-names", "artist-names"):
            return True
    return False


def place_theme_word(said: str) -> dict:
    """Place one display span. Prefers resolve_term when available.

    non-strings are absent. Never stringify None into a query.
    when the whole span is not one catalog term, fall through to
    resolve_theme so 'vampire lifegain' / 'drain the table' place the same
    way the build path does.
    """
    if not isinstance(said, str):
        said = ""
    said = said.strip()
    lookup = _lookup_text(said)
    if not said or not lookup:
        return {"said": said, "resolved": False, "operator": None,
                "query": None, "count": None, "note": "no match in Scryfall"}

    placed: dict | None = None
    rt = globals().get("resolve_term")
    # When resolve_term is present, trust a positive hit. An
    # unresolved single-term result may still compose via resolve_theme
    # below — do not treat unresolved as final for multi-word spans.
    if callable(rt):
        try:
            r = rt(lookup)
        except Exception:
            r = None
        if isinstance(r, dict) and r.get("operator") and r.get("value") is not None:
            op = r["operator"]
            val = r["value"]
            # resolve_term may return operator as 't' / 'otag' or a full 't:pirate'.
            if ":" in str(op):
                query = (str(op) if str(val) in str(op)
                         else f"{op}:{scryterm(str(val))}")
            else:
                query = f"{op}:{scryterm(str(val))}"
            placed = {"operator": query, "query": query,
                      "catalog": r.get("catalog") or ""}
        else:
            placed = None
    else:
        placed = _place_provisional(lookup)

    if not placed:
        # Conversational spans: compose thing + behaviour, strip stopwords.
        try:
            hit = resolve.resolve_theme(lookup)
        except Exception:
            hit = None
        if isinstance(hit, dict) and hit.get("expression"):
            parts = hit.get("parts") or []
            leftover = hit.get("unresolved") or []
            # Dead behaviours inside the span stay unresolved (stax pirates).
            # Incidental leftovers ("permanents") do not veto a real placement.
            if (_theme_parts_are_real(parts)
                    and not any(_is_dead_behaviour(u) for u in leftover)):
                expr = hit["expression"]
                cat = "composed" if len(parts) > 1 else (
                    str((parts[0] or {}).get("catalog") or "") if parts else "")
                placed = {"operator": expr, "query": expr, "catalog": cat}

    if not placed:
        return {"said": said, "resolved": False, "operator": None,
                "query": None, "count": None, "note": "no match in Scryfall"}

    return {"said": said, "resolved": True,
            "operator": placed["operator"], "query": placed["query"],
            "count": None, "note": None, "catalog": placed.get("catalog")}


def compose_pool_query(resolved: list[dict]) -> str | None:
    """Scryfall pool from placed readback terms.

    Sibling types OR — `horses and knights` → `(t:horse OR t:knight)`.
    One behaviour ANDs with that group. Keyword menus do not AND onto a
    type pool (they zeroed cavalry themes). Same shape as
    resolve.compose_expressions, fed by place_theme_word rows.
    """
    types: list[str] = []
    behaviours: list[str] = []
    keywords: list[str] = []
    other: list[str] = []
    for t in resolved or []:
        q = (t.get("query") or "").strip()
        if not q:
            continue
        cat = str(t.get("catalog") or "").lower()
        # Prefer operator shape; catalog is a tie-break for composed spans.
        if q.startswith("t:") or cat in ("type", "creature-types"):
            # A composed span like "t:vampire otag:lifegain" is already done.
            if " " in q and not q.startswith("("):
                other.append(q)
            else:
                types.append(q)
        elif q.startswith("otag:") or cat == "behaviour":
            behaviours.append(q)
        elif q.startswith("keyword:") or cat == "keyword":
            keywords.append(q)
        else:
            other.append(q)

    chunks: list[str] = []
    if types:
        # Dedup while preserving order — segments can echo the same type.
        seen: set[str] = set()
        uniq: list[str] = []
        for c in types:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        chunks.append(uniq[0] if len(uniq) == 1
                      else f"({' OR '.join(uniq)})")
    if behaviours:
        chunks.append(behaviours[0])
    if not chunks and keywords:
        seen_k: set[str] = set()
        uniq_k: list[str] = []
        for c in keywords:
            if c not in seen_k:
                seen_k.add(c)
                uniq_k.append(c)
        chunks.append(uniq_k[0] if len(uniq_k) == 1
                      else f"({' OR '.join(uniq_k)})")
    if not chunks and other:
        chunks.append(other[0])
    return " ".join(chunks) if chunks else None


async def build_readback(theme: str, colors: str = "") -> dict:
    """Live Scryfall counts for the pre-commander readback screen.

    Returns terms (her word, operator, count), an optional composed row, and
    thin=True when the composed pool in her colours is under READBACK_THIN.
    Counts come from Scryfall only. Failed requests stay None — never 0.
    """
    await load_vocab()
    ci = "".join(c for c in (colors or "").upper() if c in "WUBRG")
    segments = theme_segments(theme)
    terms: list[dict] = []
    for said in segments:
        t = place_theme_word(said)
        if t.get("resolved") and t.get("query"):
            t["count"] = await count_matching(t["query"])
        terms.append({
            "said": t["said"],
            "resolved": bool(t.get("resolved")),
            "operator": t.get("operator"),
            "query": t.get("query"),
            "count": t.get("count"),
            "catalog": t.get("catalog"),
            "note": None if t.get("resolved") else "no match in Scryfall",
        })

    resolved = [t for t in terms if t["resolved"] and t.get("query")]
    composed = None
    thin = False
    if resolved:
        # Prefer the whole-theme expression when resolve_theme already ORs
        # siblings / composes behaviour — segments alone lose slash forms.
        q = None
        try:
            hit = resolve.resolve_theme(theme)
        except Exception:
            hit = None
        if isinstance(hit, dict) and hit.get("expression"):
            q = hit["expression"]
        if not q:
            q = compose_pool_query(resolved)
        if q:
            if ci:
                q = f"{q} id<={ci}"
            count = await count_matching(q)
            said_parts = [t["said"] for t in resolved]
            said = " ".join(said_parts)
            phrase = color_phrase(ci)
            if phrase:
                said = f"{said}, in {phrase}"
            # one resolved term already carries the
            # operator + count. A second identical "together" row is noise.
            # Keep composed when multiple terms combine (pirates that steal).
            if len(resolved) >= 2:
                composed = {"said": said, "query": q, "count": count}
                if isinstance(count, int) and count < READBACK_THIN:
                    thin = True
            elif isinstance(count, int) and count < READBACK_THIN:
                thin = True
            elif len(resolved) == 1 and isinstance(resolved[0].get("count"), int):
                if resolved[0]["count"] < READBACK_THIN:
                    thin = True

    return {
        "theme": (theme or "").strip(),
        "colors": ci,
        "terms": terms,
        "composed": composed,
        "thin": thin,
        "thin_threshold": READBACK_THIN,
    }


# ---------------------------------------------------------------- instrument
#
# Live Scryfall counts shaped for INTERROGATOR. Same composition as readback;
# status is ok | empty | unknown — never coerce unknown → 0. No card names.

INSTRUMENT_FENCE = "[instrument]"


def _count_status(count) -> str:
    if count is None:
        return "unknown"
    if isinstance(count, int) and count == 0:
        return "empty"
    return "ok"


async def build_instrument(theme: str, colors: str = "",
                           readiness: dict | None = None) -> dict:
    """Deterministic instrument payload for chat.

    Spans cover resolved readback terms only. Unresolved words are omitted
    (they are not empty pools). Failed counts are status=unknown.
    """
    theme = (theme or "").strip()
    if not theme:
        return {
            "theme": "",
            "colors": "",
            "spans": [],
            "composed": None,
            "unknowns": [],
            "empty": [],
            "readiness": readiness,
            "thin": False,
        }
    rb = await build_readback(theme, colors)
    spans: list[dict] = []
    unknowns: list[str] = []
    empty: list[str] = []
    for t in rb.get("terms") or []:
        if not t.get("resolved") or not t.get("query"):
            continue
        status = _count_status(t.get("count"))
        span = {
            "said": t.get("said") or "",
            "operator": t.get("operator"),
            "query": t.get("query"),
            "count": t.get("count"),
            "status": status,
        }
        spans.append(span)
        if status == "unknown":
            unknowns.append(span["said"])
        elif status == "empty":
            empty.append(span["said"])
    composed_out = None
    comp = rb.get("composed")
    if isinstance(comp, dict) and comp.get("query"):
        status = _count_status(comp.get("count"))
        composed_out = {
            "said": comp.get("said") or "",
            "query": comp.get("query"),
            "count": comp.get("count"),
            "status": status,
        }
        if status == "unknown":
            unknowns.append(composed_out["said"] or "composed")
        elif status == "empty":
            empty.append(composed_out["said"] or "composed")
    return {
        "theme": rb.get("theme") or theme,
        "colors": rb.get("colors") or "",
        "spans": spans,
        "composed": composed_out,
        "unknowns": unknowns,
        "empty": empty,
        "readiness": readiness,
        "thin": bool(rb.get("thin")),
    }


def theme_hint_for_instrument(messages: list | None,
                              brief: dict | None) -> str:
    """Theme string for instrument: brief wins, else last short user line."""
    bt = brief_theme(brief) if brief else ""
    if bt:
        return bt
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        # Strip any prior instrument fence the client accidentally echoed.
        if INSTRUMENT_FENCE in content:
            content = content.split(INSTRUMENT_FENCE, 1)[0]
        text = content.strip()
        if not text or len(text) > 240:
            continue
        # Skip pure acknowledgements.
        if text.lower() in {"yes", "yeah", "yep", "ok", "okay", "sure", "no"}:
            continue
        return text
    return ""


def messages_with_instrument(messages: list, instrument: dict | None) -> list:
    """Copy messages and append an app-authored instrument block for the model.

    The caller's list is not mutated — UI keeps showing her words only.
    """
    out = [dict(m) for m in (messages or []) if isinstance(m, dict)]
    if not instrument or not (instrument.get("spans") or instrument.get("composed")):
        return out
    payload = {
        "spans": instrument.get("spans") or [],
        "composed": instrument.get("composed"),
        "unknowns": instrument.get("unknowns") or [],
        "empty": instrument.get("empty") or [],
        "thin": bool(instrument.get("thin")),
    }
    block = (
        f"{INSTRUMENT_FENCE}\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "Use these live Scryfall counts in your reply. "
        "status ok = real count; empty = none in Commander; "
        "unknown = could not look (not zero). Never invent card names."
    )
    out.append({"role": "user", "content": block})
    return out


def strip_pool_magnitudes_for_handoff(instrument: dict | None) -> dict | None:
    """ok pool sizes are builder diagnostics — suppress at handoff.

    Empty and unknown stay graded (honesty). Healthy counts become None with
    count_suppressed=True so a handoff cannot restate an unlabelled magnitude.
    """
    if not isinstance(instrument, dict):
        return instrument
    out = dict(instrument)
    spans_out: list[dict] = []
    for span in out.get("spans") or []:
        if not isinstance(span, dict):
            continue
        s = dict(span)
        if s.get("status") == "ok":
            s["count"] = None
            s["count_suppressed"] = True
        spans_out.append(s)
    out["spans"] = spans_out
    comp = out.get("composed")
    if isinstance(comp, dict) and comp.get("status") == "ok":
        c = dict(comp)
        c["count"] = None
        c["count_suppressed"] = True
        out["composed"] = c
    return out


def ensure_closing_instrument_readback(text: str, instrument: dict | None,
                                       *, still_asking: bool) -> str:
    """Handoff honesty for empty/unknown instrument spans.

    do **not** append healthy pool sizes. "278 cards matching the
    theme" is a builder diagnostic — wrong beat, and unlabelled as commanders.
    Empty and unknown still surface: unknown is never zero.
    """
    if still_asking or not instrument:
        return text or ""
    text = text or ""
    comp = instrument.get("composed") if isinstance(instrument, dict) else None
    count = None
    label = ""
    status = None
    if isinstance(comp, dict) and comp.get("status") in ("ok", "empty", "unknown"):
        # ok composed counts are suppressed — skip them as a handoff source.
        if comp.get("status") != "ok":
            count = comp.get("count")
            status = comp.get("status")
            label = (comp.get("said") or "").strip()
    if status is None:
        for span in instrument.get("spans") or []:
            if not isinstance(span, dict):
                continue
            st = span.get("status")
            if st == "ok":
                continue  # never force a healthy pool magnitude
            if st in ("empty", "unknown") and status is None:
                status = st
                count = span.get("count")
                label = (span.get("said") or "").strip()
    if status is None:
        return text
    if status == "empty":
        if "could not find" in text.lower() or "none in commander" in text.lower():
            return text
        bit = (f"I could not find cards in Commander for "
               f"“{label or 'that'}” — we should pick different words.")
        return f"{text}\n\n{bit}".strip() if text else bit
    if status == "unknown":
        if "could not look" in text.lower() or "couldn't look" in text.lower():
            return text
        bit = ("I could not look that up just now — not the same as finding "
               "zero cards. Want to try again, or keep talking?")
        return f"{text}\n\n{bit}".strip() if text else bit
    return text


# ---------------------------------------------------------------- narrowing
#
# Between "we understand your wish" and the picker, size the *commander* forks
# she is about to choose among — not the theme card pool. Data comes from the
# commanders KB (ways_to_play) + theme_fit / strategy_fit. Names only from
# Scryfall cards we already fetched. INTERROGATOR prompt text is unchanged;
# this is the payload a narrowing turn would consume.

NARROWING_COUNT = 3
NARROWING_FETCH_CAP = 60  # enough to diversify ways; not the full rung


def readback_count_display(count, *, thin: bool = False) -> dict | None:
    """What the readback may show for a magnitude.

    Healthy pool sizes return None — not shown. Empty and unknown stay labelled.
    Thin pools stay visible and say they are cards in her colours.
    """
    if count is None:
        return {"kind": "unknown", "label": "couldn't reach Scryfall"}
    if not isinstance(count, int):
        return {"kind": "unknown", "label": "couldn't reach Scryfall"}
    if count == 0:
        return {"kind": "empty", "label": "none in Commander"}
    if thin:
        return {"kind": "thin", "label": f"{count} cards in your colours",
                "count": count}
    return None


def pick_narrowing_candidates(
    cards: list[dict],
    brief: dict | None,
    n: int = NARROWING_COUNT,
) -> list[dict]:
    """Pick up to n commanders that differ by lead playstyle.

    Walks the pool in order (caller ranks first). Lead way comes from the KB
    / detect (`_ways_for_card`) so theme-only briefs still diversify. Strategy-
    matched ways float first when present. Unknown ways never become score 0 —
    the candidate may still seat with ways_status=unknown and no lead_way.
    """
    brief = brief if isinstance(brief, dict) else {}
    n = max(1, int(n or NARROWING_COUNT))
    vocab = playstyles.load_playstyles()
    picked: list[dict] = []
    seen_ways: set[str] = set()
    leftovers: list[dict] = []

    def _row(card: dict, *, lead: str | None, says: str,
             ways_status: str, theme: dict, strat: dict) -> dict:
        return {
            "name": card.get("name") or "",
            "color_identity": list(card.get("color_identity") or []),
            "type": card.get("type") or "",
            "lead_way": lead,
            "says": says,
            "ways_status": ways_status,
            "theme_fit": {
                "status": theme.get("status"),
                "score": theme.get("score"),
                "matched": list(theme.get("matched") or []),
            },
            "strategy_fit": {
                "status": strat.get("status"),
                "score": strat.get("score"),
                "matched": list(strat.get("matched") or []),
            },
            "differ": says or (
                "I could not read how this commander wants to be played."
                if ways_status == "unknown" else ""
            ),
        }

    for card in cards or []:
        if len(picked) >= n:
            break
        theme = cmd_kb.theme_fit(card, brief)
        strat = cmd_kb.strategy_fit(card, brief)
        ways_status, ways = cmd_kb._ways_for_card(card)
        if ways_status == "unknown":
            leftovers.append(
                _row(card, lead=None, says="", ways_status="unknown",
                     theme=theme, strat=strat))
            continue
        matched = list(strat.get("matched") or [])
        ordered_pids: list[str] = []
        for pid in matched:
            if pid and pid not in ordered_pids:
                ordered_pids.append(pid)
        for w in ways or []:
            pid = w.get("playstyle") if isinstance(w, dict) else None
            if pid and pid not in ordered_pids:
                ordered_pids.append(pid)
        lead = None
        says = ""
        for pid in ordered_pids:
            if pid in seen_ways:
                continue
            line = (vocab.get(pid) or {}).get("says") or ""
            if not line:
                continue
            lead, says = pid, line
            break
        if lead:
            seen_ways.add(lead)
            picked.append(
                _row(card, lead=lead, says=says, ways_status="ok",
                     theme=theme, strat=strat))
        else:
            leftovers.append(
                _row(card, lead=None, says="", ways_status=ways_status,
                     theme=theme, strat=strat))

    for row in leftovers:
        if len(picked) >= n:
            break
        # Fill only when we have nothing distinct left — still never invent a way.
        if row.get("name") and row["name"] not in {p["name"] for p in picked}:
            picked.append(row)
    return picked[:n]


async def build_narrowing_turn(
    brief: dict | None,
    *,
    cards: list[dict] | None = None,
    rung_status: str | None = None,
    rung_total: int | None = None,
) -> dict:
    """Payload for a pre-picker narrowing beat.

    status:
      ok       — candidates from real Scryfall commanders
      empty    — looked; none
      unknown  — could not look (commander_count is None, never 0)

    INTERROGATOR does not consume this yet; that needs a deliberate prompt edit.
    Shape is stable for injection beside [instrument] later.
    """
    brief = brief if isinstance(brief, dict) else {}
    usable, reason = brief_is_usable(brief)
    if not usable:
        return {
            "status": "empty",
            "candidates": [],
            "commander_count": 0,
            "query": "",
            "note": reason or "No theme yet.",
        }

    apply_way_fields(brief)
    query = ""
    status = rung_status
    total = rung_total
    pool = list(cards) if cards is not None else None

    if pool is None:
        await load_vocab()
        ladder, _generic = commander_ladder(brief)
        pool, query, status, total = await search_primary_rung(
            ladder, limit=NARROWING_FETCH_CAP)
    if status == "unknown":
        return {
            "status": "unknown",
            "candidates": [],
            "commander_count": None,
            "query": query,
            "note": ("I could not look up commanders just now — not the same "
                     "as finding none."),
        }
    pool = legal_commanders_only(pool or [])
    pool = prefer_way_playstyle(pool, brief.get("way_playstyle"))
    ranked = rank_commanders_pool(pool, brief)
    if not ranked:
        return {
            "status": "empty",
            "candidates": [],
            "commander_count": 0 if status == "ok" else None,
            "query": query,
            "note": "I could not find commanders for this yet.",
        }
    # Prefer Scryfall's true commander count when we have it; never the fetch cap.
    if isinstance(total, int):
        commander_count = total
    else:
        commander_count = len(ranked)
    candidates = pick_narrowing_candidates(ranked, brief, n=NARROWING_COUNT)
    theme = brief_theme(brief)
    return {
        "status": "ok",
        "candidates": candidates,
        "commander_count": commander_count,
        "query": query,
        "theme": theme,
        "note": None,
    }


def ensure_closing_narrowing(text: str, narrowing: dict | None) -> str:
    """Append a deterministic narrowing beat — real names, labelled units.

    Replaces the old pool-size sentence. Unknown stays unknown.
    """
    text = text or ""
    if not isinstance(narrowing, dict):
        return text
    status = narrowing.get("status")
    if status == "unknown":
        if "could not look" in text.lower() or "couldn't look" in text.lower():
            return text
        bit = (narrowing.get("note")
               or "I could not look up commanders just now — not the same "
                  "as finding none.")
        return f"{text}\n\n{bit}".strip() if text else bit
    if status == "empty":
        if "could not find" in text.lower():
            return text
        bit = (narrowing.get("note")
               or "I could not find commanders for this yet.")
        return f"{text}\n\n{bit}".strip() if text else bit
    if status != "ok":
        return text
    cands = [c for c in (narrowing.get("candidates") or [])
             if isinstance(c, dict) and c.get("name")]
    if not cands:
        return text
    # Skip if she already sees these names (model or prior append).
    if all(c["name"] in text for c in cands):
        return text
    theme = (narrowing.get("theme") or "").strip()
    n_cmd = narrowing.get("commander_count")
    head_bits = ["Before you pick, a few that fit differently"]
    if isinstance(n_cmd, int):
        unit = f"{n_cmd} {theme} commanders" if theme else f"{n_cmd} commanders"
        head_bits.append(f"({unit} in print — not the card pool)")
    head = " ".join(head_bits) + ":"
    lines = []
    for c in cands:
        name = c["name"]
        says = (c.get("says") or c.get("differ") or "").strip().rstrip(".")
        lead = (c.get("lead_way") or "").strip()
        if lead and says:
            lines.append(f"— {name}: {lead} — {says}.")
        elif says:
            lines.append(f"— {name}: {says}.")
        elif c.get("ways_status") == "unknown":
            lines.append(
                f"— {name}: I could not read how this one wants to be played.")
        else:
            lines.append(f"— {name}.")
    bit = head + "\n" + "\n".join(lines)
    return f"{text}\n\n{bit}".strip() if text else bit


# ---------------------------------------------------------------- readiness
#
# Countable gate between brief and readback. The model does not decide it is
# finished — the app does, from facts: terms resolve, pool has depth, strategy
# is present. A self-reported confidence percentage never appears.
#
# Colours are intentionally not a blocker. The INTERROGATOR prompt leaves colours
# to the commander; forcing a colour question here would fight that rule. When
# she volunteers colours, pool depth uses them.
#
# The check names the next question. INTERROGATOR stays untouched (sacred core).

READINESS_POOL_FLOOR = READBACK_THIN  # < 10 composed cards blocks


def _brief_strategy(brief: dict) -> str:
    """What the deck does — angle from interrogation, strategy/plan if present."""
    for key in ("strategy", "angle", "plan"):
        v = (brief or {}).get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _question_for_blocker(blocker: dict, *, stuck: bool = False,
                          brief: dict | None = None) -> str:
    """One concrete question — names the missing fact, never a generic re-prompt."""
    signal = blocker.get("signal")
    detail = blocker.get("detail") or {}

    if signal == "terms":
        term = detail.get("term") or "that"
        if stuck:
            resolved = detail.get("resolved_parts") or []
            if resolved:
                alt = " and ".join(resolved[:2])
                return (f"I still could not find “{term}” in Scryfall — "
                        f"want to try different words, or shall I build around "
                        f"{alt} instead?")
            return (f"I still could not find “{term}” in Scryfall — "
                    f"want to try different words, or move on without it?")
        # Name the word; don't force "a {plural}" grammar.
        return (f"“{term}” did not resolve in Scryfall — what does that deck "
                f"do: steal permanents, take extra turns, or something else?")

    if signal == "pool":
        n = detail.get("count")
        count_bit = f"about {n} cards" if isinstance(n, int) else "very few cards"
        if stuck:
            return (f"That theme is still thin ({count_bit}). "
                    f"Want to drop a word, add a behaviour, or try different words?")
        return (f"That theme only finds {count_bit} together — thin. "
                f"Want to widen it (drop a word, add a behaviour), "
                f"or try different words?")

    if signal == "strategy":
        if stuck:
            return ("I still need what a turn looks like when this deck is working — "
                    "one sentence is enough.")
        return ("What should a turn with this deck feel like when it is working?")

    return "What is still undecided about this deck?"


async def check_readiness(brief: dict,
                          previously_asked: str | None = None) -> dict:
    """Measurable readiness before readback / commanders.

    Returns ready, blockers, warnings, the one question to ask if blocked,
    asked_about (stable id for the next pass), and stuck when the same
    blocker survived a question. Never emits a confidence percentage.
    """
    brief = brief or {}
    await load_vocab()

    blockers: list[dict] = []
    warnings: list[dict] = []
    pool_unknown = False
    theme = brief_theme(brief)

    # --- terms resolve (blocking when theme is present) --------------------
    # authority is resolve_theme on the full theme — the same
    # composition the build path uses — not per-segment resolve_term. A
    # behaviour-flavoured span the catalog cannot place ("keep me alive")
    # is not the same as a load-bearing term that does not exist (heists,
    # stax). Only the latter blocks when the theme already has an expression.
    unresolved: list[str] = []
    resolved_parts: list[str] = []
    theme_expr = None
    if theme:
        try:
            theme_hit = resolve.resolve_theme(theme)
        except Exception:
            theme_hit = {"parts": [], "expression": None, "unresolved": [theme]}
        theme_expr = theme_hit.get("expression")
        for p in theme_hit.get("parts") or []:
            label = str(p.get("value") or resolve.expression(p) or "").strip()
            if label:
                resolved_parts.append(label)
        leftover = list(theme_hit.get("unresolved") or [])
        if not theme_expr:
            # Nothing usable — name every unresolved word (or the theme).
            unresolved = leftover or [theme]
        else:
            for w in leftover:
                if _is_dead_behaviour(w):
                    unresolved.append(w)
                else:
                    # Visible remainder, not a gate. House rule: say which.
                    warnings.append({
                        "signal": "terms",
                        "detail": {
                            "term": w,
                            "reason": "unresolved_remainder",
                        },
                    })
        for term in unresolved:
            blockers.append({
                "signal": "terms",
                "detail": {
                    "term": term,
                    "resolved_parts": list(resolved_parts),
                },
            })
    # Empty theme with a named commander: nothing to resolve — proceed.
    # Empty theme without a commander and without strategy is caught below.

    # --- strategy / angle present (blocking) -------------------------------
    if not _brief_strategy(brief):
        blockers.append({"signal": "strategy", "detail": {}})

    # --- pool depth (thin but non-zero discloses; zero still blocks) ---
    # Skip when there is nothing resolved to compose, or when the theme is
    # empty (commander-named path). Unknown counts warn — never become thin.
    # A composed pool of 5 is buildable — refusal hid the tradeoff; disclosure
    # on the deck page is the honest answer. Zero means we cannot proceed on
    # theme and must keep asking.
    composed_count = None
    if theme and theme_expr and not unresolved:
        # Reuse readback composition so the gate and the screen agree.
        colors = brief.get("colors") or ""
        rb = await build_readback(theme, colors)
        composed = rb.get("composed") or {}
        composed_count = composed.get("count")
        if composed_count is None:
            pool_unknown = True
            warnings.append({
                "signal": "pool",
                "detail": {"reason": "unreachable"},
            })
        elif isinstance(composed_count, int) and composed_count == 0:
            blockers.append({
                "signal": "pool",
                "detail": {
                    "count": 0,
                    "floor": READINESS_POOL_FLOOR,
                    "query": composed.get("query"),
                },
            })
        elif isinstance(composed_count, int) and composed_count < READINESS_POOL_FLOOR:
            warnings.append({
                "signal": "pool",
                "detail": {
                    "count": composed_count,
                    "floor": READINESS_POOL_FLOOR,
                    "query": composed.get("query"),
                    "disclosure": (
                        f"Only {composed_count} on-theme cards in these colours — "
                        f"the rest of the 99 will serve strategy and curve. "
                        f"That is a starting point, not a refusal."
                    ),
                },
            })

    # --- mechanics (warn) --------------------------------------------------
    mechs = brief.get("mechanics") or []
    if not (isinstance(mechs, list) and len(mechs) >= 1):
        warnings.append({"signal": "mechanics", "detail": {}})

    # --- way_preference unmapped (warn — do not invent a playstyle) --------
    # she named a scene we cannot seat on the 13 detectable ids.
    # Disclosure + clarifying question; not a blocker by default.
    apply_way_fields(brief)
    way_pref = (brief.get("way_preference") or "").strip()
    if way_pref and not brief.get("way_playstyle"):
        warnings.append({
            "signal": "way_preference",
            "detail": {
                "said": way_pref,
                "disclosure": (
                    f"I heard “{way_pref}” but I cannot map that to a known "
                    f"way yet — commanders will not be steered by it."
                ),
                "question": (
                    f"When you say “{way_pref}”, is that closer to tokens, "
                    f"sacrifice, lifegain, or something else?"
                ),
            },
        })

    # --- budget / bracket (warn — defaults exist) --------------------------
    if brief.get("budget_total") in (None, ""):
        warnings.append({"signal": "budget", "detail": {}})
    if brief.get("bracket") in (None, ""):
        warnings.append({"signal": "bracket", "detail": {}})

    # One question: first blocker, in signal priority order.
    _PRIORITY = ("terms", "pool", "strategy")
    blockers.sort(key=lambda b: (
        _PRIORITY.index(b["signal"]) if b["signal"] in _PRIORITY else 99))

    primary = blockers[0] if blockers else None
    asked_about = None
    if primary:
        if primary["signal"] == "terms":
            asked_about = f"terms:{primary['detail'].get('term', '')}"
        elif primary["signal"] == "pool":
            asked_about = "pool"
        else:
            asked_about = primary["signal"]

    stuck = bool(
        previously_asked and asked_about and previously_asked == asked_about
    )
    question = (_question_for_blocker(primary, stuck=stuck, brief=brief)
                if primary else None)

    ready = not blockers

    return {
        "ready": ready,
        "blockers": blockers,
        "warnings": warnings,
        "question": question,
        "asked_about": asked_about,
        "stuck": stuck,
        "pool_count": composed_count,
        "pool_unknown": pool_unknown,
        "pool_floor": READINESS_POOL_FLOOR,
    }


# strategy prose names how the deck plays; utility roles should search for
# cards that fit that shape instead of one generic EDH package.
# Curve phrases live in curve.py — one ear, used by preference() and
# strategy_profile(). Do not re-list them here.
_REFILL_DRAW = ("empty", "discard", "attack", "go wide", "refill", "hellbent",
                "treasure", "aggressive")
_ACCUM_DRAW = ("draw two", "draw three", "card advantage", "hold", "accumulate",
               "control", "hand size")
_SURGICAL_REM = ("alive", "attack with", "go wide", "token", "protect",
                 "swinging", "combat", "creatures you control")
_MASS_REM = ("board clear", "wipe the board", "wipe", "wrath", "reset the board",
             "destroy all")

# which card types the plan wants inside theme/plan/deepcut — not a
# second budget beside the skeleton. "creatures" = combat/tokens; "spells" =
# spellslinger; "interaction" = control; "mixed" = no type floor.
_CREATURE_BODIES = (
    "combat", "go wide", "token", "tokens", "attack", "swing", "swinging",
    "creatures you control", "tribal", "aggro", "beatdown", "voltron",
    "attackers", "board full",
)
_SPELL_BODIES = (
    "spellslinger", "instant", "instants", "sorcery", "sorceries",
    "noncreature spell", "noncreature spells", "cast spells", "prowess",
    "storm", "cantrip", "magecraft",
)
_INTERACTION_BODIES = (
    "control", "counter", "counterspell", "counterspells", "permission",
    "answer threats", "keep the board", "stax", "pillow fort",
)

# Fractions of each flexible role that must match a composition axis.
# Keys are strategy_profile()["bodies"]. Utility roles are intentionally absent.
ROLE_TYPE_FRACTIONS = {
    "creatures": {
        "theme":   {"creatures": 0.60},
        "plan":    {"creatures": 0.50},
        "deepcut": {"creatures": 0.40},
    },
    "spells": {
        "theme":   {"instants_sorceries": 0.50},
        "plan":    {"instants_sorceries": 0.40},
        "deepcut": {"instants_sorceries": 0.30},
    },
    "interaction": {
        "theme":   {"instants_sorceries": 0.40},
        "plan":    {"instants_sorceries": 0.30},
    },
    "mixed": {},
}

# Whole-deck soft floors for overview cons — below this, the type mix is a
# checkable weakness for the chosen bodies shape.
DECK_TYPE_FLOORS = {
    "creatures": ("creatures", 20),
    "spells": ("instants_sorceries", 15),
    "interaction": ("instants_sorceries", 12),
}


def strategy_profile(brief: dict) -> dict:
    """Infer curve, draw, removal, and body shape from commander strategy prose.

    curve preference is curve.preference() — it reads every brief
    field that survives commander overwrite (angle/fantasy/theme/playstyle),
    not just strategy/plan. The old `plan or angle` form dropped her curve
    words the moment the pick write-up filled plan.
    """
    from deckwright import curve as _curve
    text = " ".join(filter(None, [
        brief.get("strategy") or "",
        brief.get("plan") or "",
        brief.get("angle") or "",
        brief.get("fantasy") or "",
        brief.get("theme") or "",
        brief.get("playstyle") or "",
        brief.get("fits") or "",
        brief.get("does") or "",
        " ".join(brief.get("mechanics") or []),
    ])).lower()

    curve = _curve.preference(brief)
    if curve == _curve.UNKNOWN:
        curve = "mid"

    draw = "standard"
    if any(h in text for h in _ACCUM_DRAW):
        draw = "accumulate"
    elif any(h in text for h in _REFILL_DRAW):
        draw = "refill"

    removal = "standard"
    if any(h in text for h in _MASS_REM):
        removal = "mass"
    elif any(h in text for h in _SURGICAL_REM):
        removal = "surgical"

    # Bodies shape is checked before draw/removal phrasing that also says
    # "control" — spellslinger and combat are more specific when present.
    bodies = "mixed"
    if any(h in text for h in _SPELL_BODIES):
        bodies = "spells"
    elif any(h in text for h in _CREATURE_BODIES):
        bodies = "creatures"
    elif any(h in text for h in _INTERACTION_BODIES):
        bodies = "interaction"

    return {"curve": curve, "draw": draw, "removal": removal, "bodies": bodies}


def role_type_floors(role: str, target: int, brief: dict) -> dict[str, int]:
    """Min counts per composition axis for one role under this strategy.

    floors compose with the skeleton — '9 of 15 theme cards are
    creatures' — they do not compete with ramp/draw slot counts.
    """
    if target <= 0:
        return {}
    bodies = strategy_profile(brief).get("bodies") or "mixed"
    fracs = ROLE_TYPE_FRACTIONS.get(bodies, {}).get(role) or {}
    # round so 50% of 15 is 8, not truncated to 7.
    return {axis: max(1, round(target * frac))
            for axis, frac in fracs.items() if frac > 0}


def type_bias_clause(brief: dict) -> str:
    """Scryfall type clause for early theme/plan/deepcut rungs, or ''."""
    bodies = strategy_profile(brief).get("bodies") or "mixed"
    if bodies == "creatures":
        return "t:creature"
    if bodies in ("spells", "interaction"):
        return "(t:instant OR t:sorcery)"
    return ""


def type_breakdown(deck: list) -> dict[str, int]:
    """Creature / instant+sorcery / artifact / enchantment / land counts."""
    return tweak_mod.composition_of(deck)


def enforce_type_targets(role: str, chosen: list, pool: list, target: int,
                         brief: dict) -> list:
    """Swap chosen cards so the role meets its type floors. Never invents.

    Prefers keeping earlier (higher-ranked) picks; replaces from the end with
    matching cards still in the pool. Lands are never used as type fillers.
    """
    floors = role_type_floors(role, target, brief)
    if not floors:
        return list(chosen[:target])

    result = list(chosen[:target])
    in_result = {c["name"] for c in result}

    for axis, need in floors.items():
        def matches(c, ax=axis):
            return tweak_mod.card_axis(c) == ax

        have = [c for c in result if matches(c)]
        if len(have) >= need:
            continue
        donors = [
            c for c in pool
            if c["name"] not in in_result
            and matches(c)
            and not is_land_card(c)
        ]
        # Replace lowest-priority non-matches first (end of ranked list).
        victims = [c for c in reversed(result) if not matches(c)]
        for victim, donor in zip(victims, donors):
            if len(have) >= need:
                break
            idx = next(i for i, c in enumerate(result) if c["name"] == victim["name"])
            result[idx] = donor
            in_result.discard(victim["name"])
            in_result.add(donor["name"])
            have.append(donor)
    return result


def leave_enabler_floor(blink_cards: list[dict], *, minimum: int = 1) -> dict:
    """Payoff-floor descriptor for cards that cause the commander to leave.

    put otag:blink into the theme pool; the expert-deck comparison measured that zero of
    those candidates still reached the 99. Same shape as the archetype payoff floors: recall into
    the pool is not enough when the goldfish cannot see the card. Reserve at
    least `minimum` blink names via enforce_payoff_floors. Never invents a
    name — the floor's set is exactly the Scryfall response.
    """
    names = {c["name"] for c in blink_cards if c.get("name")}
    return {
        "id": "leave_enabler_blink",
        "min": max(0, int(minimum)),
        "names": names,
    }


def synergy_enabler_floor(family: str, cards: list[dict], *,
                          minimum: int = 1) -> dict:
    """Payoff-floor for a relation-neighbor enabler.

    tokens feeding dies_triggers is the sac-fodder case: recall alone left
    the goldfish free to drop every token maker. Same shape as
    leave_enabler_floor — names are exactly the Scryfall response.
    """
    names = {c["name"] for c in cards if c.get("name")}
    fam = (family or "enabler").strip() or "enabler"
    return {
        "id": f"synergy_enabler_{fam}",
        "min": max(0, int(minimum)),
        "names": names,
    }


def enforce_payoff_floors(chosen: list, pool: list, floors: list,
                          target: int) -> list:
    """Reserve slots for plan payoff floors. Never invents cards.

    archetype extras (and any future payoff floor) carry a min on the
    99. card_score ranks by goldfish lift, so a loop half that needs an
    opponent — Exquisite Blood — scores zero and loses the theme contest.
    Honour the floor the way type floors are honoured: keep higher-ranked
    picks, replace from the end with matching pool cards. A floor larger than
    the pool is capped by what exists — an unknown is not padded with fiction.
    Cards already satisfying an earlier floor are not stolen by a later one.
    """
    if not floors or target <= 0:
        return list(chosen[:target])

    result = list(chosen[:target])
    in_result = {c["name"] for c in result}
    protected: set[str] = set()

    for floor in floors:
        need = int(floor.get("min") or 0)
        names = set(floor.get("names") or ())
        if need <= 0 or not names:
            continue

        def matches(c, ns=names):
            return c["name"] in ns

        have = [c for c in result if matches(c)]
        if len(have) >= need:
            protected.update(c["name"] for c in have)
            continue
        # Pool order is EDHREC rank from the payoff search — prefer those.
        donors = [
            c for c in pool
            if matches(c)
            and c["name"] not in in_result
            and not is_land_card(c)
        ]
        # Replace lowest-priority non-matches that are not already reserved.
        victims = [
            c for c in reversed(result)
            if not matches(c) and c["name"] not in protected
        ]
        for victim, donor in zip(victims, donors):
            if len(have) >= need:
                break
            idx = next(i for i, c in enumerate(result) if c["name"] == victim["name"])
            result[idx] = donor
            in_result.discard(victim["name"])
            in_result.add(donor["name"])
            have.append(donor)
        protected.update(c["name"] for c in have)
    return result


def theme_creature_types(brief: dict) -> list[str]:
    """All creature-type values from the brief theme (sibling OR tribes).

    "horses and knights" must keep horse *and* knight
    in accounting and tribe floors — not only the first `t:` part.
    Broad card types (creature, artifact, …) are not a tribe.
    """
    theme = brief_theme(brief)
    if not theme:
        return []
    if resolve._grammar is None:
        resolve.load_grammar()
    hit = resolve.resolve_theme(theme)
    out: list[str] = []
    for part in hit.get("parts") or []:
        if part.get("operator") != "t":
            continue
        if part.get("catalog") != "creature-types":
            continue
        value = str(part.get("value") or "").strip().lower()
        if not value or value in _BROAD_TYPES:
            continue
        if value not in out:
            out.append(value)
    return out


def theme_creature_type(brief: dict) -> str | None:
    """Primary creature-type value, or None when there is no tribe.

    Prefer theme_creature_types() when every sibling must count.
    """
    types = theme_creature_types(brief)
    return types[0] if types else None


def tribe_floor_min(target: int) -> int:
    """How many theme slots must carry the named creature type.

    Floored at 5 when the slot is large enough — the differential membership
    bar for a named tribe is ≥5 in the 99, and theme is where the tribe lives.
    """
    if target <= 0:
        return 0
    if target < 5:
        return target
    return min(target, max(5, round(target / 3)))


def card_has_creature_type(card: dict, tribe: str) -> bool:
    """Whether the card's type line carries this creature subtype as a word."""
    if not tribe:
        return False
    typ = (card.get("type") or card.get("type_line") or "").lower()
    if not typ:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(tribe.lower())}(?![a-z0-9])",
                          typ))


def card_has_any_creature_type(card: dict, tribes: list[str] | None) -> bool:
    """OR across sibling tribes — horse or knight both count."""
    for t in tribes or []:
        if card_has_creature_type(card, t):
            return True
    return False


def enforce_tribe_floor(chosen: list, pool: list, tribe: str | None,
                        need: int, target: int,
                        protected: set[str] | None = None,
                        tribes: list[str] | None = None) -> list:
    """Reserve theme slots for the named creature type(s). Never invents cards.

    behaviour payoff searches (`o:"whenever you gain life"`) are not
    tribe-matched. Prepended ahead of the theme pool they crowd angels out of
    a lifegain+angels deck while vampires survive (drain payoffs *are*
    vampires). Apply after payoff floors; do not steal cards already reserved
    for an archetype loop piece. A floor larger than the pool is capped by
    what exists — an unknown is not padded with fiction.

    when `tribes` has siblings, any of them satisfies the floor.
    """
    tribe_list = [t for t in (tribes or ([tribe] if tribe else [])) if t]
    if not tribe_list or need <= 0 or target <= 0:
        return list(chosen[:target])

    result = list(chosen[:target])
    in_result = {c["name"] for c in result}
    keep = set(protected or ())

    def matches(c, ts=tribe_list):
        return card_has_any_creature_type(c, ts)

    have = [c for c in result if matches(c)]
    if len(have) >= need:
        return result

    donors = [
        c for c in pool
        if matches(c)
        and c["name"] not in in_result
        and not is_land_card(c)
    ]
    victims = [
        c for c in reversed(result)
        if not matches(c) and c["name"] not in keep
    ]
    for victim, donor in zip(victims, donors):
        if len(have) >= need:
            break
        idx = next(i for i, c in enumerate(result) if c["name"] == victim["name"])
        result[idx] = donor
        in_result.discard(victim["name"])
        in_result.add(donor["name"])
        have.append(donor)
    return result


def _mechanic_ladder(base: str, mechanics: list[str], price: str,
                     hints: tuple[str, ...]) -> list[str]:
    """Turn named strategy mechanics into query rungs when they match a role."""
    ladder = []
    for m in mechanics:
        if not any(h in m.lower() for h in hints):
            continue
        for clause in theme_clauses(m):
            ladder.append(f"{base} {clause}{price}")
        if " " in m:
            ladder.append(f"{base} o:{scryterm(m)}{price}")
    return ladder


def _dedupe_queries(queries: list[str]) -> list[str]:
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


async def search_ladder(queries: list[str], limit: int = 60, floor: int = 10) -> tuple[list[dict], str]:
    """Try queries in order; keep hits and top up from later rungs until `floor`.

    A theme that isn't a creature type makes the obvious query return zero. Falling
    straight through to a generic query builds a deck with no relation to what was
    asked for — and says nothing about it. The ladder degrades on purpose, and
    hands back which rung it landed on so the caller can say so.

    a rung with a few on-theme hits is better than a full page of
    unrelated ones. Keep those hits and widen from the next rung rather than
    discarding them. `query` is the first rung that contributed cards — so a
    theme hit topped up from generic still reports the theme rung, and a pure
    generic fallthrough still reports the generic one.
    """
    accumulated: list[dict] = []
    seen: set[str] = set()
    primary = ""
    for q in queries:
        if not q:
            continue
        cards = await search(q, limit=limit)
        if not cards:
            continue
        # Clean hit, nothing earlier: take the full page (floor is a stop
        # condition, not a cap — role search needs the wider pool).
        if not accumulated and len(cards) >= floor:
            return cards[:limit], q
        added = False
        for c in cards:
            name = c.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            accumulated.append(c)
            added = True
            if len(accumulated) >= limit:
                break
        if added and not primary:
            primary = q
        if len(accumulated) >= floor:
            return accumulated[:limit], primary
    return accumulated, primary


def _face_field(c: dict, key: str, joiner: str) -> str:
    """Top-level Scryfall field, or composed from card_faces when null/absent.

    None means "not on the card object" (modal_dfc mana_cost). Empty string is a
    real value (Command Tower's mana_cost) and must not fall through to faces.
    power/toughness are strings — including '*', '1+*', 'X'. Never coerce to int.
    """
    if key in c and c[key] is not None:
        return c[key]
    faces = c.get("card_faces") or []
    parts = [f.get(key) for f in faces if f.get(key)]
    return joiner.join(parts) if parts else ""


def _art_crop(c: dict) -> str | None:
    """Just the illustration, no frame — the asset the UI puts behind the page."""
    uris = c.get("image_uris") or {}
    if not uris:
        faces = c.get("card_faces") or []
        uris = (faces[0].get("image_uris") or {}) if faces else {}
    return uris.get("art_crop")


def _image_normal(c: dict) -> str | None:
    """Only the 'normal' size. DFCs put image_uris on faces, not top-level."""
    uris = c.get("image_uris") or {}
    if not uris:
        faces = c.get("card_faces") or []
        uris = (faces[0].get("image_uris") or {}) if faces else {}
    return uris.get("normal")


# Full trim = model-facing + UI. for_model() strips UI_ONLY before prompts.
UI_ONLY = frozenset({"purchase_uris", "image", "art", "edhrec_rank"})


def for_model(card: dict) -> dict:
    """Projection safe to json.dumps into a model prompt."""
    return {k: v for k, v in card.items() if k not in UI_ONLY}


def tcgplayer_url(card: dict) -> str | None:
    """Per-card TCGplayer link from trim, or None when missing (basics)."""
    url = (card.get("purchase_uris") or {}).get("tcgplayer")
    return url or None


MASS_ENTRY_URL_CAP = 8000


def tcgplayer_mass_entry(commander: str, deck: list[dict],
                         partner: str | None = None) -> dict:
    """Build a TCGplayer mass-entry URL for the commander(s) + main deck.

    Payload is `qty Name` lines joined by `||`. Basics are collapsed (18 Island,
    not eighteen `1 Island` lines). Returns url plus length metadata so the UI
    can refuse a link that would silently truncate.
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for c in deck:
        name = c["name"]
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
    heads = [f"1 {commander}"]
    if partner:
        heads.append(f"1 {partner}")
    lines = heads + [f"{counts[n]} {n}" for n in order]
    payload = "||".join(lines)
    url = ("https://www.tcgplayer.com/massentry"
           f"?productline=Magic&c={quote(payload, safe='')}")
    return {
        "url": url if len(url) <= MASS_ENTRY_URL_CAP else None,
        "payload": payload,
        "length": len(url),
        "ok": len(url) <= MASS_ENTRY_URL_CAP,
    }


def trim_card(c: dict) -> dict:
    """Normalize a raw Scryfall card. See docs/SCRYFALL-CONTRACT.md.

    Oracle text is kept in full — a 220-char cap previously dropped Yuriko's
    entire ninjutsu payoff (147 chars) before the model ever saw the commander.
    """
    return {
        "name": c["name"],
        "cost": _face_field(c, "mana_cost", " // "),
        "cmc": c.get("cmc", 0),
        "type": _face_field(c, "type_line", " // "),
        "text": _face_field(c, "oracle_text", "\n//\n"),
        "usd": (c.get("prices") or {}).get("usd"),
        "color_identity": list(c.get("color_identity") or []),
        "legal": (c.get("legalities") or {}).get("commander", "not_legal"),
        # Boolean field on the card object — not buried in legalities.
        "gc": bool(c.get("game_changer")),
        # Model-facing
        "keywords": list(c.get("keywords") or []),
        "produced_mana": list(c.get("produced_mana") or []),
        "layout": c.get("layout") or "normal",
        "power": _face_field(c, "power", " // "),
        "toughness": _face_field(c, "toughness", " // "),
        # UI / logic only — stripped by for_model()
        "purchase_uris": dict(c.get("purchase_uris") or {}),
        "image": _image_normal(c),
        "art": _art_crop(c),
        "edhrec_rank": c.get("edhrec_rank"),
    }


async def count_matching(q: str) -> int | None:
    """How many cards a query really matches. None when the query failed.

    search() returns at most one Scryfall page — 175 cards — so counting its
    rows understates any larger set: Bloomburrow reported 175 against a true
    280. And it swallows every exception into an empty list, which is
    indistinguishable from a set with nothing in it. Both matter here, because
    a wrong zero tells someone a deck they asked for cannot be built.
    """
    try:
        j = await scry("/cards/search", q=q, order="edhrec", unique="cards")
    except ScryfallError as e:
        print(f"  [count] {q[:60]} failed: {e}")
        return None
    except Exception as e:
        print(f"  [count] {q[:60]} failed: {e}")
        return None
    return int(j.get("total_cards") or 0)


async def search(q: str, limit: int = 60) -> list[dict]:
    """Run a Scryfall query, return trimmed card dicts.

    a transport failure used to `return []`, which every caller
    reads as "Scryfall has no cards matching that" — the house rule violation
    in its purest form. The commander ladder then walked past a rung it never
    actually saw, and a thin pool got reported as a real one.

    An HTTP failure already raises ScryfallError ("not a zero-result query"),
    so callers must tolerate that exception today. Transport failures now do
    the same thing: same exception, same call sites, no new failure mode —
    only the lie removed. `[]` from here now means Scryfall answered and the
    answer was empty.
    """
    try:
        j = await scry("/cards/search", q=q, order="edhrec", unique="cards")
    except ScryfallError:
        raise
    except Exception as e:
        raise ScryfallError(0, f"transport: {type(e).__name__}: {e}") from e
    return [trim_card(c) for c in j.get("data", [])[:limit]]


# Commander picker sizing (pages within one rung). Defined here so
# search_paginated defaults can see them; the commanders section re-exports.
COMMANDER_FLOOR = 8
COMMANDER_PICKS = 5
# Safety ceiling only — never the reported total. Scryfall's total_cards is.
COMMANDER_FETCH_CAP = 2000


async def search_paginated(
    q: str, *, max_cards: int = COMMANDER_FETCH_CAP
) -> tuple[list[dict], int | None, str]:
    """Follow Scryfall pages for one query.

    Returns (cards, total_cards, status):
      ok       — total_cards is Scryfall's figure (may be 0); cards are what we got
      unknown  — a request failed; total_cards is None (never invent 0)

    len(cards) is not the total. A single page is 175; capping the fetch and
    reporting that as the rung size is how "85" hid "479".
    """
    cards: list[dict] = []
    total: int | None = None
    page = 1
    try:
        while True:
            j = await scry("/cards/search", q=q, order="edhrec",
                           unique="cards", page=page)
            if total is None:
                # 404 → {"data": []} with no total_cards — that is a real zero.
                total = int(j.get("total_cards") or 0)
            for raw in j.get("data") or []:
                cards.append(trim_card(raw))
                if len(cards) >= max_cards:
                    return cards, total, "ok"
            if not j.get("has_more"):
                return cards, total if total is not None else 0, "ok"
            page += 1
    except ScryfallError as e:
        print(f"  [search_paginated] {q[:60]} failed: {e}")
        return [], None, "unknown"
    except Exception as e:
        print(f"  [search_paginated] {q[:60]} failed: {e}")
        return [], None, "unknown"


async def search_primary_rung(
    queries: list[str], limit: int = COMMANDER_FETCH_CAP
) -> tuple[list[dict], str, str, int | None]:
    """First ladder rung with hits — for commander paging.

    Returns (cards, query, status, total_cards) where status is:
      ok       — cards may be empty if every rung returned a real zero
      unknown  — a request failed before we saw a real result

    total_cards is Scryfall's count for the rung that hit — not len(cards).
    Does not top up from a weaker rung. "More" must not mean "worse."
    """
    saw_real_empty = False
    for q in queries:
        if not q:
            continue
        cards, total, status = await search_paginated(q, max_cards=limit)
        if status == "unknown":
            return [], q, "unknown", None
        if cards:
            return cards, q, "ok", total
        saw_real_empty = True
    return [], "", "ok" if saw_real_empty else "unknown", (
        0 if saw_real_empty else None)


# ---------------------------------------------------------------- legality
# Asserted on the assembled 100 — query filters are not enough. f:commander and
# id<= keep most junk out, but a ladder miss, backfill, or future card swap can
# still produce a list someone would try to buy.

CARD_POOL_TOURNAMENT = "tournament_legal"
CARD_POOL_CASUAL = "casual"
DEFAULT_CARD_POOL = CARD_POOL_TOURNAMENT


def parse_card_pool(data: dict) -> str:
    """Card pool mode. Default is tournament legal — casual is always opt-in."""
    pool = data.get("card_pool")
    if pool in (CARD_POOL_TOURNAMENT, CARD_POOL_CASUAL):
        return pool
    if data.get("house_rules"):
        return CARD_POOL_CASUAL
    return DEFAULT_CARD_POOL


def pool_format(card_pool: str) -> str:
    """Scryfall format filter for the card pool. Casual drops f:commander."""
    return "f:commander" if card_pool == CARD_POOL_TOURNAMENT else ""


def apply_pool_filter(query: str, card_pool: str) -> str:
    """Attach or strip f:commander on a query string for the active pool."""
    q = re.sub(r"\bf:commander\b", "", query or "").strip()
    q = re.sub(r"\s+", " ", q)
    fmt = pool_format(card_pool)
    if not fmt:
        return q
    return f"{fmt} {q}".strip() if q else fmt


BASIC_CI = {"Plains": ["W"], "Island": ["U"], "Swamp": ["B"],
            "Mountain": ["R"], "Forest": ["G"], "Wastes": []}


def _is_basic(card: dict) -> bool:
    return "basic" in (card.get("type") or "").lower() or card.get("role") == "basic"


def _allows_any_number(card: dict) -> bool:
    return "a deck can have any number of cards named" in (card.get("text") or "").lower()


def _is_legal_commander(card: dict) -> bool:
    t = (card.get("type") or "").lower()
    text = (card.get("text") or "").lower()
    if "legendary" in t and "creature" in t:
        return True
    return "can be your commander" in text


def validate_deck(commander: dict, deck: list[dict], *,
                  partner: dict | None = None,
                  commanders: list[dict] | None = None,
                  card_pool: str = DEFAULT_CARD_POOL,
                  house_rules: bool | None = None) -> dict:
    """Check the finished list against Commander construction rules.

    tournament_legal (default): banned and not_legal cards fail validation.
    casual: ban list does not restrict the build; banned cards are still listed
    for the UI to mark on each row.

    `partner` or `commanders` makes colour identity the union and
    expects a 98-card main deck (100 − commanders in the zone).
    """
    if house_rules is not None:
        card_pool = CARD_POOL_CASUAL if house_rules else CARD_POOL_TOURNAMENT
    casual = card_pool == CARD_POOL_CASUAL
    violations = []
    banned = [c["name"] for c in deck
              if (c.get("legal") == "banned") and not _is_basic(c)]

    if commanders is not None:
        zone = [c for c in commanders if c]
    else:
        zone = resolve_commanders(commander, partner)
    if not zone and commander:
        zone = [commander]
    want = deck_size_for(len(zone) or 1)

    if len(deck) != want:
        violations.append({"code": "size",
                           "detail": f"deck has {len(deck)} cards, want {want}",
                           "cards": []})

    for c in zone:
        if not _is_legal_commander(c):
            violations.append({"code": "commander",
                               "detail": f"{c.get('name')} is not a legal commander",
                               "cards": [c.get("name")]})

    if len(zone) == 2 and not partners_compatible(zone[0], zone[1]):
        violations.append({"code": "partner",
                           "detail": (f"{zone[0].get('name')} and {zone[1].get('name')} "
                                      f"cannot share the command zone"),
                           "cards": [zone[0].get("name"), zone[1].get("name")]})

    cmdr_ci = set(union_color_identity(*zone))
    counts: dict[str, int] = {}
    for c in deck:
        counts[c["name"]] = counts.get(c["name"], 0) + 1

    for name, n in counts.items():
        if n < 2:
            continue
        sample = next(c for c in deck if c["name"] == name)
        if _is_basic(sample) or _allows_any_number(sample):
            continue
        violations.append({"code": "singleton",
                           "detail": f"{name} appears {n} times",
                           "cards": [name]})

    for c in deck:
        if _is_basic(c):
            continue
        card_ci = set(c.get("color_identity") or [])
        if not card_ci <= cmdr_ci:
            violations.append({"code": "color_identity",
                               "detail": f"{c['name']} identity {sorted(card_ci)} "
                                         f"outside commander {sorted(cmdr_ci)}",
                               "cards": [c["name"]]})

    for c in deck:
        if _is_basic(c):
            continue
        legal = c.get("legal") or "not_legal"
        if legal == "banned":
            if not casual:
                violations.append({"code": "banned",
                                   "detail": f"{c['name']} is banned in Commander",
                                   "cards": [c["name"]]})
        elif legal != "legal" and not casual:
            violations.append({"code": "banned",
                               "detail": f"{c['name']} is {legal} in Commander",
                               "cards": [c["name"]]})

    # Deduplicate violation codes that may fire per-card.
    seen_v, uniq = set(), []
    for v in violations:
        key = (v["code"], tuple(v["cards"]), v["detail"])
        if key not in seen_v:
            seen_v.add(key)
            uniq.append(v)

    ok = len(uniq) == 0
    return {"ok": ok, "buyable": ok, "violations": uniq, "banned": banned,
            "mode": card_pool, "card_pool": card_pool, "deck_size": want}


def budget_report(deck: list, budget_total) -> dict:
    """Advisory cost readout. Never reshapes the deck to hit the number.

    Per-card usd< ceilings on role queries still steer what gets considered;
    the sum is reported honestly against the brief so \"where did the money go\"
    is answerable.
    """
    priced = []
    for c in deck:
        raw = c.get("usd")
        if raw in (None, ""):
            continue
        try:
            priced.append((c["name"], float(raw)))
        except (TypeError, ValueError):
            continue
    total = round(sum(p for _, p in priced), 2)
    top = [{"name": n, "usd": round(u, 2)}
           for n, u in sorted(priced, key=lambda x: -x[1])[:3]]
    try:
        brief = float(budget_total) if budget_total not in (None, "") else None
    except (TypeError, ValueError):
        brief = None
    if brief is None:
        delta, vs = None, "no budget stated"
    else:
        delta = round(total - brief, 2)
        if delta > 0:
            vs = f"${delta:.2f} over the ${brief:g} brief"
        elif delta < 0:
            vs = f"${abs(delta):.2f} under the ${brief:g} brief"
        else:
            vs = f"on the ${brief:g} brief"
    return {"total": total, "budget": brief, "delta": delta,
            "vs_brief": vs, "top": top}


def deck_signals(deck: list, mana_report: dict | None = None,
                 brief: dict | None = None) -> dict:
    """Strong-against / watch-out-for lines derived only from decklist counts.

    no opponent model, no recalled matchups. Every line carries the
    numbers it came from so a reader can tell a fact from an opinion.
    type breakdown is always in counts; when brief carries a strategy,
    a thin creature (or spell) count against that plan is a watch line.
    """
    def role_n(role):
        return sum(1 for c in deck if c.get("role") == role)

    wipes = role_n("wipe")
    spot = role_n("spot")
    draw = role_n("draw")
    instants = sum(
        1 for c in deck
        if "instant" in (c.get("type") or "").lower()
        or "flash" in [k.lower() for k in (c.get("keywords") or [])]
    )
    counters = sum(
        1 for c in deck
        if "counter target" in (c.get("text") or "").lower()
    )
    enchant_answers = sum(
        1 for c in deck
        if re.search(
            r"(destroy|exile|return).{0,60}enchantment|enchantment.{0,40}(destroy|exile)",
            (c.get("text") or ""), re.I)
    )
    diag = sim.diagnose(sim.prep(deck, set()))
    rocks = diag["mana_rocks"]
    land_ramp = diag["land_ramp"]
    avg = diag["avg_cmc"]
    ramp = rocks + land_ramp
    breakdown = type_breakdown(deck)

    counts = {
        "wipes": wipes, "spot": spot, "draw": draw, "instants": instants,
        "counters": counters, "enchant_answers": enchant_answers,
        "mana_rocks": rocks, "land_ramp": land_ramp, "avg_cmc": avg,
        **breakdown,
    }

    strong, watch = [], []

    def add(side, text, used):
        """`used` is either key names to read from counts, or explicit numbers.

        The mana-shortage signal passes its own figures — they are per-colour
        and not in `counts` at all — while every other caller passes key names.
        Reading a dict as key names asked counts for "sources_short" and threw
        a KeyError, taking the whole build down. It surfaced on the first
        four-colour deck, because a shortage is near-certain across four
        colours and rare across one or two.
        """
        vals = dict(used) if isinstance(used, dict) else {k: counts[k] for k in used}
        side.append({"text": text, "counts": vals})

    if wipes >= 2:
        add(strong, f"{wipes} board wipes — you can reset a crowded board {wipes} times.",
            ["wipes"])
    elif wipes == 0:
        add(watch, "0 board wipes — a wide board sticks.", ["wipes"])

    if spot >= 5:
        add(strong, f"{spot} spot removal — you answer individual creatures.",
            ["spot"])
    elif spot <= 2:
        add(watch, f"only {spot} spot removal — single threats stick.", ["spot"])

    if enchant_answers == 0 and spot >= 3:
        add(watch,
            f"{spot} spot removal and 0 enchantment answers — "
            "creatures die, an enchantment sticks.",
            ["spot", "enchant_answers"])
    elif enchant_answers >= 2:
        add(strong,
            f"{enchant_answers} enchantment answers — noncreature permanents can be removed.",
            ["enchant_answers"])

    if instants >= 8:
        add(strong,
            f"{instants} instant-speed cards — you can act on an opponent's turn.",
            ["instants"])
    elif instants <= 3:
        add(watch,
            f"only {instants} instant-speed cards — most answers wait for your turn.",
            ["instants"])

    if counters >= 2:
        add(strong,
            f"{counters} counterspells — you can stop a spell on the stack.",
            ["counters"])
    elif counters == 0:
        add(watch, "0 counterspells — you cannot stop a spell on the stack.",
            ["counters"])

    if ramp >= 10:
        add(strong,
            f"{rocks} mana rocks and {land_ramp} land-ramp — the plan comes online early.",
            ["mana_rocks", "land_ramp"])
    elif ramp <= 4:
        add(watch,
            f"only {rocks} mana rocks and {land_ramp} land-ramp — the plan is slow to fund.",
            ["mana_rocks", "land_ramp"])

    if draw >= 7:
        add(strong, f"{draw} draw sources — you refill after a wipe.", ["draw"])
    elif draw <= 4:
        add(watch, f"only {draw} draw sources — recovering from a wipe is hard.",
            ["draw"])

    if avg >= 3.5:
        add(watch,
            f"average mana value {avg} — slower than a low-curve deck around 2.4.",
            ["avg_cmc"])
    elif avg and avg <= 2.6:
        add(strong,
            f"average mana value {avg} — the curve is low enough to cast on pace.",
            ["avg_cmc"])

    if mana_report and not mana_report.get("supported"):
        for s in mana_report.get("shortages") or []:
            add(watch, s["text"],
                 {"sources_short": s["short"], "sources_need": s["need"]})

    # "only 12 creatures for a combat deck" — checkable from the list.
    if brief and brief.get("strategy"):
        bodies = strategy_profile(brief).get("bodies") or "mixed"
        floor = DECK_TYPE_FLOORS.get(bodies)
        if floor:
            axis, need = floor
            have = breakdown.get(axis, 0)
            if have < need:
                label = {
                    "creatures": "creatures",
                    "instants_sorceries": "instants and sorceries",
                }.get(axis, axis)
                plan = {
                    "creatures": "a combat deck",
                    "spells": "a spellslinger deck",
                    "interaction": "a control deck",
                }.get(bodies, "this plan")
                add(watch,
                    f"only {have} {label} — {plan} wants more "
                    f"(this list has {have}, soft floor {need}).",
                    [axis])

    return {"strong": strong, "watch": watch, "counts": counts}


async def edhrec(commander: str) -> dict:
    """EDHREC's open JSON endpoint. Best-effort — the app works without it."""
    slug = commander.lower().split(",")[0].replace(" ", "-").replace("'", "")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://json.edhrec.com/pages/commanders/{slug}.json", headers=UA)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- interrogate

INTERROGATOR = """You are the friend who has played Commander for years and is genuinely
excited that someone new wants to build a deck. You are sitting with them, not interviewing
them. They may know nothing about Magic. That is fine — it is the fun part.

Your job is to NARROW, and to make narrowing enjoyable. A theme like "pirates" is four
different decks (treasure tokens, combat damage triggers, stealing opponents' stuff, pure
flavor) and they are wildly different to play. But a beginner cannot pick between options
she cannot picture, and asking "which one?" cold is a quiz.

## Live instrument counts

The app may attach a final user message that starts with `[instrument]` and a JSON
block of live Scryfall counts. That block is for you, not for her — never quote the
fence or the JSON. Use the numbers in warm prose.

Each span has `status`:
- **ok** — `count` is real; you may say the number.
- **empty** — Scryfall has none in Commander for that span. Say we cannot find it.
  Do not invent a workaround card name.
- **unknown** — the look failed. Say we could not look. **Never treat unknown as zero.**

When she has not chosen among real forks, prefer the denser ok pool. When you hand
off (`still_asking` false), restated the composed count (or honest empty/unknown) in
one short sentence in her words — still no card names from memory.

## First, decide how much you actually need to say

Before writing anything, ask yourself one question: **how much of the deck is still
undecided by what she just said?**

The answer sets the length of your reply. Not your enthusiasm — the length.

- **She named a whole deck.** "Goblins that attack fast and go wide." That is a theme,
  a speed and a shape. There is no fork to show her. Say one line that proves you got it
  and ask the one thing you still need. Two sentences. Do NOT hand her three versions of
  a decision she already made.

- **She named a direction with one real fork.** "I want to play the villain." Ask the
  one question that splits it, in a sentence. Still no menu.

- **She named something that is genuinely several different decks.** "Elves." "Pirates."
  Now the scenes below earn their place, because she cannot choose between things she
  cannot picture.

Three options is a tool for one situation: she said something whose versions play so
differently that naming them is the only way to choose. Reaching for it every turn is a
habit, and it reads as a form — the same shape arriving no matter what she said, which
teaches her that nothing she says changes anything.

**A short reply to a clear answer is respect, not laziness.** The most common failure here
is padding: restating what she said, then offering three flavours of it, then asking a
question she already answered.

## When the fork is real, show it — don't quiz

- **React first.** Whatever she said, take it somewhere. "Villain" is a great answer —
  say what kind of villain Magic lets you be before you ask anything.
- **Offer pictures, not categories.** Instead of "do you want tokens or combat damage?",
  describe decks as scenes — what a turn looks like, what the table does when it works.

- **Options illustrate the space; they do not define it.** Two or three scenes are a way to
  show her what kinds of thing exist, not a menu to choose from. Say so: "those are three of
  them — there are others, and none of them might be it." Then actually mean it. If she picks
  one, that has narrowed *one* axis, not the deck.

- **A pick is not the end of the conversation.** She has told you the flavour of turn she
  wants; you still do not know how fast she wants the game to go, whether she likes decisions
  or wants the deck to do the work, whether she wants to be the table's problem or nobody's,
  or what she wants it to *feel* like to sit behind. Keep going until the commander is
  obvious, not until the topic is answered.
- **One question per message.** Short. It should read like talking, not a form.
- **Only ask what changes the build.** If she already told you, don't confirm it back.
  Skip anything you can infer.

- **Do not rush — but do not pad either.** There is no question quota in either
  direction. The conversation is over when you could name the commander she wants, not
  when you have enough to write *a* brief. Converging in three exchanges because the shape
  of a deck appeared is how someone ends up with a perfectly good deck that isn't hers.
  If you find yourself summarising, ask one more real question instead.

  Not rushing means asking more QUESTIONS when the deck is still open. It does not mean
  writing more WORDS per question. A four-paragraph reply that ends in the same single
  question is not thoroughness; it is a long way of asking one thing.
- **Never name specific Magic cards.** You will get real card data later; anything you
  recall from memory is probably wrong. Describe *effects* ("a creature that makes a
  treasure every time it hits") — never card names.
- **Explain jargon the first time**, in half a sentence, without being condescending.
- **Budget and power level**: don't lead with these. Fold them in near the end, once she
  is excited about something, and offer a sane default so she can just say "yeah, that."

## What this conversation is actually for

You are finding the **commander** she will love. Everything else — the colours, the card
list, the budget — follows from that one card, and she will be looking at its art every game.

So the questions worth asking are the ones that make a commander obviously right or obviously
wrong for her: what she wants to be doing on turn six, whether she'd rather play the villain
or the underdog, whether she wants one big threat or a board full of small ones, whether she
minds a deck that sometimes does nothing. Not: which of my three options.

When you write `commander_query`, encode what **she** said. Do not narrow it with a shape you
inferred and she never confirmed — a query built around your guess returns candidates that
prove your guess, and she never sees the one she would have chosen.

## Do not ask about colours

The commander decides the colours. That is Commander's colour identity rule, and it runs
the other way from every other format: you pick a commander, and the deck's colours follow.
Asking someone new to name colours first is asking her to guess at an answer the game is
about to give her.

So ask what the deck should **do** — the mechanic, the feeling, the turn she wants to have.
The colours fall out of the commander we find for that, and the app explains them to her
once the deck exists.

If she volunteers a colour, take it as flavour and say so warmly — "if a red one fits this
best, great" — never as a constraint you are promising to honour.

**Never say "splash".** There is no splashing in Commander. A card is inside the commander's
colour identity or it is illegal in the deck; there is no partial third colour. Promising a
"light black splash" describes a deck that cannot legally exist, and she will notice when no
black cards arrive. If a third colour would genuinely help, that is a different commander,
not a splash — say that instead.

Before you hand off, say back what you heard in a sentence or two — the deck in plain
language, so she can catch it if you got her wrong.

## How your answer is shaped

Your entire reply is one JSON object — not prose with a JSON block taped on the end.
The app parses that object; a fence inside free text is how briefs used to go missing.

```
{"reply": "what you say to her — questions, readback, warmth. Never put the brief here.",
 "still_asking": true,
 "brief": null}
```

While you still have a question, `still_asking` is true and `brief` is null.
When you know enough, `still_asking` is false and `brief` is this object:

```
{"theme": "...", "angle": "one sentence on what the deck actually does",
 "colors": "leave empty. Colours come from the commander, not the brief. Only fill this
            if she named a colour herself, and even then it is a hint, not a filter",
 "mechanics": ["what the deck should DO — go wide, attack triggers, sacrifice, lifegain"],
 "budget_total": 150,
 "bracket": 2, "commander_query": "a Scryfall query for candidate commanders",
 "flavor_terms": ["words that would appear in the art or flavor text"],
 "fantasy": "the turn she pictures having, in her words — 'a huge board of angels swinging'",
 "playstyle": "one of: proactive | reactive | grindy. See below.",
 "strategy_shape": "one of: incremental | burst | aggro | control | null. See below.",
 "way_preference": "the scene or way she picked, in her words — 'steal their stuff',
                    'go wide', 'gain life'. Empty string if she has not chosen a scene.",
 "way_playstyle": "leave null. The app maps way_preference onto a detectable
                   playstyle id. Do not invent an id.",
 "commander_name": "ONLY if she named a specific card to lead the deck — the exact
                    card name, nothing else. Leave empty otherwise.",
 "table": "who she plays against, in her words — 'my husband and two friends, nothing sweaty'"}
```

Never claim ready with a null brief. Never put card names in `reply` that are not
hers — `commander_name` is the only place a specific card belongs, and only when she
named it.

## Way preference — the scene she chose

After a real fork with live counts, the picture she picks is `way_preference` —
her words, not a jargon label. Leave `way_playstyle` null; the app fills it from
a verified alias table when it can. If she said something we cannot map, keep
her words and leave the id null — do not invent an otag or a fourteenth
playstyle. This is not the same field as `playstyle` (proactive/reactive/grindy).

## Strategy shape — stated, not inferred from your prose

`strategy_shape` is how the deck wins. The app reads this field directly; it does
**not** guess the archetype from the wording of `angle`. Fill it from what she
said. Leaving it null when she already chose a line is how two adjacent wishes
collapse onto the same deck.

- **incremental** — many small triggers, grind, every turn, attrition, chips away
- **burst** — one big turn, combo finish, out of nowhere, a single swing that ends it
- **aggro** — attack with creatures, combat damage as the plan, beat down
- **control** — answers, permission, stopping others from doing their thing
- **null** — only when she has genuinely not chosen among these

If she wants "one big life swing that kills from nowhere", that is **burst** even
when the scene also mentions a board of vampires. If she wants "small drains every
turn", that is **incremental** even when creatures attack along the way. The board
is texture; the win shape is the field.

## Playstyle, and why it is not a question you ask

`playstyle` decides whether the deck gets interaction or more threats, so it has to be
right — but nobody new can answer "are you proactive or reactive?" Read it from what she
already said, and only ask if you genuinely cannot tell:

- **proactive** — she describes doing things: attacking, building a board, going wide,
  casting something huge. Deck wants threats and fewer answers.
- **reactive** — she describes stopping things: countering, destroying, surviving, "I
  hate when someone combos off". Deck wants removal and interaction.
- **grindy** — she describes the long game: value, recursion, attrition, "I want to still
  be there at the end". Deck wants card advantage and wipes.

If she says "swarms of tiny things that add up" that is proactive, and you do not need to
ask. When it is genuinely ambiguous, ask what the turn she enjoys looks like — never the
word "playstyle".

**What she does not want outranks the theme.** "I hate when people combo off", "I want to
be able to stop things", "it's miserable when someone takes the whole turn" — these are the
strongest signal in the conversation and they mean **reactive**, even when the theme sounds
aggressive. A goblin deck belonging to someone who hates losing to combo is a goblin deck
with removal and interaction, and it is not the same deck as one built for someone who only
wants to attack. Do not let the creature type decide this; she told you.

Read it from her whole conversation, not her last message. If she opened by saying what she
hates and then agreed to a fun-sounding plan, she is still reactive.

`table` replaces asking for a bracket number. Nobody new can answer "bracket 2", but
everyone can answer who they play with. Read the bracket from it: a kitchen table with
family is 2, a group that owns fetchlands and knows what a Rhystic Study is is 3.

## If she names a commander, that is the commander

"I was thinking Mister Negative", "can I use Krenko", "my commander is Atraxa" — she has
told you the answer. Put the exact card name in `commander_name` and make `commander_query`
search for **that card**, not for the mechanic you think it represents.

This is the one place where guessing is worst. Someone who names a card has usually already
pictured the deck, sometimes owns the card, and being handed five other commanders reads as
not being listened to. If the card turns out not to be legal as a commander, the app says so
plainly — but that is the app's job to discover, not yours to pre-empt by searching for
something else.

For commander_query use real Scryfall syntax, e.g.
  is:commander t:pirate
  is:commander o:"whenever a creature you control attacks"
Search on what the deck DOES — a type, or the text of the trigger it wants. **Do not put
`id<=` in the query.** Scoping candidates to colours you guessed filters out the commander
that would have been the right answer. Keep it broad enough to return 20+ hits."""


class Turn(BaseModel):
    messages: list[dict]
    # last readiness blocker id the app asked about. Same id on the
    # next brief means the question did not resolve it — degrade honestly.
    readiness_asked: str | None = None


# structured chat output. Prose and the brief used to share one string;
# a JSONDecodeError became brief=null with no signal. The schema keeps them
# apart, and extract_brief_payload() names every failure mode on the wire.
_BRIEF_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "theme": {"type": "string"},
        "angle": {"type": "string"},
        "colors": {"type": "string"},
        "mechanics": {"type": "array", "items": {"type": "string"}},
        "budget_total": {"type": "number"},
        "bracket": {"type": "integer"},
        "commander_query": {"type": "string"},
        "flavor_terms": {"type": "array", "items": {"type": "string"}},
        "fantasy": {"type": "string"},
        "playstyle": {"type": "string"},
        # archetype is stated here, not inferred from angle prose.
        # null = she has not chosen; phrase matching is the fallback only then.
        "strategy_shape": {
            "anyOf": [
                {"type": "string",
                 "enum": ["incremental", "burst", "aggro", "control"]},
                {"type": "null"},
            ],
        },
        # scene she picked (her words) + mapped detectable playstyle.
        # way_playstyle is filled by the app from the alias map — model may
        # leave it null; freestyle ids outside the 13 are overwritten.
        "way_preference": {"type": "string"},
        "way_playstyle": {
            "anyOf": [
                {"type": "string",
                 "enum": [
                     "aristocrats", "blink", "counters", "voltron",
                     "spellslinger", "landfall", "tokens", "reanimator",
                     "stax", "group_hug", "lifegain", "artifacts", "tribal",
                 ]},
                {"type": "null"},
            ],
        },
        "commander_name": {"type": "string"},
        "table": {"type": "string"},
    },
    "required": [
        "theme", "angle", "colors", "mechanics", "budget_total", "bracket",
        "commander_query", "flavor_terms", "fantasy", "playstyle",
        "strategy_shape", "way_preference", "way_playstyle",
        "commander_name", "table",
    ],
    "additionalProperties": False,
}

CHAT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "still_asking": {"type": "boolean"},
        "brief": {"anyOf": [{"type": "null"}, _BRIEF_OBJECT_SCHEMA]},
    },
    "required": ["reply", "still_asking", "brief"],
    "additionalProperties": False,
}


def _strip_code_fence(raw: str) -> str:
    """Drop a wrapping ``` / ```json fence if the model added one anyway."""
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```(?:json|brief)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _brief_looks_present(brief: Any) -> bool:
    return isinstance(brief, dict) and bool(
        (brief.get("theme") or "").strip()
        or (brief.get("angle") or "").strip()
        or (brief.get("commander_query") or "").strip()
    )



def _peel_trailing_reply_envelope(text: str) -> dict | None:
    """Parse a trailing {reply, still_asking, brief} envelope after prose.

    bare trailing envelope. same shape inside a trailing
    ``` / ```json fence — the closing fence is not payload, so accept it.
    """
    decoder = json.JSONDecoder()
    last_valid = None
    # Tolerate whitespace after `{` — models emit `{ "reply"` as well as
    # `{"reply"`.
    for match in re.finditer(r'\{\s*"reply"', text):
        pos = match.start()
        try:
            envelope, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            continue
        # Only fence markers / whitespace may follow — never eat a real
        # trailing code block that is not our envelope.
        tail = text[end:].strip()
        if (isinstance(envelope, dict) and "reply" in envelope
                and tail in ("", "```")):
            last_valid = envelope
    return last_valid


def extract_brief_payload(
    text: str,
    *,
    stop_reason: str | None = None,
    what: str = "chat",
) -> dict:
    """Pull prose + brief out of a model reply. Never a silent null.

    Three states the caller can tell apart (see docs/DERIVATION.md):
      brief present      → brief is a dict, still_asking False, brief_error None
      still interrogating → brief None, still_asking True, brief_error None
      extraction failed   → brief None, still_asking False, brief_error set

    Accepts the structured envelope ({reply, still_asking, brief}) and the
    legacy ```brief fence, so CLI replies and older transcripts still grade.
    """
    raw = text or ""
    if stop_reason == "max_tokens":
        print(f"  [{what}] TRUNCATED at max_tokens ({len(raw)} chars)")
        return {
            "text": raw.strip(),
            "brief": None,
            "still_asking": False,
            "brief_error": "truncated",
        }

    stripped = _strip_code_fence(raw)

    # Structured envelope — preferred path (output_config / prompt).
    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        envelope = None

    if envelope is None:
        envelope = _peel_trailing_reply_envelope(stripped)

    if isinstance(envelope, dict) and "reply" in envelope:
        reply = envelope.get("reply")
        if not isinstance(reply, str):
            reply = "" if reply is None else str(reply)
        brief = envelope.get("brief")
        still = envelope.get("still_asking")
        if _brief_looks_present(brief):
            return {
                "text": reply.strip(),
                "brief": brief,
                "still_asking": False,
                "brief_error": None,
            }
        if brief is not None and not isinstance(brief, dict):
            print(f"  [{what}] brief is {type(brief).__name__}, not an object")
            return {
                "text": reply.strip(),
                "brief": None,
                "still_asking": False,
                "brief_error": "unparseable",
            }
        if isinstance(brief, dict):
            # Parsed object, but no theme/angle/query — claimed a brief, delivered none.
            print(f"  [{what}] brief object empty of theme/angle/query")
            return {
                "text": reply.strip(),
                "brief": None,
                "still_asking": False,
                "brief_error": "missing_brief",
            }
        if still is False:
            print(f"  [{what}] still_asking=false with null brief")
            return {
                "text": reply.strip(),
                "brief": None,
                "still_asking": False,
                "brief_error": "missing_brief",
            }
        return {
            "text": reply.strip(),
            "brief": None,
            "still_asking": True,
            "brief_error": None,
        }

    # Legacy fence — kept so a CLI reply that still emits ```brief is graded,
    # never swallowed into brief=null with no reason.
    if "```brief" in raw:
        try:
            fenced = raw.split("```brief", 1)[1].split("```", 1)[0]
        except IndexError:
            fenced = ""
        prose = raw.split("```brief", 1)[0].strip()
        try:
            brief = json.loads(fenced)
        except json.JSONDecodeError as e:
            print(f"  [{what}] unparseable brief fence ({e}); "
                  f"stop_reason={stop_reason}, {len(fenced)} chars; "
                  f"raw={fenced[:400]!r}")
            return {
                "text": prose,
                "brief": None,
                "still_asking": False,
                "brief_error": "unparseable",
            }
        if _brief_looks_present(brief):
            return {
                "text": prose,
                "brief": brief,
                "still_asking": False,
                "brief_error": None,
            }
        print(f"  [{what}] brief fence parsed but empty of theme/angle/query")
        return {
            "text": prose,
            "brief": None,
            "still_asking": False,
            "brief_error": "missing_brief",
        }

    # Prose only, or JSON that is not our envelope — still interrogating.
    # A bare object that failed the envelope shape is an extraction failure
    # only when it looked like it meant to be structured.
    if stripped.startswith("{") and envelope is None:
        print(f"  [{what}] unparseable JSON reply ({len(stripped)} chars); "
              f"stop_reason={stop_reason}; raw={stripped[:400]!r}")
        return {
            "text": raw.strip(),
            "brief": None,
            "still_asking": False,
            "brief_error": "unparseable",
        }
    if isinstance(envelope, dict):
        # Parsed as JSON but missing reply — not the chat envelope.
        print(f"  [{what}] JSON reply missing 'reply' key; keys={list(envelope)[:8]}")
        return {
            "text": raw.strip(),
            "brief": None,
            "still_asking": False,
            "brief_error": "unparseable",
        }

    return {
        "text": raw.strip(),
        "brief": None,
        "still_asking": True,
        "brief_error": None,
    }


@app.post("/api/chat")
async def chat(t: Turn):
    # structured outputs on the API path so the brief cannot arrive as
    # malformed prose. CLI ignores output_config (llm.py **_ignored) and relies
    # on the INTERROGATOR envelope + extract_brief_payload().
    #
    # build a live instrument from theme hint *before* the model call
    # so forks can cite real Scryfall counts. Injected messages are for the
    # model only — response.text never includes the [instrument] fence.
    theme_hint = theme_hint_for_instrument(t.messages, None)
    instrument: dict | None = None
    if theme_hint:
        try:
            instrument = await build_instrument(theme_hint)
        except Exception as e:
            print(f"  [instrument] pre-brief failed: {type(e).__name__}: {e}")
            instrument = None
    model_messages = messages_with_instrument(t.messages, instrument)

    kwargs: dict[str, Any] = dict(
        model=MODEL, max_tokens=2000, thinking={"type": "disabled"},
        system=INTERROGATOR, messages=model_messages,
    )
    if getattr(client, "via", None) == "api":
        kwargs["output_config"] = {
            "format": {"type": "json_schema", "schema": CHAT_OUTPUT_SCHEMA},
        }
    r = await client.messages.create(**kwargs)
    raw = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
    got = extract_brief_payload(
        raw, stop_reason=getattr(r, "stop_reason", None), what="chat")
    text = got["text"]
    brief = got["brief"]
    still_asking = got["still_asking"]
    brief_error = got["brief_error"]

    # map way_preference → way_playstyle before readiness / handoff.
    if brief is not None:
        apply_way_fields(brief)

    # countable readiness before readback. The model emitting a brief
    # is not permission to proceed — unresolved terms and thin pools block.
    readiness = None
    if brief is not None:
        readiness = await check_readiness(
            brief, previously_asked=t.readiness_asked)
        if not readiness.get("ready"):
            # Keep chatting. Append the named question so the next turn sees it.
            q = (readiness.get("question") or "").strip()
            if q:
                text = f"{text}\n\n{q}".strip() if text else q
            still_asking = True
        # Refresh instrument from the brief theme/colours once we have them.
        bt = brief_theme(brief)
        colors = brief.get("colors") if isinstance(brief.get("colors"), str) else ""
        if bt:
            try:
                instrument = await build_instrument(
                    bt, colors, readiness=readiness)
            except Exception as e:
                print(f"  [instrument] post-brief failed: {type(e).__name__}: {e}")

    # at handoff, suppress healthy pool magnitudes (builder-only) and
    # append a commander-narrowing beat instead of "278 cards for angels".
    narrowing = None
    if not still_asking and brief is not None and (
            readiness is None or readiness.get("ready")):
        instrument = strip_pool_magnitudes_for_handoff(instrument)
        try:
            narrowing = await build_narrowing_turn(brief)
        except Exception as e:
            print(f"  [narrowing] handoff failed: {type(e).__name__}: {e}")
            narrowing = {
                "status": "unknown",
                "candidates": [],
                "commander_count": None,
                "note": ("I could not look up commanders just now — not the "
                         "same as finding none."),
            }

    text = ensure_closing_instrument_readback(
        text, instrument, still_asking=still_asking)
    if narrowing is not None:
        text = ensure_closing_narrowing(text, narrowing)
    # Never leak the fence into what she reads.
    if text and INSTRUMENT_FENCE in text:
        text = text.split(INSTRUMENT_FENCE, 1)[0].strip()

    return {
        "text": text,
        "brief": brief,
        "still_asking": still_asking,
        "brief_error": brief_error,
        "readiness": readiness,
        "instrument": instrument,
        "narrowing": narrowing,
    }


# ---------------------------------------------------------------- commanders
# COMMANDER_FLOOR / COMMANDER_PICKS / COMMANDER_FETCH_CAP: defined above search.


def page_commanders(
    cards: list[dict] | None,
    *,
    offset: int = 0,
    n: int = COMMANDER_PICKS,
    rung_status: str = "ok",
    rung_total: int | None = None,
) -> dict:
    """Slice a rung's commanders for the picker. Never descends a rung here.

    rung_status:
      ok       — cards is the real set (may be empty)
      unknown  — we could not look; rung_total is None, not 0

    rung_total: Scryfall's true count when known. Defaults to len(cards).
    Never silently report a fetch-cap as the set size when the rung is larger.
    """
    offset = max(0, int(offset or 0))
    n = max(1, int(n or COMMANDER_PICKS))
    if rung_status == "unknown":
        return {
            "cards": [],
            "rung_total": None,
            "offset": offset,
            "has_more": None,
            "rung_exhausted": False,
            "shown_of": None,
            "status": "unknown",
            "note": ("Could not look up more commanders — Scryfall did not "
                     "answer. That is unknown, not 'no more'."),
        }
    pool = list(cards or [])
    total = len(pool) if rung_total is None else max(0, int(rung_total))
    slice_ = pool[offset:offset + n]
    has_more = (offset + n) < total
    rung_exhausted = not has_more
    shown = len(slice_)
    note = ""
    if offset > 0 and shown == 0 and total > 0:
        note = (f"That is all {total} commanders on this rung — not descending "
                f"to a weaker query.")
        rung_exhausted = True
    elif offset > 0 and shown == 0 and total == 0:
        note = "No commanders on this rung."
    if shown == 0 and has_more and offset < total:
        note = (note or (f"More commanders exist on this rung ({total}) — "
                         f"fetch did not hold them yet.")).strip()
    return {
        "cards": slice_,
        "rung_total": total,
        "offset": offset,
        "has_more": has_more,
        "rung_exhausted": rung_exhausted,
        "shown_of": f"{shown} of {total}",
        "status": "ok",
        "note": note,
    }


def rank_commanders_pool(cards: list[dict], brief: dict) -> list[dict]:
    """Stable order for paging: fit gates first, then EDHREC (more-played first).

    Never a weighted sum of theme and strategy — fit_rank_key is lexicographic.
    """
    scored: list[tuple[tuple, tuple, dict]] = []
    for c in cards or []:
        theme = cmd_kb.theme_fit(c, brief)
        strat = cmd_kb.strategy_fit(c, brief)
        scored.append((cmd_kb.fit_rank_key(theme, strat), _edhrec_key(c), c))
    scored.sort(key=lambda row: (
        -row[0][0], -row[0][1], -row[0][2], -row[0][3],
        row[1][0], row[1][1],
    ))
    return [c for _, _, c in scored]

PARTNER_NOTICE = (
    "This commander can pair with a second one. "
    "Pick a legal partner next — colour identity becomes the union of both."
)

# Partner / Partner with / Friends forever — ability word, not "partnership".
_PARTNER_TEXT = re.compile(
    r"(?:^|\n)\s*Partner(?:\s+with\b|[^\w]|$)|Friends forever\b",
    re.IGNORECASE,
)
_PARTNER_WITH_TEXT = re.compile(
    r"(?:^|\n)\s*Partner with ([^\n(]+)",
    re.IGNORECASE,
)
_WUBRG = "WUBRG"


def has_partner(card: dict) -> bool:
    """True if this commander can legally pair with a second commander."""
    return partner_kind(card) is not None


def partner_kind(card: dict) -> str | None:
    """Which pairing mechanism this card uses, if any.

    Order is load-bearing: 'Partner with' must win over bare 'Partner', or every
    named pair is misclassified as a free-for-all Partner card.
    """
    for kw in card.get("keywords") or []:
        low = (kw or "").lower().strip()
        if low.startswith("partner with"):
            return "partner_with"
        if low == "friends forever":
            return "friends_forever"
        if low == "partner":
            return "partner"
    text = card.get("text") or ""
    if _PARTNER_WITH_TEXT.search(text):
        return "partner_with"
    if re.search(r"Friends forever\b", text, re.IGNORECASE):
        return "friends_forever"
    if re.search(r"(?:^|\n)\s*Partner(?:[^\w]|$)", text, re.IGNORECASE):
        return "partner"
    return None


def partner_with_name(card: dict) -> str | None:
    """The exact name named by 'Partner with …', or None."""
    for kw in card.get("keywords") or []:
        m = re.match(r"partner with\s+(.+)", kw or "", re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
    m = _PARTNER_WITH_TEXT.search(card.get("text") or "")
    if not m:
        return None
    return m.group(1).strip().rstrip(".")


def partners_compatible(a: dict, b: dict) -> bool:
    """True when a and b may legally share the command zone."""
    if not a or not b:
        return False
    if (a.get("name") or "") == (b.get("name") or ""):
        return False
    ka, kb = partner_kind(a), partner_kind(b)
    if not ka or not kb:
        return False
    if ka == "partner" and kb == "partner":
        return True
    if ka == "friends_forever" and kb == "friends_forever":
        return True
    if ka == "partner_with" or kb == "partner_with":
        # Named pairs only — never a free Partner or Friends forever.
        if ka != "partner_with" or kb != "partner_with":
            return False
        return (partner_with_name(a) == b.get("name")
                and partner_with_name(b) == a.get("name"))
    return False


def union_color_identity(*cards: dict) -> list[str]:
    """WUBRG-ordered union of every commander's colour identity."""
    found: set[str] = set()
    for c in cards:
        if not c:
            continue
        found.update(c.get("color_identity") or [])
    return [c for c in _WUBRG if c in found]


def identity_string(ci_list: list[str] | None) -> str:
    """Scryfall id<= string; colourless decks use 'C'."""
    s = "".join(ci_list or [])
    return s or "C"


def brief_color_identity(brief: dict | None) -> str:
    """WUBRG letters she named on the brief, or '' when colours are unknown.

    an absent constraint is not id<=C. Empty means leave the
    commander ladder unscoped — do not invent a colour filter.
    """
    raw = (brief or {}).get("colors")
    if not raw:
        return ""
    if isinstance(raw, (list, tuple)):
        raw = "".join(str(c) for c in raw)
    found = {c for c in str(raw).upper() if c in _WUBRG}
    return "".join(c for c in _WUBRG if c in found)


def brief_theme(brief: dict | None) -> str:
    """Theme string from a brief. Non-strings (None) are absent — never 'None'.

    `brief.get("theme", "")` returns None when the key is present with
    a null value (JSON null from a swallowed parse). Stringifying that into a
    Scryfall query searches for the card type "None". Guard at the boundary.
    """
    t = (brief or {}).get("theme")
    if not isinstance(t, str):
        return ""
    return t.strip()


def apply_way_fields(brief: dict | None) -> dict:
    """keep her words; set way_playstyle only from the alias map.

    Model freestyle outside the 13 is overwritten. Unmapped preference keeps
    the said string and nulls the id — never invents an otag or playstyle.
    """
    brief = brief if isinstance(brief, dict) else {}
    pref = brief.get("way_preference")
    if not isinstance(pref, str):
        pref = ""
    pref = pref.strip()
    brief["way_preference"] = pref
    brief["way_playstyle"] = playstyles.map_way_playstyle(pref) if pref else None
    return brief


def prefer_way_playstyle(cards: list[dict], way_playstyle: str | None
                         ) -> list[dict]:
    """Soft-prefer commanders whose detected ways include way_playstyle.

    Stable. Does not drop anyone. Same spirit as synergy.prefer_glued.
    """
    pid = (way_playstyle or "").strip()
    if not pid or not cards:
        return list(cards)
    hit: list[dict] = []
    rest: list[dict] = []
    for c in cards:
        status, ways = cmd_kb._ways_for_card(c)
        if status == "unknown":
            rest.append(c)
            continue
        ids = {w.get("playstyle") for w in (ways or []) if w.get("playstyle")}
        (hit if pid in ids else rest).append(c)
    return hit + rest


def brief_is_usable(brief: dict | None) -> tuple[bool, str]:
    """Whether a brief can drive /api/commanders or /api/build.

    A missing theme is not a generic deck — it is an unknown. Refuse with a
    plain reason so the UI keeps asking rather than shipping an off-theme 99.
    """
    if brief_theme(brief):
        return True, ""
    return False, "Brief has no theme — keep asking what the deck is about."


def deck_size_for(n_commanders: int) -> int:
    """Main-deck size: 99 with one commander, 98 with a partner pair."""
    n = max(1, min(2, int(n_commanders or 1)))
    return 100 - n


def resolve_commanders(commander: dict, partner: dict | None = None) -> list[dict]:
    """Normalize to a 1- or 2-card command zone list."""
    out = [commander] if commander else []
    if partner and partner.get("name") and partner.get("name") != commander.get("name"):
        out.append(partner)
    return out


def _commander_haystack(card: dict) -> str:
    return " ".join([
        card.get("name") or "",
        card.get("type") or "",
        card.get("text") or "",
        " ".join(card.get("keywords") or []),
    ]).lower()


def _theme_fit_score(card: dict, brief: dict) -> int:
    theme = (brief.get("theme") or "").strip()
    if not theme:
        return 0
    terms = [theme.lower()] + [t.lower() for t in singulars(theme) if t]
    # de-dupe, keep order
    seen, uniq = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    hay = _commander_haystack(card)
    return sum(1 for t in uniq if t in hay)


def _fantasy_fit_score(card: dict, brief: dict) -> int:
    terms = [t.lower() for t in (brief.get("flavor_terms") or []) if t]
    if not terms:
        return 0
    hay = _commander_haystack(card)
    return sum(1 for t in terms if t in hay)


def _edhrec_key(card: dict):
    rank = card.get("edhrec_rank")
    return (rank is None, rank if rank is not None else 10**9)


def _usd_value(card: dict) -> float | None:
    raw = card.get("usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def legal_commanders_only(cards: list[dict]) -> list[dict]:
    """Drop anything that cannot legally lead a deck.

    The queries already filter for this, and a query is one typo — or one
    model-written OR — away from not filtering at all. A commander is a
    legendary creature, or a card that says it can be your commander. Checking
    the card we got back is the only version of this that cannot be bypassed.
    """
    out = []
    for c in cards or []:
        type_line = (c.get("type") or "").lower()
        text = (c.get("text") or "").lower()
        if ("legendary" in type_line and "creature" in type_line) \
                or "can be your commander" in text:
            out.append(c)
    return out


def label_commander_picks(cards: list[dict], brief: dict, n: int = COMMANDER_PICKS
                          ) -> list[tuple[dict, str]]:
    """Pick n commanders and earn a reason tag for each from Scryfall data.

    Labels are assigned in code, never invented by the model. One card, one label.
    theme fit and strategy fit are separate axes — both can earn a
    seat. Ranking uses gates/tiebreaks (see commanders.fit_rank_key), never a
    weighted sum of the two scores.
    """
    remaining = list(cards)
    selected: list[tuple[dict, str]] = []

    def take(chooser, reason: str) -> None:
        if len(selected) >= n or not remaining:
            return
        chosen = chooser(remaining)
        if not chosen:
            return
        selected.append((chosen, reason))
        remaining[:] = [c for c in remaining if c["name"] != chosen["name"]]

    def most_played(pool):
        return min(pool, key=_edhrec_key) if pool else None

    def best_fit(pool):
        scored = [( _theme_fit_score(c, brief), c) for c in pool]
        scored = [(s, c) for s, c in scored if s > 0]
        if not scored:
            return None
        # Prefer stronger theme hit; tie-break toward more-played.
        return max(scored, key=lambda sc: (sc[0], -_edhrec_key(sc[1])[1]))[1]

    def best_strategy(pool):
        # strategy is its own axis. A commander that nails how she
        # wants to play earns a seat even when the theme hit is weak — and is
        # labelled that way, not silently ranked down.
        from deckwright import commanders as cmd_kb
        scored = []
        for c in pool:
            strat = cmd_kb.strategy_fit(c, brief)
            if strat.get("status") != "ok" or not (strat.get("score") or 0):
                continue
            theme = cmd_kb.theme_fit(c, brief)
            scored.append((cmd_kb.fit_rank_key(theme, strat), c))
        if not scored:
            return None
        return max(scored, key=lambda sc: (sc[0], -_edhrec_key(sc[1])[1]))[1]

    def cheapest(pool):
        priced = [(v, c) for c in pool if (v := _usd_value(c)) is not None]
        if not priced:
            return None
        return min(priced, key=lambda vc: vc[0])[1]

    def fantasy(pool):
        if not brief.get("flavor_terms"):
            return None
        scored = [(_fantasy_fit_score(c, brief), c) for c in pool]
        scored = [(s, c) for s, c in scored if s > 0]
        if not scored:
            return None
        return max(scored, key=lambda sc: (sc[0], -_edhrec_key(sc[1])[1]))[1]

    # If she named a card, it leads — labelled as hers, not as a recommendation.
    # Anything else at the top reads as the tool talking over her.
    named = str((brief or {}).get("commander_name") or "").strip().lower()
    if named:
        take(lambda pool: next(
            (c for c in pool if c["name"].lower() == named
             or c["name"].lower().split(" // ")[0] == named), None),
            "the one you asked for")

    take(most_played, "most played")
    take(best_fit, "best fit for your theme")
    take(best_strategy, "best fit for your strategy")
    take(cheapest, "cheapest")
    take(fantasy, "closest to your fantasy")
    while len(selected) < n and remaining:
        c = most_played(remaining)
        if not c:
            break
        selected.append((c, "also highly played"))
        remaining = [x for x in remaining if x["name"] != c["name"]]
    return selected


# Scryfall's `is:commander` is broader than this format allows — it matches 41
# Vehicles and 64 non-creature cards. A Vehicle is not a creature card, so it
# cannot be your commander in Commander. Narrow it to what the rules permit:
# a legendary creature, or a card that says it can be your commander.
IS_COMMANDER = 'is:commander (t:creature or o:"can be your commander")'


# Scryfall query atoms we peel off when relaxing a commander_query.
_O_CLAUSE = re.compile(r'\bo:(?:"[^"]*"|[^\s()]+)', re.IGNORECASE)
_TK_CLAUSE = re.compile(r'\b(?:t|keyword):(?:"[^"]*"|[^\s()]+)', re.IGNORECASE)


def _clause_value_len(clause: str) -> int:
    """Length of the value side of a Scryfall clause (quotes stripped)."""
    _, _, val = clause.partition(":")
    return len(val.strip('"'))


def _drop_clauses(query: str, drop: set[str]) -> str:
    """Remove whole clauses from a query string, collapsing whitespace."""
    out = query
    for c in drop:
        out = re.sub(r'(?<!\S)' + re.escape(c) + r'(?!\S)', " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _query_has_behaviour(q: str) -> bool:
    """True when a query still carries a behaviour clause (o: / otag:), not bare type.

    model-query relaxations that have thrown away every behaviour
    clause are less specific than the composed theme and must not outrank it.
    """
    if not q:
        return False
    return bool(_O_CLAUSE.search(q) or re.search(r"\botag:", q, re.I))


def commander_ladder(brief: dict) -> tuple[list[str], set[str]]:
    """Commander query ladder, plus the rungs that mean "we gave up on the theme".

    when she named colours on the brief, every search rung carries
    id<=<identity>. When she has not, leave the ladder unscoped — an absent
    constraint is not id<=C. /api/build still derives ci from the chosen
    commander either way.

    rung order:
      1. named commander (exact), when she gave one
      2. composed theme (thing + behaviour) — before any bare-type rung
      3. model commander_query and its o: relaxations, while they still
         carry a behaviour clause
      4. bare type (model t:/keyword: only, then theme type half)
      5. bare behaviour (theme behaviour half)
      6. remaining theme_clauses fallbacks
      7. generic is:commander — caller warns when this rung is used
    """
    card_pool = parse_card_pool(brief)
    ci = brief_color_identity(brief)

    def rung(extra: str) -> str:
        # Never trust the model query to constrain the search to actual
        # commanders. Without is:commander, f:commander alone means "legal in
        # the format" — which matched every card and returned Sol Ring.
        # Parenthesise the model's half. Scryfall's OR binds looser than the
        # implicit AND, so "FILTER a OR b" parses as "(FILTER AND a) OR b" and
        # that second branch escapes the commander filter entirely. It is how
        # Necromancy and Skrelv's Hive — both enchantments — were offered as
        # commanders during a playtest.
        q = f"{IS_COMMANDER} ({extra})".strip() if extra else IS_COMMANDER
        if ci:
            q = f"{q} id<={ci}"
        return apply_pool_filter(q, card_pool)

    # She named a card. That is the answer, and it goes at the top of the ladder
    # as an exact-name lookup — no theme search can outrank it. Someone who says
    # "my commander is Mister Negative" has already pictured the deck, and being
    # handed five other commanders reads as not being listened to.
    named = str(brief.get("commander_name") or "").strip()

    raw = (brief.get("commander_query") or "").strip()
    # Work on the model's clauses only — IS_COMMANDER's own
    # o:"can be your commander" must not be peeled off as a relaxation.
    model_q = re.sub(r"\bis:commander\b", "", raw, flags=re.IGNORECASE)
    model_q = re.sub(r"\s+", " ", model_q).strip()

    ladder: list[str] = []
    if named:
        safe = named.replace('"', "")
        # Exact name, and without the pool filter — a card she owns should be
        # found and then judged, not silently filtered out before she sees it.
        ladder.append(f'{IS_COMMANDER} !"{safe}"')

    # composition before the type alone. The model often
    # writes t:angel (or t:angel plus loose o: fragments); its relaxation
    # chain used to land on bare t:angel *above* (t:angel otag:lifegain), so
    # search_ladder filled with tribe commanders and never reached the
    # behaviour half. Emit the composed rung first; keep model queries that
    # still carry behaviour next; defer bare type until after composition.
    # None is absent — never search t:None.
    theme = brief_theme(brief)
    if resolve._grammar is None:
        resolve.load_grammar()
    hit = resolve.resolve_theme(theme)
    composed = hit.get("expression") or ""
    parts = hit.get("parts") or []
    if composed and len(parts) > 1:
        ladder.append(rung(composed))

    bare_model: list[str] = []
    if model_q:
        if _query_has_behaviour(model_q):
            ladder.append(rung(model_q))
        else:
            bare_model.append(model_q)

        o_clauses: list[str] = []
        for c in _O_CLAUSE.findall(model_q):
            if c not in o_clauses:
                o_clauses.append(c)
        drop_order = sorted(o_clauses, key=_clause_value_len, reverse=True)
        dropped: list[str] = []
        for c in drop_order:
            dropped.append(c)
            relaxed = _drop_clauses(model_q, set(dropped))
            if not relaxed:
                continue
            if _query_has_behaviour(relaxed):
                ladder.append(rung(relaxed))
            else:
                bare_model.append(relaxed)

        tk: list[str] = []
        for c in _TK_CLAUSE.findall(model_q):
            if c not in tk:
                tk.append(c)
        if tk:
            bare_model.append(" ".join(tk))

    # Bare-type / bare-behaviour rungs: model leftovers first, then theme_clauses
    # (composed already emitted above; _dedupe_queries drops the repeat).
    for extra in bare_model:
        ladder.append(rung(extra))
    for c in theme_clauses(theme):
        ladder.append(rung(c))

    generic_q = rung("")
    generic = {generic_q}
    ladder.append(generic_q)
    return _dedupe_queries(ladder), generic


def _peel_first_json(text: str, expect: str = "list"):
    """First complete JSON value in text, or None.

    models often emit a valid object then a trailing sentence
    (`Extra data: line N`). json.loads rejects the whole string; a retry
    reproduces the same shape. raw_decode keeps the value and drops the rest.
    Truncated mid-value still returns None — that case may need a retry.
    """
    s = (text or "").lstrip()
    if not s:
        return None
    start = next((i for i, ch in enumerate(s) if ch in "{["), None)
    if start is None:
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(s, start)
    except json.JSONDecodeError:
        return None
    if expect == "object":
        return parsed if isinstance(parsed, dict) else None
    return parsed if isinstance(parsed, list) else None


def parse_json_list(response, what: str, expect: str = "list"):
    """Parse a JSON array/object out of a model response, loudly.

    NOTE on max_tokens: Sonnet 5 runs adaptive thinking by default and
    max_tokens caps thinking PLUS output. A call can burn the whole budget
    thinking and return zero characters — which looks like truncation but is
    not fixed by raising the ceiling. Every structured-JSON call here sets
    thinking={"type": "disabled"}: they format known data, they don't reason.

    Truncation is the failure mode this codebase keeps hitting: the response is
    cut at max_tokens, json.loads() raises, the caller swallows it, and every
    field arrives empty with nothing in the logs. Three separate calls have been
    bitten by this. Say which one broke and why.

    when loads fails with trailing prose after a complete value, peel
    that value before the caller burns a second identical attempt.
    """
    raw = "".join(b.text for b in response.content if b.type == "text")
    raw = raw.replace("```json", "").replace("```", "").strip()
    if response.stop_reason == "max_tokens":
        print(f"  [{what}] TRUNCATED at max_tokens "
              f"({response.usage.output_tokens} out) — raise max_tokens")
    try:
        parsed = json.loads(raw)
        if expect == "object":
            return parsed if isinstance(parsed, dict) else {}
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError as e:
        peeled = _peel_first_json(raw, expect=expect)
        if peeled is not None:
            print(f"  [{what}] peeled trailing text after JSON "
                  f"(was: {e}); stop_reason={response.stop_reason}, "
                  f"{len(raw)} chars")
            return peeled
        print(f"  [{what}] unparseable JSON ({e}); "
              f"stop_reason={response.stop_reason}, {len(raw)} chars")
        return {} if expect == "object" else []


# Boilerplate every commander ladder query carries. Stripped before asking
# why.explain_group what the rung was about — otherwise o:"can be your
# commander" wins over the theme clause that actually selected the pool.
_COMMANDER_QUERY_NOISE = re.compile(
    r"""\bis:commander\b|\(t:creature\s+or\s+o:"can be your commander"\)""",
    re.I)


def plain_commander_reason(card: dict, label: str, query: str = "") -> str:
    """Honest stand-in when a commander write-up fails. Facts only — like why.py.

    The card was already found by Scryfall and labelled in code. Name, type line,
    and the selection label cannot be wrong about what happened. A second model
    call to invent prose about a card we already have is how empty pickers start.

    the picker no longer puts this string in `fits`. It stays for
    diagnostics and older call sites — rendering it as "Why this is your deck"
    made five same-theme commanders read identical.
    """
    name = (card.get("name") or "").strip() or "This commander"
    type_line = (card.get("type") or "").strip()
    parts = [f"{name} — {type_line}." if type_line else f"{name}."]
    if label:
        parts.append(f"Shown as {label}.")
    # Theme rung that filled the pool — same provenance as why.explain_group.
    q = _COMMANDER_QUERY_NOISE.sub(" ", query or "")
    q = re.sub(r"\s+", " ", q).strip()
    found = why_card.explain_group(q, "theme") if q else ""
    if found:
        parts.append(found)
    return " ".join(parts)


def enrich_commander_pick(
    card: dict,
    brief: dict,
    theme: dict,
    strat: dict,
    ways: list,
    *,
    model: dict | None = None,
    reason: str = "",
    query: str = "",
    vocab: dict | None = None,
) -> dict:
    """Assemble one picker row. differs from data; fits from write-up only.

    `fits` is model prose when a write-up landed. Ways, theme_fit, strategy_fit,
    and `differs` carry the comparison on every page — including offset > 0,
    which never calls the model. A missing write-up is
    `writeup_missing`, not a template that only swaps the name.
    """
    del query  # kept for call-site symmetry with plain_commander_reason
    m = model if isinstance(model, dict) else {}
    ways = list(ways or [])
    writeup_missing = not bool(m) and not bool(ways)

    def _lines(v, cap):
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in (v or []) if str(x).strip()][:cap]

    # Model fits only. relational_copy / plain_commander_reason are not write-ups.
    fits = (m.get("fits") or "").strip() if m else ""

    t_score = theme.get("score")
    s_score = strat.get("score")
    if (isinstance(t_score, int) and t_score > 0
            and isinstance(s_score, int) and s_score > 0):
        fit_axis = "theme+strategy"
    elif isinstance(t_score, int) and t_score > 0:
        fit_axis = "theme"
    elif isinstance(s_score, int) and s_score > 0:
        fit_axis = "strategy"
    elif strat.get("status") == "unknown" or theme.get("status") == "unknown":
        fit_axis = "unknown"
    else:
        fit_axis = "neither"

    matched = list(strat.get("matched") or [])
    want_way = brief.get("way_playstyle")
    way_ids = {w.get("playstyle") for w in ways if w.get("playstyle")}
    if want_way and want_way in way_ids:
        chosen = want_way
    else:
        chosen = matched[0] if matched else (
            ways[0]["playstyle"] if ways else None)

    _vocab = vocab if vocab is not None else playstyles.load_playstyles()
    if ways:
        lead = next((w for w in ways if w["playstyle"] == chosen), ways[0])
        does = lead.get("says") or lead.get("why") or ""
        strategy = ""
        mechanics = []
        for w in ways:
            pid = w.get("playstyle")
            det = ((_vocab.get(pid) or {}).get("detect") or {})
            mechanics.extend(det.get("requires") or [])
            mechanics.extend(det.get("requires_any") or [])
        seen_m, mechanics_u = set(), []
        for x in mechanics:
            if x not in seen_m:
                seen_m.add(x)
                mechanics_u.append(x)
        mechanics = mechanics_u[:6]
        pros, cons, catch = [], [], ""
    else:
        does = m.get("does") or ""
        strategy = m.get("strategy") or ""
        mechanics = _lines(m.get("mechanics"), 6)
        pros = _lines(m.get("pros"), 3)
        cons = _lines(m.get("cons"), 3)
        catch = m.get("catch") or ""

    partner = has_partner(card)
    differs = cmd_kb.picker_differs(card, theme, strat, ways)
    p = {
        "name": card["name"],
        "does": does,
        "strategy": strategy,
        "fits": fits,
        "writeup_missing": writeup_missing,
        "differs": differs,
        "theme_fit": {
            "status": theme.get("status"),
            "score": theme.get("score"),
            "matched": theme.get("matched") or [],
        },
        "strategy_fit": {
            "status": strat.get("status"),
            "score": strat.get("score"),
            "matched": strat.get("matched") or [],
        },
        "fit_axis": fit_axis,
        "ways_to_play": ways,
        "chosen_playstyle": chosen,
        "mechanics": mechanics,
        "pros": pros,
        "cons": cons,
        "catch": catch,
        "reason": reason,
        "partner": partner,
        "color_identity": list(card.get("color_identity") or []),
    }
    if partner:
        p["partner_notice"] = PARTNER_NOTICE
    if not ways:
        p["ways_note"] = "No ways detected — showing what we know without guessing."
    p.update({k: card.get(k) for k in (
        "cost", "type", "text", "usd", "image", "art",
        "keywords", "power", "toughness")})
    return p


@app.post("/api/readback")
async def readback(brief: dict):
    """intent rows + commander narrowing before the picker.

    Term rows still carry live counts for the app (thin / empty / unknown).
    Healthy pool magnitudes are builder diagnostics — the UI suppresses them.
    `narrowing` names a few real commanders that differ by way to play.
    """
    theme = brief_theme(brief)
    colors = brief.get("colors") or ""
    rb = await build_readback(theme, colors)
    narrowing = None
    if brief_is_usable(brief)[0]:
        try:
            narrowing = await build_narrowing_turn(brief)
        except Exception as e:
            print(f"  [narrowing] readback failed: {type(e).__name__}: {e}")
            narrowing = {
                "status": "unknown",
                "candidates": [],
                "commander_count": None,
                "note": ("I could not look up commanders just now — not the "
                         "same as finding none."),
            }
    rb["narrowing"] = narrowing
    # Display contract: healthy pool sizes stay in the payload for the app
    # (thin gate, diagnostics) but display is None so the UI does not show them.
    for t in rb.get("terms") or []:
        # Term rows: empty / unknown only. Thin is a composed-pool fact.
        t["display"] = readback_count_display(t.get("count"), thin=False)
    comp = rb.get("composed")
    if isinstance(comp, dict):
        comp["display"] = readback_count_display(
            comp.get("count"), thin=bool(rb.get("thin")))
    return rb


@app.post("/api/commanders")
async def commanders(brief: dict):
    # a themeless brief is an unknown, not a generic commander pool.
    usable, reason = brief_is_usable(brief)
    if not usable:
        raise HTTPException(422, reason)
    apply_way_fields(brief)
    await load_vocab()

    # page within one rung. "More" must not mean "worse."
    try:
        offset = max(0, int(brief.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    ladder, generic = commander_ladder(brief)
    cards, used, rung_status, scry_total = await search_primary_rung(ladder)
    if rung_status == "unknown":
        page = page_commanders(None, offset=offset, rung_status="unknown")
        return {
            "picks": [],
            "note": page["note"],
            "query": used or "",
            "rung_total": None,
            "offset": offset,
            "has_more": None,
            "rung_exhausted": False,
            "shown_of": None,
            "status": "unknown",
        }

    cards = legal_commanders_only(cards)
    # soft-prefer commanders that already detect her mapped way.
    cards = prefer_way_playstyle(cards, brief.get("way_playstyle"))
    ranked = rank_commanders_pool(cards, brief)
    # If we hit the fetch cap short of Scryfall's total_cards, keep Scryfall's
    # figure — "5 of 85" was the bug when the rung actually had 479.
    if (isinstance(scry_total, int) and scry_total > len(cards)
            and len(cards) >= COMMANDER_FETCH_CAP):
        true_total = scry_total
    else:
        # After legality/prefer filters, report the pageable set size.
        true_total = len(ranked)
    page = page_commanders(ranked, offset=offset, n=COMMANDER_PICKS,
                           rung_status="ok", rung_total=true_total)
    page_cards = page["cards"]
    # If we fell past every theme-aware rung, the list is generic. Say so —
    # silently handing over unrelated commanders is the worst failure here.
    off_theme = bool(ranked) and used in generic

    labelled = label_commander_picks(page_cards, brief, n=len(page_cards))
    # Preserve page order (fit rank); attach reason tags from the labeler.
    reason_by = {c["name"]: r for c, r in labelled}
    labelled = [(c, reason_by.get(c["name"], "also on this rung"))
                for c in page_cards]
    candidates = [c for c, _ in labelled]

    # ways-to-play are derived (KB / detect), not model-generated.
    # Precompute them once so we can skip the fragile write-up when present.
    # Cap at three most synergistic — never invent a fourth to fill the card.
    # Strategy-matched ways float to the front so the pre-select stays visible.
    WAYS_ON_CARD = 3
    _vocab = playstyles.load_playstyles()
    fit_cache: dict[str, tuple[dict, dict, list]] = {}
    for card, _reason in labelled:
        theme = cmd_kb.theme_fit(card, brief)
        strat = cmd_kb.strategy_fit(card, brief)
        matched = set(strat.get("matched") or [])
        raw_ways = list(strat.get("ways") or [])
        raw_ways.sort(key=lambda w: (
            0 if w.get("playstyle") in matched else 1,
            0 if w.get("method") == "both" else
            1 if w.get("method") == "community" else 2,
            w.get("playstyle") or "",
        ))
        ways = []
        for w in raw_ways:
            pid = w.get("playstyle")
            if not pid or pid not in _vocab:
                continue  # never invent a row to fill space
            says = (_vocab.get(pid) or {}).get("says") or ""
            if not says:
                continue  # no player-language line → not offerable
            ways.append({
                "playstyle": pid,
                "says": says,
                "confidence": w.get("confidence"),
                "method": w.get("method"),
                "why": w.get("why"),
                "source": w.get("source"),
                "note": w.get("note"),
            })
            if len(ways) >= WAYS_ON_CARD:
                break
        fit_cache[card["name"]] = (theme, strat, ways)

    # One call per commander rather than one for all five. The combined format
    # blew past 1200, then 4000, then 8000 tokens — each time the JSON truncated
    # and every field emptied. Per-commander calls cannot truncate, run in
    # parallel, and one failure costs one write-up instead of all of them.
    #
    # when derived ways exist, skip the model write-up entirely —
    # burns both retries on unparseable JSON, and the ways replace it.
    # Keep the write-up only for commanders whose ways we cannot detect.
    #
    # later pages never call the model — paging must not multiply cost.
    async def write_up(card: dict) -> dict:
        prompt = f"""They want: {brief.get('angle')} (theme: {brief.get('theme')})
Budget around ${brief.get('budget_total')}, bracket {brief.get('bracket')}.

The commander:
{json.dumps(for_model(card), indent=1)}

Describe **the deck they would build around it** — not the card. They can read
the card. What they cannot do is picture the deck: what the other 99 become,
what a game actually looks like, and how it delivers the fantasy above.

Return only json:
{{"name": {json.dumps(card["name"])},
  "does": "one sentence: what this DECK does on a normal turn — the loop it
           repeats, not the commander's rules text",
  "strategy": "four or five sentences on the deck you would build around this
               commander. What kind of cards fill the other 99. What turns 1-3
               look like, what turn 6 looks like, and how it actually closes a
               game. Write it as the deck she would be playing, not as an
               assessment of the card — she is choosing what her games will
               feel like, and this is the only place she can see that before
               committing. Two commanders must never get interchangeable
               paragraphs; if yours would fit either, you have described the
               archetype rather than this deck.",
  "fits": "one or two sentences on how this deck delivers the fantasy and the
           playstyle she asked for. Quote or name the thing SHE said — 'you
           wanted the whole team swinging every turn' — so she can see it was
           heard. If it satisfies most of her ask but not all of it, say which
           part it leaves behind. A candidate that quietly drops half of what
           she wanted is the one she most needs warning about.",
  "mechanics": ["3-6 SHORT searchable phrases naming the card effects this deck
                 needs, in Magic's own words: \"sacrifice outlet\", \"treasure\",
                 \"gain control of target creature\", \"landfall\". These are used
                 to search for real cards — name effects, never card names, never
                 vague ideas like \"good stuff\"."],
  "pros": ["3 short phrases about playing THE DECK"],
  "cons": ["2-3 short phrases about playing THE DECK — how it loses, what it
            struggles against, what gets frustrating. Never soften these."],
  "catch": "the single thing most likely to make them regret building this deck"}}

Write for someone who has never built a deck. Explain jargon the first time.
The test for every line: would this help them picture playing it on a Friday
night? "It's a 2/1 that dies to removal" describes a card. "The deck runs out of
gas after a board wipe because everything is small" describes the deck."""
        for attempt in (1, 2):
            try:
                r = await client.messages.create(
                    model=MODEL, max_tokens=3000, thinking={"type": "disabled"},
                    messages=[{"role": "user", "content": prompt}])
                got = parse_json_list(
                    r, f"write-up: {card['name']}", expect="object")
                if isinstance(got, dict) and got.get("name"):
                    return got
                print(f"  [write-up: {card['name']}] empty/unparseable "
                      f"(attempt {attempt}/2)")
            except Exception as e:
                print(f"  [write-up: {card['name']}] {type(e).__name__}: {e} "
                      f"(attempt {attempt}/2)")
        return {}

    need_writeup = (
        [] if offset > 0
        else [c for c, _ in labelled if not fit_cache[c["name"]][2]]
    )
    written = await asyncio.gather(*[write_up(c) for c in need_writeup]) if need_writeup else []
    model_picks = [w for w in written if w.get("name")]

    # verifier: only enrich does/catch from names that were in the Scryfall list
    valid = {c["name"] for c in candidates}
    model_by_name = {p["name"]: p for p in model_picks
                     if isinstance(p, dict) and p.get("name") in valid}

    picks = []
    writeups_missing = 0
    for card, reason in labelled:
        theme, strat, ways = fit_cache[card["name"]]
        m = model_by_name.get(card["name"]) or None
        p = enrich_commander_pick(
            card, brief, theme, strat, ways,
            model=m, reason=reason, query=used, vocab=_vocab,
        )
        if p.get("writeup_missing"):
            writeups_missing += 1
        picks.append(p)

    # soft-prefer final ordering without dropping seats.
    if brief.get("way_playstyle") and picks:
        want = brief["way_playstyle"]
        hit = [p for p in picks
               if want in {w.get("playstyle") for w in (p.get("ways_to_play") or [])}]
        rest = [p for p in picks if p not in hit]
        picks = hit + rest

    note = ""
    way_pref = (brief.get("way_preference") or "").strip()
    if way_pref and not brief.get("way_playstyle"):
        unmapped = (
            f"I heard “{way_pref}” but could not map it to a known way — "
            f"these commanders are not steered by that preference."
        )
        note = unmapped
    if off_theme:
        theme_shown = brief_theme(brief) or "that theme"
        off = (f"Scryfall had nothing for “{theme_shown}” as a card type or "
               f"rules-text term, so these are general commanders, not on-theme ones. "
               f"Try naming a creature type (pirate, zombie, dragon) or a mechanic "
               f"that's printed on cards (treasure, sacrifice, graveyard).")
        note = f"{note} {off}".strip() if note else off
    # no colours on the brief → the ladder was unscoped. Say so —
    # silently mixing mono-red into a blue-black ask is how Ragavan got offered.
    if picks and not brief_color_identity(brief):
        unscoped = ("No colours were named, so these commanders span colour "
                    "identities. Pick one and the deck will follow its colours.")
        note = f"{note} {unscoped}".strip() if note else unscoped
    # Search found cards; every write-up failed. Say so — empty strategy/does is
    # not the same as an empty picker, and she should know why the prose is gone.
    if picks and writeups_missing == len(picks):
        missing = ("The write-ups are missing this time — the commanders below "
                   "are still real. Pick from the name, type line, and why it "
                   "was offered.")
        note = f"{note} {missing}".strip() if note else missing
    if page.get("note"):
        note = f"{note} {page['note']}".strip() if note else page["note"]
    return {
        "picks": picks,
        "note": note,
        "query": used,
        "rung_total": page["rung_total"],
        "offset": page["offset"],
        "has_more": page["has_more"],
        "rung_exhausted": page["rung_exhausted"],
        "shown_of": page["shown_of"],
        "status": page["status"],
    }


# ---------------------------------------------------------------- build the 99

def is_land_card(card: dict) -> bool:
    """True when the card's type line is a land (type line, not effect)."""
    return "land" in (card.get("type") or "").lower()


def prefer_pool(
    cards: list[dict],
    commander: dict | None,
    scaffold: list | None,
    strategy: dict | None = None,
) -> list[dict]:
    """Synergy glue first, then Gate 4 strategy steer when candidate B or C."""
    ordered = synergy.prefer_glued(cards, commander, scaffold or [])
    return strategy_steer.prefer_strategy(ordered, strategy, scaffold or [])


def backfill_chosen(
    chosen: list,
    pool: list,
    target: int,
    commander: dict | None = None,
    scaffold: list | None = None,
    strategy: dict | None = None,
) -> list:
    """Fill short model picks from pool order, preferring glued cards.

    Model-valid names stay first. Remaining slots walk pool candidates sorted by
    synergy.prefer_glued (edge_steer_vote / edge_count, then EDHREC pool order).
    Fail-open when commander is unread — prefer_glued returns pool order.

    never emit the same non-unique name twice. prefer_glued reorders a
    pool that can carry duplicates (overlapping payoff searches); walking that
    list without a membership check re-picks a card already taken.

    when candidate B or C and the commander reads, strategy steer
    deprioritises bound violations and prefers feeders — never drops cards.
    candidate C may enrich feeders via CardFacts (union with oracle).
    """
    out: list = []
    taken: set[str] = set()
    for c in chosen:
        name = c.get("name")
        if not name or name in taken:
            continue
        out.append(c)
        taken.add(name)
        if len(out) >= target:
            return out
    remaining = [c for c in pool if c.get("name") and c["name"] not in taken]
    remaining = prefer_pool(remaining, commander, scaffold, strategy)
    for c in remaining:
        if len(out) >= target:
            break
        name = c.get("name")
        if not name or name in taken:
            continue
        out.append(c)
        taken.add(name)
    return out


def place_role_cards(role: str, deck: list, chosen: list, pool: list,
                     target: int, seen: set,
                     commander: dict | None = None,
                     scaffold: list | None = None,
                     strategy: dict | None = None) -> None:
    """Add picks for a role. Any land-type card counts against the land slot.

    type line, not effect. Cultivate (sorcery that fetches a land) stays
    in its role. Command Tower / Fountainport / Promising Vein are lands wherever
    the query found them — they displace a lands-slot card so the skeleton's land
    count holds. The role is then backfilled with non-land cards from the pool.

    dedupe by name at the point a card enters the 99. A duplicate in
    chosen or pool must not consume a second slot — backfill a different card
    instead. Basics are filled later and are allowed to repeat.
    """
    if role == "lands":
        placed = 0
        for c in chosen:
            if placed >= target:
                break
            name = c.get("name")
            if not name or name in seen:
                continue
            deck.append({**c, "role": "lands"})
            seen.add(name)
            placed += 1
        return

    slot = []
    have: set[str] = set()
    for c in chosen:
        name = c.get("name")
        if not name:
            continue
        if is_land_card(c):
            if name in seen:
                continue
            lands = [x for x in deck if x.get("role") == "lands"]
            if lands:
                victim = lands[-1]
                deck.remove(victim)
                seen.discard(victim["name"])
            deck.append({**c, "role": "lands"})
            seen.add(name)
        else:
            if name in seen or name in have:
                continue
            slot.append(c)
            have.add(name)
    remaining = [
        c for c in pool
        if c.get("name")
        and c["name"] not in seen
        and c["name"] not in have
        and not is_land_card(c)
    ]
    for c in prefer_pool(remaining, commander, scaffold or deck, strategy):
        if len(slot) >= target:
            break
        name = c.get("name")
        if not name or name in seen or name in have:
            continue
        slot.append(c)
        have.add(name)
    for c in slot[:target]:
        deck.append({**c, "role": role})
        seen.add(c["name"])


def place_ramp_cards(deck: list, chosen: list, pool: list, target: int, seen: set) -> None:
    """Backward-compatible alias for place_role_cards('ramp', ...)."""
    place_role_cards("ramp", deck, chosen, pool, target, seen)


# Ramp/draw/interaction counts are conventional EDH ratios, not a measured optimum.
# `plan` is Deckwright-specific: strategy-named mechanics get their own
# slot. Land count is curve-derived at build time; the lands role target
# is filled from mana.plan() after spells are placed.
# card-type targets are floors *inside* theme/plan/deepcut (see
# ROLE_TYPE_FRACTIONS), not a parallel creature/spell budget beside these counts.
ROLES = [
    ("lands",        0, "Nonbasic lands and utility lands"),
    ("ramp",        10, "Mana rocks and land ramp"),
    ("draw",         9, "Card advantage"),
    ("spot",         7, "Targeted removal"),
    ("wipe",         3, "Board wipes"),
    ("plan",        10, "Cards the chosen strategy specifically calls for"),
    ("theme",       15, "On-theme synergy pieces"),
    ("deepcut",      5, "Flavor cards almost nobody plays"),
]

# total lands is a decision, not "whatever's left to 99." sizes it
# from the curve; basics make up total − nonbasics (floored at MIN_BASICS).
MIN_BASICS = 2
DEFAULT_TOTAL_LANDS = 38  # finish_mana_base fallback when no plan is passed
FLEX_ROLES = ("theme", "deepcut")


# playstyle moves the skeleton, which is what the card asked for —
# "visible in the skeleton, not just the flavor". Two people asking for goblins
# get different decks if one wants to attack and the other wants to answer.
#
# These are deliberately small. The skeleton is 99 slots and every one moved has
# to come from somewhere; a swing of three or four changes how a deck feels
# without turning a goblin deck into a control deck she never asked for. They
# also stay inside the roles that already exist rather than inventing new ones.
PLAYSTYLE_SHIFT = {
    # Doing things: more of the plan and its support, fewer answers.
    "proactive": {"spot": -2, "wipe": -1, "plan": +2, "theme": +1},
    # Stopping things: answers, and the cards to find them.
    "reactive":  {"spot": +3, "wipe": +1, "draw": +1, "theme": -3, "deepcut": -2},
    # The long game: card advantage and resets, at the theme's expense.
    "grindy":    {"draw": +3, "wipe": +2, "spot": +1, "theme": -4, "deepcut": -2},
}


# A deck that never had a conversation still has a shape. Precons and pasted
# lists carry no `playstyle` — it comes from the interrogation — but their own
# analysis describes how they play, and these are the words it uses. This gap
# was found while shaping the skeleton by play preference; the phrase lists are narrowed to the one
# axis this codebase already tests.
_PLAYSTYLE_HINTS = {
    # "counter" alone is a trap: +1/+1 counters are everywhere and mean the
    # opposite thing. Every phrase here has to be unambiguous on its own.
    "reactive": ("counterspell", "counters spells", "counter target", "control deck",
                 "removal-heavy", "control the table", "answer threat", "answers threat",
                 "stop them", "interaction", "stax", "defensive"),
    "grindy":   ("attrition", "grind", "value engine", "recursion", "long game",
                 "card advantage", "outlast", "inevitability"),
    "proactive": ("aggro", "go wide", "attack", "beatdown", "race", "all gas",
                  "swarm", "haste", "combat damage", "goldfish"),
}


def infer_playstyle(brief: dict) -> str | None:
    """Read a playstyle off a deck that was never asked. Explicit always wins.

    Deliberately not a query. This picks between three slot-count
    profiles, and a slot count cannot be mistaken for a Scryfall search term.
    """
    explicit = str((brief or {}).get("playstyle") or "").strip().lower()
    if explicit in PLAYSTYLE_SHIFT:
        return explicit
    text = " ".join(str(x or "") for x in (
        (brief or {}).get("strategy"), (brief or {}).get("angle"),
        (brief or {}).get("fantasy"), " ".join((brief or {}).get("mechanics") or []),
    )).lower()
    if not text.strip():
        return None
    # Most hits wins; a tie means the text is not telling us, so say nothing
    # rather than guess a shape and quietly reshape someone's deck.
    scores = {k: sum(1 for h in hints if h in text) for k, hints in _PLAYSTYLE_HINTS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def _spell_role_sum(roles: list[tuple]) -> int:
    """Non-land slot total — lands are sized separately."""
    return sum(c for n, c, _ in roles if n != "lands")


def skeleton_for(brief: dict, commander: dict | None = None) -> list[tuple]:
    """Role targets for the 99.

    playstyle shifts the skeleton. when `curve.SKELETON_LIVE`,
    commander CMC + curve preference reshape counts first; playstyle then
    shifts on top. Both shifts are designed sum-zero within spell roles.
    A brief with no playstyle and no readable curve preference keeps the
    conventional ROLES (mid + CMC densify when the commander is 4+).
    """
    from deckwright import curve as _curve

    if _curve.SKELETON_LIVE:
        pref = _curve.preference(brief)
        # UNKNOWN → mid inside curve.skeleton_for; never invent low/high.
        report = _curve.skeleton_for(commander, {
            "brief": brief,
            "curve": pref,
        })
        base = list(report["roles"])
    else:
        base = list(ROLES)

    shift = PLAYSTYLE_SHIFT.get(infer_playstyle(brief) or "")
    if not shift:
        return base
    out = []
    for name, count, label in base:
        delta = shift.get(name, 0)
        # Never below zero, and never let a role vanish entirely — a deck with
        # no removal at all is a worse answer than a proactive deck with two.
        out.append((name, max(2, count + delta) if count else count, label))
    return out


def surplus_from_pools(role_pools: dict | None, deck_names: set[str]) -> list[dict]:
    """Unused non-land cards still sitting in role_pools, EDHREC-ranked.

    when theme/plan come back short, borrow these before basics.
    Lands (by role or type line) stay out — they are not spell fillers.
    """
    seen = set(deck_names)
    out: list[dict] = []
    for role, pool in (role_pools or {}).items():
        if role == "lands":
            continue
        for c in pool or []:
            name = c.get("name")
            if not name or name in seen or is_land_card(c):
                continue
            out.append(c)
            seen.add(name)
    out.sort(key=_edhrec_key)
    return out


def finish_mana_base(deck: list, flex_pool: list, basic_cards: list,
                     total_lands: int = DEFAULT_TOTAL_LANDS,
                     min_basics: int = MIN_BASICS,
                     deck_size: int = 99,
                     surplus_pool: list | None = None) -> dict:
    """Add basics to hit total_lands, then flex/surplus non-lands toward deck_size.

    Never pads with basics past total_lands. If flex and surplus
    cannot fill the rest, the deck stays short and shortfall is reported.
    Mutates deck in place. basic_cards is a non-empty list of trimmed basic
    land dicts to cycle through (Island/Swamp/…).
    """
    if not basic_cards:
        raise ValueError("finish_mana_base needs at least one basic land card")

    nonbasic = sum(1 for c in deck if is_land_card(c) and c.get("role") != "basic")
    basics_n = max(min_basics, total_lands - nonbasic)
    # Never exceed deck_size; prefer the land target over flex if short on room.
    room = max(0, deck_size - len(deck))
    basics_n = min(basics_n, room)

    seen = {c["name"] for c in deck}
    for i in range(basics_n):
        src = basic_cards[i % len(basic_cards)]
        card = dict(src)
        card["role"] = "basic"
        deck.append(card)

    flex_added = 0
    for c in flex_pool:
        if len(deck) >= deck_size:
            break
        if c["name"] in seen or is_land_card(c):
            continue
        deck.append({**c, "role": c.get("role") or "theme"})
        seen.add(c["name"])
        flex_added += 1

    # preference 1: unused cards from roles that still have pool left.
    surplus_added = 0
    for c in surplus_pool or []:
        if len(deck) >= deck_size:
            break
        if c["name"] in seen or is_land_card(c):
            continue
        deck.append({**c, "role": c.get("role") or "theme"})
        seen.add(c["name"])
        surplus_added += 1

    # do not pad with basics past the land target. An incomplete deck
    # she can see is better than a legal-looking list that is two-thirds lands.
    shortfall = max(0, deck_size - len(deck))
    total = sum(1 for c in deck if is_land_card(c))
    return {
        "padded_basics": 0,
        "basics_added": basics_n,
        "flex_added": flex_added,
        "surplus_added": surplus_added,
        "shortfall": shortfall,
        "total_lands": total,
        "nonbasic_lands": nonbasic,
        "target": total_lands,
        "basis": (f"{total} lands ({nonbasic} nonbasic + {basics_n} basic) — "
                  f"target {total_lands}, theme/deepcut absorb the rest"),
    }


async def on_color_staples(ci: str, brief: dict, budget_each: float,
                           need: int, seen: set[str]) -> list[dict]:
    """On-colour non-land staples by EDHREC rank (preference 2)."""
    if need <= 0 or not ci:
        return []
    price = budget_price_ceiling(budget_each)
    gc = " -is:gamechanger" if brief.get("bracket", 2) <= 2 else ""
    fmt = pool_format(parse_card_pool(brief))
    q = f"{fmt} {gc} id<={ci} -t:land{price}".strip()
    q = re.sub(r"\s+", " ", q)
    cards = await search(q, limit=max(need + 30, 45))
    out = []
    for c in cards:
        if c["name"] in seen or is_land_card(c):
            continue
        out.append(c)
        seen.add(c["name"])
        if len(out) >= need:
            break
    return out


def with_pool_set(rungs: list[str], brief: dict) -> list[str]:
    """Prefer a named set, then fall back to the ordinary ladder.

    She asked for "only Spider-Man cards" and the honest answer was 70 of 99 —
    the set cannot fill a deck on its own. So each rung is tried inside the set
    first, and the unconstrained ladder sits underneath to fill what is left.
    The existing degrade-and-top-up behaviour does the rest, which is why this
    is six lines rather than a second builder.

    No set named, nothing changes.
    """
    code = str((brief or {}).get("pool_set") or "").strip().lower()
    if not code or not rungs:
        return rungs
    return [f"{q} set:{code}" for q in rungs] + list(rungs)


def budget_price_ceiling(budget_each: float | None) -> str:
    """Per-card Scryfall price ceiling for pool queries. Empty when unset."""
    if not budget_each:
        return ""
    return f" usd<{budget_each:.2f}"


def role_query(role: str, ci: str, brief: dict, budget_each: float) -> list[str]:
    """Query ladder for a role, with a named card pool applied if she asked for one.

    Wraps the real builder so every one of its return paths gets the same
    treatment — there are eight, and patching each would eventually miss one.
    """
    return with_pool_set(_role_query_base(role, ci, brief, budget_each), brief)


def _role_query_base(role: str, ci: str, brief: dict, budget_each: float) -> list[str]:
    """Query ladder for a deck role, best first. Falls back on empty results.

    Theme roles interpolate the user's theme. Utility roles (ramp, draw, spot,
    wipe) use a generic ladder when no commander strategy is present; with a
    strategy they read curve and playstyle out of the prose.

    A brief carrying `pool_set` gets each rung tried inside that set first —
    see with_pool_set().
    """
    price = budget_price_ceiling(budget_each)
    # Bracket ≤2: exclude Game Changers at query time (53 cards). Do not rely on
    # post-filtering a parsed field — that already failed once.
    gc = " -is:gamechanger" if brief.get("bracket", 2) <= 2 else ""
    fmt = pool_format(parse_card_pool(brief))
    base = f"{fmt} {gc} id<={ci}".strip() if ci else f"{fmt} {gc}".strip()
    base = re.sub(r"\s+", " ", base).strip()
    # coerce at the boundary — None is absent, never the string "None".
    theme = brief_theme(brief)
    variants = singulars(theme)
    flavor_terms = brief.get("flavor_terms") or []
    if not isinstance(flavor_terms, list):
        flavor_terms = []
    flavor = " OR ".join(
        f"ft:{w}" for w in flavor_terms[:4] if isinstance(w, str) and w.strip())

    if role == "plan":
        # the strategy names mechanics; each becomes a real query. The
        # model proposes the phrase, Scryfall decides whether it exists — same
        # discipline as every other search here.
        mech = brief.get("mechanics") or []
        type_bias = type_bias_clause(brief)
        ladder = []
        for m in mech:
            if not isinstance(m, str) or not m.strip():
                continue
            for clause in theme_clauses(m):
                # type floor as an early rung inside the plan slot.
                if type_bias:
                    ladder.append(f"{base} {clause} {type_bias}{price}")
                ladder.append(f"{base} {clause}{price}")
            if " " in m:
                if type_bias:
                    ladder.append(f"{base} o:{scryterm(m)} {type_bias}{price}")
                ladder.append(f"{base} o:{scryterm(m)}{price}")
        # No strategy, or none of it resolved: fall back to on-theme cards so the
        # slot is never filled with noise.
        return _dedupe_queries(ladder) or role_query("theme", ci, brief, budget_each)

    if role == "theme":
        if resolve._grammar is None:
            resolve.load_grammar()
        hit = resolve.resolve_theme(theme)
        clauses = theme_clauses(theme)
        type_bias = type_bias_clause(brief)
        ladder = []
        # prefer the type the strategy wants, then the untyped theme.
        if type_bias:
            ladder += [f"{base} {c} {type_bias}{price}" for c in clauses]
        ladder += [f"{base} {c}{price}" for c in clauses]
        # Widen with singular o: only for a single non-set / non-otag placement.
        # Composed themes and behaviours already have their real operators;
        # inventing o:steal next to otag:theft is the keyword:steal trap's cousin.
        parts = hit.get("parts") or []
        if (len(parts) == 1 and parts[0].get("operator") in ("t", "keyword")
                and parts[0].get("operator") != "set"):
            ladder += [f"{base} o:{scryterm(t)}{price}"
                       for t in variants
                       if " " not in t and t.lower() not in _FUNCTION_TERMS]
        # Empty ladder = unresolved. Do not substitute otag:draw and pretend
        # the theme matched.
        return _dedupe_queries(ladder)

    if role == "deepcut":
        type_bias = type_bias_clause(brief)
        ladder = []
        if flavor:
            if type_bias:
                ladder.append(f"{base} ({flavor}) {type_bias} -is:reprint{price}")
            ladder.append(f"{base} ({flavor}) -is:reprint{price}")
        for t in variants:
            if type_bias:
                ladder.append(
                    f"{base} (ft:{scryterm(t)} OR art:{scryterm(t)}) "
                    f"{type_bias} -is:reprint{price}")
            ladder.append(
                f"{base} (ft:{scryterm(t)} OR art:{scryterm(t)}) -is:reprint{price}")
        ladder += [f"{base} (ft:{scryterm(t)} OR art:{scryterm(t)}){price}" for t in variants]
        # Last resort: on-theme cards that simply aren't widely played.
        ladder += [f"{base} {c} -is:reprint{price}"
                   for c in theme_clauses(theme)]
        return _dedupe_queries(ladder) or [f"{base} -is:reprint{price}"]

    generic = {
        "lands":   f"{base} t:land -t:basic{price}",
        "ramp":    f"{base} (otag:ramp OR o:'search your library for a') -t:land cmc<=3{price}",
        "draw":    f"{base} otag:draw{price}",
        "spot":    f"{base} otag:removal -t:land cmc<=4{price}",
        "wipe":    f"{base} o:'destroy all'{price}",
    }[role]
    if not brief.get("strategy"):
        return [generic]

    prof = strategy_profile(brief)
    mech = brief.get("mechanics") or []

    if role == "ramp":
        ladder = _mechanic_ladder(base, mech, price,
                                  ("treasure", "mana", "ramp", "land"))
        if prof["curve"] == "low":
            ladder.append(
                f"{base} (otag:ramp OR o:\"add {{\" OR o:\"add one mana\") "
                f"-t:land cmc<=2{price}")
        if prof["curve"] in ("low", "mid"):
            ladder.append(
                f"{base} (otag:ramp OR o:\"search your library for a\") "
                f"-t:land cmc<=3{price}")
        if prof["curve"] in ("mid", "high"):
            cap = 4 if prof["curve"] == "mid" else 5
            ladder.append(
                f"{base} (otag:ramp OR o:\"search your library for a land\" "
                f"OR o:\"put a land\") -t:land cmc<={cap}{price}")
        return _dedupe_queries(ladder) or [generic]

    if role == "draw":
        ladder = _mechanic_ladder(base, mech, price,
                                  ("draw", "discard", "loot", "impulse", "wheel"))
        if prof["draw"] == "refill":
            ladder += [
                f"{base} (otag:draw OR o:\"refill your hand\" "
                f"OR o:\"draw cards equal\") cmc<=4{price}",
                f"{base} (otag:draw OR o:\"whenever you attack\" o:draw) cmc<=3{price}",
            ]
        elif prof["draw"] == "accumulate":
            ladder += [
                f"{base} (otag:draw OR o:\"draw two\" OR o:\"draw three\") cmc>=2{price}",
                f"{base} otag:draw cmc>=3{price}",
            ]
        ladder.append(generic)
        return _dedupe_queries(ladder)

    if role == "spot":
        removal_style = prof["removal"]
        if removal_style == "standard" and prof["curve"] == "low":
            removal_style = "surgical"
        if removal_style == "surgical":
            ladder = [
                f"{base} (otag:removal OR o:fight OR o:\"destroy target creature\") "
                f"-t:land -o:\"destroy all\" -o:\"each creature\" cmc<=4{price}",
            ]
        elif prof["removal"] == "mass":
            ladder = [
                f"{base} (otag:removal OR o:\"destroy target\") -t:land cmc<=5{price}",
            ]
        else:
            ladder = []
        ladder.append(generic)
        return _dedupe_queries(ladder)

    if role == "wipe":
        removal_style = prof["removal"]
        if removal_style == "standard" and prof["curve"] == "low":
            removal_style = "surgical"
        if removal_style == "surgical":
            return _dedupe_queries([
                f"{base} o:\"destroy all creatures your opponents control\"{price}",
                generic,
            ])
        if removal_style == "mass":
            return _dedupe_queries([
                f"{base} (o:\"destroy all creatures\" OR o:\"destroy all permanents\"){price}",
                generic,
            ])
        return [generic]

    return [generic]


async def fetch_partner_options(card: dict) -> dict:
    """Legal seconds for a partner-capable commander, from Scryfall.

    Never invents names — every return is a real card. Filter is
    partners_compatible(), so Partner / Partner with / Friends forever
    cannot cross-contaminate.

    Returns status ok | empty | unknown so callers can distinguish a real
    zero from a lookup we could not complete.
    """
    kind = partner_kind(card)
    if not kind:
        return {"status": "empty", "partners": [], "note": None}
    if kind == "partner_with":
        named = partner_with_name(card)
        if not named:
            return {"status": "empty", "partners": [], "note": None}
        try:
            info = await scry("/cards/named", exact=named)
        except Exception:
            try:
                info = await scry("/cards/named", fuzzy=named)
            except Exception:
                return {
                    "status": "unknown",
                    "partners": [],
                    "note": "Could not look up the named partner — not inventing any.",
                }
        other = trim_card(info)
        if partners_compatible(card, other) and _is_legal_commander(other):
            return {"status": "ok", "partners": [other], "note": None}
        return {"status": "empty", "partners": [], "note": None}
    if kind == "friends_forever":
        q = f'{IS_COMMANDER} o:"Friends forever" f:commander'
    else:
        # Bare Partner only — exclude named pairs and Friends forever.
        q = (f'{IS_COMMANDER} o:Partner -o:"Partner with" '
             f'-o:"Friends forever" f:commander')
    try:
        cards, _ = await search_ladder([q], limit=80, floor=1)
    except Exception:
        return {
            "status": "unknown",
            "partners": [],
            "note": "Could not look up partners — not inventing any.",
        }
    cards = legal_commanders_only(cards)
    options = [c for c in cards
               if c.get("name") != card.get("name") and partners_compatible(card, c)]
    if options:
        return {"status": "ok", "partners": options, "note": None}
    return {"status": "empty", "partners": [], "note": None}


@app.post("/api/partners")
async def partners_endpoint(payload: dict):
    """legal partner seconds for a chosen first commander."""
    await load_vocab()
    name = (payload.get("commander") or "").strip()
    if not name:
        raise HTTPException(400, "commander required")
    info = await scry("/cards/named", exact=name)
    card = trim_card(info)
    kind = partner_kind(card)
    if not kind:
        return {"commander": name, "kind": None, "partners": [],
                "note": f"{name} cannot pair with a second commander."}
    fetched = await fetch_partner_options(card)
    options = fetched.get("partners") or []
    status = fetched.get("status") or "empty"
    # Sort by EDHREC so the picker is not a random scrape order.
    options.sort(key=_edhrec_key)
    picks = []
    for c in options:
        picks.append({
            "name": c["name"],
            "color_identity": list(c.get("color_identity") or []),
            "union_color_identity": union_color_identity(card, c),
            **{k: c.get(k) for k in (
                "cost", "type", "text", "usd", "image", "art",
                "keywords", "power", "toughness", "edhrec_rank")},
        })
    note = fetched.get("note") or ""
    if not note:
        if status == "unknown":
            note = "Could not look up partners — not inventing any."
        elif kind == "partner_with" and not picks:
            note = (f"{name} names a partner, but Scryfall did not return that card.")
        elif not picks:
            note = f"No legal partners found for {name}."
    return {
        "commander": name,
        "commander_card": card,
        "kind": kind,
        "partners": picks,
        "note": note,
    }


async def pair_strategy_writeup(brief: dict, a: dict, b: dict,
                                pick: dict) -> dict:
    """One write-up for what the two commanders do *together*."""
    prompt = f"""They want: {brief.get('angle')} (theme: {brief.get('theme')})
Budget around ${brief.get('budget_total')}, bracket {brief.get('bracket')}.

These two commanders share the command zone (Partner / Friends forever):
{json.dumps(for_model(a), indent=1)}
{json.dumps(for_model(b), indent=1)}

Colour identity of the deck is the union: {identity_string(union_color_identity(a, b))}.

First commander's solo write-up (may ignore — rewrite for the pair):
strategy: {pick.get('strategy') or ''}
does: {pick.get('does') or ''}

Describe **the deck built around BOTH** — what they do together. If they pull
in unrelated directions, say so honestly: "not much synergy" is a valid answer.

Return only json:
{{"name": {json.dumps(a['name'] + ' / ' + b['name'])},
  "does": "one sentence: what this DECK does on a normal turn with both in the zone",
  "strategy": "four or five sentences on the paired deck. What the other 98 become.
               What turns look like. How the two commanders work together — or don't.",
  "fits": "one or two sentences on how the PAIR delivers the fantasy she asked for.",
  "mechanics": ["3-6 SHORT searchable phrases for card effects this deck needs"]}}"""
    try:
        r = await client.messages.create(
            model=MODEL, max_tokens=3000, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}])
        got = parse_json_list(r, f"pair-write-up: {a['name']}/{b['name']}",
                              expect="object")
        if isinstance(got, dict) and got.get("strategy"):
            return got
    except Exception as e:
        print(f"  [pair-write-up] {type(e).__name__}: {e}")
    # Fallback: stitch the solo write-up rather than invent synergy.
    return {
        "does": pick.get("does") or "",
        "strategy": (pick.get("strategy") or "") + (
            f" Paired with {b['name']} — colour identity becomes "
            f"{identity_string(union_color_identity(a, b))}."
            if pick.get("strategy") else
            f"{a['name']} and {b['name']} share the command zone; "
            f"identity is {identity_string(union_color_identity(a, b))}."
        ),
        "fits": pick.get("fits") or "",
        "mechanics": pick.get("mechanics") or [],
    }


async def _resolve_edhrec_seed_cards(
    names: list[str],
    *,
    ci: str,
    card_pool: str,
    budget_each: float,
    bracket: int,
) -> list[dict]:
    """Resolve EDHREC average-deck names through Scryfall — verifier path."""
    out: list[dict] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        try:
            info = await scry("/cards/named", exact=name)
            card = trim_card(info)
        except Exception:
            continue
        if not edhrec_seed.accepts_seed_card(
            card,
            ci=ci,
            card_pool=card_pool,
            max_usd=budget_each,
            bracket=bracket,
        ):
            continue
        seen.add(name)
        out.append(card)
    return out


@app.post("/api/build")
async def build(payload: dict):
    brief, cmdr = payload.get("brief") or {}, payload.get("commander")
    # refuse before any Scryfall work. An empty brief used to ship a
    # confident off-theme 99; that is worse than failing.
    usable, reason = brief_is_usable(brief)
    if not usable:
        raise HTTPException(422, reason)
    if not cmdr:
        raise HTTPException(400, "commander required")
    partner_name = (payload.get("partner") or "").strip() or None
    # the strategy written at commander-pick time is the spec for this
    # build. Without it the tool describes one deck and constructs another.
    pick = payload.get("pick") or {}
    if pick.get("strategy"):
        brief = {**brief, "strategy": pick["strategy"],
                 "plan": pick.get("does") or brief.get("angle"),
                 "fits": pick.get("fits") or "",
                 "mechanics": pick.get("mechanics") or []}
    # measure live vs local Scryfall calls for this build.
    corpus.meters().reset()
    await load_vocab()
    card_pool = parse_card_pool({**brief, **payload})
    brief = {**brief, "card_pool": card_pool}

    info = await scry("/cards/named", exact=cmdr)
    cmdr_card = trim_card(info)
    partner_card = None
    if partner_name:
        pinfo = await scry("/cards/named", exact=partner_name)
        partner_card = trim_card(pinfo)
        if not partners_compatible(cmdr_card, partner_card):
            raise HTTPException(
                400, f"{cmdr} and {partner_name} cannot share the command zone")
        # Rewrite strategy for the pair — solo prose describes the wrong deck.
        pair = await pair_strategy_writeup(brief, cmdr_card, partner_card, pick)
        brief = {**brief,
                 "strategy": pair.get("strategy") or brief.get("strategy") or "",
                 "plan": pair.get("does") or brief.get("plan") or brief.get("angle"),
                 "fits": pair.get("fits") or brief.get("fits") or "",
                 "mechanics": pair.get("mechanics") or brief.get("mechanics") or []}

    zone = resolve_commanders(cmdr_card, partner_card)
    ci_list = union_color_identity(*zone)
    ci = identity_string(ci_list)
    # Persist the union so later swaps/coverage do not re-derive from one card.
    brief = {**brief, "colors": ci if ci != "C" else ""}
    deck_size = deck_size_for(len(zone))
    cmdr_for_mana = zone if len(zone) > 1 else cmdr_card
    cmdr_cmc = min(int(c.get("cmc") or 5) for c in zone)
    budget_each = max(1.0, float(brief.get("budget_total", 150)) / deck_size * 3)
    price = budget_price_ceiling(budget_each)
    # derive once for soft steer through the 99.
    cmdr_strategy = (
        commander_strategy.derive(cmdr_card)
        if strategy_steer.steer_enabled() else None
    )

    # EDHREC average deck seeds role pools (green + flag only).
    seed_bundle: edhrec_seed.SeedBundle | None = None
    seed_cards: list[dict] = []
    if edhrec_seed.enabled():
        seed_bundle = await edhrec_seed.load_for_commander(cmdr)
        if seed_bundle.usable:
            seed_cards = await _resolve_edhrec_seed_cards(
                seed_bundle.average_names,
                ci=ci,
                card_pool=card_pool,
                budget_each=budget_each,
                bracket=int(brief.get("bracket") or 2),
            )
            if seed_cards:
                print(f"  [edhrec-seed] {len(seed_cards)} cards resolved for {cmdr} "
                      f"(num_decks={seed_bundle.num_decks})")
            else:
                print(f"  [edhrec-seed] average deck parsed but none resolved for {cmdr}")
        else:
            print(f"  [edhrec-seed] {seed_bundle.status} for {cmdr} "
                  f"(cmd={seed_bundle.commander_fetch} avg={seed_bundle.average_fetch})")

    # live curve skeleton needs the commander (CMC densify). Preference
    # is read off the brief — plan_graph.derive later carries the same ear.
    skeleton = skeleton_for(brief, commander=cmdr_card)
    spell_roles = [r for r in skeleton if r[0] != "lands"]
    land_role = next(r for r in skeleton if r[0] == "lands")

    results = await asyncio.gather(*[
        search_ladder(role_query(r, ci, brief, budget_each), limit=45, floor=target)
        for r, target, _ in spell_roles
    ])

    deck, seen, notes = [], {c["name"] for c in zone}, []
    # Slots no role could fill. They become flex picks at the end, never basics.
    flex_shortfall = 0
    sim_scoring_ms = 0
    sim_skips: list[str] = []
    role_pools: dict[str, list[dict]] = {}
    # The query that returned a card is why that card is here — we knew
    # it and were discarding it. Kept so the list can say so without inventing.
    role_query_used: dict[str, str] = {}
    flex_pool = []  # leftover theme/deepcut candidates absorb card-count remainder

    # the role ladder searches for the THEME, which for a behaviour
    # theme is the enablers. o:"gain life" returns 211 cards that gain life and
    # never the 67 that pay it off, so a lifegain deck came back 26 enablers
    # and 1 payoff — the payoffs were never candidates, not ranked low.
    # The plan's commitments say how many payoff-class cards the 99 needs;
    # search for them and put them in front of the theme pool so the scorer
    # has something to choose between.
    #
    # derive once, keep it. The plan used to choose cards must travel
    # with the build — archetype, conditions, commitments — not disappear after
    # payoff_searches(). An unknown (derive failed) omits the key; never invent.
    #
    # archetype extras also carry a min. Those cards enter the pool
    # below, but card_score drops any the goldfish cannot see (Exquisite Blood
    # needs an opponent). Track the floors so theme selection can reserve slots
    # after ranking — the min is a floor, not a search hint.
    plan_for_build = None
    plan_derive_failed = False
    payoff_pool: list[dict] = []
    payoff_floors: list[dict] = []
    try:
        plan_for_build = plan_graph.derive(brief, cmdr_card, partner_card)
        for srch in plan_graph.payoff_searches(plan_for_build):
            # Wrap the query before id<= so OR arms stay inside the identity
            # filter. Bare `q OR r id<=WB` binds id only to r (Scryfall
            # precedence) and was letting Thassa's Oracle into a WB burst floor.
            q = (f'({srch["query"]}) id<={ci}' if ci
                 else f'({srch["query"]})')
            got, _ = await search_ladder([f"{q} -t:land{price}"], limit=60, floor=1)
            # overlapping payoff searches can return the same name.
            # A duplicated pool + prefer_glued reorders both copies into the
            # role window; placement must not see the name twice to begin with.
            have = {c["name"] for c in payoff_pool}
            payoff_pool.extend(c for c in got if c["name"] not in have)
            if str(srch.get("id") or "").startswith("archetype_"):
                payoff_floors.append({
                    "id": srch["id"],
                    "min": int(srch.get("min") or 0),
                    "names": {c["name"] for c in got},
                })
        # commander demand → one-hop relations → Scryfall recall into
        # the theme-facing payoff pool. Not into plan commitments (
        # dies_triggers→life_math invented lifegain for Hei Bai).
        # synergy_recall_queries calls through leave_enabler_queries so the
        # blink floor cannot drift off this path.
        leave_qs = set(synergy.leave_enabler_queries(cmdr_card))
        leave_cards: list[dict] = []
        enabler_cards: dict[str, list[dict]] = {}
        enabler_q_to_fam: dict[str, str] = {}
        for neigh in synergy.synergy_neighbors(cmdr_card):
            if not neigh.get("enabler_floor"):
                continue
            fam = neigh["family"]
            for rq in synergy.recall_queries_for_neighbor(neigh):
                enabler_q_to_fam[rq] = fam
        for bq in synergy.synergy_recall_queries(cmdr_card):
            q = f'({bq}) id<={ci}' if ci else bq
            got, _ = await search_ladder([f"{q} -is:extra -t:land{price}"],
                                         limit=40, floor=1)
            if not got:
                continue
            have = {c["name"] for c in payoff_pool}
            payoff_pool.extend(c for c in got if c["name"] not in have)
            if bq in leave_qs:
                leave_cards.extend(got)
            fam = enabler_q_to_fam.get(bq)
            if fam:
                enabler_cards.setdefault(fam, []).extend(got)
        if leave_cards:
            payoff_floors.append(leave_enabler_floor(leave_cards, minimum=1))
            print(f"  [leave-enablers] {len({c['name'] for c in leave_cards})} "
                  f"blink candidates for {cmdr_card.get('name')} "
                  f"(floor min=1)")
        for fam, cards in enabler_cards.items():
            payoff_floors.append(
                synergy_enabler_floor(fam, cards, minimum=1))
            print(f"  [synergy-enablers] {fam}: "
                  f"{len({c['name'] for c in cards})} candidates "
                  f"for {cmdr_card.get('name')} (floor min=1)")
    except Exception as e:
        # A missing payoff pool is not a reason to fail the build — the roles
        # still fill. But say so rather than silently shipping enablers.
        plan_derive_failed = True
        print(f"  [payoff-pool] {type(e).__name__}: {e}")
    if payoff_pool:
        print(f"  [payoff-pool] {len(payoff_pool)} payoff candidates for the plan")
    if payoff_floors:
        print(f"  [payoff-floors] {[(f['id'], f['min'], len(f['names'])) for f in payoff_floors]}")

    for (role, target, desc), (pool, used) in zip(spell_roles, results):
        if role == "theme" and payoff_pool:
            have = {c["name"] for c in pool}
            pool = [c for c in payoff_pool if c["name"] not in have] + list(pool)
        role_pools[role] = list(pool)
        role_query_used[role] = used or ""
        if len(pool) < target:
            notes.append(
                f"{role}: we wanted {target} cards here and found {len(pool)}.")
        pool = [c for c in pool if c["name"] not in seen]
        # Non-land roles never offer lands to the model. Assembly still re-homes
        # any that slip through: a land is a land, wherever found.
        if role != "lands":
            pool = [c for c in pool if not is_land_card(c)]
        if seed_cards and role != "lands":
            pool = edhrec_seed.merge_pool_front(pool, seed_cards)
            role_pools[role] = edhrec_seed.merge_pool_front(
                role_pools.get(role) or [], seed_cards)
            if seed_bundle and seed_bundle.meta:
                pool = edhrec_seed.prefer_seed_order(pool, seed_bundle.meta)
        if not pool:
            # A role that finds nothing used to just `continue`, and its slots
            # fell through to the basics fill at the end. On one build that
            # turned 25 empty theme and plan slots into 30 basic lands: a deck
            # that targeted 38 lands finished with 66.
            #
            # A theme Scryfall has no vocabulary for is a real outcome, but the
            # answer to it is more spells, not more Swamps. Widen to the role's
            # generic query — the one every deck can fall back on — and say
            # plainly that the theme could not be searched.
            notes.append(f"No results for {role} — nothing matched "
                         f"“{brief.get('theme') or brief.get('angle') or role}”.")
            generic = await search_ladder(
                role_query(role, ci, {"budget_total": brief.get("budget_total", 150)},
                           budget_each),
                limit=45, floor=target)
            pool = [c for c in generic[0] if c["name"] not in seen
                    and not is_land_card(c)]
            if not pool:
                notes.append(f"{role}: nothing matched at all, so these slots "
                             f"went to other parts of the deck.")
                flex_shortfall += target
                continue
            notes.append(f"{role}: nothing matched your theme, so we used "
                         f"well-regarded cards in your colours instead.")

        strategy_block = ""
        if brief.get("strategy"):
            strategy_block = (f"\nThe deck being built:\n{brief['strategy']}\n"
                              f"Choose cards that serve THIS plan specifically.\n")
        cmdr_label = " / ".join(c["name"] for c in zone)
        prompt = f"""Commander: {cmdr_label}. Plan: {brief.get('plan') or brief.get('angle')}
{strategy_block}
Fill the "{desc}" slot. Choose exactly {min(target, len(pool))} cards from this list.
Favor cards that support the plan over generically strong cards.

{json.dumps([for_model(c) for c in pool[:45]], indent=1)}

Return only a json array of exact names."""
        # 25-36 exact card names as JSON. Truncation here fails json.loads()
        # and the role silently falls back to backfill order — same class of bug
        # that emptied every commander write-up at 1200.
        r = await client.messages.create(model=MODEL, max_tokens=2500,
                                         thinking={"type": "disabled"},
                                         messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in r.content if b.type == "text")
        raw = raw.replace("```json", "").replace("```", "").strip()
        try:
            names = json.loads(raw)
        except json.JSONDecodeError:
            names = []

        valid = {c["name"]: c for c in pool}
        chosen = [valid[n] for n in names if n in valid][:target]
        # backfill if the model under-delivered or hallucinated
        chosen = backfill_chosen(
            chosen, pool, target, cmdr_card, deck, strategy=cmdr_strategy)
        # rank the model shortlist by marginal sim lift on this deck.
        shortlist = chosen[:max(target, card_score.SAMPLE_SIZE)]
        chosen, sim_meta, ms, score_info = await card_score.rank_for_role(
            deck, pool, role, target, shortlist, cmdr_cmc,
            cmdr_for_mana, ci, role_pools, spell_roles, seen, brief=brief,
            strategy=cmdr_strategy)
        sim_scoring_ms += ms
        if score_info.get("skipped"):
            sim_skips.append(f"{role}: {score_info['reason']}")
        # type floors inside the role, after ranking, before placement.
        floors = role_type_floors(role, target, brief)
        chosen = enforce_type_targets(role, chosen, pool, target, brief)
        for axis, need in floors.items():
            have = sum(1 for c in chosen if tweak_mod.card_axis(c) == axis)
            if have < need:
                notes.append(
                    f"{role}: type floor {axis} met {have}/{need} — "
                    f"the pool did not have enough matching cards.")
        # archetype payoff floors win over type mix — a zero-score
        # loop piece must still land. Applied after type floors so a creature
        # bias cannot swap Exquisite Blood back out.
        protected_payoffs: set[str] = set()
        if role == "theme" and payoff_floors:
            before = {c["name"] for c in chosen}
            chosen = enforce_payoff_floors(chosen, pool, payoff_floors, target)
            for floor in payoff_floors:
                need = int(floor.get("min") or 0)
                have = sum(1 for c in chosen if c["name"] in floor["names"])
                avail = sum(1 for c in pool if c["name"] in floor["names"]
                            and not is_land_card(c))
                want = min(need, avail)
                if have < want:
                    notes.append(
                        f"{role}: payoff floor {floor['id']} met {have}/{want} — "
                        f"the pool did not have enough matching cards.")
                protected_payoffs.update(
                    c["name"] for c in chosen if c["name"] in floor["names"])
            added = {c["name"] for c in chosen} - before
            if added:
                print(f"  [payoff-floors] reserved into theme: {sorted(added)}")
        # the named creature type must survive payoff reservation.
        # Behaviour payoffs are not tribe-matched; without a floor, angels+lifegain
        # ships a lifegain deck with 2 angels while vampires+lifegain keeps 18.
        if role == "theme":
            tribes = theme_creature_types(brief)
            tribe = tribes[0] if tribes else None
            tribe_need = tribe_floor_min(target)
            if tribes and tribe_need:
                before = {c["name"] for c in chosen}
                chosen = enforce_tribe_floor(
                    chosen, pool, tribe, tribe_need, target,
                    protected=protected_payoffs, tribes=tribes)
                have = sum(1 for c in chosen
                           if card_has_any_creature_type(c, tribes))
                avail = sum(1 for c in pool
                            if card_has_any_creature_type(c, tribes)
                            and not is_land_card(c))
                want = min(tribe_need, avail)
                tribe_label = " OR ".join(f"t:{t}" for t in tribes)
                if have < want:
                    notes.append(
                        f"{role}: tribe floor {tribe_label} met {have}/{want} — "
                        f"the pool did not have enough matching cards.")
                added = {c["name"] for c in chosen} - before
                if added:
                    print(f"  [tribe-floor] reserved {tribe_label} into theme: "
                          f"{sorted(added)}")
        place_role_cards(role, deck, chosen, pool, target, seen,
                         commander=cmdr_card, scaffold=deck,
                         strategy=cmdr_strategy)
        if not score_info.get("skipped"):
            card_score.stamp_sim_pick(deck, sim_meta)
        if role in FLEX_ROLES:
            chosen_names = {c["name"] for c in chosen}
            flex_pool.extend(c for c in pool if c["name"] not in chosen_names)

    # size lands from the spell curve before filling the land slot.
    mana_plan = mana.plan(deck, cmdr_for_mana, ci_list, min_basics=MIN_BASICS)
    land_target = mana_plan["nonbasic_lands"]
    land_desc = land_role[2]
    land_pool, land_used = await search_ladder(
        role_query("lands", ci, brief, budget_each),
        limit=45, floor=land_target)
    role_pools["lands"] = list(land_pool)
    if len(land_pool) < land_target:
        notes.append(
            f"lands: we wanted {land_target} and found {len(land_pool)}.")
    land_pool = [c for c in land_pool if c["name"] not in seen]
    if land_pool:
        strategy_block = ""
        if brief.get("strategy"):
            strategy_block = (f"\nThe deck being built:\n{brief['strategy']}\n"
                              f"Choose cards that serve THIS plan specifically.\n")
        cmdr_label = " / ".join(c["name"] for c in zone)
        prompt = f"""Commander: {cmdr_label}. Plan: {brief.get('plan') or brief.get('angle')}
{strategy_block}
Fill the "{land_desc}" slot. Choose exactly {min(land_target, len(land_pool))} cards from this list.
Favor cards that support the plan over generically strong cards.

{json.dumps([for_model(c) for c in land_pool[:45]], indent=1)}

Return only a json array of exact names."""
        r = await client.messages.create(model=MODEL, max_tokens=2500,
                                         thinking={"type": "disabled"},
                                         messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in r.content if b.type == "text")
        raw = raw.replace("```json", "").replace("```", "").strip()
        try:
            names = json.loads(raw)
        except json.JSONDecodeError:
            names = []
        valid = {c["name"]: c for c in land_pool}
        chosen = [valid[n] for n in names if n in valid][:land_target]
        chosen = backfill_chosen(
            chosen, land_pool, land_target, cmdr_card, deck,
            strategy=cmdr_strategy)
        shortlist = chosen[:max(land_target, card_score.SAMPLE_SIZE)]
        chosen, sim_meta, ms, score_info = await card_score.rank_for_role(
            deck, land_pool, "lands", land_target, shortlist,
            cmdr_cmc, cmdr_for_mana, ci, role_pools, spell_roles,
            seen, brief=brief, land_pool=land_pool,
            strategy=cmdr_strategy)
        sim_scoring_ms += ms
        if score_info.get("skipped"):
            sim_skips.append(f"lands: {score_info['reason']}")
        place_role_cards("lands", deck, chosen, land_pool, land_target, seen,
                         commander=cmdr_card, scaffold=deck,
                         strategy=cmdr_strategy)
        if not score_info.get("skipped"):
            card_score.stamp_sim_pick(deck, sim_meta)
    else:
        notes.append("No results for lands — query came back empty.")

    # lands are a target, not residue. Basics hit mana_plan total; theme/
    # deepcut leftovers fill to deck_size (99, or 98 with partners). Basics are
    # fetched so they carry art/buy links.
    basic_names = {"W": "Plains", "U": "Island", "B": "Swamp",
                   "R": "Mountain", "G": "Forest"}
    fills = [basic_names[c] for c in ci if c in basic_names] or ["Wastes"]
    basic_cards = []
    for name in fills:  # preserve CI order; duplicates ok for cycling
        try:
            basic_cards.append(trim_card(await scry("/cards/named", exact=name)))
        except Exception:
            basic_cards.append({
                "name": name, "usd": "0.10", "cost": "", "type": f"Basic Land — {name}",
                "text": "", "color_identity": list(BASIC_CI.get(name, [])),
                "legal": "legal", "gc": False, "cmc": 0, "keywords": [],
                "produced_mana": list(BASIC_CI.get(name, [])), "layout": "normal",
                "power": "", "toughness": "", "purchase_uris": {}, "image": None,
                "art": None, "edhrec_rank": None,
            })

    # borrow unused role-pool cards before asking Scryfall for staples,
    # and never let basics push past the land target.
    surplus = surplus_from_pools(role_pools, {c["name"] for c in deck}
                                 | {c["name"] for c in flex_pool})
    nonbasic_now = sum(1 for c in deck if is_land_card(c) and c.get("role") != "basic")
    basics_planned = min(
        max(MIN_BASICS, mana_plan["total_lands"] - nonbasic_now),
        max(0, deck_size - len(deck)))
    spell_room = max(0, deck_size - len(deck) - basics_planned)
    fill_have = sum(1 for c in flex_pool if c["name"] not in {x["name"] for x in deck}
                    and not is_land_card(c))
    fill_have += len(surplus)
    staple_added = 0
    if fill_have < spell_room:
        staples = await on_color_staples(
            ci, brief, budget_each, spell_room - fill_have,
            {c["name"] for c in deck} | {c["name"] for c in flex_pool}
            | {c["name"] for c in surplus})
        surplus = list(surplus) + staples
        staple_added = len(staples)
        if staple_added:
            notes.append(
                f"Filled {staple_added} empty slot(s) with on-colour staples "
                f"(EDHREC rank) after theme/role searches came up short.")

    # flex/surplus fill prefers cards glued to commander + deck so far.
    # Soft prefer only — never drops a card; thin themes still complete to 99.
    flex_pool = prefer_pool(flex_pool, cmdr_card, deck, cmdr_strategy)
    surplus = prefer_pool(surplus, cmdr_card, deck, cmdr_strategy)

    land_fill = finish_mana_base(deck, flex_pool, basic_cards,
                                 total_lands=mana_plan["total_lands"],
                                 deck_size=deck_size,
                                 surplus_pool=surplus)
    land_fill["staple_added"] = staple_added
    final_src = mana.analyze_sources(deck, cmdr_for_mana, ci_list,
                                       land_fill["total_lands"])
    land_fill["supported"] = final_src["supported"]
    land_fill["shortages"] = final_src["shortages"]
    land_fill["avg_mv"] = mana_plan["avg_mv"]
    land_fill["sources_have"] = final_src["have"]
    land_fill["sources_need"] = final_src["need"]
    land_fill["basis"] = (
        f"{mana_plan['basis']}; finished {land_fill['total_lands']} lands "
        f"({land_fill['nonbasic_lands']} nonbasic + {land_fill['basics_added']} basic)")
    if land_fill.get("shortfall"):
        notes.append(
            f"WARNING: deck has {len(deck)} cards, short {land_fill['shortfall']}. "
            f"Roles came back short and surplus/staples could not fill the rest. "
            f"Land count stayed at the planned {mana_plan['total_lands']} — "
            f"rebuild with a theme Scryfall can search, or add cards by hand.")
    if not final_src["supported"] and final_src["shortages"]:
        land_fill["basis"] += f"; under-sourced — {final_src['shortages'][0]['text']}"
    notes.append(land_fill["basis"])

    report = validate_deck(cmdr_card, deck, partner=partner_card, card_pool=card_pool)
    if not report["ok"]:
        notes.append(
            "WARNING: Legality check failed — this list is not buyable as-is:")
        notes.extend(
            f"WARNING: • {v['detail']}" for v in report["violations"])

    if has_partner(cmdr_card) and not partner_card:
        notes.append(PARTNER_NOTICE)
    elif partner_card:
        notes.append(
            f"Partner pair: {cmdr} + {partner_card['name']}. "
            f"Colour identity is {ci}; main deck is {deck_size} cards.")

    total = sum(float(c["usd"]) for c in deck if c.get("usd"))
    budget = budget_report(deck, brief.get("budget_total"))
    rec = await edhrec(cmdr)
    off_meta = []
    if rec:
        popular = json.dumps(rec).lower()
        off_meta = [c["name"] for c in deck
                    if c["role"] in ("theme", "deepcut") and c["name"].lower() not in popular][:12]

    signals = deck_signals(deck, land_fill, brief=brief)
    why_card.annotate(deck, role_query_used, commander=cmdr_card)
    if seed_bundle and seed_bundle.status == "seeded" and seed_bundle.meta:
        for i, c in enumerate(deck):
            deck[i] = edhrec_seed.annotate_card(
                c, seed_bundle.meta, status=seed_bundle.status)
    # cohesion was computed into score_info then dropped. Surface it
    # on the build response — honesty about the graph, not a selection change.
    cohesion = None
    try:
        cohesion = synergy.cohesion(deck, cmdr_card)
    except Exception as e:
        notes.append(errors.failure_note("cohesion", e))
    # theme/strategy accounting — every card names its job.
    theme_label = brief_theme(brief) or "theme"
    tribes = theme_creature_types(brief)
    tribe = tribes[0] if tribes else None
    theme_legal = None
    try:
        colors = brief.get("colors") or "".join(ci_list or [])
        if theme_label and theme_label != "theme":
            rb = await build_readback(theme_label, colors)
            composed = (rb or {}).get("composed") or {}
            # Unknown stays None — never coerce a failed count to 0.
            if isinstance(composed.get("count"), int):
                theme_legal = composed["count"]
    except Exception as e:
        notes.append(errors.failure_note("accounting pool", e))
    accounting = why_card.account_deck(
        deck,
        commander=cmdr_card,
        theme_label=theme_label,
        tribe=tribe,
        tribes=tribes,
        theme_legal_count=theme_legal,
    )
    if (isinstance(theme_legal, int) and theme_legal < READINESS_POOL_FLOOR
            and theme_legal > 0):
        notes.append(
            f"Only {theme_legal} on-theme cards in these colours — "
            f"the rest of the 99 serve strategy and curve."
        )
    subs = substitution.build_substitutions(deck, role_pools, ci)
    sim_out = await asyncio.to_thread(
        overview.run_build_sim, deck, cmdr_cmc)
    cmdr_label = " / ".join(c["name"] for c in zone)
    try:
        overview_data = await overview.generate_overview(
            client, MODEL, cmdr_label, cmdr_card, deck, brief, signals, sim_out,
            for_model, parse_json_list, partner_card=partner_card)
    except Exception as e:
        notes.append(errors.failure_note("overview", e))
        overview_data = {"how_it_works": "", "combos": [], "pros": [], "cons": []}

    text = "\n".join(f"1 {c['name']}" for c in zone) + "\n" + "\n".join(
        f"1 {c['name']}" for c in deck)
    mass = tcgplayer_mass_entry(cmdr, deck,
                                partner=partner_card["name"] if partner_card else None)
    out = {"commander": cmdr, "deck": deck, "price": round(total, 2),
           # the commander's own card, so the UI can show its art like any other
           "commander_card": cmdr_card,
           "partner": partner_card["name"] if partner_card else None,
           "partner_card": partner_card,
           "commanders": [c["name"] for c in zone],
           "commander_cards": zone,
           "color_identity": ci_list,
           "deck_size": deck_size,
           "budget": budget,
           "archidekt": text, "tcgplayer": mass,
           "off_meta": off_meta, "notes": notes,
           "signals": signals,  # countable only
           "overview": overview_data,  # sim-grounded explanation
           "substitutions": subs,  # role, replaceability, alternates
           "accounting": accounting,  # theme/strategy buckets
           # graph shape of the 99. Keys: edges, orphans, unknown,
           # unread, hub_dependence. unknown/unread = could not read ≠ orphan.
           # None when we could not compute — unknown ≠ zero.
           "cohesion": cohesion,
           # unused ladder cards the sliders swap from. Persisted with
           # the deck so /api/simulate can move composition without inventing.
           "role_pools": role_pools,
           # card-type counts — the rebuild target for card swaps and composition rebuilds.
           "composition": tweak_mod.composition_of(deck),
           # at the group level. The query that filled a role is the
           # honest answer to "why these cards" for the whole group, and it was
           # being used for per-card lines then thrown away.
           "role_queries": {r: why_card.explain_group(q, r) or "" 
                            for r, q in role_query_used.items()},
           "role_queries_raw": role_query_used,
           "lands": land_fill,  # decided total, not residue
           "legal": report["ok"], "buyable": report["buyable"],
           "violations": report["violations"], "banned": report["banned"],
           "card_pool": card_pool, "legality_mode": report["mode"],
           "brief": brief,  # reasoning travels with the list
           "sim_scoring": {  # build-time candidate scoring
               "latency_ms": sim_scoring_ms,
               "runs_per_candidate": card_score.SCORE_RUNS,
               "turn": card_score.TARGET_TURN,
               "skipped_roles": sim_skips,
           }}
    if seed_bundle is not None:
        out["edhrec_seed"] = seed_bundle.as_response()
    # the plan that chose the cards. Explains; does not grade.
    #
    # Restored after a conflict resolution dropped it. Plan-on-build and the archetype payoff floors both
    # restructured the block above; composing them lost this line, and the only
    # thing that caught it was the plan-on-build test.
    #
    # decided:true with no label / empty commitments is a lie in data.
    # derive() makes that structurally impossible; refuse if it ever reappears.
    if plan_for_build is not None:
        arch = plan_for_build.get("archetype") or {}
        if arch.get("decided") and not plan_graph.plan_is_success(plan_for_build):
            raise HTTPException(
                422,
                "Plan decided nothing usable — keep asking what the deck is about.")
        out["plan"] = plan_for_build
    elif plan_derive_failed:
        out["plan_derive_failed"] = True
    # curve measurement + derived skeleton. When
    # SKELETON_LIVE the same roles already shaped the search targets above;
    # the report names what was applied (live=true) rather than observation.
    try:
        from deckwright import curve as _curve
        skel = _curve.skeleton_for(cmdr_card, {
            **(plan_for_build or {}),
            "brief": brief,
            "curve": (plan_for_build or {}).get("curve") or _curve.preference(brief),
        })
        cast = _curve.castable_curve(
            deck, cmdr_cmc, runs=120, seed=1,
        )
        missed = _curve.missed_land_drops(deck, cmdr_cmc, runs=120, seed=1)
        split = _curve.ramp_split(deck)
        out["curve_report"] = {
            "preference": skel.get("preference"),
            "castable_curve": cast,
            "missed_land_drops": missed,
            "ramp_split": split,
            "skeleton": skel,
            "roles_applied": [
                {"role": n, "count": c} for n, c, _ in skeleton if n != "lands"
            ],
        }
    except Exception as e:
        notes.append(errors.failure_note("curve_report", e))
    # her interrogation turns, verbatim. Display already reads this.
    conversation = conversation_from_payload(payload)
    if conversation is not None:
        out["conversation"] = conversation
    # how many lookups stayed on disk vs hit the live API.
    m = corpus.meters().as_dict()
    out["corpus"] = {
        **corpus.get_corpus().status(),
        "calls": m,
    }
    print(
        f"  [corpus] local={m['local']} live_needed={m['live_needed']} "
        f"live_done={m['live_done']} missing={m['missing']}"
    )
    # B/C clarity — commander-read + audit. Does not change deck names.
    # Stamps out["commander_strategy"] and out["strategy_audit"] when candidate B or C.
    strategy_audit.attach_clarity(out, enabled=strategy_audit.clarity_enabled())
    # per-card steer votes. C also stamps serve_report.
    strategy_steer.stamp_votes(out)
    persist_deck(out)  # unique id — never silently overwrite
    return out


async def analyze_imported_deck(
    result: dict,
    brief: dict,
    card_pool: str,
    *,
    precon: dict | None = None,
    way_playstyle: str | None = None,
) -> dict[str, Any]:
    """Shared tail of /api/resolve and /api/precons/{id} — same pipeline."""
    out: dict[str, Any] = {
        "unresolved": result["unresolved"],
        "commander_pick": result["commander_pick"],
        "entries": result["entries"],
        "deck": result["deck"],
        "notes": [],
    }
    if precon:
        out["precon"] = precon

    pick = result["commander_pick"]
    if pick["status"] != "found":
        if pick.get("reason"):
            out["notes"].append(pick["reason"])
        return out

    cmdr_name = pick["commander"]
    cmdr_card = result["commander_card"]
    partner_name = pick.get("partner") or None
    partner_card = result.get("partner_card")
    if partner_name and not partner_card:
        partner_card = (result.get("resolved") or {}).get(partner_name)
    zone = result.get("commander_cards") or resolve_commanders(cmdr_card, partner_card)
    deck = result["deck"]
    ci_list = union_color_identity(*zone)
    deck_size = deck_size_for(len(zone) or 1)

    report = validate_deck(
        cmdr_card, deck, partner=partner_card, card_pool=card_pool)
    if not report["ok"]:
        out["notes"].append(
            "WARNING: Legality check failed — this list is not buyable as-is:")
        out["notes"].extend(
            f"WARNING: • {v['detail']}" for v in report["violations"])

    if partner_card:
        out["notes"].append(
            f"Partner pair: {cmdr_name} + {partner_card['name']}. "
            f"Colour identity is {identity_string(ci_list)}; "
            f"main deck is {deck_size} cards.")
    elif has_partner(cmdr_card):
        out["notes"].append(PARTNER_NOTICE)

    total = sum(float(c["usd"]) for c in deck if c.get("usd"))
    budget = budget_report(deck, brief.get("budget_total"))
    signals = deck_signals(deck, brief=brief)
    sim_out: dict = {}
    try:
        cmdr_cmc = min(int(c.get("cmc") or 5) for c in zone) if zone else 5
        sim_out = await asyncio.to_thread(
            overview.run_build_sim, deck, cmdr_cmc)
    except Exception as e:
        out["notes"].append(errors.failure_note("sim", e))

    overview_data = {"how_it_works": "", "combos": [], "pros": [], "cons": []}
    cmdr_label = (f"{cmdr_name} / {partner_card['name']}"
                  if partner_card else cmdr_name)
    # CLI needs no key — run.sh unsets ANTHROPIC_API_KEY when the CLI is on
    # PATH. Gating on the key alone skipped overview on the CLI path.
    if llm.can_call(client):
        try:
            overview_data = await overview.generate_overview(
                client, MODEL, cmdr_label, cmdr_card, deck, brief, signals,
                sim_out, for_model, parse_json_list, partner_card=partner_card)
        except Exception as e:
            out["notes"].append(f"overview: {type(e).__name__}: {e}")

    text_export = (
        "\n".join(f"1 {c['name']}" for c in zone) + "\n"
        + "\n".join(f"1 {c['name']}" for c in deck)
    )
    mass = tcgplayer_mass_entry(
        cmdr_name, deck,
        partner=partner_card["name"] if partner_card else None)

    out.update({
        "commander": cmdr_name,
        "commander_card": cmdr_card,
        # Same shape as /api/build so the UI needs no new vocabulary.
        "partner": partner_card["name"] if partner_card else None,
        "partner_card": partner_card,
        "commanders": [c["name"] for c in zone],
        "commander_cards": zone,
        "color_identity": ci_list,
        "deck_size": deck_size,
        "price": round(total, 2),
        "budget": budget,
        "archidekt": text_export,
        "tcgplayer": mass,
        "signals": signals,
        "overview": overview_data,
        "sim": sim_out,
        "legal": report["ok"],
        "buyable": report["buyable"],
        "violations": report["violations"],
        "banned": report["banned"],
        "card_pool": card_pool,
        "legality_mode": report["mode"],
    })
    # a precon (or any import without an ask) still needs a brief so
    # the plan half of the page has strategy/theme to hang thesis and sim on.
    if precon and not (brief or {}).get("strategy"):
        out["brief"] = precon_bake.brief_from_analysis(
            overview_data, deck, cmdr_card, signals)
    elif brief:
        out["brief"] = brief
    # ways + beef-it-up for a bought precon (not a pasted list).
    if precon:
        out = await attach_precon_upgrade(
            out, card_pool=card_pool, way_playstyle=way_playstyle)
    # import/precon is a saved deck from the moment it resolves —
    # same unique id as /api/build. Catalog browse is GET /api/precons;
    # a POST that reaches this tail is a chosen list, so it persists. Illegal
    # lists still save; violations stay on the record.
    # B clarity on imports too — audit only, no steer.
    strategy_audit.attach_clarity(out, enabled=strategy_audit.clarity_enabled())
    strategy_steer.stamp_votes(out)
    persist_deck(out)
    return out


async def compute_coverage(text: str, deck: list, brief: dict,
                           identity: str | None = None) -> dict | None:
    """The arithmetic behind a pool constraint, or None if there is no pool.

    Shared by /api/coverage and the ask box. Returns None — never raises and
    never guesses — when the text does not name a set we can search, so every
    caller can treat "no constraint here" as the ordinary case.
    """
    if not text or not deck:
        return None
    sets = (await scry("/sets")).get("data") or []
    found = coverage.resolve_set(text, sets)
    if not found:
        return None

    code = found["code"]
    ci_set = set()
    for c in deck:
        ci_set.update(c.get("color_identity") or [])
    ci = "".join(sorted(identity or ci_set))
    pool_q = apply_pool_filter(f"set:{code}", parse_card_pool(brief or {}))
    in_ci_q = f"{pool_q} id<={ci}" if ci else pool_q

    pool_total = await count_matching(pool_q)
    in_identity = await count_matching(in_ci_q)
    if pool_total is None or in_identity is None:
        # Scryfall hiccupped. Say nothing rather than report a zero she would
        # read as "that is impossible".
        return None
    pool_cards = await search(pool_q, limit=400)   # names, for the overlap check

    # What each role could draw from the set, so the trade is legible: a set
    # rich in removal and thin on ramp changes how the deck plays, and that is
    # the part she can actually decide on.
    role_pool: dict[str, int] = {}
    for role, _target, _label in ROLES:
        if role == "lands":
            continue
        rungs = role_query(role, ci, brief or {},
                           float((brief or {}).get("budget_each") or 0) or 12.0)
        rungs = list(rungs) if isinstance(rungs, (list, tuple)) else [rungs]
        # One rung per role, counted rather than fetched. Walking four rungs
        # across seven roles was thirty-odd requests for a single question,
        # which tripped Scryfall's rate limit — and a 429 came back as zero,
        # so the answer said the set was empty. The widest rung is the last
        # one, and it is the honest measure of "what could this role draw
        # from that set".
        widest = rungs[-1] if rungs else ""
        n = await count_matching(f"{widest} set:{code}") if widest else 0
        # count_matching returns None on failure. `n or 0` turned that
        # unknown into an empty role — the same lie pool_total refuses above.
        if n is None:
            return None
        role_pool[role] = n

    out = coverage.summarise(
        pool_total=pool_total,
        in_identity=in_identity,
        deck_size=len(deck),
        already=len(coverage.deck_from_pool(deck, {c["name"] for c in pool_cards})),
        role_now=coverage.role_targets(deck),
        role_pool=role_pool,
        land_count=sum(1 for c in deck if is_land_card(c)),
    )
    out.update({"ok": True, "set": found, "identity": ci})
    return out


@app.post("/api/coverage")
async def coverage_check(payload: dict):
    """How much of a deck could actually come from what she asked for.

    Arithmetic only — see coverage.py. Answers before a rebuild, never after,
    because the failure this exists to stop is the app describing a deck that
    cannot be built.
    """
    out = await compute_coverage(
        (payload.get("text") or "").strip(),
        payload.get("deck") or [],
        payload.get("brief") or {},
        payload.get("identity"))
    if out is None:
        return {"ok": False, "reason": "no_set",
                "detail": "That does not match a Magic set we can search."}
    return out


@app.post("/api/cards")
async def cards_by_name(payload: dict):
    """Look up a handful of cards by exact name.

    For the rules page, which teaches with real cards — an explanation of
    "instant" that shows an actual instant beats one that describes the idea.
    Goes through scry_post so Scryfall still sees our User-Agent and rate gate;
    the browser must not talk to them directly on our behalf.

    Capped: this serves a static page with a fixed handful of examples, not a
    lookup surface. Front faces only, which is all the examples need.
    """
    names = [n for n in (payload.get("names") or []) if isinstance(n, str)][:20]
    if not names:
        return {"cards": []}
    data = await scry_post("/cards/collection",
                           {"identifiers": [{"name": n} for n in names]})
    return {"cards": [trim_card(c) for c in (data.get("data") or [])]}


# two different things were called "resolve". `resolve.py` maps her
# WORDS onto Scryfall operators (theme resolution). This endpoint takes a pasted
# DECKLIST and resolves card names against Scryfall. Same verb, unrelated jobs —
# an earlier system audit recorded the collision as a finding.
#
# /api/import is the honest name and the one to use. /api/resolve stays for the
# frontend and any saved bookmark; both are the same handler, not a redirect, so
# behaviour cannot drift between them.
@app.post("/api/import")
@app.post("/api/resolve")
async def resolve_deck(payload: dict):
    """paste a decklist, resolve against Scryfall, run analysis.

    Registered at /api/import (canonical) and /api/resolve (kept working).
    This is decklist import. Theme resolution is `resolve.py`, reached through
    /api/readback — different thing, similar word.
    """
    text = payload.get("text") or ""
    brief = payload.get("brief") or {}
    card_pool = parse_card_pool({**brief, **payload})
    explicit = payload.get("commander")
    explicit_partner = payload.get("partner")

    result = await import_deck.resolve_decklist(
        text, scry_post, trim_card, explicit, explicit_partner)
    return await analyze_imported_deck(result, brief, card_pool)


_PRECON_ID_RE = re.compile(r"^\d+$")


@app.get("/api/precons")
async def list_precons():
    """Commander precons from Archidekt's Archidekt_Precons account."""
    try:
        catalog = await precons.get_catalog()
    except precons.PreconsUnavailable as e:
        errors.log_exception("precons", e)
        return JSONResponse(
            status_code=503,
            content={"available": False, "message": precons.PASTE_FALLBACK,
                     "precons": []},
        )
    return {"available": True, "precons": catalog}


async def refresh_card_prices(
    deck: list[dict], commander_card: dict,
) -> tuple[list[dict], dict]:
    """Live Scryfall usd for a baked list — the one number that must stay current."""
    names = []
    if commander_card.get("name"):
        names.append(commander_card["name"])
    names.extend(c["name"] for c in deck if c.get("name"))
    priced, _ = await import_deck.resolve_names(names, scry_post, trim_card)
    return precon_bake.apply_prices(deck, commander_card, priced)


async def fetch_precon_upgrades(
    commander_card: dict,
    deck: list[dict],
    brief: dict | None,
    *,
    card_pool: str = DEFAULT_CARD_POOL,
    limit: int = 8,
    way_playstyle: str | None = None,
) -> dict[str, Any]:
    """Scryfall-verified beef-it-up candidates for a bought precon.

    Queries come from synergy recall + optional tribe theme. Every name is
    read off a Scryfall response. Transport failure → status unknown, never
    a fake empty "no upgrades".

    per-query `instrument` (ok/empty/unknown) and soft-prefer cards
    we can read (signal ∨ keyword) inside rank_upgrades.
    """
    queries = precon_upgrade.upgrade_queries(
        commander_card, brief, way_playstyle=way_playstyle)
    if not queries:
        # Unknown demand (could not look) vs looked-and-nothing-to-search.
        demand = synergy.demand_profile(commander_card)
        if demand["status"] == synergy.UNKNOWN:
            return {
                "status": "unknown",
                "cards": [],
                "already_in": [],
                "already_in_note": None,
                "instrument": {"queries": [], "way_playstyle": way_playstyle},
                "note": "Could not read the commander — not inventing upgrades.",
            }
        return {
            "status": "empty",
            "cards": [],
            "already_in": [],
            "already_in_note": None,
            "instrument": {"queries": [], "way_playstyle": way_playstyle},
            "note": "No upgrade searches for this commander — not inventing any.",
        }

    ci = "".join(sorted(commander_card.get("color_identity") or []))
    pool: list[dict] = []
    seen: set[str] = set()
    search_failed = False
    instrument_queries: list[dict[str, Any]] = []
    for frag in queries:
        q = f"({frag}) -t:land -is:extra"
        if ci:
            q = f"({frag}) id<={ci} -t:land -is:extra"
        q = apply_pool_filter(q, card_pool)
        try:
            got = await search(q, limit=20)
        except ScryfallError as e:
            print(f"  [precon-upgrades] {q[:70]} failed: {e}")
            search_failed = True
            instrument_queries.append({
                "fragment": frag,
                "query": q,
                "status": "unknown",
                "count": None,
            })
            continue
        # Page-sized hit count — enough for honesty; not total_cards.
        status = "empty" if not got else "ok"
        instrument_queries.append({
            "fragment": frag,
            "query": q,
            "status": status,
            "count": len(got),
        })
        for c in got:
            name = c.get("name")
            if name and name not in seen:
                seen.add(name)
                pool.append(c)

    instrument = {
        "queries": instrument_queries,
        "way_playstyle": way_playstyle,
        "ok": sum(1 for r in instrument_queries if r["status"] == "ok"),
        "empty": sum(1 for r in instrument_queries if r["status"] == "empty"),
        "unknown": sum(1 for r in instrument_queries if r["status"] == "unknown"),
        "pool_size": len(pool),
    }

    if not pool and search_failed:
        return {
            "status": "unknown",
            "cards": [],
            "already_in": [],
            "already_in_note": None,
            "instrument": instrument,
            "note": "Scryfall unreachable for upgrades — not inventing any.",
        }
    ranked = precon_upgrade.rank_upgrades(
        pool, commander_card, deck, limit=limit)
    ranked["instrument"] = instrument
    return ranked


async def attach_precon_upgrade(out: dict[str, Any],
                                *, card_pool: str = DEFAULT_CARD_POOL,
                                way_playstyle: str | None = None) -> dict:
    """stamp ways (offline) + beef-it-up (Scryfall) on a precon response."""
    if not out.get("precon"):
        return out
    precon_upgrade.attach_ways(out)
    brief = out.get("brief") or {}
    # explicit way pick wins when it is one of the detected ways.
    way = way_playstyle if isinstance(way_playstyle, str) else None
    way = (way or "").strip() or None
    detected = {w.get("playstyle") for w in (out.get("ways_to_play") or [])}
    if way and way in detected:
        out["chosen_playstyle"] = way
    else:
        way = out.get("chosen_playstyle")
    try:
        out["upgrades"] = await fetch_precon_upgrades(
            out.get("commander_card") or {},
            out.get("deck") or [],
            brief,
            card_pool=card_pool,
            way_playstyle=way,
        )
    except Exception as e:
        print(f"  [precon-upgrades] {type(e).__name__}: {e}")
        out["upgrades"] = {
            "status": "unknown",
            "cards": [],
            "already_in": [],
            "already_in_note": None,
            "instrument": {"queries": [], "way_playstyle": way},
            "note": "Could not look up upgrades — not inventing any.",
        }
    return out


async def serve_baked_precon(
    cached: dict, brief: dict, card_pool: str,
    *, way_playstyle: str | None = None,
) -> dict[str, Any]:
    """Assemble the resolve-shaped response from a fresh bake + live prices."""
    deck, cmdr_card = await refresh_card_prices(
        list(cached.get("deck") or []),
        dict(cached.get("commander_card") or {}),
    )
    cmdr_name = cached["commander"]
    report = validate_deck(cmdr_card, deck, card_pool=card_pool)
    total = sum(float(c["usd"]) for c in deck if c.get("usd"))
    budget = budget_report(deck, brief.get("budget_total"))
    text_export = f"1 {cmdr_name}\n" + "\n".join(f"1 {c['name']}" for c in deck)
    mass = tcgplayer_mass_entry(cmdr_name, deck)
    out = precon_bake.response_from_bake(
        cached,
        deck=deck,
        commander_card=cmdr_card,
        price=round(total, 2),
        budget=budget,
        legal_report=report,
        archidekt=text_export,
        tcgplayer=mass,
        card_pool=card_pool,
    )
    # opening a precon is choosing it — same persist as paste import.
    out = await attach_precon_upgrade(
        out, card_pool=card_pool, way_playstyle=way_playstyle)
    strategy_audit.attach_clarity(out, enabled=strategy_audit.clarity_enabled())
    strategy_steer.stamp_votes(out)
    persist_deck(out)
    return out


@app.post("/api/precons/{precon_id}")
async def load_precon(precon_id: str, payload: dict | None = None):
    """load one precon — baked analysis when fresh, else live."""
    if not _PRECON_ID_RE.fullmatch(precon_id):
        return JSONResponse(status_code=400, content={"error": "invalid precon id"})
    payload = payload or {}
    brief = payload.get("brief") or {}
    card_pool = parse_card_pool({**brief, **payload})
    explicit = payload.get("commander")
    explicit_partner = payload.get("partner")
    way_raw = payload.get("way_playstyle")
    way_playstyle = way_raw.strip() if isinstance(way_raw, str) else None
    way_playstyle = way_playstyle or None

    cached = precon_bake.read_bake(precon_id)
    live_text = None
    live_meta = None
    live_error = None

    try:
        live_text, live_cmdr, live_meta = await precons.decklist_for_precon(precon_id)
        if not explicit:
            explicit = live_cmdr
    except httpx.HTTPError as e:
        live_error = e

    # Prefer the shipped bake when version stamps still match the engine we run.
    if cached and precon_bake.analysis_fresh(cached):
        if live_text is not None:
            # Resolve once: live prices + hash check against the bake. A hash
            # mismatch means Archidekt edited the list — refuse stale analysis.
            live_result = await import_deck.resolve_decklist(
                live_text, scry_post, trim_card, explicit, explicit_partner)
            pick = live_result["commander_pick"]
            if pick["status"] == "found":
                live_deck = live_result["deck"]
                if (explicit and explicit != cached.get("commander")) or \
                   not precon_bake.analysis_fresh(cached, live_deck):
                    return await analyze_imported_deck(
                        live_result, brief, card_pool, precon=live_meta,
                        way_playstyle=way_playstyle)
                # Fresh bake: keep baked overview/signals/sim, live cards/prices.
                report = validate_deck(
                    live_result["commander_card"], live_deck, card_pool=card_pool)
                total = sum(float(c["usd"]) for c in live_deck if c.get("usd"))
                cmdr_name = pick["commander"]
                text_export = (
                    f"1 {cmdr_name}\n"
                    + "\n".join(f"1 {c['name']}" for c in live_deck)
                )
                baked_out = precon_bake.response_from_bake(
                    cached,
                    deck=live_deck,
                    commander_card=live_result["commander_card"],
                    price=round(total, 2),
                    budget=budget_report(live_deck, brief.get("budget_total")),
                    legal_report=report,
                    archidekt=text_export,
                    tcgplayer=tcgplayer_mass_entry(cmdr_name, live_deck),
                    card_pool=card_pool,
                )
                # baked precon load is a chosen deck — persist like import.
                baked_out = await attach_precon_upgrade(
                    baked_out, card_pool=card_pool,
                    way_playstyle=way_playstyle)
                persist_deck(baked_out)
                return baked_out
            # Ambiguous/missing commander — same as live import UX.
            return await analyze_imported_deck(
                live_result, brief, card_pool, precon=live_meta,
                way_playstyle=way_playstyle)

        # Archidekt down — serve the bake as a stale catalog. Versions matched;
        # list hash cannot be re-checked without the remote.
        if explicit and explicit != cached.get("commander"):
            return JSONResponse(
                status_code=503,
                content={
                    "error": "precon_unavailable",
                    "message": precons.PASTE_FALLBACK,
                },
            )
        return await serve_baked_precon(
            cached, brief, card_pool, way_playstyle=way_playstyle)

    # No usable bake — live pipeline, or 503 if Archidekt is also down.
    if live_text is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "precon_unavailable",
                "message": precons.PASTE_FALLBACK,
            },
        )

    result = await import_deck.resolve_decklist(
        live_text, scry_post, trim_card, explicit, explicit_partner)
    return await analyze_imported_deck(
        result, brief, card_pool, precon=live_meta,
        way_playstyle=way_playstyle)


def deck_save_slug(commander: str, when: datetime | None = None) -> str:
    """Filesystem-safe unique id. Two builds of the same commander never collide."""
    base = re.sub(r"[^A-Za-z0-9]+", "_", (commander or "deck").split(",")[0]).strip("_")
    base = (base or "deck")[:48]
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{base}_{stamp}"


def conversation_from_payload(payload: dict) -> list[dict] | None:
    """Pull the interrogation transcript off a build payload.

    This is the only thing on a saved deck that is her words rather than ours.
    Store what she typed — no summarising, no truncating, no model pass.
    Assistant turns stay too: the question she answered is context for the answer.
    Missing or empty is fine; a precon or pasted list has no conversation.
    """
    raw = payload.get("conversation")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        # Exact text. Strip nothing beyond what JSON already gave us.
        out.append({"role": role, "content": content})
    return out or None


def persist_deck(out: dict, directory: pathlib.Path | None = None) -> pathlib.Path:
    """Write a built deck to disk under a unique slug. Mutates out with id/saved_at.

    `directory` defaults to SAVE_DIR at call time (not def time) so tests can
    rebind app.SAVE_DIR the same way load_saved_deck / write_saved_deck do.
    """
    directory = directory or SAVE_DIR
    when = datetime.now(timezone.utc)
    slug = deck_save_slug(out.get("commander") or "deck", when)
    out["id"] = slug
    out["saved_at"] = when.isoformat().replace("+00:00", "Z")
    if not out.get("name"):
        out["name"] = out.get("commander") or "deck"
    # stamp mechanic density from Scryfall keywords + family taxonomy.
    # Recountable off the list — never a model opinion.
    mechanics.attach_mechanics(out)
    path = directory / f"{slug}.json"
    path.write_text(json.dumps(out, indent=1))
    return path


# Hyphens are in here because commander names have them. The first version of
# this pattern was [A-Za-z0-9_]+, which made every deck named after a hyphenated
# commander — Niv-Mizzet, Jaya Ballard — unreachable: it still appeared in the
# saved list and then did nothing when clicked. Path traversal needs "/" or
# "..", and neither survives this class.
_SLUG_RE = re.compile(r"[A-Za-z0-9_-]+")
# Caches that live in the same directory and are not decks.
_NOT_DECKS = {"vocab", "symbols", "precons", "mechanics"}


def saved_deck_path(slug: str, directory: pathlib.Path | None = None) -> pathlib.Path | None:
    """Resolve a saved-deck file. Returns None for bad slugs or missing files.

    The slug must be a bare file stem and the resolved path must stay inside
    the save directory (see safe_paths.safe_child_path).
    """
    directory = directory or SAVE_DIR
    if not slug or not _SLUG_RE.fullmatch(slug) or slug in _NOT_DECKS:
        return None
    p = safe_paths.safe_child_path(directory, slug)
    if p is None:
        return None
    return p if p.exists() else None


def load_saved_deck(slug: str, directory: pathlib.Path | None = None) -> dict | None:
    """Load one saved deck by id. None if missing or corrupt."""
    p = saved_deck_path(slug, directory)
    if not p:
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if "commander" not in data or "deck" not in data:
        return None
    return data


def write_saved_deck(slug: str, data: dict, directory: pathlib.Path | None = None) -> bool:
    """Overwrite an existing saved deck. False if the slug is invalid."""
    p = saved_deck_path(slug, directory)
    if not p:
        return False
    # list edits (swaps, pending accepts) must refresh density.
    mechanics.attach_mechanics(data)
    p.write_text(json.dumps(data, indent=1))
    return True


# ---------------------------------------------------------------- sim cache
#
# The baseline thesis + simulation for a saved deck is written once and read
# back on every open. Sliders re-run /api/simulate with a tweak and must never
# overwrite this — only the deck's own untweaked result is stored.
# Invalidation: deck_list_hash + sim.SIM_VERSION (see bake.py). Same stamps
# will use for precon analysis.


def build_sim_baseline(deck: list, thesis: dict, sim_out: dict) -> dict:
    """Stamp a baseline result for storage on a saved deck."""
    payload = {
        "thesis": {
            "payoff_cards": list(thesis.get("payoff_cards") or []),
            "conditions": list(thesis.get("conditions") or []),
        },
        "runs": sim_out.get("runs"),
        "curves": sim_out.get("curves") or [],
        "diagnostics": sim_out.get("diagnostics") or {},
        "assumptions": sim_out.get("assumptions") or sim.ASSUMPTIONS,
        "review": sim_out.get("review") or {},
    }
    if sim_out.get("good_hand") is not None:
        payload["good_hand"] = sim_out["good_hand"]
    if sim_out.get("bad_hand") is not None:
        payload["bad_hand"] = sim_out["bad_hand"]
    return bake.stamp(payload, deck, sim.SIM_VERSION, version_key="sim_version")


def sim_baseline_fresh(data: dict) -> bool:
    """True when the saved deck's simulation matches its list and engine."""
    return bake.fresh(data.get("simulation"), data.get("deck") or [],
                      sim.SIM_VERSION, version_key="sim_version")


def expose_saved_deck(data: dict) -> dict:
    """Client-facing saved deck: omit a stale or missing simulation block."""
    return bake.strip_stale(data, "simulation", sim.SIM_VERSION,
                            version_key="sim_version")


def attach_sim_baseline(data: dict, thesis: dict, sim_out: dict) -> dict:
    """Write a fresh baseline onto a saved-deck dict. Mutates and returns it."""
    data["simulation"] = build_sim_baseline(data.get("deck") or [], thesis, sim_out)
    return data


def store_sim_baseline(slug: str, thesis: dict, sim_out: dict,
                       directory: pathlib.Path | None = None) -> dict | None:
    """Persist a baseline onto an existing saved deck. None if slug missing."""
    data = load_saved_deck(slug, directory)
    if not data:
        return None
    attach_sim_baseline(data, thesis, sim_out)
    if not write_saved_deck(slug, data, directory):
        return None
    return expose_saved_deck(data)


def delete_saved_deck(slug: str, directory: pathlib.Path | None = None) -> bool:
    """Remove a saved deck file. False if not found."""
    p = saved_deck_path(slug, directory)
    if not p:
        return False
    p.unlink()
    return True


def is_auto_saved_deck(data: dict) -> bool:
    """True when the list should mark this row as an unnamed auto-build.

    Builds persist with name == commander. Playtests flooded the list with no
    way to tell those from decks she meant to keep. A favorite or a christened
    name (anything other than the commander) means she kept it.
    """
    if not isinstance(data, dict):
        return False
    if data.get("favorite"):
        return False
    # A suggested name is still an auto-save. Writing the christening to disk
    # would otherwise have made every playtest look like a deck she kept, and
    # silently undone the sort this function exists to drive. The name helps
    # her recognise the row; "auto-saved" still says who named it.
    if data.get("named_by") == "auto":
        return True
    cmdr = (data.get("commander") or "").strip()
    name = (data.get("name") or cmdr).strip()
    return bool(cmdr) and name == cmdr


def _saved_wish_and_way(data: dict) -> dict:
    """Her words from the brief, for the list row. Never falls back.

    An absent brief yields "" for both, and the row renders neither — it does
    not substitute the theme, the commander, or a guess. Older decks saved
    before the brief was persisted simply have less to show, which is the
    truth about them.
    """
    brief = data.get("brief")
    if not isinstance(brief, dict):
        return {"wish": "", "way": ""}
    return {"wish": str(brief.get("theme") or "").strip(),
            "way": str(brief.get("way_to_play") or "").strip()}


def saved_deck_summary(data: dict, path: pathlib.Path) -> dict:
    """List-row metadata for one saved deck."""
    return {
        "id": data.get("id") or path.stem,
        "name": data.get("name") or data["commander"],
        "commander": data["commander"],
        # Both halves of the command zone. Without this a pair listed under its
        # first commander alone, so two Thrasios decks — one solo, one the
        # Thrasios/Tymna pair — were the same row twice with different prices.
        "partner": data.get("partner"),
        "price": data.get("price"),
        "saved_at": data.get("saved_at") or datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "buyable": data.get("buyable"),
        "card_pool": data.get("card_pool"),
        "has_brief": bool(data.get("brief")),
        # Hers, not ours — so the saved list shows what she called it.
        "theme": data.get("theme") or "",
        "has_primer": bool(str(data.get("primer") or "").strip()),
        "favorite": bool(data.get("favorite")),
        # playtest builds vs decks she named or starred.
        "auto_saved": is_auto_saved_deck(data),
        # An unnamed build's row was the commander's name and a timestamp, so
        # four Adeline playtests were four identical lines. What actually tells
        # them apart is already on the record and was never sent: the wish she
        # typed and the way she picked. Hers, not ours — no name is invented
        # here, the row just stops hiding what she already said.
        **_saved_wish_and_way(data),
        # The commander's face, for the saved list. A row of names is a filing
        # cabinet; the art is how she recognises her own deck at a glance.
        "art": (data.get("commander_card") or {}).get("art")
               or (data.get("commander_card") or {}).get("image") or "",
    }


def list_saved_decks(directory: pathlib.Path | None = None) -> list[dict]:
    """Metadata for decks the UI can reload. Skips vocab cache and junk files."""
    directory = directory or SAVE_DIR
    items = []
    for p in directory.glob("*.json"):
        if p.name in ("vocab.json", "symbols.json", "precons.json"):
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "commander" not in data or "deck" not in data:
            continue
        items.append(saved_deck_summary(data, p))
    items.sort(key=lambda x: x.get("saved_at") or "", reverse=True)
    return items


# ---------------------------------------------------------------- the thesis

# prompt keys must match sim.THESIS_KEYS. Hiding draw/life/board
# keys made "working" under-ask for those plans even though the sim can check them.
THESIS = """You define what "working" means for a specific Commander deck, so it can be
tested by simulation.

You may only use these state keys (the live allowlist — nothing else is checkable):
  commander_in_play (true/false)
  mana_available (n)
  payoffs_in_play (n)
  extra_land_drops (n)
  lands_in_play (n)
  land_drops_this_turn (n)
  cards_in_hand (n)
  cards_drawn (n) — cards drawn beyond the once-per-turn draw
  creatures_in_play (n) — bodies on the battlefield, tokens included
  board_power (n) — bodies scaled by anthem effects
  life_gained (n) — life you gained
  life_drained (n) — opponent life lost to drain effects

The user message may include a **glued payoff allowlist** — exact deck names with
synergy edges to the commander (or to other glued cards). Prefer `payoff_cards`
from that allowlist. If the plan truly needs a deck card with 0 edges, you may
name it and say so in a condition note — do not invent names, and do not silently
pad with ungated staples that are not the plan.

Return only json:
{"payoff_cards": ["exact names from the decklist that ARE the plan working —
                  the engine pieces and their direct enablers, usually 8-20 cards"],
 "conditions": [{"label": "3-4 words", "note": "plain english, one line",
                 "check": {"key": value}}]}

Give 4 conditions. Order them from earliest to latest. At least one should be genuinely
demanding — a thesis where everything reads 90% is not measuring anything.
When the deck is about draw, lifegain, drain, or a wide/tall board, use the matching
keys above — do not force everything through payoffs_in_play alone.

Anti-patterns:
- A lifegain / stay-alive plan must include `life_gained` (or `life_drained` when
  the win is drain) — not only `payoffs_in_play`.
- A go-wide / token swarm plan must include `creatures_in_play` or `board_power`
  — do not express a wide board only as `payoffs_in_play`."""


def thesis_key_fit(plan_text: str, spec: dict) -> bool:
    """Whether thesis conditions use keys that match the plan shape.

    Offline golden / live property helper. Does not invent keys; only checks
    that the stated plan is not forced through payoffs_in_play alone.
    """
    text = (plan_text or "").lower()
    checks: list[dict] = []
    for c in (spec or {}).get("conditions") or []:
        if isinstance(c, dict) and isinstance(c.get("check"), dict):
            checks.append(c["check"])
    keys = {k for check in checks for k in check}

    lifegainish = any(w in text for w in (
        "life", "lifegain", "gain life", "stay alive", "drain", "lifedrain",
    ))
    if lifegainish:
        return bool(keys & {"life_gained", "life_drained"})

    wide = any(w in text for w in (
        "token", "tokens", "go wide", "swarm", "many creatures", "board full",
    ))
    if wide:
        return bool(keys & {"creatures_in_play", "board_power"})

    return True  # no shape claim — nothing to fail


def build_thesis_user_message(
    deck: list,
    brief: dict,
    commander: str,
    *,
    commander_card: dict | None = None,
) -> str:
    """Assemble the thesis user message with glued payoff allowlist."""
    listing = [for_model(c) for c in deck if c.get("role") not in ("basic", "lands")]
    cmdr_card = commander_card
    if cmdr_card is None:
        # Prefer a deck card matching the commander name; else a stub.
        for c in deck:
            if (c.get("name") or "") == commander:
                cmdr_card = c
                break
    if cmdr_card is None:
        cmdr_card = {"name": commander, "oracle_text": None}

    glued = synergy.glued_payoff_candidates(deck, cmdr_card, cap=30)
    if glued["status"] == "ok" and glued["names"]:
        allow = (
            "Glued payoff candidates (prefer these exact names — they have "
            "synergy edges to the commander or to other glued cards):\n"
            + json.dumps(glued["names"], indent=1)
            + "\n"
        )
    elif glued["status"] == "unknown":
        allow = (
            "Glued payoff candidates: could not read the commander — not "
            "inventing an allowlist. Prefer engine pieces from the decklist; "
            "names must still be exact deck names.\n"
        )
    else:
        allow = (
            "Glued payoff candidates: none detected. Prefer engine pieces from "
            "the decklist; if you name a 0-edge card, say so in a condition note.\n"
        )

    return (
        f"Commander: {commander}\n"
        f"The plan: {brief.get('plan') or brief.get('angle')}\n"
        + (f"The deck was built to this strategy:\n{brief['strategy']}\n"
           if brief.get("strategy") else "")
        + "\n" + allow + "\n"
        f"Decklist:\n{json.dumps(listing, indent=1)}"
    )


@app.post("/api/christen")
async def christen_deck(payload: dict):
    """Three names, a theme line and a primer draft for a finished deck.

    Runs after the simulation on purpose — see christen.py. Never fatal: a deck
    without a name is still a deck, so a failure here returns empty fields and
    lets the player type her own rather than taking the screen down.

    same assembled record as /api/ask — brief, overview, and curves
    come from deck_context rather than a hand-picked subset.
    """
    data = deck_record_for_ask(payload)
    deck = data.get("deck") or []
    ctx = deck_context(data)
    commander = (ctx.get("commander") or "").strip()
    if not deck or not commander:
        return JSONResponse({"error": "deck and commander required"}, status_code=400)
    try:
        out = await christening.christen(
            client, MODEL, commander, ctx.get("brief") or {}, deck,
            ctx.get("overview") or {}, ctx.get("curves") or [],
            parse_json_list)
    except Exception as e:
        errors.log_exception("christen", e)
        return {"names": [], "theme": "", "primer": "",
                "note": f"Could not suggest names right now ({type(e).__name__})."}
    return out


# ---------------------------------------------------------------- assembled context
#
# Every model call is stateless. The saved deck file is the durable memory —
# but each endpoint used to hand-pick a different subset and throw the rest
# away. The two things that make "why did you pick these cards" answerable
# (per-card `why`, and her interrogation) were the two /api/ask dropped.
# One assembler; every model-facing endpoint that talks about a finished deck
# reads from it.


def improve_candidates_from_pools(
    deck: list[dict],
    commander: dict | None,
    role_pools: dict | None,
    *,
    cap: int = 12,
) -> list[dict]:
    """Scryfall-verified pool leftovers ranked by glue to the commander.

    Names only from role_pools, never invented. Already-in-99 skipped.
    Soft-prefer edged + readable; orphans stay available after.
    """
    cmdr = commander or {}
    in_deck = {c.get("name") for c in (deck or []) if c.get("name")}
    if cmdr.get("name"):
        in_deck.add(cmdr["name"])
    surplus = surplus_from_pools(role_pools, in_deck)
    if not surplus:
        return []

    def sort_key(c: dict) -> tuple:
        n = synergy.edge_count(c, cmdr, deck or [])
        readable = 1 if precon_upgrade.card_is_readable(c) else 0
        rank = c.get("edhrec_rank")
        try:
            rank_n = int(rank) if rank is not None else 10**9
        except (TypeError, ValueError):
            rank_n = 10**9
        return (-n, -readable, rank_n, c.get("name") or "")

    ranked = sorted(surplus, key=sort_key)
    out: list[dict] = []
    for c in ranked[: max(1, int(cap))]:
        read_status = synergy.card_read_status(c)
        n = synergy.edge_count(c, cmdr, deck or [])
        row = {
            "name": c["name"],
            "role": c.get("role") or "",
            "edge_count": n,
            "readable": read_status == "ok",
            "read_status": read_status,
            "usd": c.get("usd"),
        }
        # unknown/unread with 0 edges — copy, not a silent zero.
        if read_status in (synergy.UNKNOWN, synergy.UNREAD) and not n:
            row["synergy_note"] = synergy.COULD_NOT_READ
        out.append(row)
    return out


def deck_context(data: dict) -> dict:
    """Assemble the full record a model needs for a finished deck.

    Accepts a saved-deck dict or a fresh build dict — same shape. Fields that
    were never written stay absent; the prompt layer says so rather than
    inventing a fill. Part of the shared ask context.

    projects way_preference / way_playstyle (remapped via alias table)
    so ask/improve can steer from the scene she picked when the deck was built.
    """
    if not isinstance(data, dict):
        data = {}
    brief_in = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    brief_in = apply_way_fields(dict(brief_in))
    deck_in = data.get("deck") if isinstance(data.get("deck"), list) else []

    cards = []
    for c in deck_in:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        entry = {"name": c["name"], "role": c.get("role") or ""}
        why = c.get("why")
        if isinstance(why, str) and why.strip():
            entry["why"] = why.strip()
        cards.append(entry)

    mechanics = brief_in.get("mechanics")
    ctx: dict[str, Any] = {
        "commander": data.get("commander") or "",
        "deck": cards,
        "brief": {
            "strategy": brief_in.get("strategy") or "",
            "theme": brief_in.get("theme") or "",
            "angle": brief_in.get("angle") or "",
            "plan": brief_in.get("plan") or "",
            "fits": brief_in.get("fits") or "",
            "mechanics": list(mechanics) if isinstance(mechanics, list) else [],
            "way_preference": brief_in.get("way_preference") or "",
            "way_playstyle": brief_in.get("way_playstyle"),
            "colors": brief_in.get("colors") or "",
        },
    }

    cleaned = conversation_from_payload(data)
    if cleaned is not None:
        ctx["conversation"] = cleaned

    overview = data.get("overview")
    if isinstance(overview, dict) and overview:
        ctx["overview"] = {
            "how_it_works": overview.get("how_it_works") or "",
            "beats": overview.get("beats") or [],
            "combos": overview.get("combos") or [],
            "pros": overview.get("pros") or [],
            "cons": overview.get("cons") or [],
        }

    signals = data.get("signals")
    if isinstance(signals, dict) and signals:
        ctx["signals"] = signals

    # Fresh ask payloads put curves at the top level; saved decks stash them
    # under the saved simulation baseline.
    curves = data.get("curves")
    if not isinstance(curves, list):
        sim_block = data.get("simulation")
        curves = (sim_block.get("curves")
                  if isinstance(sim_block, dict) else None)
    if isinstance(curves, list) and curves:
        ctx["curves"] = curves

    notes = data.get("notes")
    if isinstance(notes, list) and notes:
        ctx["notes"] = [n for n in notes if isinstance(n, str)]

    substitutions = data.get("substitutions")
    if isinstance(substitutions, (list, dict)) and substitutions:
        ctx["substitutions"] = substitutions

    # pressure-test record when one exists. Absent on most decks.
    pressure = data.get("pressure")
    if pressure is None:
        pressure = data.get("pressure_test")
    if isinstance(pressure, dict) and pressure:
        ctx["pressure"] = pressure

    # Keys the source never carried — so the prompt can say "not recorded"
    # instead of the model filling the gap with something plausible.
    missing = []
    if "conversation" not in ctx:
        missing.append("conversation")
    if "overview" not in ctx:
        missing.append("overview")
    if "signals" not in ctx:
        missing.append("signals")
    if "curves" not in ctx:
        missing.append("curves")
    if "notes" not in ctx:
        missing.append("notes")
    if "substitutions" not in ctx:
        missing.append("substitutions")
    if "pressure" not in ctx:
        missing.append("pressure")
    if not any(ctx["brief"].get(k) for k in ("strategy", "theme", "angle", "plan")):
        missing.append("brief")
    ctx["missing"] = missing
    return ctx


async def enrich_ask_context(data: dict) -> dict:
    """Rebuild live instrument + glued improve facts for ask.

    Never freezes Scryfall counts onto the saved file — recomputes each ask.
    Unknown counts stay unknown. Improve names come only from role_pools or
    glued deck payoffs (exact deck names).
    """
    ctx = deck_context(data)
    brief = ctx.get("brief") or {}
    theme = (brief.get("theme") or "").strip()
    colors = (brief.get("colors") or "").strip()
    deck = data.get("deck") if isinstance(data.get("deck"), list) else []
    cmdr_card = data.get("commander_card")
    if not isinstance(cmdr_card, dict) or not cmdr_card.get("name"):
        cmdr_card = None
        name = (data.get("commander") or "").strip()
        for c in deck:
            if (c.get("name") or "") == name:
                cmdr_card = c
                break
        if cmdr_card is None and name:
            cmdr_card = {"name": name, "oracle_text": None}

    instrument = None
    if theme:
        try:
            instrument = await build_instrument(theme, colors)
        except Exception as e:
            print(f"  [ask] instrument rebuild failed: {type(e).__name__}: {e}")
            instrument = {
                "theme": theme,
                "colors": colors,
                "spans": [],
                "composed": None,
                "unknowns": [theme],
                "empty": [],
                "status": "unknown",
            }
    ctx["instrument"] = instrument

    glued = synergy.glued_payoff_candidates(deck, cmdr_card, cap=20)
    ctx["glued_payoffs"] = list(glued.get("names") or [])
    ctx["glued_payoff_status"] = glued.get("status")

    pools = data.get("role_pools") if isinstance(data.get("role_pools"), dict) else {}
    ctx["improve_candidates"] = improve_candidates_from_pools(
        deck, cmdr_card, pools, cap=12)
    return ctx


def ask_prompt_facts(ctx: dict) -> str:
    """Render deck_context into the fact block /api/ask sends the model."""
    lines = [f"COMMANDER: {ctx.get('commander') or ''}"]
    brief = ctx.get("brief") or {}
    plan = (brief.get("strategy") or brief.get("plan")
            or brief.get("angle") or brief.get("theme") or "")
    lines.append(f"PLAN: {plan}")
    if brief.get("mechanics"):
        lines.append("MECHANICS: " + ", ".join(str(m) for m in brief["mechanics"]))

    # her scene + mapped way — ask/improve steers from these.
    way_pref = (brief.get("way_preference") or "").strip()
    way_ps = brief.get("way_playstyle")
    if way_pref:
        if way_ps:
            lines.append(
                f"WAY PREFERENCE: “{way_pref}” → mapped playstyle `{way_ps}`")
        else:
            lines.append(
                f"WAY PREFERENCE: “{way_pref}” — not mapped to a detectable "
                f"playstyle; do not invent one. Keep her words; do not steer "
                f"upgrades as if a way were known.")

    inst = ctx.get("instrument")
    if isinstance(inst, dict) and (inst.get("spans") or inst.get("composed")):
        lines.append("INSTRUMENT (live Scryfall counts — recompute, not frozen):")
        for sp in inst.get("spans") or []:
            st = sp.get("status") or "unknown"
            n = sp.get("count")
            said = sp.get("said") or ""
            if st == "ok" and isinstance(n, int):
                lines.append(f"  · {said}: {n} cards ({sp.get('query')})")
            elif st == "empty":
                lines.append(f"  · {said}: none in Commander — empty, not unknown")
            else:
                lines.append(f"  · {said}: could not look — unknown, not zero")
        comp = inst.get("composed")
        if isinstance(comp, dict) and comp.get("query"):
            st = comp.get("status") or "unknown"
            n = comp.get("count")
            if st == "ok" and isinstance(n, int):
                lines.append(
                    f"  · composed “{comp.get('said')}”: {n} "
                    f"({comp.get('query')})")
            elif st == "empty":
                lines.append(f"  · composed: empty")
            else:
                lines.append(f"  · composed: unknown — could not look")
    elif "instrument" in ctx:
        lines.append("INSTRUMENT: could not rebuild — treat theme depth as unknown")

    glued = ctx.get("glued_payoffs") or []
    if glued:
        lines.append(
            "GLUED PAYOFFS IN THE 99 (edges to commander — prefer these when "
            "naming what the plan is):\n  " + ", ".join(glued[:20]))
    elif ctx.get("glued_payoff_status") == "unknown":
        lines.append("GLUED PAYOFFS: could not read the commander — not inventing any")

    improve = ctx.get("improve_candidates") or []
    if improve:
        bits = []
        for c in improve:
            note = c.get("synergy_note") or ""
            n = c.get("edge_count", 0)
            rs = c.get("read_status") or (
                "ok" if c.get("readable") else "unread")
            # never print edges=0 for unknown/unread — that reads as none.
            if note or (rs in ("unknown", "unread") and not n):
                bits.append(
                    f"{c['name']} [could not read — not the same as no synergy]")
            else:
                bits.append(
                    f"{c['name']} [edges={n}"
                    f"{', readable' if c.get('readable') else ''}]")
        lines.append(
            "IMPROVE CANDIDATES (from build pools, not in the 99 — exact names "
            "only; prefer higher edges when she asks how to improve):\n  "
            + "; ".join(bits))
    else:
        lines.append(
            "IMPROVE CANDIDATES: none in saved role_pools — say so; do not "
            "invent upgrade card names")

    if "conversation" in ctx:
        turns = []
        for t in ctx["conversation"]:
            who = "SHE" if t.get("role") == "user" else "APP"
            turns.append(f"{who}: {t.get('content') or ''}")
        lines.append("INTERROGATION (her words, verbatim):\n" + "\n".join(turns))
    else:
        lines.append("INTERROGATION: not recorded on this deck")

    ov = ctx.get("overview") or {}
    if ov.get("how_it_works"):
        lines.append(f"HOW IT WORKS: {ov['how_it_works']}")

    for c in (ctx.get("curves") or [])[:5]:
        if not isinstance(c, dict):
            continue
        pts = c.get("points") or []
        lines.append(
            f"ODDS: {c.get('label')} — {pts[4] if len(pts) > 4 else 0}% by turn 5"
            + (f" ({c.get('note')})" if c.get("note") else ""))

    if ctx.get("signals"):
        lines.append("COUNTS: " + json.dumps(ctx["signals"]))

    if ctx.get("notes"):
        lines.append("BUILD NOTES:\n" + "\n".join(f"• {n}" for n in ctx["notes"]))

    if ctx.get("substitutions"):
        lines.append("SUBSTITUTIONS: " + json.dumps(ctx["substitutions"]))

    if ctx.get("pressure"):
        lines.append("PRESSURE TEST: " + json.dumps(ctx["pressure"]))
    else:
        lines.append("PRESSURE TEST: not recorded on this deck")

    lines.append(f"ASSUMPTIONS: {sim.ASSUMPTIONS}")

    # Role and why per card. The why clause is the recorded reason — do not
    # drop it here and force the model to reconstruct.
    deck_lines = []
    for c in ctx.get("deck") or []:
        bit = f"{c['name']} [{c.get('role') or ''}]"
        if c.get("why"):
            bit += f" — why: {c['why']}"
        deck_lines.append(bit)
    lines.append("DECKLIST:\n" + "\n".join(deck_lines))
    return "\n".join(lines)


def deck_record_for_ask(payload: dict,
                        directory: pathlib.Path | None = None) -> dict:
    """Resolve the durable record /api/ask should reason from.

    Prefer the saved file when an id is given — that is the memory. Fall back
    to the client-held build payload (same shape) for a deck that has not been
    reloaded from disk.
    """
    slug = (payload.get("id") or "").strip()
    data = load_saved_deck(slug, directory) if slug else None
    if data is None:
        return {
            "commander": payload.get("commander"),
            "deck": payload.get("deck") or [],
            "brief": payload.get("brief") or {},
            "overview": payload.get("overview") or {},
            "signals": payload.get("signals"),
            "notes": payload.get("notes"),
            "substitutions": payload.get("substitutions"),
            "conversation": payload.get("conversation"),
            "pressure": payload.get("pressure") or payload.get("pressure_test"),
            "curves": payload.get("curves"),
            "simulation": payload.get("simulation"),
        }
    # Live curves from the open page (slider run) overlay the stored baseline.
    if payload.get("curves"):
        data = {**data, "curves": payload["curves"]}
    # A build saved by an older version has no transcript on disk; the client
    # may still hold her turns from this session.
    if not data.get("conversation") and payload.get("conversation"):
        data = {**data, "conversation": payload["conversation"]}
    if not data.get("pressure") and not data.get("pressure_test"):
        pressure = payload.get("pressure") or payload.get("pressure_test")
        if pressure:
            data = {**data, "pressure": pressure}
    return data


DECKTALK = """You are talking to someone about the Commander deck sitting on screen in
front of her. She built it a minute ago and has a question, or wants something changed.

You are given one assembled record for this deck: the plan, her interrogation, the
simulation numbers, build notes, and the decklist. Answer from that record. Never name a
card that is not in the list — if the answer is "this deck doesn't have one of those", that
is a real and useful answer, and a made-up card name is the one thing that would make her
stop trusting any of this.

## Improve questions use the instrument

When she asks how to improve the deck, use WAY PREFERENCE, INSTRUMENT counts,
GLUED PAYOFFS, and IMPROVE CANDIDATES from the fact block. Prefer improve
candidates with edges. Never invent a card name that is not in DECKLIST or
IMPROVE CANDIDATES. Instrument counts that are unknown mean "could not look,"
not zero. An unmapped way preference keeps her words — do not invent a playstyle.

## Recorded reasons outrank anything you compose

Each DECKLIST line may carry a `why:` clause. That is the Scryfall query that found the
card, read back in English. If it is present, quote it. Do not replace it with a
reconstructed reason that sounds better — the query that found the card cannot be wrong
about what happened.

## What is not in the record is not known

If the fact block says a field was not recorded (interrogation, pressure test, notes), say
so. Do not invent what she asked for, or what a pressure test would have shown, to fill the
gap. When she asks what she wanted and the INTERROGATION block is present, quote her own
words.

Two kinds of thing she might say, and you handle both:

**A question** — "why is there no counterspell?", "why did you pick Carrion Feeder?",
"what did I say I wanted?", "what does 65% actually mean?", "is this fast?", "what beats
it?". Answer it plainly, in two or three sentences, using her deck's own numbers and cards.
Explain a term the first time you use it. If the honest answer is that the simulation
cannot tell her — it has no opponent and models no removal — say so rather than guessing.

**A change** — "more removal", "make it cheaper", "I don't like the black cards". Say in one
line what you would change and why, then end with a fenced json block so the deck can be
rebuilt:

```brief
{"theme": "...", "angle": "...", "strategy": "...", "budget_total": 150,
 "commander_query": "unchanged unless she asked for a different commander",
 "flavor_terms": ["..."]}
```

Only emit the block for a change. A question gets an answer and no block — rebuilding a deck
because she asked what a number meant would be maddening.

Never condescend, never pad, and never claim the deck is better than the numbers say."""


@app.post("/api/ask")
async def ask(payload: dict):
    """Questions and change requests about a finished deck.

    The end of the flow used to be a rebuild box. Everything she might actually
    want to say at that moment — why is this card here, what does 65% mean, is
    this fast — had nowhere to go, and those are the questions someone new
    genuinely has once a deck exists.

    facts come from deck_context — role and why per card, her
    interrogation, notes, substitutions, pressure test when present. A
    reconstructed reason where a recorded one exists is the failure mode.

    rebuild live instrument + glued improve candidates each ask —
    never freeze Scryfall counts onto the save.
    """
    data = deck_record_for_ask(payload)
    if not data.get("deck"):
        return JSONResponse({"error": "deck required"}, status_code=400)
    # Prefer client-held role_pools when the saved file is thin (old saves).
    if not data.get("role_pools") and isinstance(payload.get("role_pools"), dict):
        data = {**data, "role_pools": payload["role_pools"]}
    if not data.get("commander_card") and isinstance(
            payload.get("commander_card"), dict):
        data = {**data, "commander_card": payload["commander_card"]}
    ctx = await enrich_ask_context(data)
    facts = ask_prompt_facts(ctx)

    # If she named a card pool — "only Spider-Man cards" — hand over the counted
    # facts before answering. Without them the model speculates about a pool it
    # was never given, and it has done that in both directions: once promising a
    # Spider-Man tribal deck that cannot exist, and once refusing on the grounds
    # that Scryfall cannot filter by set, which it can.
    #
    # Strictly additive, and the whole block is guarded — including the string
    # building. A first version guarded only the lookup and then called .append
    # on something that was never a list, which took the ask box down with a
    # 500. An optional extra must not be able to break the answer.
    cov = None          # bound before the try: the return reads it either way
    try:
        # The full deck, not the context projection — deck_context() reduces
        # each card to name and role, so colour identity read off it is empty
        # and the pool comes back unfiltered. That reported 175 of 175 legal in
        # a two-colour deck, and the answer built on it was confidently wrong.
        cov = await compute_coverage((payload.get("text") or "").strip(),
                                     data.get("deck") or [], ctx.get("brief") or {})
        if cov:
            deltas = " · ".join(
                f"{r}: {v['now']} now, {v['available']} available in the set"
                for r, v in (cov.get("role_deltas") or {}).items())
            facts = facts + (
                "\n\nCARD POOL SHE NAMED — counted, not estimated. Use these figures:\n"
                f"  set: {cov['set']['name']} ({cov['set']['code']})\n"
                f"  cards in that set we can search: {cov['pool_total']}\n"
                f"  legal in this deck's colours ({cov['identity']}): {cov['in_identity']}\n"
                f"  this deck is {cov['deck_size']} cards; {cov['already_from_pool']} already come from that set\n"
                f"  {cov['land_slots']} of those are lands and stay as they are\n"
                f"  the deck has {cov['spell_slots']} spell slots, so at most"
                f" {cov['from_pool_at_most']} of them could come from the set\n"
                "  Do NOT predict how many actually will. The build falls back to\n"
                "  her colours wherever the set runs thin, and that is only known\n"
                "  once it runs. Describe the trade by role instead, and say the\n"
                "  real split is reported after the rebuild.\n"
                f"  spell roles — {deltas or 'no change'}\n"
                "  (the mana base is not part of this trade: lands come from the\n"
                "   deck's own curve and basics are unlimited)\n"
                f"  {cov['basics_note']}\n"
                "This IS something the app can do. Say what the trade is in those\n"
                "numbers and offer to rebuild it that way. Do not call it\n"
                "impossible and do not estimate any of these figures.")
    except Exception as e:
        print(f"  [ask] coverage skipped: {e}")

    msgs = (payload.get("history") or []) + [
        {"role": "user", "content": facts + "\n\nSHE SAYS: "
         + (payload.get("text") or "")}]
    r = await client.messages.create(model=MODEL, max_tokens=1400,
                                     thinking={"type": "disabled"},
                                     system=DECKTALK, messages=msgs)
    raw = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
    # Same three states as /api/chat. A change request with a broken
    # ```brief fence used to become brief=null with no reason — silent rebuild miss.
    got = extract_brief_payload(
        raw, stop_reason=getattr(r, "stop_reason", None), what="ask")
    out = {
        "text": got["text"],
        "brief": got["brief"],
        "still_asking": got["still_asking"],
        "brief_error": got["brief_error"],
        # live improve surface for the page — never frozen on disk.
        "instrument": ctx.get("instrument"),
        "improve_candidates": ctx.get("improve_candidates") or [],
        "glued_payoffs": ctx.get("glued_payoffs") or [],
        "way_preference": (ctx.get("brief") or {}).get("way_preference") or "",
        "way_playstyle": (ctx.get("brief") or {}).get("way_playstyle"),
    }
    # Hand the coverage back so the page can offer the rebuild as a button
    # rather than a sentence. The numbers were already stated in the reply;
    # this is the same decision made pressable.
    if cov:
        out["coverage"] = {
            "set": cov["set"], "in_identity": cov["in_identity"],
            "at_most": cov["from_pool_at_most"],
            "spell_slots": cov["spell_slots"], "land_slots": cov["land_slots"],
            "already": cov["already_from_pool"], "deck_size": cov["deck_size"],
        }
    return out


def resolve_thesis_plan(
    payload: dict,
    brief: dict,
    cmdr_card: dict | None,
) -> dict | None:
    """Plan that built the 99, when available — never invent one from silence.

    Prefer the plan stamped on the build. Else derive from brief + commander
    (same helper build used). Missing commander or derive failure → None so
    the model thesis remains the fallback for imports / older saves.
    """
    plan = payload.get("plan")
    if isinstance(plan, dict) and (plan.get("conditions") or []):
        return plan
    if not cmdr_card:
        return None
    try:
        return plan_graph.derive(brief or {}, cmdr_card)
    except Exception:
        return None


@app.post("/api/thesis")
async def thesis(payload: dict):
    deck, brief, cmdr = payload["deck"], payload["brief"], payload["commander"]
    pick = payload.get("pick") or {}
    if pick.get("strategy"):
        brief = {**brief, "strategy": pick["strategy"],
                 "plan": pick.get("does") or brief.get("angle"),
                 "fits": pick.get("fits") or "",
                 "mechanics": pick.get("mechanics") or []}
    cmdr_card = payload.get("commander_card")
    if not isinstance(cmdr_card, dict) or not cmdr_card.get("name"):
        cmdr_card = None
        for c in deck:
            if (c.get("name") or "") == cmdr:
                cmdr_card = c
                break
    if cmdr_card is None:
        # Named lookup keeps derive()/glue honest when the commander is not
        # duplicated inside the 99 (normal build shape).
        try:
            info = await scry("/cards/named", exact=cmdr)
            if isinstance(info, dict) and info.get("name"):
                cmdr_card = trim_card(info)
        except Exception:
            cmdr_card = None

    # The plan already says what must be true, in THESIS_KEYS. Goldfish that
    # plan against the 99 instead of asking the model to invent a second one.
    # Any plan shape — lifegain, tokens, attack, draw — same path.
    plan = resolve_thesis_plan(payload, brief, cmdr_card)
    from_plan = plan_graph.thesis_from_plan(
        plan, deck, cmdr_card or {"name": cmdr})
    if from_plan is not None:
        return from_plan

    user_msg = build_thesis_user_message(
        deck, brief, cmdr, commander_card=cmdr_card)

    r = await client.messages.create(
        # payoff_cards (8-20 names) + 4 conditions with labels and notes.
        # A truncated response returns "Could not parse thesis" and the deck
        # ships with no simulation at all.
        model=MODEL, max_tokens=3000, system=THESIS, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": user_msg}])
    raw = "".join(b.text for b in r.content if b.type == "text")
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Could not parse thesis."}

    # verifier, same as everywhere else: only real cards, only real state keys
    names = {c["name"] for c in deck}
    spec["payoff_cards"] = [n for n in spec.get("payoff_cards", []) if n in names]
    spec["conditions"] = [c for c in spec.get("conditions", [])
                          if c.get("check") and all(k in sim.THESIS_KEYS for k in c["check"])]
    # Diagnostics — never softens the verifier. Coverage is a COUNT fraction.
    glued = synergy.glued_payoff_candidates(deck, cmdr_card or {"name": cmdr},
                                            cap=30)
    spec["source"] = "model"
    spec["glued_payoff_candidates"] = glued.get("names") or []
    spec["glued_payoff_status"] = glued.get("status")
    spec["glued_payoff_coverage"] = synergy.payoff_edge_coverage(
        spec["payoff_cards"], deck, cmdr_card or {"name": cmdr})
    return spec


# ---------------------------------------------------------------- pressure test
#
# Forge AI vs AI, opt-in from the deck page. Never on the build path. Absent
# Java or Forge, both endpoints return available:false and nothing else changes.


@app.get("/api/pressure")
async def pressure_available():
    """Can this machine play real games? Asked before the invitation is drawn.

    Without this the page had to offer a button and then find out — so pressing
    it made the whole panel vanish, which reads as the app breaking rather than
    a feature being absent. Cheap: two path lookups, no subprocess.
    """
    return pressure.detect_engine()


@app.post("/api/pressure")
async def pressure_start(payload: dict):
    """Start a Forge pressure run. Returns {run_id, available} immediately.

    Body: {deck, commander, games?: 10}. Ten games take minutes — poll GET.
    """
    engine = pressure.detect_engine()
    if not engine.get("available"):
        return {
            "available": False,
            "reason": engine["reason"],
            "detail": engine["detail"],
        }

    deck = payload.get("deck") or []
    commander = (payload.get("commander") or "").strip()
    if not deck or not commander:
        return JSONResponse({"error": "deck and commander required"}, status_code=400)

    games = int(payload.get("games") or pressure.DEFAULT_GAMES)
    if games < 1 or games > 10:
        # Cap at ten games until wall-clock time is measured.
        games = min(max(games, 1), 10)

    return pressure.STORE.start(commander=commander, deck=deck, games=games)


@app.get("/api/pressure/{run_id}")
async def pressure_status(run_id: str):
    """Poll a pressure run. Finished games appear as they land."""
    engine = pressure.detect_engine()
    if not engine.get("available"):
        return {
            "available": False,
            "reason": engine["reason"],
            "detail": engine["detail"],
        }

    view = pressure.STORE.get(run_id)
    if view is None:
        return JSONResponse({"error": "unknown run_id"}, status_code=404)
    return view


@app.post("/api/simulate")
async def simulate(payload: dict):
    deck = payload["deck"]
    tweak = payload.get("tweak") or {}
    # pools the build already fetched. Client passes them back so a
    # slider move swaps real cards. Absent pools + non-zero tweak refuses to
    # invent stand-ins — see tweak.apply_tweak.
    role_pools = payload.get("role_pools") or payload.get("pools") or {}

    tweak_result = None
    if tweak:
        tweak_result = apply_tweak(deck, tweak, role_pools)
        deck = tweak_result["deck"]

    info = await scry("/cards/named", exact=payload["commander"])
    out = await asyncio.to_thread(
        sim.run, deck, int(info.get("cmc") or 5),
        payload["payoff_cards"], payload["conditions"], 3000,
        commander_card=info)  # on-cast commander oracle

    # Three model layers stack here — the thesis decides what "working"
    # means, the simulation measures exactly that, the overview explains it —
    # and nothing checked the first. A thesis grading the wrong plan produces
    # numbers that are correct about the wrong question, which reads identically
    # to a good answer. This says so rather than letting it pass as fact.
    out["review"] = thesis_check.review(
        {"payoff_cards": payload.get("payoff_cards"),
         "conditions": payload.get("conditions")},
        out.get("curves") or [], deck,
        commander_card=info)

    # Composition counts — always. Post-tweak this is the rebuild target for
    # Composition tweak; without a tweak it is the deck as built.
    out["composition"] = (
        tweak_result["composition"] if tweak_result
        else tweak_mod.composition_of(deck))
    if tweak_result is not None:
        out["tweak"] = {
            "requested": {k: int(v) for k, v in tweak.items()},
            "applied": tweak_result["applied"],
            "blocked": tweak_result["blocked"],
            "ok": tweak_result["ok"],
            "axes": list(tweak_mod.COMPOSITION_AXES),
        }
        # The list after honest swaps — UI can show what changed without
        # inventing names client-side.
        out["deck"] = deck

    # persist the untweaked baseline on the saved deck. Sliders pass
    # tweak and must keep recomputing — never overwrite the stored baseline
    # with a sensitivity run.
    if not tweak and payload.get("id"):
        store_sim_baseline(
            payload["id"],
            {"payoff_cards": payload.get("payoff_cards"),
             "conditions": payload.get("conditions")},
            out)
    return out


def apply_tweak(deck: list, tweak: dict, role_pools: dict | None = None) -> dict:
    """swap real pool cards by composition axis. See tweak.apply_tweak."""
    return tweak_mod.apply_tweak(deck, tweak, role_pools)


# ---------------------------------------------------------------- card swap
#
# Replace one card with a verified pool alternate. Design decision:
# every swap re-runs the simulation automatically — stale numbers are worse
# than none. Overview / per-card rationale go stale and are marked, not left
# describing a deck that changed.


def _ci_from_saved(data: dict) -> str:
    brief = data.get("brief") or {}
    colors = brief.get("colors") or ""
    if colors:
        return "".join(c for c in colors.upper() if c in "WUBRG")
    # Prefer the persisted union; fall back to merging both cards.
    if data.get("color_identity"):
        return identity_string(list(data["color_identity"]))
    zone = data.get("commander_cards") or []
    if not zone:
        zone = resolve_commanders(data.get("commander_card") or {},
                                  data.get("partner_card"))
    if zone:
        return identity_string(union_color_identity(*zone))
    cmdr = data.get("commander_card") or {}
    return "".join(cmdr.get("color_identity") or [])


def perform_swap(data: dict, remove: str, add: str, *,
                 payoff_cards: list | None = None,
                 conditions: list | None = None,
                 runs: int = 3000,
                 directory: pathlib.Path | None = None) -> dict:
    """Apply a verified swap, re-sim, mark prose stale, and persist if saved.

    `data` is a build/saved-deck dict (must carry deck + role_pools). Mutates
    and writes back when data has an id on disk. Returns the client payload.
    """
    deck = list(data.get("deck") or [])
    role_pools = data.get("role_pools") or {}
    ci = _ci_from_saved(data)
    problem = swap.swap_problem(deck, remove, add, role_pools, ci=ci)
    if problem:
        return {"ok": False, "error": problem}
    try:
        result = swap.apply_swap(deck, remove, add, role_pools, ci=ci)
    except swap.SwapError as e:
        errors.log_exception("swap", e)
        return {"ok": False, "error": swap.REFUSED}

    new_deck = result["deck"]
    data["deck"] = new_deck
    data["price"] = round(sum(float(c["usd"]) for c in new_deck if c.get("usd")), 2)
    data["substitutions"] = substitution.build_substitutions(new_deck, role_pools, ci)
    # Prose about the old list must not keep speaking as fact.
    data["overview"] = None
    data["overview_stale"] = True

    cmdr_cmc = int((data.get("commander_card") or {}).get("cmc") or 5)
    thesis_block = (data.get("simulation") or {}).get("thesis") or {}
    payoffs = list(payoff_cards if payoff_cards is not None
                   else thesis_block.get("payoff_cards") or [])
    conds = list(conditions if conditions is not None
                 else thesis_block.get("conditions") or [])
    # Drop payoffs that left the list with the removed card.
    names = {c["name"] for c in new_deck}
    payoffs = [n for n in payoffs if n in names]
    if conds:
        sim_out = sim.run(
            new_deck, cmdr_cmc, payoffs, conds, runs,
            commander_card=data.get("commander_card"))
        sim_out["review"] = thesis_check.review(
            {"payoff_cards": payoffs, "conditions": conds},
            sim_out.get("curves") or [], new_deck,
            commander_card=data.get("commander_card"))
        attach_sim_baseline(data, {"payoff_cards": payoffs, "conditions": conds},
                            sim_out)
    else:
        # No thesis yet — still refresh diagnostics so the rail isn't stale.
        cards = sim.prep(new_deck, set())
        sim_out = {"runs": 0, "curves": [], "diagnostics": sim.diagnose(cards),
                   "assumptions": sim.ASSUMPTIONS, "review": {}}
        data.pop("simulation", None)

    mass = tcgplayer_mass_entry(data.get("commander") or "", new_deck)
    data["tcgplayer"] = mass
    data["archidekt"] = (f"1 {data.get('commander')}\n"
                         + "\n".join(f"1 {c['name']}" for c in new_deck))

    slug = data.get("id")
    if slug and saved_deck_path(slug, directory):
        write_saved_deck(slug, data, directory)

    out = {
        "ok": True,
        "removed": result["removed"],
        "added": result["added"],
        "role": result["role"],
        "deck": new_deck,
        "price": data["price"],
        "substitutions": data["substitutions"],
        "overview": None,
        "overview_stale": True,
        "diagnostics": sim_out.get("diagnostics") or {},
        "curves": sim_out.get("curves") or [],
        "runs": sim_out.get("runs", 0),
        "assumptions": sim_out.get("assumptions") or sim.ASSUMPTIONS,
        "review": sim_out.get("review") or {},
        "tcgplayer": data.get("tcgplayer"),
        "archidekt": data.get("archidekt"),
        "id": slug,
    }
    if sim_out.get("good_hand") is not None:
        out["good_hand"] = sim_out["good_hand"]
    if sim_out.get("bad_hand") is not None:
        out["bad_hand"] = sim_out["bad_hand"]
    if data.get("simulation"):
        out["simulation"] = data["simulation"]
    return out


@app.post("/api/swap")
async def swap_card(payload: dict):
    """Replace one card with a verified pool alternate; re-sim automatically.

    Body: remove, add, and either `id` (saved deck) or an inline `deck` +
    `role_pools`. Optional payoff_cards / conditions reuse the thesis; when
    omitted, a saved deck's stored thesis is used.
    """
    remove = (payload.get("remove") or "").strip()
    add = (payload.get("add") or "").strip()
    if not remove or not add:
        return JSONResponse({"ok": False, "error": "remove and add are required"},
                            status_code=400)

    slug = payload.get("id")
    data = None
    if slug:
        data = load_saved_deck(slug)
        if not data:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        # Allow the client to pass fresher pools (e.g. just after build).
        if payload.get("role_pools"):
            data["role_pools"] = payload["role_pools"]
        if payload.get("deck"):
            data["deck"] = payload["deck"]
    else:
        deck = payload.get("deck")
        role_pools = payload.get("role_pools")
        if not deck or not role_pools:
            return JSONResponse(
                {"ok": False,
                 "error": "deck and role_pools required when id is omitted"},
                status_code=400)
        data = {
            "deck": deck,
            "role_pools": role_pools,
            "commander": payload.get("commander") or "",
            "commander_card": payload.get("commander_card") or {},
            "brief": payload.get("brief") or {},
            "simulation": payload.get("simulation"),
        }

    if not data.get("role_pools"):
        return JSONResponse(
            {"ok": False,
             "error": "no role_pools on this deck — rebuild to enable swaps"},
            status_code=400)

    result = await asyncio.to_thread(
        perform_swap, data, remove, add,
        payoff_cards=payload.get("payoff_cards"),
        conditions=payload.get("conditions"),
        runs=int(payload.get("runs") or 3000))
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


# ---------------------------------------------------------------- backend spine
# notes · suggest · pending versions · precon delta · search · editor


@app.get("/api/mechanics/related/{name}")
async def mechanics_related(name: str):
    """Lineage + family relations for one mechanic — plan math substrate."""
    return mechanics.related(name)


@app.get("/api/goals")
async def list_goals():
    """Goal ids the suggest endpoint accepts, with expanded family seeds."""
    out = []
    for gid in sorted(suggest.GOALS):
        spec = suggest.goal_spec(gid)
        if spec:
            out.append(spec)
    return {"goals": out}


@app.post("/api/suggest")
async def suggest_swaps(payload: dict):
    """goal-scoped swap plan. Stages a change set; does not apply."""
    slug = (payload.get("id") or "").strip()
    goal = (payload.get("goal") or "").strip()
    if not slug or not goal:
        return JSONResponse({"ok": False, "error": "id and goal are required"},
                            status_code=400)
    data = load_saved_deck(slug)
    if not data:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    count = int(payload.get("count") or 5)
    plan = suggest.plan_swaps(data, goal, count=max(1, min(count, 8)))
    if not plan.get("ok"):
        return JSONResponse(plan, status_code=400)
    staged = deck_state.stage_pending(
        data, plan["suggestions"], source="suggest", goal=goal)
    write_saved_deck(slug, data)
    set_id = staged[0]["set_id"] if staged else None
    return {
        "ok": True,
        "id": slug,
        "goal": plan["goal"],
        "suggestions": plan["suggestions"],
        "pending": staged,
        "set_id": set_id,
        "version": deck_state.version_summary(data),
    }


@app.get("/api/saved/{name}/notes")
async def get_match_notes(name: str):
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": name, "match_notes": deck_state.list_match_notes(data)}


@app.post("/api/saved/{name}/notes")
async def post_match_note(name: str, payload: dict):
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    note = deck_state.add_match_note(data, payload or {})
    write_saved_deck(name, data)
    return {"ok": True, "note": note, "match_notes": deck_state.list_match_notes(data)}


@app.delete("/api/saved/{name}/notes/{note_id}")
async def remove_match_note(name: str, note_id: str):
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not deck_state.delete_match_note(data, note_id):
        return JSONResponse({"error": "note not found"}, status_code=404)
    write_saved_deck(name, data)
    return {"ok": True, "match_notes": deck_state.list_match_notes(data)}


@app.get("/api/saved/{name}/versions")
async def get_versions(name: str):
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": name, **deck_state.version_summary(data)}


@app.post("/api/saved/{name}/pending/reject")
async def reject_pending(name: str, payload: dict | None = None):
    """drop a pending change set. Never mutates the 99 or version."""
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    payload = payload or {}
    n = deck_state.clear_pending(
        data,
        pending_id=payload.get("pending_id"),
        set_id=payload.get("set_id"),
        source=payload.get("source"),
    )
    write_saved_deck(name, data)
    return {"ok": True, "cleared": n, **deck_state.version_summary(data)}


@app.post("/api/saved/{name}/pending/approve")
async def approve_pending_changes(name: str, payload: dict | None = None):
    """apply a pending set → new version. Old tip in history."""
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    payload = payload or {}
    ids = payload.get("pending_ids")
    ci = _ci_from_saved(data)
    result = deck_state.approve_pending(
        data,
        pending_ids=ids,
        set_id=payload.get("set_id"),
        source=payload.get("source"),
        ci=ci,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)

    # Bookkeeping mirrors perform_swap after the list moves.
    new_deck = data["deck"]
    role_pools = data.get("role_pools") or {}
    data["price"] = round(sum(float(c["usd"]) for c in new_deck if c.get("usd")), 2)
    if role_pools:
        data["substitutions"] = substitution.build_substitutions(
            new_deck, role_pools, ci)
    data["overview"] = None
    data["overview_stale"] = True
    data.pop("simulation", None)  # list changed — baseline must re-run
    mass = tcgplayer_mass_entry(data.get("commander") or "", new_deck)
    data["tcgplayer"] = mass
    data["archidekt"] = (f"1 {data.get('commander')}\n"
                         + "\n".join(f"1 {c['name']}" for c in new_deck))

    # Legality after approve — report, don't silently ship illegal.
    cmdr = data.get("commander_card") or {}
    partner = data.get("partner_card")
    report = validate_deck(cmdr, new_deck, partner=partner,
                           card_pool=data.get("card_pool") or DEFAULT_CARD_POOL)
    data["legal"] = report
    write_saved_deck(name, data)
    return {
        **result,
        "id": name,
        "legal": report,
        "price": data["price"],
        "mechanics": data.get("mechanics"),
        **deck_state.version_summary(data),
    }


@app.get("/api/search")
async def api_search(q: str = "", limit: int = 20):
    """paced Scryfall card search. No model.

    404 → real zero. Any other failure → 502 unknown — never score as 0 cards.
    """
    q = (q or "").strip()
    if not q:
        return {"query": q, "cards": [], "total": 0}
    limit = max(1, min(int(limit or 20), 60))
    try:
        j = await scry("/cards/search", q=q, order="edhrec", unique="cards")
    except ScryfallError as e:
        # named refusal (challenge / rate_limit / timeout / …).
        return JSONResponse(
            scryfall_refusal_payload(e),
            status_code=_http_status_for_refusal(e.kind))
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return {"query": q, "cards": [], "total": 0}
        status = e.response.status_code if e.response is not None else 0
        wrapped = ScryfallError(status or 0, "HTTPStatusError", kind="unreachable")
        return JSONResponse(
            scryfall_refusal_payload(wrapped),
            status_code=_http_status_for_refusal(wrapped.kind))
    except Exception as e:
        wrapped = ScryfallError(0, f"{type(e).__name__}: {e}", kind="transport")
        return JSONResponse(
            scryfall_refusal_payload(wrapped),
            status_code=_http_status_for_refusal(wrapped.kind))
    # scry() maps 404 to {"data": []} — treat empty data with no total as zero.
    cards = [trim_card(c) for c in (j.get("data") or [])[:limit]]
    return {"query": q, "cards": cards,
            "total": int(j.get("total_cards") or len(cards))}


@app.post("/api/saved/{name}/edit")
async def edit_saved_deck(name: str, payload: dict):
    """stage a Scryfall-verified cut/add into pending (does not apply)."""
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    cut = (payload.get("remove") or payload.get("cut") or "").strip()
    add_name = (payload.get("add") or "").strip()
    if not cut or not add_name:
        return JSONResponse({"error": "remove and add are required"}, status_code=400)
    # Resolve add against Scryfall — never trust a bare client name.
    try:
        raw = await scry_post("/cards/collection",
                              {"identifiers": [{"name": add_name}]})
    except ScryfallError as e:
        return JSONResponse(
            scryfall_refusal_payload(e),
            status_code=_http_status_for_refusal(e.kind))
    except Exception as e:
        wrapped = ScryfallError(0, f"{type(e).__name__}: {e}", kind="transport")
        return JSONResponse(
            scryfall_refusal_payload(wrapped),
            status_code=_http_status_for_refusal(wrapped.kind))
    found = [trim_card(c) for c in (raw.get("data") or [])]
    not_found = raw.get("not_found") or []
    if not found or not_found:
        return JSONResponse(
            {"ok": False, "error": f"Scryfall has no card named {add_name!r}"},
            status_code=404)
    add_card = found[0]
    # Dry-run legality on a hypothetical post-approve list.
    ci = _ci_from_saved(data)
    current = list(data.get("deck") or [])
    problem = swap.swap_card_problem(current, cut, add_card, ci=ci)
    if problem:
        return JSONResponse({"ok": False, "error": problem}, status_code=400)
    try:
        preview = swap.apply_swap_card(current, cut, add_card, ci=ci)
    except swap.SwapError as e:
        errors.log_exception("swap preview", e)
        return JSONResponse({"ok": False, "error": swap.REFUSED}, status_code=400)
    cmdr = data.get("commander_card") or {}
    report = validate_deck(cmdr, preview["deck"],
                           partner=data.get("partner_card"),
                           card_pool=data.get("card_pool") or DEFAULT_CARD_POOL)
    staged = deck_state.stage_pending(
        data,
        [{
            "cut": cut,
            "add": add_card["name"],
            "add_card": add_card,
            "reason": payload.get("reason") or f"Manual edit: {cut} → {add_card['name']}",
            "role": preview.get("role") or "",
        }],
        source="editor",
        goal=None,
        replace_source_goal=False,
    )
    write_saved_deck(name, data)
    return {
        "ok": True,
        "pending": staged,
        "preview_legal": report,
        **deck_state.version_summary(data),
    }


@app.post("/api/precons/{precon_id}/delta")
async def precon_strategy_delta(precon_id: str, payload: dict):
    """mechanic + diagnose delta for adds/cuts on a precon."""
    if not _PRECON_ID_RE.fullmatch(precon_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    baked = precon_bake.read_bake(precon_id)
    deck = list((baked or {}).get("deck") or [])
    cmc = int(((baked or {}).get("commander_card") or {}).get("cmc") or 5)
    if not deck:
        # Live Archidekt list when no bake ships for this id.
        try:
            live_text, _live_cmdr, _meta = await precons.decklist_for_precon(precon_id)
        except Exception:
            return JSONResponse({"error": "precon not found"}, status_code=404)
        resolved = await import_deck.resolve_decklist(
            live_text, scry_post, trim_card, _live_cmdr)
        if resolved.get("commander_pick", {}).get("status") != "found":
            return JSONResponse({"error": "precon not found"}, status_code=404)
        deck = resolved["deck"]
        cmc = int((resolved.get("commander_card") or {}).get("cmc") or 5)
    if not deck:
        return JSONResponse({"error": "precon not found"}, status_code=404)

    add_names = [n for n in (payload.get("adds") or []) if isinstance(n, str)][:15]
    cuts = [n for n in (payload.get("cuts") or []) if isinstance(n, str)][:15]
    add_cards = []
    if add_names:
        try:
            raw = await scry_post(
                "/cards/collection",
                {"identifiers": [{"name": n} for n in add_names]})
        except ScryfallError as e:
            return JSONResponse(
                scryfall_refusal_payload(e),
                status_code=_http_status_for_refusal(e.kind))
        except Exception as e:
            wrapped = ScryfallError(0, f"{type(e).__name__}: {e}", kind="transport")
            return JSONResponse(
                scryfall_refusal_payload(wrapped),
                status_code=_http_status_for_refusal(wrapped.kind))
        by_name = {c["name"]: trim_card(c) for c in (raw.get("data") or [])}
        for n in add_names:
            # Exact name match; fuzzy Scryfall renames still land under real name.
            card = by_name.get(n) or next(
                (c for c in by_name.values() if c["name"].lower() == n.lower()), None)
            if card:
                add_cards.append(card)
    delta = precon_delta.strategy_delta(
        deck, adds=add_cards, cuts=cuts, commander_cmc=cmc)
    delta["precon_id"] = precon_id
    return delta


@app.get("/favicon.ico")
async def favicon():
    """The only console error either page produced. A tiny inline SVG
    beats shipping a binary into a repo that has no other assets — and the mark
    is the same gold the header uses."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#12100D"/>'
        '<path d="M9 8h7a8 8 0 0 1 0 16H9z" fill="none" stroke="#C6A46A" '
        'stroke-width="3" stroke-linejoin="round"/></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


def _all_saved_decks() -> list[dict]:
    """Full saved-deck dicts for calendar overlay — skips cache files."""
    out: list[dict] = []
    for p in SAVE_DIR.glob("*.json"):
        if p.stem in _NOT_DECKS or p.name in ("vocab.json", "symbols.json", "precons.json"):
            continue
        data = load_saved_deck(p.stem)
        if data:
            out.append(data)
    return out


@app.get("/api/calendar")
async def calendar_feed(
    released_limit: int = release_calendar.RELEASED_PAGE_SIZE,
    released_offset: int = 0,
    counts: bool = False,
):
    """Release calendar first paint: Scryfall /sets + hand overlay.

    content is debut mechanics, not saved-deck queries. Default path
    does not block on mechanics enrich — cache hits return, misses are
    `pending` (never an invented empty list). Pass counts=1 (legacy name) to
    wait for mechanics on this request. Future sets stay unspoiled.
    """
    try:
        return await release_calendar.build_calendar(
            scry, None,
            released_limit=released_limit,
            released_offset=released_offset,
            fetch_mechanics=counts,
        )
    except release_calendar.CalendarUnavailable as e:
        errors.log_exception("calendar", e)
        return JSONResponse({"error": release_calendar.UNAVAILABLE_MESSAGE},
                            status_code=503)


@app.get("/api/calendar/enrich")
async def calendar_enrich(codes: str = ""):
    """Fill debut mechanics (and art) for released set codes.

    one paced card search per set for keywords, then debut checks
    against earliest print. Does not query saved decks. `codes` is
    comma-separated; empty means the current first page of released sets.
    Failures are unknown, never zero.
    """
    wanted = [c.strip().lower() for c in codes.split(",") if c.strip()]
    try:
        merged = await release_calendar.get_merged_sets(scry, force=False)
        by_code = {str(s.get("code") or "").lower(): s for s in merged}
        if not wanted:
            shell = await release_calendar.build_calendar(
                scry, None, fetch_mechanics=False)
            wanted = [r.get("code") or "" for r in shell.get("released") or []]
        rows = [by_code.get(c) or {"code": c, "released_at": ""} for c in wanted]
        filled = await release_calendar.enrich_released_mechanics(
            scry, rows, fetch_art=True)
    except release_calendar.CalendarUnavailable as e:
        errors.log_exception("calendar", e)
        return JSONResponse({"error": release_calendar.UNAVAILABLE_MESSAGE},
                            status_code=503)
    return {
        "codes": wanted,
        "sets": [
            {"code": code, **filled.get(
                code, {"art_url": None,
                       "mechanics": release_calendar.mechanics_payload(
                           status="unknown")})}
            for code in wanted
        ],
    }


@app.get("/api/saved")
async def saved():
    return list_saved_decks()


@app.get("/api/saved/{name}")
async def load(name: str):
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    # a stale baseline is omitted — presence means safe to show.
    return expose_saved_deck(data)


@app.put("/api/saved/{name}/simulation")
async def put_saved_simulation(name: str, payload: dict):
    """Store the baseline thesis + simulation on a saved deck.

    Body is the thesis spec plus the /api/simulate response (curves,
    diagnostics, hands, review). Sliders do not call this — only the
    deck's own untweaked baseline belongs here.
    """
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    thesis = payload.get("thesis") or {
        "payoff_cards": payload.get("payoff_cards") or [],
        "conditions": payload.get("conditions") or [],
    }
    if not thesis.get("conditions") and not thesis.get("payoff_cards"):
        return JSONResponse({"error": "thesis required"}, status_code=400)
    if not payload.get("curves"):
        return JSONResponse({"error": "curves required"}, status_code=400)
    attach_sim_baseline(data, thesis, payload)
    write_saved_deck(name, data)
    return expose_saved_deck(data)


@app.patch("/api/saved/{name}")
async def rename_saved(name: str, payload: dict):
    """Update what the player owns about a deck: its name, theme and notes.

    Only these three: name, theme, primer. The 99 are ours — they came from
    Scryfall and are checked against it — but what the deck is called and the
    story behind it are hers, and nothing else in this app may change them.

    The story is "primer", not "notes": the build already writes a `notes` list
    of its own diagnostics, and prose written into that key would overwrite real
    data and break every reader of the file.

    Every field is optional, so saving a name does not wipe a story written
    earlier, and an empty string is a deliberate clear rather than a no-op.
    """
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)
    touched = False
    # NOT "notes" — the build response already uses that key for its own
    # diagnostic warnings, as a list. Writing her story there would overwrite
    # real data with prose and break every reader of the saved file.
    for field in ("name", "theme", "primer"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str):
                return JSONResponse({"error": f"{field} must be text"}, status_code=400)
            data[field] = value.strip()
            touched = True
            # She typed it or accepted it, so it is hers from here on and the
            # suggester must never write over it again.
            if field == "name":
                data["named_by"] = "her"
    # Favouriting is how she finds one deck among a dozen. Hers, like the name.
    if "favorite" in payload:
        data["favorite"] = bool(payload["favorite"])
        touched = True
    if not touched:
        return JSONResponse({"error": "nothing to update"}, status_code=400)
    if not (data.get("name") or "").strip():
        data["name"] = data.get("commander") or "deck"
    write_saved_deck(name, data)
    return saved_deck_summary(data, saved_deck_path(name))


@app.post("/api/saved/{name}/suggest-name")
async def suggest_saved_name(name: str, payload: dict):
    """Write the christening's first suggestion onto a deck she has not named.

    Deliberately NOT part of PATCH /api/saved/{name}. That endpoint is the one
    place the player owns outright — "nothing else in this app may change
    them" — and the app writing a name through it would make that sentence
    false. This is the app naming its own auto-save, so it gets its own door
    and its own refusals, and it records `named_by: "auto"` so the difference
    survives on disk.

    Refuses whenever there is any sign she chose the name herself:

    * `named_by == "her"` — she typed or accepted one, even if she later
      cleared it back to the commander.
    * the stored name is not the commander — a name from before `named_by`
      existed. Older decks have no provenance recorded, so the only safe
      reading of "this is not the commander" is that she wrote it.

    Refusing is not an error. It returns `applied: false` with the reason, so
    the caller can carry on without treating a deck she already named as a
    failure.
    """
    data = load_saved_deck(name)
    if not data:
        return JSONResponse({"error": "not found"}, status_code=404)

    suggested = payload.get("name")
    if not isinstance(suggested, str) or not suggested.strip():
        return JSONResponse({"error": "name must be text"}, status_code=400)

    if data.get("named_by") == "her":
        return {"applied": False, "reason": "she named it", "name": data.get("name")}

    cmdr = (data.get("commander") or "").strip()
    stored = (data.get("name") or cmdr).strip()
    if cmdr and stored != cmdr:
        return {"applied": False, "reason": "already named", "name": stored}

    data["name"] = suggested.strip()
    data["named_by"] = "auto"
    write_saved_deck(name, data)
    return {"applied": True, "name": data["name"],
            **saved_deck_summary(data, saved_deck_path(name))}


# The opening screen's ambient art. Openers live here rather than in the page
# because the fetch does too — see below.
AMBIENT_OPENERS = [
    "t:dragon", "t:angel", "t:demon", "t:pirate", "t:vampire",
    "t:hydra", "t:sphinx", "t:elemental", "t:zombie", "t:phoenix",
]


@app.get("/api/ambient")
async def ambient_card():
    """A random commander with art, for the opening screen.

    This used to be a fetch straight from the browser to api.scryfall.com, and
    it was the only place in the app that talked to Scryfall without going
    through scry(). Two things followed from that, both real:

    1. No User-Agent and no rate gate. Docs are explicit that Scryfall
       wants a descriptive agent and under 10 req/sec, and that scry() handles
       both. A direct browser call honours neither.
    2. It depended on Scryfall's CORS headers. WebKit refused it outright —
       "Origin http://127.0.0.1:8000 is not allowed by
       Access-Control-Allow-Origin" — so on Safari and on every iPhone the call
       threw, and with it went the background art, the accent colours and the
       hero card, because all three hang off this one response.

    Same-origin through the server: no CORS, one rate gate, one User-Agent.

    A failure returns `card: null` with the reason rather than a 500. No art is
    the old behaviour and the page already handles it; a broken opening screen
    would be worse than a plain one.
    """
    query = random.choice(AMBIENT_OPENERS)
    try:
        card = await scry("/cards/random",
                          q=f"{query} is:commander has:art_crop -is:digital")
    except ScryfallError as e:
        # keep card:null honesty; name the refusal kind.
        return {"card": None, **scryfall_refusal_payload(e)}
    except Exception as e:
        return {"card": None, "error": f"{type(e).__name__}"}
    if not isinstance(card, dict) or not card.get("name"):
        return {"card": None, "error": "no card"}
    images = card.get("image_uris") or {}
    return {"card": {
        "name": card.get("name"),
        "artist": card.get("artist"),
        "color_identity": card.get("color_identity") or [],
        "scryfall_uri": card.get("scryfall_uri"),
        "image_uris": {k: images.get(k) for k in ("art_crop", "normal", "large")
                       if images.get(k)},
    }}


BUG_DIR = SAVE_DIR / "bugs"


@app.post("/api/bug")
async def report_bug(payload: dict):
    """Take a bug report from the footer and put it somewhere we will find it.

    Disk first, Linear second. The write to ~/.deckwright/bugs/ is
    the commitment — greppable, survives a restart, needs no credentials. The
    Linear card is enrichment that may fail (expired Keychain key, plan full,
    network down). If it fails, the file already has the report and records
    `linear_error`; `scripts/sync_bug_reports.py` files it later.

    The description is hers. Everything else is context the page collected on
    her behalf, because "it broke" plus the deck she was looking at is worth
    more than "it broke" and is not something anyone should have to type.

    Never silently accepted: if the write fails, this says so, because a
    feedback box that quietly drops feedback is worse than no box. A Linear
    miss still returns ok — the report landed.
    """
    note = str((payload or {}).get("note") or "").strip()
    if not note:
        return JSONResponse({"error": "say what happened"}, status_code=400)

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    record = {
        "id": stamp,
        "reported_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "note": note[:4000],
        # Whatever the page knew about where she was. Capped so a runaway
        # console cannot write an unbounded file.
        "context": {k: v for k, v in (payload.get("context") or {}).items()
                    if k in {"page", "url", "deck_id", "commander", "deck_name",
                             "viewport", "user_agent", "errors"}},
        "app_version": bug_report.app_version(),
        "status": "new",
    }
    try:
        BUG_DIR.mkdir(parents=True, exist_ok=True)
        path = BUG_DIR / f"{stamp}.json"
        path.write_text(json.dumps(record, indent=1) + "\n")
    except OSError as e:
        return JSONResponse(
            {"error": f"could not save it here: {type(e).__name__}"}, status_code=500)

    # Enrichment only. Never let a Linear failure flip the response — the file
    # is already the source of truth.
    linear = bug_report.file_report_to_linear(record, path)
    out = {"ok": True, "id": stamp}
    if linear.get("ok") and linear.get("linear"):
        out["linear"] = linear["linear"]
    elif linear.get("error"):
        # The full reason is stamped on the saved report on disk.
        out["linear_error"] = "Could not file the report in Linear; it is saved on disk."
    return out


@app.get("/api/bugs")
async def list_bugs():
    """Every report, newest first. For triage — the app never renders this."""
    if not BUG_DIR.exists():
        return {"bugs": [], "total": 0}
    out = []
    for p in sorted(BUG_DIR.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            # Unreadable is not absent — say the file is there and broken.
            out.append({"id": p.stem, "status": "unreadable", "note": None})
    return {"bugs": out, "total": len(out)}


@app.delete("/api/saved/{name}")
async def remove_saved(name: str):
    if not delete_saved_deck(name):
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


# The page is read from disk on every request so an edit is live immediately —
# but the browser was caching it, which meant shipped changes did not appear
# and looked like they had not been pushed. Local single-user app; there is no
# reason to cache anything here.
@app.get("/api/boot")
async def api_boot():
    """progressive boot status for the launch brand veil.

    includes `card_source` — whether live Scryfall answers. A
    challenge is a legitimate not-ready reason for building; local pages
    still open because `ready` stays true.
    """
    return boot.status()


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (pathlib.Path(__file__).resolve().parents[1] / "index.html").read_text()
    if instance.env_name() == "green":
        badge = (
            '<span class="env-badge" role="status">'
            "STAGING — do not buy this list</span>"
        )
        html = html.replace("<!-- DECKWRIGHT_ENV_BADGE -->", badge, 1)
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    # A key is only required when there is no `claude` CLI — llm.py prefers the
    # CLI path, which needs no key. This gate predates llm.py and refused to
    # start the configuration the app normally runs in.
    if not os.environ.get("ANTHROPIC_API_KEY") and not shutil.which("claude"):
        raise SystemExit("Set ANTHROPIC_API_KEY, or install the claude CLI.")
    instance.require_boot()
    # HOST=0.0.0.0 to reach it from another device on your network
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Deckwright -> http://{host}:{port}")
    print(f"  model calls -> {LLM_VIA}")
    if instance.env_name() == "green":
        print("  STAGING — do not buy this list\n")
    else:
        print()
    uvicorn.run(app, host=host, port=port)
