"""Turn caught exceptions into safe, client-facing values.

Exception text can carry internal detail (URLs, file paths, library
messages). The full exception goes to the server log; responses only get a
short description, the exception class name, and plain status fields.
"""

from __future__ import annotations

from typing import Iterable

# Every HTTP status a refusal can report, mapped to itself. Looking the code up
# here copies it out as a plain int rather than passing the exception's own
# attribute through to the response.
_HTTP_STATUS = {code: code for code in range(100, 600)}


def log_exception(where: str, e: BaseException) -> None:
    """Write the full exception to the server log (stdout)."""
    print(f"  [{where}] {type(e).__name__}: {e}")


def failure_note(where: str, e: BaseException) -> str:
    """Log `e` and return a short note that is safe to show in the UI."""
    log_exception(where, e)
    return f"{where}: failed ({type(e).__name__})"


def known_kind(raw: object, kinds: Iterable[str], default: str = "transport") -> str:
    """Return the matching name from `kinds`, or `default` when there is none."""
    lookup = {k: k for k in kinds}
    return lookup.get(raw, default) if isinstance(raw, str) else default


def plain_status(raw: object) -> int:
    """An HTTP status code as a plain int, or 0 when it is missing or invalid."""
    return _HTTP_STATUS.get(raw, 0) if isinstance(raw, int) else 0
