#!/usr/bin/env python3
"""bug reports land on disk first; Linear is enrichment that may fail.

Offline. No network. The Linear client is injected so a missing Keychain key,
a refused create, and a successful stamp are all graded without calling out.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from deckwright import bug_report  # noqa: E402

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def fail(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        fail(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    if cond:
        ok(label, extra)
    else:
        fail(label, extra or "expected truthy")


def sample_record(**overrides):
    rec = {
        "id": "20260809T120000000000",
        "reported_at": "2026-08-09T12:00:00Z",
        "note": "The swap button did nothing\nI clicked it twice.",
        "context": {
            "page": "deck page",
            "url": "http://127.0.0.1:8000/",
            "deck_id": "heliod-1",
            "commander": "Heliod, Sun-Crowned",
            "deck_name": "Sun Choir",
            "viewport": "375x812",
            "user_agent": "TestAgent/1.0",
            "errors": ["TypeError: x is null @ app.js:12"],
        },
        "app_version": "abc1234",
        "status": "new",
    }
    rec.update(overrides)
    return rec


def test_title_from_first_line():
    print("\ntitle — her first line, Bug: prefix")
    eq("first line",
       bug_report.issue_title(sample_record()),
       "Bug: The swap button did nothing")
    eq("single line",
       bug_report.issue_title({"note": "  broke on save  "}),
       "Bug: broke on save")
    eq("empty note falls back",
       bug_report.issue_title({"note": "   \n  "}),
       "Bug: (no description)")
    long = "x" * 300
    title = bug_report.issue_title({"note": long})
    true("long title capped", len(title) <= 200)
    true("still Bug: prefix", title.startswith("Bug: "))


def test_description_carries_context():
    print("\ndescription — her words + page/deck/errors/version")
    body = bug_report.issue_description(sample_record())
    true("note verbatim", "The swap button did nothing\nI clicked it twice." in body)
    true("page", "**Page:** deck page" in body)
    true("deck id", "**Deck id:** heliod-1" in body)
    true("commander", "**Commander:** Heliod, Sun-Crowned" in body)
    true("viewport", "**Viewport:** 375x812" in body)
    true("version", "**App version:** abc1234" in body)
    true("errors in code block", "```\nTypeError: x is null @ app.js:12\n```" in body)
    true("note before context",
         body.index("The swap button") < body.index("**Page:**"))


def test_keychain_absent_is_error_not_success():
    print("\nkey — absent Keychain skips Linear honestly")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        rec = sample_record()
        path.write_text(json.dumps(rec))
        out = bug_report.file_report_to_linear(
            rec, path, key_fn=lambda: None, create_fn=MagicMock())
        true("returns not ok", out.get("ok") is False)
        stamped = json.loads(path.read_text())
        true("no linear stamp", "linear" not in stamped)
        true("linear_error recorded", "linear_error" in stamped)
        true("error names key", "key" in stamped["linear_error"].lower()
             or "keychain" in stamped["linear_error"].lower())


def test_linear_success_stamps_file():
    print("\nLinear success — stamps id + url on the report file")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        rec = sample_record()
        path.write_text(json.dumps(rec))

        def fake_create(title, description, *, api_key):
            eq("title passed through", title, "Bug: The swap button did nothing")
            true("description has errors", "TypeError" in description)
            true("key handed in", api_key == "lin_test_key")
            return {"id": "issue-uuid", "identifier": "BUG-999",
                    "url": "https://example.test/issue/BUG-999"}

        out = bug_report.file_report_to_linear(
            rec, path, key_fn=lambda: "lin_test_key", create_fn=fake_create)
        true("ok", out.get("ok") is True)
        stamped = json.loads(path.read_text())
        eq("linear id", stamped["linear"]["id"], "BUG-999")
        eq("linear url", stamped["linear"]["url"],
           "https://example.test/issue/BUG-999")
        true("no linear_error", "linear_error" not in stamped)


def test_linear_failure_leaves_error_never_silent_ok():
    print("\nLinear failure — linear_error, never a silent success")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        rec = sample_record()
        path.write_text(json.dumps(rec))

        def boom(title, description, *, api_key):
            raise bug_report.LinearError("plan limit reached")

        out = bug_report.file_report_to_linear(
            rec, path, key_fn=lambda: "lin_test_key", create_fn=boom)
        true("not ok", out.get("ok") is False)
        stamped = json.loads(path.read_text())
        true("no linear stamp", "linear" not in stamped)
        eq("error reason", stamped.get("linear_error"), "plan limit reached")


def test_disk_first_endpoint_survives_linear_miss():
    """/api/bug writes the file even when Linear cannot run."""
    print("\n/api/bug — disk first; Linear miss still ok:true")
    from deckwright import app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        save = Path(tmp)
        old_save, old_bug = app.SAVE_DIR, app.BUG_DIR
        app.SAVE_DIR = save
        app.BUG_DIR = save / "bugs"
        try:
            with patch("deckwright.bug_report.read_linear_api_key", return_value=None), \
                 patch("deckwright.bug_report.create_linear_issue") as create:
                client = TestClient(app.app)
                r = client.post("/api/bug", json={
                    "note": "prices missing on the deck page",
                    "context": {
                        "page": "deck page",
                        "commander": "Krenko, Mob Boss",
                        "viewport": "1440x900",
                        "errors": ["fetch failed"],
                    },
                })
            eq("HTTP 200", r.status_code, 200)
            body = r.json()
            true("ok despite no key", body.get("ok") is True)
            true("id returned", bool(body.get("id")))
            files = list((save / "bugs").glob("*.json"))
            eq("one file on disk", len(files), 1)
            rec = json.loads(files[0].read_text())
            eq("note saved", rec["note"], "prices missing on the deck page")
            true("linear_error present", "linear_error" in rec)
            true("create never called without key", not create.called)
        finally:
            app.SAVE_DIR = old_save
            app.BUG_DIR = old_bug


def test_endpoint_stamps_linear_on_success():
    print("\n/api/bug — successful Linear stamps the file")
    from deckwright import app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        save = Path(tmp)
        old_save, old_bug = app.SAVE_DIR, app.BUG_DIR
        app.SAVE_DIR = save
        app.BUG_DIR = save / "bugs"
        try:
            with patch("deckwright.bug_report.read_linear_api_key", return_value="k"), \
                 patch("deckwright.bug_report.create_linear_issue",
                       return_value={"id": "u", "identifier": "BUG-1",
                                     "url": "https://linear.app/x/BUG-1"}):
                client = TestClient(app.app)
                r = client.post("/api/bug", json={
                    "note": "footer overlap on mobile",
                    "context": {"page": "landing", "viewport": "375x667"},
                })
            eq("HTTP 200", r.status_code, 200)
            rec = json.loads(next((save / "bugs").glob("*.json")).read_text())
            eq("linear id", rec["linear"]["id"], "BUG-1")
            true("no error stamp", "linear_error" not in rec)
            true("app_version recorded", "app_version" in rec)
        finally:
            app.SAVE_DIR = old_save
            app.BUG_DIR = old_bug


def test_sync_walks_unstamped_only():
    print("\nsync — files without linear stamp, skips stamped")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        synced = root / "a.json"
        pending = root / "b.json"
        failed = root / "c.json"
        synced.write_text(json.dumps({
            **sample_record(id="a"),
            "linear": {"id": "BUG-1", "url": "https://linear.app/x/BUG-1"},
        }))
        pending.write_text(json.dumps(sample_record(id="b", note="pending one")))
        failed.write_text(json.dumps({
            **sample_record(id="c", note="retry me"),
            "linear_error": "timeout",
        }))

        created = []

        def fake_create(title, description, *, api_key):
            created.append(title)
            return {"id": "u", "identifier": f"BUG-{len(created)+10}",
                    "url": f"https://example.test/x/BUG-{len(created)+10}"}

        summary = bug_report.sync_bug_dir(
            root, key_fn=lambda: "k", create_fn=fake_create)
        eq("filed two", summary["filed"], 2)
        eq("skipped one", summary["skipped"], 1)
        true("pending filed", "Bug: pending one" in created)
        true("retry filed", "Bug: retry me" in created)
        true("synced left alone", "Bug: The swap button" not in " ".join(created))
        eq("pending now stamped",
           json.loads(pending.read_text())["linear"]["id"].startswith("BUG-"), True)
        true("error cleared on success",
             "linear_error" not in json.loads(failed.read_text()))


def test_no_key_constant_in_repo():
    print("\nno key in repo — source does not hardcode a tracker secret")
    src = (REPO / "deckwright" / "bug_report.py").read_text(encoding="utf-8")
    true("no lin_api_ style literals", "lin_api_" not in src)
    true("no Linear workspace team id", "LINEAR_TEAM_ID" not in src)
    true("no Linear project id", "LINEAR_PROJECT_ID" not in src)
    true("does not read LINEAR_API_KEY from env as the path",
         'os.environ.get("LINEAR_API_KEY")' not in src
         and "os.environ['LINEAR_API_KEY']" not in src)


if __name__ == "__main__":
    test_title_from_first_line()
    test_description_carries_context()
    test_keychain_absent_is_error_not_success()
    test_linear_success_stamps_file()
    test_linear_failure_leaves_error_never_silent_ok()
    test_disk_first_endpoint_survives_linear_miss()
    test_endpoint_stamps_linear_on_success()
    test_sync_walks_unstamped_only()
    test_no_key_constant_in_repo()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        sys.exit(1)
    print(f"{GREEN}{passed} passed{OFF}")
    sys.exit(0)
