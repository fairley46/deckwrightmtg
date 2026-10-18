"""
test_backend_spine.py — offline + light checks.

    python test_backend_spine.py
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import sys
import tempfile
from pathlib import Path

from deckwright import mechanics
from deckwright import suggest
from deckwright import deck_state
from deckwright import precon_delta
from deckwright import swap
from deckwright import app

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
passed = failed = 0


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


def _toy_pools():
    """Minimal verified pools for suggest/pending tests."""
    sol = {"name": "Sol Ring", "type": "Artifact", "cmc": 1, "usd": "1.00",
           "keywords": [], "text": "{T}: Add {C}{C}.", "color_identity": [],
           "legal": "legal", "role": "ramp"}
    signet = {"name": "Arcane Signet", "type": "Artifact", "cmc": 2, "usd": "0.50",
              "keywords": [], "text": "{T}: Add one mana of any color in your commander's color identity.",
              "color_identity": [], "legal": "legal", "role": "ramp"}
    virtue = {"name": "Intangible Virtue", "type": "Enchantment", "cmc": 2, "usd": "0.40",
              "keywords": [], "text": "Creature tokens you control get +1/+1 and have vigilance.",
              "color_identity": ["W"], "legal": "legal", "role": "theme"}
    garrison = {"name": "Hanweir Garrison", "type": "Creature", "cmc": 3, "usd": "0.80",
                "keywords": [],
                "text": "Whenever Hanweir Garrison attacks, create two 1/1 red Human creature tokens that are tapped and attacking.",
                "color_identity": ["R"], "legal": "legal", "role": "theme"}
    filler = {"name": "Random Charm", "type": "Instant", "cmc": 2, "usd": "0.10",
              "keywords": [], "text": "Do a thing unrelated to tokens.",
              "color_identity": ["U"], "legal": "legal", "role": "deepcut"}
    counter = {"name": "Counterspell", "type": "Instant", "cmc": 2, "usd": "1.00",
               "keywords": [], "text": "Counter target spell.",
               "color_identity": ["U"], "legal": "legal", "role": "spot"}
    return {
        "ramp": [sol, signet],
        "theme": [virtue, garrison],
        "deepcut": [filler],
        "spot": [counter],
        "plan": [virtue, garrison],
    }


def _toy_deck(pools):
    deck = []
    for role, cards in pools.items():
        if role == "theme":
            # Put filler-ish deepcut in deck; leave virtue/garrison in pool for adds
            continue
        if role == "plan":
            continue
        if role == "spot":
            continue
        for c in cards[:1]:
            card = dict(c)
            card["role"] = role
            deck.append(card)
    # On-deck: a deepcut to cut, and a token-adjacent piece
    deck.append(dict(pools["deepcut"][0]))
    # A non-token card in theme role that we can cut
    dead = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
            "keywords": [], "text": "Vigilance", "color_identity": ["W"],
            "legal": "legal", "role": "theme"}
    deck.append(dead)
    for i in range(36):
        deck.append({"name": "Plains", "type": "Basic Land — Plains", "cmc": 0,
                     "keywords": [], "text": "", "color_identity": ["W"],
                     "legal": "legal", "role": "basic", "usd": "0.05"})
    return deck


def test_mechanic_relations():
    print("\nmechanic relations  — lineage + family graph")
    rel = mechanics.related("Flashback")
    eq("Flashback family", rel["mechanic"]["family"], "cast_from_graveyard")
    (ok if any(e.get("name") == "Disturb" for e in rel["lineage"]) else bad)(
        "Flashback lineages to Disturb", str(rel["lineage"]))
    tok = mechanics.family_relations("tokens")
    targets = {e["family"] for e in tok if e["direction"] == "to"}
    (ok if "go_wide_payoffs" in targets else bad)(
        "tokens feeds go_wide", str(targets))
    expanded = mechanics.expand_goal_families(["tokens"], depth=1)
    (ok if "go_wide_payoffs" in expanded and "tokens" in expanded else bad)(
        "expand tokens pulls width", str(expanded))


def test_goal_spec_uses_relations():
    print("\ngoal spec  — expansion")
    spec = suggest.goal_spec("more_tokens_wide")
    (ok if spec and "tokens" in spec["families"] else bad)("has tokens", str(spec))
    (ok if "go_wide_payoffs" in (spec or {}).get("families", []) else bad)(
        "expanded to go_wide_payoffs", str((spec or {}).get("families")))
    eq("unknown goal", suggest.goal_spec("not_a_goal"), None)


def test_suggest_plan_verified():
    print("\nsuggest plan  — verified adds")
    pools = _toy_pools()
    deck = _toy_deck(pools)
    data = {
        "commander": "Test",
        "commander_card": {"name": "Test", "color_identity": ["W", "U", "R"], "cmc": 3},
        "deck": deck,
        "role_pools": pools,
    }
    plan = suggest.plan_swaps(data, "more_tokens_wide", count=3)
    eq("plan ok", plan.get("ok"), True)
    for s in plan.get("suggestions") or []:
        # Add must exist in some pool
        found = any(swap.find_in_pool(s["add"], p) for p in pools.values())
        (ok if found else bad)(f"add {s['add']} in pool", s.get("reason"))
        eq(f"cut {s['cut']} in deck",
           any(c["name"] == s["cut"] for c in deck), True)
    # Invented goal fails cleanly
    bad_goal = suggest.plan_swaps(data, "telepathy")
    eq("bad goal rejected", bad_goal.get("ok"), False)


def test_match_notes():
    print("\nmatch notes")
    data = {"deck": [], "commander": "X"}
    n = deck_state.add_match_note(data, {
        "opponent_plan": "combo", "result": "loss",
        "text": "Lost to Thoracle", "tags": ["combo"],
    })
    eq("note id set", bool(n.get("id")), True)
    eq("one note", len(deck_state.list_match_notes(data)), 1)
    eq("delete", deck_state.delete_match_note(data, n["id"]), True)
    eq("empty after delete", len(deck_state.list_match_notes(data)), 0)


def test_pending_version_fork():
    print("\npending + version")
    pools = _toy_pools()
    # Deck must contain a cut target and pools must contain the add.
    cut = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
           "keywords": [], "text": "Vigilance", "color_identity": ["W"],
           "legal": "legal", "role": "theme"}
    keep = dict(pools["ramp"][0])
    keep["role"] = "ramp"
    data = {
        "commander": "Test",
        "commander_card": {"name": "Test", "color_identity": ["W", "R"], "cmc": 3},
        "deck": [cut, keep],
        "role_pools": pools,
        "version": 1,
    }
    staged = deck_state.stage_pending(data, [{
        "cut": "Flavor Text",
        "add": "Intangible Virtue",
        "reason": "tokens wide",
        "role": "theme",
    }], source="suggest", goal="more_tokens_wide")
    eq("staged one", len(staged), 1)
    eq("deck unchanged", [c["name"] for c in data["deck"]],
       ["Flavor Text", "Sol Ring"])
    result = deck_state.approve_pending(data, ci="WR")
    eq("approve ok", result.get("ok"), True)
    eq("version bumped", data.get("version"), 2)
    eq("parent set", data.get("parent_version"), 1)
    eq("history kept old", len(data.get("history") or []), 1)
    names = {c["name"] for c in data["deck"]}
    (ok if "Intangible Virtue" in names and "Flavor Text" not in names else bad)(
        "swap applied", str(names))
    eq("pending cleared", len(data.get("pending") or []), 0)
    # Reject path
    deck_state.stage_pending(data, [{
        "cut": "Intangible Virtue", "add": "Hanweir Garrison", "role": "theme",
    }], goal="x")
    n = deck_state.clear_pending(data)
    eq("reject clears", n, 1)


def test_change_set_d1_contract():
    """The pending change sets / D1: suggestions are a pending change set.

    Approve forks vN → vN+1. Reject leaves version + 99 alone.
    Mixed suggest + editor sets reject/approve independently.
    """
    print("\nchange set D1")
    pools = _toy_pools()
    flavor = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
              "keywords": [], "text": "Vigilance", "color_identity": ["W"],
              "legal": "legal", "role": "theme"}
    charm = {"name": "Random Charm", "type": "Instant", "cmc": 2, "usd": "0.10",
             "keywords": [], "text": "Charm.", "color_identity": ["R"],
             "legal": "legal", "role": "deepcut"}
    keep = dict(pools["ramp"][0])
    keep["role"] = "ramp"
    data = {
        "commander": "Test",
        "commander_card": {"name": "Test", "color_identity": ["W", "R"], "cmc": 3},
        "deck": [flavor, charm, keep],
        "role_pools": pools,
        "version": 3,
    }
    before_names = [c["name"] for c in data["deck"]]

    suggest_rows = deck_state.stage_pending(data, [{
        "cut": "Flavor Text",
        "add": "Intangible Virtue",
        "reason": "tokens",
        "role": "theme",
        "add_card": dict(pools["theme"][0]),  # Intangible Virtue
    }], source="suggest", goal="more_tokens_wide")
    editor_rows = deck_state.stage_pending(data, [{
        "cut": "Random Charm",
        "add": "Hanweir Garrison",
        "reason": "manual",
        "role": "theme",
        "add_card": dict(pools["theme"][1]),  # Hanweir Garrison
    }], source="editor", goal=None, replace_source_goal=False)

    eq("suggest staged", len(suggest_rows), 1)
    eq("editor staged", len(editor_rows), 1)
    eq("shared suggest set_id", bool(suggest_rows[0].get("set_id")), True)
    eq("editor has own set_id",
       editor_rows[0].get("set_id") != suggest_rows[0].get("set_id"), True)
    eq("stage never mutates 99", [c["name"] for c in data["deck"]], before_names)
    eq("version still 3 while pending", data.get("version"), 3)

    suggest_set = suggest_rows[0]["set_id"]
    # Reject the suggest set only — editor pending + deck + version stay.
    n = deck_state.clear_pending(data, set_id=suggest_set)
    eq("reject set removes one", n, 1)
    eq("reject leaves version", data.get("version"), 3)
    eq("reject leaves 99", [c["name"] for c in data["deck"]], before_names)
    eq("editor pending remains", len(data.get("pending") or []), 1)
    eq("remaining is editor", data["pending"][0].get("source"), "editor")

    # Re-stage suggest; approve that set only → v3 → v4; editor row stays pending.
    suggest_rows = deck_state.stage_pending(data, [{
        "cut": "Flavor Text",
        "add": "Intangible Virtue",
        "role": "theme",
        "add_card": dict(pools["theme"][0]),
    }], source="suggest", goal="more_tokens_wide")
    suggest_set = suggest_rows[0]["set_id"]
    result = deck_state.approve_pending(data, set_id=suggest_set, ci="WR")
    eq("approve set ok", result.get("ok"), True)
    eq("approve bumps version", data.get("version"), 4)
    eq("parent is prior tip", data.get("parent_version"), 3)
    names = {c["name"] for c in data["deck"]}
    (ok if "Intangible Virtue" in names and "Flavor Text" not in names else bad)(
        "suggest set applied", str(names))
    eq("editor still pending after set approve", len(data.get("pending") or []), 1)
    eq("charm still in 99 until editor approve", "Random Charm" in names, True)

    # Source-scoped reject clears only editor rows.
    deck_state.stage_pending(data, [{
        "cut": "Sol Ring", "add": "Arcane Signet", "role": "ramp",
        "add_card": dict(pools["ramp"][1]),
    }], source="suggest", goal="faster_mana")
    n = deck_state.clear_pending(data, source="editor")
    eq("reject by source clears editor", n, 1)
    eq("suggest set remains", len(data.get("pending") or []), 1)
    eq("source filter leaves version", data.get("version"), 4)


def test_apply_swap_card_editor():
    print("\neditor swap card  — path")
    deck = [{
        "name": "Flavor Text", "type": "Creature", "cmc": 2, "role": "theme",
        "keywords": [], "text": "", "color_identity": ["W"], "legal": "legal",
    }]
    add = {
        "name": "Intangible Virtue", "type": "Enchantment", "cmc": 2,
        "keywords": [], "text": "Creature tokens you control get +1/+1",
        "color_identity": ["W"], "legal": "legal",
    }
    out = swap.apply_swap_card(deck, "Flavor Text", add, ci="W")
    eq("added", out["added"]["name"], "Intangible Virtue")
    eq("role kept", out["added"]["role"], "theme")


def test_precon_delta_help_hurt():
    print("\nprecon delta")
    base = [
        {"name": "A", "type": "Creature", "keywords": [],
         "text": "Whenever Hanweir attacks, create two 1/1 red Human creature tokens."},
        {"name": "B", "type": "Enchantment", "keywords": [],
         "text": "Creature tokens you control get +1/+1 and have vigilance."},
        {"name": "C", "type": "Creature", "keywords": ["Flying"],
         "text": "Flying"},
        {"name": "Plains", "type": "Basic Land — Plains", "keywords": [], "text": ""},
    ]
    helps = {"name": "Coat", "type": "Artifact", "keywords": [],
             "text": "Each creature gets +1/+1 for each other creature on the battlefield that shares a type."}
    hurts = {"name": "Millstone", "type": "Artifact", "keywords": [],
             "text": "Target player mills two cards."}
    delta = precon_delta.strategy_delta(base, adds=[helps, hurts], cuts=[])
    eq("delta ok", delta.get("ok"), True)
    verdicts = {a["name"]: a["verdict"] for a in delta["adds"]}
    eq("coat helps wide precon", verdicts.get("Coat"), "helps")
    # Mill has no overlap with token lean — hurts or neutral
    (ok if verdicts.get("Millstone") in ("hurts", "neutral") else bad)(
        "mill off-plan", str(verdicts))


def test_versions_api_smoke():
    print("\nversions API")
    from fastapi.testclient import TestClient
    pools = _toy_pools()
    cut = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
           "keywords": [], "text": "Vigilance", "color_identity": ["W"],
           "legal": "legal", "role": "theme"}
    keep = dict(pools["ramp"][0])
    keep["role"] = "ramp"
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp)
        old = app.SAVE_DIR
        app.SAVE_DIR = save_dir
        try:
            out = {
                "commander": "Test Commander",
                "commander_card": {"name": "Test Commander",
                                   "color_identity": ["W", "R"], "cmc": 3},
                "deck": [cut, keep],
                "role_pools": pools,
                "price": 1.0,
                "buyable": True,
                "card_pool": "tournament_legal",
                "version": 1,
            }
            app.persist_deck(out, save_dir)
            slug = out["id"]
            data = app.load_saved_deck(slug, save_dir)
            deck_state.stage_pending(data, [{
                "cut": "Flavor Text",
                "add": "Intangible Virtue",
                "reason": "tokens wide",
                "role": "theme",
            }], source="suggest", goal="more_tokens_wide")
            deck_state.approve_pending(data, ci="WR")
            app.write_saved_deck(slug, data, save_dir)
            client = TestClient(app.app)
            r = client.get(f"/api/saved/{slug}/versions")
            eq("versions 200", r.status_code, 200)
            body = r.json()
            eq("version 2", body.get("version"), 2)
            eq("parent 1", body.get("parent_version"), 1)
            eq("history len", len(body.get("history") or []), 1)
            diff = (body.get("history") or [{}])[0].get("diff") or []
            eq("one swap in diff", len(diff), 1)
            eq("cut in diff", diff[0].get("cut"), "Flavor Text")
            eq("add in diff", diff[0].get("add"), "Intangible Virtue")
        finally:
            app.SAVE_DIR = old


def test_persist_notes_and_pending():
    print("\npersist round-trip  — notes + pending on saved deck")
    pools = _toy_pools()
    cut = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
           "keywords": [], "text": "Vigilance", "color_identity": ["W"],
           "legal": "legal", "role": "theme"}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {
            "commander": "Test Commander",
            "commander_card": {"name": "Test Commander",
                               "color_identity": ["W", "R"], "cmc": 3},
            "deck": [cut] + [
                {"name": "Plains", "type": "Basic Land — Plains", "cmc": 0,
                 "keywords": [], "text": "", "color_identity": ["W"],
                 "legal": "legal", "role": "basic", "usd": "0.05"}
                for _ in range(5)
            ],
            "role_pools": pools,
            "price": 1.0,
            "buyable": True,
            "card_pool": "tournament_legal",
        }
        app.persist_deck(out, d)
        slug = out["id"]
        data = app.load_saved_deck(slug, d)
        deck_state.add_match_note(data, {"text": "got beaten by elves",
                                         "opponent_plan": "aggro",
                                         "result": "loss"})
        plan = suggest.plan_swaps(data, "more_tokens_wide", count=2)
        if plan.get("suggestions"):
            deck_state.stage_pending(data, plan["suggestions"],
                                     source="suggest", goal="more_tokens_wide")
        app.write_saved_deck(slug, data, d)
        again = app.load_saved_deck(slug, d)
        eq("notes persisted", len(again.get("match_notes") or []), 1)
        eq("mechanics stamped", "mechanics" in again, True)
        (ok if again.get("pending") is not None else bad)(
            "pending key present", str(again.get("pending")))


def test_api_suggest_pending_smoke():
    """HTTP suggest → pending → approve/reject without model key."""
    print("\napi suggest/pending  — smoke")
    from starlette.testclient import TestClient

    pools = _toy_pools()
    cut = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
           "keywords": [], "text": "Vigilance", "color_identity": ["W"],
           "legal": "legal", "role": "theme"}
    keep = dict(pools["ramp"][0])
    keep["role"] = "ramp"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        old_save = app.SAVE_DIR
        app.SAVE_DIR = d
        try:
            out = {
                "commander": "Test Commander",
                "commander_card": {"name": "Test Commander",
                                   "color_identity": ["W", "R"], "cmc": 3},
                "deck": [cut, keep] + [
                    {"name": "Plains", "type": "Basic Land — Plains", "cmc": 0,
                     "keywords": [], "text": "", "color_identity": ["W"],
                     "legal": "legal", "role": "basic", "usd": "0.05"}
                    for _ in range(5)
                ],
                "role_pools": pools,
                "price": 1.0,
                "buyable": True,
                "card_pool": "tournament_legal",
                "version": 1,
            }
            app.persist_deck(out, d)
            slug = out["id"]
            client = TestClient(app.app)

            r = client.post("/api/suggest",
                            json={"id": slug, "goal": "more_tokens_wide", "count": 1})
            eq("suggest http", r.status_code, 200)
            body = r.json()
            eq("suggest ok", body.get("ok"), True)
            pending = (body.get("version") or {}).get("pending") or body.get("pending") or []
            (ok if pending else bad)("pending staged", str(len(pending)))

            loaded = client.get(f"/api/saved/{slug}").json()
            v0 = int(loaded.get("version") or 1)
            (ok if loaded.get("pending") else bad)("pending on disk", str(loaded.get("pending")))

            pid = pending[0]["id"]
            r2 = client.post(f"/api/saved/{slug}/pending/approve",
                             json={"pending_ids": [pid]})
            eq("approve http", r2.status_code, 200)
            eq("version bumped", r2.json().get("version"), v0 + 1)

            r3 = client.post("/api/suggest",
                             json={"id": slug, "goal": "more_tokens_wide", "count": 1})
            eq("re-stage http", r3.status_code, 200)
            r4 = client.post(f"/api/saved/{slug}/pending/reject", json={})
            eq("reject http", r4.status_code, 200)
            eq("reject cleared", len(r4.json().get("pending") or []), 0)
        finally:
            app.SAVE_DIR = old_save


def test_api_reject_leaves_deck_alone():
    """HTTP reject clears pending without bumping version or 99."""
    print("\napi reject leaves alone")
    from starlette.testclient import TestClient

    pools = _toy_pools()
    cut = {"name": "Flavor Text", "type": "Creature", "cmc": 2, "usd": "0.10",
           "keywords": [], "text": "Vigilance", "color_identity": ["W"],
           "legal": "legal", "role": "theme"}
    keep = dict(pools["ramp"][0])
    keep["role"] = "ramp"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        old_save = app.SAVE_DIR
        app.SAVE_DIR = d
        try:
            out = {
                "commander": "Test Commander",
                "commander_card": {"name": "Test Commander",
                                   "color_identity": ["W", "R"], "cmc": 3},
                "deck": [cut, keep] + [
                    {"name": "Plains", "type": "Basic Land — Plains", "cmc": 0,
                     "keywords": [], "text": "", "color_identity": ["W"],
                     "legal": "legal", "role": "basic", "usd": "0.05"}
                    for _ in range(5)
                ],
                "role_pools": pools,
                "price": 1.0,
                "buyable": True,
                "card_pool": "tournament_legal",
                "version": 1,
            }
            app.persist_deck(out, d)
            slug = out["id"]
            client = TestClient(app.app)
            before = client.get(f"/api/saved/{slug}").json()
            before_names = [c["name"] for c in before["deck"]]
            r = client.post("/api/suggest",
                            json={"id": slug, "goal": "more_tokens_wide", "count": 1})
            eq("suggest http", r.status_code, 200)
            body = r.json()
            pending = (body.get("version") or {}).get("pending") or body.get("pending") or []
            (ok if pending else bad)("pending staged", str(len(pending)))
            set_id = pending[0].get("set_id")
            (ok if set_id else bad)("set_id on staged row", str(pending[0]))
            r2 = client.post(f"/api/saved/{slug}/pending/reject",
                             json={"set_id": set_id})
            eq("reject http", r2.status_code, 200)
            rejected = r2.json()
            eq("reject cleared set", len(rejected.get("pending") or []), 0)
            eq("reject version unchanged", rejected.get("version"), 1)
            after = client.get(f"/api/saved/{slug}").json()
            eq("reject deck unchanged",
               [c["name"] for c in after["deck"]], before_names)
            eq("reject tip version", after.get("version"), 1)
        finally:
            app.SAVE_DIR = old_save


def main() -> int:
    test_mechanic_relations()
    test_goal_spec_uses_relations()
    test_suggest_plan_verified()
    test_match_notes()
    test_pending_version_fork()
    test_change_set_d1_contract()
    test_apply_swap_card_editor()
    test_precon_delta_help_hurt()
    test_versions_api_smoke()
    test_persist_notes_and_pending()
    test_api_suggest_pending_smoke()
    test_api_reject_leaves_deck_alone()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
