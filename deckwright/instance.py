"""blue/green instance identity and data directories.

Unset environment = production today: saved decks and the Scryfall bulk
corpus both live in ~/.deckwright.

Green (staging) sets DECKWRIGHT_HOME to a different folder so a save cannot
touch the shared save shelf, and points DECKWRIGHT_CORPUS_DIR at the shared
bulk files. HOME itself stays the real user home (Claude CLI, Keychain, Forge).
"""

from __future__ import annotations

import os
from pathlib import Path

_CANDIDATES = frozenset({"off", "A", "B", "C"})
_ENVS = frozenset({"blue", "green"})


def env_name() -> str:
    raw = os.environ.get("DECKWRIGHT_ENV", "blue").strip().lower()
    if raw in ("", "blue"):
        return "blue"
    return raw


def candidate() -> str:
    raw = os.environ.get("DECKWRIGHT_CANDIDATE", "off").strip()
    if not raw or raw.lower() == "off":
        return "off"
    return raw.upper()


def home_dir() -> Path:
    raw = os.environ.get("DECKWRIGHT_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".deckwright"


def corpus_dir() -> Path:
    raw = os.environ.get("DECKWRIGHT_CORPUS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".deckwright"


def corpus_readonly() -> bool:
    v = os.environ.get("DECKWRIGHT_CORPUS_READONLY", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def boot_error() -> str | None:
    env = env_name()
    if env not in _ENVS:
        return (
            f"DECKWRIGHT_ENV={env!r} is not blue or green. "
            "Refusing to start."
        )
    cand = candidate()
    if cand not in _CANDIDATES:
        return (
            f"DECKWRIGHT_CANDIDATE={cand!r} is not off/A/B/C. "
            "Refusing to start."
        )
    if env == "blue" and cand != "off":
        return (
            "DECKWRIGHT_CANDIDATE is green-only. "
            f"Blue production refused to start with CANDIDATE={cand}."
        )
    return None


def require_boot() -> None:
    err = boot_error()
    if err:
        raise SystemExit(err)
