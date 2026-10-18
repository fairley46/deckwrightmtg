#!/usr/bin/env python3
"""every offline test_*.py is registered — or deliberately excluded.

Safe to list in check_all._UNIT_COMMANDS: this file only reads the registry.
It never calls ca.main() or run_tier(), so run_unit() cannot recurse through it.

    python test_unit_registry.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
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


def test_unit_commands_cover_offline_files():
    print("\nunit registry — offline coverage")
    missing = ca.unregistered_offline_tests()
    eq("no offline unit file missing from _UNIT_COMMANDS / _UNIT_EXCLUDED",
       missing, [])
    dupes = ca.duplicate_unit_commands()
    eq("no duplicate unit commands", dupes, [])
    listed = [args[0] for args in ca._UNIT_COMMANDS]
    eq("this guard is itself listed in _UNIT_COMMANDS",
       "test_unit_registry.py" in listed, True)
    eq("differential harness unit is listed",
       "test_differential_harness.py" in listed, True)
    for name, reason in ca._UNIT_EXCLUDED.items():
        eq(f"excluded {name} has a reason", bool(reason and reason.strip()), True)
        eq(f"excluded {name} is not also listed", name in listed, False)


def test_gap_is_detectable():
    """Failure mode: a new file with no registration surfaces as a gap."""
    print("\nunit registry — unregistered file is a gap, not a silent pass")
    root = Path(__file__).resolve().parent  # tests/
    # tests dir
    root = Path(__file__).resolve().parent
    phantom = root / "test_unregistered_phantom.py"
    if phantom.exists():
        bad("phantom path clear", f"{phantom.name} already exists")
        return
    try:
        phantom.write_text("# temporary should never stay\n", encoding="utf-8")
        missing = ca.unregistered_offline_tests(root)
        eq("phantom appears in unregistered_offline_tests",
           "test_unregistered_phantom.py" in missing, True)
    finally:
        phantom.unlink(missing_ok=True)
    eq("phantom removed after probe", phantom.exists(), False)


def main() -> int:
    test_unit_commands_cover_offline_files()
    test_gap_is_detectable()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
