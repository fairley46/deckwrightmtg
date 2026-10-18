#!/usr/bin/env python3
"""
build_commanders_kb.py — commander knowledge base builder.

Two halves, reported separately:

  * mechanics — free. synergy.demand_profile / playstyles.detect over oracle.
    Runs over EVERY legal commander. Missing mechanics is a bug, not a gap.
  * research  — expensive. Community sources (fixture, EDHREC fills). Partial
    is legitimate and must be stated as a fraction of the legal total.

Legal commander = the app's own definition (legal_commanders_only in app.py):

    ((t:legendary t:creature) or o:"can be your commander") f:commander

Do not substitute is:commander or bare legendary creature — they disagree by
hundreds.

    python scripts/build_commanders_kb.py              # all mechanics + research
    python scripts/build_commanders_kb.py --top 50     # research fills (default 50)
    python scripts/build_commanders_kb.py --offline    # fixture only (incomplete)
    python scripts/build_commanders_kb.py --research-patch
        # B grow: EDHREC tags onto existing KB (empties + derived-only).
        # No full Scryfall census — patches data/commanders.json in place.
    python scripts/build_commanders_kb.py --mechanics-refresh
        # re-run synergy/playstyles over existing KB entries.
        # Fetches oracle/type from Scryfall; preserves EDHREC community ways.
        # No full legal census or EDHREC re-research.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import app  # noqa: E402
from deckwright import commanders  # noqa: E402
from deckwright import playstyles  # noqa: E402

EDHREC_UA = {
    "User-Agent": "Deckwright/1.0 (local personal deckbuilder)",
    "Accept": "application/json",
}

FIXTURE = REPO / "testdata" / "review" / "commander-playstyles.json"
OUT = REPO / "data" / "commanders.json"
GRAMMAR = REPO / "data" / "scryfall_grammar.json"

# Graded looks (ok + missing) below this are too small to judge a rate.
_EDHREC_RATE_MIN_GRADED = 500
# Above this missing fraction among graded looks, a 403-throttle is more
# likely than EDHREC simply lacking pages. Measured the commander knowledge base run was ~6%.
_EDHREC_MISSING_RATE_FAIL = 0.25
# Failed (unknown) fraction of attempted fetches that means the transport
# did not complete — not a catalogue gap.
_EDHREC_FAILED_RATE_FAIL = 0.15


def edhrec_transport_check(
    *, ok: int, missing: int, failed: int
) -> dict[str, object]:
    """Is this EDHREC run's missing/failed shape catalogue or transport?

    403 from EDHREC's S3 means "no page" for an absent key — correct when
    rare. Across thousands of requests, a throttle that also returns 403
    records every miss as missing and the run looks honest while being wrong.
    """
    ok_n = max(0, int(ok))
    missing_n = max(0, int(missing))
    failed_n = max(0, int(failed))
    graded = ok_n + missing_n
    attempted = graded + failed_n
    missing_rate = (missing_n / graded) if graded else 0.0
    failed_rate = (failed_n / attempted) if attempted else 0.0

    reasons: list[str] = []
    if graded >= _EDHREC_RATE_MIN_GRADED and missing_rate > _EDHREC_MISSING_RATE_FAIL:
        reasons.append(
            f"missing_rate={missing_rate:.1%} "
            f"({missing_n}/{graded}) > {_EDHREC_MISSING_RATE_FAIL:.0%} — "
            f"suspect 403 throttle, not EDHREC catalogue"
        )
    if attempted >= _EDHREC_RATE_MIN_GRADED and failed_rate > _EDHREC_FAILED_RATE_FAIL:
        reasons.append(
            f"failed_rate={failed_rate:.1%} "
            f"({failed_n}/{attempted}) > {_EDHREC_FAILED_RATE_FAIL:.0%} — "
            f"transport did not finish; unknowns are not empties"
        )

    suspicious = bool(reasons)
    return {
        "status": "fail" if suspicious else "ok",
        "suspicious": suspicious,
        "missing_rate": missing_rate,
        "failed_rate": failed_rate,
        "graded": graded,
        "attempted": attempted,
        "reasons": reasons,
    }


# Matches legal_commanders_only() — measured, not assumed (prior count).
LEGAL_QUERY = '((t:legendary t:creature) or o:"can be your commander") f:commander'


def squash(s: str) -> str:
    return commanders.squash(s)


def load_catalog() -> dict[str, str]:
    grammar = json.loads(GRAMMAR.read_text())
    names = (grammar.get("catalogs") or {}).get("card-names") or []
    out: dict[str, str] = {}
    for raw in names:
        n = str(raw).strip()
        if n:
            out.setdefault(squash(n), n)
    return out


def oracle_of(card: dict) -> str:
    """Full oracle text, both faces. Empty string when nothing to read."""
    text = card.get("oracle_text") or card.get("text") or ""
    if isinstance(text, str) and text.strip():
        return text
    faces = card.get("card_faces") or []
    parts = []
    for f in faces:
        ft = (f or {}).get("oracle_text") or (f or {}).get("text") or ""
        if isinstance(ft, str) and ft.strip():
            parts.append(ft)
    return "\n".join(parts)


def type_line_of(card: dict) -> str:
    return (card.get("type_line") or card.get("type") or "") or ""


async def fetch_all_legal_commanders() -> list[dict] | None:
    """Every legal commander via /cards/search paging. None = unknown (not [])."""
    try:
        j = await app.scry(
            "/cards/search", q=LEGAL_QUERY, unique="cards", order="name",
        )
    except Exception as e:
        print(f"  [legal] search failed: {e}")
        return None
    cards = list(j.get("data") or [])
    total = int(j.get("total_cards") or 0)
    print(f"  [legal] page 1: {len(cards)} / {total}")
    page = 1
    while j.get("has_more") and j.get("next_page"):
        parsed = urlparse(str(j["next_page"]))
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            j = await app.scry(parsed.path, **params)
        except Exception as e:
            print(f"  [legal] page {page + 1} failed: {e}")
            return None
        batch = list(j.get("data") or [])
        cards.extend(batch)
        page += 1
        print(f"  [legal] page {page}: +{len(batch)} → {len(cards)} / {total}")
        await asyncio.sleep(0.1)
    return cards


async def fetch_collection(names: list[str]) -> dict[str, dict]:
    """Name → Scryfall card (color_identity, type_line, oracle_text)."""
    by_name: dict[str, dict] = {}
    for i in range(0, len(names), 75):
        chunk = names[i:i + 75]
        payload = {"identifiers": [{"name": n} for n in chunk]}
        try:
            res = await app.scry_post("/cards/collection", payload)
        except Exception as e:
            print(f"  [collection] failed for chunk @ {i}: {e}")
            continue
        for card in (res or {}).get("data") or []:
            name = card.get("name") or ""
            if name:
                by_name[name] = card
        not_found = (res or {}).get("not_found") or []
        if not_found:
            print(f"  [collection] not_found: {not_found[:5]}"
                  + ("…" if len(not_found) > 5 else ""))
        await asyncio.sleep(0.2)
    return by_name


async def fetch_edhrec_top(n: int) -> list[dict]:
    """Top-N legal commanders by EDHREC rank. Empty on failure (unknown ≠ [])."""
    if n <= 0:
        return []
    # Ranked fill for the research half only — mechanics already covers all.
    q = "is:commander legal:commander -is:funny -is:token"
    try:
        cards = await app.search(q, limit=n)
    except Exception as e:
        print(f"  [edhrec-top] search failed: {e} — skipping top fill")
        return []
    if not cards:
        print("  [edhrec-top] empty result — unknown, not zero; skipping")
        return []
    return cards[:n]


def _parse_edhrec_taglinks(data: dict) -> list[dict] | None:
    # Prefer tag_counts — fuller than panel taglinks on some pages.
    counts = data.get("tag_counts")
    if isinstance(counts, dict):
        return [
            {"value": k, "count": v, "slug": commanders.edhrec_slug(str(k))}
            for k, v in counts.items()
        ]
    panels = data.get("panels") or {}
    taglinks = panels.get("taglinks")
    if isinstance(taglinks, list):
        return taglinks
    return []


async def research_edhrec_targets(
    entries: dict[str, dict],
    names: list[str],
    *,
    vocab: dict,
    today: str,
) -> tuple[int, int, int, dict[str, object]]:
    """Fetch EDHREC tags for `names` and merge into `entries`. Mutates entries.

    Returns (researched, filled, unknown, transport). Unknown = fetch failed —
    not a zero. `transport` is edhrec_transport_check over ok/missing/failed
    so a 403-throttle cannot masquerade as an empty catalogue.
    """
    researched = filled = unknown = 0
    ok_n = missing_n = 0
    empty_transport = edhrec_transport_check(ok=0, missing=0, failed=0)
    if not names:
        return researched, filled, unknown, empty_transport
    print(f"EDHREC research pass on {len(names)} commanders…", flush=True)
    async with httpx.AsyncClient(timeout=25, headers=EDHREC_UA) as client:
        for i, name in enumerate(names, 1):
            status, taglinks = await fetch_edhrec_taglinks(name, client=client)
            await asyncio.sleep(0.12)
            if status == "unknown":
                unknown += 1
                if i % 100 == 0 or i == len(names):
                    print(f"  [edhrec-tags] {i}/{len(names)} "
                          f"researched={researched} filled={filled} "
                          f"unknown={unknown}", flush=True)
                continue
            if status == "missing":
                missing_n += 1
                taglinks = taglinks or []
            else:
                ok_n += 1
                taglinks = taglinks or []
            community = commanders.playstyles_from_edhrec_tags(
                taglinks, vocab=vocab,
            )
            researched += 1
            source = (
                f"EDHREC /commanders/{commanders.edhrec_slug(name)} "
                "tag_counts"
            )
            why_bits = []
            for row in taglinks:
                val = (row.get("value") or "").strip()
                pid = commanders.EDHREC_TAG_TO_PLAYSTYLE.get(val.lower())
                if pid in community:
                    why_bits.append(f"{val} {row.get('count')}")
            why = ("EDHREC tag_counts: " + ", ".join(why_bits)
                   if why_bits else
                   "EDHREC page fetched; no allowlisted tags")
            entries[name] = commanders.apply_edhrec_community(
                entries[name],
                community=community,
                community_source=source,
                community_why=why,
                researched_at=today,
                vocab=vocab,
            )
            if community:
                filled += 1
            if i % 100 == 0 or i == len(names):
                print(f"  [edhrec-tags] {i}/{len(names)} "
                      f"researched={researched} filled={filled} "
                      f"unknown={unknown}", flush=True)
    print(f"EDHREC research: researched={researched} "
          f"filled={filled} unknown={unknown} "
          f"ok={ok_n} missing={missing_n}", flush=True)
    transport = edhrec_transport_check(
        ok=ok_n, missing=missing_n, failed=unknown)
    if transport["suspicious"]:
        for reason in transport["reasons"]:
            print(f"EDHREC TRANSPORT CHECK FAILED: {reason}")
    else:
        print(
            f"EDHREC transport check: ok "
            f"(missing_rate={transport['missing_rate']:.1%}, "
            f"failed_rate={transport['failed_rate']:.1%})"
        )
    return researched, filled, unknown, transport


async def fetch_edhrec_taglinks(
    name: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, list[dict] | None]:
    """EDHREC community tags for one commander.

    Tries front-face slug, then back face for DFCs. Returns (status, payload):
      ("ok", list)       — HTTP 200 page (list may be empty = no allowlisted tags)
      ("missing", [])    — persistent 403/404 on every face; EDHREC has no page
      ("unknown", None)  — network/5xx after retries. Caller must not invent.
    Missing ≠ unknown. A throttle that 403s everything must not look like a
    catalogue of empty pages.
    """
    faces = ["front"]
    if " // " in (name or ""):
        faces.append("back")
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=25, headers=EDHREC_UA)
    try:
        saw_miss = False
        for face in faces:
            slug = commanders.edhrec_slug(name, face=face)
            if not slug:
                continue
            url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
            r = None
            for attempt in range(3):
                try:
                    r = await client.get(url)
                except Exception as e:
                    if attempt == 2:
                        print(f"  [edhrec-tags] {name}: request failed: {e}",
                              flush=True)
                        return "unknown", None
                    await asyncio.sleep(0.8 * (2 ** attempt))
                    continue
                if r.status_code == 429:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                if r.status_code in (500, 502, 503):
                    await asyncio.sleep(0.8 * (2 ** attempt))
                    continue
                break
            if r is None:
                return "unknown", None
            if r.status_code in (404, 403):
                saw_miss = True
                continue
            if r.status_code != 200:
                print(f"  [edhrec-tags] {name}: HTTP {r.status_code}",
                      flush=True)
                return "unknown", None
            try:
                data = r.json()
            except Exception as e:
                print(f"  [edhrec-tags] {name}: bad JSON: {e}", flush=True)
                return "unknown", None
            parsed = _parse_edhrec_taglinks(data)
            return "ok", (parsed if parsed is not None else [])
        # Every face 403/404 — looked, no EDHREC page.
        if saw_miss:
            return "missing", []
        return "unknown", None
    finally:
        if owns_client:
            await client.aclose()


def build_entry(
    *,
    name: str,
    color_identity: list[str],
    type_line: str,
    oracle_text: str,
    community: list[str],
    community_source: str,
    community_why: str,
    researched_at: str | None,
    vocab: dict,
    mechanics_at: str,
) -> dict | None:
    """One KB entry.

    Returns None only when oracle is unreadable AND there is no community
    source — caller decides whether that is a hard failure (full census) or a
    skip (offline fixture path).

    Entry-level method:
      * "both"    — community research present and mechanics ran
      * "derived" — mechanics only (including ways_to_play: [] = looked, none)
    Community without mechanics is never returned — that is a bug.
    """
    card = {
        "name": name,
        "oracle_text": oracle_text,
        "text": oracle_text,
        "type_line": type_line,
    }
    detected = playstyles.detect(card)
    derived: list[str] = []
    scores: dict[str, int] = {}
    signals: list[str] = []
    mechanics_unknown = detected.get("status") == "unknown"

    if not mechanics_unknown:
        derived = list(detected.get("matched") or [])
        scores = dict(detected.get("scores") or {})
        signals = list(detected.get("signals") or [])

    community_ok = [p for p in community if p in vocab]

    if community_ok and mechanics_unknown:
        # Surface — do not accept community rows with no derived profile.
        return {
            "_bug": "community_without_mechanics",
            "name": name,
        }

    if not community_ok and mechanics_unknown:
        return None

    if not community_ok and not derived:
        # Honest empty: we looked at oracle and found no playstyle.
        ways: list[dict] = []
        entry_method = "derived"
    else:
        ways = commanders.merge_ways(
            community=community_ok,
            derived=derived,
            scores=scores,
            vocab=vocab,
            community_source=community_source,
            community_why=community_why,
        )
        entry_method = "both" if community_ok else "derived"

    entry: dict = {
        "color_identity": list(color_identity or []),
        "ways_to_play": ways,
        "themes": commanders.themes_from_type_line(type_line),
        "signals": signals,
        "mechanics_status": "ok",
        "mechanics_at": mechanics_at,
        "method": entry_method,
    }
    if researched_at:
        entry["researched_at"] = researched_at
    return entry


def community_from_entry(
    entry: dict, vocab: dict[str, dict],
) -> tuple[list[str], str, str]:
    """Community playstyles + EDHREC metadata from an existing KB row."""
    community: list[str] = []
    source = ""
    why = ""
    for way in entry.get("ways_to_play") or []:
        if way.get("method") not in ("community", "both"):
            continue
        pid = way.get("playstyle")
        if pid in vocab and pid not in community:
            community.append(pid)
        if not source and way.get("source"):
            source = str(way["source"])
        if not why and way.get("why"):
            why = str(way["why"])
    return community, source, why


def _count_empty_ways(entries: dict) -> tuple[int, int, int]:
    empty_no_signals = 0
    empty_signals_no_playstyle = 0
    for e in entries.values():
        if e.get("ways_to_play"):
            continue
        if e.get("signals"):
            empty_signals_no_playstyle += 1
        else:
            empty_no_signals += 1
    return (
        empty_no_signals + empty_signals_no_playstyle,
        empty_no_signals,
        empty_signals_no_playstyle,
    )


async def mechanics_refresh_async(args: argparse.Namespace) -> int:
    """recompute mechanics signals/ways on existing KB without census."""
    if not OUT.is_file():
        print(f"missing {OUT} — nothing to refresh")
        return 1
    payload = json.loads(OUT.read_text())
    entries = payload.get("commanders") or {}
    if not isinstance(entries, dict) or not entries:
        print("KB has no commanders — refuse to refresh")
        return 1

    vocab = playstyles.load_playstyles()
    today = args.date or date.today().isoformat()
    names = sorted(entries)
    print(f"mechanics-refresh: {len(names)} commanders")

    cards_by_name: dict[str, dict] = {}
    if args.offline:
        print("offline: skip Scryfall — filter themes on stored lists only")
    else:
        print("fetching oracle/type_line from Scryfall (collection API)…")
        for i in range(0, len(names), 75):
            chunk = names[i : i + 75]
            got = await fetch_collection(chunk)
            cards_by_name.update(got)
            print(
                f"  batch {i // 75 + 1}: +{len(got)} "
                f"({len(cards_by_name)}/{len(names)})",
                flush=True,
            )

    updated = 0
    themes_only = 0
    fetch_miss = 0
    unreadable = 0
    bugs: list[str] = []

    for name in names:
        existing = entries[name]
        card = cards_by_name.get(name)

        if args.offline or not card:
            if not args.offline and not card:
                fetch_miss += 1
            old_themes = list(existing.get("themes") or [])
            new_themes = commanders.filter_theme_tokens(old_themes)
            if new_themes != old_themes:
                existing["themes"] = new_themes
                themes_only += 1
            continue

        community, comm_src, comm_why = community_from_entry(existing, vocab)
        oracle = oracle_of(card)
        type_line = type_line_of(card)
        ci = list(card.get("color_identity")
                  or existing.get("color_identity") or [])
        researched_at = existing.get("researched_at")

        entry = build_entry(
            name=name,
            color_identity=ci,
            type_line=type_line,
            oracle_text=oracle if isinstance(oracle, str) else "",
            community=community,
            community_source=comm_src,
            community_why=comm_why,
            researched_at=researched_at,
            vocab=vocab,
            mechanics_at=today,
        )
        if entry is None:
            unreadable += 1
            if type_line:
                existing["themes"] = commanders.themes_from_type_line(type_line)
                themes_only += 1
            continue
        if entry.get("_bug") == "community_without_mechanics":
            bugs.append(name)
            continue

        if existing.get("research_note"):
            entry["research_note"] = existing["research_note"]
        entries[name] = entry
        updated += 1

    if bugs:
        print(f"BUG: community without mechanics ({len(bugs)}):")
        for n in bugs[:20]:
            print(f"  - {n}")
        return 1

    empty_ways, empty_no_signals, empty_signals_no_playstyle = (
        _count_empty_ways(entries)
    )
    mechanics_count = sum(
        1 for e in entries.values() if e.get("mechanics_status") == "ok"
    )
    ways_count = sum(1 for e in entries.values() if e.get("ways_to_play"))
    researched_count = sum(1 for e in entries.values() if e.get("researched_at"))
    legal_total = int(payload.get("legal_estimate") or len(entries))

    payload.update({
        "researched_at_run": today,
        "mechanics_refresh_at": today,
        "mechanics": mechanics_count,
        "ways_to_play": ways_count,
        "researched": researched_count,
        "empty_ways": empty_ways,
        "empty_ways_no_signals": empty_no_signals,
        "empty_ways_signals_no_playstyle": empty_signals_no_playstyle,
        "commanders": entries,
    })

    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    commanders.clear_cache()
    cov = commanders.coverage(payload)
    rate = commanders.disagreement_rate(payload)
    print()
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"mechanics coverage:   {cov['mechanics_label']}")
    print(f"ways-to-play coverage: {cov['ways_to_play_label']}")
    print(f"research coverage:    {cov['research_label']}")
    print(f"empty ways: {empty_ways}")
    print(f"  {empty_no_signals}  no signals")
    print(f"  {empty_signals_no_playstyle}  signals, no playstyle")
    print(f"disagreement: both={rate['both']}  "
          f"community_only={rate['community_only']}  "
          f"derived_only={rate['derived_only']}  "
          f"(across {rate['commanders_with_ways']} commanders with ways)")
    print(f"refresh: updated={updated} themes_only={themes_only} "
          f"fetch_miss={fetch_miss} unreadable={unreadable}")
    if fetch_miss:
        print(f"  fetch_miss={fetch_miss} — those rows kept prior mechanics")
    return 0


async def main_async(args: argparse.Namespace) -> int:
    fixture = json.loads(FIXTURE.read_text())
    rows = fixture.get("commanders") or []
    vocab = playstyles.load_playstyles()
    catalog = load_catalog()
    today = args.date or date.today().isoformat()

    verified_rows = []
    excluded = []
    for row in rows:
        name = row.get("commander") or ""
        if squash(name) not in catalog:
            excluded.append(name)
            continue
        canonical = catalog[squash(name)]
        verified_rows.append({**row, "commander": canonical})

    if excluded:
        print(f"EXCLUDED (not in Scryfall catalog): {len(excluded)}")
        for n in excluded:
            print(f"  - {n}")

    community_by_name: dict[str, dict] = {
        r["commander"]: r for r in verified_rows
    }

    legal_cards: list[dict] = []
    legal_unknown = False
    if args.offline:
        print("offline: mechanics census skipped — fixture path only")
        # Pure offline: fixture oracle_text / type_line only. Incomplete by design.
        for row in verified_rows:
            legal_cards.append({
                "name": row["commander"],
                "oracle_text": row.get("oracle_text") or "",
                "type_line": row.get("type_line") or "",
                "color_identity": [],
            })
    else:
        print(f"fetching all legal commanders:\n  {LEGAL_QUERY}")
        fetched = await fetch_all_legal_commanders()
        if fetched is None:
            print("LEGAL FETCH UNKNOWN — refusing to write a partial census")
            return 1
        legal_cards = fetched
        print(f"fetched {len(legal_cards)} legal commanders")

    # Research cohort: fixture + optional EDHREC-ranked fills (marks only).
    research_names: set[str] = set(community_by_name)
    top_added = 0
    top_unknown = False
    edhrec_cards: dict[str, dict] = {}
    if args.top > 0 and not args.offline:
        print(f"fetching EDHREC top {args.top} for research cohort…")
        top_cards = await fetch_edhrec_top(args.top)
        if not top_cards:
            top_unknown = True
        for card in top_cards:
            name = card.get("name") or ""
            if not name or squash(name) not in catalog:
                continue
            canonical = catalog[squash(name)]
            edhrec_cards[canonical] = card
            if canonical not in research_names:
                research_names.add(canonical)
                top_added += 1

    entries: dict[str, dict] = {}
    bugs: list[str] = []
    unverified = []
    skipped_unreadable = []

    for card in legal_cards:
        raw_name = card.get("name") or ""
        if not raw_name:
            continue
        if squash(raw_name) not in catalog:
            unverified.append(raw_name)
            continue
        name = catalog[squash(raw_name)]
        row = community_by_name.get(name) or {}
        community = list(row.get("playstyles") or [])
        community_source = (
            row.get("source") or ("fixture" if row else "")
        )
        community_why = row.get("why") or ""
        # Prefer live Scryfall oracle; fall back to fixture text.
        oracle = oracle_of(card) or (row.get("oracle_text") or "")
        type_line = type_line_of(card) or (row.get("type_line") or "")
        ci = list(card.get("color_identity") or [])
        researched_at = today if name in research_names else None

        entry = build_entry(
            name=name,
            color_identity=ci,
            type_line=type_line,
            oracle_text=oracle if isinstance(oracle, str) else "",
            community=community,
            community_source=community_source,
            community_why=community_why,
            researched_at=researched_at,
            vocab=vocab,
            mechanics_at=today,
        )
        if entry is None:
            skipped_unreadable.append(name)
            continue
        if entry.get("_bug") == "community_without_mechanics":
            bugs.append(name)
            continue
        if name in research_names and name not in community_by_name:
            entry["research_note"] = (
                "EDHREC-ranked research cohort; no community classification yet."
            )
        entries[name] = entry

    # Fixture commanders must appear even if the bulk page somehow missed them
    # (should not happen — surface if it does).
    for row in verified_rows:
        name = row["commander"]
        if name in entries:
            continue
        print(f"  fixture miss in bulk fetch, collecting: {name}")
        if args.offline:
            oracle = row.get("oracle_text") or ""
            type_line = row.get("type_line") or ""
            ci: list[str] = []
        else:
            got = await fetch_collection([name])
            scry = got.get(name) or {}
            oracle = oracle_of(scry) or (row.get("oracle_text") or "")
            type_line = type_line_of(scry) or (row.get("type_line") or "")
            ci = list(scry.get("color_identity") or [])
        entry = build_entry(
            name=name,
            color_identity=ci,
            type_line=type_line,
            oracle_text=oracle if isinstance(oracle, str) else "",
            community=list(row.get("playstyles") or []),
            community_source=row.get("source") or "fixture",
            community_why=row.get("why") or "",
            researched_at=today,
            vocab=vocab,
            mechanics_at=today,
        )
        if entry is None:
            skipped_unreadable.append(name)
            continue
        if entry.get("_bug") == "community_without_mechanics":
            bugs.append(name)
            continue
        entries[name] = entry

    if bugs:
        print(f"BUG: community without mechanics ({len(bugs)}):")
        for n in bugs[:20]:
            print(f"  - {n}")
        print("Refusing to write — mechanics half is free and must run.")
        return 1

    if unverified:
        print(f"EXCLUDED unverified names: {len(unverified)}")
        for n in unverified[:10]:
            print(f"  - {n}")

    if skipped_unreadable:
        print(f"UNREADABLE oracle (no entry): {len(skipped_unreadable)}")
        for n in skipped_unreadable[:10]:
            print(f"  - {n}")

    # B: research pass — EDHREC tags for empties (+ derived-only grow).
    # Mechanics already ran; this only adds community labels. Fetch failure
    # is unknown (skip), not "no playstyles".
    edhrec_researched = 0
    edhrec_filled = 0
    edhrec_unknown = 0
    if (args.research_empty or args.research_derived) and not args.offline:
        targets = commanders.research_targets(
            entries,
            include_empty=bool(args.research_empty),
            include_derived=bool(args.research_derived),
            limit=int(args.research_empty_limit or 0),
        )
        edhrec_researched, edhrec_filled, edhrec_unknown, transport = (
            await research_edhrec_targets(
                entries, targets, vocab=vocab, today=today,
            )
        )
        if transport["suspicious"]:
            # Do not write a catalogue shaped by a throttle.
            return 1
    elif args.offline:
        print("offline: EDHREC research pass skipped")

    mechanics_count = sum(
        1 for e in entries.values() if e.get("mechanics_status") == "ok"
    )
    ways_count = sum(1 for e in entries.values() if e.get("ways_to_play"))
    researched_count = sum(1 for e in entries.values() if e.get("researched_at"))
    legal_total = len(entries) if args.offline else len(legal_cards) - len(unverified)
    # Prefer measured fetch size when online and complete.
    if not args.offline and not skipped_unreadable and not unverified:
        legal_total = len(entries)

    if not args.offline and mechanics_count != legal_total:
        print(
            f"BUG: mechanics coverage {mechanics_count} of {legal_total} "
            f"— must be 100%. skipped_unreadable={len(skipped_unreadable)} "
            f"unverified={len(unverified)}"
        )
        # Still write so the gap is inspectable, but fail the process.
        fail = True
    else:
        fail = False

    # Empty-ways split: no signals vs signals-but-no-playstyle.
    empty_no_signals = 0
    empty_signals_no_playstyle = 0
    for e in entries.values():
        if e.get("ways_to_play"):
            continue
        if e.get("signals"):
            empty_signals_no_playstyle += 1
        else:
            empty_no_signals += 1

    payload = {
        "version": 1,
        "note": (
            "commander knowledge base. Mechanics from "
            "playstyles.detect() over every legal commander; community "
            "classifications from testdata/review/commander-playstyles.json "
            "plus EDHREC-ranked research cohort. Disagreement is data. "
            "Absent ≠ empty. Mechanics coverage must be 100%; ways-to-play and "
            "research may be partial and are reported separately."
        ),
        "researched_at_run": today,
        "legal_estimate": legal_total,
        "legal_estimate_query": LEGAL_QUERY,
        "legal_estimate_note": (
            "Measured against Scryfall, not assumed. Matches "
            "legal_commanders_only() in app.py: a legendary creature, or a card "
            "that says it can be your commander. Do not substitute is:commander "
            "or bare t:legendary t:creature — they disagree by hundreds."
        ),
        "mechanics": mechanics_count,
        "ways_to_play": ways_count,
        "researched": researched_count,
        "empty_ways": empty_no_signals + empty_signals_no_playstyle,
        "empty_ways_no_signals": empty_no_signals,
        "empty_ways_signals_no_playstyle": empty_signals_no_playstyle,
        "fixture_rows": len(verified_rows),
        "edhrec_top_added": top_added,
        "edhrec_top_unknown": top_unknown,
        "edhrec_empty_researched": edhrec_researched,
        "edhrec_empty_filled": edhrec_filled,
        "edhrec_empty_unknown": edhrec_unknown,
        "excluded_unverified": excluded + unverified,
        "commanders": entries,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    commanders.clear_cache()

    rate = commanders.disagreement_rate(payload)
    cov = commanders.coverage(payload)
    print()
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"mechanics coverage:   {cov['mechanics_label']}"
          f"   ({cov['mechanics_pct']:.1f}%)")
    print(f"ways-to-play coverage: {cov['ways_to_play_label']}")
    print(f"research coverage:    {cov['research_label']}")
    print(f"empty ways: {payload['empty_ways']}")
    print(f"  {empty_no_signals}  no signals")
    print(f"  {empty_signals_no_playstyle}  signals, no playstyle")
    print(f"disagreement: both={rate['both']}  "
          f"community_only={rate['community_only']}  "
          f"derived_only={rate['derived_only']}  "
          f"(across {rate['commanders_with_ways']} commanders with ways)")
    if top_unknown:
        print("edhrec top: UNKNOWN (search failed) — not scored as zero fills")
    if args.offline:
        print("offline run: mechanics census incomplete by design")
    return 1 if fail else 0


async def research_patch_async(args: argparse.Namespace) -> int:
    """B grow: patch existing KB with EDHREC community tags only."""
    if not OUT.is_file():
        print(f"missing {OUT} — run a full build first")
        return 1
    payload = json.loads(OUT.read_text())
    entries = payload.get("commanders") or {}
    if not isinstance(entries, dict) or not entries:
        print("KB has no commanders — refuse to patch")
        return 1
    vocab = playstyles.load_playstyles()
    today = args.date or date.today().isoformat()
    targets = commanders.research_targets(
        entries,
        include_empty=bool(args.research_empty),
        include_derived=bool(args.research_derived),
        limit=int(args.research_empty_limit or 0),
    )
    print(f"research-patch: {len(targets)} targets "
          f"(empty={args.research_empty}, derived={args.research_derived})")
    researched, filled, unknown, transport = await research_edhrec_targets(
        entries, targets, vocab=vocab, today=today,
    )
    if transport["suspicious"]:
        return 1

    empty_no_signals = 0
    empty_signals_no_playstyle = 0
    for e in entries.values():
        if e.get("ways_to_play"):
            continue
        if e.get("signals"):
            empty_signals_no_playstyle += 1
        else:
            empty_no_signals += 1
    mechanics_count = sum(
        1 for e in entries.values() if e.get("mechanics_status") == "ok"
    )
    ways_count = sum(1 for e in entries.values() if e.get("ways_to_play"))
    researched_count = sum(1 for e in entries.values() if e.get("researched_at"))
    legal_total = int(payload.get("legal_estimate") or len(entries))

    payload.update({
        "researched_at_run": today,
        "mechanics": mechanics_count,
        "ways_to_play": ways_count,
        "researched": researched_count,
        "empty_ways": empty_no_signals + empty_signals_no_playstyle,
        "empty_ways_no_signals": empty_no_signals,
        "empty_ways_signals_no_playstyle": empty_signals_no_playstyle,
        "edhrec_empty_researched": researched,
        "edhrec_empty_filled": filled,
        "edhrec_empty_unknown": unknown,
        "commanders": entries,
    })
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    commanders.clear_cache()
    cov = commanders.coverage(payload)
    rate = commanders.disagreement_rate(payload)
    print()
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"mechanics coverage:   {cov['mechanics_label']}")
    print(f"ways-to-play coverage: {cov['ways_to_play_label']}")
    print(f"research coverage:    {cov['research_label']}")
    print(f"empty ways: {payload['empty_ways']}")
    print(f"  {empty_no_signals}  no signals")
    print(f"  {empty_signals_no_playstyle}  signals, no playstyle")
    print(f"disagreement: both={rate['both']}  "
          f"community_only={rate['community_only']}  "
          f"derived_only={rate['derived_only']}  "
          f"(across {rate['commanders_with_ways']} commanders with ways)")
    print(f"patch this run: researched={researched} filled={filled} "
          f"unknown={unknown}")
    return 0 if unknown == 0 or filled > 0 or researched > 0 else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=50,
                    help="mark this many EDHREC-ranked commanders as research cohort")
    ap.add_argument("--offline", action="store_true",
                    help="fixture only; skip full legal-commander fetch")
    ap.add_argument("--date", default=None,
                    help="researched_at / mechanics_at stamp (default: today)")
    ap.add_argument("--research-empty", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="pull EDHREC tags for empty-ways commanders")
    ap.add_argument("--research-derived", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="B grow: EDHREC tags for derived-only ways "
                         "(no community yet) — merges as both/community")
    ap.add_argument("--research-empty-limit", type=int, default=0,
                    help="cap EDHREC research targets (0 = all)")
    ap.add_argument("--research-patch", action="store_true",
                    help="patch existing data/commanders.json only "
                         "(no Scryfall census)")
    ap.add_argument("--mechanics-refresh", action="store_true",
                    help="recompute mechanics on existing KB "
                         "(collection fetch, no census/EDHREC)")
    args = ap.parse_args()
    if args.offline:
        args.top = 0
        args.research_empty = False
        args.research_derived = False
    if args.research_patch:
        return asyncio.run(research_patch_async(args))
    if args.mechanics_refresh:
        return asyncio.run(mechanics_refresh_async(args))
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
