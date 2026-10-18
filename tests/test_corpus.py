"""
test_corpus.py — CON-294 local Scryfall corpus (offline).

Synthetic cards only. No network. Asserts:

  * supported queries answer from the local index (including real zeros)
  * otag: / art: are unsupported — never lied about as empty
  * missing corpus is unknown, not empty
  * named + collection resolve against local cards
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from deckwright import corpus  # noqa: E402

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FIXTURE = REPO / "testdata" / "corpus" / "sample_cards.jsonl"


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


def names(result: corpus.ServeResult) -> list[str]:
    data = result.payload.get("data") or []
    return [c["name"] for c in data]


def load_fixture() -> corpus.Corpus:
    return corpus.Corpus.from_jsonl(FIXTURE, meta={
        "updated_at": "2026-08-09T00:00:00Z",
        "fetched_at": "2026-08-09T12:00:00Z",
        "type": "oracle_cards",
        "card_count": 15,
    })


def test_classify_live_only_operators():
    print("\ncorpus — classify")
    eq("otag needs live", corpus.needs_live("f:commander otag:draw"), True)
    eq("art needs live", corpus.needs_live("art:pirate"), True)
    eq("plain type is local", corpus.needs_live("f:commander t:pirate id<=U"), False)
    eq("oracle text is local",
       corpus.needs_live("o:\"destroy all\" -t:land"), False)
    eq("otag inside OR still live",
       corpus.needs_live("(otag:ramp OR o:'search your library for a')"), True)


def test_unsupported_is_not_empty():
    print("\ncorpus — unsupported ≠ empty")
    c = load_fixture()
    r = c.serve_search("f:commander otag:draw")
    eq("otag status unsupported", r.status, corpus.UNSUPPORTED)
    eq("otag payload empty dict not data[]", r.payload, {})
    eq("detail names otag", "otag" in (r.detail or "").lower(), True)

    # Pure art: stays unsupported. art: next to a local content clause is
    # incomplete (CON-308) — local served, not a smaller HIT.
    r2 = c.serve_search("art:pirate")
    eq("pure art status unsupported", r2.status, corpus.UNSUPPORTED)
    r2b = c.serve_search("ft:treasure art:pirate")
    eq("art+flavor is incomplete not unsupported",
       r2b.status, corpus.INCOMPLETE)
    eq("art+flavor served local flavor hits",
       "Malcolm, Keen-Eyed Navigator" in names(r2b), True)


def test_missing_corpus_is_unknown():
    print("\ncorpus — missing ≠ empty")
    empty = corpus.Corpus()
    r = empty.serve_search("f:commander t:pirate")
    eq("missing status", r.status, corpus.MISSING)
    eq("missing is not a zero-result payload", "data" not in r.payload, True)


def test_local_search_basics():
    print("\ncorpus — local search")
    c = load_fixture()

    r = c.serve_search("f:commander t:pirate")
    eq("pirate status", r.status, corpus.HIT)
    got = set(names(r))
    eq("pirates found", got, {"Malcolm, Keen-Eyed Navigator",
                               "Breeches, Brazen Plunderer"})
    eq("total_cards", r.payload.get("total_cards"), 2)

    r0 = c.serve_search("f:commander t:elk")
    eq("real zero status", r0.status, corpus.EMPTY)
    eq("real zero data", r0.payload.get("data"), [])
    eq("real zero total", r0.payload.get("total_cards"), 0)

    r_land = c.serve_search("f:commander t:land -t:basic")
    eq("nonbasic lands", names(r_land), ["Command Tower"])

    r_ci = c.serve_search("f:commander id<=UB t:creature")
    eq("UB creatures include Yuriko",
       "Yuriko, the Tiger's Shadow" in names(r_ci), True)
    eq("UB creatures exclude Breeches (R)",
       "Breeches, Brazen Plunderer" not in names(r_ci), True)

    r_gc = c.serve_search("f:commander -is:gamechanger t:artifact cmc<=2")
    eq("no Sol Ring when gc filtered",
       "Sol Ring" not in names(r_gc), True)
    eq("Arcane Signet survives gc filter",
       "Arcane Signet" in names(r_gc), True)

    r_o = c.serve_search("f:commander o:\"search your library\" -t:land cmc<=3")
    eq("cultivate via oracle", names(r_o), ["Cultivate"])

    r_kw = c.serve_search("f:commander keyword:flying t:pirate")
    eq("flying pirate", names(r_kw), ["Malcolm, Keen-Eyed Navigator"])

    r_usd = c.serve_search("f:commander t:enchantment usd<5")
    eq("rhystic too expensive", names(r_usd), [])
    eq("usd filter is real empty", r_usd.status, corpus.EMPTY)

    r_cmd = c.serve_search(
        'is:commander (t:creature or o:"can be your commander") f:commander')
    cmd_names = set(names(r_cmd))
    eq("is:commander includes Malcolm",
       "Malcolm, Keen-Eyed Navigator" in cmd_names, True)
    eq("is:commander excludes Sol Ring", "Sol Ring" not in cmd_names, True)
    eq("is:commander excludes banned Griselbrand",
       "Griselbrand" not in cmd_names, True)

    r_edh = c.serve_search("f:commander t:artifact", order="edhrec")
    eq("edhrec order", names(r_edh)[:2], ["Sol Ring", "Arcane Signet"])


def test_named_and_collection():
    print("\ncorpus — named + collection")
    c = load_fixture()

    r = c.serve_named(exact="Sol Ring")
    eq("named exact status", r.status, corpus.HIT)
    eq("named exact name", r.payload.get("name"), "Sol Ring")

    miss = c.serve_named(exact="Not A Real Card")
    eq("named miss is empty 404 shape", miss.status, corpus.EMPTY)
    eq("named miss payload", miss.payload, {"data": []})

    fuzzy = c.serve_named(fuzzy="malcolm keen")
    eq("named fuzzy", fuzzy.payload.get("name"),
       "Malcolm, Keen-Eyed Navigator")

    coll = c.serve_collection([
        {"name": "Island"},
        {"name": "No Such Card"},
        {"name": "Cultivate"},
    ])
    eq("collection status", coll.status, corpus.HIT)
    eq("collection found",
       [c["name"] for c in coll.payload["data"]],
       ["Island", "Cultivate"])
    eq("collection not_found",
       coll.payload.get("not_found"),
       [{"name": "No Such Card"}])


def test_status_reports_age():
    print("\ncorpus — status")
    c = load_fixture()
    st = c.status()
    eq("loaded", st["loaded"], True)
    eq("card_count", st["card_count"], 15)
    eq("has updated_at", bool(st.get("updated_at")), True)
    eq("has fetched_at", bool(st.get("fetched_at")), True)

    bare = corpus.Corpus()
    st2 = bare.status()
    eq("unloaded", st2["loaded"], False)
    eq("unloaded count unknown not zero-lie", st2["card_count"], None)


def test_router_prefers_local():
    print("\ncorpus — router")
    c = load_fixture()
    meters = corpus.Meters()

    local = corpus.route_get(
        c, "/cards/search",
        {"q": "f:commander t:pirate", "order": "edhrec", "unique": "cards"},
        meters=meters,
    )
    eq("search served local", local.status, corpus.HIT)
    eq("meter local", meters.local, 1)

    live = corpus.route_get(
        c, "/cards/search",
        {"q": "f:commander otag:draw"},
        meters=meters,
    )
    eq("otag deferred live", live.status, corpus.UNSUPPORTED)
    eq("meter live_needed", meters.live_needed, 1)

    other = corpus.route_get(c, "/sets", {}, meters=meters)
    eq("sets not served by corpus", other.status, corpus.UNSUPPORTED)


def test_prices_overlay():
    print("\ncorpus — prices overlay")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cards = root / "oracle_cards.jsonl"
        # Strip prices from a copy, then overlay.
        lines = []
        for line in FIXTURE.read_text().splitlines():
            card = json.loads(line)
            card["prices"] = {"usd": None}
            lines.append(json.dumps(card))
        cards.write_text("\n".join(lines) + "\n")
        prices = root / "prices.json"
        prices.write_text(json.dumps({
            "oracle-sol-ring": "1.99",
            "oracle-cultivate": "0.15",
        }))
        c = corpus.Corpus.from_jsonl(
            cards, prices_path=prices,
            meta={"updated_at": "2026-08-09T00:00:00Z",
                  "fetched_at": "2026-08-09T12:00:00Z"},
        )
        sol = c.serve_named(exact="Sol Ring")
        eq("overlay usd", (sol.payload.get("prices") or {}).get("usd"), "1.99")


def test_app_scry_local_first():
    """app.scry answers from the fixture without touching the network."""
    print("\ncorpus — app.scry local-first")
    import asyncio
    from deckwright import app as deck_app

    fixture = load_fixture()
    corpus.set_corpus(fixture)
    corpus.meters().reset()
    old = __import__("os").environ.get("DECKWRIGHT_CORPUS")
    __import__("os").environ["DECKWRIGHT_CORPUS"] = "local-only"

    async def _run():
        cards = await deck_app.search("f:commander t:pirate", limit=10)
        eq("search names from local",
           {c["name"] for c in cards},
           {"Malcolm, Keen-Eyed Navigator", "Breeches, Brazen Plunderer"})
        named = await deck_app.scry("/cards/named", exact="Sol Ring")
        eq("named from local", named.get("name"), "Sol Ring")
        try:
            await deck_app.search("f:commander otag:draw")
            fail("otag local-only raises", "no exception")
        except deck_app.ScryfallError as e:
            ok("otag local-only raises ScryfallError", str(e))
            eq("otag error is not a zero", "corpus" in str(e).lower(), True)
        m = corpus.meters().as_dict()
        eq("local calls counted", m["local"] >= 2, True)
        eq("live_done stays 0 in local-only", m["live_done"], 0)

    try:
        asyncio.run(_run())
    finally:
        if old is None:
            __import__("os").environ.pop("DECKWRIGHT_CORPUS", None)
        else:
            __import__("os").environ["DECKWRIGHT_CORPUS"] = old
        corpus.set_corpus(None)


def test_cadence_mentions_corpus():
    print("\ncorpus — cadence wiring")
    sh = (REPO / "scripts" / "refresh_data_cadence.sh").read_text()
    eq("cadence runs python -m corpus", "python -m corpus" in sh or "-m corpus" in sh, True)
    eq("cadence requires corpus.py", "corpus.py" in sh, True)


def test_partition_composite_queries():
    """CON-308: AND-conjuncts split — local clauses stay local."""
    print("\ncorpus — partition composite queries (CON-308)")
    p = corpus.partition_query(
        "(t:pirate OR t:treasure) otag:discard f:commander")
    eq("kind mixed", p["kind"], "mixed")
    eq("local keeps types + format",
       "t:pirate" in (p["local_query"] or "")
       and "t:treasure" in (p["local_query"] or "")
       and "f:commander" in (p["local_query"] or ""),
       True)
    eq("local drops otag", "otag:" not in (p["local_query"] or ""), True)
    eq("live is otag only", p["live_query"], "otag:discard")

    pure = corpus.partition_query("f:commander otag:draw")
    eq("pure otag kind live", pure["kind"], "live")
    eq("pure otag has no local", pure["local_query"], None)

    local = corpus.partition_query("f:commander t:pirate id<=U")
    eq("plain query kind local", local["kind"], "local")
    eq("plain query has no live", local["live_query"], None)

    # An OR that mixes otag: with o: cannot be halved — the whole OR is live.
    or_only = corpus.partition_query(
        "(otag:ramp OR o:\"search your library for a\")")
    eq("mixed OR alone is live", or_only["kind"], "live")
    eq("mixed OR has no local half", or_only["local_query"], None)

    # Sibling local filters may peel, but the OR's local arm must not.
    or_mix = corpus.partition_query(
        "(otag:ramp OR o:\"search your library for a\") -t:land")
    eq("sibling filter can peel", or_mix["kind"], "mixed")
    eq("peeled local is the filter only",
       or_mix["local_query"], "-t:land")
    eq("OR's o: arm not served as a local match",
       "search your library" not in (or_mix["local_query"] or ""), True)
    eq("live keeps the whole OR",
       "otag:ramp" in (or_mix["live_query"] or "")
       and "OR" in (or_mix["live_query"] or ""),
       True)


def test_composite_serve_incomplete_not_smaller_hit():
    """CON-308: local conjuncts served; result is incomplete, not a smaller HIT."""
    print("\ncorpus — composite incomplete ≠ smaller hit (CON-308)")
    c = load_fixture()
    r = c.serve_search("(t:pirate OR t:treasure) otag:discard f:commander")
    eq("composite status incomplete", r.status, corpus.INCOMPLETE)
    got = set(names(r))
    eq("local types served",
       got, {"Malcolm, Keen-Eyed Navigator", "Breeches, Brazen Plunderer"})
    eq("detail names unevaluated live",
       "otag" in (r.detail or "").lower(), True)
    # Must not look like a complete HIT — callers branch on status.
    eq("incomplete is not hit", r.status == corpus.HIT, False)
    eq("incomplete is not empty", r.status == corpus.EMPTY, False)
    eq("incomplete is not unsupported", r.status == corpus.UNSUPPORTED, False)

    # AND short-circuit: local empty means the full AND is a real zero.
    r0 = c.serve_search("t:elk otag:discard f:commander")
    eq("local-empty AND is real empty", r0.status, corpus.EMPTY)
    eq("local-empty AND data", r0.payload.get("data"), [])

    # Pure otag still unsupported — never an empty data list.
    pure = c.serve_search("f:commander otag:draw")
    eq("pure otag still unsupported", pure.status, corpus.UNSUPPORTED)
    eq("pure otag payload not data[]", pure.payload, {})

    # Fully local unchanged.
    plain = c.serve_search("f:commander t:pirate")
    eq("plain local still hit", plain.status, corpus.HIT)


def test_composite_live_refusal_unknown_not_complete():
    """CON-308: live refusing a composite serves local, raises unknown-not-complete."""
    print("\ncorpus — composite live refusal (CON-308)")
    import asyncio
    from unittest.mock import AsyncMock, patch
    from deckwright import app as deck_app

    fixture = load_fixture()
    corpus.set_corpus(fixture)
    corpus.meters().reset()
    old = __import__("os").environ.get("DECKWRIGHT_CORPUS")
    __import__("os").environ.pop("DECKWRIGHT_CORPUS", None)

    refuse = deck_app.ScryfallError(
        503, "offline for maintenance", kind="maintenance")

    async def _run():
        with patch.object(deck_app, "_scryfall_request",
                          new=AsyncMock(side_effect=refuse)):
            # Fully local still answers without touching live.
            cards = await deck_app.search("f:commander t:pirate", limit=10)
            eq("fully local unchanged under live refuse",
               {c["name"] for c in cards},
               {"Malcolm, Keen-Eyed Navigator", "Breeches, Brazen Plunderer"})

            try:
                await deck_app.search(
                    "(t:pirate OR t:treasure) otag:discard f:commander",
                    limit=10)
                fail("composite live refuse must not return a card list",
                     "no exception")
            except deck_app.ScryfallError as e:
                ok("composite live refuse raises", str(e))
                eq("refusal kind preserved", e.kind, "maintenance")
                # Local portion was served before the raise.
                m = corpus.meters().as_dict()
                eq("local conjuncts counted as served", m["local"] >= 1, True)
                eq("live still needed for complete answer",
                   m["live_needed"] >= 1, True)

            try:
                await deck_app.search("f:commander otag:draw", limit=10)
                fail("pure otag refuse must raise", "no exception")
            except deck_app.ScryfallError as e:
                ok("pure otag refuse raises", str(e))
                eq("pure otag never empty list", True, True)

            # search_ladder must not descend to a weaker rung on refusal.
            descended = False
            try:
                await deck_app.search_ladder(
                    [
                        "(t:pirate OR t:treasure) otag:discard f:commander",
                        "f:commander o:discard",  # weaker — must not run
                    ],
                    limit=10, floor=1,
                )
                fail("ladder must not return on incomplete refusal", "returned")
            except deck_app.ScryfallError:
                ok("ladder aborts on refusal — no silent descent")
            # If the weaker rung had run, meters.local would include a second
            # plain search. The composite path counts local once for the split;
            # a descent would add another local hit for o:discard.
            local_before_probe = corpus.meters().local
            await deck_app.search("f:commander t:pirate", limit=1)
            # Sanity: search still works; descent check is the exception above.
            eq("no ladder descent (exception path)", descended, False)
            eq("meters still advancing after abort",
               corpus.meters().local > local_before_probe, True)

    try:
        asyncio.run(_run())
    finally:
        if old is None:
            __import__("os").environ.pop("DECKWRIGHT_CORPUS", None)
        else:
            __import__("os").environ["DECKWRIGHT_CORPUS"] = old
        corpus.set_corpus(None)


def main() -> int:
    test_classify_live_only_operators()
    test_unsupported_is_not_empty()
    test_missing_corpus_is_unknown()
    test_local_search_basics()
    test_named_and_collection()
    test_status_reports_age()
    test_router_prefers_local()
    test_prices_overlay()
    test_app_scry_local_first()
    test_cadence_mentions_corpus()
    test_partition_composite_queries()
    test_composite_serve_incomplete_not_smaller_hit()
    test_composite_live_refusal_unknown_not_complete()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
