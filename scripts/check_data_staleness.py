#!/usr/bin/env python3
"""CON-230: report age of committed data artifacts.

Exit 0 always for informational output (including STALE warnings).
Exit 1 only when a required tracked file is missing (unknown).

    python scripts/check_data_staleness.py
    python scripts/check_data_staleness.py --days 7

Missing timestamps are reported as UNKNOWN — never treated as zero/fresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

REQUIRED = (
    REPO / "data" / "scryfall_grammar.json",
    REPO / "data" / "commanders.json",
    REPO / "data" / "synergy_objects.json",
    REPO / "data" / "mechanic_families.json",
)


def _parse_ts(raw: object) -> datetime | None:
    """Parse ISO date or datetime strings. Returns None when absent/invalid."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_days(ts: datetime | None, now: datetime) -> int | None:
    if ts is None:
        return None
    delta = now - ts.astimezone(timezone.utc)
    return max(0, delta.days)


def _status(age: int | None, limit: int) -> str:
    if age is None:
        return "UNKNOWN"
    if age > limit:
        return "STALE"
    return "OK"


def _print_row(label: str, ts: datetime | None, age: int | None, limit: int,
               detail: str = "") -> None:
    status = _status(age, limit)
    when = ts.astimezone(timezone.utc).strftime("%Y-%m-%d") if ts else "—"
    age_s = str(age) if age is not None else "UNKNOWN"
    suffix = f"  ({detail})" if detail else ""
    print(f"{label:28} {status:8}  age={age_s:>7}  at={when}{suffix}")


def _file_mtime(path: Path, now: datetime) -> tuple[datetime | None, int | None]:
    if not path.is_file():
        return None, None
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts, _age_days(ts, now)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=14,
        help="warn when data is older than this many days (default: 14)",
    )
    args = parser.parse_args()
    limit = max(1, args.days)
    now = datetime.now(timezone.utc)
    missing = 0

    print(f"data staleness check  (warn after {limit} days)")
    print(f"repo: {REPO}")
    print()

    for path in REQUIRED:
        rel = path.relative_to(REPO)
        if not path.is_file():
            print(f"{str(rel):28} MISSING")
            missing += 1
            continue

    if missing:
        print()
        print(f"{missing} required file(s) missing — exit 1")
        return 1

    # scryfall_grammar.json — fetched_at
    grammar_path = REPO / "data" / "scryfall_grammar.json"
    grammar = json.loads(grammar_path.read_text())
    g_ts = _parse_ts(grammar.get("fetched_at"))
    _print_row("scryfall_grammar", g_ts, _age_days(g_ts, now), limit,
               "fetched_at")

    # commanders.json — researched_at_run + mechanics_refresh_at
    cmd_path = REPO / "data" / "commanders.json"
    cmd = json.loads(cmd_path.read_text())
    for key in ("researched_at_run", "mechanics_refresh_at"):
        ts = _parse_ts(cmd.get(key))
        _print_row(f"commanders.{key}", ts, _age_days(ts, now), limit)

    # precon bakes — file mtimes (no single baked_at stamp in payload)
    precon_dir = REPO / "data" / "precons"
    precon_files = sorted(precon_dir.glob("*.json")) if precon_dir.is_dir() else []
    if precon_files:
        mtimes = [p.stat().st_mtime for p in precon_files]
        oldest = datetime.fromtimestamp(min(mtimes), tz=timezone.utc)
        newest = datetime.fromtimestamp(max(mtimes), tz=timezone.utc)
        _print_row(
            "precons (oldest file)",
            oldest,
            _age_days(oldest, now),
            limit,
            f"{len(precon_files)} bakes",
        )
        _print_row(
            "precons (newest file)",
            newest,
            _age_days(newest, now),
            limit,
        )
    else:
        print(f"{'precons':28} MISSING")

    # derived artifacts — mtime when no embedded timestamp
    for rel in ("data/synergy_objects.json", "data/mechanic_families.json"):
        path = REPO / rel
        ts, age = _file_mtime(path, now)
        _print_row(rel.split("/", 1)[1], ts, age, limit, "mtime")

    # CON-294: local Scryfall corpus — DECKWRIGHT_CORPUS_DIR or ~/.deckwright.
    # Missing is UNKNOWN (could not look), never treated as fresh.
    try:
        sys.path.insert(0, str(REPO))
        from deckwright import instance as _instance
        home = _instance.corpus_dir()
    except Exception:
        home = Path.home() / ".deckwright"
    corpus_meta = home / "corpus.meta.json"
    oracle_meta = home / "oracle_cards.meta.json"
    oracle_jsonl = home / "oracle_cards.jsonl"
    meta_path = corpus_meta if corpus_meta.is_file() else oracle_meta
    if not oracle_jsonl.is_file():
        print(f"{'corpus (oracle_cards)':28} MISSING  (~/.deckwright/oracle_cards.jsonl)")
    elif not meta_path.is_file():
        ts, age = _file_mtime(oracle_jsonl, now)
        _print_row("corpus (oracle_cards)", ts, age, limit,
                   "mtime only — meta UNKNOWN")
    else:
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        # corpus.meta.json nests oracle; oracle_cards.meta.json is flat.
        oracle_block = meta.get("oracle") if isinstance(meta.get("oracle"), dict) else meta
        ts = _parse_ts((oracle_block or {}).get("fetched_at")) or _parse_ts(
            (oracle_block or {}).get("updated_at")) or _parse_ts(meta.get("fetched_at"))
        count = (oracle_block or {}).get("card_count") or meta.get("card_count")
        detail = f"cards={count}" if count is not None else "card_count UNKNOWN"
        _print_row("corpus (oracle_cards)", ts, _age_days(ts, now), limit, detail)
    prices_meta = home / "prices.meta.json"
    if prices_meta.is_file():
        try:
            pmeta = json.loads(prices_meta.read_text())
        except (OSError, json.JSONDecodeError):
            pmeta = {}
        pts = _parse_ts(pmeta.get("fetched_at")) or _parse_ts(pmeta.get("updated_at"))
        _print_row(
            "corpus (prices)",
            pts,
            _age_days(pts, now),
            limit,
            f"n={pmeta.get('price_count', 'UNKNOWN')}",
        )
    else:
        print(f"{'corpus (prices)':28} MISSING  (~/.deckwright/prices.json)")

    print()
    print("exit 0 (informational; STALE is a warning, not a failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
