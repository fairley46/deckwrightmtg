#!/usr/bin/env python3
"""
oracle_coverage.py — CON-245 instrument: how much of Magic can we read?

One Scryfall bulk oracle_cards fetch, offline detectors, no model. Reports:

  * % of commander-legal cards where at least one oracle_signal fires
  * % with any synergy consumes (edges need produces ∩ consumes)
  * unaccounted-for oracle lines on silent cards, ranked by frequency

Unread is reported. Unknown (could not look) is not scored as silent.

    python scripts/oracle_coverage.py
    python scripts/oracle_coverage.py --offline   # reuse cached bulk file
    python scripts/oracle_coverage.py --write data/oracle_coverage_report.json

Cache: ~/.deckwright/oracle_cards.jsonl (gunzipped bulk).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Iterator

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from deckwright import synergy  # noqa: E402
from deckwright import instance  # noqa: E402

CACHE_DIR = instance.corpus_dir()
CACHE_JSONL = CACHE_DIR / "oracle_cards.jsonl"
CACHE_META = CACHE_DIR / "oracle_cards.meta.json"
DEFAULT_REPORT = REPO / "data" / "oracle_coverage_report.json"

# Reminder text is not rules we failed to read.
_REMINDER = re.compile(r"\s*\([^)]*\)")
# Collapse runs of mana/tap symbols so pain-land variants rank together.
_MANA = re.compile(r"\{[^}]+\}")
_WS = re.compile(r"\s+")


def ensure_bulk(*, offline: bool) -> dict[str, Any]:
    """Return meta; ensure CACHE_JSONL exists.

    CON-294: delegates to corpus.ensure_oracle_cards so coverage and the
    serving path share one on-disk file under ~/.deckwright.
    """
    from deckwright import corpus as _corpus
    try:
        return _corpus.ensure_oracle_cards(offline=offline)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from e
    except Exception as e:
        raise SystemExit(f"oracle_cards fetch failed: {e}") from e


def iter_oracle_cards(path: pathlib.Path = CACHE_JSONL) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def is_commander_legal(card: dict) -> bool:
    leg = card.get("legalities") or {}
    return leg.get("commander") == "legal"


def normalize_line(line: str) -> str:
    """Stable key for ranking unread lines. Not a card dump."""
    s = line.strip().lower()
    if not s:
        return ""
    s = _REMINDER.sub("", s)
    s = _MANA.sub("{M}", s)
    s = _WS.sub(" ", s).strip(" .")
    return s


def unread_lines(oracle: str) -> list[str]:
    out = []
    for raw in oracle.splitlines():
        key = normalize_line(raw)
        if key:
            out.append(key)
    return out


def analyze(cards: Iterator[dict]) -> dict[str, Any]:
    synergy.clear_cache()
    n_legal = 0
    n_unknown = 0
    n_with_signal = 0
    n_with_keyword = 0
    n_keyword_only = 0
    n_signal_or_keyword = 0
    n_with_consumes = 0
    n_silent = 0  # no signal AND no catalog keyword
    line_counts: Counter[str] = Counter()
    signal_hits: Counter[str] = Counter()
    keyword_hits: Counter[str] = Counter()

    for card in cards:
        if not is_commander_legal(card):
            continue
        n_legal += 1
        prof = synergy.profile(card)
        if prof["status"] == synergy.UNKNOWN:
            n_unknown += 1
            continue
        sigs = prof.get("signals") or []
        kws = prof.get("keywords") or []
        if sigs:
            n_with_signal += 1
            for s in sigs:
                signal_hits[s] += 1
        if kws:
            n_with_keyword += 1
            for k in kws:
                keyword_hits[k] += 1
        if sigs or kws:
            n_signal_or_keyword += 1
            if kws and not sigs:
                n_keyword_only += 1
        else:
            # Truly unread: no oracle_signal and no Scryfall keyword catalog hit.
            n_silent += 1
            text = synergy._oracle(card) or ""
            for line in unread_lines(text):
                line_counts[line] += 1
        if prof.get("consumes"):
            n_with_consumes += 1

    # Denominator for coverage: cards we could look at (not unknown).
    graded = n_legal - n_unknown
    def pct(n: int) -> float:
        return (100.0 * n / graded) if graded else 0.0

    top_unread = [
        {"count": c, "line": line}
        for line, c in line_counts.most_common(80)
    ]
    return {
        "issue": "CON-251",
        "measured_on": date.today().isoformat(),
        "universe_commander_legal": n_legal,
        "unknown_unreadable": n_unknown,
        "graded": graded,
        "at_least_one_signal": {
            "count": n_with_signal,
            "pct": round(pct(n_with_signal), 1),
        },
        "at_least_one_keyword": {
            "count": n_with_keyword,
            "pct": round(pct(n_with_keyword), 1),
        },
        "keyword_only": {
            "count": n_keyword_only,
            "pct": round(pct(n_keyword_only), 1),
        },
        "signal_or_keyword": {
            "count": n_signal_or_keyword,
            "pct": round(pct(n_signal_or_keyword), 1),
        },
        "no_signal": {
            # Kept for lap-1 continuity: signal-empty, may still have keywords.
            "count": graded - n_with_signal,
            "pct": round(pct(graded - n_with_signal), 1),
        },
        "unread_no_signal_no_keyword": {
            "count": n_silent,
            "pct": round(pct(n_silent), 1),
        },
        "any_consumes": {
            "count": n_with_consumes,
            "pct": round(pct(n_with_consumes), 1),
        },
        "oracle_signal_count": len(synergy.load_signals()),
        "signal_hit_counts": dict(signal_hits.most_common()),
        "keyword_hit_counts": dict(keyword_hits.most_common(40)),
        "unread_lines_ranked": top_unread,
        "note": (
            "CON-251: readable = oracle_signal OR Scryfall keyword catalog. "
            "Keywords are not invented signals and do not create produce/consume. "
            "Unread tail ranks lines on cards with neither. "
            "Unknown means we could not look (missing oracle)."
        ),
    }


def render_text(report: dict[str, Any]) -> str:
    a = report["at_least_one_signal"]
    sk = report.get("signal_or_keyword") or a
    unread = report.get("unread_no_signal_no_keyword") or report["no_signal"]
    c = report["any_consumes"]
    lines = [
        f"CON-251 oracle coverage — {report['measured_on']}",
        f"commander-legal: {report['universe_commander_legal']:,}"
        f"  graded: {report['graded']:,}"
        f"  unknown: {report['unknown_unreadable']:,}",
        f"oracle_signals loaded: {report['oracle_signal_count']}",
        "",
        f"at least one signal fires :  {a['count']:>6,}   {a['pct']}%",
        f"signal OR keyword catalog :  {sk['count']:>6,}   {sk['pct']}%",
        f"unread (no signal, no kw) :  {unread['count']:>6,}   {unread['pct']}%",
        f"any consumes              :  {c['count']:>6,}   {c['pct']}%",
        "",
        "Unread lines (no signal and no keyword), ranked by frequency:",
    ]
    for row in report["unread_lines_ranked"][:40]:
        lines.append(f"  {row['count']:>5}×  {row['line']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="reuse ~/.deckwright/oracle_cards.jsonl")
    ap.add_argument("--write", type=pathlib.Path, nargs="?",
                    const=DEFAULT_REPORT, default=None,
                    help=f"write JSON report (default path: {DEFAULT_REPORT})")
    ap.add_argument("--limit", type=int, default=0,
                    help="grade only the first N legal cards (smoke)")
    args = ap.parse_args()

    meta = ensure_bulk(offline=args.offline)
    print(f"bulk updated_at={meta.get('updated_at', '?')}")
    print(f"cache {CACHE_JSONL}")

    def cards() -> Iterator[dict]:
        n = 0
        for card in iter_oracle_cards():
            if not is_commander_legal(card):
                continue
            yield card
            n += 1
            if args.limit and n >= args.limit:
                return

    report = analyze(cards())
    report["bulk_updated_at"] = meta.get("updated_at")
    report["bulk_fetched_at"] = meta.get("fetched_at")
    text = render_text(report)
    print(text)

    if args.write is not None:
        out = args.write
        out.parent.mkdir(parents=True, exist_ok=True)
        # Preserve a prior comparison block when re-running after a batch so
        # before/after stays visible in the committed report.
        if out.is_file():
            try:
                prev = json.loads(out.read_text())
                if isinstance(prev.get("comparison"), dict):
                    comp = dict(prev["comparison"])
                    before = comp.get("before") or {}
                    prev_after = comp.get("after") or {}
                    after = {
                        "oracle_signal_count": report["oracle_signal_count"],
                        "at_least_one_signal_pct": report["at_least_one_signal"]["pct"],
                        "at_least_one_signal_count": report["at_least_one_signal"]["count"],
                        "any_consumes_pct": report["any_consumes"]["pct"],
                        "any_consumes_count": report["any_consumes"]["count"],
                        "no_signal_pct": report["no_signal"]["pct"],
                        "playstyles_precision_pct": prev_after.get(
                            "playstyles_precision_pct",
                            before.get("playstyles_precision_pct"),
                        ),
                    }
                    if before.get("at_least_one_signal_pct") is not None:
                        comp["after"] = after
                        prev_delta = comp.get("delta") or {}
                        comp["delta"] = {
                            "at_least_one_signal_pp": round(
                                after["at_least_one_signal_pct"]
                                - before["at_least_one_signal_pct"], 1),
                            "any_consumes_pp": round(
                                after["any_consumes_pct"]
                                - before["any_consumes_pct"], 1),
                            "precision_pp": prev_delta.get("precision_pp", 0.0),
                        }
                        report["comparison"] = comp
            except (json.JSONDecodeError, OSError):
                pass
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {out}")
        if out.resolve() == DEFAULT_REPORT.resolve():
            _write_markdown_companion(report, out.with_suffix(".md"))

    return 0


def _write_markdown_companion(report: dict[str, Any], path: pathlib.Path) -> None:
    """Human-readable twin of the JSON report (committed beside it)."""
    comp = report.get("comparison") or {}
    before = comp.get("before") or {}
    after = comp.get("after") or {
        "oracle_signal_count": report["oracle_signal_count"],
        "at_least_one_signal_pct": report["at_least_one_signal"]["pct"],
        "at_least_one_signal_count": report["at_least_one_signal"]["count"],
        "any_consumes_pct": report["any_consumes"]["pct"],
        "any_consumes_count": report["any_consumes"]["count"],
        "no_signal_pct": report["no_signal"]["pct"],
    }
    delta = comp.get("delta") or {}
    lines = [
        "# Oracle coverage report",
        "",
        f"Measured `{report['measured_on']}` on Scryfall bulk `oracle_cards` "
        f"({report.get('bulk_updated_at')}).",
        "",
        f"- Commander-legal universe: **{report['universe_commander_legal']:,}**",
        f"- Graded (looked): **{report['graded']:,}**",
        f"- Unknown (could not look): **{report['unknown_unreadable']:,}**",
        f"- `oracle_signals` loaded: **{report['oracle_signal_count']}**",
        "",
        "## Coverage numbers",
        "",
    ]
    sk = report.get("signal_or_keyword") or report["at_least_one_signal"]
    unread = report.get("unread_no_signal_no_keyword") or report["no_signal"]
    if before:
        def _n(d: dict, key: str) -> str:
            v = d.get(key)
            return f"{v:,}" if isinstance(v, int) else str(v)

        prec_delta = delta.get("precision_pp", 0.0)
        try:
            prec_s = f"{float(prec_delta):+.1f}"
        except (TypeError, ValueError):
            prec_s = str(prec_delta)
        lines += [
            "| metric | before | after | Δ |",
            "|---|---:|---:|---:|",
            (
                f"| at least one signal fires | "
                f"{before.get('at_least_one_signal_pct')}% "
                f"({_n(before, 'at_least_one_signal_count')}) | "
                f"{after.get('at_least_one_signal_pct')}% "
                f"({_n(after, 'at_least_one_signal_count')}) | "
                f"{delta.get('at_least_one_signal_pp', '?')} pp |"
            ),
            (
                f"| signal OR keyword catalog | "
                f"{before.get('signal_or_keyword_pct', before.get('at_least_one_signal_pct'))}% "
                f"({_n(before, 'signal_or_keyword_count') if before.get('signal_or_keyword_count') is not None else '—'}) | "
                f"{after.get('signal_or_keyword_pct', sk.get('pct'))}% "
                f"({_n(after, 'signal_or_keyword_count') if after.get('signal_or_keyword_count') is not None else sk.get('count')}) | "
                f"{delta.get('signal_or_keyword_pp', '?')} pp |"
            ),
            (
                f"| any `consumes` | {before.get('any_consumes_pct')}% "
                f"({_n(before, 'any_consumes_count')}) | "
                f"{after.get('any_consumes_pct')}% "
                f"({_n(after, 'any_consumes_count')}) | "
                f"{delta.get('any_consumes_pp', '?')} pp |"
            ),
            (
                f"| playstyles precision | "
                f"{before.get('playstyles_precision_pct', '?')}% | "
                f"{after.get('playstyles_precision_pct', '?')}% | "
                f"{prec_s} pp |"
            ),
            "",
        ]
    else:
        a = report["at_least_one_signal"]
        c = report["any_consumes"]
        lines += [
            f"- at least one signal fires: {a['count']:,} ({a['pct']}%)",
            f"- signal OR keyword catalog: {sk['count']:,} ({sk['pct']}%)",
            f"- any consumes: {c['count']:,} ({c['pct']}%)",
            "",
        ]
    lines += [
        f"Unread (no signal and no keyword): {unread['count']:,} "
        f"({unread['pct']}%). Unknown is not unread.",
        "",
    ]
    new_sigs = comp.get("new_signals") or []
    if new_sigs:
        hits = report.get("signal_hit_counts") or {}
        lines += ["## Signals shipped this card", ""]
        for s in new_sigs:
            lines.append(f"- `{s}` — {hits.get(s, 0):,} commander-legal hits")
        lines.append("")
        if comp.get("note"):
            lines += [str(comp["note"]), ""]
    lines += [
        "## Remaining unread tail (no signal and no keyword)",
        "",
        "```",
    ]
    for row in report["unread_lines_ranked"][:40]:
        lines.append(f"  {row['count']:>5}×  {row['line']}")
    lines += [
        "```",
        "",
        "## Re-run",
        "",
        "```",
        "python scripts/oracle_coverage.py --write data/oracle_coverage_report.json",
        "python scripts/check_playstyles.py   # precision must not drop",
        "```",
        "",
    ]
    path.write_text("\n".join(lines))
    print(f"wrote {path}")


if __name__ == "__main__":
    sys.exit(main())
