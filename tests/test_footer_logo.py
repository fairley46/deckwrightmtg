#!/usr/bin/env python3
"""Footer shows the DeckWright logo lockup on the SPA.

Offline. Reads index.html. Asserts the quiet footer uses the favicon D mark
and the masthead Deck/wright wordmark — not plain Deckwright text, not foil.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INDEX = ROOT / "index.html"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


def main() -> int:
    html = INDEX.read_text(encoding="utf-8")
    print("\nfooter logo lockup — index.html")

    m = re.search(
        r'<footer class="foot"[^>]*aria-label="About this app"[^>]*>(.*?)</footer>',
        html,
        re.S,
    )
    true("footer landmark exists", m is not None)
    if not m:
        print(f"\n{failed} failed, {passed} passed")
        return 1
    foot = m.group(0)

    true(
        "footer after </main>",
        "</main>" in html.split(foot)[0][-500:],
        "footer should sit outside main",
    )
    true(
        "footer inside .wrap",
        bool(re.search(r'<div class="wrap">[\s\S]*<footer class="foot"', html)),
    )

    true("foot-brand wrapper", 'class="foot-brand"' in foot)
    true(
        "favicon D path in foot-logo",
        "M9 8h7a8 8 0 0 1 0 16H9z" in foot and "foot-logo" in foot,
    )
    true(
        "Deck/wright wordmark split",
        bool(re.search(r'class="foot-mark">Deck<span class="w">wright</span>', foot)),
    )
    true("bug link kept", "Found a bug?" in foot)
    # Comment may say "No foilsweep"; assert no animation rule on footer classes.
    foot_css = re.search(
        r"/\* ── footer ──.*?/\* The report box",
        html,
        re.S,
    )
    css = foot_css.group(0) if foot_css else ""
    true(
        "no foilsweep animation on footer",
        "animation:" not in css and "@keyframes" not in css,
        "footer CSS must stay quiet",
    )

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
