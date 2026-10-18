#!/usr/bin/env python3
"""tiered check_all entry point — house rules, not a green suite.

    python test_check_all.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_all as ca  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, detail=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def bad(label, detail=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r} want {want!r}")


def _pass(name="layer"):
    return ca.Result(name=name, status=ca.PASS, detail="ok", seconds=0.01)


def _fail(name="layer", detail="broke"):
    return ca.Result(name=name, status=ca.FAIL, detail=detail, seconds=0.01)


def _skip(name="layer", detail="missing tool"):
    return ca.Result(name=name, status=ca.SKIP, detail=detail, seconds=0.0)


def test_tier_selects_layers():
    print("\ncheck_all — tier membership")
    t1 = [L.name for L in ca.layers_for(1)]
    t2 = [L.name for L in ca.layers_for(2)]
    t3 = [L.name for L in ca.layers_for(3)]
    OFFLINE = ["conflict_markers", "unit", "perception", "node_check"]
    NETWORK = ["scryfall_gates", "playwright", "schemathesis"]
    # e2e_flow is opt-in and deliberately NOT in the default tier 3.
    # The ship gate is differential + Forge; e2e re-walks the chain differential
    # already covers, at model cost. It stays reachable via --with-optional.
    SHIP = ["differential", "forge_calibration"]
    eq("tier 1 names", t1, OFFLINE)
    eq("tier 2 adds network layers", t2, OFFLINE + NETWORK)
    eq("tier 3 adds ship layers", t3, OFFLINE + NETWORK + SHIP)
    eq("tier 2 contains tier 1", t2[:len(t1)], t1)
    eq("tier 3 contains tier 2", t3[:len(t2)], t2)

    # The opt-in layer exists and appears only when asked for.
    t3_opt = [L.name for L in ca.layers_for(3, include_optional=True)]
    eq("e2e_flow absent from the gate", "e2e_flow" in t3, False)
    eq("e2e_flow available on request", "e2e_flow" in t3_opt, True)
    eq("opt-in adds only e2e_flow", [n for n in t3_opt if n not in t3], ["e2e_flow"])


def test_skip_fails_the_tier():
    print("\ncheck_all — skip is not a pass")
    runners = {
        "unit": lambda: _pass("unit"),
        "perception": lambda: _skip("perception", "invented skip"),
        "node_check": lambda: _pass("node_check"),
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ca.run_tier(1, runners=runners)
    out = buf.getvalue()
    eq("skip exit code", code, ca.EXIT_INCOMPLETE)
    eq("summary names the skip", "invented skip" in out, True)
    eq("summary does not call skip PASS",
       "PASS  perception" not in out and "perception" in out, True)


def test_fail_beats_skip_for_exit():
    print("\ncheck_all — fail exit takes precedence")
    runners = {
        "unit": lambda: _fail("unit", "assertion"),
        "perception": lambda: _skip("perception", "no tool"),
        "node_check": lambda: _pass("node_check"),
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ca.run_tier(1, runners=runners)
    eq("fail exit code", code, ca.EXIT_FAIL)


def test_all_pass_exit_zero():
    print("\ncheck_all — clean tier exits 0")
    runners = {
        "unit": lambda: _pass("unit"),
        "perception": lambda: _pass("perception"),
        "node_check": lambda: _pass("node_check"),
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ca.run_tier(1, runners=runners)
    out = buf.getvalue()
    eq("pass exit code", code, ca.EXIT_OK)
    eq("summary table printed", "unit" in out and "perception" in out, True)


def test_summary_lists_every_layer():
    print("\ncheck_all — summary covers the tier")
    runners = {n: (lambda n=n: _pass(n)) for n in
               ["conflict_markers", "unit", "perception", "node_check"]}
    buf = io.StringIO()
    with redirect_stdout(buf):
        ca.run_tier(1, runners=runners)
    out = buf.getvalue()
    for name in ("unit", "perception", "node_check"):
        eq(f"row for {name}", name in out, True)


def test_extract_index_script():
    print("\ncheck_all — index.html script extract")
    js = ca.extract_index_script(Path(__file__).resolve().parents[1] / "index.html")
    eq("extracted non-empty", len(js) > 1000, True)
    eq("no script tags in extract", "<script>" not in js and "</script>" not in js, True)


def test_node_check_layer_runs():
    print("\ncheck_all — live node --check on index.html")
    result = ca.run_node_check()
    eq("node_check status", result.status, ca.PASS)
    eq("node_check names itself", result.name, "node_check")


def test_unit_commands_cover_offline_files():
    """every offline test_*.py is registered or excluded.

    The merge-gate copy of this assertion lives in test_unit_registry.py so
    tier 1 can run it without listing this file (which would recurse).
    """
    print("\ncheck_all — unit list covers offline tests")
    missing = ca.unregistered_offline_tests()
    eq("no offline unit file missing from _UNIT_COMMANDS / _UNIT_EXCLUDED",
       missing, [])
    dupes = ca.duplicate_unit_commands()
    eq("no duplicate unit commands", dupes, [])
    listed = [args[0] for args in ca._UNIT_COMMANDS]
    eq("differential harness unit is listed",
       "test_differential_harness.py" in listed, True)
    eq("registry guard listed for tier 1",
       "test_unit_registry.py" in listed, True)


def test_differential_missing_is_skip():
    print("\ncheck_all — missing differential harness is SKIP, not PASS")
    # has not landed; the layer must refuse to look green.
    # When that harness ships, this assertion retires — the runner will
    # exercise the real script instead of the absence path.
    candidates = [
        Path(__file__).resolve().parents[1] / "scripts" / "differential_harness.py",
        Path(__file__).resolve().parents[1] / "scripts" / "e2e_differential.py",
        Path(__file__).resolve().parents[1] / "scripts" / "diff_wishes.py",
    ]
    if any(p.is_file() for p in candidates):
        # The harness HAS landed. Do not call ca.run_differential()
        # here: it drives the real thing -- live model calls and several
        # minutes -- and hung this file indefinitely once both PRs were on
        # main. Neither PR was wrong alone; the pair was. A Tier-1 unit test
        # must never spend quota. Assert the layer is wired, not that it runs;
        # actually running it is Tier 3's job.
        eq("differential layer is wired", callable(ca.run_differential), True)
        eq("differential is not in tier 1",
           "differential" not in [l.name for l in ca.layers_for(1)], True)
        return
    result = ca.run_differential()
    eq("differential status", result.status, ca.SKIP)
    eq("differential names the harness", "differential harness" in result.detail, True)


def test_cli_rejects_bad_tier():
    print("\ncheck_all — CLI")
    code = ca.main(["--tier", "9"])
    eq("bad tier exit", code, 2)


def main():
    test_tier_selects_layers()
    test_skip_fails_the_tier()
    test_fail_beats_skip_for_exit()
    test_all_pass_exit_zero()
    test_summary_lists_every_layer()
    test_extract_index_script()
    test_node_check_layer_runs()
    test_unit_commands_cover_offline_files()
    test_differential_missing_is_skip()
    test_cli_rejects_bad_tier()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
