#!/usr/bin/env python3
"""backRow labels must not collide with the built-in Start over.

Brad's playtest: import failure showed `← Start over` next to `Start over` —
same words, different handlers (showLanding vs restart). A control says what
it does; two identically-named exits where one clears the deck is the hazard.

Offline. Renders every backRow() call site from index.html and asserts no
view has two buttons with the same visible label and different onclick.

    python test_backrow_labels.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import html as html_mod
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INDEX = ROOT / "index.html"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, detail=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def bad(label, detail=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{detail}{OFF}" if detail else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r} want {want!r}")


def true(label, cond, detail=""):
    (ok if cond else bad)(label, detail)


def extract_script() -> str:
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>\s*</body>", html, re.S | re.I)
    if not m:
        raise RuntimeError("no <script> block before </body>")
    return m.group(1)


def backrow_source(script: str) -> str:
    m = re.search(r"function backRow\s*\(fn,\s*label\)\s*\{", script)
    if not m:
        raise RuntimeError("backRow not found")
    start = m.start()
    depth = 0
    i = script.index("{", m.end() - 1)
    while i < len(script):
        c = script[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return script[start : i + 1]
        i += 1
    raise RuntimeError("unbalanced backRow")


_CALL_RE = re.compile(
    r"""backRow\s*\(\s*(['"])(.*?)\1\s*,\s*(['"])(.*?)\3\s*\)""",
    re.S,
)


def call_sites(script: str) -> list[tuple[str, str]]:
    """Simple backRow('fn', 'label') sites. Multi-line ternary sites are
    checked via the honest-label sweep on the source string itself."""
    return [(m.group(2), m.group(4)) for m in _CALL_RE.finditer(script)]


def esc(s: str) -> str:
    """Mirror index.html esc() — the five XML entities."""
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def back_row(fn: str, label: str) -> str:
    """Python mirror of index.html backRow(). Kept in lockstep by
    test_backrow_source_contract — if the JS omits the Start-over guard or
    the built-in restart button, that test fails before this helper drifts."""
    left = f'<button class="link" onclick="{fn}">← {esc(label)}</button>'
    if str(label or "").strip().lower() == "start over":
        return f'<div class="row" style="margin-top:var(--s4)">{left}</div>'
    return (
        f'<div class="row" style="margin-top:var(--s4)">\n'
        f"    {left}\n"
        f'    <button class="link" onclick="restart()" '
        f'style="margin-left:var(--s3)">Start over</button></div>'
    )


class _ButtonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons: list[tuple[str, str]] = []
        self._in_btn = False
        self._onclick = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "button":
            return
        self._in_btn = True
        ad = dict(attrs)
        self._onclick = ad.get("onclick", "")
        self._text = []

    def handle_endtag(self, tag):
        if tag.lower() == "button" and self._in_btn:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            # Unescape entities so &quot; etc. don't invent fake differences.
            text = html_mod.unescape(text)
            self.buttons.append((text, self._onclick))
            self._in_btn = False

    def handle_data(self, data):
        if self._in_btn:
            self._text.append(data)


def buttons_in(html: str) -> list[tuple[str, str]]:
    p = _ButtonParser()
    p.feed(html)
    return p.buttons


def norm_label(text: str) -> str:
    """Strip the decorative back-arrow so `← Start over` and `Start over`
    compare as the same visible action name — the bug Brad screenshot."""
    return re.sub(r"^←\s*", "", text).strip().lower()


def collisions(html: str) -> list[str]:
    """Labels that appear on ≥2 buttons with differing onclick handlers."""
    by_label: dict[str, set[str]] = {}
    for text, onclick in buttons_in(html):
        if not text:
            continue
        by_label.setdefault(norm_label(text), set()).add(onclick.strip())
    out = []
    for label, handlers in sorted(by_label.items()):
        if len(handlers) > 1:
            out.append(f"{label!r} → {sorted(handlers)}")
    return out


def test_backrow_source_contract():
    """The Python mirror is only honest if the JS still does these things."""
    print("\nbackRow — source contract")
    src = backrow_source(extract_script())
    true("built-in Start over calls restart()",
         'onclick="restart()"' in src and ">Start over</button>" in src)
    true(
        "omits duplicate when label is Start over",
        "=== 'start over'" in src or '=== "start over"' in src,
    )
    true("comment present", "the right slot is always restart()" in src)


def test_call_sites_honest():
    print("\nbackRow — call-site labels match handlers")
    script = extract_script()
    sites = call_sites(script)
    true("found backRow call sites", len(sites) >= 8, f"n={len(sites)}")
    for fn, label in sites:
        if fn.strip() == "showLanding()":
            eq(f"showLanding → Back to the list",
               label, "Back to the list")
        if label.strip().lower() == "start over":
            eq("Start over label only wraps restart()",
               fn.strip(), "restart()")
    # Multi-line ternary site in buildIt — labels must stay distinct.
    true("build failure keeps Back to partners",
         "'Back to partners'" in script)
    true("build failure keeps Back to commanders",
         "'Back to commanders'" in script)
    # No showLanding paired with Start over anywhere in source.
    true(
        "no showLanding() + Start over pairing in source",
        not re.search(
            r"backRow\s*\(\s*['\"]showLanding\(\)['\"]\s*,\s*['\"]Start over['\"]",
            script,
        ),
    )


def test_rendered_views_no_label_collision():
    print("\nbackRow — rendered views: no shared label, different onclick")
    script = extract_script()
    sites = list(call_sites(script)) + [
        ("backToCommanders()", "Back to commanders"),
        ("pickPartner(name)", "Back to partners"),
    ]
    views = [back_row(fn, label) for fn, label in sites]
    composites = [f'<div class="err"><b>broke</b></div>{v}' for v in views]
    # Ambiguous-commander import screen: candidate picks + back row.
    composites.append(
        '<div class="card"><div class="starts">'
        '<button class="start" onclick="pickImportedCommander(&quot;A&quot;)">A</button>'
        '<button class="start" onclick="pickImportedCommander(&quot;B&quot;)">B</button>'
        "</div></div>"
        + back_row("showLanding()", "Back to the list")
    )
    for i, html in enumerate(composites):
        hits = collisions(html)
        eq(f"view[{i}] no colliding labels", hits, [])


def test_regression_start_over_collision_is_detectable():
    """The bug shape: left label Start over + built-in Start over, different fn."""
    print("\nbackRow — collision detector catches the original bug shape")
    buggy = (
        '<div class="row">'
        '<button class="link" onclick="showLanding()">← Start over</button>'
        '<button class="link" onclick="restart()">Start over</button>'
        "</div>"
    )
    hits = collisions(buggy)
    true("buggy pair is flagged", len(hits) == 1, f"hits={hits!r}")
    true("flag names start over", bool(hits) and "start over" in hits[0],
         f"hits={hits!r}")


def test_omit_when_label_is_start_over():
    print("\nbackRow — Start over label does not duplicate the restart button")
    html = back_row("restart()", "Start over")
    btns = buttons_in(html)
    eq("single button", len(btns), 1)
    eq("onclick is restart()", btns[0][1], "restart()")
    true("label reads Start over", "start over" in norm_label(btns[0][0]))


def test_import_path_keeps_both_actions_named():
    print("\nbackRow — import failure: Back to the list + Start over")
    html = back_row("showLanding()", "Back to the list")
    btns = buttons_in(html)
    eq("two buttons", len(btns), 2)
    labels = [norm_label(t) for t, _ in btns]
    handlers = [h for _, h in btns]
    eq("left label", labels[0], "back to the list")
    eq("left handler", handlers[0], "showLanding()")
    eq("right label", labels[1], "start over")
    eq("right handler", handlers[1], "restart()")
    eq("no collision", collisions(html), [])


def main() -> int:
    test_backrow_source_contract()
    test_call_sites_honest()
    test_rendered_views_no_label_collision()
    test_regression_start_over_collision_is_detectable()
    test_omit_when_label_is_start_over()
    test_import_path_keeps_both_actions_named()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
