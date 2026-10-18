"""optional Access header gate + per-client rate limit on COSTLY_PATHS."""

from __future__ import annotations

import os
import time
from collections import deque

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

COSTLY_PATHS: frozenset[str] = frozenset({
    "/api/chat",
    "/api/commanders",
    "/api/partners",
    "/api/build",
    "/api/ask",
    "/api/thesis",
    "/api/christen",
})

_ACCESS_HEADER = "cf-access-authenticated-user-email"
_DEFAULT_RATE_LIMIT = 30
_DEFAULT_RATE_WINDOW_SEC = 60

_TRUTHY = frozenset({"1", "true", "yes"})

ACCESS_WHAT = (
    "Cloudflare Access authentication required. "
    "Missing Cf-Access-Authenticated-User-Email."
)
ACCESS_HOW = "Sign in through Cloudflare Access, then reload this page."
RATE_WHAT = "Rate limit exceeded"
RATE_HOW = "Wait a minute and try again."


def access_required() -> bool:
    raw = os.environ.get("DECKWRIGHT_REQUIRE_ACCESS", "").strip().lower()
    return raw in _TRUTHY


def rate_limit() -> int:
    raw = os.environ.get("DECKWRIGHT_RATE_LIMIT", "").strip()
    if not raw:
        return _DEFAULT_RATE_LIMIT
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_RATE_LIMIT
    return n if n > 0 else _DEFAULT_RATE_LIMIT


def rate_window_sec() -> float:
    raw = os.environ.get("DECKWRIGHT_RATE_WINDOW_SEC", "").strip()
    if not raw:
        return float(_DEFAULT_RATE_WINDOW_SEC)
    try:
        n = float(raw)
    except ValueError:
        return float(_DEFAULT_RATE_WINDOW_SEC)
    return n if n > 0 else float(_DEFAULT_RATE_WINDOW_SEC)


def client_key(request: Request) -> str:
    forwarded = (request.headers.get("cf-connecting-ip") or "").strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def reset(self) -> None:
        self._hits.clear()

    def allow(self, key: str, limit: int, window: float,
              now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
        cutoff = t - window
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


_windows = SlidingWindow()


def reset_rate_windows() -> None:
    _windows.reset()


def refusal(status: int, kind: str, what: str, how: str) -> JSONResponse:
    """kind/what/how so index.html fail() does not sniff Scryfall or Claude."""
    return JSONResponse(
        {
            "ok": False,
            "error": kind,
            "kind": kind,
            "status": status,
            "what": what,
            "how": how,
            "detail": what,
        },
        status_code=status,
    )


class DemoGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if access_required():
            email = (request.headers.get(_ACCESS_HEADER) or "").strip()
            if not email:
                return refusal(401, "access_required", ACCESS_WHAT, ACCESS_HOW)
        if request.url.path in COSTLY_PATHS:
            key = client_key(request)
            if not _windows.allow(key, rate_limit(), rate_window_sec()):
                return refusal(429, "demo_rate_limit", RATE_WHAT, RATE_HOW)
        return await call_next(request)


def install_demo_guards(app: FastAPI) -> None:
    app.add_middleware(DemoGuardMiddleware)
