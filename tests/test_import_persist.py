#!/usr/bin/env python3
"""CON-309: imported decks must persist; Save must not swallow a failed write.

Brad imported a list, tapped Save, and the button said Saving… then quietly
went back to Save. Root cause: analyze_imported_deck never called
persist_deck, so the response had no id; PATCH /api/saved/{id} 404'd into a
catch that said nothing.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

SAVE_FAIL_WHAT = "Couldn’t save this deck."


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        bad(label, extra or "expected truthy")


def _cmdr_card(name="Admiral Beckett Brass"):
    return {
        "name": name,
        "type_line": "Legendary Creature — Human Pirate",
        "oracle_text": "Other Pirates you control get +1/+1.",
        "cmc": 3,
        "mana_cost": "{1}{U}{B}{R}",
        "color_identity": ["U", "B", "R"],
        "colors": ["U", "B", "R"],
        "legal": "legal",
        "usd": "1.00",
        "keywords": [],
        "layout": "normal",
    }


def _filler(n=99):
    return [
        {
            "name": f"Sol Ring {i}" if i else "Sol Ring",
            "type_line": "Artifact",
            "oracle_text": "{T}: Add {C}{C}.",
            "cmc": 1,
            "mana_cost": "{1}",
            "color_identity": [],
            "colors": [],
            "legal": "legal",
            "usd": "0.10",
            "keywords": [],
            "layout": "normal",
        }
        for i in range(n)
    ]


def _import_result(deck=None, cmdr=None, *, pick_status="found"):
    cmdr = cmdr or _cmdr_card()
    deck = deck if deck is not None else _filler(99)
    return {
        "unresolved": [],
        "commander_pick": {
            "status": pick_status,
            "commander": cmdr["name"] if pick_status == "found" else None,
            "partner": None,
            "candidates": [],
            "reason": None if pick_status == "found" else "no commander",
        },
        "entries": [{"name": c["name"]} for c in deck],
        "deck": deck,
        "commander_card": cmdr,
        "partner_card": None,
        "commander_cards": [cmdr],
        "resolved": {cmdr["name"]: cmdr},
    }


async def _analyze(result, brief=None, card_pool="tournament_legal", **kw):
    from deckwright import app
    from deckwright import overview
    from deckwright import llm

    brief = brief if brief is not None else {"theme": "pirates"}
    with patch.object(overview, "run_build_sim", return_value={}), \
         patch.object(llm, "can_call", return_value=False):
        return await app.analyze_imported_deck(result, brief, card_pool, **kw)


def test_import_persists_and_patchable():
    """Import → file in SAVE_DIR, id/saved_at on response, PATCH renames."""
    print("\nimport persist  — CON-309 analyze_imported_deck writes a saved deck")
    from deckwright import app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        was = app.SAVE_DIR
        app.SAVE_DIR = d
        try:
            out = asyncio.run(_analyze(_import_result()))
            slug = out.get("id")
            true("import response carries id", bool(slug), repr(slug))
            true("import response carries saved_at", bool(out.get("saved_at")),
                 repr(out.get("saved_at")))
            path = d / f"{slug}.json"
            true("file exists in SAVE_DIR", path.exists(), str(path))
            listed = app.list_saved_decks(d)
            true("appears in list_saved_decks",
                 any(i["id"] == slug for i in listed),
                 repr([i["id"] for i in listed]))

            client = TestClient(app.app)
            r = client.get("/api/saved")
            eq("GET /api/saved 200", r.status_code, 200)
            true("GET /api/saved includes id",
                 any(i.get("id") == slug for i in r.json()),
                 repr(r.json()))

            r = client.patch(f"/api/saved/{slug}", json={"name": "Brass Heist"})
            eq("PATCH /api/saved/{id} 200", r.status_code, 200)
            eq("PATCH applies name", (r.json() or {}).get("name"), "Brass Heist")
            loaded = app.load_saved_deck(slug, d) or {}
            eq("disk name after PATCH", loaded.get("name"), "Brass Heist")

            # Illegal list still persists — violations intact, not weakened.
            illegal = _import_result(deck=_filler(10))  # wrong size
            bad_out = asyncio.run(_analyze(illegal))
            true("illegal import still gets id", bool(bad_out.get("id")))
            true("illegal import keeps violations",
                 bool(bad_out.get("violations")),
                 repr(bad_out.get("violations")))
            true("illegal import legal flag is false",
                 bad_out.get("legal") is False)
            on_disk = app.load_saved_deck(bad_out["id"], d)
            true("illegal deck file on disk", on_disk is not None)
            true("disk keeps violations",
                 bool((on_disk or {}).get("violations")),
                 repr((on_disk or {}).get("violations")))
        finally:
            app.SAVE_DIR = was


def test_missing_commander_does_not_persist():
    """Ambiguous / missing commander is not a saved deck."""
    print("\nimport persist  — CON-309 no persist when commander not found")
    from deckwright import app

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        was = app.SAVE_DIR
        app.SAVE_DIR = d
        try:
            result = _import_result(pick_status="missing")
            out = asyncio.run(_analyze(result))
            eq("no id when commander missing", out.get("id"), None)
            eq("no files written", list(d.glob("*.json")), [])
        finally:
            app.SAVE_DIR = was


def test_build_still_persists_once():
    """/api/build persistence unchanged — one write, not a double stamp."""
    print("\nimport persist  — CON-309 /api/build still single persist_deck")
    from deckwright import app

    src = Path(app.__file__).read_text(encoding="utf-8")
    # Count call sites that invoke persist_deck( — definition excluded.
    calls = re.findall(r"(?<!def )persist_deck\s*\(", src)
    true("persist_deck called from build and import paths",
         len(calls) >= 2, f"call sites={len(calls)}")
    # The build handler must still call it directly (regression guard).
    build_idx = src.find('async def build(')
    if build_idx < 0:
        build_idx = src.find('@app.post("/api/build")')
    true("build route present", build_idx >= 0)
    window = src[build_idx:build_idx + 8000]
    # Find the build function body until the next top-level async def or def at column 0
    # after the decorator block — look for persist_deck before analyze_imported_deck def.
    analyze_idx = src.find("async def analyze_imported_deck")
    build_chunk = src[build_idx:analyze_idx] if analyze_idx > build_idx else window
    true("build calls persist_deck", "persist_deck(" in build_chunk, build_chunk[-200:])
    eq("build calls persist_deck once", build_chunk.count("persist_deck("), 1)

    analyze_end = src.find("\nasync def compute_coverage", analyze_idx)
    analyze_chunk = src[analyze_idx:analyze_end if analyze_end > 0 else analyze_idx + 6000]
    true("analyze_imported_deck calls persist_deck",
         "persist_deck(" in analyze_chunk, analyze_chunk[-300:])
    eq("analyze_imported_deck calls persist_deck once",
       analyze_chunk.count("persist_deck("), 1)


def _extract_save_helpers(html: str) -> str:
    """Pull CON-309 save-failure helpers out of index.html for node eval."""
    start = html.find("const SAVE_FAIL_WHAT")
    if start < 0:
        # Fall back: function saveFailureCopy
        start = html.find("function saveFailureCopy")
    if start < 0:
        raise AssertionError("saveFailureCopy / SAVE_FAIL_WHAT missing from index.html")
    # Take through saveIdentity's closing brace — stop before next major fn.
    end = html.find("/* ── CON-97 / CON-98:", start)
    if end < 0:
        end = html.find("async function resolvePasted", start)
    if end < 0:
        raise AssertionError("could not bound save helpers in index.html")
    return html[start:end]


def test_save_failure_ui_message():
    """Failed Save must say so — assert the message, not that nothing threw."""
    print("\nsaveIdentity  — CON-309 failed write is visible")
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    true("saveFailureCopy defined", "function saveFailureCopy" in html)
    true("SAVE_FAIL_WHAT constant present", "SAVE_FAIL_WHAT" in html or
         SAVE_FAIL_WHAT in html)

    fn = html.find("async function saveIdentity")
    true("saveIdentity present", fn >= 0)
    body = html[fn:fn + 1800]
    true("saveIdentity checks response.ok", "r.ok" in body or "!r.ok" in body, body[:400])
    true("saveIdentity catch calls showSaveFailure",
         "showSaveFailure" in body, body)
    true("showSaveFailure uses fail('save')",
         "fail('save'" in html or 'fail("save"' in html)
    # The empty catch that only resets the button is the bug.
    true(
        "saveIdentity catch is not a silent reset",
        not re.search(
            r"catch\s*\(\s*e\s*\)\s*\{\s*btn\.textContent\s*=\s*was;\s*btn\.disabled\s*=\s*false;\s*\}",
            body,
        ),
        "catch still only resets the button",
    )

    # Assert the copy itself via node — message, not merely "no throw".
    helpers = _extract_save_helpers(html)
    harness = helpers + """
const e = { message: 'not found', status: 404 };
const copy = saveFailureCopy(e);
if (!copy || !copy.what || !copy.how) {
  process.stderr.write('saveFailureCopy missing what/how\\n');
  process.exit(2);
}
process.stdout.write(JSON.stringify(copy));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run(
            ["node", path],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        bad("node saveFailureCopy", proc.stderr or proc.stdout or str(proc.returncode))
        return
    copy = json.loads(proc.stdout)
    eq("failure what names the save", copy.get("what"), SAVE_FAIL_WHAT)
    true("failure how says it is not saved",
         "not in Saved decks" in (copy.get("how") or "")
         or "lose" in (copy.get("how") or "").lower(),
         repr(copy.get("how")))
    true("failure message keeps server evidence",
         "not found" in (copy.get("message") or ""),
         repr(copy.get("message")))


def main() -> int:
    test_import_persists_and_patchable()
    test_missing_commander_does_not_persist()
    test_build_still_persists_once()
    test_save_failure_ui_message()
    print(
        f"\n{GREEN}{passed} passed{OFF}"
        + (f", {RED}{failed} FAILED{OFF}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
