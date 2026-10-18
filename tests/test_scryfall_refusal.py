"""test_scryfall_refusal.py — name Scryfall refusals plainly.

Challenge, rate limit, and timeout must be distinguishable. A Cloudflare
challenge is not a bug, and it must not look like one. Retries must not get
more aggressive than today.
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import app  # noqa: E402
from deckwright import boot as boot_mod  # noqa: E402

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" — {extra}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" — {extra}" if extra else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def _resp(status, *, headers=None, text="", json_data=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.text = text
    r.json = MagicMock(return_value=json_data if json_data is not None else {})
    r.raise_for_status = MagicMock(
        side_effect=app.httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=r
        )
    )
    return r


async def test_challenge_header_raises_named_kind():
    """403 + cf-mitigated: challenge → kind=challenge, one attempt, no backoff loop."""
    print("\nscry()  — Cloudflare challenge (header) is a named refusal")
    resp = _resp(403, headers={"cf-mitigated": "challenge"},
                 text="<html>Just a moment...</html>")
    get = AsyncMock(return_value=resp)
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock) as sleep:
        mock_client.return_value.__aenter__.return_value.get = get
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("challenge should raise ScryfallError")
            return
        except app.ScryfallError as e:
            eq("challenge kind", e.kind, "challenge")
            eq("challenge status", e.status, 403)
            (ok if e.kind == "challenge" else bad)(
                "challenge is not collapsed into unreachable", e.kind)
    eq("challenge does not retry", get.await_count, 1)
    eq("challenge does not sleep/backoff", sleep.await_count, 0)


async def test_challenge_body_just_a_moment():
    """403 body containing 'Just a moment' is a challenge even without the header."""
    print("\nscry()  — Cloudflare challenge (body) is a named refusal")
    resp = _resp(403, headers={}, text="<title>Just a moment...</title>")
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock):
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=resp)
        try:
            await app.scry("/cards/named", exact="Sol Ring")
            bad("body challenge should raise")
        except app.ScryfallError as e:
            eq("body challenge kind", e.kind, "challenge")


async def test_plain_403_is_not_challenge():
    """A bare 403 without challenge markers is unreachable — not the robot check."""
    print("\nscry()  — plain 403 is unreachable, not challenge")
    resp = _resp(403, headers={}, text='{"object":"error","details":"nope"}')
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock):
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=resp)
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("plain 403 should raise")
        except app.ScryfallError as e:
            eq("plain 403 kind", e.kind, "unreachable")
            (ok if e.kind != "challenge" else bad)(
                "plain 403 is not labeled challenge")


async def test_rate_limit_kind():
    """Persistent 429 → kind=rate_limit, distinct from challenge."""
    print("\nscry()  — 429 is rate_limit")
    resp = _resp(429, headers={"Retry-After": "0"})
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock):
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=resp)
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("429 should raise")
        except app.ScryfallError as e:
            eq("429 kind", e.kind, "rate_limit")
            eq("429 status", e.status, 429)


async def test_timeout_kind():
    """httpx timeout → kind=timeout, no invented retries."""
    print("\nscry()  — timeout is its own kind")
    # Reset the rate gate so a MIN_GAP sleep is not mistaken for a retry.
    app._last[0] = 0.0
    get = AsyncMock(side_effect=app.httpx.TimeoutException("read timed out"))
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock) as sleep:
        mock_client.return_value.__aenter__.return_value.get = get
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("timeout should raise")
        except app.ScryfallError as e:
            eq("timeout kind", e.kind, "timeout")
    eq("timeout attempts once", get.await_count, 1)
    # Rate-gate sleep is allowed; retry/backoff sleeps are not.
    eq("timeout does not add backoff retries", sleep.await_count, 0)


async def test_maintenance_kind_no_retry():
    """503 Offline for Maintenance → kind=maintenance, no backoff retries.

    a planned outage must not burn the 429/5xx retry budget, and must
    not collapse into \"didn't answer.\"
    """
    print("\nscry()  — maintenance window is a named refusal")
    app._last[0] = 0.0
    resp = _resp(
        503,
        text="<html><title>Offline for Maintenance</title>"
             "<p>Scryfall is offline for maintenance.</p></html>",
    )
    get = AsyncMock(return_value=resp)
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock) as sleep:
        mock_client.return_value.__aenter__.return_value.get = get
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("maintenance should raise ScryfallError")
            return
        except app.ScryfallError as e:
            eq("maintenance kind", e.kind, "maintenance")
            eq("maintenance status", e.status, 503)
            (ok if e.kind == "maintenance" else bad)(
                "maintenance is not collapsed into unreachable", e.kind)
    eq("maintenance does not retry", get.await_count, 1)
    eq("maintenance does not sleep/backoff", sleep.await_count, 0)


async def test_incidental_maintenance_word_is_retried():
    """503 body that mentions 'maintenance' in passing is not a planned outage.

    the predicate must require Scryfall's phrase. A generic Cloudflare /
    upstream 503 that happens to contain the word must still exhaust the retry
    budget and land as unreachable — not skip retry as kind=maintenance.
    """
    print("\nscry()  — incidental 'maintenance' in a 503 is retried")
    app._last[0] = 0.0
    resp = _resp(
        503,
        text="<html><title>Error 503</title>"
             "<p>Please try again later. This page is under "
             "maintenance by our CDN edge.</p></html>",
    )
    get = AsyncMock(return_value=resp)
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock) as sleep:
        mock_client.return_value.__aenter__.return_value.get = get
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("incidental maintenance 503 should raise after retries")
            return
        except app.ScryfallError as e:
            eq("incidental maintenance kind", e.kind, "unreachable")
            (ok if e.kind != "maintenance" else bad)(
                "incidental maintenance is not labeled maintenance", e.kind)
    eq("incidental maintenance exhausts retries",
       get.await_count, app._SCRYFALL_RETRIES)
    (ok if sleep.await_count >= app._SCRYFALL_RETRIES - 1 else bad)(
        "incidental maintenance sleeps between retries",
        f"sleeps={sleep.await_count}")


async def test_plain_503_is_retried_then_unreachable():
    """503 with unrelated body → retry budget, then kind=unreachable."""
    print("\nscry()  — plain 503 is retried, then unreachable")
    app._last[0] = 0.0
    resp = _resp(503, text="<html><title>Service Unavailable</title></html>")
    get = AsyncMock(return_value=resp)
    with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
         patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock) as sleep:
        mock_client.return_value.__aenter__.return_value.get = get
        try:
            await app.scry("/cards/search", q="t:elf")
            bad("plain 503 should raise after retries")
            return
        except app.ScryfallError as e:
            eq("plain 503 kind", e.kind, "unreachable")
            eq("plain 503 status", e.status, 503)
            (ok if e.kind != "maintenance" else bad)(
                "plain 503 is not labeled maintenance", e.kind)
    eq("plain 503 exhausts retries", get.await_count, app._SCRYFALL_RETRIES)
    (ok if sleep.await_count >= app._SCRYFALL_RETRIES - 1 else bad)(
        "plain 503 sleeps between retries", f"sleeps={sleep.await_count}")


async def test_refusal_copy_is_distinct():
    """Each kind has its own sentence — challenge ≠ rate limit ≠ timeout ≠ maintenance."""
    print("\nscryfall_refusal_copy()  — distinct sentences")
    challenge = app.scryfall_refusal_copy("challenge")
    rate = app.scryfall_refusal_copy("rate_limit")
    timeout = app.scryfall_refusal_copy("timeout")
    maintenance = app.scryfall_refusal_copy("maintenance")
    for label, payload in (("challenge", challenge), ("rate_limit", rate),
                           ("timeout", timeout), ("maintenance", maintenance)):
        (ok if payload.get("what") and payload.get("how") else bad)(
            f"{label} has what+how", str(payload))
    (ok if challenge["what"] != rate["what"] else bad)(
        "challenge what ≠ rate_limit what")
    (ok if challenge["what"] != timeout["what"] else bad)(
        "challenge what ≠ timeout what")
    (ok if rate["what"] != timeout["what"] else bad)(
        "rate_limit what ≠ timeout what")
    (ok if maintenance["what"] != app.scryfall_refusal_copy("unreachable")["what"]
     else bad)("maintenance what ≠ unreachable what")
    (ok if "robot" in challenge["what"].lower()
          or "verify" in challenge["what"].lower() else bad)(
        "challenge names the robot check", challenge["what"])
    (ok if "rate" in rate["what"].lower() else bad)(
        "rate_limit names the rate limit", rate["what"])
    (ok if "time" in timeout["what"].lower() else bad)(
        "timeout names the timeout", timeout["what"])
    (ok if "maintenance" in maintenance["what"].lower() else bad)(
        "maintenance names the maintenance window", maintenance["what"])
    (ok if "didn't answer" not in maintenance["what"].lower()
          and "did not answer" not in maintenance["what"].lower() else bad)(
        "maintenance does not say didn't answer", maintenance["what"])


async def test_refusal_payload_shape():
    """API payload carries error + kind + what + how for the UI."""
    print("\nscryfall_refusal_payload()  — structured for the page")
    err = app.ScryfallError(403, "cloudflare challenge", kind="challenge")
    payload = app.scryfall_refusal_payload(err)
    eq("error key", payload.get("error"), "scryfall_refused")
    eq("kind", payload.get("kind"), "challenge")
    eq("status", payload.get("status"), 403)
    (ok if payload.get("what") and payload.get("how") else bad)(
        "payload has what+how", str(payload))
    eq("ok false", payload.get("ok"), False)


async def test_exception_handler_returns_named_state():
    """FastAPI turns ScryfallError into the named JSON body, not a generic 500."""
    print("\nexception handler  — ScryfallError → named JSON")
    import json
    err = app.ScryfallError(403, "challenge", kind="challenge")
    resp = await app._scryfall_error_handler(None, err)
    eq("handler status code is 503 for challenge", resp.status_code, 503)
    body = json.loads(resp.body)
    eq("handler error", body.get("error"), "scryfall_refused")
    eq("handler kind", body.get("kind"), "challenge")
    (ok if body.get("what") else bad)("handler includes what")

    maint = app.ScryfallError(503, "offline for maintenance", kind="maintenance")
    resp_m = await app._scryfall_error_handler(None, maint)
    eq("handler status code is 503 for maintenance", resp_m.status_code, 503)
    body_m = json.loads(resp_m.body)
    eq("handler maintenance kind", body_m.get("kind"), "maintenance")
    (ok if "maintenance" in (body_m.get("what") or "").lower() else bad)(
        "handler maintenance what names the window", body_m.get("what"))
    eq("http map for maintenance is 503",
       app._http_status_for_refusal("maintenance"), 503)


async def test_ambient_names_refusal_kind():
    """/api/ambient keeps card:null honesty and names the refusal kind."""
    print("\n/api/ambient  — refusal kind on card:null")
    real = app.scry

    async def boom(*a, **k):
        raise app.ScryfallError(403, "challenge", kind="challenge")

    app.scry = boom
    try:
        body = await app.ambient_card()
        eq("ambient card null", body.get("card"), None)
        eq("ambient error key", body.get("error"), "scryfall_refused")
        eq("ambient kind", body.get("kind"), "challenge")
        (ok if body.get("what") else bad)("ambient includes what")
    finally:
        app.scry = real


async def test_search_names_refusal_kind():
    """/api/search includes kind so the editor search is not a generic failure."""
    print("\n/api/search  — refusal kind")
    real = app.scry

    async def boom(*a, **k):
        raise app.ScryfallError(429, "rate limited", kind="rate_limit")

    app.scry = boom
    try:
        resp = await app.api_search(q="sol ring", limit=5)
        # JSONResponse
        eq("search status", resp.status_code, 429)
        body = resp.body
        import json
        data = json.loads(body)
        eq("search error", data.get("error"), "scryfall_refused")
        eq("search kind", data.get("kind"), "rate_limit")
    finally:
        app.scry = real


async def test_boot_reports_card_source():
    """/api/boot surfaces a challenged card source without blocking local pages."""
    print("\n/api/boot  — card_source preflight")
    boot_mod.reset_for_tests()
    import os
    old = os.environ.get("DECKWRIGHT_SKIP_BOOT_REFRESH")
    os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = "1"

    async def _warm():
        return {}

    async def challenged_probe():
        raise app.ScryfallError(403, "challenge", kind="challenge")

    try:
        # Inject probe via the same hook run_boot uses.
        st = await boot_mod.run_boot(
            warm=_warm, card_source_probe=challenged_probe)
        (ok if st.get("ready") else bad)(
            "boot stays ready so saved decks still open", str(st))
        cs = st.get("card_source") or {}
        eq("card_source ok", cs.get("ok"), False)
        eq("card_source kind", cs.get("kind"), "challenge")
        (ok if cs.get("what") else bad)("card_source has what", str(cs))

        body = await app.api_boot()
        (ok if (body.get("card_source") or {}).get("kind") == "challenge"
         else bad)("api_boot exposes card_source", str(body.get("card_source")))
    finally:
        if old is None:
            os.environ.pop("DECKWRIGHT_SKIP_BOOT_REFRESH", None)
        else:
            os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = old
        boot_mod.reset_for_tests()


def test_ui_surfaces_kinds_on_ladder():
    """index.html distinguishes kinds, freezes the ladder, keeps her work."""
    print("\nindex.html  — refusal sentences + ladder + keep work")
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    (ok if "scryfallRefusal" in html or "kind === 'challenge'" in html
          or 'kind==="challenge"' in html or "kind === \"challenge\"" in html
          or "e.kind" in html else bad)(
        "UI reads structured refusal kind")
    (ok if "robot" in html.lower() else bad)(
        "UI has challenge copy naming the robot check")
    (ok if re.search(r"rate.?limit", html, re.I) else bad)(
        "UI has rate-limit copy")
    (ok if re.search(r"timed out|timeout", html, re.I) else bad)(
        "UI has timeout copy")
    (ok if re.search(r"kind === ['\"]maintenance['\"]", html) else bad)(
        "UI scryfallRefusal has a maintenance branch")
    (ok if "offline for maintenance" in html.lower() else bad)(
        "UI maintenance copy names the window")
    (ok if re.search(
        r"challenge\|maintenance\|rate_limit\|timeout\|unreachable\|transport",
        html) else bad)(
        "fail() kind regex includes maintenance")

    # Build failure must clear progress (no completion beat) and keep a back path.
    start = html.find("async function buildIt")
    build_fn = html[start:start + 3500]
    catch_start = build_fn.find("}catch(e){")
    # Slice only the catch body — the success path after it calls finishBuildProgress.
    catch_end = build_fn.find("\n  await finishBuildProgress", catch_start)
    if catch_end < 0:
        catch_end = catch_start + 700
    catch_block = build_fn[catch_start:catch_end] if catch_start >= 0 else ""
    (ok if "clearBuildProgress()" in catch_block else bad)(
        "build failure clears progress timer")
    (ok if "finishBuildProgress(" not in catch_block else bad)(
        "build failure path does not play completion beat")
    (ok if "steps(stages" in catch_block else bad)(
        "build failure keeps the progress ladder on screen")
    (ok if "backToCommanders" in catch_block or "Back to commanders" in catch_block
          else bad)("build failure keeps a way back to commanders")
    (ok if "Try again" in catch_block else bad)(
        "build failure offers retry without restarting the brief")

    # build stop must say building needs live Scryfall, and that
    # saved decks still open — not a vague "refused this look".
    (ok if re.search(
        r"building needs live Scryfall|needs the live card source",
        catch_block, re.I) else bad)(
        "build failure says building needs live Scryfall")
    (ok if re.search(r"saved decks still open", catch_block, re.I) else bad)(
        "build failure says saved decks still open")
    (ok if "refused this look" not in catch_block else bad)(
        "build failure drops the vague 'refused this look' note")

    # post() must preserve kind from JSON error bodies.
    (ok if "scryfall_refused" in html and "err.kind" in html else bad)(
        "post/fail path handles scryfall_refused kind")

    # Boot / landing surfaces card_source.
    (ok if "card_source" in html and "showCardSourceNote" in html else bad)(
        "landing/boot reads card_source")


def test_docs_say_build_needs_live():
    """corpus resilience claims must not bury the build-path gap."""
    print("\ndocs  — build needs live Scryfall (otag:)")
    om = (ROOT / "docs" / "OPERATING-MODEL.md").read_text(encoding="utf-8")
    # Survival table: building is the product path and it does not survive.
    (ok if re.search(
        r"\|\s*\*\*Building a deck\*\*\s*\|\s*\*\*No\*\*", om) else bad)(
        "OPERATING-MODEL states building does not survive Scryfall down")
    (ok if "live Scryfall" in om and "otag:" in om else bad)(
        "OPERATING-MODEL names otag: and the live-Scryfall build gap")
    # Corpus-first must not read as broad protection with otag as an aside.
    corp_idx = om.find("Corpus-first")
    build_idx = om.find("**Building a deck**")
    (ok if corp_idx >= 0 and build_idx > corp_idx else bad)(
        "OPERATING-MODEL states the build gap inside the corpus-first section")

    ops = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    (ok if re.search(
        r"Building a deck still needs the live API", ops) else bad)(
        "OPERATIONS says building needs live Scryfall")


def test_retry_budget_unchanged():
    """must not raise the retry count — more requests extend a challenge."""
    print("\nretry budget  — unchanged")
    eq("_SCRYFALL_RETRIES stays 5", app._SCRYFALL_RETRIES, 5)
    eq("_SCRYFALL_MIN_GAP stays 0.2", app._SCRYFALL_MIN_GAP, 0.2)


async def main() -> int:
    import os
    # a loaded local corpus answers ordinary searches without httpx.
    # The live-client refusal cases below must force the network path.
    old_corpus = os.environ.get("DECKWRIGHT_CORPUS")
    os.environ["DECKWRIGHT_CORPUS"] = "off"
    try:
        await test_challenge_header_raises_named_kind()
        await test_challenge_body_just_a_moment()
        await test_plain_403_is_not_challenge()
        await test_rate_limit_kind()
        await test_timeout_kind()
        await test_maintenance_kind_no_retry()
        await test_incidental_maintenance_word_is_retried()
        await test_plain_503_is_retried_then_unreachable()
    finally:
        if old_corpus is None:
            os.environ.pop("DECKWRIGHT_CORPUS", None)
        else:
            os.environ["DECKWRIGHT_CORPUS"] = old_corpus

    await test_refusal_copy_is_distinct()
    await test_refusal_payload_shape()
    await test_exception_handler_returns_named_state()
    await test_ambient_names_refusal_kind()
    await test_search_names_refusal_kind()
    await test_boot_reports_card_source()
    test_ui_surfaces_kinds_on_ladder()
    test_docs_say_build_needs_live()
    test_retry_budget_unchanged()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
