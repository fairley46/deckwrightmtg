"""Resolve user-supplied file names inside a fixed base directory.

Saved-deck ids and precon ids arrive in URLs and request bodies. Each one names
a single JSON file in a known directory, so a valid id is a short run of
letters, digits, "_" and "-", and the resolved path must stay inside that
directory. Anything else is treated as "not found" by the callers.
"""

from __future__ import annotations

import os
import pathlib
import re

# Hyphens are allowed because commander names have them (Niv-Mizzet). Path
# traversal needs "/" or "..", and neither survives this class.
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,200}")


def is_safe_name(name: object) -> bool:
    """True when `name` can be used as a bare file stem."""
    return isinstance(name, str) and SAFE_NAME_RE.fullmatch(name) is not None


def safe_child_path(base: pathlib.Path | str, name: object,
                    suffix: str = ".json") -> pathlib.Path | None:
    """Return base/<name><suffix> if the name is valid and stays inside base.

    Returns None for an invalid name or a path that resolves outside `base`
    (for example through a symlink). The file does not have to exist.
    """
    if not is_safe_name(name):
        return None
    base_real = os.path.realpath(os.fspath(base))
    candidate = os.path.realpath(os.path.join(base_real, f"{name}{suffix}"))
    if not candidate.startswith(base_real + os.sep):
        return None
    return pathlib.Path(candidate)
