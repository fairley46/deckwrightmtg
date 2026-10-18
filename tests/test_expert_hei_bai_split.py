"""
Offline tests for chosen vs mentioned split, Close/Better scores.

The 50-name fixture is "cards named in the video", not "cards in his deck".
Classification must come from transcript position + card type, with the rule
recorded per card — never a hand-curated list from memory.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckwright import synergy  # noqa: E402

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


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


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_fixture_is_split():
    print("\nexpert fixture — chosen / mentioned split")
    payload = json.loads(
        (ROOT / "testdata/review/expert-hei-bai-cards.json").read_text())
    eq("has chosen list", isinstance(payload.get("chosen"), list), True)
    eq("has mentioned list", isinstance(payload.get("mentioned"), list), True)
    chosen = payload["chosen"]
    mentioned = payload["mentioned"]
    eq("chosen non-empty", len(chosen) > 0, True)
    eq("mentioned non-empty", len(mentioned) > 0, True)

    for side, rows in ("chosen", chosen), ("mentioned", mentioned):
        for row in rows:
            eq(f"{side} row has name", bool(row.get("name")), True)
            eq(f"{side} row has rule", bool(row.get("rule")), True)

    chosen_names = {r["name"] for r in chosen}
    mentioned_names = {r["name"] for r in mentioned}
    eq("chosen and mentioned are disjoint",
       chosen_names.isdisjoint(mentioned_names), True)
    multi = set(payload.get("verified_multi_word") or [])
    eq("every multi-word name is classified",
       chosen_names | mentioned_names, multi)


def test_rejected_commanders_are_mentioned():
    print("\nexpert fixture — rejected legends land in mentioned")
    payload = json.loads(
        (ROOT / "testdata/review/expert-hei-bai-cards.json").read_text())
    by_name = {r["name"]: r for r in payload["chosen"] + payload["mentioned"]}
    for name in ("Aang, A Lot to Learn", "Jet, Freedom Fighter",
                 "Smellerbee, Rebel Fighter", "Suki, Courageous Rescuer"):
        row = by_name[name]
        eq(f"{name} is mentioned", name in {r["name"] for r in payload["mentioned"]},
           True)
        eq(f"{name} rule is rejected_commander",
           row["rule"], "rejected_commander")


def test_faithless_looting_is_counter_example():
    print("\nexpert fixture — Faithless Looting is a counter-example")
    payload = json.loads(
        (ROOT / "testdata/review/expert-hei-bai-cards.json").read_text())
    mentioned = {r["name"]: r for r in payload["mentioned"]}
    eq("Faithless Looting mentioned", "Faithless Looting" in mentioned, True)
    eq("rule is counter_example",
       mentioned["Faithless Looting"]["rule"], "counter_example")
    eq("Faithless Looting not chosen",
       "Faithless Looting" not in {r["name"] for r in payload["chosen"]}, True)


def test_headline_synergy_card_is_chosen():
    print("\nexpert fixture — Airbender Ascension is chosen")
    payload = json.loads(
        (ROOT / "testdata/review/expert-hei-bai-cards.json").read_text())
    chosen = {r["name"]: r for r in payload["chosen"]}
    eq("Airbender Ascension chosen", "Airbender Ascension" in chosen, True)
    eq("rule is building_section",
       chosen["Airbender Ascension"]["rule"], "building_section")


def test_classify_is_derived_not_memorized():
    print("\nclassify — rules fire from position + type, not a name list")
    extract = _load("extract_expert_deck.py")
    # A legendary creature first named inside the commander-selection window
    # is mentioned, even if we have never heard of it.
    transcript = (
        "Let's hop right into step one, picking your Commander. "
        "First up, Fake Legend, Totally Real. Cool art. "
        "Now that we have our commander, we have to move on to step two. "
        "I'm adding Airbender Ascension because it blinks him."
    )
    oracle = {
        "Fake Legend, Totally Real": {
            "name": "Fake Legend, Totally Real",
            "type_line": "Legendary Creature — Human",
            "color_identity": ["W"],
            "text": "Whenever Fake Legend enters, draw a card.",
        },
        "Airbender Ascension": {
            "name": "Airbender Ascension",
            "type_line": "Enchantment",
            "color_identity": ["W"],
            "text": ("At the beginning of your end step, exile up to one "
                     "target creature you control, then return it to the "
                     "battlefield under its owner's control."),
        },
    }
    rows = extract.classify(
        ["Fake Legend, Totally Real", "Airbender Ascension"],
        transcript,
        oracle,
        commander="Hei Bai, Spirit of Balance",
        commander_colors=["W", "B"],
    )
    by = {r["name"]: r for r in rows}
    eq("unknown legend in selection -> rejected_commander",
       by["Fake Legend, Totally Real"]["rule"], "rejected_commander")
    eq("unknown legend bucket",
       by["Fake Legend, Totally Real"]["bucket"], "mentioned")
    eq("building card -> chosen",
       by["Airbender Ascension"]["bucket"], "chosen")


def test_close_and_better_scores():
    print("\nagreement — Close and Better scores")
    agree = _load("hei_bai_agreement.py")
    # Real Hei Bai oracle. A fake sacrifice-outlet text made Know Magic's
    # richer edge counts inflate his-chosen median above Angel of Indemnity;
    # the expert ground truth is the printed card, and Angel clears that bar.
    hei = {
        "name": "Hei Bai, Spirit of Balance",
        "type_line": "Legendary Creature — Spirit Bear",
        "oracle_text": (
            "Whenever one or more cards leave your graveyard, put two +1/+1 "
            "counters on Hei Bai. This ability triggers only once each turn.\n"
            "Whenever Hei Bai attacks, you may return target permanent card "
            "with mana value less than or equal to Hei Bai's power from your "
            "graveyard to the battlefield."),
    }
    # His chosen: three cards. We pick two of them + one better-connected
    # spare + one weak spare.
    his_chosen = [
        {"name": "Deadly Dispute",
         "type_line": "Instant",
         "oracle_text": ("As an additional cost to cast this spell, sacrifice "
                         "an artifact or creature. Draw two cards and create "
                         "a Treasure token.")},
        {"name": "Cloudshift",
         "type_line": "Instant",
         "oracle_text": ("Exile target creature you control, then return that "
                         "card to the battlefield under your control.")},
        {"name": "Arcane Signet",
         "type_line": "Artifact",
         "oracle_text": "{T}: Add one mana of any color in your commander's "
                        "color identity."},
    ]
    our_deck = [
        his_chosen[0],  # agreement
        his_chosen[1],  # agreement
        {"name": "Angel of Indemnity",
         "type_line": "Creature — Angel Warrior",
         "oracle_text": (
             "Flying, lifelink\nWhen this creature enters, return target "
             "permanent card with mana value 4 or less from your graveyard "
             "to the battlefield.")},
        {"name": "Shock",
         "type_line": "Instant",
         "oracle_text": "Shock deals 2 damage to any target."},
    ]
    report = agree.score(his_chosen, our_deck, hei)
    eq("agreement count", report["agreement_count"], 2)
    eq("chosen denominator", report["chosen_n"], 3)
    # Arcane Signet is missing from ours; edge_count against Hei Bai is the
    # connected-but-not-picked signal.
    eq("missing includes Arcane Signet",
       "Arcane Signet" in report["missing"], True)
    eq("connected_not_picked is reported",
       "connected_not_picked" in report, True)
    # Better: Angel of Indemnity should clear the median of his chosen edges;
    # Shock should not.
    better_names = {c["name"] for c in report["better"]}
    eq("Angel of Indemnity counts as better",
       "Angel of Indemnity" in better_names, True)
    eq("Shock does not count as better",
       "Shock" not in better_names, True)


def test_reproducibility_band():
    print("\nagreement — three consecutive runs must sit inside a band")
    agree = _load("hei_bai_agreement.py")
    # Three identical percents pass; a 20-point swing fails.
    eq("stable band passes",
       agree.within_band([40.0, 40.0, 41.0], band=5.0), True)
    eq("wide swing fails",
       agree.within_band([40.0, 40.0, 60.0], band=5.0), False)
    eq("needs three runs",
       agree.within_band([40.0, 40.0], band=5.0), False)


def test_blink_floor_shape():
    print("\nleave-enabler floor — blink reserved like the archetype payoff floors payoffs")
    # The build path must expose blink names as a payoff floor so a card that
    # is only in the pool can still be forced into theme. Pure unit check on
    # the helper that builds that floor — no live Scryfall.
    from deckwright import app
    got = [
        {"name": "Ephemerate", "type": "Instant"},
        {"name": "Airbender Ascension", "type": "Enchantment"},
        {"name": "Cloudshift", "type": "Instant"},
    ]
    floor = app.leave_enabler_floor(got)
    eq("floor id", floor["id"], "leave_enabler_blink")
    eq("floor min at least 1", floor["min"] >= 1, True)
    eq("Airbender Ascension in floor names",
       "Airbender Ascension" in floor["names"], True)
    # enforce_payoff_floors must be able to pull a blink card in.
    chosen = [{"name": f"Generic{i}", "type": "Creature"} for i in range(10)]
    pool = chosen + got
    fixed = app.enforce_payoff_floors(chosen, pool, [floor], 10)
    eq("a blink card lands in the 10",
       any(c["name"] in floor["names"] for c in fixed), True)


def test_synergy_enabler_floor_shape():
    print("\nsynergy enabler floor — tokens reserved like blink")
    from deckwright import app
    got = [
        {"name": "Raise the Alarm", "type": "Instant"},
        {"name": "Lingering Souls", "type": "Sorcery"},
    ]
    floor = app.synergy_enabler_floor("tokens", got)
    eq("floor id", floor["id"], "synergy_enabler_tokens")
    eq("floor min at least 1", floor["min"] >= 1, True)
    eq("Raise the Alarm in floor names",
       "Raise the Alarm" in floor["names"], True)
    chosen = [{"name": f"Generic{i}", "type": "Creature"} for i in range(10)]
    pool = chosen + got
    fixed = app.enforce_payoff_floors(chosen, pool, [floor], 10)
    eq("a token maker lands in the 10",
       any(c["name"] in floor["names"] for c in fixed), True)


def main() -> int:
    test_fixture_is_split()
    test_rejected_commanders_are_mentioned()
    test_faithless_looting_is_counter_example()
    test_headline_synergy_card_is_chosen()
    test_classify_is_derived_not_memorized()
    test_close_and_better_scores()
    test_reproducibility_band()
    test_blink_floor_shape()
    test_synergy_enabler_floor_shape()
    print(f"\n{passed} passed" + (f", {RED}{failed} failed{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
