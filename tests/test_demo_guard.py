"""
test_demo_guard.py — Access header gate and costly-path rate limit.

    python test_demo_guard.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import os
import sys
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from deckwright import demo_guard as dg

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

ACCESS = "Cf-Access-Authenticated-User-Email"
ACCESS_WHAT = (
    "Cloudflare Access authentication required. "
    "Missing Cf-Access-Authenticated-User-Email."
)
ACCESS_HOW = "Sign in through Cloudflare Access, then reload this page."
RATE_WHAT = "Rate limit exceeded"
RATE_HOW = "Wait a minute and try again."
COSTLY = (
    "/api/chat",
    "/api/commanders",
    "/api/partners",
    "/api/build",
    "/api/ask",
    "/api/thesis",
    "/api/christen",
)


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def eq_guard(label, response, status, detail, kind, how):
    eq(f"{label} status", response.status_code, status)
    body = response.json()
    eq(f"{label} detail", body.get("detail"), detail)
    eq(f"{label} what", body.get("what"), detail)
    eq(f"{label} how", body.get("how"), how)
    eq(f"{label} kind", body.get("kind"), kind)
    eq(f"{label} error", body.get("error"), kind)
    eq(f"{label} ok", body.get("ok"), False)


@contextmanager
def patched_env(updates: dict[str, str | None]):
    previous: dict[str, str | None] = {}
    for key, value in updates.items():
        previous[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    dg.reset_rate_windows()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        dg.reset_rate_windows()


def make_app() -> FastAPI:
    app = FastAPI()
    dg.install_demo_guards(app)

    @app.get("/api/boot")
    def boot():
        return {"ready": True}

    @app.get("/api/saved")
    def saved():
        return []

    @app.get("/")
    def index():
        return {"ok": True}

    for path in COSTLY:
        def handler(p=path):
            return {"ok": True, "path": p}

        app.add_api_route(path, handler, methods=["POST"])
    return app


def _request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("10.0.0.8", 50000),
) -> Request:
    raw = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/boot",
        "raw_path": b"/api/boot",
        "query_string": b"",
        "headers": raw,
        "client": client,
        "server": ("test", 80),
    }
    return Request(scope)


def test_access_flag_values():
    print("\naccess_required()  — 1, true, or yes only")
    cases = (
        (None, False),
        ("", False),
        ("0", False),
        ("no", False),
        ("on", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("Yes", True),
        ("  true  ", True),
    )
    for raw, want in cases:
        with patched_env({"DECKWRIGHT_REQUIRE_ACCESS": raw}):
            eq(f"flag {raw!r}", dg.access_required(), want)


def test_rate_env_and_window():
    print("\nrate env + SlidingWindow  — 30 hits / 60s, per key")
    with patched_env({
        "DECKWRIGHT_RATE_LIMIT": None,
        "DECKWRIGHT_RATE_WINDOW_SEC": None,
    }):
        eq("default limit", dg.rate_limit(), 30)
        eq("default window", dg.rate_window_sec(), 60.0)
        window = dg.SlidingWindow()
        allowed = [
            window.allow("a", dg.rate_limit(), dg.rate_window_sec(), now=0.0)
            for _ in range(30)
        ]
        eq("thirty allows", allowed, [True] * 30)
        eq("thirty-first denied", window.allow("a", 30, 60.0, now=0.0), False)
        eq("other key allowed", window.allow("b", 30, 60.0, now=0.0), True)
        eq("inside window denied", window.allow("a", 30, 60.0, now=59.0), False)
        eq("window edge allows", window.allow("a", 30, 60.0, now=60.0), True)

    with patched_env({
        "DECKWRIGHT_RATE_LIMIT": "2",
        "DECKWRIGHT_RATE_WINDOW_SEC": "10",
    }):
        eq("override limit", dg.rate_limit(), 2)
        eq("override window", dg.rate_window_sec(), 10.0)
        window = dg.SlidingWindow()
        eq("first", window.allow("a", 2, 10.0, now=0.0), True)
        eq("second", window.allow("a", 2, 10.0, now=0.0), True)
        eq("third denied", window.allow("a", 2, 10.0, now=0.0), False)
        eq("aged out", window.allow("a", 2, 10.0, now=10.0), True)

    for raw in ("nope", "0", "-3"):
        with patched_env({"DECKWRIGHT_RATE_LIMIT": raw}):
            eq(f"limit {raw!r} falls back", dg.rate_limit(), 30)


def test_client_key():
    print("\nclient_key()  — CF-Connecting-IP, then socket, then unknown")
    eq(
        "connecting ip",
        dg.client_key(_request({"CF-Connecting-IP": "203.0.113.9"})),
        "203.0.113.9",
    )
    eq(
        "stripped connecting ip",
        dg.client_key(_request({"CF-Connecting-IP": "  203.0.113.8  "})),
        "203.0.113.8",
    )
    eq(
        "blank connecting ip falls through",
        dg.client_key(_request({"CF-Connecting-IP": "   "})),
        "10.0.0.8",
    )
    eq("socket host", dg.client_key(_request()), "10.0.0.8")
    eq("no client", dg.client_key(_request(client=None)), "unknown")


def test_registry():
    print("\nCOSTLY_PATHS  — the seven model routes")
    eq("registry", dg.COSTLY_PATHS, frozenset(COSTLY))


def test_flag_off_open():
    print("\nflag off  — boot and a costly POST need no Access header")
    with patched_env({
        "DECKWRIGHT_REQUIRE_ACCESS": None,
        "DECKWRIGHT_RATE_LIMIT": "30",
    }):
        client = TestClient(make_app())
        eq("GET /api/boot 200", client.get("/api/boot").status_code, 200)
        chat = client.post("/api/chat", json={})
        eq("POST /api/chat 200", chat.status_code, 200)
        eq("GET /api/saved 200", client.get("/api/saved").status_code, 200)


def test_flag_on_requires_header():
    print("\nflag on  — missing header is 401, present header is not")
    client = TestClient(make_app())
    for flag in ("1", "true", "TRUE", "yes"):
        with patched_env({"DECKWRIGHT_REQUIRE_ACCESS": flag}):
            missing = client.get("/api/boot")
            eq_guard(
                f"{flag} boot",
                missing,
                401,
                ACCESS_WHAT,
                "access_required",
                ACCESS_HOW,
            )
            present = client.get("/api/boot", headers={ACCESS: "a@b.co"})
            eq(f"{flag} header allows boot", present.status_code, 200)
            folded = client.get(
                "/api/boot",
                headers={"cf-access-authenticated-user-email": "a@b.co"},
            )
            eq(f"{flag} lowercase header allows boot", folded.status_code, 200)
            blank = client.get("/api/boot", headers={ACCESS: "   "})
            eq(f"{flag} blank header 401", blank.status_code, 401)
            chat = client.post("/api/chat", json={}, headers={ACCESS: "a@b.co"})
            eq(f"{flag} costly POST with header 200", chat.status_code, 200)
            page = client.get("/")
            eq(f"{flag} page without header 401", page.status_code, 401)


def test_rate_limit_costly_only():
    print("\nrate limit  — costly POSTs 429, other routes stay 200")
    with patched_env({
        "DECKWRIGHT_REQUIRE_ACCESS": None,
        "DECKWRIGHT_RATE_LIMIT": "1",
        "DECKWRIGHT_RATE_WINDOW_SEC": "60",
    }):
        app = make_app()
        client = TestClient(app, client=("198.51.100.10", 50000))
        for i in range(5):
            eq(f"boot before limit {i}", client.get("/api/boot").status_code, 200)
        first = client.post("/api/chat", json={})
        eq("first costly POST allowed", first.status_code, 200)
        for path in COSTLY:
            blocked = client.post(path, json={})
            eq_guard(
                path,
                blocked,
                429,
                RATE_WHAT,
                "demo_rate_limit",
                RATE_HOW,
            )
        queried = client.post("/api/build?x=1", json={})
        eq("query string still costly", queried.status_code, 429)
        eq("boot after limit", client.get("/api/boot").status_code, 200)
        eq("saved after limit", client.get("/api/saved").status_code, 200)
        eq("page after limit", client.get("/").status_code, 200)
        other = TestClient(app, client=("198.51.100.11", 50000))
        eq("other socket allowed", other.post("/api/chat", json={}).status_code, 200)
        via_header = client.post(
            "/api/chat",
            json={},
            headers={"CF-Connecting-IP": "203.0.113.50"},
        )
        eq("CF-Connecting-IP is its own bucket", via_header.status_code, 200)
        again = client.post(
            "/api/chat",
            json={},
            headers={"CF-Connecting-IP": "203.0.113.50"},
        )
        eq("same CF-Connecting-IP then 429", again.status_code, 429)
        blank_ip = client.post(
            "/api/chat",
            json={},
            headers={"CF-Connecting-IP": "   "},
        )
        eq("blank CF-Connecting-IP uses the socket bucket", blank_ip.status_code, 429)


def test_real_app_wire():
    print("\napp.py  — install_demo_guards on the live app")
    from deckwright import app as deck

    client = TestClient(deck.app)
    with patched_env({
        "DECKWRIGHT_REQUIRE_ACCESS": None,
        "DECKWRIGHT_RATE_LIMIT": "30",
    }):
        boot = client.get("/api/boot")
        eq("live boot 200 when flag off", boot.status_code, 200)
        chat = client.post("/api/chat", json={})
        (ok if chat.status_code != 401 else bad)(
            "live chat is not 401 when flag off", f"status {chat.status_code}")
    with patched_env({"DECKWRIGHT_REQUIRE_ACCESS": "1"}):
        missing = client.get("/api/boot")
        eq_guard(
            "live boot",
            missing,
            401,
            ACCESS_WHAT,
            "access_required",
            ACCESS_HOW,
        )
        present = client.get("/api/boot", headers={ACCESS: "a@b.co"})
        eq("live boot 200 with header", present.status_code, 200)
    with patched_env({
        "DECKWRIGHT_REQUIRE_ACCESS": None,
        "DECKWRIGHT_RATE_LIMIT": "1",
        "DECKWRIGHT_RATE_WINDOW_SEC": "60",
    }):
        first = client.post("/api/build", json={})
        (ok if first.status_code not in (401, 429) else bad)(
            "live build under the limit is not blocked",
            f"status {first.status_code}")
        second = client.post("/api/build", json={})
        eq_guard(
            "live build",
            second,
            429,
            RATE_WHAT,
            "demo_rate_limit",
            RATE_HOW,
        )
        eq("live boot still 200", client.get("/api/boot").status_code, 200)


def main() -> int:
    test_access_flag_values()
    test_rate_env_and_window()
    test_client_key()
    test_registry()
    test_flag_off_open()
    test_flag_on_requires_header()
    test_rate_limit_costly_only()
    test_real_app_wire()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
