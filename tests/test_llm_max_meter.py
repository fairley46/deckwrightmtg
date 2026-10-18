#!/usr/bin/env python3
"""
test_llm_max_meter.py — Claude CLI adapter is preferred; API is fallback.

Offline. No network. Proves overview/model gates do not require a key when
the CLI path is selected, and that docs-facing helpers stay honest.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from deckwright import llm  # noqa: E402

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def fail(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        fail(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        fail(label, extra or "expected truthy")


def test_can_call_max_without_key():
    print("\nllm.can_call() — CLI needs no API key")
    cli = MagicMock()
    cli.via = "cli"
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        eq("cli without key can call", llm.can_call(cli), True)
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        eq("cli with key still can call", llm.can_call(cli), True)
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_can_call_api_needs_key():
    print("\nllm.can_call() — API path needs a key")
    api = MagicMock()
    api.via = "api"
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        eq("api without key cannot call", llm.can_call(api), False)
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        eq("api with key can call", llm.can_call(api), True)
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_make_client_prefers_max_over_key():
    print("\nllm.make_client() — key present still picks CLI when it exists")
    real_which, real_env = shutil.which, dict(os.environ)

    def fake_which(name):
        return "/opt/homebrew/bin/claude" if name == "claude" else None

    shutil.which = fake_which
    try:
        os.environ.pop("DECKWRIGHT_LLM", None)
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        client, how = llm.make_client()
        eq("via cli", client.via, "cli")
        true("description names Claude/CLI", "Claude" in how and "CLI" in how)
        true("can_call on that client", llm.can_call(client))
    finally:
        shutil.which = real_which
        os.environ.clear()
        os.environ.update(real_env)


def test_overview_gate_matches_can_call():
    """The build overview must use can_call, not a bare key check.

    run.sh unsets ANTHROPIC_API_KEY when the CLI is present. Gating on the
    key alone skips overview on the intended CLI path.
    """
    print("\noverview gate source — can_call not bare key")
    src = (REPO / "deckwright" / "app.py").read_text(encoding="utf-8")
    true("can_call wraps at least one overview call",
         "if llm.can_call(client):" in src
         and "overview.generate_overview" in src)
    # The old bug: skip overview whenever run.sh unset the key on CLI.
    true("no bare-key overview gate remains",
         "if os.environ.get(\"ANTHROPIC_API_KEY\"):" not in src)


if __name__ == "__main__":
    test_can_call_max_without_key()
    test_can_call_api_needs_key()
    test_make_client_prefers_max_over_key()
    test_overview_gate_matches_can_call()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        sys.exit(1)
    print(f"{GREEN}{passed} passed{OFF}")
    sys.exit(0)
