"""
test_thesis_glued.py — glued payoff allowlist + key discipline.

Offline. No model. No Scryfall.

    python test_thesis_glued.py
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

from deckwright import app  # noqa: E402
from deckwright import sim  # noqa: E402
from deckwright import synergy  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0
FIXTURES = ROOT / "data" / "synergy_fixtures.json"


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


def _card(name: str) -> dict:
    c = json.loads(FIXTURES.read_text())["cards"][name]
    return {**c, "name": name, "role": c.get("role") or "theme"}


def test_glued_candidates_hei_bai():
    print("\nglued_payoff_candidates() — Hei Bai fixture")
    hei = _card("Hei Bai, Spirit of Balance")
    deck = [
        _card("Pitiless Plunderer"),
        _card("Angel of Indemnity"),
        _card("Cloudshift"),
        _card("Ephemerate"),
        _card("Deadly Dispute"),
        _card("Village Rites"),
        _card("Sol Ring"),
        _card("Shock"),
        {**_card("Storm Crow"), "role": "theme"},
        {"name": "Island", "type": "Basic Land — Island",
         "type_line": "Basic Land — Island", "oracle_text": "",
         "role": "basic"},
    ]
    out = synergy.glued_payoff_candidates(deck, hei, cap=30)
    eq("status ok", out["status"], "ok")
    names = out["names"]
    true("Angel of Indemnity glued", "Angel of Indemnity" in names, str(names))
    true("Pitiless Plunderer glued", "Pitiless Plunderer" in names, str(names))
    true("basic lands excluded", "Island" not in names, str(names))
    # Sol Ring / Shock may or may not glue — never invent names outside deck.
    for n in names:
        true(f"{n} is in deck", n in {c["name"] for c in deck})

    # Coverage when payoffs are a subset of the allowlist.
    payoffs = names[: max(1, len(names) * 3 // 4)]
    cov = synergy.payoff_edge_coverage(payoffs, deck, hei)
    true("allowlist subset ≥70% edged", cov >= 0.70, f"cov={cov}")


def test_unknown_commander():
    print("\nglued_payoff_candidates() — unknown commander")
    blank = {"name": "Mystery", "oracle_text": None}
    deck = [_card("Sol Ring")]
    out = synergy.glued_payoff_candidates(deck, blank)
    eq("unknown status", out["status"], "unknown")
    eq("no invented candidates", out["names"], [])


def test_verifier_unchanged():
    print("\nthesis verifier — still drops invented names / illegal keys")
    deck = [{"name": "Sol Ring"}, {"name": "Angel of Indemnity"}]
    spec = {
        "payoff_cards": ["Sol Ring", "Fake Card", "Angel of Indemnity"],
        "conditions": [
            {"label": "ok", "check": {"life_gained": 5}},
            {"label": "bad", "check": {"energy_available": 1}},
        ],
    }
    names = {c["name"] for c in deck}
    payoffs = [n for n in spec["payoff_cards"] if n in names]
    conds = [c for c in spec["conditions"]
             if c.get("check") and all(k in sim.THESIS_KEYS for k in c["check"])]
    eq("fake card dropped", payoffs, ["Sol Ring", "Angel of Indemnity"])
    eq("illegal key dropped", [c["label"] for c in conds], ["ok"])
    true("energy not in THESIS_KEYS", "energy_available" not in sim.THESIS_KEYS)


def test_key_discipline_golden():
    print("\nthesis_key_fit() — Gate 1 key discipline")
    lifegain_ok = {
        "conditions": [
            {"check": {"commander_in_play": True}},
            {"check": {"life_gained": 10}},
            {"check": {"payoffs_in_play": 2}},
        ],
    }
    lifegain_bad = {
        "conditions": [
            {"check": {"payoffs_in_play": 3}},
            {"check": {"mana_available": 5}},
        ],
    }
    true("angels/lifegain wants life_gained",
         app.thesis_key_fit("angels that gain life", lifegain_ok))
    true("lifegain-only-payoffs fails",
         not app.thesis_key_fit("gain life and stay alive", lifegain_bad))

    tokens_ok = {
        "conditions": [
            {"check": {"creatures_in_play": 6}},
            {"check": {"board_power": 10}},
        ],
    }
    tokens_bad = {
        "conditions": [
            {"check": {"payoffs_in_play": 4}},
        ],
    }
    true("token swarm wants board keys",
         app.thesis_key_fit("go wide with tokens", tokens_ok))
    true("tokens-only-payoffs fails",
         not app.thesis_key_fit("token swarm go wide", tokens_bad))


def test_thesis_prompt_mentions_allowlist():
    print("\nTHESIS prompt — glued allowlist + anti-pattern")
    true("mentions glued / allowlist",
         "glued" in app.THESIS.lower() or "allowlist" in app.THESIS.lower()
         or "prefer these" in app.THESIS.lower(),
         app.THESIS[:200])
    true("anti-pattern creatures_in_play",
         "creatures_in_play" in app.THESIS)
    true("anti-pattern life_gained", "life_gained" in app.THESIS)
    true("anti-pattern not only payoffs_in_play",
         "payoffs_in_play alone" in app.THESIS
         or "not force everything through payoffs_in_play" in app.THESIS.lower())


def test_build_thesis_user_message_includes_candidates():
    print("\nbuild_thesis_user_message() — allowlist in prompt")
    hei = _card("Hei Bai, Spirit of Balance")
    deck = [
        {**_card("Angel of Indemnity"), "role": "theme"},
        {**_card("Pitiless Plunderer"), "role": "plan"},
        {**_card("Sol Ring"), "role": "ramp"},
    ]
    brief = {"angle": "sacrifice for value", "strategy": "aristocrats"}
    msg = app.build_thesis_user_message(deck, brief, "Hei Bai, Spirit of Balance",
                                        commander_card=hei)
    true("Angel in allowlist block", "Angel of Indemnity" in msg, msg[:400])
    true("mentions prefer", "prefer" in msg.lower(), msg[:400])


def main():
    test_glued_candidates_hei_bai()
    test_unknown_commander()
    test_verifier_unchanged()
    test_key_discipline_golden()
    test_thesis_prompt_mentions_allowlist()
    test_build_thesis_user_message_includes_candidates()
    print()
    if failed:
        print(f"{RED}FAILED{OFF} {failed}  {GREEN}passed{OFF} {passed}")
        return 1
    print(f"{GREEN}ALL OK{OFF}  {passed} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
