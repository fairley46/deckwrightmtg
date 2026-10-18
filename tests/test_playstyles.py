"""
test_playstyles.py — playstyle vocabulary and detector.

    python test_playstyles.py

Fixture oracle text is embedded; no network.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import pathlib
import re
import sys

from deckwright import playstyles
from deckwright import synergy

REPO = pathlib.Path(__file__).resolve().parents[1]
VOCAB = REPO / "data" / "playstyles.json"
FIXTURE = REPO / "testdata" / "review" / "commander-playstyles.json"
GRAMMAR = REPO / "data" / "scryfall_grammar.json"
FAMILIES = REPO / "data" / "mechanic_families.json"

failed = 0


def ok(msg: str) -> None:
    print(f"  ok  {msg}")


def eq(msg: str, got, want) -> None:
    global failed
    if got == want:
        ok(msg)
    else:
        failed += 1
        print(f"  FAIL {msg}: got {got!r}, want {want!r}")


def true(msg: str, cond: bool) -> None:
    eq(msg, bool(cond), True)


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def test_vocab_contract():
    print("\nplaystyles.json — every entry has the contract fields")
    payload = json.loads(VOCAB.read_text())
    styles = payload.get("playstyles") or {}
    true("has playstyles", len(styles) >= 13)
    signals = set((json.loads(FAMILIES.read_text()).get("oracle_signals") or {}))
    required = ("name", "says", "detect", "wants", "exemplars", "not")
    for pid, entry in styles.items():
        for k in required:
            true(f"{pid} has {k}", k in entry)
        det = entry["detect"]
        true(f"{pid} detect.has requires", "requires" in det)
        true(f"{pid} detect.has boosts", "boosts" in det)
        for sig in (list(det.get("requires") or [])
                    + list(det.get("requires_any") or [])
                    + list(det.get("boosts") or [])):
            true(f"{pid} signal {sig} is in oracle_signals", sig in signals)
        true(f"{pid} not is a non-empty object", isinstance(entry["not"], dict) and entry["not"])
        true(f"{pid} says is one sentence-ish", 10 <= len(entry["says"]) <= 160)
        true(f"{pid} has sources", bool(entry.get("sources")))
        # A detector that can never fire must say why. The invariant is
        # unchanged; what counts as "can fire" widened.
        #
        # added requires_any for mechanics with more than one shape
        # (Atraxa only proliferates, so counters could not require
        # counter_placement). A playstyle detected solely through requires_any
        # has an empty `requires` and is fully detectable, so testing
        # `requires` alone failed ten working playstyles. Same shape as the
        # test correction: the assertion encoded the older semantics.
        if not (det.get("requires") or det.get("requires_any")):
            true(f"{pid} empty requires explains missing_signals",
                 bool(entry.get("missing_signals")))


def test_exemplars_are_scryfall_names():
    print("\nplaystyles.json — exemplars verified against Scryfall catalog")
    catalog = {}
    names = (json.loads(GRAMMAR.read_text()).get("catalogs") or {}).get("card-names") or []
    for n in names:
        catalog.setdefault(squash(str(n)), str(n))
    styles = json.loads(VOCAB.read_text())["playstyles"]
    for pid, entry in styles.items():
        for name in entry.get("exemplars") or []:
            true(f"{pid} exemplar {name}", squash(name) in catalog)


def test_detect_empty_requires_never_matches():
    print("\ndetect — a detectable playstyle matches; an undetectable one never defaults")
    # Grand Arbiter is classic stax. This test used to assert he matched
    # NOTHING, because stax had empty requires and could not fire — it was
    # documenting a limitation, not a desired behaviour. The playstyle signal work added
    # tax_opponent_spells ("spells your opponents cast cost {1} more"), so the
    # textbook stax commander now matches stax, which is the fix working.
    #
    # The invariant worth keeping is the other half: a playstyle that CANNOT
    # fire must never default-match. That is asserted below with a commander
    # whose text carries no signal at all.
    card = {
        "name": "Grand Arbiter Augustin IV",
        "type_line": "Legendary Creature — Human Advisor",
        "oracle_text": (
            "White spells you cast cost {1} less to cast.\n"
            "Blue spells you cast cost {1} less to cast.\n"
            "Spells your opponents cast cost {1} more to cast."
        ),
    }
    r = playstyles.detect(card)
    eq("status ok", r["status"], "ok")
    true("stax now matched — tax_opponent_spells fires", "stax" in r["matched"])

    # The half that must never regress: no signal at all means no match, not
    # a sensible-looking default. a fallback is not a match.
    blank = {"name": "Vanilla Bear", "type_line": "Legendary Creature — Bear",
             "oracle_text": "Trample"}
    rb = playstyles.detect(blank)
    eq("blank status ok", rb["status"], "ok")
    true("matched nothing rather than defaulting", rb["matched"] == [])


def test_detect_unknown_commander():
    print("\ndetect — unreadable commander is unknown, not empty-match")
    r = playstyles.detect({"name": "Mystery", "oracle_text": None})
    eq("status unknown", r["status"], "unknown")
    eq("no matched list pretending to be an answer", r["matched"], [])


def test_hei_bai_detectable_slice():
    print("\ndetect — Hei Bai against the signals we actually have")
    # Expert video names aristocrats, blink, counters, voltron. With current
    # oracle_signals we can only require what the text carries:
    #   sacrifice_outlet → aristocrats
    #   counter_placement → counters
    # blink/voltron need missing_signals (leaves_payoff / aura_or_equipment).
    card = {
        "name": "Hei Bai, Spirit of Balance",
        "type_line": "Legendary Creature — Bear Spirit",
        "oracle_text": (
            "Whenever Hei Bai enters or attacks, you may sacrifice another "
            "creature or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
            "When Hei Bai leaves the battlefield, put its counters on target "
            "creature you control."
        ),
    }
    r = playstyles.detect(card)
    eq("status ok", r["status"], "ok")
    true("aristocrats", "aristocrats" in r["matched"])
    true("counters", "counters" in r["matched"])
    # Honest gaps — do not pretend we detect these yet.
    true("blink not falsely claimed", "blink" not in r["matched"])
    true("voltron not falsely claimed", "voltron" not in r["matched"])


def test_treasure_maker_is_not_creature_tokens():
    print("\ndetect — Treasure makers are not the creature-tokens way")
    # Lannery creates Treasure tokens. Bare token_create used to put her on
    # the "wide board of creature tokens" playstyle. Resource tokens are not
    # creature tokens.
    lannery = {
        "name": "Captain Lannery Storm",
        "type_line": "Legendary Creature — Human Pirate",
        "oracle_text": (
            "Haste\n"
            "Whenever Captain Lannery Storm attacks, create a Treasure token.\n"
            "Whenever you sacrifice a Treasure, Captain Lannery Storm gets "
            "+1/+0 until end of turn."
        ),
    }
    r = playstyles.detect(lannery)
    eq("status ok", r["status"], "ok")
    true("token_create fires (Treasure is still a token)",
         "token_create" in r["signals"])
    true("resource_tokens fires", "resource_tokens" in r["signals"])
    true("not labelled creature-tokens playstyle", "tokens" not in r["matched"])

    # go_wide_payoff alone must not select tokens either — "wide" is a boost,
    # not a gate.
    wide_only = {
        "name": "Wide Without Bodies",
        "type_line": "Legendary Creature — Human",
        "oracle_text": (
            "Creatures you control get +1/+1 for each creature you control."
        ),
    }
    w = playstyles.detect(wide_only)
    true("wide anthem alone is not tokens", "tokens" not in w["matched"])

    krenko = {
        "name": "Krenko, Mob Boss",
        "type_line": "Legendary Creature — Goblin Warrior",
        "oracle_text": (
            "{T}: Create X 1/1 red Goblin creature tokens, where X is the "
            "number of Goblins you control."
        ),
    }
    k = playstyles.detect(krenko)
    true("creature-token maker still matches tokens", "tokens" in k["matched"])
def test_giada_enters_with_counters():
    """'enters with +1/+1 counters' is counter_placement, unlocks counters."""
    print("\ndetect — Giada enters-with counters")
    giada = {
        "name": "Giada, Font of Hope",
        "type_line": "Legendary Creature — Angel",
        "oracle_text": (
            "Flying, vigilance\n"
            "Each other Angel you control enters with an additional +1/+1 "
            "counter on it for each Angel you already control.\n"
            "{T}: Add {W}. Spend this mana only to cast an Angel spell."
        ),
    }
    r = playstyles.detect(giada)
    eq("status ok", r["status"], "ok")
    true("counter_placement signal", "counter_placement" in r["signals"])
    true("counters playstyle", "counters" in r["matched"])
    # Near-miss: charge counters are not +1/+1 placement.
    everflowing = {
        "name": "Everflowing Chalice",
        "type_line": "Artifact",
        "oracle_text": (
            "Multikicker {2}\n"
            "This artifact enters with a charge counter on it for each time "
            "it was kicked.\n"
            "{T}: Add {C} for each charge counter on this artifact."
        ),
    }
    er = playstyles.detect(everflowing)
    true("charge counters are not counter_placement",
         "counter_placement" not in er["signals"])


def test_lifegain_source_drain_not_auto_match():
    """lifegain_source/drain in requires_any tanks precision — reverted.

    Fill those empties via EDHREC research (B), not by inventing lifegain from
    incidental gain/drain riders.
    """
    print("\ndetect — lifegain_source/drain do not auto-match")
    elas = {
        "name": "Elas il-Kor, Sadistic Pilgrim",
        "type_line": "Legendary Creature — Phyrexian Kor Cleric",
        "oracle_text": (
            "Deathtouch\n"
            "Whenever another creature you control enters, you gain 1 life.\n"
            "Whenever another creature you control dies, each opponent loses 1 life."
        ),
    }
    r = playstyles.detect(elas)
    true("Elas still aristocrats", "aristocrats" in r["matched"])
    true("Elas not derived-lifegain from source/drain alone",
         "lifegain" not in r["matched"])

    tatyova = {
        "name": "Tatyova, Benthic Druid",
        "type_line": "Legendary Creature — Merfolk Druid",
        "oracle_text": (
            "Landfall — Whenever a land you control enters, you gain 1 life "
            "and draw a card."
        ),
    }
    tr = playstyles.detect(tatyova)
    true("Tatyova is landfall", "landfall" in tr["matched"])
    true("Tatyova is not lifegain", "lifegain" not in tr["matched"])


def test_bare_enters_attacks_do_not_invent():
    """do not map bare enters/attacks to blink/voltron."""
    print("\ndetect — bare enters/attacks invent nothing")
    enters_only = {
        "name": "Vanilla Enterer",
        "type_line": "Legendary Creature — Human",
        "oracle_text": "Whenever a creature enters, draw a card.",
    }
    attacks_only = {
        "name": "Vanilla Attacker",
        "type_line": "Legendary Creature — Human",
        "oracle_text": "Whenever a creature you control attacks, it gets +1/+0.",
    }
    er = playstyles.detect(enters_only)
    ar = playstyles.detect(attacks_only)
    true("bare enters is not blink", "blink" not in er["matched"])
    true("bare attacks is not voltron", "voltron" not in ar["matched"])


def test_treasure_maps_artifacts_not_tokens():
    """Treasure makers map to artifacts, not creature-tokens."""
    print("\ndetect — treasure → artifacts, not tokens")
    malcolm = {
        "name": "Malcolm, Keen-Eyed Navigator",
        "type_line": "Legendary Creature — Human Pirate",
        "oracle_text": (
            "Flying\n"
            "Whenever one or more Pirates you control deal damage to your "
            "opponents, you create a Treasure token for each opponent dealt "
            "damage."
        ),
    }
    r = playstyles.detect(malcolm)
    true("treasure_tokens signal", "treasure_tokens" in r["signals"])
    true("artifacts playstyle", "artifacts" in r["matched"])
    true("not creature-tokens playstyle", "tokens" not in r["matched"])


def test_go_tall_eot_pump_not_counters():
    """until-end-of-turn pumps are not go_tall or counters."""
    print("\ndetect — EOT pump is not counters")
    lannery = {
        "name": "Captain Lannery Storm",
        "type_line": "Legendary Creature — Human Pirate",
        "oracle_text": (
            "Haste\n"
            "Whenever Captain Lannery Storm attacks, create a Treasure token.\n"
            "Whenever you sacrifice a Treasure, Captain Lannery Storm gets "
            "+1/+0 until end of turn."
        ),
    }
    r = playstyles.detect(lannery)
    true("EOT pump is not go_tall_payoff", "go_tall_payoff" not in r["signals"])
    true("not counters from pump alone", "counters" not in r["matched"])


def test_blink_exile_until_leaves():
    """exile-until-leaves is blink, not bare enters."""
    print("\ndetect — exile until leaves → blink")
    sigrid = {
        "name": "Sigrid, God-Favored",
        "type_line": "Legendary Creature — Human Warrior",
        "oracle_text": (
            "Flash\n"
            "When Sigrid enters, exile up to one target attacking or blocking "
            "creature until Sigrid leaves the battlefield."
        ),
    }
    r = playstyles.detect(sigrid)
    true("blink signal", "blink" in r["signals"])
    true("blink playstyle", "blink" in r["matched"])


def test_go_tall_counters_from_counter_text():
    """go_tall_payoff from +1/+1 text unlocks counters."""
    print("\ndetect — go_tall counter text → counters")
    hardened = {
        "name": "Hardened Scales",
        "type_line": "Enchantment",
        "oracle_text": (
            "If one or more +1/+1 counters would be put on a creature you "
            "control, that many plus one +1/+1 counters are put on it instead."
        ),
    }
    r = playstyles.detect(hardened)
    true("go_tall_payoff signal", "go_tall_payoff" in r["signals"])
    true("counters playstyle", "counters" in r["matched"])


def test_match_signals_offline_kb_count():
    """offline signal-set matching for empty_ways accounting."""
    print("\nmatch_signals — offline KB empty_ways count")
    r = playstyles.match_signals(["treasure_tokens", "resource_tokens", "token_create"])
    true("treasure matches artifacts", "artifacts" in r["matched"])
    true("bare token_create does not match tokens",
         "tokens" not in r["matched"])
    r2 = playstyles.match_signals(["enters"])
    true("bare enters matches nothing", r2["matched"] == [])


def test_fixture_precision_floor():
    """precision on the validated 57 stays ≥88%."""
    print("\ndetect — fixture precision floor")
    rows = json.loads(FIXTURE.read_text()).get("commanders") or []
    hits = extras = 0
    for row in rows:
        oracle = row.get("oracle_text")
        if not isinstance(oracle, str) or not oracle.strip():
            continue
        card = {
            "name": row["commander"],
            "oracle_text": oracle,
            "type_line": row.get("type_line") or "",
        }
        result = playstyles.detect(card)
        if result["status"] != "ok":
            continue
        community = set(row.get("playstyles") or [])
        detected = set(result["matched"])
        hits += len(community & detected)
        extras += len(detected - community)
    prec = hits / (hits + extras) if (hits + extras) else 0.0
    true(f"precision {prec:.3f} ≥ 0.88 (HIT={hits} EXTRA={extras})",
         prec >= 0.88)


def test_detector_recall_gaps():
    """pattern expansion for voltron, artifacts, tribal recall gaps."""
    print("\ndetect — Wyleth/Sharuum/Giada recall gaps")

    wyleth = {
        "name": "Wyleth, Soul of Steel",
        "type_line": "Legendary Creature — Human Warrior",
        "oracle_text": (
            "Trample\n"
            "Whenever Wyleth attacks, draw a card for each Aura and "
            "Equipment attached to it."
        ),
    }
    wr = playstyles.detect(wyleth)
    true("Wyleth voltron", "voltron" in wr["matched"])
    true("Wyleth aura_or_equipment_attached signal",
         "aura_or_equipment_cast" in wr["signals"])

    sharuum = {
        "name": "Sharuum the Hegemon",
        "type_line": "Legendary Artifact Creature — Sphinx",
        "oracle_text": (
            "Flying\n"
            "When Sharuum enters, you may return target artifact card from "
            "your graveyard to the battlefield."
        ),
    }
    sr = playstyles.detect(sharuum)
    true("Sharuum artifacts", "artifacts" in sr["matched"])
    true("Sharuum artifact_matter signal", "artifact_matter" in sr["signals"])

    giada = {
        "name": "Giada, Font of Hope",
        "type_line": "Legendary Creature — Angel",
        "oracle_text": (
            "Flying, vigilance\n"
            "Each other Angel you control enters with an additional +1/+1 "
            "counter on it for each Angel you already control.\n"
            "{T}: Add {W}. Spend this mana only to cast an Angel spell."
        ),
    }
    gr = playstyles.detect(giada)
    true("Giada tribal", "tribal" in gr["matched"])
    true("Giada tribal_type_matter signal",
         "tribal_type_matter" in gr["signals"])
    true("Giada still counters", "counters" in gr["matched"])

    # Marchesa says "other creatures" — generic plural must not invent tribal.
    marchesa = {
        "name": "Marchesa, the Black Rose",
        "type_line": "Legendary Creature — Human Wizard",
        "oracle_text": (
            "Other creatures you control have dethrone.\n"
            "Whenever a creature you control with a +1/+1 counter on it dies, "
            "return that card to the battlefield under your control at the "
            "beginning of the next end step."
        ),
    }
    mr = playstyles.detect(marchesa)
    true("Marchesa not tribal from generic other creatures",
         "tribal" not in mr["matched"])


def test_near_miss_aristocrats_vs_blink():
    print("\ndetect — aristocrats/blink near-miss does not collapse")
    brago = {
        "name": "Brago, King Eternal",
        "type_line": "Legendary Creature — Spirit",
        "oracle_text": (
            "Flying\nWhenever Brago, King Eternal deals combat damage to a "
            "player, exile any number of target nonland permanents you control, "
            "then return those cards to the battlefield under their owner's control."
        ),
    }
    yawg = {
        "name": "Yawgmoth, Thran Physician",
        "type_line": "Legendary Creature — Phyrexian Human",
        "oracle_text": (
            "Protection from Humans\n"
            "Pay 1 life, Sacrifice another creature: Put a -1/-1 counter on "
            "up to one target creature and draw a card.\n"
            "{B}{B}, Discard a card: Proliferate."
        ),
    }
    b = playstyles.detect(brago)
    y = playstyles.detect(yawg)
    true("Brago is blink", "blink" in b["matched"])
    true("Brago is not aristocrats", "aristocrats" not in b["matched"])
    true("Yawgmoth is aristocrats", "aristocrats" in y["matched"])
    true("Yawgmoth is not blink", "blink" not in y["matched"])


def test_fixture_shape():
    print("\ncommander-playstyles.json — shape and diversity")
    fix = json.loads(FIXTURE.read_text())
    rows = fix.get("commanders") or []
    true("≥30 commanders", len(rows) >= 30)
    catalog = {}
    for n in (json.loads(GRAMMAR.read_text()).get("catalogs") or {}).get("card-names") or []:
        catalog.setdefault(squash(str(n)), str(n))
    styles_seen = set()
    for row in rows:
        true(f"row {row.get('commander')} in catalog",
             squash(row.get("commander") or "") in catalog)
        for k in ("commander", "playstyles", "source", "why", "oracle_text"):
            true(f"{row.get('commander')} has {k}", bool(row.get(k)))
        styles_seen.update(row.get("playstyles") or [])
    # Structural diversity guard — same spirit as the fixture-brief diversity work's unlike commanders.
    for need in ("aristocrats", "blink", "counters", "voltron", "spellslinger",
                 "landfall", "tokens", "reanimator", "stax", "group_hug",
                 "lifegain", "artifacts", "tribal"):
        true(f"fixture covers {need}", need in styles_seen)
    hei = [r for r in rows if r["commander"] == "Hei Bai, Spirit of Balance"]
    eq("Hei Bai present", len(hei), 1)
    true("Hei Bai has multiple playstyles", len(hei[0]["playstyles"]) >= 2)


def main() -> int:
    test_vocab_contract()
    test_exemplars_are_scryfall_names()
    test_detect_empty_requires_never_matches()
    test_detect_unknown_commander()
    test_hei_bai_detectable_slice()
    test_treasure_maker_is_not_creature_tokens()
    test_giada_enters_with_counters()
    test_lifegain_source_drain_not_auto_match()
    test_bare_enters_attacks_do_not_invent()
    test_detector_recall_gaps()
    test_treasure_maps_artifacts_not_tokens()
    test_go_tall_eot_pump_not_counters()
    test_blink_exile_until_leaves()
    test_go_tall_counters_from_counter_text()
    test_match_signals_offline_kb_count()
    test_fixture_precision_floor()
    test_near_miss_aristocrats_vs_blink()
    test_fixture_shape()
    print()
    if failed:
        print(f"FAILED {failed}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
