#!/usr/bin/env python3
"""API boundary — emitted keys must be consumed (or allowlisted).

Reports both directions at the seam that playtests keep finding:

  emitted_unread  — app.py returns it, index.html never reads it
  read_unsent     — index.html reads it, app.py never sends it
  saved_inert     — persisted / summarised, no reader consumes it

Also covers the HTML lead class that started the card:

  inert_data_attrs — data-* written in index.html with no dataset/CSS/reader

A lead is not a defect. SERVER_ONLY / UI_OPTIONAL / DATA_ATTR_KEEP carry a
reason. New unread emits without a reason fail the gate — that is the point.

    python scripts/api_boundary.py
    python test_api_boundary.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Contract: journey endpoints + saved-deck summary
#
# Top-level keys (and a few load-bearing dotted paths) that the UI journey
# depends on. Nested card faces (trim_card) are covered by CARD_KEYS below.
# ---------------------------------------------------------------------------

ENDPOINTS: dict[str, dict] = {
    "/api/chat": {
        "emits": {
            "text", "brief", "still_asking", "brief_error",
            "readiness", "instrument",
        },
        "ui_reads": {
            "text", "brief", "brief_error", "readiness",
            "readiness.ready", "readiness.asked_about",
        },
        "server_only": {
            "still_asking": (
                "UI infers the three the chat envelope states from brief / brief_error / "
                "readiness; still_asking stays on the wire for the model envelope"
            ),
            "instrument": (
                "Fed into ensure_closing_instrument_readback and readiness; "
                "the page never paints the raw instrument object"
            ),
        },
    },
    "/api/readback": {
        "emits": {
            "theme", "colors", "terms", "composed", "thin", "thin_threshold",
        },
        "ui_reads": {
            "terms", "composed", "thin", "thin_threshold",
            "composed.said", "composed.count",
        },
        "server_only": {
            "theme": "Echo of the brief; page already has brief.theme",
            "colors": "Echo of the brief; page already has brief.colors",
        },
    },
    "/api/commanders": {
        "emits": {
            "picks", "note", "query", "rung_total", "offset", "has_more",
            "rung_exhausted", "shown_of", "status",
        },
        "ui_reads": {
            "picks", "note", "status", "shown_of", "has_more",
            "rung_exhausted", "rung_total", "offset",
        },
        "server_only": {
            "query": (
                "Scryfall ladder query for diagnostics / tests; picker does not "
                "surface the raw query string"
            ),
        },
    },
    "/api/build": {
        "emits": {
            "commander", "deck", "price", "commander_card", "partner",
            "partner_card", "commanders", "commander_cards", "color_identity",
            "deck_size", "budget", "archidekt", "tcgplayer", "off_meta",
            "notes", "signals", "overview", "substitutions", "accounting",
            "cohesion", "role_pools", "composition", "role_queries",
            "role_queries_raw", "lands", "legal", "buyable", "violations",
            "banned", "card_pool", "legality_mode", "brief", "sim_scoring",
            "plan", "plan_derive_failed", "curve_report", "conversation",
            "id", "name", "saved_at",
            "commander_strategy", "strategy_audit", "corpus", "edhrec_seed",
        },
        "ui_reads": {
            "commander", "deck", "price", "commander_card", "partner",
            "partner_card", "commanders", "commander_cards", "color_identity",
            "deck_size", "budget", "archidekt", "tcgplayer", "off_meta",
            "notes", "signals", "overview", "substitutions", "accounting",
            "cohesion", "role_pools", "role_queries", "lands", "legal",
            "buyable", "violations", "banned", "card_pool", "brief", "plan",
            "curve_report", "conversation", "id", "name", "saved_at",
            "budget.delta", "budget.budget", "lands.total_lands",
            "accounting.counts", "curve_report.preference",
            "curve_report.skeleton", "curve_report.roles_applied",
            "commander_strategy", "strategy_audit",
            "edhrec_seed", "edhrec_seed.provenance",
            "deck.edhrec_inclusion_pct", "deck.edhrec_rising",
        },
        "server_only": {
            "composition": (
                "rebuild target; UI recomputes axis counts from deck via "
                "axisCounts() rather than reading the stamp"
            ),
            "role_queries_raw": (
                "Raw Scryfall strings behind role_queries prose; page shows "
                "explain_group text only"
            ),
            "sim_scoring": (
                "Build-time candidate-scoring diagnostics; not a "
                "player-facing surface"
            ),
            "legality_mode": (
                "Internal report mode; UI keys off legal / buyable / violations"
            ),
            "plan_derive_failed": (
                "Boolean diagnostic when derive() threw; UI treats missing plan "
                "as absent"
            ),
            "corpus": (
                "local-vs-live call meters + corpus age; ops diagnostic"
            ),
        },
    },
    "/api/ask": {
        "emits": {
            "text", "brief", "still_asking", "brief_error", "instrument",
            "improve_candidates", "glued_payoffs", "way_preference",
            "way_playstyle", "coverage",
        },
        "ui_reads": {
            "text", "brief", "brief_error", "coverage",
        },
        "server_only": {
            "still_asking": (
                "Same the chat envelope as chat; askChange branches on brief / "
                "brief_error"
            ),
            "instrument": (
                "live improve surface for the page — emitted, not yet "
                "painted. Keep until the ask panel consumes it; do not delete"
            ),
            "improve_candidates": (
                "glued leftovers for the improve surface — emitted, not "
                "yet painted. Same hold as instrument"
            ),
            "glued_payoffs": (
                "payoff leftovers for the improve surface — emitted, "
                "not yet painted"
            ),
            "way_preference": (
                "Projected onto the ask prompt via deck_context; response echo "
                "is unused by the page"
            ),
            "way_playstyle": (
                "Projected onto the ask prompt via deck_context; response echo "
                "is unused by the page"
            ),
        },
    },
    "/api/thesis": {
        "emits": {
            "payoff_cards", "conditions", "error", "source",
            "glued_payoff_candidates", "glued_payoff_status",
            "glued_payoff_coverage",
        },
        "ui_reads": {
            "payoff_cards", "conditions", "error",
        },
        "server_only": {
            "source": "Which thesis path ran (plan vs model); diagnostics only",
            "glued_payoff_candidates": "Thesis path diagnostics; not painted",
            "glued_payoff_status": "Thesis path diagnostics; not painted",
            "glued_payoff_coverage": "Thesis path diagnostics; not painted",
        },
    },
    "/api/simulate": {
        "emits": {
            "runs", "curves", "diagnostics", "assumptions", "review",
            "good_hand", "bad_hand", "composition", "tweak", "deck",
        },
        "ui_reads": {
            "runs", "curves", "diagnostics", "assumptions", "review",
            "good_hand", "bad_hand", "tweak",
        },
        "server_only": {
            "composition": (
                "Returned after a slider tweak so clients can sync; the page "
                "recomputes from deck"
            ),
            "deck": (
                "Tweaked 99 for clients that replace state from the response; "
                "this page mutates deckData.deck locally via apply_tweak path"
            ),
        },
    },
    "/api/boot": {
        "emits": {
            "ready", "phase", "detail", "refreshed", "reevaluated",
            "stale", "error", "understanding", "card_source", "elapsed_ms",
            "corpus", "env", "candidate",
        },
        "ui_reads": {
            "ready", "detail", "card_source",
        },
        "server_only": {
            "phase": "Boot veil stage label; page keys off ready + detail text",
            "refreshed": "Whether catalogs refreshed this boot; not painted",
            "reevaluated": "Whether print-wave reeval ran; not painted",
            "stale": "Staleness flag for ops; veil only needs ready/detail",
            "error": "Structured boot error; detail string is what the veil shows",
            "understanding": "Boot remeasure payload; veil uses detail",
            "elapsed_ms": "Boot timing; not painted",
            "corpus": "corpus status on every boot poll; ops/veil detail",
            "env": "blue/green identity; badge comes from HTML inject",
            "candidate": "A/B/C flag; not painted on boot veil",
        },
    },
    "/api/saved": {
        "emits": {
            "id", "name", "commander", "partner", "price", "saved_at",
            "buyable", "card_pool", "has_brief", "theme", "has_primer",
            "favorite", "auto_saved", "wish", "way", "art",
        },
        "ui_reads": {
            "id", "name", "commander", "partner", "price", "saved_at",
            "theme", "favorite", "auto_saved", "wish", "way", "art",
        },
        "server_only": {
            "buyable": "List row does not badge buyability; full load does",
            "card_pool": "List row does not show pool; full load does",
            "has_brief": "List uses wish/way/theme text, not a brief badge",
            "has_primer": "Primer is opened from the loaded deck, not the list",
        },
    },
}

# trim_card / deck-card fields that ride on every card-shaped payload.
CARD_KEYS = {
    "emits": {
        "name", "cost", "cmc", "type", "text", "usd", "color_identity",
        "legal", "gc", "keywords", "produced_mana", "layout", "power",
        "toughness", "purchase_uris", "image", "art", "edhrec_rank",
        # build stamps
        "role", "why", "bucket",
        "edhrec_inclusion_pct", "edhrec_rising", "edhrec_synergy",
    },
    "ui_reads": {
        "name", "cost", "cmc", "type", "text", "usd", "color_identity",
        "legal", "keywords", "power", "toughness", "image", "art",
        "role", "why", "bucket", "produced_mana",
        "edhrec_inclusion_pct", "edhrec_rising",
    },
    "server_only": {
        "gc": "Game Changer flag used server-side for bracket filter; not painted",
        "layout": "Scryfall layout; DFC handling uses image_uris server-side",
        "edhrec_rank": "Backfill / ranking; not a player-facing field",
        "edhrec_synergy": "deep-dive payload; shopping list shows inclusion only",
        "purchase_uris": "tcgplayer_url() reads this server-side into exports",
    },
}

# Fields the UI may read defensively even when an older save omits them.
UI_OPTIONAL: dict[str, str] = {
    "simulation": "Absent on fresh builds until goldfish runs; page guards",
    "match_notes": "the match notes; absent until first note",
    "pending": "the pending change sets; absent until a change set stages",
    "version": "the pending change sets; defaults to 1 when absent",
    "parent_version": "fork pointer; absent on v1",
    "history": "the pending change sets; absent until first approve",
    "favorite": "PATCH may set; older saves omit",
    "theme": "Christen / PATCH; older saves omit",
    "primer": "Christen; older saves omit",
    "mechanics": "attach_mechanics stamp; page may ignore density block",
    "overview_stale": "Set on approve/edit paths; page does not yet badge it",
    "named_by": "her|auto — server stamp; list does not paint the source",
    "ways_to_play": "Precon / commander paths; page guards",
    "chosen_playstyle": "Precon / commander paths; page guards",
    "ways_note": "Optional notice on ways block",
    "upgrades": "Precon beef-it-up; page guards",
    "precon": "Import path marker; page guards",
    "partner_notice": "Optional commander pick notice",
    "writeups_missing": "Commander paging honesty flag",
}

# Persisted / summarised fields. written ⊆ full save; list_reads are summary.
SAVED = {
    "list_emits": set(ENDPOINTS["/api/saved"]["emits"]),
    "list_reads": set(ENDPOINTS["/api/saved"]["ui_reads"]),
    "list_server_only": dict(ENDPOINTS["/api/saved"]["server_only"]),
}

# data-* written in index.html that stay on purpose (CSS / future filter).
DATA_ATTR_KEEP: dict[str, str] = {
    "n": (
        "CSS `.system.shut .sysname::before{content:\"show \" attr(data-n)}` "
        "— folded role count"
    ),
    "bucket": (
        "Per-card accounting provenance (theme_strategy / theme_only / …). "
        "Aggregate counts paint in renderAccountingBlock; row filter by "
        "bucket is the same shape as data-axis keep the "
        "attribute so a filter can land without re-plumbing the row"
    ),
    "tilt": "Landing / masthead parallax amplitude",
    "id": "Saved-list row load/delete handlers",
    "name": "Saved-list delete confirm + editor add pick",
    "mode": "Workbench mode toggle",
    "nav": "Masthead nav current-page mark",
    "way": "Precon / ways-to-play selection",
    "role": "System fold + keyword filter restore",
    "axis": "type tint + type-chip filter",
    "keywords": "Keyword-chip filter",
    "img": "Card-name hover preview source",
    "to": "Count-up animation target",
    "folded": "Host fold bookkeeping",
    "offered": "One-shot CTA guard on the goldfish panel",
    "kw": "Keyword chip identity",
}


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def html_property_reads(html: str) -> set[str]:
    """Best-effort set of response property names the page touches.

    Catches `r.foo`, `d.foo`, `data.foo`, optional chains, and bracket
    access with string literals. Nested paths are recovered when the parent
    binding is a known response root (`r.readiness.ready`).
    """
    keys: set[str] = set()
    # obj.prop and obj?.prop
    for m in re.finditer(
        r"\b([A-Za-z_$][\w$]*)\s*\?\.\s*([A-Za-z_$][\w$]*)"
        r"|\b([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)",
        html,
    ):
        if m.group(1) is not None:
            obj, prop = m.group(1), m.group(2)
        else:
            obj, prop = m.group(3), m.group(4)
        if prop in ("length", "map", "filter", "forEach", "join", "trim",
                    "keys", "values", "entries", "push", "pop", "style",
                    "classList", "innerHTML", "textContent", "value",
                    "checked", "disabled", "hidden", "dataset", "parentElement",
                    "closest", "querySelector", "querySelectorAll",
                    "addEventListener", "getAttribute", "setAttribute",
                    "prototype", "constructor", "toString", "then", "catch",
                    "finally", "bind", "call", "apply"):
            continue
        keys.add(prop)
        # Dotted paths off response roots the page uses.
        if obj in ("r", "d", "data", "gate", "a", "cr", "co", "sub",
                   "skel", "m", "cov", "h"):
            keys.add(f"{obj}.{prop}" if False else prop)
    # Bracket string access: r['brief_error'], d["accounting"]
    for m in re.finditer(r"""\[\s*['"]([A-Za-z_][\w]*)['"]\s*]""", html):
        keys.add(m.group(1))
    # Explicit nested: r.readiness.ready / d.lands.total_lands / etc.
    for m in re.finditer(
        r"\b(?:r|d|data|gate|a|cr|co|skel|cov)\s*"
        r"(?:\.\s*[A-Za-z_$][\w$]*){2,3}",
        html,
    ):
        parts = re.findall(r"[A-Za-z_$][\w$]*", m.group(0))
        if len(parts) >= 2:
            # drop the root binding; keep dotted from first prop
            keys.add(".".join(parts[1:]))
            keys.add(parts[1])
    return keys


def html_mentions_key(html: str, key: str) -> bool:
    """Whether index.html references this contract key at all."""
    if "." in key:
        # nested: require the leaf and a nearby parent mention, or the dotted form
        parent, leaf = key.split(".", 1)
        if re.search(rf"""\.\s*{re.escape(parent)}\s*\.\s*{re.escape(leaf)}\b""", html):
            return True
        # r.composed and later .count on that object — accept leaf + parent both present
        return (
            re.search(rf"""\.\s*{re.escape(parent)}\b""", html) is not None
            and re.search(rf"""\.\s*{re.escape(leaf)}\b""", html) is not None
        )
    # word-ish property access
    return (
        re.search(rf"""\.\s*{re.escape(key)}\b""", html) is not None
        or re.search(rf"""\[\s*['"]{re.escape(key)}['"]\s*]""", html) is not None
        or re.search(rf"""['"]{re.escape(key)}['"]\s*:""", html) is not None
    )


def py_mentions_key(py: str, key: str) -> bool:
    """Whether app.py (or boot.py for /api/boot) mentions this emit key."""
    leaf = key.split(".")[-1]
    return (
        f'"{leaf}"' in py
        or f"'{leaf}'" in py
        or f'"{key}"' in py
    )


def data_attrs_written(html: str) -> set[str]:
    """data-* attribute names emitted in markup or template strings."""
    return set(re.findall(r"\bdata-([a-z0-9-]+)\s*=", html, flags=re.I))


def data_attrs_read(html: str) -> set[str]:
    """data-* names consumed via dataset, getAttribute, CSS attr(), or selectors."""
    read: set[str] = set()
    # el.dataset.fooBar → foo-bar
    for m in re.finditer(r"\.dataset\.([A-Za-z][\w]*)", html):
        camel = m.group(1)
        kebab = re.sub(r"([A-Z])", lambda x: "-" + x.group(1).lower(), camel)
        read.add(kebab.lower())
    for m in re.finditer(
        r"""getAttribute\(\s*['"]data-([a-z0-9-]+)['"]\s*\)""", html, flags=re.I
    ):
        read.add(m.group(1).lower())
    for m in re.finditer(r"attr\(\s*data-([a-z0-9-]+)\s*\)", html, flags=re.I):
        read.add(m.group(1).lower())
    for m in re.finditer(r"\[data-([a-z0-9-]+)(?:[=|\]])", html, flags=re.I):
        read.add(m.group(1).lower())
    return read


# ---------------------------------------------------------------------------
# Diffs
# ---------------------------------------------------------------------------

def emitted_unread(endpoint: str | None = None) -> list[str]:
    """Keys app emits that the UI does not read and are not allowlisted."""
    gaps: list[str] = []
    eps = (
        {endpoint: ENDPOINTS[endpoint]} if endpoint
        else ENDPOINTS
    )
    for ep, spec in eps.items():
        unread = set(spec["emits"]) - set(spec["ui_reads"]) - set(spec["server_only"])
        # ui_reads may include dotted paths; top-level parent counts as read
        top_reads = {k.split(".", 1)[0] for k in spec["ui_reads"]}
        unread -= top_reads
        for k in sorted(unread):
            gaps.append(f"{ep}:{k}")
    return gaps


def read_unsent(endpoint: str | None = None) -> list[str]:
    """Keys the UI reads that the endpoint never emits (and are not optional)."""
    gaps: list[str] = []
    eps = (
        {endpoint: ENDPOINTS[endpoint]} if endpoint
        else ENDPOINTS
    )
    for ep, spec in eps.items():
        # Only check top-level ui_reads against emits; dotted paths need parent
        for k in sorted(spec["ui_reads"]):
            top = k.split(".", 1)[0]
            if top in spec["emits"]:
                continue
            if k in UI_OPTIONAL or top in UI_OPTIONAL:
                continue
            gaps.append(f"{ep}:{k}")
    return gaps


def card_emitted_unread() -> list[str]:
    unread = (
        set(CARD_KEYS["emits"])
        - set(CARD_KEYS["ui_reads"])
        - set(CARD_KEYS["server_only"])
    )
    return sorted(f"card:{k}" for k in unread)


def saved_list_inert() -> list[str]:
    """Summary keys emitted for the saved list that nothing on the list reads."""
    unread = (
        set(SAVED["list_emits"])
        - set(SAVED["list_reads"])
        - set(SAVED["list_server_only"])
    )
    return sorted(f"/api/saved:{k}" for k in unread)


def inert_data_attrs(html: str | None = None) -> list[str]:
    """data-* written in index.html with no reader and no keep reason."""
    text = html if html is not None else _read(ROOT / "index.html")
    written = data_attrs_written(text)
    read = data_attrs_read(text)
    inert = sorted(
        a for a in written
        if a not in read and a not in DATA_ATTR_KEEP
    )
    return inert


def allowlist_reasons_missing() -> list[str]:
    """Every SERVER_ONLY / DATA_ATTR_KEEP / UI_OPTIONAL entry needs a reason."""
    missing: list[str] = []
    for ep, spec in ENDPOINTS.items():
        for k, reason in spec["server_only"].items():
            if not (reason and str(reason).strip()):
                missing.append(f"{ep}:server_only:{k}")
            if k not in spec["emits"]:
                missing.append(f"{ep}:server_only_not_in_emits:{k}")
    for k, reason in CARD_KEYS["server_only"].items():
        if not (reason and str(reason).strip()):
            missing.append(f"card:server_only:{k}")
        if k not in CARD_KEYS["emits"]:
            missing.append(f"card:server_only_not_in_emits:{k}")
    for k, reason in DATA_ATTR_KEEP.items():
        if not (reason and str(reason).strip()):
            missing.append(f"data-attr-keep:{k}")
    for k, reason in UI_OPTIONAL.items():
        if not (reason and str(reason).strip()):
            missing.append(f"ui_optional:{k}")
    return missing


def source_honesty_gaps(
    app_py: str | None = None,
    html: str | None = None,
    boot_py: str | None = None,
    card_py: str | None = None,
) -> list[str]:
    """Contract keys must actually appear in the source files they claim."""
    py = app_py if app_py is not None else _read(ROOT / "deckwright" / "app.py")
    boot = boot_py if boot_py is not None else _read(ROOT / "deckwright" / "boot.py")
    page = html if html is not None else _read(ROOT / "index.html")
    # Card stamps also land in why.py (bucket), trim_card in app.py, edhrec_seed.
    cardsrc = card_py if card_py is not None else (
        py + "\n" + _read(ROOT / "deckwright" / "why.py") + "\n" + _read(ROOT / "deckwright" / "edhrec_seed.py")
    )
    gaps: list[str] = []
    for ep, spec in ENDPOINTS.items():
        src = boot if ep == "/api/boot" else py
        for k in spec["emits"]:
            if not py_mentions_key(src, k):
                gaps.append(f"emit-missing-in-source:{ep}:{k}")
        for k in spec["ui_reads"]:
            if not html_mentions_key(page, k):
                gaps.append(f"ui-read-missing-in-html:{ep}:{k}")
    for k in CARD_KEYS["emits"]:
        if not py_mentions_key(cardsrc, k):
            gaps.append(f"emit-missing-in-source:card:{k}")
    for k in CARD_KEYS["ui_reads"]:
        if not html_mentions_key(page, k):
            gaps.append(f"ui-read-missing-in-html:card:{k}")
    return gaps


def all_gaps() -> dict[str, list[str]]:
    return {
        "emitted_unread": emitted_unread() + card_emitted_unread(),
        "read_unsent": read_unsent(),
        "saved_inert": saved_list_inert(),
        "inert_data_attrs": inert_data_attrs(),
        "allowlist_reasons_missing": allowlist_reasons_missing(),
        "source_honesty": source_honesty_gaps(),
    }


def report() -> int:
    gaps = all_gaps()
    n = sum(len(v) for v in gaps.values())
    print("api boundary")
    for name, items in gaps.items():
        status = "PASS" if not items else "FAIL"
        print(f"  {status}  {name}: {len(items)}")
        for it in items[:40]:
            print(f"        - {it}")
        if len(items) > 40:
            print(f"        … +{len(items) - 40} more")
    print(f"  {'PASS' if n == 0 else 'FAIL'}  total gaps: {n}")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(report())
