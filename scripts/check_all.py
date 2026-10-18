#!/usr/bin/env python3
"""one entry point for the three check tiers.

    python scripts/check_all.py --tier 1   # every commit — seconds, no network
    python scripts/check_all.py --tier 2   # every PR — network, no model quota
    python scripts/check_all.py --tier 3   # before ship — model + Forge

A layer that cannot run is SKIP, and a SKIP fails the tier. An unknown is not
a zero: a skipped Playwright must never look like a passing Playwright.

Exit codes:
    0  every layer in the tier ran and passed
    1  at least one layer ran and failed
    2  no failures, but at least one layer could not run (incomplete)
    2  also used for bad CLI usage
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
EXIT_OK, EXIT_FAIL, EXIT_INCOMPLETE = 0, 1, 2

GREEN, RED, YELLOW, DIM, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m",
)


@dataclass
class Result:
    name: str
    status: str          # PASS | FAIL | SKIP
    detail: str
    seconds: float = 0.0


@dataclass
class Layer:
    name: str
    tier: int
    summary: str
    run: Callable[[], Result]
    # opt-in layers do not run unless asked. The ship gate is
    # differential + Forge; e2e_flow is a cheaper single-wish smoke that
    # duplicates the chain differential already walks. Keeping it in the
    # default tier 3 spent model quota to re-answer a question differential
    # answers better. It stays available (--with-optional), it is just not
    # the gate.
    optional: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _python() -> str:
    return sys.executable


def _run(cmd: list[str], *, cwd: Path | None = None,
         env: dict | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd or ROOT), env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def _combine_output(proc: subprocess.CompletedProcess) -> str:
    parts = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append(proc.stderr.rstrip())
    return "\n".join(parts)


def _tail(text: str, n: int = 8) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) if lines else "(no output)"


def extract_index_script(index_path: Path) -> str:
    """Pull the inline <script> body out of index.html for node --check."""
    html = index_path.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>\s*</body>", html, re.S | re.I)
    if not m:
        raise ValueError("no <script>…</script> block found before </body>")
    return m.group(1)


def _which(name: str) -> str | None:
    return shutil.which(name)


# ---------------------------------------------------------------------------
# layer runners
# ---------------------------------------------------------------------------

# Offline unit files. test_perception.py is a separate Tier-1 layer.
# Flags keep each file off the network.
#
# every tests/test_*.py must land in ONE of two places —
#   1. _UNIT_COMMANDS  — it runs in tier 1
#   2. _UNIT_EXCLUDED  — named opt-out with a reason
# test_unit_registry.py (listed below) asserts that split. It does not call
# ca.main(), so it can live in this list without recursing. test_check_all.py
# still cannot: several of its cases drive run_tier / main.
TESTS_DIR = ROOT / "tests"
_UNIT_COMMANDS: list[list[str]] = [
    ["test_deckwright.py", "--unit"],
    ["test_mechanics.py"],
    ["test_plan_graph.py"],
    ["test_behaviour_tags.py"],
    ["test_commander_difficulty.py"],
    ["test_backend_spine.py"],
    ["test_resolve.py"],
    ["test_readiness.py", "--unit"],
    ["test_readback.py", "--unit"],
    ["test_forge_log.py"],
    ["test_forge_calibrate.py"],
    ["test_prompts.py", "--offline"],
    ["test_empty_brief_refusal.py"],   # tier 1 missed a P1 regression
    ["test_playstyles.py"],           # vocabulary — offline
    ["test_expert_hei_bai_split.py"], # chosen/mentioned — offline
    ["test_synergy.py"],  # synergy graph — offline, no model
    ["test_curve.py"],    # curve / playability — offline, no model
    ["test_hei_bai_baseline_probes.py"],  # leave-cause probes — offline
    # test_expert_hei_bai_split.py was listed twice (here and above).
    # A system audit recorded it as dropped; the code still had both. The map read
    # the code instead of the card and caught the drift. One entry, line 121.
    ["test_commanders_kb.py"],  # commander knowledge base — offline
    ["test_edhrec_sanity.py"],  # missing-rate is transport, not catalogue
    ["test_differential_harness.py"],  # was offline and unlisted
    # This file was once added without being registered, so tier 1 reported
    # green while it never ran. test_check_all caught it. Same shape as the
    # finding where the runner said 3 pass with a P1 regression live.
    ["test_precon_upgrade.py"],  # precon ways + beef-it-up — offline
    ["test_commander_fit.py"],  # theme+strategy fit — offline
    ["test_way_to_play.py"],    # way_to_play binds plan — offline
    ["test_deck_accounting.py"],   # theme/strategy buckets — offline
    ["test_backfill_order.py"],  # glued backfill + singleton placement

    # Registered in one sweep after test_check_all.py was run by hand during
    # one integration (sixteen offline files were missing; the structural
    # fix is test_unit_registry.py, which now gates the list).
    ["test_instrument_chat.py"],
    ["test_narrowing.py"],
    ["test_know_magic_lap.py"],
    ["test_know_magic_lap2.py"],
    ["test_know_magic_lap4.py"],
    ["test_llm_max_meter.py"],
    ["test_oracle_coverage.py"],
    ["test_print_wave_reeval.py"],
    ["test_saved_ask_instrument.py"],
    ["test_thesis_glued.py"],
    ["test_thesis_goldfish_honesty.py"],   # once shipped unregistered
    ["test_goldfish_plan_compute.py"],     # plan thesis conditions must move
    ["test_unread_gap_research.py"],
    ["test_unread_gap_strategy.py"],
    ["test_way_preference.py"],
    ["test_commander_oracle_cast.py"],     # commander on-cast modelling
    ["test_commander_paging.py"],          # commander paging
    ["test_commander_differs.py"],         # picker differs without prose
    ["test_parse_json_peel.py"],           # JSON trailing-prose peel

    # registry coverage — no ca.main() / run_tier, safe in this list.
    ["test_unit_registry.py"],
    # API boundary — unread emits / unsent reads / inert data-*.
    ["test_api_boundary.py"],
    # bug reports — disk first, Linear stamp / linear_error.
    ["test_bug_report.py"],
    ["test_demo_guard.py"],
    ["test_footer_logo.py"],  # quiet footer shows DeckWright logo lockup
    ["test_scryfall_refusal.py"],  # name challenge / rate limit / timeout
    ["test_corpus.py"],  # local Scryfall corpus — offline
    ["test_import_empty_message.py"],  # empty import says the real cause
    ["test_import_punctuation.py"],  # curly apostrophes resolve on import
    ["test_security_hardening.py"],  # path ids, error bodies, regex speed
    ["test_backrow_labels.py"],  # no twin Start over on failure screens
    ["test_import_persist.py"],  # import persists; Save failure is visible
    ["test_green_isolation.py"],  # green SAVE_DIR / corpus / badge / plist
    ["test_commander_strategy.py"],  # B clarity extractors + audit
    ["test_strategy_steer.py"],  # soft steer + otag rollup
    ["test_bound_coverage.py"],  # derive bound coverage sign-off
    ["test_cardfacts.py"],  # CardFacts extract/bake/lookup — offline
    ["test_edhrec_seed.py"],  # EDHREC seed client + pool injection
    ["test_v2_benchmark.py"],  # bake-off harness offline
    ["test_b_vs_cardfacts.py"],  # B stamps vs CardFacts compare — offline
    ["test_candidate_c.py"],  # Candidate C CardFacts audit/steer wire

    # NOT test_check_all.py — see _UNIT_EXCLUDED. The house-rule suite still
    # exists; run it directly: `python tests/test_check_all.py`.
]


# tests/test_*.py files that must NOT appear in _UNIT_COMMANDS. Value = why.
# adding a new offline test and skipping both lists fails tier 1 via
# test_unit_registry.py. Touch this map only for a deliberate opt-out.
_UNIT_EXCLUDED: dict[str, str] = {
    "test_check_all.py": (
        "drives ca.main()/run_tier; listing it under run_unit() recurses "
        "(measured 10 min hang on a 9s tier)"
    ),
    "test_perception.py": "separate Tier-1 perception layer (run_perception)",
}


def unregistered_offline_tests(root: Path | None = None) -> list[str]:
    """Offline test_*.py names in neither _UNIT_COMMANDS nor _UNIT_EXCLUDED.

    the merge gate runs this via test_unit_registry.py. An unknown
    file is a gap, not a silent pass.
    """
    base = root if root is not None else TESTS_DIR
    offline = sorted(p.name for p in base.glob("test_*.py"))
    listed = {args[0] for args in _UNIT_COMMANDS}
    return [n for n in offline if n not in listed and n not in _UNIT_EXCLUDED]


def duplicate_unit_commands() -> list[str]:
    """Script names that appear more than once in _UNIT_COMMANDS."""
    listed = [args[0] for args in _UNIT_COMMANDS]
    return sorted({n for n in listed if listed.count(n) > 1})


def run_unit() -> Result:
    t0 = time.monotonic()
    failed: list[str] = []
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT)}
    for args in _UNIT_COMMANDS:
        script = TESTS_DIR / args[0]
        if not script.is_file():
            return Result("unit", SKIP, f"{args[0]} missing", 0.0)
        # Avoid recursion: when check_all is itself under test via
        # test_check_all.py's direct imports, still run the file — but when
        # check_all invoked test_check_all as part of unit, that's fine
        # (test_check_all mocks runners and does not re-enter run_unit for
        # the live path below). Guard only against check_all → test_check_all
        # → check_all.run_tier(live) loops: test_check_all never calls run_unit.
        proc = _run([_python(), str(script), *args[1:]], env=env, timeout=600)
        if proc.returncode != 0:
            failed.append(f"{args[0]} (exit {proc.returncode}): {_tail(_combine_output(proc))}")
    elapsed = time.monotonic() - t0
    if failed:
        return Result("unit", FAIL, f"{len(failed)} file(s): " + "; ".join(failed[:3]), elapsed)
    return Result("unit", PASS, f"{len(_UNIT_COMMANDS)} files", elapsed)


def run_perception() -> Result:
    t0 = time.monotonic()
    script = TESTS_DIR / "test_perception.py"
    if not script.is_file():
        return Result("perception", SKIP, "test_perception.py missing", 0.0)
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT)}
    proc = _run([_python(), str(script)], env=env, timeout=120)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        return Result("perception", FAIL, _tail(_combine_output(proc)), elapsed)
    return Result("perception", PASS, _tail(_combine_output(proc), 2), elapsed)


def run_node_check() -> Result:
    t0 = time.monotonic()
    node = _which("node")
    if not node:
        return Result("node_check", SKIP, "node not installed", 0.0)
    index = ROOT / "index.html"
    if not index.is_file():
        return Result("node_check", SKIP, "index.html missing", 0.0)
    try:
        js = extract_index_script(index)
    except ValueError as e:
        return Result("node_check", FAIL, str(e), time.monotonic() - t0)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        path = fh.name
    try:
        proc = _run([node, "--check", path], timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        return Result("node_check", FAIL, _tail(_combine_output(proc)), elapsed)
    return Result("node_check", PASS, f"{len(js)} bytes", elapsed)


def run_scryfall_gates() -> Result:
    t0 = time.monotonic()
    script = ROOT / "scripts" / "check_payoff_queries.py"
    if not script.is_file():
        return Result("scryfall_gates", SKIP, "scripts/check_payoff_queries.py missing", 0.0)
    proc = _run([_python(), str(script)], timeout=300)
    elapsed = time.monotonic() - t0
    out = _combine_output(proc)
    # Exit 2 = throttled / not measured (house rule). Treat as SKIP.
    if proc.returncode == 2 or "NOT MEASURED" in out:
        return Result(
            "scryfall_gates", SKIP,
            "Scryfall 429, retried and skipped" if "429" in out or "NOT MEASURED" in out
            else _tail(out),
            elapsed,
        )
    if proc.returncode != 0:
        return Result("scryfall_gates", FAIL, _tail(out), elapsed)
    return Result("scryfall_gates", PASS, _tail(out, 2), elapsed)


def _playwright_ready() -> str | None:
    """Return None if node can import playwright; else a skip reason."""
    node = _which("node")
    if not node:
        return "node not installed"
    # Resolve from design-review/ so a local node_modules there counts.
    probe = "import('playwright').then(()=>process.exit(0)).catch(()=>process.exit(1))"
    proc = _run([node, "-e", probe], cwd=ROOT / "testdata" / "review", timeout=30)
    if proc.returncode != 0:
        return "playwright not installed (cd design-review && npm i playwright)"
    return None


def _server_env(port: int, home: Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    # Scratch server: model endpoints may 500 without a key; Playwright
    # and Schemathesis stay on the non-model surface. e2e checks the key
    # itself before starting.
    return env


def _wait_http(url: str, timeout: float = 30.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _start_uvicorn(port: int, home: Path) -> subprocess.Popen | Result:
    env = _server_env(port, home)
    try:
        proc = subprocess.Popen(
            [_python(), "-m", "uvicorn", "deckwright.app:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return Result("server", SKIP, f"could not start uvicorn: {e}", 0.0)
    if not _wait_http(f"http://127.0.0.1:{port}/"):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        return Result("server", SKIP, f"uvicorn on :{port} did not become ready", 0.0)
    return proc


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()


def run_playwright() -> Result:
    t0 = time.monotonic()
    reason = _playwright_ready()
    if reason:
        return Result("playwright", SKIP, reason, 0.0)
    script = ROOT / "testdata" / "review" / "verify-workbench.mjs"
    if not script.is_file():
        return Result("playwright", SKIP, "design-review/verify-workbench.mjs missing", 0.0)
    node = _which("node")
    assert node
    port = 8012
    with tempfile.TemporaryDirectory(prefix="deckwright-pw-") as tmp:
        home = Path(tmp)
        started = _start_uvicorn(port, home)
        if isinstance(started, Result):
            started.name = "playwright"
            return started
        env = os.environ.copy()
        env["DECKWRIGHT_URL"] = f"http://127.0.0.1:{port}"
        try:
            proc = _run([node, str(script)], cwd=ROOT, env=env, timeout=180)
        finally:
            _stop(started)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        return Result("playwright", FAIL, _tail(_combine_output(proc)), elapsed)
    return Result("playwright", PASS, "1280/390 " + _tail(_combine_output(proc), 2), elapsed)


def run_schemathesis() -> Result:
    t0 = time.monotonic()
    if _run([_python(), "-c", "import schemathesis"], timeout=30).returncode != 0:
        return Result("schemathesis", SKIP, "schemathesis not installed", 0.0)
    spec = ROOT / "testdata" / "review" / "openapi-inferred.json"
    if not spec.is_file():
        return Result("schemathesis", SKIP, "openapi-inferred.json missing", 0.0)
    port = 8125
    with tempfile.TemporaryDirectory(prefix="deckwright-st-") as tmp:
        home = Path(tmp)
        started = _start_uvicorn(port, home)
        if isinstance(started, Result):
            started.name = "schemathesis"
            return started
        try:
            # Exclude model-quota / Forge-heavy routes — same cut as REPORT.md.
            proc = _run([
                _python(), "-m", "schemathesis", "run",
                str(spec),
                f"--url=http://127.0.0.1:{port}",
                "--exclude-path=/api/chat",
                "--exclude-path=/api/commanders",
                "--exclude-path=/api/build",
                "--exclude-path=/api/thesis",
                "--exclude-path=/api/pressure",
                "--checks=all",
            ], timeout=600)
        finally:
            _stop(started)
    elapsed = time.monotonic() - t0
    out = _combine_output(proc)
    if proc.returncode != 0:
        # Schemathesis exits non-zero on findings; surface that as FAIL.
        return Result("schemathesis", FAIL, _tail(out), elapsed)
    return Result("schemathesis", PASS, _tail(out, 3), elapsed)


def run_e2e_flow() -> Result:
    t0 = time.monotonic()
    if not os.environ.get("ANTHROPIC_API_KEY") and not _which("claude"):
        return Result("e2e_flow", SKIP, "no ANTHROPIC key (and no claude CLI)", 0.0)
    script = ROOT / "scripts" / "e2e_flow.py"
    if not script.is_file():
        return Result("e2e_flow", SKIP, "scripts/e2e_flow.py missing", 0.0)
    port = 8130
    with tempfile.TemporaryDirectory(prefix="deckwright-e2e-") as tmp:
        home = Path(tmp)
        started = _start_uvicorn(port, home)
        if isinstance(started, Result):
            started.name = "e2e_flow"
            return started
        try:
            proc = _run([_python(), str(script)], timeout=1200)
        finally:
            _stop(started)
    elapsed = time.monotonic() - t0
    out = _combine_output(proc)
    if proc.returncode != 0:
        return Result("e2e_flow", FAIL, _tail(out), elapsed)
    return Result("e2e_flow", PASS, _tail(out, 3), elapsed)


def run_differential() -> Result:
    """harness. Absent today — that is SKIP, never a silent pass."""
    candidates = [
        ROOT / "scripts" / "differential_harness.py",
        ROOT / "scripts" / "e2e_differential.py",
        ROOT / "scripts" / "diff_wishes.py",
    ]
    script = next((p for p in candidates if p.is_file()), None)
    if script is None:
        return Result(
            "differential", SKIP,
            "differential harness not present",
            0.0,
        )
    if not os.environ.get("ANTHROPIC_API_KEY") and not _which("claude"):
        return Result("differential", SKIP, "no ANTHROPIC key (and no claude CLI)", 0.0)
    t0 = time.monotonic()
    port = 8131
    with tempfile.TemporaryDirectory(prefix="deckwright-diff-") as tmp:
        home = Path(tmp)
        started = _start_uvicorn(port, home)
        if isinstance(started, Result):
            started.name = "differential"
            return started
        env = os.environ.copy()
        env["DECKWRIGHT_URL"] = f"http://127.0.0.1:{port}"
        try:
            proc = _run([_python(), str(script)], env=env, timeout=1800)
        finally:
            _stop(started)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        return Result("differential", FAIL, _tail(_combine_output(proc)), elapsed)
    return Result("differential", PASS, _tail(_combine_output(proc), 3), elapsed)


def run_forge_calibration() -> Result:
    t0 = time.monotonic()
    script = ROOT / "scripts" / "calibrate_forge.py"
    if not script.is_file():
        return Result("forge_calibration", SKIP, "scripts/calibrate_forge.py missing", 0.0)
    # Detect via the same helper the app uses.
    sys.path.insert(0, str(ROOT))
    try:
        from deckwright import pressure  # noqa: WPS433
        engine = pressure.detect_engine()
    except Exception as e:
        return Result("forge_calibration", SKIP, f"engine detect failed: {e}", 0.0)
    if not engine.get("available"):
        reason = engine.get("reason") or "unavailable"
        detail = engine.get("detail") or reason
        if reason == "forge_missing":
            detail = "Forge not installed"
        elif reason == "java_missing":
            detail = "Java not installed"
        return Result("forge_calibration", SKIP, detail, 0.0)
    # Calibration against a fixture log directory if present, else live games.
    # Prefer --from-logs when the operator left a cache; otherwise a short run.
    logs = Path(os.environ.get("DECKWRIGHT_FORGE_CAL_LOGS", "/tmp/forge-cal-logs"))
    if logs.is_dir() and any(logs.glob("*.log")):
        cmd = [_python(), str(script), "--from-logs", str(logs)]
    else:
        games = os.environ.get("DECKWRIGHT_FORGE_CAL_GAMES", "50")
        cmd = [_python(), str(script), "--games", str(games)]
    proc = _run(cmd, timeout=3600)
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        return Result("forge_calibration", FAIL, _tail(_combine_output(proc)), elapsed)
    return Result("forge_calibration", PASS, _tail(_combine_output(proc), 3), elapsed)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

# This exists because it already happened: a merge committed conflict
# markers into scripts/check_all.py, GitHub still reported the PR MERGEABLE
# because it merged cleanly against main, and tier 1 could not parse the file.
# A marker in a tracked source file is never intentional and costs nothing to
# find, so it runs first and offline.
_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")
_MARKER_EXTS = {".py", ".html", ".json", ".md", ".sh", ".js", ".css", ".yml", ".yaml"}
_MARKER_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".playwright-mcp"}


def run_conflict_markers() -> Result:
    t0 = time.monotonic()
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _MARKER_EXTS:
            continue
        if any(part in _MARKER_SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        # This file documents the markers it looks for, so skip self-matches
        # on the literal prefixes defined above.
        if path.resolve() == Path(__file__).resolve():
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.startswith(_MARKER_PREFIXES):
                hits.append(f"{path.relative_to(ROOT)}:{n}")
    elapsed = time.monotonic() - t0
    if hits:
        return Result("conflict_markers", FAIL,
                      f"{len(hits)} marker(s): " + ", ".join(hits[:6]), elapsed)
    return Result("conflict_markers", PASS, "no merge markers in tracked sources",
                  elapsed)


def _default_layers() -> list[Layer]:
    return [
        Layer("conflict_markers", 1, "no merge markers in sources", run_conflict_markers),
        Layer("unit", 1, "offline unit suite", run_unit),
        Layer("perception", 1, "sim perception gate", run_perception),
        Layer("node_check", 1, "node --check on index.html script", run_node_check),
        Layer("scryfall_gates", 2, "payoff query live gate", run_scryfall_gates),
        Layer("playwright", 2, "layout at 1280/390", run_playwright),
        Layer("schemathesis", 2, "API property tests", run_schemathesis),
        Layer("e2e_flow", 3, "wish → 99 chain (opt-in smoke)", run_e2e_flow,
              optional=True),
        Layer("differential", 3, "different wish → different deck", run_differential),
        Layer("forge_calibration", 3, "sim vs Forge gap", run_forge_calibration),
    ]


LAYERS: list[Layer] = _default_layers()


def layers_for(tier: int, include_optional: bool = False) -> list[Layer]:
    if tier not in (1, 2, 3):
        raise ValueError(f"tier must be 1, 2, or 3; got {tier}")
    return [L for L in LAYERS
            if L.tier <= tier and (include_optional or not L.optional)]


# ---------------------------------------------------------------------------
# runner + summary
# ---------------------------------------------------------------------------

def _tone(status: str) -> str:
    return {PASS: GREEN, FAIL: RED, SKIP: YELLOW}.get(status, DIM)


def print_summary(tier: int, results: list[Result]) -> None:
    print()
    print("=" * 72)
    print(f"  check_all — tier {tier}")
    print("=" * 72)
    print(f"  {'layer':<22} {'status':<6} {'sec':>6}  detail")
    print(f"  {'-'*22} {'-'*6} {'-'*6}  {'-'*32}")
    for r in results:
        print(f"  {r.name:<22} {_tone(r.status)}{r.status:<6}{OFF} "
              f"{r.seconds:6.1f}  {r.detail}")
    n_pass = sum(1 for r in results if r.status == PASS)
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_skip = sum(1 for r in results if r.status == SKIP)
    print("-" * 72)
    print(f"  {n_pass} pass · {n_fail} fail · {n_skip} skip/unknown")
    if n_skip:
        print()
        print(f"  {YELLOW}Skipped (tier incomplete — unknown is not a zero):{OFF}")
        for r in results:
            if r.status == SKIP:
                print(f"    · {r.name}: {r.detail}")
    if n_fail:
        print()
        print(f"  {RED}Failed:{OFF}")
        for r in results:
            if r.status == FAIL:
                print(f"    · {r.name}: {r.detail}")
    print("=" * 72)


def run_tier(tier: int, runners: dict[str, Callable[[], Result]] | None = None,
             include_optional: bool = False) -> int:
    """Run every layer at or below `tier`. Return an EXIT_* code.

    opt-in layers are excluded unless `include_optional`. The ship
    gate is differential + Forge; e2e_flow is a smoke run, not a second gate.
    """
    selected = layers_for(tier, include_optional)
    results: list[Result] = []
    print(f"\ncheck_all tier {tier} — {len(selected)} layer(s)\n")
    for layer in selected:
        print(f"→ {layer.name}  {DIM}{layer.summary}{OFF}", flush=True)
        fn = (runners or {}).get(layer.name) or layer.run
        try:
            result = fn()
        except Exception as e:
            result = Result(layer.name, FAIL, f"unhandled {type(e).__name__}: {e}", 0.0)
        results.append(result)
        print(f"  {_tone(result.status)}{result.status}{OFF}  {result.detail}  "
              f"{DIM}{result.seconds:.1f}s{OFF}", flush=True)
    print_summary(tier, results)
    if any(r.status == FAIL for r in results):
        return EXIT_FAIL
    if any(r.status == SKIP for r in results):
        return EXIT_INCOMPLETE
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run Deckwright's check tiers.",
        exit_on_error=False,
    )
    ap.add_argument(
        "--with-optional", action="store_true",
        help="also run opt-in layers (e2e_flow). not part of the gate.",
    )
    ap.add_argument(
        "--tier", type=int, required=True, choices=(1, 2, 3),
        help="1=commit (offline), 2=PR (network), 3=ship (model+Forge)",
    )
    try:
        args = ap.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as e:
        # Bad tier / missing flag — incomplete usage, not a layer failure.
        if isinstance(e, SystemExit):
            code = e.code if isinstance(e.code, int) else EXIT_INCOMPLETE
            return code if code else EXIT_OK
        print(f"check_all: {e}", file=sys.stderr)
        return EXIT_INCOMPLETE
    # Drop into repo root so relative imports inside test files resolve.
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return run_tier(args.tier, include_optional=args.with_optional)


if __name__ == "__main__":
    raise SystemExit(main())
