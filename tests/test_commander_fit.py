"""
test_commander_fit.py — theme + strategy scores and relational copy.

Offline. No model. No Scryfall.

    python test_commander_fit.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import commanders  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0

HEI = {
    "name": "Hei Bai, Spirit of Balance",
    "type": "Legendary Creature — Bear Spirit",
    "text": (
        "Whenever Hei Bai enters or attacks, you may sacrifice another creature "
        "or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
        "When Hei Bai leaves the battlefield, put its counters on target "
        "creature you control."
    ),
    "oracle_text": (
        "Whenever Hei Bai enters or attacks, you may sacrifice another creature "
        "or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
        "When Hei Bai leaves the battlefield, put its counters on target "
        "creature you control."
    ),
    "color_identity": ["W", "B"],
}


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {DIM}{extra}{OFF}" if extra else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


TEYSA = {
    "name": "Teysa Karlov",
    "type": "Legendary Creature — Human Advisor",
    "type_line": "Legendary Creature — Human Advisor",
    "text": (
        "If a creature dying causes a triggered ability of a permanent you "
        "control to trigger, that ability triggers an additional time.\n"
        "Creature tokens you control have vigilance and lifelink."
    ),
    "oracle_text": (
        "If a creature dying causes a triggered ability of a permanent you "
        "control to trigger, that ability triggers an additional time.\n"
        "Creature tokens you control have vigilance and lifelink."
    ),
    "color_identity": ["W", "B"],
}

# Real briefs put how-she-plays in `angle`. `playstyle` is proactive/reactive/
# grindy — not an aristocrats id. Matching must work off the sentence.
SACRIFICE_BRIEF = {
    "theme": "vampires",
    "angle": "sacrifice creatures for value, grind them down",
}


def test_theme_and_strategy_are_separate():
    print("\nfit scores — theme and strategy are separate axes")
    brief = {
        "theme": "spirits",
        "angle": "sacrifice your own creatures for value",
    }
    theme = commanders.theme_fit(HEI, brief)
    strat = commanders.strategy_fit(HEI, brief)
    true("theme score > 0", (theme.get("score") or 0) > 0, str(theme))
    true("strategy score > 0", (strat.get("score") or 0) > 0, str(strat))
    eq("theme status ok", theme.get("status"), "ok")
    eq("strategy status ok", strat.get("status"), "ok")
    true("aristocrats matched for strategy",
         "aristocrats" in (strat.get("matched") or []))

    # Theme-only: pirate theme on a spirit commander
    theme_only = commanders.theme_fit(HEI, {"theme": "pirates", "strategy": ""})
    strat_empty = commanders.strategy_fit(
        HEI, {"theme": "pirates", "strategy": "", "playstyle": "", "angle": ""})
    eq("off-theme score is 0", theme_only.get("score"), 0)
    # Strategy with no ask is empty-brief, not a zero match
    eq("no strategy ask is empty, not zero",
       strat_empty.get("status"), "empty")


def test_theme_fit_tokenizes_multi_word():
    """multi-word themes must tokenize — same defect class as the strategy-fit phrase bug."""
    print("\ntheme_fit — multi-word themes tokenize to real type hits")
    ragavan = {
        "name": "Ragavan, Nimble Pilferer",
        "type": "Legendary Creature — Monkey Pirate",
        "type_line": "Legendary Creature — Monkey Pirate",
        "text": "Whenever Ragavan deals combat damage to a player, create a Treasure token.",
        "oracle_text": (
            "Whenever Ragavan deals combat damage to a player, create a "
            "Treasure token and exile the top card of that player's library."
        ),
        "color_identity": ["R"],
    }
    brief = {"theme": "pirates that steal"}
    terms = commanders._brief_theme_terms(brief)
    true("emits pirate token", "pirate" in terms or "pirates" in terms, str(terms))
    true("does not keep only the whole phrase",
         terms != ["pirates that steal", "pirates that steal"],
         str(terms))
    true("drops connector 'that'", "that" not in terms, str(terms))

    theme = commanders.theme_fit(ragavan, brief)
    true("Ragavan theme score > 0 on 'pirates that steal'",
         (theme.get("score") or 0) > 0, str(theme))
    true("matched includes pirate",
         any("pirate" in m for m in (theme.get("matched") or [])),
         str(theme))

    # Sibling tribes + plurals + slash.
    knight = {
        "name": "Syr Gwyn, Hero of Ashvale",
        "type": "Legendary Creature — Human Knight",
        "type_line": "Legendary Creature — Human Knight",
        "text": "First strike",
        "oracle_text": "First strike",
        "color_identity": ["R", "W", "B"],
    }
    for theme_s in ("horses and knights", "horse/knight", "knights"):
        t = commanders.theme_fit(knight, {"theme": theme_s})
        true(f"knight hits theme {theme_s!r}",
             (t.get("score") or 0) > 0, str(t))

    # Single-word themes keep working (bears → Hei Bai).
    bears = commanders.theme_fit(HEI, {"theme": "bears"})
    true("bears still hits Hei Bai", (bears.get("score") or 0) > 0, str(bears))
    angels = commanders.theme_fit(HEI, {"theme": "angels"})
    eq("off-theme angels on Hei Bai is 0", angels.get("score"), 0)


def test_strategy_fit_reads_angle_sentences():
    """Whole-phrase match scored 0 on every real brief. Tokenize."""
    print("\nstrategy_fit — angle sentences match known ways")
    strat = commanders.strategy_fit(TEYSA, SACRIFICE_BRIEF)
    eq("teysa status ok", strat.get("status"), "ok")
    true("teysa strategy score > 0", (strat.get("score") or 0) > 0, str(strat))
    true("teysa matches aristocrats",
         "aristocrats" in (strat.get("matched") or []), str(strat))
    copy = commanders.relational_copy(TEYSA, SACRIFICE_BRIEF)
    true("copy does not claim unknown when aristocrats matched",
         "can't tell" not in copy.lower(), copy)

    # Same brief on a lifegain commander must still score 0 — stopwords like
    # for/them/down must not invent a hit.
    aerith = {
        "name": "Aerith, Last Ancient",
        "type": "Legendary Creature — Human Cleric Avatar",
        "type_line": "Legendary Creature — Human Cleric Avatar",
        "text": "Lifelink",
        "oracle_text": "Lifelink",
        "color_identity": ["W", "G"],
    }
    off = commanders.strategy_fit(aerith, SACRIFICE_BRIEF)
    # If KB has ways that aren't aristocrats, score stays 0 for this brief.
    true("off-strategy commander does not match aristocrats",
         "aristocrats" not in (off.get("matched") or []), str(off))
    eq("off-strategy score is 0", off.get("score"), 0)

    terms = commanders._brief_strategy_terms(SACRIFICE_BRIEF)
    true("keeps the full angle phrase",
         "sacrifice creatures for value, grind them down" in terms)
    true("emits word tokens", "sacrifice" in terms and "value" in terms)
    true("drops stopwords and filler",
         "for" not in terms and "them" not in terms and "down" not in terms
         and "creatures" not in terms,
         str(terms))


def test_relational_copy_leads_with_her_words():
    """copy opens with her theme/strategy words, not machine ids."""
    print("\nrelational copy — leads with her words")
    copy = commanders.relational_copy(TEYSA, SACRIFICE_BRIEF)
    low = copy.lower()
    true("leads with sacrifice not aristocrats id",
         "fits your sacrifice" in low and "fits your aristocrats" not in low,
         copy)
    true("does not open with machine playstyle id",
         not copy.startswith("Fits your aristocrats"), copy)

    brief = {
        "theme": "spirits",
        "angle": "sacrifice for value",
        "playstyle": "aristocrats",
    }
    theme = commanders.theme_fit(HEI, brief)
    strat = commanders.strategy_fit(HEI, brief)
    copy2 = commanders.relational_copy(HEI, brief, theme, strat)
    true("theme phrase leads when both axes hit",
         copy2.lower().startswith("for your spirits"), copy2)
    true("uses playstyle she named when it matches lead",
         "fits your aristocrats" in copy2.lower(), copy2)


def test_relational_copy_restates_and_sources():
    print("\nrelational copy — restates her ask, names ways, no deck cards")
    brief = {
        "theme": "spirits",
        "strategy": "sacrifice for value",
        "playstyle": "aristocrats",
    }
    theme = commanders.theme_fit(HEI, brief)
    strat = commanders.strategy_fit(HEI, brief)
    copy = commanders.relational_copy(HEI, brief, theme, strat)
    low = copy.lower()
    true("restates sacrifice/aristocrats",
         "sacrifice" in low or "aristocrats" in low, copy)
    true("names a way to play",
         "aristocrats" in low or "blink" in low or "counters" in low, copy)
    # One rule: no other card names invented into the copy.
    for banned in ("sol ring", "anguished unmaking", "pitiless plunderer"):
        true(f"no invented card {banned!r}", banned not in low)

    # One-axis label: theme hits, strategy does not
    weak = commanders.relational_copy(
        HEI,
        {"theme": "spirits", "strategy": "spellslinger", "playstyle": "spellslinger"},
        commanders.theme_fit(HEI, {"theme": "spirits"}),
        commanders.strategy_fit(
            HEI, {"strategy": "spellslinger", "playstyle": "spellslinger"}),
    )
    true("labels theme-without-strategy",
         "can't tell" in weak.lower()
         or "not" in weak.lower()
         or "theme" in weak.lower(),
         weak)


def test_filler_each_not_strategy_label():
    """'each' must not print as her strategy."""
    print("\nrelational_copy — filler stopwords")
    terms = commanders._brief_strategy_terms({
        "angle": "grow each attacker with combat keywords",
    })
    true("'each' dropped", "each" not in terms, str(terms))
    true("'around' dropped from build around",
         "around" not in commanders._brief_strategy_terms(
             {"angle": "build around artifacts"}),
         str(commanders._brief_strategy_terms({"angle": "build around artifacts"})))


def test_artifacts_not_matched_on_cavalry_filler():
    """cavalry brief must not match artifacts via says."""
    print("\nstrategy_fit — no artifacts false positive from says prose")
    adeline = {
        "name": "Adeline, Resplendent Cathar",
        "type": "Legendary Creature — Human Knight",
        "type_line": "Legendary Creature — Human Knight",
        "text": "Vigilance\nAdeline's power is equal to the number of creatures you control.",
        "oracle_text": (
            "Vigilance\nAdeline, Resplendent Cathar's power is equal to the "
            "number of creatures you control plus 1."
        ),
        "color_identity": ["W"],
    }
    brief = {"angle": "grow each attacker with combat keywords",
             "theme": "horses and knights"}
    strat = commanders.strategy_fit(adeline, brief)
    true("artifacts not matched on cavalry filler brief",
         "artifacts" not in (strat.get("matched") or []), str(strat))
    copy = commanders.relational_copy(adeline, brief)
    true("copy does not say Fits your each",
         "Fits your each" not in copy, copy)


def test_no_weighted_sum_in_rank_key():
    print("\nrank key — gates and tiebreaks, not a coefficient")
    # Lexicographic: both-axes, then theme, then strategy. Never theme+w*strategy.
    a = commanders.fit_rank_key(
        {"score": 2}, {"status": "ok", "score": 1})
    b = commanders.fit_rank_key(
        {"score": 5}, {"status": "ok", "score": 0})
    c = commanders.fit_rank_key(
        {"score": 0}, {"status": "ok", "score": 3})
    true("both-axes outranks theme-only even if theme is higher",
         a > b, f"a={a} b={b}")
    true("theme-only outranks strategy-only when neither has both",
         b > c, f"b={b} c={c}")
    # Unknown strategy is not a zero — rank key must not pretend it scored.
    u = commanders.fit_rank_key(
        {"score": 2}, {"status": "unknown", "score": None})
    z = commanders.fit_rank_key(
        {"score": 2}, {"status": "ok", "score": 0})
    true("unknown strategy sorts apart from score 0",
         u != z and u[2] == 0, f"u={u} z={z}")


def main() -> int:
    # Prefer the generated KB when present; tests still work if absent via detect.
    if (ROOT / "data" / "commanders.json").is_file():
        commanders.clear_cache()
    test_theme_and_strategy_are_separate()
    test_theme_fit_tokenizes_multi_word()
    test_strategy_fit_reads_angle_sentences()
    test_relational_copy_leads_with_her_words()
    test_relational_copy_restates_and_sources()
    test_filler_each_not_strategy_label()
    test_artifacts_not_matched_on_cavalry_filler()
    test_no_weighted_sum_in_rank_key()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
