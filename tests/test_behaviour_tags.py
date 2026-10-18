"""
test_behaviour_tags.py — CON-151 verified otag: behaviour vocabulary.

Offline unit tests for the build gate shape and resolve_term() behaviour
branch. Live Scryfall probes belong in scripts/build_behaviour_tags.py.

Run:  python test_behaviour_tags.py
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deckwright import app

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def _load_builder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_behaviour_tags.py"
    spec = importlib.util.spec_from_file_location("build_behaviour_tags", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_checked_in_file_shape():
    """data/behaviour_tags.json carries verified tags, aliases, synonyms."""
    print("\nbehaviour_tags.json  — CON-151 shape")
    path = Path(__file__).resolve().parents[1] / "data" / "behaviour_tags.json"
    eq("file exists", path.is_file(), True)
    data = json.loads(path.read_text())
    eq("version", data.get("version"), 1)
    tags = data.get("tags") or []
    (ok if len(tags) >= 30 else bad)("≥30 verified tags", str(len(tags)))
    by = {t["tag"]: t for t in tags}
    eq("theft present", "theft" in by, True)
    eq("stax absent", "stax" in by, False)
    theft = by.get("theft") or {}
    syns = {app.norm_term(s) for s in (theft.get("synonyms") or [])}
    (ok if "steal" in syns and "stealing" in syns else bad)(
        "theft synonyms include steal/stealing", str(theft.get("synonyms")))
    (ok if isinstance(theft.get("cards"), int) and theft["cards"] >= 1 else bad)(
        "theft cards ≥ 1", str(theft.get("cards")))
    bw = by.get("boardwipe") or {}
    aliases = set(bw.get("aliases") or [])
    (ok if "sweeper" in aliases else bad)("sweeper is boardwipe alias", str(aliases))
    (ok if "sweeper" not in by else bad)("sweeper not a duplicate tag entry", str(by.keys()))
    fl = by.get("flicker") or {}
    (ok if "blink" in set(fl.get("aliases") or []) else bad)(
        "blink is flicker alias", str(fl.get("aliases")))
    (ok if "blink" not in by else bad)("blink not a duplicate tag entry", "")


def test_resolve_term_behaviour_before_catalogs():
    """steal → otag:theft; stax unresolved; ramp verified."""
    print("\nresolve_term()  — CON-151 behaviour branch")
    app.load_behaviour_tags(force=True)

    steal = app.resolve_term("steal")
    eq("steal operator", steal.get("operator"), "otag")
    eq("steal value", steal.get("value"), "theft")
    eq("steal catalog", steal.get("catalog"), "behaviour")
    (ok if steal.get("confidence") != "unresolved" else bad)(
        "steal is resolved", str(steal))

    for form in ("stealing", "theft", "STEAL", "Steal"):
        r = app.resolve_term(form)
        (ok if r.get("operator") == "otag" and r.get("value") == "theft" else bad)(
            f"{form!r} → otag:theft", str(r))

    stax = app.resolve_term("stax")
    eq("stax operator", stax.get("operator"), None)
    eq("stax confidence", stax.get("confidence"), "unresolved")

    ramp = app.resolve_term("ramp")
    eq("ramp operator", ramp.get("operator"), "otag")
    eq("ramp value", ramp.get("value"), "ramp")

    wipe = app.resolve_term("wipe")
    eq("wipe → boardwipe", wipe.get("value"), "boardwipe")
    wrath = app.resolve_term("wrath")
    eq("wrath → boardwipe", wrath.get("value"), "boardwipe")

    # keyword:steal must not win — behaviour is checked first.
    # Even if a future catalog lists steal, resolve_term keeps otag:theft.
    with patch.dict(app._vocab, {"keyword": {"steal": "Steal"}, "type": {}}, clear=False):
        again = app.resolve_term("steal")
        eq("behaviour beats keyword catalog", again.get("value"), "theft")
        eq("not keyword", again.get("operator"), "otag")


def test_theme_clauses_uses_behaviour_map():
    """theme_clauses emits the verified tag, not a guessed otag:steal."""
    print("\ntheme_clauses + behaviour  — CON-151")
    app.load_behaviour_tags(force=True)
    clauses = app.theme_clauses("steal")
    eq("steal leads with otag:theft", clauses[0] if clauses else "", "otag:theft")
    (ok if "otag:steal" not in clauses else bad)(
        "no dead otag:steal", str(clauses))
    ramp = app.theme_clauses("ramp")
    eq("ramp still otag:ramp", ramp[0] if ramp else "", "otag:ramp")
    wipe = app.theme_clauses("wipe")
    # wipe / boardwipe / sweeper are Scryfall aliases for the same 934-card
    # pool. Assert the family, not which alias the vocabulary happened to
    # store — otherwise a harmless regeneration flips the test.
    (ok if wipe and wipe[0] in ("otag:wipe", "otag:boardwipe", "otag:sweeper")
     else bad)("wipe → the 934-card boardwipe pool", str(wipe[:2]))


def test_build_gate_refuses_dead_and_zero():
    """Build fails loudly on 404/zero; never writes unverified tags."""
    print("\nbuild gate  — CON-151 refuse dead tags")
    mod = _load_builder()

    async def fake_probe(tag: str):
        # theft lives; stax and card-draw are dead; empty returns zero.
        if tag == "theft":
            return "ok", 716
        if tag in ("stax", "card-draw"):
            return "zero", 0
        if tag == "unreachable":
            return "unknown", None
        return "zero", 0

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "behaviour_tags.json"
        # Dead tag in the source list → exit 1, no write.
        code = __import__("asyncio").run(mod.build(
            candidates=[{"tag": "stax", "aliases": [], "synonyms": ["stax"]}],
            out_path=out,
            probe=fake_probe,
        ))
        eq("dead tag fails build", code, 1)
        eq("dead tag writes nothing", out.exists(), False)

        code = __import__("asyncio").run(mod.build(
            candidates=[{"tag": "theft", "aliases": [], "synonyms": ["steal", "theft"]}],
            out_path=out,
            probe=fake_probe,
        ))
        eq("live tag succeeds", code, 0)
        eq("live tag writes file", out.exists(), True)
        payload = json.loads(out.read_text())
        eq("written tag", payload["tags"][0]["tag"], "theft")
        eq("written cards", payload["tags"][0]["cards"], 716)
        syns = payload["tags"][0]["synonyms"]
        (ok if "steal" in syns else bad)("synonyms preserved", str(syns))

        out.unlink()
        code = __import__("asyncio").run(mod.build(
            candidates=[{"tag": "unreachable", "aliases": [], "synonyms": []}],
            out_path=out,
            probe=fake_probe,
        ))
        eq("unknown probe refuses write", code, 2)
        eq("unknown writes nothing", out.exists(), False)

        # Alias must also probe live — a dead alias fails the build.
        out.unlink(missing_ok=True)
        code = __import__("asyncio").run(mod.build(
            candidates=[{
                "tag": "theft",
                "aliases": ["stax"],
                "synonyms": ["theft"],
            }],
            out_path=out,
            probe=fake_probe,
        ))
        eq("dead alias fails build", code, 1)
        eq("dead alias writes nothing", out.exists(), False)


def test_norm_term():
    """Lookup keys strip punctuation the same way CON-149 will."""
    print("\nnorm_term  — shared normalisation")
    eq("board wipe", app.norm_term("board wipe"), "boardwipe")
    eq("extra-turn", app.norm_term("extra-turn"), "extraturn")
    eq("Steal", app.norm_term("Steal"), "steal")


def main() -> int:
    test_norm_term()
    test_checked_in_file_shape()
    test_build_gate_refuses_dead_and_zero()
    test_resolve_term_behaviour_before_catalogs()
    test_theme_clauses_uses_behaviour_map()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
