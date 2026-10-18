#!/usr/bin/env python3
"""
print_wave_reeval.py — print-wave update path.

One command after a major set / precon dump (or when someone claims a mechanic
is missing from reading). Refreshes understanding numbers, holds the precision
floor, runs offline product spot-checks, and writes a dated report.

Does **not** invent oracle_signals, soften verifiers, or re-run live Gate 1
Playwright (Claude owns that). Gate 1 status is recorded from the last retest
doc, or overridden with --gate1-status.

    python scripts/print_wave_reeval.py
    python scripts/print_wave_reeval.py --refresh          # cadence first
    python scripts/print_wave_reeval.py --fetch            # re-download bulk
    python scripts/print_wave_reeval.py --skip-coverage    # reuse report JSON

Exit 1 when precision < 88%, coverage unknown, or a spot-check fails.
Unknown (could not look) is never scored as a real zero.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import check_playstyles as cps  # noqa: E402

DEFAULT_COVERAGE = REPO / "data" / "oracle_coverage_report.json"
DEFAULT_REPORT = REPO / "data" / "print_wave_reeval.json"
DEFAULT_MD = REPO / "data" / "print_wave_reeval.md"
GATE1_RETEST = (
    REPO / "testdata" / "review" / "2026-08-08-gate1-RETEST.md"
)
PRECISION_FLOOR = 0.88

# Offline core product checks — no model calls.
SPOT_CHECKS = (
    "test_instrument_chat.py",
    "test_way_preference.py",
    "test_thesis_glued.py",
    "test_precon_upgrade.py",
    "test_saved_ask_instrument.py",  # saved-deck ask instrument; skip if absent
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_cmd(argv: list[str], *, cwd: pathlib.Path = REPO) -> dict[str, Any]:
    """Run a subprocess. Failure is unknown/fail — never invent exit 0."""
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, check=False,
        )
    except OSError as e:
        return {
            "ok": False,
            "status": "unknown",
            "argv": argv,
            "error": str(e),
            "exit_code": None,
        }
    return {
        "ok": proc.returncode == 0,
        "status": "ok" if proc.returncode == 0 else "fail",
        "argv": argv,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def parse_gate1_retest(path: pathlib.Path) -> dict[str, Any]:
    """Read last Gate 1 retest note. Missing file = unknown, not green."""
    if not path.is_file():
        return {
            "status": "unknown",
            "path": str(path.relative_to(REPO)),
            "note": "retest doc missing — could not look",
            "unclassifiable": None,
        }
    text = path.read_text(encoding="utf-8")
    m = re.search(
        r"Unclassifiable\s+\*\*([0-9]+\s*/\s*[0-9]+\s*/\s*[0-9]+)\*\*",
        text, re.I,
    )
    if not m:
        m = re.search(
            r"unclassifiable[^\n]*?([0-9]+\s*/\s*[0-9]+\s*/\s*[0-9]+)",
            text, re.I,
        )
    unclass = m.group(1).replace(" ", "") if m else None
    green = unclass == "0/0/0" if unclass else False
    return {
        "status": "green" if green else ("unknown" if unclass is None else "fail"),
        "path": str(path.relative_to(REPO)),
        "unclassifiable": unclass,
        "note": (
            "last recorded retest — live Playwright re-run is Claude-owned"
        ),
    }


def load_coverage(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "unknown",
            "path": str(path),
            "error": "coverage report missing — run without --skip-coverage",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "unknown",
            "path": str(path),
            "error": f"could not read coverage report: {e}",
        }
    sig = data.get("at_least_one_signal") or {}
    sk = data.get("signal_or_keyword") or {}
    unread = data.get("unread_no_signal_no_keyword") or {}
    consumes = data.get("any_consumes") or {}
    ranked = data.get("unread_lines_ranked") or []
    top = [
        {"count": r.get("count"), "line": r.get("line")}
        for r in ranked[:12]
        if isinstance(r, dict)
    ]
    return {
        "status": "ok",
        "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "measured_on": data.get("measured_on"),
        "universe_commander_legal": data.get("universe_commander_legal"),
        "unknown_unreadable": data.get("unknown_unreadable"),
        "graded": data.get("graded"),
        "at_least_one_signal_pct": sig.get("pct"),
        "at_least_one_signal_count": sig.get("count"),
        "signal_or_keyword_pct": sk.get("pct"),
        "signal_or_keyword_count": sk.get("count"),
        "any_consumes_pct": consumes.get("pct"),
        "any_consumes_count": consumes.get("count"),
        "unread_pct": unread.get("pct"),
        "unread_count": unread.get("count"),
        "oracle_signal_count": data.get("oracle_signal_count"),
        "unread_lines_top": top,
        "bulk_updated_at": data.get("bulk_updated_at"),
    }


def run_spot_checks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in SPOT_CHECKS:
        path = REPO / "tests" / name
        if not path.is_file():
            out.append({
                "test": name,
                "status": "skipped",
                "ok": True,
                "note": "file absent (expected until that card merges)",
            })
            continue
        result = run_cmd([sys.executable, str(path)])
        out.append({
            "test": name,
            "status": result["status"],
            "ok": result["ok"],
            "exit_code": result.get("exit_code"),
            "stderr_tail": result.get("stderr_tail", "")[-500:],
        })
    return out


def assemble_report(
    *,
    coverage: dict[str, Any],
    playstyles: dict[str, Any],
    spot_checks: list[dict[str, Any]],
    gate1: dict[str, Any],
    refresh: dict[str, Any] | None,
    wave: str | None,
) -> dict[str, Any]:
    prec = playstyles.get("precision")
    prec_ok = bool(playstyles.get("precision_ok"))
    coverage_ok = coverage.get("status") == "ok"
    spots_ok = all(s.get("ok") for s in spot_checks)
    gate_status = gate1.get("status")
    # Gate 1 unknown (no live re-run this session) does not fail the script —
    # it is reported honestly. Precision / coverage / spot-checks do.
    overall = "ok" if (coverage_ok and prec_ok and spots_ok) else "fail"
    if coverage.get("status") == "unknown" or playstyles.get("status") == "unknown":
        overall = "fail"

    return {
        "measured_on": _today(),
        "measured_at": _now_iso(),
        "wave": wave or "baseline-reeval",
        "overall": overall,
        "refresh": refresh,
        "coverage": coverage,
        "playstyles": {
            "status": playstyles.get("status", "ok"),
            "fixture_rows": playstyles.get("fixture_rows"),
            "scored": playstyles.get("scored"),
            "excluded": playstyles.get("excluded"),
            "hit": playstyles.get("hit"),
            "miss": playstyles.get("miss"),
            "extra": playstyles.get("extra"),
            "precision": round(float(prec), 4) if prec is not None else None,
            "precision_pct": (
                round(float(prec) * 100, 1) if prec is not None else None
            ),
            "recall": (
                round(float(playstyles["recall"]), 4)
                if playstyles.get("recall") is not None else None
            ),
            "recall_pct": (
                round(float(playstyles["recall"]) * 100, 1)
                if playstyles.get("recall") is not None else None
            ),
            "precision_floor": PRECISION_FLOOR,
            "precision_ok": prec_ok,
        },
        "product_spot_checks": spot_checks,
        "gate1": gate1,
        "next_actions": _next_actions(coverage, prec_ok, gate1),
    }


def _next_actions(
    coverage: dict[str, Any],
    prec_ok: bool,
    gate1: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if coverage.get("status") != "ok":
        actions.append(
            "Coverage unknown — re-run without --skip-coverage; "
            "do not treat unread as measured."
        )
        return actions
    top = coverage.get("unread_lines_top") or []
    if top:
        first = top[0]
        actions.append(
            f"Name the gap: {first.get('count')}× unread line "
            f"{first.get('line')!r} (see unread_lines_top)."
        )
    if not prec_ok:
        actions.append(
            "Precision below 88% — do not admit new ways; investigate extras."
        )
    if gate1.get("status") != "green":
        actions.append(
            "Gate 1 not confirmed green in this report — Claude live retest "
            "or file named defects (pirates / cavalry / angels)."
        )
    else:
        actions.append(
            "If a real unread cluster moved after a named set dump: one small "
            "admit PR (exemplar + near-miss in test_synergy.py). "
            "Refuse keyword-catalog oracle_signals."
        )
    actions.append(
        "Update docs/KNOW-MAGIC-MAINTENANCE.md with "
        "these dated numbers — do not silently bump."
    )
    return actions


def render_markdown(report: dict[str, Any]) -> str:
    cov = report["coverage"]
    ps = report["playstyles"]
    g1 = report["gate1"]
    lines = [
        f"# Print-wave re-eval — {report['measured_on']}",
        "",
        f"**wave:** `{report['wave']}` · "
        f"**overall:** `{report['overall']}`",
        "",
        "## Coverage",
        "",
    ]
    if cov.get("status") != "ok":
        lines += [
            f"Status: **unknown** — {cov.get('error', 'could not look')}",
            "",
        ]
    else:
        lines += [
            f"| metric | value |",
            f"|---|---:|",
            f"| at least one signal | {cov.get('at_least_one_signal_pct')}% |",
            f"| signal ∨ keyword | {cov.get('signal_or_keyword_pct')}% |",
            f"| any consumes | {cov.get('any_consumes_pct')}% |",
            f"| unread (no signal, no keyword) | {cov.get('unread_pct')}% |",
            f"| oracle_signals | {cov.get('oracle_signal_count')} |",
            f"| graded / universe | {cov.get('graded')} / "
            f"{cov.get('universe_commander_legal')} |",
            f"| unknown unreadable | {cov.get('unknown_unreadable')} |",
            "",
            "### Top unread lines",
            "",
            "```",
        ]
        for row in cov.get("unread_lines_top") or []:
            lines.append(f"  {row.get('count'):>5}×  {row.get('line')}")
        lines += ["```", ""]

    lines += [
        "## Playstyles",
        "",
        f"- precision: **{ps.get('precision_pct')}%** "
        f"(floor {int(PRECISION_FLOOR * 100)}%; ok={ps.get('precision_ok')})",
        f"- recall: {ps.get('recall_pct')}%",
        f"- HIT/MISS/EXTRA: {ps.get('hit')} / {ps.get('miss')} / {ps.get('extra')}",
        f"- scored / excluded: {ps.get('scored')} / {ps.get('excluded')} "
        f"(excluded ≠ miss)",
        "",
        "## Product spot-checks (offline)",
        "",
    ]
    for s in report["product_spot_checks"]:
        lines.append(
            f"- `{s['test']}`: {s['status']}"
            + (f" — {s['note']}" if s.get("note") else "")
        )
    lines += [
        "",
        "## Gate 1",
        "",
        f"- status: `{g1.get('status')}`",
        f"- unclassifiable: {g1.get('unclassifiable')}",
        f"- source: `{g1.get('path')}`",
        f"- note: {g1.get('note')}",
        "",
        "## Next actions",
        "",
    ]
    for a in report.get("next_actions") or []:
        lines.append(f"- {a}")
    lines += [
        "",
        "## Re-run",
        "",
        "```bash",
        "source .venv/bin/activate",
        "python scripts/print_wave_reeval.py",
        "python scripts/print_wave_reeval.py --refresh   # after a real set dump",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--refresh", action="store_true",
        help="run scripts/refresh_data_cadence.sh before coverage",
    )
    ap.add_argument(
        "--fetch", action="store_true",
        help="re-download Scryfall oracle bulk (default: --offline cache)",
    )
    ap.add_argument(
        "--skip-coverage", action="store_true",
        help="reuse existing data/oracle_coverage_report.json",
    )
    ap.add_argument(
        "--wave", default="",
        help="named print wave label (e.g. eoe-commander)",
    )
    ap.add_argument(
        "--gate1-status",
        choices=("green", "fail", "unknown", "from-doc"),
        default="from-doc",
        help="override Gate 1 status (default: parse last retest doc)",
    )
    ap.add_argument(
        "--write", type=pathlib.Path, default=DEFAULT_REPORT,
        help=f"JSON report path (default: {DEFAULT_REPORT})",
    )
    ap.add_argument(
        "--md", type=pathlib.Path, default=DEFAULT_MD,
        help=f"Markdown report path (default: {DEFAULT_MD})",
    )
    ap.add_argument(
        "--coverage-path", type=pathlib.Path, default=DEFAULT_COVERAGE,
        help="oracle coverage JSON to write/read",
    )
    args = ap.parse_args()

    refresh_result: dict[str, Any] | None = None
    if args.refresh:
        print("== refresh_data_cadence.sh ==")
        refresh_result = run_cmd(["bash", "scripts/refresh_data_cadence.sh"])
        print(refresh_result.get("stdout_tail", "")[-2000:])
        if not refresh_result["ok"]:
            print("refresh failed — continuing to measure what we can; "
                  "overall will fail if coverage/playstyles break",
                  file=sys.stderr)

    if not args.skip_coverage:
        print("== oracle_coverage.py ==")
        cov_argv = [sys.executable, "scripts/oracle_coverage.py",
                    "--write", str(args.coverage_path)]
        if not args.fetch:
            cov_argv.append("--offline")
        cov_run = run_cmd(cov_argv)
        print(cov_run.get("stdout_tail", "")[-2000:])
        if not cov_run["ok"]:
            coverage = {
                "status": "unknown",
                "path": str(args.coverage_path),
                "error": (
                    f"oracle_coverage exit {cov_run.get('exit_code')}: "
                    f"{(cov_run.get('stderr_tail') or '')[:400]}"
                ),
            }
        else:
            coverage = load_coverage(args.coverage_path)
    else:
        print("== skip coverage (reuse report) ==")
        coverage = load_coverage(args.coverage_path)

    print("== check_playstyles ==")
    try:
        ps_raw = cps.evaluate()
        ps_raw["status"] = "ok"
    except Exception as e:  # noqa: BLE001 — surface as unknown, not fake zero
        ps_raw = {
            "status": "unknown",
            "precision": None,
            "precision_ok": False,
            "error": str(e),
        }
        print(f"playstyles evaluate unknown: {e}", file=sys.stderr)
    else:
        print(
            f"precision={ps_raw['precision']:.1%} "
            f"recall={ps_raw['recall']:.1%} "
            f"HIT={ps_raw['hit']} EXTRA={ps_raw['extra']} "
            f"ok={ps_raw['precision_ok']}"
        )

    print("== product spot-checks ==")
    spot_checks = run_spot_checks()
    for s in spot_checks:
        print(f"  [{s['status']}] {s['test']}")

    if args.gate1_status == "from-doc":
        gate1 = parse_gate1_retest(GATE1_RETEST)
    else:
        gate1 = {
            "status": args.gate1_status,
            "path": str(GATE1_RETEST.relative_to(REPO)),
            "unclassifiable": "0/0/0" if args.gate1_status == "green" else None,
            "note": "overridden via --gate1-status",
        }
    print(f"== gate1: {gate1['status']} ({gate1.get('unclassifiable')}) ==")

    report = assemble_report(
        coverage=coverage,
        playstyles=ps_raw,
        spot_checks=spot_checks,
        gate1=gate1,
        refresh=refresh_result,
        wave=args.wave or None,
    )

    args.write.parent.mkdir(parents=True, exist_ok=True)
    args.write.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_text = render_markdown(report)
    args.md.write_text(md_text, encoding="utf-8")
    print()
    print(md_text)
    print(f"wrote {args.write}")
    print(f"wrote {args.md}")

    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
