#!/usr/bin/env python3
"""API boundary gate — unread emits and unsent reads fail.

Mirrors the unit registry: the guard that catches the class is the one thing
that must run. A deliberate unread response key makes this red.

    python test_api_boundary.py
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

import api_boundary as ab  # noqa: E402

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


def true(label, cond, detail=""):
    (ok if cond else bad)(label, detail)


def test_contract_clean():
    print("\napi boundary — contract is clean")
    gaps = ab.all_gaps()
    for name, items in gaps.items():
        eq(f"{name} empty", items, [])


def test_both_directions_reported():
    print("\napi boundary — report covers both directions")
    gaps = ab.all_gaps()
    true("emitted_unread key present", "emitted_unread" in gaps)
    true("read_unsent key present", "read_unsent" in gaps)
    true("saved_inert key present", "saved_inert" in gaps)
    true("inert_data_attrs key present", "inert_data_attrs" in gaps)


def test_unread_emit_is_detectable():
    """Failure mode: a new unread response key makes the gate red."""
    print("\napi boundary — unread emit is a gap, not a silent pass")
    ep = "/api/chat"
    phantom = "unread_phantom"
    if phantom in ab.ENDPOINTS[ep]["emits"]:
        bad("phantom path clear", f"{phantom} already in emits")
        return
    ab.ENDPOINTS[ep]["emits"].add(phantom)
    try:
        gaps = ab.emitted_unread(ep)
        true(
            "phantom appears in emitted_unread",
            f"{ep}:{phantom}" in gaps,
            f"gaps={gaps!r}",
        )
    finally:
        ab.ENDPOINTS[ep]["emits"].discard(phantom)
    eq("phantom removed after probe", phantom in ab.ENDPOINTS[ep]["emits"], False)
    eq("chat emits clean again", ab.emitted_unread(ep), [])


def test_unsent_read_is_detectable():
    """Failure mode: UI reading a key the endpoint never sends."""
    print("\napi boundary — unsent read is a gap, not a silent pass")
    ep = "/api/chat"
    phantom = "unsent_phantom"
    if phantom in ab.ENDPOINTS[ep]["ui_reads"]:
        bad("phantom path clear", f"{phantom} already in ui_reads")
        return
    ab.ENDPOINTS[ep]["ui_reads"].add(phantom)
    try:
        gaps = ab.read_unsent(ep)
        true(
            "phantom appears in read_unsent",
            f"{ep}:{phantom}" in gaps,
            f"gaps={gaps!r}",
        )
    finally:
        ab.ENDPOINTS[ep]["ui_reads"].discard(phantom)
    eq("phantom removed after probe", phantom in ab.ENDPOINTS[ep]["ui_reads"], False)


def test_inert_data_attr_is_detectable():
    print("\napi boundary — inert data-* attribute is a gap")
    html = '<div data-test-phantom="1"></div>'
    gaps = ab.inert_data_attrs(html)
    true("phantom data attr listed", "test-phantom" in gaps, f"gaps={gaps!r}")
    # Keep reason clears it
    ab.DATA_ATTR_KEEP["test-phantom"] = "probe"
    try:
        eq("keep reason clears gap", ab.inert_data_attrs(html), [])
    finally:
        ab.DATA_ATTR_KEEP.pop("test-phantom", None)


def test_server_only_requires_reason():
    print("\napi boundary — allowlist entries need reasons")
    ep = "/api/chat"
    ab.ENDPOINTS[ep]["server_only"]["empty_reason"] = "   "
    ab.ENDPOINTS[ep]["emits"].add("empty_reason")
    try:
        missing = ab.allowlist_reasons_missing()
        true(
            "empty reason flagged",
            f"{ep}:server_only:empty_reason" in missing,
            f"missing={missing!r}",
        )
    finally:
        ab.ENDPOINTS[ep]["server_only"].pop("empty_reason", None)
        ab.ENDPOINTS[ep]["emits"].discard("empty_reason")


def main() -> int:
    test_contract_clean()
    test_both_directions_reported()
    test_unread_emit_is_detectable()
    test_unsent_read_is_detectable()
    test_inert_data_attr_is_detectable()
    test_server_only_requires_reason()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
