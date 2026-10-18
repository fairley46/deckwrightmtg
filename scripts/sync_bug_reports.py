#!/usr/bin/env python3
"""Walk local bug reports. Remote tracker filing is disabled in this tree.

Reports live under ~/.deckwright/bugs/ (or --dir). This script prints a summary
of stamped vs unstamped files. It never uploads.

    python scripts/sync_bug_reports.py
    python scripts/sync_bug_reports.py --dir /path/to/bugs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="bug report directory (default: DECKWRIGHT_HOME/bugs)",
    )
    args = p.parse_args(argv)
    if args.dir is None:
        from deckwright import instance
        args.dir = instance.home_dir() / "bugs"

    directory = args.dir
    if not directory.exists():
        print(f"no bug dir at {directory}")
        return 0

    stamped = unstamped = unreadable = 0
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(record, dict):
            unreadable += 1
            continue
        if record.get("linear"):
            stamped += 1
        else:
            unstamped += 1

    print(
        f"bug reports in {directory}: "
        f"{stamped} stamped, {unstamped} local-only, {unreadable} unreadable"
    )
    print("remote filing is disabled in this tree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
