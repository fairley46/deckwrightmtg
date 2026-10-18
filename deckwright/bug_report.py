"""File a bug report to disk. External issue trackers are not wired in this tree.

Disk is the commitment. Reports land under the configured bugs directory as
JSON. Optional enrichment hooks remain injectable for tests, but the default
path never calls a remote tracker and never embeds workspace IDs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]

KeyFn = Callable[[], str | None]
CreateFn = Callable[..., dict[str, str]]


class LinearError(Exception):
    """Kept for test compatibility; remote filing is disabled."""


def app_version() -> str | None:
    """Short git SHA of this checkout, if git can answer. Else None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (out.stdout or "").strip()
    return sha or None


def read_linear_api_key() -> str | None:
    """Remote filing disabled — always None."""
    return None


def issue_title(record: dict[str, Any]) -> str:
    """`Bug: <first line>`, trimmed."""
    note = str(record.get("note") or "")
    first = ""
    for line in note.splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        first = "(no description)"
    max_body = 190
    if len(first) > max_body:
        first = first[: max_body - 1].rstrip() + "…"
    return f"Bug: {first}"


def issue_description(record: dict[str, Any]) -> str:
    """Her words verbatim, then the context the page already knew."""
    note = str(record.get("note") or "").strip()
    ctx = record.get("context") or {}
    if not isinstance(ctx, dict):
        ctx = {}

    lines = [note, "", "---", ""]

    def row(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        lines.append(f"**{label}:** {value}")

    row("Page", ctx.get("page"))
    row("Deck id", ctx.get("deck_id"))
    row("Commander", ctx.get("commander"))
    row("Deck name", ctx.get("deck_name"))
    row("Viewport", ctx.get("viewport"))
    row("URL", ctx.get("url"))
    version = record.get("app_version") or ctx.get("app_version")
    row("App version", version)
    row("User agent", ctx.get("user_agent"))
    row("Report id", record.get("id"))
    row("Reported at", record.get("reported_at"))

    errors = ctx.get("errors") or []
    if isinstance(errors, list) and errors:
        lines.append("")
        lines.append("### Captured JS errors")
        lines.append("```")
        for err in errors:
            lines.append(str(err)[:500])
        lines.append("```")
    else:
        lines.append("")
        lines.append("_No JS errors were captured before she sent this._")

    return "\n".join(lines).rstrip() + "\n"


def create_linear_issue(title: str, description: str, *, api_key: str) -> dict[str, str]:
    """Remote filing disabled."""
    raise LinearError("remote bug filing is disabled in this tree")


def _stamp(path: Path, record: dict[str, Any]) -> None:
    path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def file_report_to_linear(
    record: dict[str, Any],
    path: Path,
    *,
    key_fn: KeyFn | None = None,
    create_fn: CreateFn | None = None,
) -> dict[str, Any]:
    """Optionally enrich a disk report. Default: record that filing is skipped.

    Call only after the file already exists on disk. Injected `key_fn` /
    `create_fn` keep offline tests able to exercise success and failure paths.
    """
    key_fn = key_fn or read_linear_api_key
    create_fn = create_fn or create_linear_issue

    if record.get("linear"):
        return {"ok": True, "skipped": True, "linear": record["linear"]}

    key = key_fn()
    if not key:
        err = "remote bug filing disabled (no tracker key)"
        record.pop("linear", None)
        record["linear_error"] = err
        _stamp(path, record)
        return {"ok": False, "error": err}

    try:
        issue = create_fn(
            issue_title(record),
            issue_description(record),
            api_key=key,
        )
    except LinearError as e:
        record.pop("linear", None)
        record["linear_error"] = str(e)
        _stamp(path, record)
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — stamp anything unexpected
        err = f"{type(e).__name__}: {e}"
        record.pop("linear", None)
        record["linear_error"] = err
        _stamp(path, record)
        return {"ok": False, "error": err}

    stamp_id = issue.get("identifier") or issue.get("id")
    record["linear"] = {
        "id": str(stamp_id),
        "url": str(issue["url"]),
    }
    record.pop("linear_error", None)
    _stamp(path, record)
    return {"ok": True, "linear": record["linear"]}


def sync_bug_dir(
    directory: Path,
    *,
    key_fn: KeyFn | None = None,
    create_fn: CreateFn | None = None,
) -> dict[str, int]:
    """Walk `directory` and attempt enrichment for every report without a stamp."""
    filed = skipped = errors = unreadable = 0
    if not directory.exists():
        return {"filed": 0, "skipped": 0, "errors": 0, "unreadable": 0}

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
            skipped += 1
            continue
        result = file_report_to_linear(
            record, path, key_fn=key_fn, create_fn=create_fn)
        if result.get("ok"):
            filed += 1
        else:
            errors += 1
    return {
        "filed": filed,
        "skipped": skipped,
        "errors": errors,
        "unreadable": unreadable,
    }
