"""
corpus.py — local Scryfall card corpus.

Serve card lookups from ~/.deckwright bulk files first. The network is the
exception: otag: / art: (Tagger fields absent from bulk) and anything else we
cannot evaluate go through the live rate-gated client.

composite queries are split on AND-conjuncts. Local content clauses
(t:/o:/…) answer from disk; otag:/art: still need live. A partial local answer
is INCOMPLETE — unknown-not-complete, never a smaller HIT. Pure otag:/art:
(with only format/CI wrappers) stays UNSUPPORTED.

House rule: a query the index cannot answer is UNSUPPORTED, MISSING, or
INCOMPLETE — never an empty data list pretending Scryfall said "none."
HIT / EMPTY / UNSUPPORTED / MISSING stay distinct; INCOMPLETE is the fifth.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.request import Request, urlopen

from deckwright import instance

CACHE_DIR = Path.home() / ".deckwright"
ORACLE_JSONL = CACHE_DIR / "oracle_cards.jsonl"
ORACLE_META = CACHE_DIR / "oracle_cards.meta.json"
PRICES_JSON = CACHE_DIR / "prices.json"
PRICES_META = CACHE_DIR / "prices.meta.json"
CORPUS_META = CACHE_DIR / "corpus.meta.json"


def reload_paths() -> None:
    """Rebind bulk-file paths from DECKWRIGHT_CORPUS_DIR."""
    global CACHE_DIR, ORACLE_JSONL, ORACLE_META, PRICES_JSON, PRICES_META, CORPUS_META
    CACHE_DIR = instance.corpus_dir()
    ORACLE_JSONL = CACHE_DIR / "oracle_cards.jsonl"
    ORACLE_META = CACHE_DIR / "oracle_cards.meta.json"
    PRICES_JSON = CACHE_DIR / "prices.json"
    PRICES_META = CACHE_DIR / "prices.meta.json"
    CORPUS_META = CACHE_DIR / "corpus.meta.json"


reload_paths()

UA = "Deckwright/1.0 (local card corpus)"
BULK_INDEX = "https://api.scryfall.com/bulk-data"
PAGE_SIZE = 175

# Operators that are not in bulk data (Scryfall Tagger). Live only.
_LIVE_ONLY = re.compile(r"\b(?:otag|art)\s*:", re.I)

HIT = "hit"
EMPTY = "empty"
UNSUPPORTED = "unsupported"
MISSING = "missing"
# local AND-conjuncts answered; live-only conjuncts were not evaluated.
# Distinct from HIT (complete) and UNSUPPORTED (nothing local to serve).
INCOMPLETE = "incomplete"


@dataclass
class ServeResult:
    status: str
    payload: dict = field(default_factory=dict)
    detail: str = ""


@dataclass
class Meters:
    """Live-vs-local counters for one process (resettable per build)."""

    local: int = 0
    live_needed: int = 0
    live_done: int = 0
    missing: int = 0

    def reset(self) -> None:
        self.local = self.live_needed = self.live_done = self.missing = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "local": self.local,
            "live_needed": self.live_needed,
            "live_done": self.live_done,
            "missing": self.missing,
        }


_meters = Meters()


def meters() -> Meters:
    return _meters


def needs_live(q: str) -> bool:
    """True when the query uses operators absent from bulk data."""
    return bool(_LIVE_ONLY.search(q or ""))


def _flatten_and(node: Any) -> list[Any]:
    """Top-level AND conjuncts — `(A B C)` → [A, B, C]."""
    if node and node[0] == "AND":
        return _flatten_and(node[1]) + _flatten_and(node[2])
    return [node]


def _node_needs_live(node: Any) -> bool:
    kind = node[0]
    if kind == "CLAUSE":
        return str(node[1]).lower() in ("otag", "art")
    if kind == "NOT":
        return _node_needs_live(node[1])
    if kind in ("OR", "AND"):
        return _node_needs_live(node[1]) or _node_needs_live(node[2])
    if kind == "TRUE":
        return False
    return False


# Format / CI / price / cmc wrappers that every role rung carries. Peeling only
# these next to otag: would serve nearly the whole corpus as "local" — that is
# a smaller-answer lie. Content clauses (t:/o:/keyword:/…) justify INCOMPLETE.
_FILTER_FIELDS = frozenset({
    "f", "format",
    "id", "ci", "identity", "coloridentity", "commanderidentity",
    "usd", "cmc", "mv", "manavalue",
})
_FILTER_IS_VALUES = frozenset({
    "gamechanger", "reprint", "digital", "reserved",
})


def _node_is_filter_only(node: Any) -> bool:
    """True when a local conjunct is only a pool wrapper, not theme content."""
    kind = node[0]
    if kind == "CLAUSE":
        field = str(node[1]).lower()
        if field in _FILTER_FIELDS:
            return True
        if field == "is" and str(node[3]).lower() in _FILTER_IS_VALUES:
            return True
        return False
    if kind == "NOT":
        return _node_is_filter_only(node[1])
    if kind in ("OR", "AND"):
        return _node_is_filter_only(node[1]) and _node_is_filter_only(node[2])
    if kind == "TRUE":
        return True
    return False


def _quote_val(val: str) -> str:
    if not val:
        return val
    if any(ch.isspace() for ch in val) or any(ch in val for ch in "():"):
        # Prefer double quotes; escape any embedded ones.
        esc = val.replace('"', '\\"')
        return f'"{esc}"'
    return val


def _render_node(node: Any, *, parent: str | None = None) -> str:
    """Render an AST node back to a Scryfall query fragment."""
    kind = node[0]
    if kind == "TRUE":
        return ""
    if kind == "CLAUSE":
        field, op, val = node[1], node[2], node[3]
        if op == "!":
            return f'!{_quote_val(val)}'
        return f"{field}{op}{_quote_val(val)}"
    if kind == "NOT":
        inner = _render_node(node[1], parent="NOT")
        # Unary minus binds tight; parenthesize OR/AND children.
        if node[1][0] in ("OR", "AND"):
            return f"-({inner})"
        return f"-{inner}"
    if kind == "OR":
        left = _render_node(node[1], parent="OR")
        right = _render_node(node[2], parent="OR")
        body = f"{left} OR {right}"
        if parent in ("AND", "NOT", "OR"):
            return f"({body})"
        return body
    if kind == "AND":
        left = _render_node(node[1], parent="AND")
        right = _render_node(node[2], parent="AND")
        body = f"{left} {right}".strip()
        if parent == "OR":
            return f"({body})"
        return body
    raise ValueError(f"cannot render {kind}")


def _and_nodes(nodes: list[Any]) -> Any | None:
    if not nodes:
        return None
    out = nodes[0]
    for n in nodes[1:]:
        out = ("AND", out, n)
    return out


def partition_query(q: str) -> dict[str, Any]:
    """Split a query into locally-servable AND-conjuncts vs live-only remainder.

    `needs_live()` on the whole string used to send every conjunct —
    including `t:` / `f:` already on disk — to the live client. Peel top-level
    AND conjuncts. An OR that mixes live and local stays live as a unit
    (cannot serve half an OR and call it a match).

    Returns {
      kind: "local" | "live" | "mixed",
      local_query: str | None,
      live_query: str | None,
    }
    """
    raw = (q or "").strip()
    if not raw:
        return {"kind": "local", "local_query": "", "live_query": None}
    if not needs_live(raw):
        return {"kind": "local", "local_query": raw, "live_query": None}
    try:
        tree = parse_query(raw)
    except Exception:
        # Unparseable with live ops — whole thing needs live.
        return {"kind": "live", "local_query": None, "live_query": raw}

    conjuncts = _flatten_and(tree)
    local_nodes = [n for n in conjuncts if not _node_needs_live(n)]
    live_nodes = [n for n in conjuncts if _node_needs_live(n)]

    # f:commander otag:draw is still pure-live: format wrappers alone are not
    # a local answer worth serving (pure otag: → UNSUPPORTED).
    content_local = [n for n in local_nodes if not _node_is_filter_only(n)]
    if live_nodes and not content_local:
        live_tree = _and_nodes(live_nodes + local_nodes) or _and_nodes(live_nodes)
        live_q = _render_node(live_tree) if live_tree is not None else raw
        return {"kind": "live", "local_query": None, "live_query": live_q}

    local_tree = _and_nodes(local_nodes)
    live_tree = _and_nodes(live_nodes)
    local_q = _render_node(local_tree) if local_tree is not None else None
    live_q = _render_node(live_tree) if live_tree is not None else None

    if local_q is not None and live_q is not None:
        return {"kind": "mixed", "local_query": local_q, "live_query": live_q}
    if live_q is not None:
        return {"kind": "live", "local_query": None, "live_query": live_q}
    return {"kind": "local", "local_query": local_q or raw, "live_query": None}


# ---------------------------------------------------------------------------
# bulk fetch
# ---------------------------------------------------------------------------

def _http_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _download_gz_jsonl(url: str, dest: Path) -> None:
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urlopen(req, timeout=600) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    if url.endswith(".gz") or "jsonl.gz" in url:
        with gzip.open(tmp, "rb") as gz, dest.open("wb") as out:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(dest)


def _bulk_entry(index: dict, bulk_type: str) -> dict:
    for row in index.get("data") or []:
        if row.get("type") == bulk_type:
            return row
    raise RuntimeError(f"bulk-data index has no {bulk_type} entry")


def ensure_oracle_cards(*, offline: bool = False) -> dict[str, Any]:
    """Ensure ~/.deckwright/oracle_cards.jsonl exists; return meta.

    DECKWRIGHT_CORPUS_READONLY skips fetch and write. Missing bulk
    stays missing — unknown, not a fake empty corpus.
    """
    if instance.corpus_readonly():
        if ORACLE_META.is_file():
            return json.loads(ORACLE_META.read_text())
        if ORACLE_JSONL.is_file():
            return {}
        return {"readonly": True, "missing": True}

    if offline:
        if not ORACLE_JSONL.is_file():
            raise FileNotFoundError(
                f"--offline set but cache missing: {ORACLE_JSONL}"
            )
        if ORACLE_META.is_file():
            return json.loads(ORACLE_META.read_text())
        return {}

    index = _http_json(BULK_INDEX)
    entry = _bulk_entry(index, "oracle_cards")
    download = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not download:
        raise RuntimeError("oracle_cards entry has no download URI")
    updated = entry.get("updated_at") or ""
    prev = json.loads(ORACLE_META.read_text()) if ORACLE_META.is_file() else {}
    if (
        ORACLE_JSONL.is_file()
        and prev.get("updated_at") == updated
        and prev.get("download_uri") == download
    ):
        return prev

    print(f"[corpus] fetching oracle_cards ({updated}) …")
    _download_gz_jsonl(download, ORACLE_JSONL)
    meta = {
        "updated_at": updated,
        "download_uri": download,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "type": "oracle_cards",
        "card_count": _count_jsonl(ORACLE_JSONL),
    }
    ORACLE_META.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def ensure_prices(*, offline: bool = False) -> dict[str, Any]:
    """Build ~/.deckwright/prices.json from default_cards (oracle_id → usd)."""
    if offline:
        if PRICES_META.is_file():
            return json.loads(PRICES_META.read_text())
        return {}

    index = _http_json(BULK_INDEX)
    entry = _bulk_entry(index, "default_cards")
    download = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not download:
        raise RuntimeError("default_cards entry has no download URI")
    updated = entry.get("updated_at") or ""
    prev = json.loads(PRICES_META.read_text()) if PRICES_META.is_file() else {}
    if (
        PRICES_JSON.is_file()
        and prev.get("updated_at") == updated
        and prev.get("download_uri") == download
    ):
        return prev

    print(f"[corpus] fetching default_cards for prices ({updated}) …")
    raw = CACHE_DIR / "default_cards.jsonl"
    _download_gz_jsonl(download, raw)
    prices: dict[str, str] = {}
    for card in iter_jsonl(raw):
        oid = card.get("oracle_id")
        if not oid:
            continue
        usd = (card.get("prices") or {}).get("usd")
        if usd is None:
            continue
        # Prefer the cheapest non-null printing as the ballpark.
        prev_usd = prices.get(oid)
        if prev_usd is None or float(usd) < float(prev_usd):
            prices[oid] = str(usd)
    PRICES_JSON.write_text(json.dumps(prices) + "\n")
    meta = {
        "updated_at": updated,
        "download_uri": download,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "type": "default_cards",
        "price_count": len(prices),
    }
    PRICES_META.write_text(json.dumps(meta, indent=2) + "\n")
    # default_cards is large; drop the raw dump once prices are extracted.
    try:
        raw.unlink()
    except OSError:
        pass
    return meta


def refresh_corpus(*, offline: bool = False) -> dict[str, Any]:
    """Sunday-cadence entry: oracle cards + price overlay + combined meta."""
    if instance.corpus_readonly():
        oracle_meta = ensure_oracle_cards()
        print("[corpus] readonly — not fetching, not writing bulk files")
        return {
            "oracle": oracle_meta,
            "prices": {"readonly": True},
            "readonly": True,
            "updated_at": oracle_meta.get("updated_at") or "",
            "card_count": oracle_meta.get("card_count"),
        }
    oracle_meta = ensure_oracle_cards(offline=offline)
    try:
        price_meta = ensure_prices(offline=offline)
    except Exception as e:
        # Prices are ballparks; a price fetch failure must not block the corpus.
        print(f"[corpus] prices refresh failed (oracle kept): {e}")
        price_meta = {"error": str(e)}
    meta = {
        "oracle": oracle_meta,
        "prices": price_meta,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": oracle_meta.get("updated_at") or "",
        "card_count": oracle_meta.get("card_count")
        or (_count_jsonl(ORACLE_JSONL) if ORACLE_JSONL.is_file() else None),
    }
    CORPUS_META.write_text(json.dumps(meta, indent=2) + "\n")
    print(
        f"[corpus] ready cards={meta.get('card_count')} "
        f"updated_at={meta.get('updated_at') or 'UNKNOWN'} "
        f"fetched_at={meta.get('fetched_at')}"
    )
    return meta


def _count_jsonl(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def read_corpus_meta() -> dict[str, Any]:
    if CORPUS_META.is_file():
        try:
            return json.loads(CORPUS_META.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    if ORACLE_META.is_file():
        try:
            return json.loads(ORACLE_META.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# query lexer / parser / evaluator
# ---------------------------------------------------------------------------

_COLORS = set("WUBRG")


@dataclass
class _Tok:
    kind: str
    value: str = ""


def _lex(q: str) -> list[_Tok]:
    s = q or ""
    i, n = 0, len(s)
    out: list[_Tok] = []
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            out.append(_Tok("LPAREN"))
            i += 1
            continue
        if ch == ")":
            out.append(_Tok("RPAREN"))
            i += 1
            continue
        if ch == "-":
            out.append(_Tok("NOT"))
            i += 1
            continue
        if s[i:i + 2].upper() == "OR" and (i + 2 == n or not s[i + 2].isalnum()):
            out.append(_Tok("OR"))
            i += 2
            continue
        if s[i:i + 3].upper() == "NOT" and (i + 3 == n or not s[i + 3].isalnum()):
            out.append(_Tok("NOT"))
            i += 3
            continue
        # field op value  OR  bare / quoted atom
        m = re.match(
            r"^([A-Za-z_]+)(!?=|<=|>=|<|>|:)(\"[^\"]*\"|'[^']*'|[^\s()]+)",
            s[i:],
        )
        if m:
            field, op, raw = m.group(1), m.group(2), m.group(3)
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                val = raw[1:-1]
            else:
                val = raw
            out.append(_Tok("CLAUSE", json.dumps([field.lower(), op, val])))
            i += m.end()
            continue
        # !"Exact Name"  or  name:foo
        m = re.match(r"!\"([^\"]+)\"", s[i:])
        if m:
            out.append(_Tok("CLAUSE", json.dumps(["name", "!", m.group(1)])))
            i += m.end()
            continue
        m = re.match(r"\"([^\"]+)\"|'([^']+)'|([^\s()]+)", s[i:])
        if m:
            val = m.group(1) or m.group(2) or m.group(3)
            out.append(_Tok("CLAUSE", json.dumps(["name", ":", val])))
            i += m.end()
            continue
        raise ValueError(f"cannot lex at {i}: {s[i:i + 20]!r}")
    return out


class _Parser:
    def __init__(self, tokens: list[_Tok]):
        self.toks = tokens
        self.i = 0

    def _peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _bump(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Any:
        if not self.toks:
            return ("TRUE",)
        node = self._or()
        if self._peek() is not None:
            raise ValueError("trailing tokens")
        return node

    def _or(self) -> Any:
        left = self._and()
        while self._peek() and self._peek().kind == "OR":
            self._bump()
            right = self._and()
            left = ("OR", left, right)
        return left

    def _and(self) -> Any:
        nodes = [self._not()]
        while self._peek() and self._peek().kind in ("NOT", "LPAREN", "CLAUSE"):
            nodes.append(self._not())
        if len(nodes) == 1:
            return nodes[0]
        out = nodes[0]
        for n in nodes[1:]:
            out = ("AND", out, n)
        return out

    def _not(self) -> Any:
        if self._peek() and self._peek().kind == "NOT":
            self._bump()
            return ("NOT", self._atom())
        return self._atom()

    def _atom(self) -> Any:
        t = self._peek()
        if t is None:
            raise ValueError("unexpected end")
        if t.kind == "LPAREN":
            self._bump()
            node = self._or()
            if not self._peek() or self._peek().kind != "RPAREN":
                raise ValueError("missing )")
            self._bump()
            return node
        if t.kind == "CLAUSE":
            self._bump()
            field, op, val = json.loads(t.value)
            return ("CLAUSE", field, op, val)
        raise ValueError(f"unexpected {t.kind}")


def parse_query(q: str) -> Any:
    return _Parser(_lex(q)).parse()


def _face_join(card: dict, key: str, joiner: str) -> str:
    if card.get(key) not in (None, ""):
        return str(card.get(key) or "")
    faces = card.get("card_faces") or []
    parts = [str(f.get(key) or "") for f in faces if f.get(key) not in (None, "")]
    return joiner.join(parts)


def _oracle_text(card: dict) -> str:
    return _face_join(card, "oracle_text", "\n").lower()


def _type_line(card: dict) -> str:
    return _face_join(card, "type_line", " // ").lower()


def _flavor(card: dict) -> str:
    top = card.get("flavor_text") or ""
    if top:
        return str(top).lower()
    faces = card.get("card_faces") or []
    return " ".join(str(f.get("flavor_text") or "") for f in faces).lower()


def _ci_set(card: dict) -> set[str]:
    return {c.upper() for c in (card.get("color_identity") or [])}


def _parse_colors(val: str) -> set[str]:
    v = (val or "").upper().replace(",", "")
    if v in ("", "C", "COLORLESS"):
        return set()
    return {ch for ch in v if ch in _COLORS}


def _usd(card: dict) -> float | None:
    raw = (card.get("prices") or {}).get("usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_commander(card: dict) -> bool:
    """Scryfall is:commander — legendary creature or 'can be your commander'."""
    if (card.get("legalities") or {}).get("commander") not in ("legal", "restricted"):
        # Banned commanders are still "is:commander" on Scryfall for some, but
        # our ladders always AND f:commander. Match legal faces here.
        pass
    tl = _type_line(card)
    if "legendary" in tl and "creature" in tl:
        return True
    if "can be your commander" in _oracle_text(card):
        return True
    return False


def _match_is(card: dict, val: str) -> bool:
    v = val.lower()
    if v == "commander":
        return _is_commander(card)
    if v == "gamechanger":
        return bool(card.get("game_changer"))
    if v == "reprint":
        return bool(card.get("reprint"))
    if v == "digital":
        return bool(card.get("digital")) or (
            set(card.get("games") or []) == {"arena"}
            or set(card.get("games") or []) == {"mtgo"}
        )
    if v == "reserved":
        return bool(card.get("reserved"))
    # Unknown is: keyword — refuse rather than guess false.
    raise ValueError(f"unsupported is:{val}")


def _match_has(card: dict, val: str) -> bool:
    v = val.lower()
    if v == "art_crop":
        uris = card.get("image_uris") or {}
        if uris.get("art_crop"):
            return True
        for face in card.get("card_faces") or []:
            if (face.get("image_uris") or {}).get("art_crop"):
                return True
        return False
    raise ValueError(f"unsupported has:{val}")


def _cmp_num(left: float | None, op: str, right: float) -> bool:
    if left is None:
        return False
    if op in (":", "=", "=="):
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise ValueError(f"bad numeric op {op}")


def _match_clause(card: dict, field: str, op: str, val: str) -> bool:
    f = field.lower()
    if f in ("otag", "art"):
        raise ValueError(f"{f}: not in bulk data")
    if f in ("t", "type"):
        return val.lower() in _type_line(card)
    if f in ("o", "oracle"):
        return val.lower() in _oracle_text(card)
    if f == "keyword":
        kws = [k.lower() for k in (card.get("keywords") or [])]
        return val.lower() in kws
    if f in ("f", "format"):
        # f:commander → legalities.commander == legal
        return (card.get("legalities") or {}).get(val.lower()) == "legal"
    if f in ("id", "ci", "identity", "coloridentity", "commanderidentity"):
        want = _parse_colors(val)
        have = _ci_set(card)
        if op in (":", "="):
            return have == want
        if op == "<=":
            return have <= want
        if op == ">=":
            return have >= want
        if op == "!=":
            return have != want
        raise ValueError(f"unsupported id op {op}")
    if f in ("cmc", "mv", "manavalue"):
        return _cmp_num(float(card.get("cmc") or 0), op, float(val))
    if f == "usd":
        return _cmp_num(_usd(card), op, float(val))
    if f in ("e", "set", "edition"):
        return (card.get("set") or "").lower() == val.lower()
    if f == "is":
        return _match_is(card, val)
    if f == "has":
        return _match_has(card, val)
    if f in ("ft", "flavor"):
        return val.lower() in _flavor(card)
    if f == "name":
        name = (card.get("name") or "").lower()
        if op == "!":
            return name == val.lower()
        return val.lower() in name
    if f == "power":
        raw = _face_join(card, "power", "")
        try:
            return _cmp_num(float(raw), op, float(val))
        except ValueError:
            return False
    if f == "toughness":
        raw = _face_join(card, "toughness", "")
        try:
            return _cmp_num(float(raw), op, float(val))
        except ValueError:
            return False
    raise ValueError(f"unsupported field {field}")


def eval_node(card: dict, node: Any) -> bool:
    kind = node[0]
    if kind == "TRUE":
        return True
    if kind == "OR":
        return eval_node(card, node[1]) or eval_node(card, node[2])
    if kind == "AND":
        return eval_node(card, node[1]) and eval_node(card, node[2])
    if kind == "NOT":
        return not eval_node(card, node[1])
    if kind == "CLAUSE":
        return _match_clause(card, node[1], node[2], node[3])
    raise ValueError(f"bad node {kind}")


# ---------------------------------------------------------------------------
# Corpus index
# ---------------------------------------------------------------------------

class Corpus:
    def __init__(self) -> None:
        self.cards: list[dict] = []
        self.by_name: dict[str, dict] = {}
        self.meta: dict[str, Any] = {}
        self.loaded: bool = False

    @classmethod
    def from_jsonl(
        cls,
        path: Path,
        *,
        prices_path: Path | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "Corpus":
        c = cls()
        prices: dict[str, str] = {}
        if prices_path and prices_path.is_file():
            try:
                prices = json.loads(prices_path.read_text())
            except (OSError, json.JSONDecodeError):
                prices = {}
        cards: list[dict] = []
        by_name: dict[str, dict] = {}
        for card in iter_jsonl(path):
            oid = card.get("oracle_id")
            if oid and oid in prices:
                p = dict(card.get("prices") or {})
                if p.get("usd") in (None, ""):
                    p["usd"] = prices[oid]
                    card = {**card, "prices": p}
            cards.append(card)
            key = (card.get("name") or "").lower()
            if key and key not in by_name:
                by_name[key] = card
        c.cards = cards
        c.by_name = by_name
        c.meta = dict(meta or {})
        if "card_count" not in c.meta:
            c.meta["card_count"] = len(cards)
        c.loaded = True
        return c

    def status(self) -> dict[str, Any]:
        if not self.loaded:
            return {
                "loaded": False,
                "card_count": None,
                "updated_at": None,
                "fetched_at": None,
                "path": str(ORACLE_JSONL),
                "stale": None,
            }
        updated = self.meta.get("updated_at")
        fetched = self.meta.get("fetched_at")
        age_days = None
        stale = None
        ts = _parse_ts(fetched) or _parse_ts(updated)
        if ts is not None:
            age_days = max(0, (datetime.now(timezone.utc) - ts).days)
            stale = age_days > 14
        return {
            "loaded": True,
            "card_count": self.meta.get("card_count", len(self.cards)),
            "updated_at": updated,
            "fetched_at": fetched,
            "age_days": age_days,
            "stale": stale,
            "path": str(ORACLE_JSONL),
        }

    def _eval_search(
        self,
        q: str,
        *,
        order: str = "edhrec",
        page: int = 1,
    ) -> ServeResult:
        """Evaluate a fully-local query. Caller must not pass live-only ops."""
        try:
            tree = parse_query(q)
        except Exception as e:
            return ServeResult(
                UNSUPPORTED, {},
                f"local index cannot parse query: {e}",
            )
        matched: list[dict] = []
        try:
            for card in self.cards:
                if eval_node(card, tree):
                    matched.append(card)
        except Exception as e:
            return ServeResult(
                UNSUPPORTED, {},
                f"local index cannot evaluate query: {e}",
            )
        if order == "edhrec":
            matched.sort(key=lambda c: (
                c.get("edhrec_rank") is None,
                c.get("edhrec_rank") or 10**9,
            ))
        elif order == "name":
            matched.sort(key=lambda c: (c.get("name") or "").lower())
        total = len(matched)
        page = max(1, int(page or 1))
        start = (page - 1) * PAGE_SIZE
        chunk = matched[start:start + PAGE_SIZE]
        if total == 0:
            return ServeResult(EMPTY, {
                "object": "list",
                "total_cards": 0,
                "has_more": False,
                "data": [],
            })
        return ServeResult(HIT, {
            "object": "list",
            "total_cards": total,
            "has_more": start + len(chunk) < total,
            "data": chunk,
        })

    def serve_search(
        self,
        q: str,
        *,
        order: str = "edhrec",
        unique: str = "cards",
        page: int = 1,
    ) -> ServeResult:
        if not self.loaded:
            return ServeResult(MISSING, {}, "local corpus not loaded")

        # peel AND-conjuncts. Pure otag:/art: stays UNSUPPORTED.
        # Mixed → evaluate local conjuncts; mark INCOMPLETE (not a smaller HIT).
        # Local-empty AND short-circuits to a real EMPTY without live.
        if needs_live(q):
            part = partition_query(q)
            if part["kind"] == "live" or not part.get("local_query"):
                return ServeResult(
                    UNSUPPORTED, {},
                    "query uses otag:/art: which are not in bulk data",
                )
            local_r = self._eval_search(
                part["local_query"], order=order, page=page,
            )
            if local_r.status in (UNSUPPORTED, MISSING):
                return local_r
            if local_r.status == EMPTY:
                # A AND B with A empty is empty regardless of B.
                return local_r
            live_bit = part.get("live_query") or "otag:/art:"
            return ServeResult(
                INCOMPLETE,
                local_r.payload,
                f"local clauses served; {live_bit} not evaluated — "
                "unknown-not-complete",
            )

        return self._eval_search(q, order=order, page=page)

    def serve_named(
        self, *, exact: str | None = None, fuzzy: str | None = None,
    ) -> ServeResult:
        if not self.loaded:
            return ServeResult(MISSING, {}, "local corpus not loaded")
        if exact:
            card = self.by_name.get(exact.lower())
            if card:
                return ServeResult(HIT, card)
            return ServeResult(EMPTY, {"data": []})
        if fuzzy:
            needle = fuzzy.lower().strip()
            needle_compact = re.sub(r"[^a-z0-9]+", "", needle)
            tokens = [t for t in re.split(r"[^a-z0-9]+", needle) if t]

            def _fuzzy_score(card: dict) -> tuple:
                name = (card.get("name") or "").lower()
                compact = re.sub(r"[^a-z0-9]+", "", name)
                if needle and needle in name:
                    rank = 0
                elif needle_compact and needle_compact in compact:
                    rank = 1
                elif tokens and all(t in name for t in tokens):
                    rank = 2
                else:
                    return None
                return (
                    rank,
                    card.get("edhrec_rank") is None,
                    card.get("edhrec_rank") or 10**9,
                )

            scored = []
            for c in self.cards:
                s = _fuzzy_score(c)
                if s is not None:
                    scored.append((s, c))
            if not scored:
                return ServeResult(EMPTY, {"data": []})
            scored.sort(key=lambda x: x[0])
            return ServeResult(HIT, scored[0][1])
        return ServeResult(UNSUPPORTED, {}, "named requires exact or fuzzy")

    def serve_collection(self, identifiers: list[dict]) -> ServeResult:
        if not self.loaded:
            return ServeResult(MISSING, {}, "local corpus not loaded")
        data: list[dict] = []
        not_found: list[dict] = []
        for ident in identifiers:
            card = None
            if "id" in ident:
                for c in self.cards:
                    if c.get("id") == ident["id"]:
                        card = c
                        break
            elif "name" in ident:
                card = self.by_name.get(str(ident["name"]).lower())
            if card:
                data.append(card)
            else:
                not_found.append(ident)
        return ServeResult(HIT, {
            "object": "list",
            "data": data,
            "not_found": not_found,
        })

    def serve_random(self, q: str | None = None) -> ServeResult:
        if not self.loaded:
            return ServeResult(MISSING, {}, "local corpus not loaded")
        import random as _random
        pool = self.cards
        if q:
            r = self.serve_search(q)
            if r.status in (UNSUPPORTED, MISSING, INCOMPLETE):
                # Incomplete is not a pool — unknown, not a smaller random set.
                return r
            pool = r.payload.get("data") or []
            # search already paginated — re-run without page limit for random
            if r.status in (HIT, EMPTY):
                try:
                    tree = parse_query(q)
                    pool = [c for c in self.cards if eval_node(c, tree)]
                except Exception as e:
                    return ServeResult(
                        UNSUPPORTED, {},
                        f"local index cannot evaluate random query: {e}",
                    )
        if not pool:
            return ServeResult(EMPTY, {"data": []})
        return ServeResult(HIT, _random.choice(pool))


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_CORPUS: Corpus | None = None


def get_corpus() -> Corpus:
    """Process-wide corpus. Loads from disk on first use when present."""
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS
    _CORPUS = load_default_corpus()
    return _CORPUS


def load_default_corpus(*, force: bool = False) -> Corpus:
    global _CORPUS
    if _CORPUS is not None and _CORPUS.loaded and not force:
        return _CORPUS
    if not ORACLE_JSONL.is_file():
        _CORPUS = Corpus()
        return _CORPUS
    meta = read_corpus_meta()
    prices = PRICES_JSON if PRICES_JSON.is_file() else None
    _CORPUS = Corpus.from_jsonl(ORACLE_JSONL, prices_path=prices, meta=meta)
    return _CORPUS


def set_corpus(c: Corpus | None) -> None:
    """Test seam — inject a fixture corpus."""
    global _CORPUS
    _CORPUS = c


def route_get(
    corp: Corpus,
    path: str,
    params: dict | None,
    *,
    meters: Meters | None = None,
) -> ServeResult:
    """Try to serve a GET from the local corpus.

    Returns HIT/EMPTY when answered, UNSUPPORTED when the caller should use
    live Scryfall, MISSING when no corpus is loaded.
    """
    m = meters
    params = params or {}
    if path == "/cards/search":
        r = corp.serve_search(
            params.get("q") or "",
            order=params.get("order") or "edhrec",
            unique=params.get("unique") or "cards",
            page=int(params.get("page") or 1),
        )
    elif path == "/cards/named":
        r = corp.serve_named(exact=params.get("exact"), fuzzy=params.get("fuzzy"))
    elif path == "/cards/random":
        r = corp.serve_random(params.get("q"))
    else:
        r = ServeResult(UNSUPPORTED, {}, f"path {path} not served locally")
    if m is not None:
        if r.status in (HIT, EMPTY):
            m.local += 1
        elif r.status == INCOMPLETE:
            # Local conjuncts answered; live conjuncts still required.
            m.local += 1
            m.live_needed += 1
        elif r.status == UNSUPPORTED:
            m.live_needed += 1
        elif r.status == MISSING:
            m.missing += 1
    return r


def route_post(
    corp: Corpus,
    path: str,
    body: dict | None,
    *,
    meters: Meters | None = None,
) -> ServeResult:
    m = meters
    body = body or {}
    if path == "/cards/collection":
        r = corp.serve_collection(list(body.get("identifiers") or []))
    else:
        r = ServeResult(UNSUPPORTED, {}, f"path {path} not served locally")
    if m is not None:
        if r.status in (HIT, EMPTY):
            m.local += 1
        elif r.status == INCOMPLETE:
            m.local += 1
            m.live_needed += 1
        elif r.status == UNSUPPORTED:
            m.live_needed += 1
        elif r.status == MISSING:
            m.missing += 1
    return r


def local_only_mode() -> bool:
    """DECKWRIGHT_CORPUS=local-only — never hit the network for card lookups."""
    return os.environ.get("DECKWRIGHT_CORPUS", "").strip().lower() in (
        "local-only", "require",
    )


def corpus_disabled() -> bool:
    """DECKWRIGHT_CORPUS=off — skip the local index; everything is live."""
    return os.environ.get("DECKWRIGHT_CORPUS", "").strip().lower() in (
        "0", "off", "false", "live",
    )


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="refresh local Scryfall corpus")
    p.add_argument("--offline", action="store_true",
                   help="reuse cached files; do not download")
    p.add_argument("--status", action="store_true",
                   help="print corpus status and exit")
    args = p.parse_args()
    if args.status:
        c = load_default_corpus()
        print(json.dumps(c.status(), indent=2))
        return 0 if c.loaded else 2
    meta = refresh_corpus(offline=args.offline)
    load_default_corpus(force=True)
    print(json.dumps({"ok": True, "meta": meta}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
