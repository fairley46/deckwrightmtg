"""
test_deckwright.py — correctness checks, no test framework needed.

    python test_deckwright.py              # offline, deterministic (~seconds)
    python test_deckwright.py --live         # adds live Scryfall checks (~1 min)
    python test_deckwright.py --live --full  # every catalog term (~5 min, ~880 queries)
    python test_deckwright.py --offline      # same as default
    python test_deckwright.py --unit         # alias for --offline

Three layers, in order of how badly a failure hurts:

  1. unit      — singularization and classification logic. Offline, instant.
  2. syntax    — every vocabulary term, through classify() into a real Scryfall
                 query, must return cards. Opt-in via --live.
  3. mechanics — sim.py's oracle-text extraction against real Scryfall text.
                 Opt-in via --live.
"""


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import asyncio, sys, random, re, time
import httpx

from deckwright import app
from deckwright import sim
from deckwright import mana
from deckwright import overview
from deckwright import import_deck
from deckwright import substitution
from deckwright import precons
from deckwright import christen
from deckwright import thesis_check
from deckwright import why
from deckwright import card_score
from deckwright import bake
from deckwright import swap
from deckwright import precon_bake
from deckwright import pressure
from deckwright import release_calendar

import sys
FULL = "--full" in sys.argv
LIVE = "--live" in sys.argv
OFFLINE = "--offline" in sys.argv or "--unit" in sys.argv or not LIVE
GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

passed, failed, warned = 0, 0, 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label} {DIM}{extra}{OFF}")


def warn(label, extra=""):
    global warned
    warned += 1
    print(f"{YELLOW}  warn{OFF} {label} {DIM}{extra}{OFF}")


def live_skip(label, exc):
    """Rate-limited or unreachable Scryfall — warn, do not FAIL."""
    warn(f"{label}: Scryfall unreachable — skipped", str(exc))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def test_budget_report():
    """Advisory budget readout — total vs brief, top three by price."""
    print("\nbudget_report()  — estimate vs brief, name the expensive cards")
    deck = [
        {"name": "Cheap", "usd": "1.00"},
        {"name": "Mid", "usd": "10.00"},
        {"name": "Whale", "usd": "70.78"},
        {"name": "Also", "usd": "5.50"},
        {"name": "No price"},
        {"name": "Basic", "usd": "0.10"},
    ]
    r = app.budget_report(deck, 250)
    eq("total", r["total"], 87.38)
    eq("budget", r["budget"], 250.0)
    eq("delta under", r["delta"], -162.62)
    (ok if "under" in r["vs_brief"] and "250" in r["vs_brief"] else bad)(
        "vs_brief under", r["vs_brief"])
    eq("top three names", [c["name"] for c in r["top"]], ["Whale", "Mid", "Also"])
    eq("top three prices", [c["usd"] for c in r["top"]], [70.78, 10.0, 5.5])

    over = app.budget_report([{"name": "A", "usd": "100"}, {"name": "B", "usd": "80"}], 150)
    eq("delta over", over["delta"], 30.0)
    (ok if "over" in over["vs_brief"] else bad)("vs_brief over", over["vs_brief"])

    none = app.budget_report([{"name": "A", "usd": "10"}], None)
    eq("no brief budget", none["budget"], None)
    (ok if "no budget" in none["vs_brief"] else bad)("vs_brief missing", none["vs_brief"])


def test_budget_price_ceiling():
    """per-card price ceiling suffix for pool queries."""
    print("\nbudget_price_ceiling()  — payoff/synergy pool steer")
    eq("unset returns empty", app.budget_price_ceiling(0), "")
    eq("unset None returns empty", app.budget_price_ceiling(None), "")
    eq("formats usd ceiling", app.budget_price_ceiling(4.5), " usd<4.50")
    ramp = app.role_query("ramp", "W", {"theme": "angel", "bracket": 2}, 4.5)
    (ok if ramp and "usd<4.50" in ramp[0] else bad)(
        "role_query carries budget ceiling", ramp[0] if ramp else "")


def test_tcgplayer_mass_entry():
    """mass-entry URL — commander + collapsed basics, under length cap."""
    print("\ntcgplayer_mass_entry()  — Buy this deck URL")
    deck = ([{"name": "Sol Ring"}, {"name": "Command Tower"}]
            + [{"name": "Island"} for _ in range(3)])
    r = app.tcgplayer_mass_entry("Yuriko, the Tiger's Shadow", deck)
    eq("ok under cap", r["ok"], True)
    eq("payload collapses basics",
       r["payload"],
       "1 Yuriko, the Tiger's Shadow||1 Sol Ring||1 Command Tower||3 Island")
    (ok if r["url"] and r["url"].startswith(
        "https://www.tcgplayer.com/massentry?productline=Magic&c=") else bad)(
        "url shape", r["url"][:80] if r["url"] else "")
    (ok if "Yuriko" in r["url"] or "%2C" in r["url"] else bad)(
        "commander in url", r["url"][:120] if r["url"] else "")
    eq("length tracked", r["length"] > 0, True)
    # Cap refusal: force a payload that exceeds MASS_ENTRY_URL_CAP.
    huge = [{"name": f"Card With A Fairly Long Name Number {i}"} for i in range(400)]
    over = app.tcgplayer_mass_entry("Commander", huge)
    eq("over cap not ok", over["ok"], False)
    eq("over cap url is None", over["url"], None)
    (ok if over["length"] > app.MASS_ENTRY_URL_CAP else bad)(
        "over cap length recorded", str(over["length"]))


def test_commander_selection():
    """labels/Partner + the colour-identity ladder scoping colour identity on the ladder."""
    print("\ncommander selection  — ladder, labels, Partner")

    # when she named colours, every search rung carries id<=.
    # Named-card exact lookup is exempt (same as the pool-filter exemption).
    ladder, generic = app.commander_ladder({
        "theme": "pirate", "colors": "UB",
        "commander_query": "is:commander t:pirate f:commander",
    })
    joined = " | ".join(ladder)
    eq("brief colours normalize to UB", app.brief_color_identity(
        {"colors": "UB"}), "UB")
    (ok if all("id<=UB" in q for q in ladder) else bad)(
        "ladder rungs carry id<=UB", joined)
    (ok if all("id<=UB" in q for q in generic) else bad)(
        "generic rungs carry id<=UB", str(generic))
    # Constraint added, ladder order unchanged — type rung still present.
    (ok if any("t:pirate" in q for q in ladder) else bad)(
        "t:pirate still reaches the type rung", joined)

    # Absent colours are not id<=C — leave the ladder unscoped.
    bare, bare_generic = app.commander_ladder({
        "theme": "pirate",
        "commander_query": "is:commander t:pirate f:commander",
    })
    bare_joined = " | ".join(bare)
    eq("no colours → empty identity", app.brief_color_identity({}), "")
    (ok if all("id<=" not in q for q in bare) else bad)(
        "unscoped ladder has no id<=", bare_joined)
    (ok if all("id<=" not in q for q in bare_generic) else bad)(
        "unscoped generic has no id<=", str(bare_generic))
    (ok if any("t:pirate" in q or "o:pirate" in q for q in bare) else bad)(
        "theme still on unscoped ladder", bare_joined)

    # composition leads even when the model wrote o: fragments.
    brief_angels = {
        "theme": "angels that gain life",
        "commander_query": (
            'is:commander t:angel o:"whenever" o:"gain" o:"life" o:"loses"'
        ),
    }
    # theme_clauses needs vocab for the phrase head-noun rung.
    if not app._vocab and app.VOCAB_CACHE.exists():
        app._vocab = __import__("json").loads(app.VOCAB_CACHE.read_text())
    aladder, ageneric = app.commander_ladder(brief_angels)
    eq("composed theme is rung 0",
       "t:angel" in aladder[0] and "otag:lifegain" in aladder[0], True)

    def _payload(q):
        # IS_COMMANDER carries o:"can be your commander" — strip the shell.
        return q.replace(app.IS_COMMANDER, "").replace("f:commander", "").strip()

    # Model query and its o: relaxations still appear after composition.
    (ok if any('o:"whenever"' in q for q in aladder) else bad)(
        "model query still on ladder after composition", " | ".join(aladder[:6]))
    relaxed = [q for q in aladder
               if "t:angel" in q and 'o:"whenever"' not in q and q not in ageneric]
    (ok if relaxed else bad)(
        "ladder relaxes o: clauses before generic", " | ".join(aladder[:6]))
    tk_only = [q for q in aladder
               if "t:angel" in q and q not in ageneric
               and not re.search(r'\bo:', _payload(q))
               and "otag:" not in _payload(q)]
    (ok if tk_only else bad)(
        "ladder has t:angel without model o: clauses", " | ".join(aladder))
    eq("generic still last", aladder[-1] in ageneric, True)
    (ok if not any('o:"angels that gain life"' in q for q in aladder) else bad)(
        "no literal phrase o: rung", " | ".join(aladder))
    # Composition before type-alone and before generic.
    first_composed = next(
        (i for i, q in enumerate(aladder)
         if "t:angel" in q and "otag:lifegain" in q), -1)
    first_tk = next((i for i, q in enumerate(aladder) if q in tk_only), -1)
    generic_i = next(i for i, q in enumerate(aladder) if q in ageneric)
    (ok if 0 == first_composed < first_tk < generic_i else bad)(
        "compose → type → generic order",
        f"compose@{first_composed} tk@{first_tk} generic@{generic_i}")
    # with a model query present, composed still beats bare type.
    # Assert indices — presence alone would miss the inversion that made
    # rung 4 (t:angel) fill before rung 5 (t:angel otag:lifegain).
    eq("composed index before bare-type index",
       first_composed < first_tk, True)
    no_model, _ = app.commander_ladder({"theme": "angels that gain life"})
    eq("no model query → composed still rung 0",
       "t:angel" in no_model[0] and "otag:lifegain" in no_model[0], True)

    # Partner: keywords and oracle text; not every card.
    eq("Partner keyword", app.has_partner({
        "keywords": ["Partner"], "text": "Whenever…"}), True)
    eq("Partner with keyword", app.has_partner({
        "keywords": ["Partner with Pirated Copy"], "text": ""}), True)
    eq("Friends forever keyword", app.has_partner({
        "keywords": ["Friends forever"], "text": ""}), True)
    eq("Partner in oracle text", app.has_partner({
        "keywords": [],
        "text": "Partner (You can have two commanders if both have partner.)"}), True)
    eq("Friends forever in text", app.has_partner({
        "keywords": [], "text": "Friends forever (…two commanders…)"}), True)
    eq("no partner on normal cmdr", app.has_partner({
        "keywords": ["Flying"], "text": "Whenever a pirate deals damage…"}), False)
    eq("partnership is not Partner", app.has_partner({
        "keywords": [], "text": "Form a partnership with another player."}), False)

    # classify the three pairing mechanisms; generic Partner ≠ Partner with.
    eq("kind Partner", app.partner_kind({
        "keywords": ["Partner"], "text": ""}), "partner")
    eq("kind Partner with", app.partner_kind({
        "keywords": ["Partner with Pir, Ingenious Steward"], "text": ""}),
       "partner_with")
    eq("kind Friends forever", app.partner_kind({
        "keywords": ["Friends forever"], "text": ""}), "friends_forever")
    eq("kind none", app.partner_kind({
        "keywords": ["Flying"], "text": "Flying"}), None)
    eq("Partner with name from keyword", app.partner_with_name({
        "keywords": ["Partner with Toothy, Imaginary Friend"], "text": ""}),
       "Toothy, Imaginary Friend")
    eq("Partner with name from text", app.partner_with_name({
        "keywords": [],
        "text": "Partner with Pir, Ingenious Steward (When this creature enters…)"}),
       "Pir, Ingenious Steward")

    # Labels earned from data — five picks, unique names, known reason strings.
    pool = [
        {"name": "Popular Pirate", "edhrec_rank": 10, "usd": "5.00",
         "type": "Legendary Creature — Human Pirate", "text": "Make Treasure.",
         "keywords": [], "color_identity": ["U", "B"]},
        {"name": "Niche Pirate", "edhrec_rank": 800, "usd": "1.00",
         "type": "Legendary Creature — Pirate", "text": "Pirate tribal matters.",
         "keywords": ["Partner"], "color_identity": ["R"]},
        {"name": "Budget Flyer", "edhrec_rank": 50, "usd": "0.25",
         "type": "Legendary Creature — Bird", "text": "Flying.",
         "keywords": ["Flying"], "color_identity": ["W"]},
        {"name": "Fantasy Match", "edhrec_rank": 200, "usd": "3.00",
         "type": "Legendary Creature — Human", "text": "Heist the vault.",
         "keywords": [], "color_identity": ["B"]},
        {"name": "Also Played A", "edhrec_rank": 20, "usd": "2.00",
         "type": "Legendary Creature — Elf", "text": "Draw a card.",
         "keywords": [], "color_identity": ["G"]},
        {"name": "Also Played B", "edhrec_rank": 30, "usd": "4.00",
         "type": "Legendary Creature — Dragon", "text": "Flying.",
         "keywords": ["Flying"], "color_identity": ["R"]},
    ]
    brief = {"theme": "pirate", "flavor_terms": ["heist", "vault"]}
    labelled = app.label_commander_picks(pool, brief, n=5)
    eq("five labelled picks", len(labelled), 5)
    names = [c["name"] for c, _ in labelled]
    eq("unique names", len(names), len(set(names)))
    by_reason = {reason: card["name"] for card, reason in labelled}
    eq("most played is lowest edhrec", by_reason.get("most played"), "Popular Pirate")
    eq("best fit is on-theme", by_reason.get("best fit for your theme"), "Niche Pirate")
    eq("cheapest has lowest usd", by_reason.get("cheapest"), "Budget Flyer")
    eq("fantasy from flavor_terms", by_reason.get("closest to your fantasy"), "Fantasy Match")
    (ok if any(r == "also highly played" for _, r in labelled) else bad)(
        "filler earned from edhrec", str([r for _, r in labelled]))

    # One card cannot wear two labels; most-played wins the first claim.
    tiny = [
        {"name": "Only Pirate", "edhrec_rank": 1, "usd": "0.10",
         "type": "Legendary Creature — Pirate", "text": "Pirate.",
         "keywords": [], "color_identity": ["U"]},
        {"name": "Other", "edhrec_rank": 2, "usd": "9.00",
         "type": "Legendary Creature — Wizard", "text": "Draw.",
         "keywords": [], "color_identity": ["U"]},
    ]
    two = app.label_commander_picks(tiny, {"theme": "pirate", "flavor_terms": []}, n=2)
    eq("most played claimed first", two[0][1], "most played")
    eq("best fit is the other on-theme card or filler",
       two[0][0]["name"] != two[1][0]["name"], True)


async def test_commander_unscoped_note():
    """offline: no colours → note says picks span colours; scoped stays quiet."""
    print("\ncommander unscoped note mocked search, no inventing colours")
    from unittest.mock import AsyncMock, patch

    fake = []
    for i, ci in enumerate(
            (["U"], ["B"], ["R"], ["U", "B"], ["G"]), start=1):
        fake.append({
            "name": f"Test Commander {i}",
            "type": "Legendary Creature — Test",
            "text": "Test.",
            "usd": "1.00",
            "image": "",
            "art": "",
            "keywords": [],
            "power": "1",
            "toughness": "1",
            "cost": "{1}",
            "edhrec_rank": i,
            "color_identity": ci,
        })
    boom = AsyncMock(side_effect=RuntimeError("forced write-up failure"))

    bare = {
        "theme": "pirate",
        "angle": "steal",
        "commander_query": "is:commander t:pirate",
        "budget_total": 100,
        "bracket": 2,
        "flavor_terms": [],
    }
    with patch.object(app, "search_primary_rung",
                      AsyncMock(return_value=(
                          fake, "is:commander t:pirate", "ok", len(fake)))), \
         patch.object(app, "legal_commanders_only", side_effect=lambda c: c), \
         patch.object(app.client.messages, "create", boom):
        resp = await app.commanders(bare)
    picks = resp.get("picks") or []
    (ok if len(picks) >= 1 else bad)("unscoped still returns picks", str(len(picks)))
    note = (resp.get("note") or "").lower()
    (ok if "span" in note and "colour" in note else bad)(
        "unscoped note names spanning colours", resp.get("note"))

    scoped = {**bare, "colors": "UB"}
    with patch.object(app, "search_primary_rung",
                      AsyncMock(return_value=(
                          fake, "is:commander t:pirate id<=UB", "ok",
                          len(fake)))), \
         patch.object(app, "legal_commanders_only", side_effect=lambda c: c), \
         patch.object(app.client.messages, "create", boom):
        resp2 = await app.commanders(scoped)
    note2 = (resp2.get("note") or "").lower()
    (ok if "span" not in note2 else bad)(
        "scoped colours do not claim unscoped", resp2.get("note"))


# ---------------------------------------------------------------- 1. unit

def test_singulars():
    print("\nsingulars()")
    cases = {
        "dragons":  "dragon",
        "elves":    "elf",
        "allies":   "ally",
        "faeries":  "faerie",
        "knives":   "knife",
        "foxes":    "fox",
        "pirate":   "pirate",     # already singular, must survive
    }
    for plural, expected in cases.items():
        cands = app.singulars(plural)
        (ok if expected in cands else bad)(f"{plural} -> {expected}", f"candidates {cands}")
    # Must not mangle words that legitimately end in -ss or are too short.
    eq("'bless' not truncated", "bles" in app.singulars("bless"), False)
    eq("empty string", app.singulars(""), [])
    eq("whitespace only", app.singulars("   "), [])
    # terms() is gone — singulars() is the only variant helper.
    eq("terms() removed", hasattr(app, "terms"), False)
    (ok if "dragon" in app.singulars("dragons") else bad)(
        "singulars covers former terms() plurals", str(app.singulars("dragons")))
    # conversational themes are phrases — pull the head noun out.
    phrase = app.singulars("angels that gain life")
    (ok if "angel" in phrase else bad)(
        "phrase singulars yields head noun", str(phrase))
    (ok if "angels that gain life" in phrase else bad)(
        "phrase singulars keeps full string first", str(phrase))


def test_classify():
    print("\nclassify()  — requires vocabulary loaded")
    from deckwright import resolve as resolve_mod
    resolve_mod.load_grammar()
    cases = [
        ("pirate",      "type",    "Pirate"),      # creature type
        ("Pirates",     "type",    "Pirate"),      # plural + capitalised
        ("dragons",     "type",    "Dragon"),
        ("elves",       "type",    "Elf"),         # irregular plural
        ("treasure",    "type",    "Treasure"),    # artifact type, not keyword action
        ("artifact",    "type",    "Artifact"),    # card type
        ("legendary",   "type",    "Legendary"),   # supertype
        ("aura",        "type",    "Aura"),        # enchantment type
        ("flying",      "keyword", "Flying"),      # keyword ability
        ("scry",        "keyword", "Scry"),        # keyword action
        ("landfall",    "keyword", "Landfall"),    # ability word
        ("lifelink",    "keyword", "Lifelink"),
        ("spiderman",   "set",     "spm"),         # UB theme is a set
        ("steal",       "otag",    "theft"),       # behaviour before keyword:steal
        ("ramp",        "otag",    "ramp"),
        ("heists",      "text",    "heists"),      # unresolved — not keyword:heist
        ("stax",        "text",    "stax"),        # no such otag — unresolved
        ("lifegain",    "otag",    "lifegain"),    # behaviour vocab, verified
        ("qqzzxnothing", "text",   "qqzzxnothing"),
    ]
    for term, want_kind, want_canon in cases:
        kind, canon = app.classify(term)
        (ok if (kind, canon) == (want_kind, want_canon) else bad)(
            f"{term!r}", f"got ({kind}, {canon}), want ({want_kind}, {want_canon})")


def test_theme_clauses():
    print("\ntheme_clauses()  — right operator per kind")
    from deckwright import resolve as resolve_mod
    resolve_mod.load_grammar()
    eq("type -> t:",        app.theme_clauses("dragons")[0], "t:dragon")
    eq("keyword -> keyword:", app.theme_clauses("flying")[0], "keyword:flying")
    eq("multi-word quoted", app.theme_clauses("first strike")[0], 'keyword:"first strike"')
    eq("set -> set:",       app.theme_clauses("spiderman")[0], "set:spm")
    eq("steal -> otag:theft", app.theme_clauses("steal")[0], "otag:theft")
    eq("lifegain -> otag:", app.theme_clauses("lifegain")[0], "otag:lifegain")
    # Integration ruling (disagreed here).
    # An unresolved word may still earn a rules-text rung — search_ladder
    # reports which rung it landed on, so an o: attempt is not a silent
    # fallback. What it must NEVER earn is an unverified otag:, because a
    # dead tag 400s and reads as "no cards" (one layer up).
    for unknown in ("qqzzxnothing", "heists", "stax", "aggro", "control"):
        cl = app.theme_clauses(unknown)
        (ok if not any(c.startswith("otag:") for c in cl) else bad)(
            f"{unknown} invents no otag:", str(cl))
    # function words are player vocabulary — otag: first, never bare o:.
    ramp_clauses = app.theme_clauses("ramp")
    eq("ramp leads with otag:", ramp_clauses[0] if ramp_clauses else "", "otag:ramp")
    (ok if not any(c == "o:ramp" for c in ramp_clauses) else bad)(
        "ramp has no bare o:ramp", str(ramp_clauses))
    # wipe/boardwipe/sweeper are Scryfall aliases for the same 934-card pool;
    # assert the family, not which alias the vocabulary happened to store.
    for term, tag in (("draw", "draw"), ("removal", "removal"),
                      ("wipe", ("wipe", "boardwipe", "sweeper"))):
        clauses = app.theme_clauses(term)
        want = tag if isinstance(tag, tuple) else (tag,)
        (ok if clauses and clauses[0] in {f"otag:{t}" for t in want} else bad)(
            f"{term} leads with otag:{'/'.join(want)}", str(clauses))
        (ok if not any(c == f"o:{term}" for c in clauses) else bad)(
            f"{term} has no bare o:{term}", str(clauses))
    # wipe is an alias of boardwipe (same 934-card pool).
    wipe = app.theme_clauses("wipe")
    (ok if wipe and wipe[0] in ("otag:wipe","otag:boardwipe","otag:sweeper") else bad)(
        "wipe → the 934-card boardwipe pool", str(wipe))
    (ok if not any(c == "o:wipe" for c in wipe) else bad)(
        "wipe has no bare o:wipe", str(wipe))
    # steal maps to theft — never the one-card keyword:steal.
    steal = app.theme_clauses("steal")
    eq("steal → otag:theft", steal[0] if steal else "", "otag:theft")
    # phrase themes compose thing + behaviour before the type alone.
    phrase = app.theme_clauses("angels that gain life")
    eq("phrase composes", phrase[0] if phrase else "", "t:angel otag:lifegain")
    (ok if not any(c == 'o:"angels that gain life"' for c in phrase) else bad)(
        "phrase is not one literal o: term", str(phrase))
    # Composition: thing + behaviour.
    steal_pirates = app.theme_clauses("pirates that steal")
    eq("pirates that steal composes",
       steal_pirates[0] if steal_pirates else "", "t:pirate otag:theft")
    # word-bank hit is a real resolution, not an invented otag:.
    expensive = app.theme_clauses("expensive")
    (ok if expensive and expensive[0].startswith("o:") else bad)(
        "word-bank theme is o:", str(expensive))


# ------------------------------------------------------- 2. syntax sweep

# Full sweep hits ~880 Scryfall queries; pace below 10 req/s and retry hard.
PACE = 0.5 if FULL else 0.2
COUNT_TRIES = 8 if FULL else 4
RETRY_ROUNDS = 3 if FULL else 1


async def count(client, clause, tries=COUNT_TRIES):
    """Card count for a query. Returns (n, error).

    A 404 from Scryfall means "no cards matched" — a real zero. Anything else
    (429, 5xx, timeout) means the request failed and the count is unknown. Those
    must NOT be reported as zero: doing that silently manufactures test failures
    that look exactly like syntax errors, which is how this harness lied the
    first time it ran.
    """
    delay = 0.5
    for attempt in range(tries):
        try:
            r = await client.get("https://api.scryfall.com/cards/search",
                                 params={"q": clause}, headers=app.UA, timeout=25)
            if r.status_code == 404:
                return 0, None                      # genuine "no cards"
            if r.status_code == 200:
                return r.json().get("total_cards", 0), None
            if r.status_code in (429, 500, 502, 503):
                wait = delay
                if r.status_code == 429:
                    try:
                        wait = max(wait, float(r.headers.get("Retry-After", wait)))
                    except (TypeError, ValueError):
                        pass
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            return None, f"HTTP {r.status_code}"
        except Exception:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    return None, f"unreachable after {tries} tries"


async def _grade_term(client, term, ladder):
    """Best card count for one vocabulary term's clause ladder."""
    best, err = 0, None
    for clause in ladder:
        n, e = await count(client, clause)
        await asyncio.sleep(PACE)
        if e is not None:
            err = e
            continue
        err = None
        if n > best:
            best = n
        if n >= 1:
            break
    return best, err


async def sweep(client, label, terms_and_clauses):
    """Every clause must return at least one card, or the operator is wrong."""
    total = len(terms_and_clauses)
    pending = list(terms_and_clauses)
    results = {}                                    # term -> (best, err)

    for round_num in range(RETRY_ROUNDS):
        if not pending:
            break
        if round_num:
            await asyncio.sleep(2 * round_num)      # cool down before retry pass
        still_pending = []
        for term, ladder in pending:
            best, err = await _grade_term(client, term, ladder)
            prev = results.get(term)
            if prev and prev[0] >= 1:
                continue                            # already hit on an earlier round
            if best >= 1 or err is None:
                results[term] = (best, err)
            else:
                results[term] = (best, err)
                still_pending.append((term, ladder))
        pending = still_pending

    errors = [(t, ladder[0], results[t][1])
              for t, ladder in terms_and_clauses
              if results.get(t, (0, None))[0] < 1 and results.get(t, (0, None))[1]]
    empties = [(t, ladder[0], 0)
               for t, ladder in terms_and_clauses
               if results.get(t, (0, None))[0] < 1 and results.get(t, (0, None))[1] is None]
    hits = sum(1 for t, _ in terms_and_clauses if results.get(t, (0, None))[0] >= 1)

    if errors:
        msg = f"{label}: {len(errors)}/{total} requests failed — counts unknown, not judged"
        (bad if FULL else warn)(msg)
        for term, clause, e in errors[:5]:
            print(f"{DIM}        {term:<28} {clause:<36} -> {e}{OFF}")
        if len(errors) > 5:
            print(f"{DIM}        ... and {len(errors)-5} more{OFF}")
    if empties:
        # A vocabulary term with no printed cards is a real thing (obscure types
        # from joke sets). It means the operator is right and the term is dead.
        warn(f"{label}: {len(empties)}/{total} terms have no cards in print")
        for term, clause, n in empties[:12]:
            print(f"{DIM}        {term:<28} {clause:<36} -> {n}{OFF}")
        if len(empties) > 12:
            print(f"{DIM}        ... and {len(empties)-12} more{OFF}")
    if errors and FULL:
        bad(f"{label}: {hits}/{total} terms return cards — {len(errors)} ungraded")
    elif errors:
        warn(f"{label}: {hits}/{total} graded, {len(errors)} ungraded — hit rate not judged")
    elif total and hits / total >= 0.90:
        ok(f"{label}: {hits}/{total} terms return cards")
    else:
        bad(f"{label}: only {hits}/{total} terms return cards — operator likely wrong")
    return empties


async def test_vocabulary_sweep():
    scope = "every term" if FULL else "sampled"
    print(f"\nvocabulary sweep  — {scope}: classify() -> Scryfall -> must return cards")
    vocab = await app.load_vocab()
    rng = random.Random(1)             # deterministic sample, so runs compare

    async with httpx.AsyncClient() as client:
        for kind in ("type", "keyword"):
            allterms = sorted(vocab[kind].values())
            picked = allterms if FULL else rng.sample(allterms, min(40, len(allterms)))
            pairs = []
            for t in picked:
                clauses = app.theme_clauses(t)
                # A catalog term must resolve to its own operator, never to text.
                if not clauses or clauses[0].startswith(("o:", "otag:")):
                    bad(f"{kind} {t!r} did not resolve", str(clauses[:2]))
                    continue
                pairs.append((t, clauses))
            await sweep(client, f"{kind}s ({len(pairs)} terms)", pairs)


# ------------------------------------------------------ 3. mechanics

async def oracle(client, name, tries=4):
    """Fetch one card. Returns (card, error). 404 is a real miss; 429/5xx retry."""
    delay = 0.5
    for attempt in range(tries):
        try:
            r = await client.get("https://api.scryfall.com/cards/named",
                                 params={"exact": name}, headers=app.UA, timeout=20)
            if r.status_code == 200:
                await asyncio.sleep(PACE)
                return app.trim_card(r.json()), None
            if r.status_code == 404:
                await asyncio.sleep(PACE)
                return None, None
            if r.status_code in (429, 500, 502, 503):
                wait = delay
                if r.status_code == 429:
                    try:
                        wait = max(wait, float(r.headers.get("Retry-After", wait)))
                    except (TypeError, ValueError):
                        pass
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            return None, f"HTTP {r.status_code}"
        except Exception as e:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            if attempt == tries - 1:
                return None, str(e)
    return None, f"unreachable after {tries} tries"


async def test_mechanics():
    """Extraction against real oracle text, not remembered text.

    Each case is (card, field, expected). These are the facts that move every
    curve in the simulation, so a silent miss here is the worst kind of bug.
    """
    print("\nsim.mechanics()  — oracle text -> mechanical facts")
    cases = [
        ("Sol Ring",              "produces", 2),
        ("Arcane Signet",         "produces", 1),
        ("Azusa, Lost but Seeking", "drop",   2),   # "play two additional lands"
        ("Exploration",           "drop",     1),   # "an additional land"
        ("Rampant Growth",        "fetch",    1),
        ("Cultivate",             "fetch",    1),   # searches two, one enters; other to hand
        ("Kodama's Reach",        "fetch",    1),   # same
        ("Nature's Lore",         "fetch",    1),   # Forest card, not "basic land"
        ("Burgeoning",            "drop",     0),   # opponent-triggered; model can't express
        ("Lightning Bolt",        "produces", 0),   # must not false-positive
        ("Lightning Bolt",        "drop",     0),
        ("Agadeem's Awakening",   "land",     True),  # modal DFC — text from card_faces
        ("Malakir Rebirth",       "land",     True),  # modal DFC — land face must be visible
    ]
    async with httpx.AsyncClient() as client:
        for name, field, want in cases:
            card, err = await oracle(client, name)
            if err:
                warn(f"{name}: could not fetch", err)
                continue
            if card is None:
                warn(f"{name}: not found on Scryfall", "skipped")
                continue
            if field == "land" and not card["text"].strip():
                bad(f"{name}.text", "empty — card_faces not read")
                continue
            got = sim.mechanics(card, set())[field]
            (ok if got == want else bad)(f"{name}.{field}", f"got {got}, want {want}"
                                         + (f"  |  {card['text'][:70]!r}" if got != want else ""))

        # Normal layout: top-level oracle_text, unchanged by card_faces logic.
        tower, terr = await oracle(client, "Command Tower")
        if terr:
            warn("Command Tower: could not fetch", terr)
        elif tower:
            eq("Command Tower.text", tower["text"].startswith("{T}: Add one mana"),
               True)
        else:
            warn("Command Tower: not found", "skipped")

        yuriko, yerr = await oracle(client, "Yuriko, the Tiger's Shadow")
        if yerr:
            warn("Yuriko: could not fetch", yerr)
        elif yuriko:
            (ok if len(yuriko["text"]) >= 367 else bad)(
                "Yuriko full oracle text", f"len={len(yuriko['text'])}")
            (ok if "mana value" in yuriko["text"].lower() else bad)(
                "Yuriko payoff clause present", repr(yuriko["text"][-90:]))
        else:
            warn("Yuriko: not found", "skipped")

def _card(name, *, typ="Creature", text="", ci=None, legal="legal", role="theme", usd="1.00"):
    return {"name": name, "type": typ, "text": text, "color_identity": ci or [],
            "legal": legal, "role": role, "usd": usd, "cmc": 1, "cost": "{1}"}


def test_validate_deck():
    """Assembled-deck legality. Deliberately broken decks must fail closed."""
    print("\nvalidate_deck()  — Commander rules on the finished 100")
    cmdr = _card("Test Commander", typ="Legendary Creature — Human", ci=["U", "B"])

    def ninety_nine():
        # 36 basics + 63 uniques fills a legal skeleton under UB.
        basics = [_card("Island", typ="Basic Land — Island", ci=["U"], role="basic")
                  for _ in range(18)]
        basics += [_card("Swamp", typ="Basic Land — Swamp", ci=["B"], role="basic")
                   for _ in range(18)]
        rest = [_card(f"Piece {i}", typ="Instant", text="Draw a card.", ci=["U"], role="draw")
                for i in range(63)]
        return basics + rest

    # Happy path: 99 + legal UB commander.
    good = ninety_nine()
    eq("len 99", len(good), 99)
    r = app.validate_deck(cmdr, good)
    eq("legal deck is ok", r["ok"], True)
    eq("legal deck is buyable", r["buyable"], True)

    # Too few cards.
    r = app.validate_deck(cmdr, good[:98])
    eq("98 cards not ok", r["ok"], False)
    (ok if any(v["code"] == "size" for v in r["violations"]) else bad)(
        "size violation reported", str(r["violations"]))

    # Duplicate non-basic.
    dup = good[:]
    dup[40] = _card("Piece 0", typ="Instant", text="Draw a card.", ci=["U"])  # clashes with Piece 0
    r = app.validate_deck(cmdr, dup)
    eq("duplicate non-basic not ok", r["ok"], False)
    (ok if any(v["code"] == "singleton" for v in r["violations"]) else bad)(
        "singleton violation reported", str(r["violations"]))

    # Basics may repeat.
    r = app.validate_deck(cmdr, good)
    eq("repeated basics still ok", r["ok"], True)

    # Off-color card.
    off = good[:]
    off[50] = _card("Lightning Bolt", typ="Instant", text="Bolt.", ci=["R"], role="spot")
    r = app.validate_deck(cmdr, off)
    eq("off-color not ok", r["ok"], False)
    (ok if any(v["code"] == "color_identity" for v in r["violations"]) else bad)(
        "color_identity violation reported", str(r["violations"]))

    # Banned card (colorless so we isolate the ban rule from color identity).
    banned = good[:]
    banned[51] = _card("Black Lotus", typ="Artifact", text="{T}: Add three mana.",
                       ci=[], legal="banned", role="ramp")
    r = app.validate_deck(cmdr, banned)
    eq("banned card not ok", r["ok"], False)
    (ok if any(v["code"] == "banned" for v in r["violations"]) else bad)(
        "banned violation reported", str(r["violations"]))
    eq("banned not buyable by default", r["buyable"], False)

    # Casual mode: banned is flagged but buyable.
    r = app.validate_deck(cmdr, banned, card_pool=app.CARD_POOL_CASUAL)
    eq("casual allows banned", r["ok"], True)
    eq("casual still buyable", r["buyable"], True)
    (ok if "Black Lotus" in r["banned"] else bad)("banned list populated", str(r["banned"]))
    eq("mode is casual", r["mode"], app.CARD_POOL_CASUAL)
    eq("default mode is tournament legal",
       app.validate_deck(cmdr, good)["mode"], app.CARD_POOL_TOURNAMENT)

    # house_rules kwarg maps to casual (backward compat).
    r = app.validate_deck(cmdr, banned, house_rules=True)
    eq("house_rules maps to casual", r["mode"], app.CARD_POOL_CASUAL)

    # Non-legendary commander (same colors as the deck so only the commander rule fires).
    r = app.validate_deck(_card("Wall of Denial", typ="Creature — Wall", ci=["U", "B"]), good)
    eq("non-legendary commander not ok", r["ok"], False)
    (ok if any(v["code"] == "commander" for v in r["violations"]) else bad)(
        "commander violation reported", str(r["violations"]))

    # trim_card must keep the fields validation needs.
    raw = {"name": "Sol Ring", "mana_cost": "{1}", "cmc": 1, "type_line": "Artifact",
           "oracle_text": "{T}: Add {C}{C}.", "color_identity": [],
           "legalities": {"commander": "legal"}, "game_changer": False,
           "prices": {"usd": "1.50"}}
    t = app.trim_card(raw)
    eq("trim keeps color_identity", t.get("color_identity"), [])
    eq("trim keeps legal", t.get("legal"), "legal")
    eq("trim keeps gc from game_changer", t.get("gc"), False)
    # Game Changer status comes from the boolean field, never legalities.
    # The old bug read something like "gamechanger" in legalities and silently
    # failed closed — bracket-2 filtering looked like it worked and didn't.
    poisoned = {**raw, "game_changer": False,
                "legalities": {"commander": "legal", "gamechanger": "restricted"}}
    eq("gc ignores legalities.gamechanger junk", app.trim_card(poisoned)["gc"], False)
    flagged = {**raw, "game_changer": True,
               "legalities": {"commander": "legal"}}
    eq("gc follows game_changer=True", app.trim_card(flagged)["gc"], True)

    # truncation: full oracle text — Yuriko is 367 chars and used to lose 147.
    long = "x" * 367
    raw_long = {**raw, "name": "Long Text", "oracle_text": long}
    eq("trim keeps full oracle text", len(app.trim_card(raw_long)["text"]), 367)

    # contract fields
    raw_full = {
        **raw,
        "keywords": ["Flying"],
        "produced_mana": ["U"],
        "layout": "normal",
        "power": "*",
        "toughness": "1+*",
        "purchase_uris": {"tcgplayer": "https://example.com/tcg"},
        "image_uris": {"normal": "https://example.com/normal.jpg",
                       "large": "https://example.com/large.jpg"},
        "edhrec_rank": 42,
    }
    tf = app.trim_card(raw_full)
    eq("trim keywords", tf["keywords"], ["Flying"])
    eq("trim produced_mana", tf["produced_mana"], ["U"])
    eq("trim layout", tf["layout"], "normal")
    eq("power stays string star", tf["power"], "*")
    eq("toughness stays string", tf["toughness"], "1+*")
    eq("image normal only", tf["image"], "https://example.com/normal.jpg")
    eq("purchase_uris kept", tf["purchase_uris"].get("tcgplayer"),
       "https://example.com/tcg")
    eq("edhrec_rank kept", tf["edhrec_rank"], 42)
    fm = app.for_model(tf)
    eq("for_model drops image", "image" in fm, False)
    eq("for_model drops purchase_uris", "purchase_uris" in fm, False)
    eq("for_model drops edhrec_rank", "edhrec_rank" in fm, False)
    eq("for_model keeps keywords", fm.get("keywords"), ["Flying"])
    eq("for_model keeps power", fm.get("power"), "*")

    # buy link helper — missing uri → None (basics); present → tcgplayer URL.
    eq("tcgplayer_url from trim", app.tcgplayer_url(tf), "https://example.com/tcg")
    eq("tcgplayer_url empty uris", app.tcgplayer_url({"purchase_uris": {}}), None)
    eq("tcgplayer_url missing", app.tcgplayer_url({}), None)
    eq("tcgplayer_url ignores blank",
       app.tcgplayer_url({"purchase_uris": {"tcgplayer": ""}}), None)

    # DFC: power/toughness/image from faces when top-level null
    dfc = {
        "name": "Delver // Insect",
        "layout": "transform",
        "mana_cost": None,
        "type_line": "Creature — Human Wizard // Creature — Human Insect",
        "oracle_text": None,
        "power": None,
        "toughness": None,
        "color_identity": ["U"],
        "legalities": {"commander": "legal"},
        "game_changer": False,
        "prices": {},
        "keywords": ["Flying", "Transform"],
        "card_faces": [
            {"mana_cost": "{U}", "type_line": "Creature — Human Wizard",
             "oracle_text": "Look at the top card.", "power": "1", "toughness": "1",
             "image_uris": {"normal": "https://example.com/front.jpg"}},
            {"mana_cost": "", "type_line": "Creature — Human Insect",
             "oracle_text": "Flying", "power": "3", "toughness": "2",
             "image_uris": {"normal": "https://example.com/back.jpg"}},
        ],
    }
    td = app.trim_card(dfc)
    eq("dfc power from faces", td["power"], "1 // 3")
    eq("dfc toughness from faces", td["toughness"], "1 // 2")
    eq("dfc image from front face", td["image"], "https://example.com/front.jpg")
    eq("dfc layout", td["layout"], "transform")
    tower_raw = {**raw, "name": "Command Tower", "mana_cost": "", "type_line": "Land",
                 "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
                 "power": None, "toughness": None}
    eq("empty mana_cost is real", app.trim_card(tower_raw)["cost"], "")

    # Game Changers excluded at query time for bracket ≤ 2.
    q = app.role_query("ramp", "UB", {"theme": "pirates", "bracket": 2}, 5.0)[0]
    (ok if "-is:gamechanger" in q else bad)("bracket 2 excludes game changers", q)
    (ok if "-t:land" in q and " t:land" not in q else bad)("ramp query excludes lands", q)
    q3 = app.role_query("ramp", "UB", {"theme": "pirates", "bracket": 3}, 5.0)[0]
    (ok if "-is:gamechanger" not in q3 else bad)("bracket 3 keeps game changers", q3)


def test_partner_commanders():
    """pair compatibility, CI union, 98-card main deck."""
    print("\npartner commanders  — pair, CI union, 98+2")

    thrasios = _card(
        "Thrasios, Triton Hero", typ="Legendary Creature — Merfolk Wizard",
        ci=["U", "G"], text="Partner\n{4}: Scry 1.", role="commander")
    thrasios["keywords"] = ["Partner"]
    tymna = _card(
        "Tymna the Weaver", typ="Legendary Creature — Human Cleric",
        ci=["W", "B"], text="Partner\nAt the beginning of your postcombat main phase…",
        role="commander")
    tymna["keywords"] = ["Partner"]
    yuriko = _card(
        "Yuriko, the Tiger's Shadow", typ="Legendary Creature — Human Ninja",
        ci=["U", "B"], text="Ninjutsu…", role="commander")
    yuriko["keywords"] = []
    pir = _card(
        "Pir, Ingenious Steward", typ="Legendary Creature — Human Artificer",
        ci=["G"], text="Partner with Toothy, Imaginary Friend\n…", role="commander")
    pir["keywords"] = ["Partner with Toothy, Imaginary Friend"]
    toothy = _card(
        "Toothy, Imaginary Friend", typ="Legendary Creature — Illusion",
        ci=["U"], text="Partner with Pir, Ingenious Steward\n…", role="commander")
    toothy["keywords"] = ["Partner with Pir, Ingenious Steward"]
    henzie = _card(
        "Henzie \"Toolbox\" Torre", typ="Legendary Creature — Devil Rogue",
        ci=["B", "R", "G"], text="Friends forever\n…", role="commander")
    henzie["keywords"] = ["Friends forever"]
    street = _card(
        "Street Urchin", typ="Legendary Creature — Devil Citizen",
        ci=["R"], text="Friends forever\n…", role="commander")
    street["keywords"] = ["Friends forever"]

    eq("Partner + Partner compatible",
       app.partners_compatible(thrasios, tymna), True)
    eq("Partner cannot pair with non-partner",
       app.partners_compatible(thrasios, yuriko), False)
    eq("cannot partner with self",
       app.partners_compatible(thrasios, thrasios), False)
    eq("Partner with named half",
       app.partners_compatible(pir, toothy), True)
    eq("Partner with rejects wrong half",
       app.partners_compatible(pir, thrasios), False)
    eq("Friends forever pair",
       app.partners_compatible(henzie, street), True)
    eq("Friends forever rejects Partner",
       app.partners_compatible(henzie, thrasios), False)

    ci = app.union_color_identity(thrasios, tymna)
    eq("Thrasios+Tymna CI is WUBG", ci, ["W", "U", "B", "G"])
    eq("CI string WUBG", app.identity_string(ci), "WUBG")
    eq("Pir+Toothy CI is UG",
       app.union_color_identity(pir, toothy), ["U", "G"])
    eq("deck size for two commanders is 98", app.deck_size_for(2), 98)
    eq("deck size for one commander is 99", app.deck_size_for(1), 99)

    # Partner deck: 98 cards inside the union pass; a red card fails.
    basics = [_card("Island", typ="Basic Land — Island", ci=["U"], role="basic")
              for _ in range(20)]
    basics += [_card("Forest", typ="Basic Land — Forest", ci=["G"], role="basic")
               for _ in range(18)]
    rest = [_card(f"Piece {i}", typ="Instant", text="Draw a card.",
                  ci=["U"], role="draw") for i in range(60)]
    partner_deck = basics + rest
    eq("partner main deck len 98", len(partner_deck), 98)
    r = app.validate_deck(thrasios, partner_deck, partner=tymna)
    eq("98+2 partner deck ok", r["ok"], True)
    eq("report wants 98", r.get("deck_size"), 98)

    # Same 98 under a single commander is still a size failure.
    r = app.validate_deck(thrasios, partner_deck)
    eq("98 alone still fails size", r["ok"], False)

    # Identity is the union: white is legal with Tymna, red is not.
    with_white = partner_deck[:]
    with_white[50] = _card("Swords to Plowshares", typ="Instant", text="Exile.",
                           ci=["W"], role="spot")
    r = app.validate_deck(thrasios, with_white, partner=tymna)
    eq("union allows white", r["ok"], True)
    r = app.validate_deck(thrasios, with_white)  # Thrasios alone is UG
    eq("Thrasios alone rejects white", r["ok"], False)

    off = partner_deck[:]
    off[50] = _card("Lightning Bolt", typ="Instant", text="Bolt.", ci=["R"], role="spot")
    r = app.validate_deck(thrasios, off, partner=tymna)
    eq("union rejects red", r["ok"], False)
    (ok if any(v["code"] == "color_identity" for v in r["violations"]) else bad)(
        "color_identity on red in WUBG pair", str(r["violations"]))

    # Incompatible pair fails closed — never build an illegal command zone.
    r = app.validate_deck(thrasios, partner_deck, partner=yuriko)
    eq("incompatible partners not ok", r["ok"], False)
    (ok if any(v["code"] == "partner" for v in r["violations"]) else bad)(
        "partner violation reported", str(r["violations"]))

    # legal_commanders_only still accepts a partner legendary creature.
    kept = app.legal_commanders_only([thrasios, tymna, yuriko,
        _card("Grizzly Bears", typ="Creature — Bear", ci=["G"])])
    eq("legal_commanders_only keeps partners",
       [c["name"] for c in kept],
       ["Thrasios, Triton Hero", "Tymna the Weaver", "Yuriko, the Tiger's Shadow"])

    # Resolve helpers used by /api/build — both names leave the 99.
    commanders = app.resolve_commanders(thrasios, tymna)
    eq("resolve two commanders", [c["name"] for c in commanders],
       ["Thrasios, Triton Hero", "Tymna the Weaver"])
    eq("resolve CI string", app.identity_string(
        app.union_color_identity(*commanders)), "WUBG")

    # Mana plan accounts for both commander costs in average MV.
    cheap = _card("A", typ="Instant", ci=["U"], text="Draw.", role="draw")
    cheap["cmc"] = 1
    cheap["cost"] = "{U}"
    thrasios["cmc"] = 2
    thrasios["cost"] = "{G}{U}"
    tymna["cmc"] = 2
    tymna["cost"] = "{W}{B}"
    avg_one = mana.average_spell_mv([cheap], thrasios)
    avg_pair = mana.average_spell_mv([cheap], [thrasios, tymna])
    (ok if avg_pair != avg_one else bad)(
        "pair MV includes both commanders", f"one={avg_one} pair={avg_pair}")
    # (1+2+2)/3 vs (1+2)/2
    eq("pair avg MV", round(avg_pair, 4), round(5 / 3, 4))


def test_card_pool_modes():
    """tournament legal vs casual card pool."""
    print("\ncard pool modes  — f:commander filter and banned-card handling")

    brief_legal = {"theme": "pirate", "card_pool": app.CARD_POOL_TOURNAMENT}
    brief_casual = {"theme": "pirate", "card_pool": app.CARD_POOL_CASUAL}

    q_legal = app.role_query("ramp", "UB", brief_legal, 5.0)[0]
    q_casual = app.role_query("ramp", "UB", brief_casual, 5.0)[0]
    (ok if "f:commander" in q_legal else bad)("tournament legal keeps f:commander", q_legal)
    (ok if "f:commander" not in q_casual else bad)("casual drops f:commander", q_casual)

    ladder_legal, _ = app.commander_ladder({
        "theme": "pirate", "card_pool": app.CARD_POOL_TOURNAMENT,
        "commander_query": "is:commander t:pirate f:commander",
    })
    ladder_casual, _ = app.commander_ladder({
        "theme": "pirate", "card_pool": app.CARD_POOL_CASUAL,
        "commander_query": "is:commander t:pirate f:commander",
    })
    (ok if all("f:commander" in q for q in ladder_legal) else bad)(
        "tournament ladder has f:commander", " | ".join(ladder_legal[:3]))
    (ok if all("f:commander" not in q for q in ladder_casual) else bad)(
        "casual ladder strips f:commander", " | ".join(ladder_casual[:3]))

    eq("default pool is tournament legal", app.parse_card_pool({}), app.CARD_POOL_TOURNAMENT)
    eq("house_rules maps to casual", app.parse_card_pool({"house_rules": True}),
       app.CARD_POOL_CASUAL)

    cmdr = _card("Test Commander", typ="Legendary Creature — Human", ci=["R", "G", "U"])
    basics = ([_card("Island", typ="Basic Land — Island", ci=["U"], role="basic") for _ in range(33)]
              + [_card("Mountain", typ="Basic Land — Mountain", ci=["R"], role="basic") for _ in range(33)])
    rest = [_card(f"Piece {i}", typ="Instant", text="Draw.", ci=["U"], role="draw")
            for i in range(33)]
    deck = basics + rest
    eq("fixture is 99", len(deck), 99)
    deck[40] = _card("Dockside Extortionist", typ="Creature — Human Pirate",
                     text="When this creature enters, create a Treasure token.",
                     ci=["R", "G", "U"], legal="banned", role="ramp")

    r_legal = app.validate_deck(cmdr, deck, card_pool=app.CARD_POOL_TOURNAMENT)
    eq("dockside fails tournament legal", r_legal["ok"], False)
    eq("dockside not buyable tournament legal", r_legal["buyable"], False)
    (ok if "Dockside Extortionist" in r_legal["banned"] else bad)(
        "dockside in banned list (tournament)", str(r_legal["banned"]))

    r_casual = app.validate_deck(cmdr, deck, card_pool=app.CARD_POOL_CASUAL)
    eq("dockside ok in casual", r_casual["ok"], True)
    eq("dockside buyable in casual", r_casual["buyable"], True)
    (ok if "Dockside Extortionist" in r_casual["banned"] else bad)(
        "dockside in banned list (casual)", str(r_casual["banned"]))


def test_persist_deck():
    """unique save ids; vocab.json never listed; no silent overwrite."""
    print("\npersist_deck()  — saved decks")
    import tempfile
    from pathlib import Path
    from datetime import datetime, timezone

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "vocab.json").write_text('{"type":{},"keyword":{}}')
        when = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        slug_a = app.deck_save_slug("Yuriko, the Tiger's Shadow", when)
        slug_b = app.deck_save_slug("Yuriko, the Tiger's Shadow", when)
        # Same second still unique via microseconds in live path; fixed when → same
        # stamp, so live persist_deck uses now() each call. Two writes → two files.
        (ok if re.fullmatch(r"Yuriko_\d{8}T\d{6}\d+Z", slug_a) else bad)(
            "slug is filesystem-safe", slug_a)
        (ok if "'" not in slug_a and " " not in slug_a and "," not in slug_a else bad)(
            "slug strips awkward chars", slug_a)

        out1 = {"commander": "Yuriko, the Tiger's Shadow", "deck": [{"name": "A"}],
                "price": 12.5, "buyable": True, "card_pool": "tournament_legal"}
        out2 = {"commander": "Yuriko, the Tiger's Shadow", "deck": [{"name": "B"}],
                "price": 99.0, "buyable": True, "card_pool": "tournament_legal"}
        p1 = app.persist_deck(out1, d)
        p2 = app.persist_deck(out2, d)
        eq("two saves two files", p1 == p2, False)
        eq("both exist", p1.exists() and p2.exists(), True)
        eq("ids recorded", out1["id"] != out2["id"], True)

        listed = app.list_saved_decks(d)
        eq("vocab.json excluded", any(i["id"] == "vocab" for i in listed), False)
        eq("two decks listed", len(listed), 2)
        eq("newest first", listed[0]["id"], out2["id"])
        eq("commander preserved", listed[0]["commander"], "Yuriko, the Tiger's Shadow")
        # unnamed builds are auto-saved playtest rows until she names them.
        eq("unnamed build is auto_saved", listed[0]["auto_saved"], True)
        out2["name"] = "Night raids"
        app.write_saved_deck(out2["id"], out2, d)
        named = app.list_saved_decks(d)
        row = next(i for i in named if i["id"] == out2["id"])
        eq("christened name clears auto_saved", row["auto_saved"], False)
        out1["favorite"] = True
        app.write_saved_deck(out1["id"], out1, d)
        fav = next(i for i in app.list_saved_decks(d) if i["id"] == out1["id"])
        eq("favorite is not auto_saved even if unnamed", fav["auto_saved"], False)

        # Her wish and the way she picked are what tell two builds of the same
        # commander apart in the list. They are read from the brief and never
        # substituted: a deck saved before the brief was kept shows neither
        # rather than borrowing the theme or the commander's name.
        eq("no brief means no wish", app._saved_wish_and_way({}),
           {"wish": "", "way": ""})
        eq("null brief means no wish", app._saved_wish_and_way({"brief": None}),
           {"wish": "", "way": ""})
        eq("malformed brief means no wish",
           app._saved_wish_and_way({"brief": "cavalry"}), {"wish": "", "way": ""})
        eq("brief theme never falls back to commander",
           app._saved_wish_and_way(
               {"commander": "Adeline, Resplendent Cathar", "brief": {}}),
           {"wish": "", "way": ""})
        eq("wish and way come from the brief, trimmed",
           app._saved_wish_and_way(
               {"brief": {"theme": "  cavalry knights ", "way_to_play": "counters"}}),
           {"wish": "cavalry knights", "way": "counters"})
        eq("a wish without a way still shows the wish",
           app._saved_wish_and_way({"brief": {"theme": "angels"}}),
           {"wish": "angels", "way": ""})


async def test_suggested_name_never_overwrites_hers():
    """The suggester may name an auto-save and may never touch one she named.

    Every refusal here is the feature's whole safety argument, so each one is
    asserted rather than assumed. Refusing returns applied: False, not an
    error — a deck she already named is a normal outcome, not a failure.
    """
    print("\nsuggest-name  — the app may name its own auto-saves, and only those")
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        was, app.SAVE_DIR = app.SAVE_DIR, d
        try:
            def mk(slug, **kw):
                rec = {"commander": "Adeline, Resplendent Cathar",
                       "deck": [{"name": "A"}], "price": 1.0, "id": slug}
                rec.update(kw)
                (d / f"{slug}.json").write_text(json.dumps(rec))
                return rec

            def stored(slug):
                return json.loads((d / f"{slug}.json").read_text())

            mk("a")
            r = await app.suggest_saved_name("a", {"name": "Dawn of the Charge"})
            eq("unnamed auto-save takes the suggestion", r.get("applied"), True)
            eq("and the name is written", stored("a")["name"], "Dawn of the Charge")
            eq("marked as ours", stored("a")["named_by"], "auto")

            # The playtest sort fix's sort must survive the name — otherwise every playtest
            # starts looking like a deck she kept.
            eq("an auto-named deck is still an auto-save",
               app.is_auto_saved_deck(stored("a")), True)
            eq("starring it still means she kept it",
               app.is_auto_saved_deck({**stored("a"), "favorite": True}), False)

            # Re-suggesting must not churn the stored name: the saved list
            # would reorder and rename under her while she is reading it.
            r = await app.suggest_saved_name("a", {"name": "Something Else"})
            eq("a second suggestion is refused", r.get("applied"), False)
            eq("and the first name stands", stored("a")["name"], "Dawn of the Charge")

            mk("b", name="Her Own Name", named_by="her")
            r = await app.suggest_saved_name("b", {"name": "Ours"})
            eq("a name she chose is refused", r.get("applied"), False)
            eq("refusal says why", r.get("reason"), "she named it")
            eq("hers is untouched", stored("b")["name"], "Her Own Name")

            # She cleared the box back to the commander. That is still her
            # decision, and the provenance is what records it.
            mk("c", name="Adeline, Resplendent Cathar", named_by="her")
            r = await app.suggest_saved_name("c", {"name": "Ours"})
            eq("cleared-by-her is still hers", r.get("applied"), False)

            # Saved before named_by existed: no provenance, but a name that is
            # not the commander can only have come from her.
            mk("e", name="Old Favourite")
            r = await app.suggest_saved_name("e", {"name": "Ours"})
            eq("a legacy name is refused", r.get("applied"), False)
            eq("legacy name is untouched", stored("e")["name"], "Old Favourite")

            mk("f")
            await app.rename_saved("f", {"name": "Mine"})
            eq("her PATCH marks the deck hers", stored("f")["named_by"], "her")
            r = await app.suggest_saved_name("f", {"name": "Ours"})
            eq("and locks the suggester out", r.get("applied"), False)

            eq("a blank suggestion is rejected",
               (await app.suggest_saved_name("a", {"name": "  "})).status_code, 400)
            eq("a missing deck is 404",
               (await app.suggest_saved_name("nope", {"name": "x"})).status_code, 404)
        finally:
            app.SAVE_DIR = was


def test_saved_deck_brief_name_delete():
    """brief travels with deck; player name; delete."""
    print("\nsaved deck API  — brief, name, delete")
    import tempfile
    from pathlib import Path

    brief = {"theme": "pirate", "angle": "treasure tokens", "budget_total": 150,
             "bracket": 2, "card_pool": "tournament_legal"}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {"commander": "Admiral Beckett Brass", "deck": [{"name": "Sol Ring"}],
               "price": 80.0, "buyable": True, "card_pool": "tournament_legal",
               "brief": brief}
        app.persist_deck(out, d)
        slug = out["id"]

        loaded = app.load_saved_deck(slug, d)
        eq("brief round-trips", loaded.get("brief"), brief)
        eq("default name is commander", out["name"], "Admiral Beckett Brass")

        listed = app.list_saved_decks(d)
        eq("list includes name", listed[0]["name"], "Admiral Beckett Brass")
        eq("list flags brief", listed[0]["has_brief"], True)

        loaded["name"] = "Brass Heist"
        app.write_saved_deck(slug, loaded, d)
        listed2 = app.list_saved_decks(d)
        eq("rename visible in list", listed2[0]["name"], "Brass Heist")

        eq("delete removes file", app.delete_saved_deck(slug, d), True)
        eq("gone after delete", app.load_saved_deck(slug, d), None)
        eq("bad slug rejected", app.delete_saved_deck("../etc/passwd", d), False)
        eq("vocab slug rejected", app.delete_saved_deck("vocab", d), False)


def test_build_returns_plan():
    """the plan that chose the cards rides on the build and saves.

    /api/build already calls plan_graph.derive() for payoff_searches(); this
    asserts the result is not discarded. decided:false must survive — a guess
    presented as a decision is the bug the archetype layer exists to prevent.
    """
    print("\nbuild returns plan")
    import tempfile
    from pathlib import Path
    from deckwright import plan_graph

    brief = {"theme": "lifegain", "strategy": "a lifegain deck",
             "mechanics": ["lifegain", "drain"], "colors": "WB"}
    vito = {"name": "Vito, Thorn of the Dusk Rose", "cost": "{3}{B}", "cmc": 4,
            "type": "Legendary Creature — Vampire Cleric",
            "text": "Whenever you gain life, target opponent loses that much life."}
    plan = plan_graph.derive(brief, vito)

    (ok if isinstance(plan.get("archetype"), dict) else bad)(
        "plan.archetype present", str(plan.get("archetype")))
    (ok if isinstance(plan.get("conditions"), list) else bad)(
        "plan.conditions present", str(type(plan.get("conditions"))))
    (ok if isinstance(plan.get("commitments"), list) else bad)(
        "plan.commitments present", str(type(plan.get("commitments"))))
    (ok if plan["archetype"].get("decided") is False else bad)(
        "decided:false preserved (brief did not choose)",
        str(plan["archetype"]))
    eq("sim_version matches live engine",
       (plan.get("derived_from") or {}).get("sim_version"), sim.SIM_VERSION)
    (ok if "coherence" not in plan else bad)(
        "no coherence score", str(plan.keys()))

    # /api/build must put the plan it derives onto the response.
    build_src = Path(app.__file__).read_text()
    build_fn = build_src.split("async def build(", 1)[-1].split(
        "\nasync def ", 1)[0]
    (ok if "plan_for_build" in build_fn and (
        '"plan": plan_for_build' in build_fn
        or "out[\"plan\"]" in build_fn
        or "out['plan']" in build_fn) else bad)(
        "build response includes plan_for_build", "")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {"commander": vito["name"], "deck": [{"name": "Sol Ring"}],
               "price": 1.0, "buyable": True, "card_pool": "tournament_legal",
               "brief": brief, "plan": plan}
        app.persist_deck(out, d)
        loaded = app.load_saved_deck(out["id"], d)
        eq("plan round-trips", loaded.get("plan"), plan)
        exposed = app.expose_saved_deck(loaded)
        eq("expose keeps plan", exposed.get("plan"), plan)
        eq("decided survives save",
           exposed["plan"]["archetype"]["decided"], False)
        eq("sim_version survives save",
           exposed["plan"]["derived_from"]["sim_version"], sim.SIM_VERSION)


def test_conversation_on_saved_deck():
    """interrogation transcript rides with the deck, verbatim."""
    print("\nconversation on saved deck")
    import tempfile
    from pathlib import Path

    long = "I want pirates who steal treasure and make people mad. " * 20
    eq("long answer is kept whole", len(long) > 400, True)

    turns = [
        {"role": "user", "content": "I want a pirate deck"},
        {"role": "assistant", "content": "What matters more — the crew, or the treasure?"},
        {"role": "user", "content": long},
        {"role": "assistant", "content": "Got it. Bracket and budget?"},
        {"role": "user", "content": "Bracket 2, around $100"},
    ]

    # Accept + keep both sides; her words unchanged; no length cut.
    kept = app.conversation_from_payload({"conversation": turns})
    eq("keeps every turn", kept, turns)
    eq("her long answer unchanged", kept[2]["content"], long)
    eq("assistant turns kept", [t["role"] for t in kept],
       ["user", "assistant", "user", "assistant", "user"])

    # Missing / empty / junk → None. Precon and paste have no conversation.
    eq("missing is None", app.conversation_from_payload({}), None)
    eq("empty list is None", app.conversation_from_payload({"conversation": []}), None)
    eq("non-list is None", app.conversation_from_payload({"conversation": "hi"}), None)
    # Malformed rows drop; well-formed ones stay. Never invent text.
    mixed = app.conversation_from_payload({"conversation": [
        {"role": "user", "content": "keep me"},
        {"role": "robot", "content": "bad role"},
        {"content": "no role"},
        "not a dict",
        {"role": "assistant", "content": 12},
        {"role": "assistant", "content": "keep me too"},
    ]})
    eq("malformed dropped, rest kept", mixed, [
        {"role": "user", "content": "keep me"},
        {"role": "assistant", "content": "keep me too"},
    ])
    eq("all-bad is None", app.conversation_from_payload(
        {"conversation": [{"role": "x", "content": "y"}]}), None)

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {"commander": "Admiral Beckett Brass", "deck": [{"name": "Sol Ring"}],
               "price": 80.0, "buyable": True, "card_pool": "tournament_legal",
               "brief": {"theme": "pirate", "angle": "treasure tokens"},
               "conversation": list(turns)}
        app.persist_deck(out, d)
        loaded = app.load_saved_deck(out["id"], d)
        eq("conversation round-trips", loaded.get("conversation"), turns)
        eq("her words still hers", loaded["conversation"][2]["content"], long)
        exposed = app.expose_saved_deck(loaded)
        eq("expose keeps conversation", exposed.get("conversation"), turns)

        # Rename / primer patch must not wipe the transcript.
        loaded["name"] = "Brass Heist"
        app.write_saved_deck(out["id"], loaded, d)
        again = app.load_saved_deck(out["id"], d)
        eq("rename leaves conversation", again.get("conversation"), turns)

        # No conversation on a precon-shaped save — field absent, not empty.
        bare = {"commander": "Atraxa, Praetors' Voice", "deck": [{"name": "Sol Ring"}],
                "price": 40.0, "buyable": True, "card_pool": "tournament_legal"}
        app.persist_deck(bare, d)
        eq("precon has no conversation key",
           "conversation" in app.load_saved_deck(bare["id"], d), False)


def test_match_notes_on_saved_deck():
    """post-game notes persist on a saved deck. No model.

    Backend + thin UI already shipped earlier. This gate keeps the
    HTTP path honest: add / list / delete round-trip through disk, missing
    deck and missing note are 404s, and a rename does not wipe notes.
    """
    print("\nmatch notes on saved deck")
    import tempfile
    from pathlib import Path
    from fastapi.testclient import TestClient
    from deckwright import deck_state

    data = {"deck": [], "commander": "X"}
    note = deck_state.add_match_note(data, {
        "text": "Lost to Thoracle",
        "opponent_plan": "combo",
        "result": "loss",
        "tags": ["combo", "", "  interaction  "] + [f"t{i}" for i in range(10)],
    })
    eq("note id set", bool(note.get("id")), True)
    eq("note text kept", note.get("text"), "Lost to Thoracle")
    eq("tags trimmed and capped at 8", note.get("tags"),
       ["combo", "interaction"] + [f"t{i}" for i in range(6)])
    eq("one note listed", len(deck_state.list_match_notes(data)), 1)
    eq("delete removes", deck_state.delete_match_note(data, note["id"]), True)
    eq("empty after delete", deck_state.list_match_notes(data), [])
    eq("delete missing is false",
       deck_state.delete_match_note(data, "nope"), False)
    eq("malformed match_notes reads as empty",
       deck_state.list_match_notes({"match_notes": "oops"}), [])

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        old = app.SAVE_DIR
        app.SAVE_DIR = d
        try:
            out = {
                "commander": "Admiral Beckett Brass",
                "deck": [{"name": "Sol Ring"}],
                "price": 80.0,
                "buyable": True,
                "card_pool": "tournament_legal",
                "brief": {"theme": "pirate"},
            }
            app.persist_deck(out, d)
            slug = out["id"]
            client = TestClient(app.app)

            r = client.get(f"/api/saved/{slug}/notes")
            eq("GET notes empty 200", r.status_code, 200)
            eq("GET notes empty list", r.json().get("match_notes"), [])

            r = client.post(f"/api/saved/{slug}/notes", json={
                "text": "Mana screwed turn 3",
                "opponent_plan": "aggro",
                "result": "loss",
                "tags": ["mana", "interaction"],
            })
            eq("POST note 200", r.status_code, 200)
            body = r.json()
            eq("POST ok", body.get("ok"), True)
            note_id = (body.get("note") or {}).get("id")
            eq("POST returns note id", bool(note_id), True)
            eq("POST returns list of one", len(body.get("match_notes") or []), 1)

            on_disk = app.load_saved_deck(slug, d)
            eq("note persisted to disk",
               len(on_disk.get("match_notes") or []), 1)
            eq("disk text", on_disk["match_notes"][0].get("text"),
               "Mana screwed turn 3")

            loaded = client.get(f"/api/saved/{slug}")
            eq("GET deck 200", loaded.status_code, 200)
            eq("deck payload carries match_notes",
               len(loaded.json().get("match_notes") or []), 1)

            # Rename must not wipe notes (same path as conversation).
            r = client.patch(f"/api/saved/{slug}", json={"name": "Brass Heist"})
            eq("rename 200", r.status_code, 200)
            again = app.load_saved_deck(slug, d)
            eq("rename leaves match_notes",
               len(again.get("match_notes") or []), 1)
            eq("rename applied", again.get("name"), "Brass Heist")

            r = client.get(f"/api/saved/{slug}/notes")
            eq("GET notes one", len(r.json().get("match_notes") or []), 1)

            r = client.delete(f"/api/saved/{slug}/notes/{note_id}")
            eq("DELETE note 200", r.status_code, 200)
            eq("DELETE clears list", r.json().get("match_notes"), [])
            eq("disk empty after delete",
               app.load_saved_deck(slug, d).get("match_notes"), [])

            r = client.get("/api/saved/no_such_deck/notes")
            eq("GET missing deck 404", r.status_code, 404)
            r = client.post("/api/saved/no_such_deck/notes",
                            json={"text": "x"})
            eq("POST missing deck 404", r.status_code, 404)
            r = client.delete(f"/api/saved/{slug}/notes/missing-id")
            eq("DELETE missing note 404", r.status_code, 404)
        finally:
            app.SAVE_DIR = old


def test_sim_baseline_cache():
    """baseline sim rides with the saved deck; stamps invalidate it."""
    print("\nsim baseline cache")
    import tempfile
    from pathlib import Path

    deck_a = [{"name": "Sol Ring"}, {"name": "Island"}, {"name": "Island"}]
    deck_b = [{"name": "Sol Ring"}, {"name": "Island"}, {"name": "Swamp"}]
    eq("hash stable", bake.deck_list_hash(deck_a), bake.deck_list_hash(list(deck_a)))
    eq("basics counted", bake.deck_list_hash(deck_a) != bake.deck_list_hash(
        [{"name": "Sol Ring"}, {"name": "Island"}]), True)
    eq("swap changes hash", bake.deck_list_hash(deck_a) != bake.deck_list_hash(deck_b), True)

    thesis = {"payoff_cards": ["Sol Ring"],
              "conditions": [{"label": "mana", "note": "have mana",
                              "check": {"mana_available": 3}}]}
    sim_out = {
        "runs": 3000,
        "curves": [{"label": "mana", "note": "have mana",
                    "points": [10, 20, 40, 60, 70, 80, 85, 90, 92, 95],
                    "median_turn": 4}],
        "diagnostics": {"lands": 2, "mana_rocks": 1, "land_ramp": 0,
                        "extra_drops": 0, "payoffs": 1, "avg_cmc": 1.0},
        "assumptions": sim.ASSUMPTIONS,
        "good_hand": [{"name": "Sol Ring", "drawn_turn": 0}],
        "bad_hand": [{"name": "Island", "drawn_turn": 0}],
        "review": {"sound": True, "caveat": ""},
    }

    # bake.fresh is the shared gate the precon bake will reuse.
    stamped = bake.stamp({"curves": sim_out["curves"]}, deck_a, 1)
    eq("fresh when stamps match", bake.fresh(stamped, deck_a, 1), True)
    eq("stale on hash mismatch", bake.fresh(stamped, deck_b, 1), False)
    eq("stale on version bump", bake.fresh(stamped, deck_a, 2), False)
    eq("stale when missing", bake.fresh(None, deck_a, 1), False)

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {"commander": "Malcolm, Keen-Eyed Navigator", "deck": list(deck_a),
               "price": 40.0, "buyable": True, "card_pool": "tournament_legal",
               "brief": {"theme": "pirates"}}
        app.persist_deck(out, d)
        slug = out["id"]

        stored = app.store_sim_baseline(slug, thesis, sim_out, d)
        eq("store returns exposed deck", bool(stored and stored.get("simulation")), True)
        block = stored["simulation"]
        eq("thesis stored", block["thesis"], thesis)
        eq("curves stored", block["curves"], sim_out["curves"])
        eq("diagnostics stored", block["diagnostics"], sim_out["diagnostics"])
        eq("good hand stored", block.get("good_hand"), sim_out["good_hand"])
        eq("bad hand stored", block.get("bad_hand"), sim_out["bad_hand"])
        eq("review stored", block.get("review"), sim_out["review"])
        eq("sim_version stamped", block.get("sim_version"), sim.SIM_VERSION)
        eq("deck_hash stamped", block.get("deck_hash"), bake.deck_list_hash(deck_a))

        loaded = app.load_saved_deck(slug, d)
        exposed = app.expose_saved_deck(loaded)
        eq("load exposes fresh baseline", bool(exposed.get("simulation")), True)
        eq("baseline round-trips", exposed["simulation"]["curves"], sim_out["curves"])

        # Card swap → stale → omitted from the client payload.
        loaded["deck"] = list(deck_b)
        app.write_saved_deck(slug, loaded, d)
        swapped = app.expose_saved_deck(app.load_saved_deck(slug, d))
        eq("swap drops simulation", "simulation" in swapped, False)
        # File still holds the stale block — only the client view strips it.
        on_disk = app.load_saved_deck(slug, d)
        eq("stale block remains on disk", "simulation" in on_disk, True)
        eq("fresh() rejects swapped deck", app.sim_baseline_fresh(on_disk), False)

        # Version bump → stale even with the matching list.
        on_disk["deck"] = list(deck_a)
        on_disk["simulation"]["deck_hash"] = bake.deck_list_hash(deck_a)
        on_disk["simulation"]["sim_version"] = 0  # older than sim.SIM_VERSION
        app.write_saved_deck(slug, on_disk, d)
        old_engine = app.expose_saved_deck(app.load_saved_deck(slug, d))
        eq("old sim_version drops simulation", "simulation" in old_engine, False)

        # Re-store after a swap writes a fresh baseline for the new list.
        loaded = app.load_saved_deck(slug, d)
        loaded["deck"] = list(deck_b)
        app.write_saved_deck(slug, loaded, d)
        again = app.store_sim_baseline(slug, thesis, sim_out, d)
        eq("re-store after swap is fresh",
           again["simulation"]["deck_hash"], bake.deck_list_hash(deck_b))
        eq("re-store exposes baseline", bool(again.get("simulation")), True)

        # Missing slug is a no-op, not a crash.
        eq("missing slug returns None",
           app.store_sim_baseline("no_such_deck", thesis, sim_out, d), None)


def test_finish_mana_base():
    """total lands is decided; flex absorbs role-target changes."""
    print("\nfinish_mana_base()  — total lands not residue")

    def skeleton(n_spells, n_nonbasic=36):
        d = [_card(f"L{i}", typ="Land", role="lands", text="{T}: Add {U}.")
             for i in range(n_nonbasic)]
        d += [_card(f"S{i}", typ="Instant", role="theme", text="Draw a card.")
              for i in range(n_spells)]
        return d

    island = _card("Island", typ="Basic Land — Island", text="", role="basic")
    flex = [_card(f"Flex {i}", typ="Creature", role="theme", text="Flying")
            for i in range(20)]

    # Full skeleton: 36 nonbasic + 59 spells = 95 → 2 basics + 2 flex = 99, 38 lands.
    deck = skeleton(59)
    r = app.finish_mana_base(deck, flex, [island], total_lands=38, min_basics=2)
    eq("deck is 99", len(deck), 99)
    eq("total lands hits target", r["total_lands"], 38)
    eq("basics make up the gap", r["basics_added"], 2)
    eq("flex absorbed remainder", r["flex_added"], 2)
    (ok if "target 38" in r["basis"] else bad)("basis states target", r["basis"])

    # Drop two spell slots (as if wipe target shrank): lands must stay 38.
    deck2 = skeleton(57)
    r2 = app.finish_mana_base(deck2, flex, [island], total_lands=38, min_basics=2)
    eq("shorter spell mix still 99", len(deck2), 99)
    eq("lands unchanged when spells shrink", r2["total_lands"], 38)
    eq("basics still 2", r2["basics_added"], 2)
    eq("flex grew instead of basics", r2["flex_added"], 4)

    # Add two spell slots: lands still 38; fewer flex.
    deck3 = skeleton(61)
    r3 = app.finish_mana_base(deck3, flex, [island], total_lands=38, min_basics=2)
    eq("longer spell mix still 99", len(deck3), 99)
    eq("lands unchanged when spells grow", r3["total_lands"], 38)
    eq("basics still 2 with more spells", r3["basics_added"], 2)
    eq("flex shrank", r3["flex_added"], 0)


def test_no_land_padding():
    """short roles must not push total_lands above the plan target.

    Reproduces the finish_mana_base table from the issue: with a 36-land target
    and a thin/empty flex pool, the old basics loop padded past the target.
    Surplus from other role pools fills first; if still short, the deck stays
    under 99 with lands at the planned count — never padded basics.
    """
    print("\nfinish_mana_base()  — no pad past land target")

    def skeleton(n_spells, n_nonbasic=36):
        d = [_card(f"L{i}", typ="Land", role="lands", text="{T}: Add {U}.")
             for i in range(n_nonbasic)]
        d += [_card(f"S{i}", typ="Instant", role="theme", text="Draw a card.")
              for i in range(n_spells)]
        return d

    island = _card("Island", typ="Basic Land — Island", text="", role="basic")
    target = 36

    # Issue table row: flex 10 → used to finish at 59 lands / 23 padded.
    deck = skeleton(30)
    flex = [_card(f"Flex {i}", typ="Creature", role="theme", text="Flying")
            for i in range(10)]
    r = app.finish_mana_base(deck, flex, [island], total_lands=target, min_basics=0)
    eq("thin flex: total_lands stays at target", r["total_lands"], target)
    eq("thin flex: no padded basics", r["padded_basics"], 0)
    (ok if len(deck) < 99 else bad)(
        "thin flex: incomplete deck rather than land-pad", f"len={len(deck)}")
    eq("thin flex: shortfall reported", r.get("shortfall"), 99 - len(deck))

    # Issue table row: flex 0 → used to finish at 69 lands / 33 padded.
    deck0 = skeleton(30)
    r0 = app.finish_mana_base(deck0, [], [island], total_lands=target, min_basics=0)
    eq("empty flex: total_lands stays at target", r0["total_lands"], target)
    eq("empty flex: no padded basics", r0["padded_basics"], 0)
    (ok if len(deck0) < 99 else bad)(
        "empty flex: incomplete rather than 69 lands", f"len={len(deck0)}")

    # Preference 1: borrow unused ramp candidates before giving up.
    deck_s = skeleton(30)
    flex_s = [_card(f"Flex {i}", typ="Creature", role="theme", text="Flying")
              for i in range(5)]
    surplus = [_card(f"Ramp leftover {i}", typ="Artifact", role="ramp",
                     text="{T}: Add {C}.")
               for i in range(40)]
    # Attach edhrec ranks so surplus_from_pools ordering is deterministic.
    for i, c in enumerate(surplus):
        c["edhrec_rank"] = 1000 + i
    r_s = app.finish_mana_base(
        deck_s, flex_s, [island], total_lands=target, min_basics=0,
        surplus_pool=surplus)
    eq("surplus fills to 99", len(deck_s), 99)
    eq("surplus: lands stay at target", r_s["total_lands"], target)
    eq("surplus: no padded basics", r_s["padded_basics"], 0)
    (ok if r_s.get("surplus_added", 0) > 0 else bad)(
        "surplus_added counts borrowed cards", r_s.get("surplus_added"))

    # surplus_from_pools: unused non-lands from role_pools, lands role skipped.
    deck_names = {"Sol Ring", "Island"}
    pools = {
        "ramp": [
            {"name": "Sol Ring", "type": "Artifact", "edhrec_rank": 1},
            {"name": "Arcane Signet", "type": "Artifact", "edhrec_rank": 5},
            {"name": "Command Tower", "type": "Land", "edhrec_rank": 2},
        ],
        "lands": [
            {"name": "Reliquary Tower", "type": "Land", "edhrec_rank": 10},
        ],
        "theme": [
            {"name": "Arcane Signet", "type": "Artifact", "edhrec_rank": 5},
            {"name": "Brainstorm", "type": "Instant", "edhrec_rank": 20},
        ],
    }
    got = app.surplus_from_pools(pools, deck_names)
    names = [c["name"] for c in got]
    eq("surplus skips cards already in deck", "Sol Ring" in names, False)
    eq("surplus skips land-type cards", "Command Tower" in names, False)
    eq("surplus skips lands role entirely", "Reliquary Tower" in names, False)
    eq("surplus dedupes across roles", names.count("Arcane Signet"), 1)
    eq("surplus keeps non-land leftovers", "Brainstorm" in names, True)
    eq("surplus ordered by edhrec_rank", names[0], "Arcane Signet")


def _finish_like_build(spells, cmdr, identity):
    """Spell roles → mana.plan → nonbasics → finish_mana_base → build basis string.

    Mirrors /api/build's land-sizing path without Scryfall or the model.
    """
    plan = mana.plan(spells, cmdr, identity)
    deck = list(spells)
    for i in range(plan["nonbasic_lands"]):
        deck.append({
            "name": f"NB {identity[0]}{i}", "type": "Land", "cmc": 0, "cost": "",
            "text": f"{{T}}: Add {{{identity[0]}}}.",
            "produced_mana": [identity[0]], "role": "lands",
        })
    basic_name = {"W": "Plains", "U": "Island", "B": "Swamp",
                  "R": "Mountain", "G": "Forest"}[identity[0]]
    basic = {
        "name": basic_name, "type": f"Basic Land — {basic_name}", "cmc": 0,
        "cost": "", "text": "", "produced_mana": [identity[0]], "role": "basic",
    }
    flex = [{"name": f"Flex {identity[0]}{i}", "type": "Creature", "cmc": 2,
             "cost": f"{{1}}{{{identity[0]}}}", "text": "Flying", "role": "theme"}
            for i in range(40)]
    fill = app.finish_mana_base(deck, flex, [basic], total_lands=plan["total_lands"])
    # Same composition /api/build writes onto lands.basis.
    basis = (f"{plan['basis']}; finished {fill['total_lands']} lands "
             f"({fill['nonbasic_lands']} nonbasic + {fill['basics_added']} basic)")
    fill["basis"] = basis
    fill["plan_total"] = plan["total_lands"]
    fill["deck"] = deck
    return fill


def test_mana_sizing():
    """land count follows curve; under-sourced bases are reported."""
    print("\nmana.plan()  — curve sizing and source verdict")

    def low_curve_spells():
        return [{"name": f"C{i}", "type": "Creature", "cmc": 2,
                 "cost": "{1}{U}", "text": "Draw.", "role": "theme"}
                for i in range(40)]

    def high_curve_spells():
        return [{"name": f"H{i}", "type": "Creature", "cmc": 5,
                 "cost": "{3}{U}{U}", "text": "Big.", "role": "theme"}
                for i in range(40)]

    cmdr_low = {"name": "Low", "type": "Legendary Creature", "cmc": 2,
                "cost": "{1}{U}", "color_identity": ["U"]}
    cmdr_high = {"name": "High", "type": "Legendary Creature", "cmc": 5,
                 "cost": "{3}{U}{U}", "color_identity": ["U"]}

    low_plan = mana.plan(low_curve_spells(), cmdr_low, ["U"])
    high_plan = mana.plan(high_curve_spells(), cmdr_high, ["U"])
    (ok if low_plan["total_lands"] < high_plan["total_lands"] else bad)(
        "low curve fewer lands than high curve",
        f"{low_plan['total_lands']} vs {high_plan['total_lands']}")
    (ok if "Karsten" in low_plan["basis"] else bad)("basis cites Karsten", low_plan["basis"])

    # done criteria (post empty-role fix): on two different curves,
    # total_lands == leading number in basis == land-type cards in the 99.
    # The 66-land bug was padding after empty roles; that path must not fire here.
    low_fill = _finish_like_build(low_curve_spells(), cmdr_low, ["U"])
    high_fill = _finish_like_build(high_curve_spells(), cmdr_high, ["U"])
    (ok if low_fill["total_lands"] < high_fill["total_lands"] else bad)(
        "finished low curve fewer lands than high",
        f"{low_fill['total_lands']} vs {high_fill['total_lands']}")

    for label, fill in (("low", low_fill), ("high", high_fill)):
        deck = fill["deck"]
        actual = sum(1 for c in deck if app.is_land_card(c))
        m_lead = re.match(r"(\d+) lands", fill["basis"])
        m_fin = re.search(r"finished (\d+) lands", fill["basis"])
        leading = int(m_lead.group(1)) if m_lead else None
        finished = int(m_fin.group(1)) if m_fin else None
        eq(f"{label} deck is 99", len(deck), 99)
        eq(f"{label} no padded basics", fill["padded_basics"], 0)
        eq(f"{label} total_lands == plan target", fill["total_lands"], fill["plan_total"])
        eq(f"{label} total_lands == basis leading", fill["total_lands"], leading)
        eq(f"{label} total_lands == basis finished", fill["total_lands"], finished)
        eq(f"{label} total_lands == land-type count", fill["total_lands"], actual)

    # White-only lands cannot cast UB — fixing rocks list all five in produced_mana
    # but UB commander identity should not count phantom colors.
    def _fixing_rocks():
        return [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                 "text": "{T}: Add one mana of any color.",
                 "produced_mana": ["W", "U", "B", "R", "G"], "cost": "{2}"}
                for i in range(10)]

    white_lands = [{"name": f"Pl{i}", "type": "Land", "cmc": 0,
                    "text": "{T}: Add {W}.", "produced_mana": ["W"], "cost": ""}
                   for i in range(36)]
    ub_spells = [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                  "text": "Draw.", "cost": "{1}{U}{B}", "role": "theme"}
                 for i in range(40)]
    cmdr_ub = {"name": "UB", "type": "Legendary Creature", "cmc": 3,
               "cost": "{1}{U}{B}", "color_identity": ["U", "B"]}
    broken = white_lands + _fixing_rocks() + ub_spells
    analysis = mana.analyze_sources(broken, cmdr_ub, ["U", "B"], total_lands=38)
    (ok if not analysis["supported"] else bad)("white-only base flagged under-sourced")
    (ok if analysis["shortages"] else bad)("shortages listed", analysis["shortages"])
    ub_short = [s for s in analysis["shortages"] if s["color"] in ("U", "B")]
    (ok if ub_short else bad)("UB colors in shortage list", ub_short)

    proper_lands = ([{"name": f"Is{i}", "type": "Land", "cmc": 0,
                       "text": "{T}: Add {U}.", "produced_mana": ["U"], "cost": ""}
                     for i in range(18)]
                    + [{"name": f"Sw{i}", "type": "Land", "cmc": 0,
                        "text": "{T}: Add {B}.", "produced_mana": ["B"], "cost": ""}
                       for i in range(18)])
    proper = proper_lands + _fixing_rocks() + ub_spells
    proper_a = mana.analyze_sources(proper, cmdr_ub, ["U", "B"], total_lands=38)
    (ok if proper_a["supported"] or len(proper_a["shortages"]) < len(ub_short)
     else bad)("proper base has fewer shortages than broken",
               f"{len(proper_a['shortages'])} vs {len(ub_short)}")


def test_deck_signals():
    """strong/watch lines are countable facts from the decklist only."""
    print("\ndeck_signals()  — countable only")

    def base_deck():
        # 36 lands + filler creatures; roles layered on below.
        d = [_card(f"L{i}", typ="Land", role="lands", text="{T}: Add {U}.")
             for i in range(36)]
        d += [_card(f"C{i}", typ="Creature", role="theme", text="Flying")
              for i in range(50)]
        return d

    # Empty interaction → watch lines with zeros.
    empty = base_deck() + [_card(f"F{i}", typ="Sorcery", role="theme") for i in range(13)]
    eq("fixture 99", len(empty), 99)
    sig = app.deck_signals(empty)
    watch_text = " ".join(s["text"] for s in sig["watch"])
    (ok if "0 board wipes" in watch_text else bad)("0 wipes watch", watch_text)
    (ok if "0 counterspells" in watch_text else bad)("0 counters watch", watch_text)
    (ok if not any("wipe" in s["text"] and s in sig["strong"] for s in sig["strong"])
     else bad)("wipes not strong when zero", sig["strong"])
    for line in sig["strong"] + sig["watch"]:
        (ok if line.get("counts") else bad)("every line carries counts", str(line))

    # Loaded interaction → strong lines; enchantment gap still a watch.
    # 36 lands + 3 wipe + 7 spot + 9 draw + 10 ramp + 2 counters + 32 theme = 99
    loaded = [_card(f"L{i}", typ="Land", role="lands", text="{T}: Add {U}.")
              for i in range(36)]
    loaded += [_card(f"W{i}", typ="Sorcery", role="wipe",
                     text="Destroy all creatures.") for i in range(3)]
    loaded += [_card(f"S{i}", typ="Instant", role="spot",
                     text="Destroy target creature.") for i in range(7)]
    loaded += [_card(f"D{i}", typ="Instant", role="draw",
                     text="Draw two cards.") for i in range(9)]
    loaded += [_card(f"R{i}", typ="Artifact", role="ramp",
                     text="{T}: Add one mana of any color.") for i in range(10)]
    loaded += [_card("Counterspell", typ="Instant", role="theme",
                     text="Counter target spell.")]
    loaded += [_card("Negate", typ="Instant", role="theme",
                     text="Counter target noncreature spell.")]
    loaded += [_card(f"T{i}", typ="Creature", role="theme", text="Flying")
               for i in range(32)]
    eq("loaded fixture 99", len(loaded), 99)
    sig = app.deck_signals(loaded)
    eq("counts.wipes", sig["counts"]["wipes"], 3)
    eq("counts.spot", sig["counts"]["spot"], 7)
    eq("counts.draw", sig["counts"]["draw"], 9)
    eq("counts.counters", sig["counts"]["counters"], 2)
    strong_text = " ".join(s["text"] for s in sig["strong"])
    (ok if "3 board wipes" in strong_text else bad)("wipes strong", strong_text)
    (ok if "7 spot removal" in strong_text else bad)("spot strong", strong_text)
    (ok if "2 counterspells" in strong_text else bad)("counters strong", strong_text)
    (ok if "9 draw sources" in strong_text else bad)("draw strong", strong_text)
    watch_text = " ".join(s["text"] for s in sig["watch"])
    (ok if "enchantment" in watch_text.lower() else bad)(
        "enchantment gap watched", watch_text)
    # No matchup language.
    all_text = strong_text + " " + watch_text
    for banned in ("beats", "loses to", "good against", "bad against", "meta"):
        (ok if banned not in all_text.lower() else bad)(
            f"no matchup word '{banned}'", all_text)


def test_overview_verifier():
    """overview card names must come from the deck; combos verified."""
    print("\noverview verifier  — grounded in sim")

    commander = "Thrasios, Triton Hero"
    deck_names = {"Sol Ring", "Lightning Bolt", "Counterspell", "Island"}
    deck = [
        _card("Sol Ring", typ="Artifact", role="ramp",
              text="{T}: Add {C}."),
        _card("Lightning Bolt", typ="Instant", role="spot",
              text="Lightning Bolt deals 3 damage to any target."),
        _card("Counterspell", typ="Instant", role="theme",
              text="Counter target spell."),
        _card("Island", typ="Land", role="lands", text="{T}: Add {U}."),
    ]

    valid = {
        "how_it_works": "Ramp with Sol Ring, then protect with Counterspell.",
        "combos": [{"cards": ["Sol Ring", "Lightning Bolt"],
                    "text": "Sol Ring fuels Lightning Bolt on turn two."}],
        "pros": [{"text": "1 counterspell", "counts": {"counters": 1}}],
        "cons": [{"text": "0 board wipes", "counts": {"wipes": 0}}],
    }
    verified = overview.verify_overview(valid, deck_names, commander)
    named = overview.all_named_cards(verified, deck_names, commander)
    for name in named:
        (ok if name in deck_names or name == commander else bad)(
            f"card '{name}' in deck", name)
    eq("valid combo kept", len(verified["combos"]), 1)

    invalid = {
        "how_it_works": "Ramp with Sol Ring.",
        "combos": [{"cards": ["Black Lotus", "Sol Ring"], "text": "fast mana."}],
        "pros": [], "cons": [],
    }
    verified_bad = overview.verify_overview(invalid, deck_names, commander)
    eq("invented combo dropped", len(verified_bad["combos"]), 0)

    fixture = [_card(f"L{i}", typ="Land", role="lands", text="{T}: Add {G}.")
               for i in range(36)]
    fixture += [_card("Sol Ring", typ="Artifact", role="ramp",
                      text="{T}: Add {C}.")]
    fixture += [_card(f"P{i}", typ="Creature", role="theme", text="Draw a card.")
               for i in range(62)]
    eq("overview fixture 99", len(fixture), 99)
    sim_out = overview.run_build_sim(fixture, 3, runs=500)
    (ok if sim_out.get("diagnostics") else bad)("build sim diagnostics", str(sim_out))
    (ok if len(sim_out.get("curves") or []) == 4 else bad)(
        "build sim four curves", str(len(sim_out.get("curves") or [])))

    signals = app.deck_signals(fixture)
    brief = {"strategy": "grow creatures", "mechanics": ["draw"]}
    cmdr_card = _card(commander, typ="Legendary Creature", text="Draw.")
    facts = overview.facts_block(commander, cmdr_card, fixture, brief, signals,
                                 sim_out, app.for_model)
    (ok if '"signals"' in facts and '"sim_diagnostics"' in facts else bad)(
        "facts block has signals and diagnostics", facts[:120])

    all_text = verified["how_it_works"] + " " + " ".join(
        c["text"] for c in verified["combos"])
    (ok if not overview.MATCHUP_BANNED.search(all_text) else bad)(
        "no matchup language in fixture overview", all_text)

    # countable overview for API-free precon bake.
    counted = overview.from_counts(commander, fixture, signals, sim_out)
    eq("from_counts has strategy", bool(counted.get("strategy")), True)
    eq("from_counts names commander", "Thrasios" in counted["strategy"], True)
    eq("from_counts has how_it_works", bool(counted.get("how_it_works")), True)
    eq("from_counts cites a percent", "%" in counted["how_it_works"], True)
    eq("from_counts no invented combos", len(counted.get("combos") or []), 0)
    eq("from_counts has beats", len(counted.get("beats") or []) >= 1, True)
    for side in ("pros", "cons"):
        for item in counted.get(side) or []:
            (ok if item.get("text") and item.get("counts") else bad)(
                f"from_counts {side} has counts", item)
    grounded = {c["name"] for c in fixture} | {commander}
    for name in overview.all_named_cards(counted, grounded, commander):
        (ok if name in grounded else bad)(
            f"from_counts card '{name}' grounded", name)


def test_substitution():
    """per-card role, replaceability, and validated alternates."""
    print("\nsubstitution  — shopping guidance")

    def pool_card(name, *, cmc=2, ci=None, rank=100):
        return {"name": name, "cost": f"{{{cmc}}}", "cmc": cmc,
                "color_identity": ci or ["U", "B"], "usd": "1.00",
                "edhrec_rank": rank}

    ci = "UB"
    deck = [
        _card("Sol Ring", typ="Artifact", role="ramp", ci=["U", "B"]),
        _card("Lightning Bolt", typ="Instant", role="spot", ci=["R"]),
        _card("Strategy Piece", typ="Creature", role="plan", ci=["U", "B"]),
        _card("Island", typ="Basic Land — Island", role="basic", ci=["U"]),
    ]
    deck_names = {c["name"] for c in deck}
    ramp_pool = [
        pool_card("Sol Ring", cmc=1, rank=10),
        pool_card("Arcane Signet", cmc=2, rank=50),
        pool_card("Talisman of Dominance", cmc=2, rank=80),
        pool_card("Dimir Signet", cmc=2, rank=90),
        pool_card("Mana Crypt", cmc=0, rank=5),  # too far from Sol Ring cmc=1? 0 vs 1 is ok
        pool_card("Cultivate", cmc=3, rank=200),  # cmc 3 vs 1 — excluded
    ]
    spot_pool = [
        pool_card("Lightning Bolt", cmc=1, ci=["R"], rank=20),
        pool_card("Go for the Throat", cmc=2, ci=["B"], rank=30),
        pool_card("Fatal Push", cmc=1, ci=["B"], rank=40),
    ]
    plan_pool = [pool_card("Strategy Piece", cmc=3, rank=500)]
    role_pools = {"ramp": ramp_pool, "spot": spot_pool, "plan": plan_pool,
                  "basic": [], "lands": []}

    subs = substitution.build_substitutions(deck, role_pools, ci)
    eq("one entry per deck card", len(subs), len(deck))

    ramp = subs["Sol Ring"]
    eq("ramp role", ramp["role"], "ramp")
    eq("ramp replaceability", ramp["replaceability"], "swappable")
    eq("ramp purpose present", bool(ramp["purpose"]), True)
    eq("ramp has alternates", len(ramp["alternates"]) >= 2, True)
    alt_names = {a["name"] for a in ramp["alternates"]}
    (ok if "Sol Ring" not in alt_names else bad)("ramp excludes self", alt_names)
    (ok if "Cultivate" not in alt_names else bad)("ramp cost filter", alt_names)
    for alt in ramp["alternates"]:
        (ok if alt["name"] in {c["name"] for c in ramp_pool} else bad)(
            f"ramp alt '{alt['name']}' in pool", alt["name"])

    plan = subs["Strategy Piece"]
    eq("plan replaceability core", plan["replaceability"], "core")
    eq("plan has no alternates", len(plan["alternates"]), 0)

    basic = subs["Island"]
    eq("basic replaceability", basic["replaceability"], "swappable")

    # Verifier drops invented names.
    bogus = [{"name": "Black Lotus", "cost": "{0}", "cmc": 0, "usd": "10000"}]
    verified = substitution.verify_alternates(bogus, ramp_pool)
    eq("invented alternate dropped", len(verified), 0)

    # pick_alternates respects deck membership.
    alts = substitution.pick_alternates(
        deck[0], ramp_pool, deck_names, ci, count=3)
    (ok if all(a["name"] not in deck_names for a in alts) else bad)(
        "alts not in deck", alts)


def test_land_ramp_policy():
    """any land-type pick counts against the land slot, for every role."""
    print("\nland-slot policy  — (all roles)")
    land_target = 36  # fixture size for the land-slot policy slot policy, not curve-derived
    ramp_target = next(t for r, t, _ in app.ROLES if r == "ramp")
    draw_target = next(t for r, t, _ in app.ROLES if r == "draw")

    def nonbasic_lands(deck):
        return sum(1 for c in deck if app.is_land_card(c) and c.get("role") != "basic")

    def lands_outside_land_roles(deck):
        return [c["name"] for c in deck
                if app.is_land_card(c) and c.get("role") not in ("lands", "basic")]

    # Ramp: lands picked for ramp swap into the land slot.
    deck = [_card(f"Land Slot {i}", typ="Land", role="lands") for i in range(land_target)]
    seen = {c["name"] for c in deck}
    pool = ([_card("Command Tower", typ="Land")]
            + [_card("Evolving Wilds", typ="Land")]
            + [_card(f"Sol Ring {i}", typ="Artifact",
                     text="{T}: Add {C}.") for i in range(20)])
    chosen = pool[:ramp_target]  # 2 lands + 8 rocks if target is 10
    app.place_role_cards("ramp", deck, chosen, pool, ramp_target, seen)
    eq("nonbasic land count after ramp placement", nonbasic_lands(deck), land_target)
    eq("ramp slot has no lands",
       sum(1 for c in deck if c.get("role") == "ramp" and app.is_land_card(c)), 0)
    eq("ramp slot filled", sum(1 for c in deck if c.get("role") == "ramp"), ramp_target)

    # Draw / deepcut (the reopen leak): Fountainport and Promising Vein must not
    # sit in non-land roles or push the land count past the skeleton target.
    deck = [_card(f"Land Slot {i}", typ="Land", role="lands") for i in range(land_target)]
    seen = {c["name"] for c in deck}
    draw_pool = ([_card("Fountainport", typ="Land")]
                 + [_card(f"Draw Spell {i}", typ="Instant", text="Draw a card.")
                    for i in range(20)])
    draw_chosen = draw_pool[:draw_target]  # land first, then spells
    app.place_role_cards("draw", deck, draw_chosen, draw_pool, draw_target, seen)
    eq("nonbasic land count after draw leak", nonbasic_lands(deck), land_target)
    eq("draw slot has no lands",
       sum(1 for c in deck if c.get("role") == "draw" and app.is_land_card(c)), 0)
    eq("draw slot filled", sum(1 for c in deck if c.get("role") == "draw"), draw_target)
    eq("Fountainport counted as land",
       sum(1 for c in deck if c["name"] == "Fountainport" and c.get("role") == "lands"), 1)

    deep_pool = ([_card("Promising Vein", typ="Land")]
                 + [_card(f"Flavor {i}", typ="Artifact") for i in range(12)])
    deep_chosen = deep_pool[:5]
    app.place_role_cards("deepcut", deck, deep_chosen, deep_pool, 5, seen)
    eq("nonbasic land count after deepcut leak", nonbasic_lands(deck), land_target)
    eq("no lands outside lands/basic roles", lands_outside_land_roles(deck), [])
    eq("deepcut slot has no lands",
       sum(1 for c in deck if c.get("role") == "deepcut" and app.is_land_card(c)), 0)

    # Cultivate is a sorcery that fetches a land — type line, not effect. Stays ramp.
    deck = [_card(f"Land Slot {i}", typ="Land", role="lands") for i in range(land_target)]
    seen = {c["name"] for c in deck}
    cultivate = _card("Cultivate", typ="Sorcery",
                      text="Search your library for up to two basic land cards.")
    rocks = [_card(f"Rock {i}", typ="Artifact", text="{T}: Add {C}.") for i in range(20)]
    app.place_role_cards("ramp", deck, [cultivate] + rocks[:ramp_target - 1],
                         [cultivate] + rocks, ramp_target, seen)
    eq("Cultivate stays ramp",
       sum(1 for c in deck if c["name"] == "Cultivate" and c.get("role") == "ramp"), 1)

    # Built-deck shape: skeleton lands + non-land ramp + filler + basics.
    random.seed(1)
    deck = ([{"name": f"L{i}", "type": "Land", "cmc": 0, "text": "{T}: Add {U}.",
              "role": "lands"} for i in range(land_target)]
            + [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                "text": "{T}: Add one mana of any color.", "role": "ramp"}
               for i in range(ramp_target)]
            + [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                "text": "Whenever a land enters, create a token.", "role": "theme"}
               for i in range(99 - land_target - ramp_target - 4)])
    deck += [{"name": "Island", "type": "Basic Land — Island", "cmc": 0,
              "text": "", "role": "basic"} for _ in range(4)]
    eq("fixture is 99", len(deck), 99)
    prepped = sim.prep(deck, set())
    eq("sim.diagnose nonbasic lands match skeleton",
       sum(1 for c in prepped if c["land"] and c.get("role") != "basic"), land_target)
    eq("sim.diagnose ramp has no lands",
       sum(1 for c in prepped if c.get("role") == "ramp" and c["land"]), 0)


def test_strategy_role_queries():
    """ramp/draw/spot queries follow the chosen strategy, not one generic ladder."""
    print("\nstrategy role queries")

    aggro = {
        "theme": "pirate",
        "strategy": ("Mono-red low-to-the-ground aggro. The other 99 are mostly "
                     "1- and 2-mana creatures, cheap burn, and a dozen Treasure-makers."),
        "mechanics": ["treasure", "sacrifice outlet"],
    }
    bombs = {
        "theme": "pirate",
        "strategy": ("Ramp into seven-drop haymakers. Hold cards, draw two at a time, "
                     "and wipe the board when creatures stick."),
        "mechanics": ["ramp", "draw two"],
    }
    generic = {"theme": "pirate", "bracket": 2}

    ramp_aggro = app.role_query("ramp", "UB", aggro, 5.0)
    ramp_bombs = app.role_query("ramp", "UB", bombs, 5.0)
    ramp_plain = app.role_query("ramp", "UB", generic, 5.0)
    generic_ramp = ("f:commander -is:gamechanger id<=UB "
                    "(otag:ramp OR o:'search your library for a') -t:land cmc<=3 usd<5.00")
    (ok if ramp_plain == [generic_ramp] else bad)(
        "no strategy falls back to one generic ramp rung", str(ramp_plain))
    (ok if ramp_aggro != ramp_bombs else bad)(
        "aggro vs haymaker ramp ladders differ", f"{ramp_aggro[0]} | {ramp_bombs[0]}")
    (ok if any("cmc<=2" in q or "cmc<=3" in q for q in ramp_aggro) else bad)(
        "aggro ramp caps cmc", ramp_aggro[0])
    (ok if any("cmc<=5" in q or "put a land" in q.lower() for q in ramp_bombs) else bad)(
        "haymaker ramp allows bigger / land ramp", ramp_bombs[0])
    # player-vocab mechanics must not emit bare o:ramp (incidental text).
    (ok if not any(re.search(r"(^|\s)o:ramp(\s|$)", q) for q in ramp_bombs) else bad)(
        "haymaker ramp ladder has no bare o:ramp", ramp_bombs[0])
    (ok if any(re.search(r"(^|\s)otag:ramp(\s|$)", q) for q in ramp_bombs) else bad)(
        "haymaker ramp ladder uses otag:ramp", ramp_bombs[0])

    draw_aggro = app.role_query("draw", "UB", aggro, 5.0)
    draw_bombs = app.role_query("draw", "UB", bombs, 5.0)
    (ok if draw_aggro != draw_bombs else bad)(
        "aggro vs haymaker draw ladders differ", f"{draw_aggro[0]} | {draw_bombs[0]}")
    (ok if any("attack" in q.lower() or "refill" in q.lower() for q in draw_aggro) else bad)(
        "aggro draw prefers refill / combat", draw_aggro[0])
    (ok if any("draw two" in q.lower() or "cmc>=2" in q for q in draw_bombs) else bad)(
        "haymaker draw prefers card advantage", draw_bombs[0])

    spot_aggro = app.role_query("spot", "UB", aggro, 5.0)
    spot_clear = app.role_query("spot", "UB", bombs, 5.0)
    (ok if spot_aggro != spot_clear else bad)(
        "protect-board vs clear-board spot ladders differ", f"{spot_aggro[0]} | {spot_clear[0]}")
    (ok if any("destroy all" not in q for q in spot_aggro) else bad)(
        "aggro spot avoids symmetrical wipes in first rung", spot_aggro[0])
    (ok if "-o:\"destroy all\"" in spot_aggro[0] or "-o:'destroy all'" in spot_aggro[0]
          else bad)("aggro spot excludes destroy all", spot_aggro[0])

    prof = app.strategy_profile(aggro)
    eq("aggro curve low", prof["curve"], "low")
    eq("bombs curve high", app.strategy_profile(bombs)["curve"], "high")
    eq("aggro draw refill", prof["draw"], "refill")
    eq("bombs draw accumulate", app.strategy_profile(bombs)["draw"], "accumulate")


def test_type_targets():
    """type floors live inside roles; strategy picks which floors."""
    print("\ntype targets inside roles")

    combat = {
        "theme": "pirate",
        "strategy": ("Swing wide with pirate tokens. Attack every turn, "
                     "go wide, combat damage triggers, creatures you control."),
        "mechanics": ["go wide", "tokens", "combat"],
    }
    spells = {
        "theme": "pirate",
        "strategy": ("Spellslinger pirates. Cast instants and sorceries, "
                     "noncreature spells that draw and burn, prowess triggers."),
        "mechanics": ["spellslinger", "instants", "noncreature spells"],
    }
    control = {
        "theme": "pirate",
        "strategy": ("Control the board. Counter spells, answer threats, "
                     "keep the board clear until a late permission win."),
        "mechanics": ["counterspells", "removal"],
    }
    plain = {"theme": "pirate"}

    eq("combat bodies", app.strategy_profile(combat)["bodies"], "creatures")
    eq("spellslinger bodies", app.strategy_profile(spells)["bodies"], "spells")
    eq("control bodies", app.strategy_profile(control)["bodies"], "interaction")
    eq("no strategy mixed", app.strategy_profile(plain)["bodies"], "mixed")

    # Same theme role, different floors — not a parallel creature budget.
    combat_theme = app.role_type_floors("theme", 15, combat)
    spell_theme = app.role_type_floors("theme", 15, spells)
    control_theme = app.role_type_floors("theme", 15, control)
    plain_theme = app.role_type_floors("theme", 15, plain)
    eq("combat theme wants creatures", combat_theme.get("creatures"), 9)  # 60% of 15
    eq("spell theme wants I/S", spell_theme.get("instants_sorceries"), 8)  # ~50%
    eq("control theme wants I/S", control_theme.get("instants_sorceries"), 6)
    eq("no strategy: no type floor", plain_theme, {})
    (ok if combat_theme != spell_theme else bad)(
        "combat vs spellslinger theme floors differ", f"{combat_theme} | {spell_theme}")

    # Utility roles are not reshaped by type floors (constraints stay in theme/plan/deepcut).
    eq("ramp has no type floor", app.role_type_floors("ramp", 10, combat), {})

    # enforce_type_targets swaps from the pool; never invents.
    chosen = [_card(f"Spell{i}", typ="Instant", role="theme") for i in range(15)]
    pool = (list(chosen)
            + [_card(f"Body{i}", typ="Creature — Pirate", role="theme") for i in range(12)])
    fixed = app.enforce_type_targets("theme", chosen, pool, 15, combat)
    eq("still 15 cards", len(fixed), 15)
    creatures = sum(1 for c in fixed if "creature" in (c.get("type") or "").lower())
    (ok if creatures >= 9 else bad)(f"combat theme ≥9 creatures after enforce", creatures)
    eq("all names from pool",
       all(c["name"] in {p["name"] for p in pool} for c in fixed), True)

    # Query ladder: combat theme prefers t:creature early; spellslinger prefers I/S.
    combat_q = app.role_query("theme", "UB", combat, 5.0)
    spell_q = app.role_query("theme", "UB", spells, 5.0)
    (ok if any("t:creature" in q for q in combat_q[:3]) else bad)(
        "combat theme ladder biases creatures", combat_q[0])
    (ok if any("t:instant" in q or "t:sorcery" in q for q in spell_q[:3]) else bad)(
        "spellslinger theme ladder biases instants/sorceries", spell_q[0])
    (ok if combat_q != spell_q else bad)(
        "same theme, different strategy → different theme ladders",
        f"{combat_q[0]} | {spell_q[0]}")

    # Breakdown is countable and feeds overview cons for a thin combat deck.
    thin = ([_card(f"L{i}", typ="Basic Land — Island", role="basic") for i in range(36)]
            + [_card(f"R{i}", typ="Artifact", role="ramp",
                     text="{T}: Add {U}.") for i in range(10)]
            + [_card(f"D{i}", typ="Instant", role="draw", text="Draw a card.")
               for i in range(9)]
            + [_card(f"S{i}", typ="Instant", role="spot",
                     text="Destroy target creature.") for i in range(7)]
            + [_card(f"W{i}", typ="Sorcery", role="wipe",
                     text="Destroy all creatures.") for i in range(3)]
            + [_card(f"P{i}", typ="Instant", role="plan", text="Draw two cards.")
               for i in range(10)]
            + [_card(f"T{i}", typ="Instant", role="theme", text="Create a Treasure.")
               for i in range(12)]
            + [_card(f"C{i}", typ="Creature — Pirate", role="theme") for i in range(3)]
            + [_card(f"X{i}", typ="Sorcery", role="deepcut") for i in range(5)]
            + [_card("Spare", typ="Artifact", role="theme")])
    # 36+10+9+7+3+10+12+3+5+1 = 96 — pad to 99
    thin += [_card(f"Pad{i}", typ="Artifact", role="theme") for i in range(3)]
    eq("thin fixture 99", len(thin), 99)
    breakdown = app.type_breakdown(thin)
    eq("breakdown creatures", breakdown["creatures"], 3)
    eq("breakdown lands", breakdown["lands"], 36)
    (ok if breakdown["instants_sorceries"] >= 30 else bad)(
        "breakdown counts I/S", breakdown["instants_sorceries"])

    sig = app.deck_signals(thin, brief=combat)
    for axis in ("creatures", "instants_sorceries", "artifacts", "enchantments", "lands"):
        (ok if axis in sig["counts"] else bad)(f"signals.counts has {axis}", sig["counts"])
    watch = " ".join(s["text"] for s in sig["watch"])
    (ok if "creature" in watch.lower() and "3" in watch else bad)(
        "combat deck with 3 creatures is a cons signal", watch)

    # facts_block surfaces the breakdown for the deck overview.
    facts = overview.facts_block(
        "Malcolm", {"name": "Malcolm"}, thin, combat, sig,
        {"curves": [], "diagnostics": {}, "assumptions": []},
        lambda c: {"name": c["name"], "type": c.get("type")})
    (ok if '"creatures": 3' in facts or '"creatures":3' in facts else bad)(
        "overview FACTS include type breakdown", facts[:400])


async def test_ramp_query_no_lands():
    """Ramp Scryfall pool must not return lands."""
    print("\nramp query pool  — no lands in results")
    try:
        q = app.role_query("ramp", "UB", {"theme": "pirate", "bracket": 2}, 5.0)[0]
        pool = await app.search(q, limit=45)
        lands = [c for c in pool if app.is_land_card(c)]
        eq("ramp pool has zero lands", len(lands), 0)
        cultivate = await app.search(
            "f:commander o:'search your library for a' -t:land cmc<=3 id<=UBG", limit=5)
        spells = [c for c in cultivate if not app.is_land_card(c)]
        (ok if spells else bad)(
            "land-fetch spells still searchable", [c["name"] for c in cultivate[:3]])
    except app.ScryfallError as e:
        live_skip("ramp query pool", e)


async def test_strategy_ramp_pools():
    """haymaker ramp is rocks/land-fetch, not Embercleave from o:ramp."""
    print("\nstrategy ramp pools  — live Scryfall")
    await app.load_vocab()
    aggro = {
        "theme": "pirate",
        "strategy": ("Mono-red low-to-the-ground aggro. The other 99 are mostly "
                     "1- and 2-mana creatures, cheap burn, and a dozen Treasure-makers."),
        "mechanics": ["treasure", "sacrifice outlet"],
        "bracket": 2,
    }
    bombs = {
        "theme": "pirate",
        "strategy": ("Ramp into seven-drop haymakers. Hold cards, draw two at a time, "
                     "and wipe the board when creatures stick."),
        "mechanics": ["ramp", "draw two"],
        "bracket": 2,
    }
    try:
        aggro_pool, aggro_used = await app.search_ladder(
            app.role_query("ramp", "R", aggro, 12.0), limit=25, floor=10)
        bombs_pool, bombs_used = await app.search_ladder(
            app.role_query("ramp", "R", bombs, 12.0), limit=25, floor=10)
    except app.ScryfallError as e:
        live_skip("strategy ramp pools", e)
        return
    aggro_names = [c["name"] for c in aggro_pool]
    bombs_names = [c["name"] for c in bombs_pool]
    print(f"{DIM}  aggro used: {aggro_used}{OFF}")
    print(f"{DIM}  aggro pool: {aggro_names}{OFF}")
    print(f"{DIM}  haymaker used: {bombs_used}{OFF}")
    print(f"{DIM}  haymaker pool: {bombs_names}{OFF}")

    junk = {"Embercleave", "Temur Battle Rage", "Loxodon Warhammer", "Leyline Axe"}
    rocks = {"Sol Ring", "Arcane Signet", "Fellwar Stone", "Mind Stone", "Thought Vessel"}
    (ok if not junk & set(bombs_names) else bad)(
        "haymaker ramp is not incidental o:ramp junk",
        sorted(junk & set(bombs_names)))
    (ok if rocks & set(bombs_names) else bad)(
        "haymaker ramp includes mana rocks", bombs_names[:10])
    (ok if not junk & set(aggro_names) else bad)(
        "aggro ramp is not o:ramp junk either",
        sorted(junk & set(aggro_names)))
    # Aggro with Treasure mechanics may land on t:treasure (mana via tokens) or
    # the cheap-rock rung — either is real ramp. Bare o:ramp is not.
    (ok if "o:ramp" not in (aggro_used or "") else bad)(
        "aggro did not land on bare o:ramp", aggro_used)
    (ok if "o:ramp" not in (bombs_used or "") else bad)(
        "haymaker landed on a real ramp rung, not o:ramp", bombs_used)
    (ok if set(aggro_names) != set(bombs_names) else bad)(
        "aggro vs haymaker pools still differ",
        f"overlap={len(set(aggro_names) & set(bombs_names))}")


def test_sim_baseline():
    """A known deck must produce known curves. Guards against extraction drift."""
    print("\nsim.run()  — fixed-seed baseline")
    random.seed(1)
    deck = ([{"name": f"L{i}", "type": "Land", "cmc": 0, "text": "{T}: Add {G}."} for i in range(36)]
            + [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                "text": "{T}: Add one mana of any color."} for i in range(10)]
            + [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                "text": "Whenever a land enters, create a token."} for i in range(12)])
    deck += [{"name": f"F{i}", "type": "Instant", "cmc": 3, "text": "Counter target spell."}
             for i in range(99 - len(deck))]
    out = sim.run(deck, 5, {f"P{i}" for i in range(12)},
                  [{"label": "cmdr", "check": {"commander_in_play": True}},
                   {"label": "seven", "check": {"mana_available": 7}}], runs=1500)
    d = out["diagnostics"]
    eq("diagnostics.lands", d["lands"], 36)
    eq("diagnostics.mana_rocks", d["mana_rocks"], 10)
    eq("diagnostics.payoffs", d["payoffs"], 12)
    # goldfish assumptions travel with the curves (commander tax stated, not modelled).
    eq("assumptions present", out.get("assumptions"), sim.ASSUMPTIONS)
    (ok if "cast once and stays" in sim.ASSUMPTIONS else bad)(
        "assumptions state commander tax", sim.ASSUMPTIONS)
    (ok if "on cast" in sim.ASSUMPTIONS.lower() else bad)(
        "assumptions state commander on-cast effects", sim.ASSUMPTIONS)
    (ok if "exchange" in sim.ASSUMPTIONS.lower() else bad)(
        "assumptions deny inventing life exchange", sim.ASSUMPTIONS)
    (ok if "no colour screw" not in sim.ASSUMPTIONS.lower()
          and "no color screw" not in sim.ASSUMPTIONS.lower() else bad)(
        "assumptions do not deny colour (models it)", sim.ASSUMPTIONS)
    c1, c2 = out["curves"]
    for label, pts, lo, hi in (("commander by T5", c1["points"][4], 60, 80),
                               ("seven mana by T5", c2["points"][4], 4, 20)):
        (ok if lo <= pts <= hi else bad)(label, f"{pts}% (expected {lo}-{hi}%)")

    # Monotonicity: a cumulative curve can never go down.
    for c in out["curves"]:
        p = c["points"]
        (ok if all(p[i] <= p[i + 1] for i in range(len(p) - 1)) else bad)(
            f"{c['label']} curve is non-decreasing", str(p))


def test_sim_colored_mana():
    """UB spells on a white-only mana base must fail to cast — not match a proper base."""
    print("\nsim.run()  — colored mana")
    random.seed(1)

    def _fixing_rocks():
        return [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                 "text": "{T}: Add one mana of any color.",
                 "produced_mana": ["W", "U", "B", "R", "G"], "cost": "{2}"}
                for i in range(10)]

    def _ub_payoffs_and_filler():
        payoffs = [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                    "text": "Draw.", "cost": "{1}{U}{B}"} for i in range(12)]
        filler = [{"name": f"F{i}", "type": "Instant", "cmc": 3,
                   "text": "Counter.", "cost": "{1}{U}{B}"} for i in range(41)]
        return payoffs + filler

    white_lands = [{"name": f"Pl{i}", "type": "Land", "cmc": 0,
                    "text": "{T}: Add {W}.", "produced_mana": ["W"], "cost": ""}
                   for i in range(36)]
    proper_lands = ([{"name": f"Is{i}", "type": "Land", "cmc": 0,
                      "text": "{T}: Add {U}.", "produced_mana": ["U"], "cost": ""}
                     for i in range(18)]
                    + [{"name": f"Sw{i}", "type": "Land", "cmc": 0,
                        "text": "{T}: Add {B}.", "produced_mana": ["B"], "cost": ""}
                       for i in range(18)])

    broken = white_lands + _fixing_rocks() + _ub_payoffs_and_filler()
    proper = proper_lands + _fixing_rocks() + _ub_payoffs_and_filler()
    eq("broken deck is 99", len(broken), 99)
    eq("proper deck is 99", len(proper), 99)

    cond = [{"label": "three payoffs", "check": {"payoffs_in_play": 3}}]
    payoff_names = {f"P{i}" for i in range(12)}
    broken_out = sim.run(broken, 5, payoff_names, cond, runs=1500)
    proper_out = sim.run(proper, 5, payoff_names, cond, runs=1500)
    broken_t5 = broken_out["curves"][0]["points"][4]
    proper_t5 = proper_out["curves"][0]["points"][4]

    # White sources cannot pay {U}{B}; curve must collapse vs a real UB base.
    (ok if broken_t5 < 5 else bad)(
        "white-only mana base collapses payoffs by T5", f"{broken_t5}%")
    (ok if proper_t5 >= 10 else bad)(
        "proper UB mana base casts payoffs by T5", f"{proper_t5}%")
    (ok if proper_t5 > broken_t5 + 5 else bad)(
        "proper base beats broken on payoffs", f"{proper_t5}% vs {broken_t5}%")


def test_sim_sample_hands():
    """sample hands are real deals from the run, every card in the deck."""
    print("\nsim.run()  — sample good/bad hands")
    random.seed(1)
    deck = ([{"name": f"L{i}", "type": "Land", "cmc": 0, "text": "{T}: Add {G}."} for i in range(36)]
            + [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                "text": "{T}: Add one mana of any color."} for i in range(10)]
            + [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                "text": "Whenever a land enters, create a token."} for i in range(12)])
    deck += [{"name": f"F{i}", "type": "Instant", "cmc": 3, "text": "Counter target spell."}
             for i in range(99 - len(deck))]
    names = {c["name"] for c in deck}
    out = sim.run(deck, 5, {f"P{i}" for i in range(12)},
                  [{"label": "cmdr", "check": {"commander_in_play": True}},
                   {"label": "seven", "check": {"mana_available": 7}}], runs=1500)

    for label in ("good_hand", "bad_hand"):
        hand = out.get(label)
        (ok if hand else bad)(f"{label} present", hand)
        if not hand:
            continue
        (ok if len(hand) >= 7 else bad)(f"{label} has opening seven", len(hand))
        for card in hand:
            (ok if card["name"] in names else bad)(
                f"{label} card in deck", card["name"])
            (ok if "draw_turn" in card and "played_turn" in card else bad)(
                f"{label} card has turn fields", card)

    good, bad = out.get("good_hand"), out.get("bad_hand")
    if good and bad:
        good_plays = [c["played_turn"] for c in good if c["played_turn"] is not None]
        bad_plays = [c["played_turn"] for c in bad if c["played_turn"] is not None]
        (ok if good_plays and (not bad_plays or min(good_plays) <= max(bad_plays or [99]))
         else bad)("good hand plays earlier than bad", f"good={good_plays[:3]} bad={bad_plays[:3]}")


def test_launchd_plist():
    """Personal launchd plists are not in the public tree."""
    from pathlib import Path

    print("\nlaunchd plist  — public tree has none")
    repo = Path(__file__).resolve().parents[1]
    plist = repo / "config" / "com.deckwright.plist"
    (ok if not plist.is_file() else bad)("no personal launchd plist committed")


async def test_desktop_install_update():
    """Pull-on-launch is opt-in; boot veil and update.sh still matter."""
    import os
    import stat
    from pathlib import Path

    print("\ndesktop install/update  — pull opt-in + progressive boot")
    repo = Path(__file__).resolve().parents[1]

    cadence_sh = repo / "scripts" / "refresh_data_cadence.sh"
    if cadence_sh.is_file():
        text = cadence_sh.read_text()
        (ok if "--reeval" in text and "print_wave_reeval.py" in text else bad)(
            "cadence script supports understanding --reeval")

    run_sh = (repo / "run.sh").read_text()
    (ok if "pull_on_launch.sh" in run_sh else bad)("run.sh can call pull_on_launch")
    (ok if "DECKWRIGHT_PULL_ON_LAUNCH" in run_sh else bad)(
        "git pull on launch is opt-in")
    (ok if "127.0.0.1" in run_sh else bad)("run.sh defaults HOST to localhost")
    pull = repo / "scripts" / "pull_on_launch.sh"
    (ok if pull.is_file() else bad)("pull_on_launch.sh exists")
    if pull.is_file():
        (ok if pull.stat().st_mode & stat.S_IXUSR else bad)(
            "pull_on_launch.sh is executable")
        pt = pull.read_text()
        (ok if "git pull --ff-only" in pt else bad)("pull uses ff-only")
        (ok if "DECKWRIGHT_SKIP_PULL" in pt else bad)("pull can be skipped")
        (ok if "dirty working tree" in pt else bad)("pull skips dirty tree")
        (ok if "DECKWRIGHT_RELEASE_REF" in pt else bad)(
            "pull supports DECKWRIGHT_RELEASE_REF release channel")
        (ok if "refs/tags/" in pt or "checkout --detach" in pt else bad)(
            "pull can check out a release tag")
        wf = repo / ".github" / "workflows" / "tier1.yml"
        (ok if wf.is_file() else bad)("tier1 CI workflow exists")
        if wf.is_file():
            wft = wf.read_text()
            (ok if "check_all.py --tier 1" in wft else bad)(
                "CI runs authoritative tier 1 gate")

    update = repo / "update.sh"
    (ok if update.is_file() else bad)("update.sh exists")
    if update.is_file():
        (ok if update.stat().st_mode & stat.S_IXUSR else bad)("update.sh is executable")
        text = update.read_text()
        (ok if "--cadence" in text else bad)("update.sh supports --cadence")
        (ok if "--print-wave" in text else bad)("update.sh supports --print-wave")
        (ok if "print_wave_reeval.py" in text else bad)(
            "update.sh wires print-wave reeval")
        (ok if "refresh_data_cadence.sh" in text else bad)(
            "update.sh can run data cadence")

    html = (repo / "index.html").read_text()
    (ok if 'id="boot"' in html else bad)("index has boot veil")
    (ok if "ensureBoot" in html else bad)("index waits on ensureBoot")
    (ok if "/api/boot" in html else bad)("index polls /api/boot")

    from deckwright import boot as boot_mod
    from deckwright import corpus as corpus_mod
    boot_mod.reset_for_tests()
    (ok if boot_mod.data_is_stale(limit_days=10_000) is False else bad)(
        "fresh committed data is not stale at huge limit")
    (ok if boot_mod.data_is_stale(limit_days=-1) is True else bad)(
        "negative limit treats current data as stale")

    # re-sign-off: a missing local corpus must trigger boot refresh.
    # Grammar/KB can be fresh while ~/.deckwright/oracle_cards.jsonl is absent —
    # that left every lookup on live Scryfall (the afternoon outage exposure).
    # data_is_stale() stays about committed catalogs; corpus absence is separate.
    real_oracle = corpus_mod.ORACLE_JSONL
    corpus_mod.ORACLE_JSONL = Path("/tmp/deckwright-missing-oracle.jsonl")
    old_skip_refresh = os.environ.pop("DECKWRIGHT_SKIP_BOOT_REFRESH", None)
    old_force_refresh = os.environ.pop("DECKWRIGHT_BOOT_REFRESH", None)
    try:
        (ok if boot_mod.corpus_is_missing() is True else bad)(
            "absent oracle_cards.jsonl is corpus_is_missing")
        (ok if boot_mod.data_is_stale(limit_days=10_000) is False else bad)(
            "missing corpus does not pretend committed data is stale")
        (ok if boot_mod._should_refresh() is True else bad)(
            "missing corpus triggers boot refresh")
    finally:
        corpus_mod.ORACLE_JSONL = real_oracle
        if old_skip_refresh is not None:
            os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = old_skip_refresh
        if old_force_refresh is not None:
            os.environ["DECKWRIGHT_BOOT_REFRESH"] = old_force_refresh

    summary = boot_mod.load_understanding_summary()
    (ok if summary.get("status") in ("ok", "unknown") else bad)(
        "understanding summary has status")
    (ok if "needs_admit" in summary else bad)(
        "understanding summary exposes needs_admit")
    (ok if "unread_pct" in summary or summary.get("status") == "unknown" else bad)(
        "understanding summary exposes unread_pct when report present")

    async def _noop_warm():
        return {}

    old_skip = os.environ.get("DECKWRIGHT_SKIP_BOOT_REFRESH")
    os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = "1"
    try:
        st = await boot_mod.run_boot(warm=_noop_warm)
        (ok if st.get("ready") else bad)("boot ready after warm")
        eq("boot phase ready", st.get("phase"), "ready")
        (ok if st.get("refreshed") is False else bad)(
            "skip env does not refresh cadence")
        (ok if isinstance(st.get("understanding"), dict) else bad)(
            "boot status includes understanding summary")

        # Call the route function directly — TestClient+lifespan nests
        # asyncio.run inside this suite's running loop.
        body = await app.api_boot()
        (ok if body.get("ready") and body.get("phase") else bad)(
            "/api/boot shape has ready+phase")
        eq("/api/boot phase", body.get("phase"), "ready")
        (ok if "understanding" in body else bad)(
            "/api/boot includes understanding")
    finally:
        if old_skip is None:
            os.environ.pop("DECKWRIGHT_SKIP_BOOT_REFRESH", None)
        else:
            os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = old_skip
        boot_mod.reset_for_tests()


def test_import_deck_parser():
    """pasted list formats parse to name/qty/section."""
    print("\nimport_deck.parse_decklist()  — Archidekt/Moxfield/plain formats")

    text = """
// Commander
1 Admiral Beckett Brass
// Lands
1 Command Tower
18 Island
1 Sol Ring
1x Lightning Bolt
Sol Ring
"""
    parsed = import_deck.parse_decklist(text)
    entries = parsed["entries"]
    eq("entry count", len(entries), 6)
    eq("commander section", entries[0]["section"], "commander")
    eq("commander name", entries[0]["name"], "Admiral Beckett Brass")
    eq("lands qty", entries[2]["qty"], 18)
    eq("1x prefix", entries[4]["name"], "Lightning Bolt")
    eq("plain name", entries[5]["name"], "Sol Ring")
    eq("moxfield set strip",
       import_deck.parse_decklist("1 Sol Ring (SLD) 123")["entries"][0]["name"],
       "Sol Ring")

    skipped = import_deck.parse_decklist("1 Real\n// Sideboard\n1 Fake Card")
    eq("sideboard skipped", len(skipped["entries"]), 1)

    comments = import_deck.parse_decklist("# comment\n\n1 Card")
    eq("hash comment skipped", comments["entries"][0]["name"], "Card")

    after_cmdr = import_deck.parse_decklist("// Commander\n1 Brass\n1 Sol Ring")
    eq("post-commander section is main", after_cmdr["entries"][1]["section"], "main")


def test_pick_commander():
    """commander from section, singleton legendary, or ambiguous."""
    print("\nimport_deck.pick_commander()  — section / CI / ambiguity")

    brass = _card("Admiral Beckett Brass",
                  typ="Legendary Creature — Human Pirate",
                  ci=["U", "B", "R"])
    ring = _card("Sol Ring", typ="Artifact", ci=[])
    bolt = _card("Lightning Bolt", typ="Instant", ci=["R"])

    resolved = {c["name"]: c for c in [brass, ring, bolt]}
    entries = [
        {"line": 1, "name": "Admiral Beckett Brass", "qty": 1, "section": "commander"},
        {"line": 2, "name": "Sol Ring", "qty": 1, "section": "main"},
        {"line": 3, "name": "Lightning Bolt", "qty": 1, "section": "main"},
    ]
    pick = import_deck.pick_commander(entries, resolved)
    eq("commander section wins", pick["status"], "found")
    eq("commander name", pick["commander"], "Admiral Beckett Brass")

    solo_entries = [
        {"line": 1, "name": "Admiral Beckett Brass", "qty": 1, "section": "main"},
        {"line": 2, "name": "Sol Ring", "qty": 1, "section": "main"},
    ]
    solo = import_deck.pick_commander(solo_entries, resolved)
    eq("single legendary auto-pick", solo["status"], "found")

    two_leg = {
        "Thrasios, Triton Hero": _card("Thrasios, Triton Hero",
                                       typ="Legendary Creature", ci=["G", "U"]),
        "Kydele, Chosen of Kruphix": _card("Kydele, Chosen of Kruphix",
                                           typ="Legendary Creature", ci=["G", "U"]),
        "Sol Ring": ring,
    }
    amb_entries = [
        {"line": 1, "name": "Sol Ring", "qty": 1, "section": "main"},
    ]
    amb = import_deck.pick_commander(amb_entries, two_leg)
    eq("two legendaries ambiguous", amb["status"], "ambiguous")
    (ok if len(amb["candidates"]) >= 2 else bad)("ambiguous lists candidates")


def test_pick_commander_partners():
    """import recognises Partner / Partner with pairs for CI cover."""
    print("\nimport_deck.pick_commander()  — partner pairs")

    malcolm = _card(
        "Malcolm, Keen-Eyed Navigator",
        typ="Legendary Creature — Siren Pirate",
        ci=["U"],
        text="Flying\nWhenever you…\nPartner",
    )
    malcolm["keywords"] = ["Flying", "Treasure", "Partner"]
    breeches = _card(
        "Breeches, Brazen Plunderer",
        typ="Legendary Creature — Goblin Pirate",
        ci=["R"],
        text="Menace\nWhenever…\nPartner",
    )
    breeches["keywords"] = ["Partner", "Menace"]
    # Bilbo (UB) in a UR list would correctly keep the pair from covering —
    # the happy path is a pure UR list (issue repro without an off-identity
    # legendary in the 98).
    ring = _card("Sol Ring", typ="Artifact", ci=[])
    bolt = _card("Lightning Bolt", typ="Instant", ci=["R"])
    counter = _card("Counterspell", typ="Instant", ci=["U"])
    fog = _card("Fog", typ="Instant", ci=["G"])

    ur = {
        c["name"]: c
        for c in [malcolm, breeches, ring, bolt, counter]
    }
    ur_entries = [
        {"line": i, "name": n, "qty": 1, "section": "main"}
        for i, n in enumerate(ur, 1)
    ]
    pick = import_deck.pick_commander(ur_entries, ur)
    eq("Malcolm+Breeches UR → found", pick["status"], "found")
    # Stable alphabetical order for the command-zone pair.
    eq("pair sets commander", pick.get("commander"),
       "Breeches, Brazen Plunderer")
    eq("pair sets partner", pick.get("partner"),
       "Malcolm, Keen-Eyed Navigator")
    eq("commanders list is the pair", pick.get("commanders"),
       ["Breeches, Brazen Plunderer", "Malcolm, Keen-Eyed Navigator"])

    green = {
        c["name"]: c
        for c in [malcolm, breeches, ring, bolt, counter, fog]
    }
    green_entries = [
        {"line": i, "name": n, "qty": 1, "section": "main"}
        for i, n in enumerate(green, 1)
    ]
    refused = import_deck.pick_commander(green_entries, green)
    eq("UR partners + green still refused", refused["status"], "ambiguous")
    (ok if refused.get("partner") in (None, "") else bad)(
        "refused pick has no partner", str(refused))

    # Two legendaries without Partner, neither covering a multicolour list.
    mono_u = _card("Talrand, Sky Summoner", typ="Legendary Creature", ci=["U"])
    mono_r = _card("Krenko, Mob Boss", typ="Legendary Creature", ci=["R"])
    no_kw = {
        "Talrand, Sky Summoner": mono_u,
        "Krenko, Mob Boss": mono_r,
        "Sol Ring": ring,
        "Lightning Bolt": bolt,
        "Counterspell": counter,
    }
    no_kw_entries = [
        {"line": i, "name": n, "qty": 1, "section": "main"}
        for i, n in enumerate(no_kw, 1)
    ]
    still_amb = import_deck.pick_commander(no_kw_entries, no_kw)
    eq("non-partners stay ambiguous", still_amb["status"], "ambiguous")
    eq("candidates stay individual names",
       sorted(still_amb.get("candidates") or []),
       sorted(["Talrand, Sky Summoner", "Krenko, Mob Boss"]))

    # One legendary covering everything — unchanged.
    brass = _card("Admiral Beckett Brass", typ="Legendary Creature",
                  ci=["U", "B", "R"])
    solo = {
        "Admiral Beckett Brass": brass,
        "Sol Ring": ring,
        "Lightning Bolt": bolt,
    }
    solo_entries = [
        {"line": 1, "name": "Admiral Beckett Brass", "qty": 1, "section": "main"},
        {"line": 2, "name": "Sol Ring", "qty": 1, "section": "main"},
        {"line": 3, "name": "Lightning Bolt", "qty": 1, "section": "main"},
    ]
    one = import_deck.pick_commander(solo_entries, solo)
    eq("single covering legendary still found", one["status"], "found")
    eq("solo has no partner key-or-null", one.get("partner"), None)

    # Three Partner legendaries, two covering pairs → ambiguous with pairs.
    thrasios = _card("Thrasios, Triton Hero", typ="Legendary Creature",
                     ci=["U", "G"], text="Partner")
    thrasios["keywords"] = ["Partner"]
    tymna = _card("Tymna the Weaver", typ="Legendary Creature",
                  ci=["W", "B"], text="Partner")
    tymna["keywords"] = ["Partner"]
    kydele = _card("Kydele, Chosen of Kruphix", typ="Legendary Creature",
                   ci=["G", "U"], text="Partner")
    kydele["keywords"] = ["Partner"]
    # WUBG cards: Thrasios+Tymna covers; Kydele+Tymna covers (same UG+WB).
    # Thrasios+Kydele is UG only — does not cover WB. Two covering pairs.
    pir = _card("Pir, Ingenious Steward", typ="Legendary Creature",
                ci=["G"], text="Partner with Toothy, Imaginary Friend")
    pir["keywords"] = ["Partner with Toothy, Imaginary Friend"]
    toothy = _card("Toothy, Imaginary Friend", typ="Legendary Creature",
                   ci=["U"], text="Partner with Pir, Ingenious Steward")
    toothy["keywords"] = ["Partner with Pir, Ingenious Steward"]
    wubg_spells = [
        _card("Swords to Plowshares", typ="Instant", ci=["W"]),
        _card("Counterspell", typ="Instant", ci=["U"]),
        _card("Dark Ritual", typ="Instant", ci=["B"]),
        _card("Rampant Growth", typ="Sorcery", ci=["G"]),
        ring,
    ]
    multi = {
        c["name"]: c
        for c in [thrasios, tymna, kydele, *wubg_spells]
    }
    multi_entries = [
        {"line": i, "name": n, "qty": 1, "section": "main"}
        for i, n in enumerate(multi, 1)
    ]
    multi_pick = import_deck.pick_commander(multi_entries, multi)
    eq("two covering pairs → ambiguous", multi_pick["status"], "ambiguous")
    cands = multi_pick.get("candidates") or []
    eq("ambiguous offers pair candidates", len(cands), 2)
    (ok if all(isinstance(c, dict) and c.get("commander") and c.get("partner")
               for c in cands) else bad)(
        "pair candidates are {commander, partner} objects", str(cands))
    pair_sets = {
        frozenset([c["commander"], c["partner"]]) for c in cands
    }
    eq("pair candidates are the two covering pairs", pair_sets, {
        frozenset(["Thrasios, Triton Hero", "Tymna the Weaver"]),
        frozenset(["Kydele, Chosen of Kruphix", "Tymna the Weaver"]),
    })

    # Partner with — Pir + Toothy cover a UG list.
    ug = {
        c["name"]: c
        for c in [pir, toothy, ring,
                  _card("Growth Spiral", typ="Instant", ci=["G", "U"])]
    }
    ug_entries = [
        {"line": i, "name": n, "qty": 1, "section": "main"}
        for i, n in enumerate(ug, 1)
    ]
    named = import_deck.pick_commander(ug_entries, ug)
    eq("Partner with → found", named["status"], "found")
    eq("Partner with commanders", set(named.get("commanders") or []),
       {"Pir, Ingenious Steward", "Toothy, Imaginary Friend"})

    # build_deck_list excludes both halves from the 98.
    deck = import_deck.build_deck_list(
        ur_entries, ur, pick["commander"], partner_name=pick.get("partner"))
    names = {c["name"] for c in deck}
    (ok if "Malcolm, Keen-Eyed Navigator" not in names else bad)(
        "commander excluded from main")
    (ok if "Breeches, Brazen Plunderer" not in names else bad)(
        "partner excluded from main")


def test_infer_role():
    """imported cards get roles for signals/sim."""
    print("\nimport_deck.infer_role()  — heuristic classification")
    eq("land", import_deck.infer_role(_card("Island", typ="Basic Land — Island")), "basic")
    eq("wipe", import_deck.infer_role(
        _card("Wrath", typ="Sorcery", text="Destroy all creatures.")), "wipe")
    eq("spot", import_deck.infer_role(
        _card("Murder", typ="Instant", text="Destroy target creature.")), "spot")


# ---------------------------------------------------------------- runner

def test_saved_deck_slugs():
    """every deck already on disk must still open after the shape change."""
    print("\nsaved decks — slugs and legacy files")
    eq("hyphenated commander resolves", bool(app._SLUG_RE.fullmatch("Niv-Mizzet")), True)
    eq("underscored commander resolves", bool(app._SLUG_RE.fullmatch("Lyra_Dawnbringer")), True)
    # The caches share the save directory and are not decks.
    for cache in ("vocab", "symbols", "precons"):
        eq(f"{cache} is not a deck", app.load_saved_deck(cache), None)
    # Path traversal must not survive the slug class.
    for bad in ("../etc/passwd", "a/b", "..", "a b"):
        eq(f"rejects {bad!r}", bool(app._SLUG_RE.fullmatch(bad)), False)


def test_precons_archidekt_conversion():
    """Archidekt deck JSON → decklist text, including DFC names."""
    print("\nprecons — Archidekt deck conversion")
    sample = {
        "id": 1,
        "name": "Virtue and Valor",
        "cards": [
            {
                "quantity": 1,
                "categories": ["Commander"],
                "card": {"oracleCard": {"name": "Ellivere of the Wild Court", "faces": []}},
            },
            {
                "quantity": 1,
                "categories": ["Creature"],
                "card": {
                    "oracleCard": {
                        "name": "Realm-Cloaked Giant // Cast Off",
                        "faces": [
                            {"name": "Realm-Cloaked Giant"},
                            {"name": "Cast Off"},
                        ],
                    },
                },
            },
            {"quantity": 12, "categories": ["Land"],
             "card": {"oracleCard": {"name": "Forest", "faces": []}}},
        ],
    }
    text, cmdr = precons.archidekt_to_decklist(sample)
    eq("commander name", cmdr, "Ellivere of the Wild Court")
    eq("commander section", text.startswith("// Commander\n1 Ellivere"), True)
    eq("dfc full name kept", "Realm-Cloaked Giant // Cast Off" in text, True)
    eq("land qty", "12 Forest" in text, True)

    faces_only = {"name": "", "faces": [{"name": "Front"}, {"name": "Back"}]}
    eq("faces compose name", precons.card_name(faces_only), "Front // Back")

    item = precons.summarize_list_item({
        "id": 99, "name": "Test Precon",
        "colors": {"W": 10, "U": 0, "B": 0, "R": 5, "G": 0},
        "featured": "https://example.com/art.jpg",
        "updatedAt": "2026-01-01",
    })
    eq("list colors", item["colors"], "WR")
    eq("list id string", item["id"], "99")


async def test_precons_cache_fallback():
    """stale remote falls back to on-disk catalog."""
    print("\nprecons — cache fallback")
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "precons.json"
        cache.write_text('{"fetched_at": 1, "precons": [{"id": "1", "name": "Cached"}]}')
        with patch.object(precons, "CACHE_PATH", cache), \
             patch.object(precons, "CACHE_MAX_AGE", 0), \
             patch.object(precons, "fetch_catalog_remote", AsyncMock(side_effect=RuntimeError("down"))):
            got = await precons.get_catalog()
            eq("uses cache when fetch fails", got[0]["name"], "Cached")

            cache.unlink()
            try:
                await precons.get_catalog()
                bad("expected PreconsUnavailable")
            except precons.PreconsUnavailable as e:
                (ok if "Paste" in str(e) else bad)("points at paste fallback", str(e))


def test_precon_brief_from_analysis():
    """a precon brief is read off the baked analysis, not asked for."""
    print("\nprecon brief from analysis")
    from deckwright import precon_bake

    # Tribal wizards — theme comes from creature subtypes on plan cards.
    wizards = [
        {"name": f"Wizard {i}", "role": "theme", "cmc": 3,
         "type": "Creature — Human Wizard",
         "text": "Whenever you cast an instant, create a 1/1 Wizard token.",
         "cost": "{2}{U}"}
        for i in range(12)
    ] + [
        {"name": "Sol Ring", "role": "ramp", "cmc": 1,
         "type": "Artifact", "text": "{T}: Add {C}{C}.", "cost": "{1}"},
        {"name": "Island", "role": "basic", "cmc": 0,
         "type": "Basic Land — Island", "text": "", "cost": ""},
    ]
    cmdr = {"name": "Inalla, Archmage Ritualist",
            "type": "Legendary Creature — Human Wizard", "cmc": 5}
    overview_data = {
        "strategy": "Wizards copy themselves and overwhelm with tokens.",
        "how_it_works": "Cast wizards, copy them, go wide.",
        "roles": {"theme": "Wizards that copy and make tokens.",
                  "draw": "Refill after emptying your hand."},
        "beats": [{"when": "Turns 1-3", "what": "Ramp"}],
        "combos": [], "pros": [], "cons": [],
    }
    brief = precon_bake.brief_from_analysis(
        overview_data, wizards, cmdr,
        signals={"counts": {"avg_cmc": 2.8, "counters": 0, "instants": 4,
                            "mana_rocks": 1, "land_ramp": 0}})
    eq("strategy from overview", brief["strategy"], overview_data["strategy"])
    eq("theme is wizard", brief.get("theme"), "wizard")
    eq("angle is how_it_works", brief.get("angle"), overview_data["how_it_works"])
    eq("source is precon", brief.get("source"), "precon")
    (ok if "theme" in (brief.get("mechanics") or []) else bad)(
        "mechanics include role keys", brief.get("mechanics"))

    # Archetype mechanics must diverge so baked thesis conditions diverge.
    aggro_deck = [
        {"name": f"Goblin {i}", "role": "theme", "cmc": 1,
         "type": "Creature — Goblin",
         "text": "Haste. Create a 1/1 red Goblin creature token.",
         "cost": "{R}"}
        for i in range(30)
    ]
    control_deck = [
        {"name": f"Counter {i}", "role": "theme", "cmc": 2,
         "type": "Instant",
         "text": "Counter target spell. Draw a card.",
         "cost": "{1}{U}"}
        for i in range(10)
    ] + [
        {"name": f"Wall {i}", "role": "theme", "cmc": 3,
         "type": "Creature — Wall",
         "text": "Defender. Draw a card.",
         "cost": "{2}{U}"}
        for i in range(10)
    ]
    combo_deck = [
        {"name": f"Piece {i}", "role": "theme", "cmc": 4,
         "type": "Creature — Wizard",
         "text": "When this enters, you win the game.",
         "cost": "{3}{U}"}
        for i in range(6)
    ] + [
        {"name": f"Tutor {i}", "role": "theme", "cmc": 2,
         "type": "Sorcery",
         "text": "Search your library for a card. Infinite combo enabler.",
         "cost": "{1}{U}"}
        for i in range(4)
    ]

    aggro_b = precon_bake.brief_from_analysis(
        {"strategy": "Low-curve goblins go wide.", "how_it_works": "Attack early.",
         "roles": {}, "beats": [], "combos": [], "pros": [], "cons": []},
        aggro_deck, {"name": "Krenko", "type": "Legendary Creature — Goblin"},
        signals={"counts": {"avg_cmc": 1.8, "counters": 0, "instants": 2,
                            "mana_rocks": 2, "land_ramp": 0}})
    control_b = precon_bake.brief_from_analysis(
        {"strategy": "Counter spells and accumulate card advantage.",
         "how_it_works": "Hold up mana.", "roles": {}, "beats": [],
         "combos": [], "pros": [], "cons": []},
        control_deck, {"name": "Talrand", "type": "Legendary Creature — Merfolk Wizard"},
        signals={"counts": {"avg_cmc": 3.2, "counters": 8, "instants": 14,
                            "mana_rocks": 4, "land_ramp": 0}})
    combo_b = precon_bake.brief_from_analysis(
        {"strategy": "Assemble a two-card combo and win.",
         "how_it_works": "Tutor the pieces.", "roles": {}, "beats": [],
         "combos": [], "pros": [], "cons": []},
        combo_deck, {"name": "Inalla", "type": "Legendary Creature — Human Wizard"},
        signals={"counts": {"avg_cmc": 3.5, "counters": 1, "instants": 3,
                            "mana_rocks": 6, "land_ramp": 0}})

    from deckwright import card_score
    aggro_c = [c["label"] for c in card_score.provisional_conditions(aggro_b)]
    control_c = [c["label"] for c in card_score.provisional_conditions(control_b)]
    combo_c = [c["label"] for c in card_score.provisional_conditions(combo_b)]
    (ok if aggro_c != control_c else bad)(
        "aggro vs control conditions differ", f"{aggro_c} vs {control_c}")
    (ok if aggro_c != combo_c else bad)(
        "aggro vs combo conditions differ", f"{aggro_c} vs {combo_c}")
    (ok if control_c != combo_c else bad)(
        "control vs combo conditions differ", f"{control_c} vs {combo_c}")
    eq("aggro theme goblin", aggro_b.get("theme"), "goblin")


def test_precon_bake_stamps():
    """baked analysis is keyed by list hash + analysis/sim versions."""
    print("\nprecon bake stamps")
    import tempfile
    from pathlib import Path
    from deckwright import precon_bake

    deck_a = [
        {"name": "Sol Ring", "role": "ramp", "cmc": 1, "usd": "1.50",
         "type": "Artifact", "text": "{T}: Add {C}{C}.", "cost": "{1}"},
        {"name": "Island", "role": "basic", "cmc": 0, "usd": "0.10",
         "type": "Basic Land — Island", "text": "", "cost": ""},
        {"name": "Island", "role": "basic", "cmc": 0, "usd": "0.10",
         "type": "Basic Land — Island", "text": "", "cost": ""},
        *[{"name": f"Pirate {i}", "role": "theme", "cmc": 2, "usd": "0.50",
           "type": "Creature — Human Pirate", "text": "Menace",
           "cost": "{1}{U}"} for i in range(6)],
    ]
    deck_b = list(deck_a) + [
        {"name": "Swamp", "role": "basic", "cmc": 0, "usd": "0.10",
         "type": "Basic Land — Swamp", "text": "", "cost": ""}
    ]
    cmdr = {"name": "Malcolm, Keen-Eyed Navigator", "cmc": 4, "usd": "2.00",
            "type": "Legendary Creature — Siren Pirate", "text": "Flying",
            "cost": "{2}{U}{U}", "color_identity": ["U"]}
    signals = {"strong": [], "watch": [], "counts": {"wipes": 0, "avg_cmc": 2.0,
                                                    "counters": 0, "instants": 1,
                                                    "mana_rocks": 1, "land_ramp": 0}}
    overview_data = {"strategy": "Pirates", "beats": [], "how_it_works": "Go wide.",
                     "combos": [], "pros": [], "cons": [], "roles": {"theme": "Pirates"}}
    sim_out = {
        "runs": 1500,
        "curves": [{"label": "Five mana", "note": "", "points": [0] * 10,
                    "median_turn": 5}],
        "diagnostics": {"lands": 2, "mana_rocks": 1, "land_ramp": 0,
                        "extra_drops": 0, "payoffs": 1, "avg_cmc": 1.0},
        "assumptions": sim.ASSUMPTIONS,
        "good_hand": [{"name": "Sol Ring", "drawn_turn": 0}],
        "bad_hand": [{"name": "Island", "drawn_turn": 0}],
    }

    payload = precon_bake.build_payload(
        precon_id="999001", name="Test Precon",
        source="https://archidekt.com/decks/999001",
        commander=cmdr["name"], commander_card=cmdr, deck=deck_a,
        signals=signals, overview_data=overview_data, sim_out=sim_out,
    )
    eq("analysis_version stamped", payload["analysis_version"],
       precon_bake.ANALYSIS_VERSION)
    eq("sim_version stamped", payload["sim_version"], sim.SIM_VERSION)
    eq("deck_hash stamped", payload["deck_hash"], bake.deck_list_hash(deck_a))
    eq("prices stripped from deck", "usd" in payload["deck"][0], False)
    eq("prices stripped from commander", "usd" in payload["commander_card"], False)
    eq("seed recorded", payload["sim"]["seed"], precon_bake.PRECON_SIM_SEED)
    eq("conditions recorded", bool(payload["sim"]["conditions"]), True)
    brief = payload.get("brief") or {}
    eq("brief stamped", brief.get("source"), "precon")
    eq("brief strategy", brief.get("strategy"), "Pirates")
    eq("brief theme from cards", brief.get("theme"), "pirate")
    (ok if brief.get("angle") == "Go wide." else bad)("brief angle", brief.get("angle"))

    eq("fresh with matching deck", precon_bake.analysis_fresh(payload, deck_a), True)
    eq("stale on hash mismatch", precon_bake.analysis_fresh(payload, deck_b), False)
    eq("fresh versions-only when deck omitted",
       precon_bake.analysis_fresh(payload), True)
    incomplete = dict(payload, complete=False)
    eq("incomplete not fresh", precon_bake.analysis_fresh(incomplete, deck_a), False)
    empty_ov = dict(payload, overview={"strategy": "", "how_it_works": ""})
    eq("empty overview not fresh", precon_bake.analysis_fresh(empty_ov, deck_a), False)
    no_brief = {k: v for k, v in payload.items() if k != "brief"}
    eq("missing brief not fresh", precon_bake.analysis_fresh(no_brief, deck_a), False)

    bumped = dict(payload, analysis_version=0)
    eq("stale on analysis_version", precon_bake.analysis_fresh(bumped, deck_a), False)
    old_sim = dict(payload, sim_version=0)
    eq("stale on sim_version", precon_bake.analysis_fresh(old_sim, deck_a), False)
    eq("stale when missing", precon_bake.analysis_fresh(None), False)

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        path = precon_bake.write_bake(payload, d)
        eq("wrote file", path.name, "999001.json")
        loaded = precon_bake.read_bake("999001", d)
        eq("round-trip id", loaded["id"], "999001")
        eq("round-trip hash", loaded["deck_hash"], payload["deck_hash"])
        eq("round-trip brief", loaded["brief"]["source"], "precon")
        eq("list ids", precon_bake.list_baked_ids(d), ["999001"])
        eq("missing id", precon_bake.read_bake("nope", d), None)

    # Live prices reattached without mutating the bake.
    priced = {
        "Sol Ring": {"name": "Sol Ring", "usd": "1.99"},
        "Island": {"name": "Island", "usd": "0.05"},
        cmdr["name"]: {"name": cmdr["name"], "usd": "3.25"},
    }
    fresh_deck, fresh_cmdr = precon_bake.apply_prices(
        payload["deck"], payload["commander_card"], priced)
    eq("deck price refreshed", fresh_deck[0]["usd"], "1.99")
    eq("basic price refreshed", fresh_deck[1]["usd"], "0.05")
    eq("commander price refreshed", fresh_cmdr["usd"], "3.25")
    eq("bake deck still priceless", "usd" in payload["deck"][0], False)

    resp = precon_bake.response_from_bake(
        payload, deck=fresh_deck, commander_card=fresh_cmdr, price=4.09,
        budget={"total": 4.09}, legal_report={
            "ok": True, "buyable": True, "violations": [], "banned": [],
            "mode": "tournament_legal"},
        archidekt="1 Malcolm\n1 Sol Ring", tcgplayer={"ok": True},
        card_pool="tournament_legal")
    eq("response marked baked", resp["baked"], True)
    eq("response has overview", resp["overview"]["strategy"], "Pirates")
    eq("response has brief", resp["brief"]["source"], "precon")
    eq("response brief strategy", resp["brief"]["strategy"], "Pirates")
    eq("response has signals", resp["signals"]["counts"]["wipes"], 0)
    eq("response has sim curves", len(resp["sim"]["curves"]), 1)


def test_sim_seed_reproducible():
    """fixed seed makes precon goldfish bit-identical across runs."""
    print("\nsim seed")
    deck = [
        {"name": "Sol Ring", "cmc": 1, "cost": "{1}", "type": "Artifact",
         "text": "{T}: Add {C}{C}.", "role": "ramp"},
        {"name": "Mind Stone", "cmc": 2, "cost": "{2}", "type": "Artifact",
         "text": "{T}: Add {C}.", "role": "ramp"},
        *[{"name": "Island", "cmc": 0, "cost": "", "type": "Basic Land — Island",
           "text": "{T}: Add {U}.", "role": "basic", "produced_mana": ["U"]}
          for _ in range(20)],
        *[{"name": f"Pirate {i}", "cmc": 2, "cost": "{1}{U}", "type": "Creature",
           "text": "Flying", "role": "theme"} for i in range(10)],
    ]
    a = overview.run_build_sim(deck, 4, runs=80, seed=102001)
    b = overview.run_build_sim(deck, 4, runs=80, seed=102001)
    c = overview.run_build_sim(deck, 4, runs=80, seed=102002)
    eq("same seed same curves", a["curves"], b["curves"])
    eq("same seed same diagnostics", a["diagnostics"], b["diagnostics"])
    eq("different seed can differ", a["curves"] != c["curves"] or
       a.get("good_hand") != c.get("good_hand"), True)
    # Global RNG restored after a seeded run — unseeded call still works.
    unseeded = overview.run_build_sim(deck, 4, runs=40)
    eq("unseeded returns curves", len(unseeded["curves"]) > 0, True)


async def test_load_precon_serves_bake():
    """fresh bake is served with no model call and no simulation."""
    print("\nprecon load serves bake")
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock, patch
    import httpx

    deck = [
        {"name": "Sol Ring", "role": "ramp", "cmc": 1, "cost": "{1}",
         "type": "Artifact", "text": "{T}: Add {C}{C}.",
         "color_identity": [], "legal": "legal", "keywords": [],
         "produced_mana": ["C"], "layout": "normal"},
        *[{"name": "Island", "role": "basic", "cmc": 0, "cost": "",
           "type": "Basic Land — Island", "text": "{T}: Add {U}.",
           "color_identity": ["U"], "legal": "legal", "keywords": [],
           "produced_mana": ["U"], "layout": "normal"} for _ in range(36)],
        *[{"name": f"Sailor {i}", "role": "theme", "cmc": 2, "cost": "{1}{U}",
           "type": "Creature — Human Pirate", "text": "Flying",
           "color_identity": ["U"], "legal": "legal", "keywords": ["Flying"],
           "produced_mana": [], "layout": "normal", "power": "2", "toughness": "2"}
          for i in range(63)],
    ]
    cmdr = {"name": "Malcolm, Keen-Eyed Navigator", "cmc": 4,
            "cost": "{2}{U}{U}", "type": "Legendary Creature — Siren Pirate",
            "text": "Flying", "color_identity": ["U"], "legal": "legal",
            "keywords": ["Flying"], "produced_mana": [], "layout": "normal",
            "power": "2", "toughness": "2"}
    payload = precon_bake.build_payload(
        precon_id="888001", name="Baked Buccaneers",
        source="https://archidekt.com/decks/888001",
        commander=cmdr["name"], commander_card=cmdr, deck=deck,
        signals={"strong": [{"text": "ramp", "counts": {"mana_rocks": 1}}],
                 "watch": [], "counts": {"mana_rocks": 1, "wipes": 0,
                                         "spot": 0, "draw": 0, "instants": 0,
                                         "counters": 0, "enchant_answers": 0,
                                         "land_ramp": 0, "avg_cmc": 2.0}},
        overview_data={"strategy": "Pirates make treasure",
                       "beats": [{"when": "Turns 1-3", "what": "Ramp"}],
                       "how_it_works": "Treasure.", "combos": [],
                       "pros": [], "cons": [], "roles": {}},
        sim_out={"runs": 1500,
                 "curves": [{"label": "Five mana", "note": "",
                             "points": [10] * 10, "median_turn": 4}],
                 "diagnostics": {"lands": 36, "mana_rocks": 1, "land_ramp": 0,
                                 "extra_drops": 0, "payoffs": 10, "avg_cmc": 2.0},
                 "assumptions": sim.ASSUMPTIONS},
    )

    async def fake_prices(deck_in, cmdr_in):
        priced_deck = [{**c, "usd": "1.00"} for c in deck_in]
        priced_cmdr = {**cmdr_in, "usd": "2.00"}
        return priced_deck, priced_cmdr

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        precon_bake.write_bake(payload, d)
        async def fake_upgrades(commander_card, deck, brief, *, card_pool="tournament_legal",
                                limit=8, way_playstyle=None):
            # added way_playstyle=; a stale stub TypeErrors, and
            # attach_precon_upgrade correctly returns unknown on that failure.
            # Status empty here means "looked, nothing to invent" — not unknown.
            return {"status": "empty", "cards": [],
                    "note": "test stub — no Scryfall"}

        with patch.object(precon_bake, "DATA_DIR", d), \
             patch.object(app, "refresh_card_prices", AsyncMock(side_effect=fake_prices)), \
             patch.object(app, "fetch_precon_upgrades",
                          AsyncMock(side_effect=fake_upgrades)), \
             patch.object(precons, "decklist_for_precon",
                          AsyncMock(side_effect=httpx.ConnectError("down"))), \
             patch.object(overview, "generate_overview",
                          AsyncMock(side_effect=AssertionError("model must not run"))), \
             patch.object(overview, "run_build_sim",
                          side_effect=AssertionError("sim must not run")):
            out = await app.load_precon("888001", {})
            eq("baked flag", out.get("baked"), True)
            eq("overview served", out["overview"]["strategy"],
               "Pirates make treasure")
            eq("brief served", (out.get("brief") or {}).get("source"), "precon")
            eq("brief strategy served",
               (out.get("brief") or {}).get("strategy"), "Pirates make treasure")
            eq("sim served", out["sim"]["curves"][0]["label"], "Five mana")
            eq("signals served", out["signals"]["counts"]["mana_rocks"], 1)
            eq("live price on card", out["deck"][0]["usd"], "1.00")
            eq("price total", out["price"] > 0, True)
            # ways stamped offline; upgrades from stub (not invented).
            eq("ways_to_play present",
               isinstance(out.get("ways_to_play"), list), True)
            eq("ways_status present",
               out.get("ways_status") in ("ok", "empty", "unknown"), True)
            eq("upgrades status from stub",
               (out.get("upgrades") or {}).get("status"), "empty")

        # Version bump → refuse bake; Archidekt down → 503, not stale numbers.
        stale = dict(payload, analysis_version=0)
        precon_bake.write_bake(stale, d)
        with patch.object(precon_bake, "DATA_DIR", d), \
             patch.object(precons, "decklist_for_precon",
                          AsyncMock(side_effect=httpx.ConnectError("down"))):
            resp = await app.load_precon("888001", {})
            eq("stale bake is 503", getattr(resp, "status_code", None), 503)


def test_import_double_faced():
    """Deckwright must be able to re-import its own export.

    Scryfall writes double-faced cards as "Front // Back" and every exporter,
    ours included, puts that whole string in the decklist — but
    /cards/collection only matches the front face and calls the combined form
    not-found. The Lyra deck this app wrote contains two such lines.
    """
    print("\nimport — double-faced names")
    from deckwright import import_deck as I
    for name, front in [
        ("Witch Enchanter // Witch-Blessed Meadow", "Witch Enchanter"),
        ("Ratchet, Field Medic // Ratchet, Rescue Racer", "Ratchet, Field Medic"),
        ("Sol Ring", "Sol Ring"),
    ]:
        got = name.split("//")[0].strip() if "//" in name else name
        eq(f"{name[:34]!r} asks for front face", got, front)
    parsed = I.parse_decklist("1 Witch Enchanter // Witch-Blessed Meadow")
    eq("parser keeps the full name", parsed["entries"][0]["name"],
       "Witch Enchanter // Witch-Blessed Meadow")


def test_substitution_price_band():
    """an alternate has to be affordable, not just castable.

    Matching on mana value alone offered Mana Vault ($118) and Chrome Mox
    ($165) as replacements for Sol Ring ($1.43) — all cheap to cast, all
    ruinous to buy. This feature exists for someone at a shop counter with a
    budget.
    """
    print("\nsubstitution — alternates stay in budget")
    sol = {"name": "Sol Ring", "cmc": 1, "usd": "1.43", "color_identity": [], "role": "ramp"}
    pool = [
        sol,
        {"name": "Mana Vault", "cmc": 1, "usd": "118.31", "color_identity": []},
        {"name": "Chrome Mox", "cmc": 0, "usd": "164.92", "color_identity": []},
        {"name": "Everflowing Chalice", "cmc": 0, "usd": "0.33", "color_identity": []},
        {"name": "Mind Stone", "cmc": 2, "usd": "0.38", "color_identity": []},
        {"name": "Unpriced Rock", "cmc": 1, "usd": None, "color_identity": []},
    ]
    alts = substitution.pick_alternates(sol, pool, {"Sol Ring"}, "W")
    names = [a["name"] for a in alts]
    for pricey in ("Mana Vault", "Chrome Mox"):
        eq(f"{pricey} excluded", pricey in names, False)
    eq("a cheap alternate survives", "Everflowing Chalice" in names, True)
    # A missing price is not evidence of an expensive card, but it ranks last.
    eq("unpriced allowed through", substitution._affordable(
        {"usd": None}, {"usd": "1.43"}), True)
    eq("a real step up is allowed", substitution._affordable(
        {"usd": "3.50"}, {"usd": "1.43"}), True)
    eq("a different league is not", substitution._affordable(
        {"usd": "118.00"}, {"usd": "1.43"}), False)


def test_christen_guard():
    """the primer is flavour writing, which is where the model is most
    tempted to name a card it remembers rather than one we fetched."""
    print("\nchristening — the one rule holds in prose")
    deck = {"Righteous Valkyrie", "Angelic Accord", "Well of Lost Dreams"}
    cmdr = "Lyra Dawnbringer"
    good = ("A good game is turn six, Righteous Valkyrie and Angelic Accord "
            "both online, every point I gain coming back as damage.")
    bad = "I lean on Righteous Valkyrie early, then Smothering Tithe pays for the turn."
    r = christen.verify({"names": ["A"], "theme": "t", "primer": good}, deck, cmdr)
    eq("real cards keep the primer", bool(r["primer"]), True)
    r = christen.verify({"names": ["A"], "theme": "t", "primer": bad}, deck, cmdr)
    eq("an invented card drops the primer", r["primer"], "")
    eq("and says which one", r.get("dropped_primer"), ["Smothering Tithe"])
    # Shortened commander references must not read as inventions.
    eq("'Lyra' alone is fine",
       christen.unverified_cards("Lyra swings for the win.", deck, cmdr), [])


def test_thesis_review():
    """nothing checked the layer that decides what \"working\" means.

    A thesis grading the wrong plan produces a simulation that faithfully
    measures the wrong thing and an overview that confidently explains it —
    output indistinguishable from a correct one.
    """
    print("\nthesis review — the layer nobody was checking")
    deck = [{"name": f"P{i}", "role": "plan"} for i in range(6)]
    deck += [{"name": f"L{i}", "role": "lands"} for i in range(6)]
    plan_names = [c["name"] for c in deck if c["role"] == "plan"]
    land_names = [c["name"] for c in deck if c["role"] == "lands"]
    on_plan = [{"check": {"payoffs_in_play": 2}}, {"check": {"commander_in_play": True}}]
    mixed = [{"points": [0, 0, 0, 0, 66]}, {"points": [0, 0, 0, 0, 82]}]

    eq("a sound thesis passes",
       thesis_check.review({"payoff_cards": plan_names, "conditions": on_plan},
                           mixed, deck)["sound"], True)
    eq("lands named as payoffs is caught",
       thesis_check.review({"payoff_cards": land_names, "conditions": on_plan},
                           mixed, deck)["sound"], False)
    eq("grading only the mana base is caught",
       thesis_check.review({"payoff_cards": plan_names,
                            "conditions": [{"check": {"lands_in_play": 4}},
                                           {"check": {"mana_available": 6}}]},
                           mixed, deck)["sound"], False)
    # Both ends of the spread measure nothing, for opposite reasons.
    for label, pts in (("everything above 90%", 99), ("nothing above 10%", 4)):
        eq(f"{label} is caught",
           thesis_check.review({"payoff_cards": plan_names, "conditions": on_plan},
                               [{"points": [0, 0, 0, 0, pts]}] * 2, deck)["sound"], False)
    # Two runs that ask different questions make every number less trustworthy.
    eq("an unstable thesis is caught", thesis_check.stability(
        {"conditions": [{"check": {"payoffs_in_play": 2}}]},
        {"conditions": [{"check": {"lands_in_play": 5}}]})["ok"], False)


def test_why_card():
    """why a card is here is the query that found it, read back."""
    print("\nwhy this card — provenance, not invention")
    eq("a creature type is the theme",
       why.explain("id<=W t:angel usd<=4.5", "theme"), "It's an angel — your theme.")
    eq("a keyword is what she asked for",
       why.explain("id<=UR keyword:flying", "theme"),
       "It has flying, which is what you asked for.")
    # Structural clauses are not reasons — the role heading already says it.
    for q in ("id<=W t:land -t:basic", "id<=W t:creature usd<=4.5", ""):
        eq(f"no hollow line for {q[:22]!r}", why.explain(q, "theme"), "")
    # Roles whose answer IS the role get no line at all.
    deck = [{"name": "A", "role": "spot"}, {"name": "B", "role": "theme"}]
    why.annotate(deck, {"spot": "id<=W o:destroy", "theme": "id<=W t:angel"})
    eq("removal gets no rationale", "why" in deck[0], False)
    eq("a theme card does", bool(deck[1].get("why")), True)


def test_deck_context():
    """one assembled record — why + conversation reach the ask prompt."""
    print("\ndeck_context()  — assembled record for ask")
    reason = "Tagged by the community as sacrifice outlet."
    wanted = "I want a commander that does the work itself"
    data = {
        "commander": "Syr Konrad, the Grim",
        "brief": {
            "theme": "mill",
            "angle": "death triggers",
            "strategy": "Mill and drain with Konrad on the field",
            "mechanics": ["mill", "sacrifice"],
        },
        "deck": [
            {"name": "Carrion Feeder", "role": "theme", "why": reason,
             "image": "https://example.com/x.jpg", "usd": "0.25"},
            {"name": "Sol Ring", "role": "ramp"},
            {"name": "Swamp", "role": "basic"},
        ],
        "conversation": [
            {"role": "user", "content": wanted},
            {"role": "assistant", "content": "Bracket and budget?"},
            {"role": "user", "content": "Bracket 2, around $100"},
        ],
        "overview": {"how_it_works": "Konrad drains when creatures die."},
        "signals": {"spot": 8, "wipes": 2},
        "notes": ["theme role came up short"],
        "substitutions": [{"name": "Carrion Feeder", "alts": ["Viscera Seer"]}],
        "simulation": {
            "curves": [{"label": "Konrad online", "points": [0, 10, 40, 60, 75]}],
        },
        "pressure": {"record": {"won": 3, "lost": 7}},
    }
    ctx = app.deck_context(data)
    eq("commander", ctx["commander"], "Syr Konrad, the Grim")
    eq("strategy kept", ctx["brief"]["strategy"],
       "Mill and drain with Konrad on the field")
    eq("mechanics kept", ctx["brief"]["mechanics"], ["mill", "sacrifice"])
    feeder = next(c for c in ctx["deck"] if c["name"] == "Carrion Feeder")
    eq("why rides with the card", feeder.get("why"), reason)
    eq("role rides with the card", feeder.get("role"), "theme")
    eq("UI fields stripped from context cards", "image" in feeder, False)
    eq("conversation kept verbatim", ctx["conversation"][0]["content"], wanted)
    eq("overview kept", ctx["overview"]["how_it_works"],
       "Konrad drains when creatures die.")
    eq("signals kept", ctx["signals"]["spot"], 8)
    eq("notes kept", ctx["notes"], ["theme role came up short"])
    eq("substitutions kept", ctx["substitutions"][0]["name"], "Carrion Feeder")
    eq("curves from simulation block", ctx["curves"][0]["label"], "Konrad online")
    eq("pressure kept", ctx["pressure"]["record"]["won"], 3)
    eq("nothing marked missing when present", ctx["missing"], [])

    # Predates conversation / pressure — missing, not invented.
    bare = app.deck_context({
        "commander": "Atraxa, Praetors' Voice",
        "deck": [{"name": "Sol Ring", "role": "ramp"}],
    })
    eq("bare has no conversation key", "conversation" in bare, False)
    eq("bare has no pressure key", "pressure" in bare, False)
    (ok if "conversation" in bare["missing"] and "pressure" in bare["missing"]
     else bad)("bare lists conversation+pressure as missing",
               f"missing={bare['missing']}")

    facts = app.ask_prompt_facts(ctx)
    (ok if f"Carrion Feeder [theme] — why: {reason}" in facts else bad)(
        "ask facts include recorded why", facts[facts.find("Carrion"):facts.find("Carrion")+120])
    (ok if wanted in facts else bad)("ask facts quote her interrogation", wanted)
    (ok if "INTERROGATION (her words, verbatim):" in facts else bad)(
        "interrogation block labeled", "")
    (ok if "PRESSURE TEST:" in facts and '"won": 3' in facts else bad)(
        "pressure test in facts", "")
    (ok if "BUILD NOTES:" in facts and "theme role came up short" in facts else bad)(
        "build notes in facts", "")

    bare_facts = app.ask_prompt_facts(bare)
    (ok if "INTERROGATION: not recorded on this deck" in bare_facts else bad)(
        "missing interrogation said so", "")
    (ok if "PRESSURE TEST: not recorded on this deck" in bare_facts else bad)(
        "missing pressure said so", "")
    (ok if "why:" not in bare_facts else bad)(
        "no invented why on bare cards", "")

    # Saved-deck path: id loads the durable file, not the client subset.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        saved = dict(data)
        app.persist_deck(saved, d)
        ctx2 = app.deck_context(app.load_saved_deck(saved["id"], d))
        eq("saved round-trip keeps why",
           next(c for c in ctx2["deck"] if c["name"] == "Carrion Feeder")["why"],
           reason)
        eq("saved round-trip keeps conversation",
           ctx2["conversation"][0]["content"], wanted)

        # Thin client payload + id → file wins for why/conversation; live
        # curves from the open page still overlay the stored baseline.
        thin = {
            "id": saved["id"],
            "commander": "Syr Konrad, the Grim",
            "deck": [{"name": "Carrion Feeder", "role": "theme"}],  # no why
            "brief": {},
            "curves": [{"label": "live", "points": [1, 2, 3, 4, 5]}],
        }
        merged = app.deck_record_for_ask(thin, d)
        ctx3 = app.deck_context(merged)
        eq("file why survives thin client deck",
           next(c for c in ctx3["deck"] if c["name"] == "Carrion Feeder")["why"],
           reason)
        eq("live curves overlay stored baseline",
           ctx3["curves"][0]["label"], "live")
        eq("file conversation survives thin client",
           ctx3["conversation"][0]["content"], wanted)

    # Prompt rules the model must see.
    (ok if "Recorded reasons outrank" in app.DECKTALK else bad)(
        "DECKTALK requires quoting recorded why", "")
    (ok if "not in the record is not known" in app.DECKTALK else bad)(
        "DECKTALK forbids inventing missing fields", "")


def test_sim_sees_card_draw():
    """A draw spell and a vanilla card of the same cost must not look identical."""
    print("\nsim — card draw is visible")
    drawer = {"name": "Drawer", "type": "Sorcery", "cmc": 2, "cost": "{1}{R}",
              "text": "Draw two cards."}
    vanilla = {"name": "Vanilla", "type": "Creature", "cmc": 2, "cost": "{1}{R}",
               "text": "Vanilla creature with no abilities."}
    eq("a draw spell reports its draws",
       sim.mechanics(drawer, set())["draws"], 2)
    eq("a vanilla card reports none",
       sim.mechanics(vanilla, set())["draws"], 0)

    # "target player draws" is black's draw suite. Goldfish: you choose
    # yourself. Scoring 0 made Arena-vs-Sign pass as 6>0 — wrong reason.
    sign = {"name": "Sign in Blood", "type": "Sorcery", "cmc": 2, "cost": "{B}{B}",
            "text": "Target player draws two cards and loses 2 life."}
    truths = {"name": "Painful Truths", "type": "Sorcery", "cmc": 4,
              "cost": "{2}{B}{B}",
              "text": "Target player draws X cards and loses X life."}
    ambition = {"name": "Ambition's Cost", "type": "Sorcery", "cmc": 4,
                "cost": "{3}{B}",
                "text": "You draw three cards and you lose 3 life."}
    hostile = {"name": "Force Draw", "type": "Sorcery", "cmc": 2, "cost": "{1}{U}",
               "text": "Target opponent draws two cards."}
    eq("Sign in Blood target-player draws=2",
       sim.mechanics(sign, set())["draws"], 2)
    # X: floor 1 — deliberate; magnitude is mana-dependent, not a regex miss.
    eq("Painful Truths X-draw floor=1",
       sim.mechanics(truths, set())["draws"], 1)
    eq("Ambition's Cost you-draw still 3",
       sim.mechanics(ambition, set())["draws"], 3)
    eq("target opponent draws is not yours",
       sim.mechanics(hostile, set())["draws"], 0)

    # And it must reach the game: a deck of drawers sees more cards than a
    # deck of vanillas, which is the whole point of teaching it.
    lands = [{"name": f"Mountain{i}", "type": "Basic Land", "cmc": 0, "cost": "",
              "text": "{T}: Add {R}.", "produced_mana": ["R"]} for i in range(40)]
    cond = [{"label": "drew five", "check": {"cards_drawn": 5}}]
    with_draw = lands + [dict(drawer, name=f"D{i}") for i in range(59)]
    without = lands + [dict(vanilla, name=f"V{i}") for i in range(59)]
    a = sim.run(with_draw, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    b = sim.run(without, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    (ok if a > b + 20 else bad)("a drawing deck draws more", f"{a}% vs {b}%")


def test_sim_sees_life_math():
    """drain ≠ lifelink; lifegain payoff ≠ one-shot gain; Vortex ≠ gain."""
    print("\nsim — lifegain / drain perception")
    artist = {"name": "Blood Artist", "type": "Creature", "cmc": 2, "cost": "{1}{B}",
              "text": "Whenever Blood Artist or another creature dies, target player "
                      "loses 1 life and you gain 1 life."}
    nighthawk = {"name": "Vampire Nighthawk", "type": "Creature", "cmc": 3,
                 "cost": "{1}{B}{B}", "text": "Flying, deathtouch, lifelink"}
    well = {"name": "Well of Lost Dreams", "type": "Artifact", "cmc": 4, "cost": "{4}",
            "text": "Whenever you gain life, you may pay {X}. If you do, draw X cards."}
    salve = {"name": "Healing Salve", "type": "Instant", "cmc": 1, "cost": "{W}",
             "text": "You gain 3 life."}
    exquisite = {"name": "Exquisite Blood", "type": "Enchantment", "cmc": 5,
                 "cost": "{4}{B}",
                 "text": "Whenever an opponent loses life, you gain that much life."}
    bond = {"name": "Sanguine Bond", "type": "Enchantment", "cmc": 5,
            "cost": "{3}{B}{B}",
            "text": "Whenever you gain life, target opponent loses that much life."}
    vortex = {"name": "Sulfuric Vortex", "type": "Enchantment", "cmc": 3,
              "cost": "{1}{R}{R}",
              "text": "At the beginning of each player's upkeep, Sulfuric Vortex deals "
                      "2 damage to that player. If a player would gain life, that player "
                      "gains no life instead."}
    eq("Artist drains", sim.mechanics(artist, set())["drain"] > 0, True)
    eq("Nighthawk does not drain", sim.mechanics(nighthawk, set())["drain"], 0)
    eq("Well is lifegain payoff", sim.mechanics(well, set())["lifegain_payoff"] > 0, True)
    eq("Salve is not payoff", sim.mechanics(salve, set())["lifegain_payoff"], 0)
    eq("Salve one-shot gains 3", sim.mechanics(salve, set())["life_gain"], 3)
    eq("Exquisite is not drain", sim.mechanics(exquisite, set())["drain"], 0)
    eq("Exquisite gain proxy", sim.mechanics(exquisite, set())["life_gain_proxy_per_turn"] > 0, True)
    eq("Bond drains", sim.mechanics(bond, set())["drain"] > 0, True)
    eq("Bond is payoff", sim.mechanics(bond, set())["lifegain_payoff"] > 0, True)
    eq("Vortex gains nothing", sim.mechanics(vortex, set())["life_gain"], 0)
    eq("Vortex not payoff", sim.mechanics(vortex, set())["lifegain_payoff"], 0)
    eq("life_gained is a thesis key", "life_gained" in sim.THESIS_KEYS, True)
    eq("life_drained is a thesis key", "life_drained" in sim.THESIS_KEYS, True)

    # Direction: Artist deck drains; blank does not. Zero shell is exact 0.
    lands = [{"name": f"Swamp{i}", "type": "Basic Land — Swamp", "cmc": 0, "cost": "",
              "text": "{T}: Add {B}.", "produced_mana": ["B"]} for i in range(36)]
    rocks = [{"name": f"Ring{i}", "type": "Artifact", "cmc": 1, "cost": "{1}",
              "text": "{T}: Add {C}{C}."} for i in range(6)]
    fill = [{"name": f"F{i}", "type": "Creature", "cmc": 1, "cost": "{B}", "text": ""}
            for i in range(49)]
    blank = {"name": "Blank", "type": "Enchantment", "cmc": 2, "cost": "{1}{B}", "text": ""}
    cond = [{"label": "drained 4", "check": {"life_drained": 4}}]
    with_a = lands + rocks + [dict(artist, name=f"A{i}") for i in range(8)] + fill
    with_b = lands + rocks + [dict(blank, name=f"B{i}") for i in range(8)] + fill
    zero = lands + rocks + fill + [{"name": f"Z{i}", "type": "Creature", "cmc": 1,
                                    "cost": "{B}", "text": ""} for i in range(8)]
    pa = sim.run(with_a, 3, set(), cond, runs=400, seed=121)["curves"][0]["points"][9]
    pb = sim.run(with_b, 3, set(), cond, runs=400, seed=121)["curves"][0]["points"][9]
    pz = sim.run(zero, 3, set(), cond, runs=200, seed=121)["curves"][0]["points"][9]
    (ok if pa > pb + 20 else bad)("Artist drains more than blank", f"{pa}% vs {pb}%")
    eq("zero life shell is exact 0", pz, 0.0)


def test_sim_sees_bodies():
    """A token maker and a lone creature of the same cost must differ."""
    print("\nsim — bodies on the battlefield are visible")
    lone = {"name": "Lone", "type": "Creature — Goblin", "cmc": 2, "cost": "{1}{R}",
            "text": "Vanilla creature."}
    maker = {"name": "Maker", "type": "Creature — Goblin", "cmc": 2, "cost": "{1}{R}",
             "text": "When Maker enters, create three 1/1 red Goblin creature tokens."}
    spell = {"name": "Spell", "type": "Sorcery", "cmc": 2, "cost": "{1}{R}",
             "text": "Create two 1/1 red Goblin creature tokens."}
    eq("a lone creature is one body", sim.mechanics(lone, set())["bodies"], 1)
    eq("a token maker counts its tokens", sim.mechanics(maker, set())["bodies"], 4)
    eq("a token spell is bodies without being a creature",
       sim.mechanics(spell, set())["bodies"], 2)
    eq("a land is no bodies",
       sim.mechanics({"name": "L", "type": "Land", "cmc": 0, "cost": "",
                      "text": "{T}: Add {R}."}, set())["bodies"], 0)

    lands = [{"name": f"Mountain{i}", "type": "Basic Land", "cmc": 0, "cost": "",
              "text": "{T}: Add {R}.", "produced_mana": ["R"]} for i in range(40)]
    cond = [{"label": "four bodies", "check": {"creatures_in_play": 4}}]
    wide = lands + [dict(maker, name=f"M{i}") for i in range(59)]
    thin = lands + [dict(lone, name=f"L{i}") for i in range(59)]
    a = sim.run(wide, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    b = sim.run(thin, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    (ok if a > b + 10 else bad)("a token deck gets wider faster", f"{a}% vs {b}%")


def test_sim_sees_token_types():
    """Treasure ≠ Soldier; Krenko X/{T} grows the board; Map does not."""
    print("\nsim — creature tokens vs resource tokens")
    krenko = {
        "name": "Krenko, Mob Boss", "type": "Creature — Goblin Warrior",
        "cmc": 4, "cost": "{2}{R}{R}",
        "text": "{T}: Create X 1/1 red Goblin creature tokens, where X is the "
                "number of Goblins you control.",
    }
    treasure_map = {
        "name": "Treasure Map", "type": "Artifact", "cmc": 2, "cost": "{2}",
        "text": "{1}, {T}: Scry 1. Then if there are three or more scry counters "
                "on Treasure Map, remove them and create three Treasure tokens.",
    }
    prize = {
        "name": "Pirate's Prize", "type": "Sorcery", "cmc": 2, "cost": "{1}{U}",
        "text": "Draw a card. Create a Treasure token.",
    }
    secure = {
        "name": "Secure the Wastes", "type": "Instant", "cmc": 1, "cost": "{X}{W}",
        "text": "Create X 1/1 white Warrior creature tokens.",
    }
    mk, mm, mp, ms = (sim.mechanics(c, set())
                      for c in (krenko, treasure_map, prize, secure))
    eq("Krenko creature_tokens > 0", mk["creature_tokens"] > 0, True)
    eq("Krenko resource_tokens == 0", mk["resource_tokens"], 0)
    eq("Map resource_tokens > 0", mm["resource_tokens"] > 0, True)
    eq("Map creature_tokens == 0", mm["creature_tokens"], 0)
    eq("Map does not fake bodies", mm["bodies"], 0)
    eq("Prize is resource not body", mp["resource_tokens"] > 0, True)
    eq("Prize bodies stay 0", mp["bodies"], 0)
    eq("Secure floors X to creature token", ms["creature_tokens"] >= 1, True)
    eq("Secure floors X into bodies", ms["bodies"] >= 1, True)
    (ok if mk["creature_tokens"] > mm["creature_tokens"] else bad)(
        "Krenko beats Map on creature_tokens",
        f"{mk['creature_tokens']} vs {mm['creature_tokens']}")

    # Enchantment filler: empty text is never cast, so it cannot fake board width.
    lands = [{"name": f"Mountain{i}", "type": "Basic Land", "cmc": 0, "cost": "",
              "text": "{T}: Add {R}.", "produced_mana": ["R"]} for i in range(36)]
    rocks = [{"name": f"Ring{i}", "type": "Artifact", "cmc": 1, "cost": "{1}",
              "text": "{T}: Add {C}{C}."} for i in range(6)]
    fill = [{"name": f"F{i}", "type": "Enchantment", "cmc": 1, "cost": "{R}", "text": ""}
            for i in range(49)]
    cheap = {"name": "Body", "type": "Creature — Goblin", "cmc": 1, "cost": "{R}",
             "text": ""}
    cond8 = [{"label": "eight bodies", "check": {"creatures_in_play": 8}}]
    with_k = lands + rocks + [dict(krenko, name=f"K{i}") for i in range(8)] + fill
    with_t = lands + rocks + [dict(treasure_map, name=f"T{i}") for i in range(8)] + fill
    with_c = lands + rocks + [dict(cheap, name=f"C{i}") for i in range(8)] + fill
    zero = lands + rocks + [{"name": f"Z{i}", "type": "Enchantment", "cmc": 2,
                             "cost": "{1}{R}", "text": ""} for i in range(8)] + fill
    pk = sim.run(with_k, 3, set(), cond8, runs=400, seed=122)["curves"][0]["points"][9]
    pt = sim.run(with_t, 3, set(), cond8, runs=400, seed=122)["curves"][0]["points"][9]
    pc = sim.run(with_c, 3, set(), cond8, runs=400, seed=122)["curves"][0]["points"][9]
    pz = sim.run(zero, 3, set(), cond8, runs=200, seed=122)["curves"][0]["points"][9]
    (ok if pk > pt + 15 else bad)(
        "Krenko widens creatures_in_play; Treasure Map does not",
        f"Krenko {pk}% vs Map {pt}%")
    (ok if pk > pc + 10 else bad)(
        "token engine beats cheap bodies at creatures_in_play>=8",
        f"Krenko {pk}% vs cheap {pc}%")
    eq("treasure-only shell is exact 0 at >=8", pt, 0.0)
    eq("blank shell is exact 0 at >=8", pz, 0.0)



def test_sim_sees_graveyard():
    """flashback ≠ one-shot; self-mill / gy_return / delirium visible."""
    print("\nsim — graveyard / flashback perception")
    looting = {
        "name": "Faithless Looting", "type": "Sorcery", "cmc": 1, "cost": "{R}",
        "keywords": ["Flashback"],
        "text": "Draw two cards, then discard two cards.\nFlashback {2}{R}",
    }
    burst = {
        "name": "Burst", "type": "Sorcery", "cmc": 1, "cost": "{R}",
        "text": "Draw two cards.",
    }
    millikin = {
        "name": "Millikin", "type": "Artifact Creature", "cmc": 2, "cost": "{2}",
        "text": "{T}: Mill a card.",
    }
    dread = {
        "name": "Dread Return", "type": "Sorcery", "cmc": 4, "cost": "{2}{B}{B}",
        "text": "Return target creature card from your graveyard to the battlefield.",
    }
    traverse = {
        "name": "Traverse the Ulvenwald", "type": "Sorcery", "cmc": 1, "cost": "{G}",
        "text": "Delirium — If there are four or more card types among cards in "
                "your graveyard, instead search your library for a card.",
    }
    ml, mb = sim.mechanics(looting, set()), sim.mechanics(burst, set())
    eq("Looting has flashback", ml["flashback"] > 0, True)
    eq("Burst has no flashback", mb["flashback"], 0)
    eq("Looting queues flashback_draws", ml["flashback_draws"], 2)
    eq("Burst flashback_draws zero", mb["flashback_draws"], 0)
    (ok if ml["flashback"] > mb["flashback"] else bad)(
        "Looting beats Burst on flashback",
        f"{ml['flashback']} vs {mb['flashback']}")
    eq("Millikin self_mill", sim.mechanics(millikin, set())["self_mill"], 1)
    eq("Dread Return gy_return", sim.mechanics(dread, set())["gy_return"], 1)
    eq("Traverse delirium", sim.mechanics(traverse, set())["delirium"] > 0, True)
    # No new THESIS_KEY without Box — flashback second cast feeds cards_drawn.
    eq("no gy_card_types thesis key", "gy_card_types" in sim.THESIS_KEYS, False)
    eq("no cards_in_graveyard thesis key", "cards_in_graveyard" in sim.THESIS_KEYS, False)

    lands = [{"name": f"Mountain{i}", "type": "Basic Land", "cmc": 0, "cost": "",
              "text": "{T}: Add {R}.", "produced_mana": ["R"]} for i in range(36)]
    rocks = [{"name": f"Ring{i}", "type": "Artifact", "cmc": 1, "cost": "{1}",
              "text": "{T}: Add {C}{C}."} for i in range(6)]
    fill = [{"name": f"F{i}", "type": "Enchantment", "cmc": 1, "cost": "{R}", "text": ""}
            for i in range(49)]
    cond = [{"label": "drew 4", "check": {"cards_drawn": 4}}]
    with_fb = lands + rocks + [dict(looting, name=f"L{i}") for i in range(8)] + fill
    with_os = lands + rocks + [dict(burst, name=f"B{i}") for i in range(8)] + fill
    zero = lands + rocks + [{"name": f"Z{i}", "type": "Enchantment", "cmc": 2,
                             "cost": "{1}{R}", "text": ""} for i in range(8)] + fill
    pf = sim.run(with_fb, 3, set(), cond, runs=400, seed=123)["curves"][0]["points"][9]
    po = sim.run(with_os, 3, set(), cond, runs=400, seed=123)["curves"][0]["points"][9]
    pz = sim.run(zero, 3, set(), cond, runs=200, seed=123)["curves"][0]["points"][9]
    (ok if pf > po + 15 else bad)(
        "flashback lifts cards_drawn more than one-shot draw-2",
        f"Looting {pf}% vs Burst {po}%")
    eq("blank shell exact 0 at cards_drawn>=4", pz, 0.0)


def test_sim_sees_anthems():
    """An anthem is worth nothing alone and a lot beside bodies."""
    print("\nsim — anthems scale with the board")
    anthem = {"name": "Anthem", "type": "Enchantment", "cmc": 2, "cost": "{1}{R}",
              "text": "Creatures you control get +1/+1."}
    lone = {"name": "Lone", "type": "Creature — Goblin", "cmc": 2, "cost": "{1}{R}",
            "text": "Vanilla creature."}
    eq("an anthem reports its bonus", sim.mechanics(anthem, set())["anthem"], 1)
    eq("a creature reports none", sim.mechanics(lone, set())["anthem"], 0)

    lands = [{"name": f"Mountain{i}", "type": "Basic Land", "cmc": 0, "cost": "",
              "text": "{T}: Add {R}.", "produced_mana": ["R"]} for i in range(40)]
    cond = [{"label": "board power eight", "check": {"board_power": 8}}]
    # Same body count both sides; only the anthems differ.
    creatures = [dict(lone, name=f"C{i}") for i in range(49)]
    with_anthems = lands + creatures + [dict(anthem, name=f"A{i}") for i in range(10)]
    without = lands + creatures + [dict(lone, name=f"X{i}") for i in range(10)]
    a = sim.run(with_anthems, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    b = sim.run(without, 3, set(), cond, runs=400)["curves"][0]["points"][4]
    (ok if a > b else bad)("anthems raise board power", f"{a}% vs {b}%")
def test_card_score_marginal():
    """same candidate, different marginal lift in different decks."""
    print("\ncard_score — marginal sim lift")
    random.seed(7)

    def lands(n):
        return [{"name": f"L{i}", "type": "Land", "cmc": 0,
                 "text": "{T}: Add {G}.", "produced_mana": ["G"], "role": "lands"}
                for i in range(n)]

    def rock(name="Rock"):
        return {"name": name, "type": "Artifact", "cmc": 2,
                "text": "{T}: Add one mana of any color.", "role": "ramp"}

    def payoff(name):
        return {"name": name, "type": "Creature", "cmc": 4,
                "text": "Big creature.", "role": "theme"}

    # Score against payoffs — harder floors keep headroom at TARGET_TURN=10
    #; "3 payoffs" saturates by T10 on these scaffolds.
    conds = [
        {"label": "Commander out", "check": {"commander_in_play": True}},
        {"label": "5 payoffs", "check": {"payoffs_in_play": 5}},
        {"label": "7 payoffs", "check": {"payoffs_in_play": 7}},
    ]
    lean = lands(30) + [payoff(f"V{i}") for i in range(29)]
    heavy = lands(30) + [rock(f"R{i}") for i in range(8)] + [payoff(f"V{i}") for i in range(21)]
    swap = 30
    lean[swap] = payoff("placeholder")
    heavy[swap] = payoff("placeholder")
    candidate = rock("Test Rock")

    lean_scored, lean_target = card_score.score_candidates_sync(
        lean, swap, [candidate], "ramp", 5, conditions=conds, runs=800)
    heavy_scored, heavy_target = card_score.score_candidates_sync(
        heavy, swap, [candidate], "ramp", 5, conditions=conds, runs=800)
    (ok if lean_target and heavy_target else bad)(
        "both decks have a measurable scoring target",
        f"lean={lean_target}, heavy={heavy_target}")
    d_lean = lean_scored[0]["delta_pct"]
    d_heavy = heavy_scored[0]["delta_pct"]
    (ok if d_lean != d_heavy else bad)(
        "same candidate ranks differently in different decks",
        f"lean +{d_lean}% vs heavy +{d_heavy}%")
    (ok if d_lean != 0 or d_heavy != 0 else bad)(
        "at least one deck shows measurable lift", f"lean={d_lean}%, heavy={d_heavy}%")


def test_card_score_skips_saturated():
    """saturated baselines skip scoring — no +0.0 sim_pick notes."""
    print("\ncard_score — skip saturated conditions")
    random.seed(3)

    def lands(n):
        return [{"name": f"L{i}", "type": "Land", "cmc": 0,
                 "text": "{T}: Add {G}.", "produced_mana": ["G"], "role": "lands"}
                for i in range(n)]

    def rock(name):
        return {"name": name, "type": "Artifact", "cmc": 2,
                "text": "{T}: Add one mana of any color.", "role": "ramp"}

    # 30 lands + 10 rocks — Five mana is saturated at ~100%.
    deck = lands(30) + [rock(f"R{i}") for i in range(10)]
    conds = [
        {"label": "Five mana", "check": {"mana_available": 5}},
        {"label": "Commander out", "check": {"commander_in_play": True}},
    ]
    payoffs = overview.payoff_names_from_deck(deck)
    baseline = sim.run(deck, 5, payoffs, conds, runs=600)
    target = card_score.pick_scoring_target(baseline, conds)
    eq("saturated deck has no scoring target", target, None)

    scored, scoring = card_score.score_candidates_sync(
        deck, 30, [rock("Extra")], "ramp", 5, conditions=conds, runs=400)
    eq("scoring skipped returns no target", scoring, None)
    eq("skipped scoring leaves model order", scored[0]["card"]["name"], "Extra")


def test_card_score_provisional_conditions():
    """strategy prose shapes which conditions get scored."""
    print("\ncard_score — provisional conditions from strategy")
    aggro = {"strategy": "Low-to-the-ground aggro with cheap creatures and refill your hand.",
             "mechanics": ["treasure"]}
    bombs = {"strategy": "Ramp into seven-drop haymakers. Hold cards and draw two.",
             "mechanics": ["ramp"]}
    aggro_labels = [c["label"] for c in card_score.provisional_conditions(aggro)]
    bomb_labels = [c["label"] for c in card_score.provisional_conditions(bombs)]
    (ok if "Full hand" in aggro_labels else bad)("aggro gets hand condition", aggro_labels)
    (ok if "Seven mana" in bomb_labels else bad)("haymaker gets seven mana", bomb_labels)
    (ok if aggro_labels != bomb_labels else bad)(
        "different strategies get different conditions", f"{aggro_labels} vs {bomb_labels}")


def test_card_score_zero_delta_preserves_model_order():
    """interaction cards that score 0 are not demoted below model picks."""
    print("\ncard_score — zero-score cards keep model order")
    picks = [
        {"name": "First Pick", "type": "Instant", "cmc": 2,
         "text": "Destroy target creature.", "role": "spot"},
        {"name": "Second Pick", "type": "Instant", "cmc": 2,
         "text": "Exile target creature.", "role": "spot"},
    ]
    model_rank = {c["name"]: i for i, c in enumerate(picks)}
    rows = [{"card": c, "delta_pct": 0.0} for c in picks]
    rows.sort(key=lambda r: (-r["delta_pct"], model_rank.get(r["card"]["name"], 999)))
    eq("first model pick wins tie at zero delta", rows[0]["card"]["name"], "First Pick")


def test_card_score_sim_pick_note():
    """chosen cards carry a measurable claim on the response."""
    print("\ncard_score — sim_pick metadata")
    meta = {
        "Sol Ring": {
            "delta_pct": 4.2,
            "condition": "3 payoffs",
            "turn": card_score.TARGET_TURN,
            "baseline_pct": 61.0,
            "candidates_scored": 12,
            "note": (
                f'Cut from 12 candidates; +4.2% to "3 payoffs" '
                f'by turn {card_score.TARGET_TURN}'
            ),
        }
    }
    deck = [{"name": "Sol Ring", "role": "ramp"}]
    card_score.stamp_sim_pick(deck, meta)
    sp = deck[0].get("sim_pick") or {}
    eq("sim_pick.delta_pct", sp.get("delta_pct"), 4.2)
    eq("sim_pick.note names condition", "3 payoffs" in sp.get("note", ""), True)


async def test_card_score_derive_failed_reported():
    """plan derive failure is visible in score_info, not silent fallback."""
    print("\ncard_score — plan derive failure is reported")
    from unittest.mock import patch
    from deckwright import plan_graph

    cmdr = {
        "name": "Karlov of the Ghost Council",
        "type": "Legendary Creature — Spirit Advisor", "cmc": 5,
        "oracle_text": "Whenever you gain life, Karlov gets +1/+1.",
        "text": "Whenever you gain life, Karlov gets +1/+1.",
        "color_identity": ["W", "B"],
    }
    picks = [{
        "name": "Soul Warden", "type": "Creature — Human Cleric", "cmc": 1,
        "text": "Whenever another creature enters, you gain 1 life.",
        "oracle_text": "Whenever another creature enters, you gain 1 life.",
        "color_identity": ["W"],
    }]
    deck = [{
        "name": f"L{i}", "type": "Basic Land — Plains", "cmc": 0,
        "text": "{T}: Add {W}.", "produced_mana": ["W"], "role": "lands",
        "oracle_text": "{T}: Add {W}.", "color_identity": ["W"],
    } for i in range(36)]
    with patch.object(plan_graph, "derive", side_effect=RuntimeError("boom")):
        _chosen, _meta, _ms, info = await card_score.rank_for_role(
            deck, picks, "theme", 1, picks, 5, cmdr, "WB",
            {"theme": picks, "lands": deck}, [("theme", 25, "Theme")], set(),
            brief={"theme": "lifegain", "strategy": "gain life"},
            runs=40,
        )
    eq("derive failure is reported", info.get("derive_failed"), True)


async def test_partners_unknown_not_empty():
    """Scryfall failure on partner lookup is unknown, not a real zero."""
    print("\npartners — Scryfall failure is unknown")
    from unittest.mock import AsyncMock, patch

    thrasios = _card(
        "Thrasios, Triton Hero", typ="Legendary Creature — Merfolk Wizard",
        ci=["U", "G"], text="Partner\n{4}: Scry 1.", role="commander")
    thrasios["keywords"] = ["Partner"]
    fail = AsyncMock(side_effect=app.ScryfallError(0, "transport: down"))
    with patch.object(app, "search_ladder", fail):
        got = await app.fetch_partner_options(thrasios)
    eq("status is unknown", got.get("status"), "unknown")
    eq("partners empty", got.get("partners"), [])
    eq("note explains unknown", "not inventing" in (got.get("note") or "").lower(), True)


async def test_card_score_gate_failed_open_reported():
    """orphan-gate fail-open is visible in score_info, not only stdout."""
    print("\ncard_score — synergy gate failed-open is reported")
    from unittest.mock import patch
    from deckwright import synergy as syn

    cmdr = {
        "name": "Cmdr", "type": "Legendary Creature — Elf", "cmc": 3,
        "oracle_text": "Whenever Cmdr enters, sacrifice another creature.",
        "text": "Whenever Cmdr enters, sacrifice another creature.",
        "color_identity": ["G"],
    }
    picks = [{
        "name": "Shock", "type": "Instant", "cmc": 1,
        "text": "Shock deals 2 damage to any target.",
        "oracle_text": "Shock deals 2 damage to any target.",
        "color_identity": ["R"],
    }]
    deck = [{
        "name": f"L{i}", "type": "Basic Land — Forest", "cmc": 0,
        "text": "{T}: Add {G}.", "produced_mana": ["G"], "role": "lands",
        "oracle_text": "{T}: Add {G}.", "color_identity": ["G"],
    } for i in range(36)]
    with patch.object(syn, "eligible", return_value=False):
        _chosen, _meta, _ms, info = await card_score.rank_for_role(
            deck, picks, "theme", 1, picks, 3, cmdr, "G",
            {"theme": picks, "lands": deck}, [("theme", 25, "Theme")], set(),
            brief={"theme": "elves", "strategy": "go wide"},
            runs=40,
        )
    eq("gate failed open is reported", info.get("synergy_gate_failed_open"), True)
    eq("failed open did not claim drops", info.get("synergy_dropped"), 0)


async def test_card_score_skip_prefers_glued():
    """when sim scoring is skipped, edged cards still beat orphans."""
    print("\ncard_score — skipped scoring still soft-prefers glued cards")
    from unittest.mock import patch

    cmdr = {
        "name": "Hei Bai, Spirit of Balance",
        "type": "Legendary Creature — Bear Spirit", "cmc": 3,
        "oracle_text": (
            "Whenever Hei Bai enters or attacks, you may sacrifice another "
            "creature or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
            "When Hei Bai leaves the battlefield, put its counters on target "
            "creature you control."
        ),
        "text": (
            "Whenever Hei Bai enters or attacks, you may sacrifice another "
            "creature or artifact. If you do, put two +1/+1 counters on Hei Bai.\n"
            "When Hei Bai leaves the battlefield, put its counters on target "
            "creature you control."
        ),
        "color_identity": ["W", "B"],
    }
    shock = {
        "name": "Shock", "type": "Instant", "cmc": 1,
        "text": "Shock deals 2 damage to any target.",
        "oracle_text": "Shock deals 2 damage to any target.",
        "color_identity": ["R"],
    }
    blink = {
        "name": "Cloudshift", "type": "Instant", "cmc": 1,
        "text": ("Exile target creature you control, then return that card "
                 "to the battlefield under your control."),
        "oracle_text": ("Exile target creature you control, then return that "
                        "card to the battlefield under your control."),
        "color_identity": ["W"],
    }
    # Orphan first in model order — steer must promote Cloudshift.
    picks = [shock, blink]
    deck = [{
        "name": f"L{i}", "type": "Basic Land — Plains", "cmc": 0,
        "text": "{T}: Add {W}.", "produced_mana": ["W"], "role": "lands",
        "oracle_text": "{T}: Add {W}.", "color_identity": ["W"],
    } for i in range(36)]
    with patch.object(card_score, "pick_scoring_target", return_value=None):
        chosen, _meta, _ms, info = await card_score.rank_for_role(
            deck, picks, "plan", 1, picks, 3, cmdr, "WB",
            {"plan": picks, "lands": deck}, [("plan", 10, "Plan")], set(),
            brief={"theme": "spirits", "strategy": "blink and sacrifice"},
            runs=40,
        )
    eq("skipped path reported", info.get("skipped"), True)
    eq("cohesion_steer flagged", info.get("cohesion_steer"), True)
    eq("glued card wins skip ordering",
       (chosen[0] or {}).get("name"), "Cloudshift")


def test_enforce_payoff_floors():
    """an archetype-extra card that scores zero still reaches the deck.

    Burst loop halves (Exquisite Blood) score 0 in a goldfish — no opponent to
    lose life — and lose a 249-wide theme contest. The plan's min on the
    archetype search is a floor; honour it the way type floors are honoured.
    """
    print("\nenforce_payoff_floors  — zero-score archetype extra")
    # Fifteen zero-score theme picks the scorer preferred. None is the loop piece.
    chosen = [_card(f"Generic{i}", typ="Enchantment",
                    text="At the beginning of your upkeep, you gain 1 life.")
              for i in range(15)]
    loop = _card(
        "Exquisite Blood", typ="Enchantment", usd="36.52",
        text="Whenever an opponent loses life, you gain that much life.")
    bond = _card(
        "Sanguine Bond", typ="Enchantment", usd="2.00",
        text="Whenever you gain life, target opponent loses that much life.")
    pool = list(chosen) + [loop, bond] + [
        _card("Bloodthirsty Conqueror", typ="Creature — Vampire Knight", usd="35.79",
              text="Whenever an opponent loses life, you gain that much life."),
        _card("Filler Spell", typ="Instant", text="Draw a card."),
    ]
    # Two separate floors — Bond half alone must not satisfy the Exquisite half.
    # A single OR floor with min:6 was the archetype-search fix miss that let Bond clones
    # fill the quota and drop the card that closes the loop.
    floors = [
        {"id": "archetype_burst", "min": 6,
         "names": {"Sanguine Bond"}},
        {"id": "archetype_burst", "min": 6,
         "names": {"Exquisite Blood", "Bloodthirsty Conqueror"}},
    ]
    fixed = app.enforce_payoff_floors(chosen, pool, floors, 15)
    eq("still 15 cards", len(fixed), 15)
    names = {c["name"] for c in fixed}
    (ok if "Exquisite Blood" in names else bad)(
        "zero-score loop piece reserved into the deck", sorted(names))
    (ok if "Bloodthirsty Conqueror" in names else bad)(
        "second available loop piece also reserved", sorted(names))
    (ok if "Sanguine Bond" in names else bad)(
        "Bond half reserved on its own floor", sorted(names))
    eq("does not invent outside the pool",
       names <= {c["name"] for c in pool}, True)
    # Empty floors leave ranking alone.
    eq("no floors: unchanged",
       [c["name"] for c in app.enforce_payoff_floors(chosen, pool, [], 15)],
       [c["name"] for c in chosen])
    # Never invents a name that is not in the pool.
    ghost = app.enforce_payoff_floors(
        chosen, pool,
        [{"id": "archetype_burst", "min": 6, "names": {"Not A Real Card"}}], 15)
    eq("unknown name not invented",
       "Not A Real Card" in {c["name"] for c in ghost}, False)


def test_enforce_tribe_floor():
    """payoff pool/floors must not erase the named creature type.

    Angels+lifegain found the tribe at commander select, then filled theme with
    behaviour payoffs (`o:"whenever you gain life"`) that are not angels. The
    vampire twin kept its tribe because drain payoffs *are* vampires. Honour a
    tribe floor the way the archetype payoff floors honours archetype floors — after payoffs, without
    inventing cards, without stealing protected loop pieces.
    """
    print("\nenforce_tribe_floor  — tribe survives payoff reservation")
    eq("angel theme → angel",
       app.theme_creature_type({"theme": "angels lifegain"}), "angel")
    eq("vampire theme → vampire",
       app.theme_creature_type({"theme": "vampires that gain life"}), "vampire")
    eq("behaviour-only theme → none",
       app.theme_creature_type({"theme": "lifegain"}), None)
    eq("floor min for 15 theme slots", app.tribe_floor_min(15), 5)
    eq("floor min caps at target", app.tribe_floor_min(3), 3)

    # Fifteen lifegain payoffs the scorer preferred — none is an Angel.
    chosen = [
        _card(f"Drain{i}", typ="Enchantment",
              text="Whenever you gain life, target opponent loses that much life.")
        for i in range(15)
    ]
    angels = [
        _card("Lyra Dawnbringer", typ="Legendary Creature — Angel",
              text="Flying, first strike, lifelink"),
        _card("Giada, Font of Hope", typ="Legendary Creature — Angel",
              text="Flying, vigilance. Each Angel gets +1/+1 for each other Angel."),
        _card("Angel of Vitality", typ="Creature — Angel",
              text="Flying. If you would gain life, you gain that much life plus 1."),
        _card("Bishop of Wings", typ="Creature — Human Cleric",
              text="Whenever an Angel enters, you gain 4 life."),
        _card("Resplendent Angel", typ="Creature — Angel",
              text="Flying. At end of combat, if you gained 5 life, create a 4/4 Angel."),
        _card("Righteous Valkyrie", typ="Creature — Angel Cleric",
              text="Flying. Whenever you gain life, put a +1/+1 counter on this."),
    ]
    loop = _card(
        "Exquisite Blood", typ="Enchantment",
        text="Whenever an opponent loses life, you gain that much life.")
    pool = list(chosen) + angels + [loop]

    # Payoff floor first, then tribe — protected loop piece stays.
    after_payoff = app.enforce_payoff_floors(
        chosen, pool,
        [{"id": "archetype_burst", "min": 1, "names": {"Exquisite Blood"}}], 15)
    protected = {c["name"] for c in after_payoff if c["name"] == "Exquisite Blood"}
    fixed = app.enforce_tribe_floor(
        after_payoff, pool, "angel", app.tribe_floor_min(15), 15,
        protected=protected)
    eq("still 15 cards", len(fixed), 15)
    names = {c["name"] for c in fixed}
    angel_names = {c["name"] for c in angels if "Angel" in c["type"]}
    n_angels = len(names & angel_names)
    (ok if n_angels >= 5 else bad)(
        f"≥5 angels reserved into theme (got {n_angels})", sorted(names))
    (ok if "Exquisite Blood" in names else bad)(
        "protected payoff floor card not stolen by tribe", sorted(names))
    eq("does not invent outside the pool",
       names <= {c["name"] for c in pool}, True)
    # Bishop of Wings is not an Angel — must not count toward the floor.
    eq("non-angel cleric is not a tribe hit",
       app.card_has_creature_type(
           _card("Bishop of Wings", typ="Creature — Human Cleric"), "angel"),
       False)
    # No tribe / empty floor leaves ranking alone.
    eq("no tribe: unchanged",
       [c["name"] for c in app.enforce_tribe_floor(chosen, pool, None, 5, 15)],
       [c["name"] for c in chosen])




def test_brief_extract_states():
    """brief present · still asking · extraction failed — never silent null."""
    print("\nextract_brief_payload()  — three states, no swallowed parse")

    good = (
        '{"reply": "Pirates that steal treasure — sound right?", '
        '"still_asking": false, '
        '"brief": {"theme": "pirate", "angle": "treasure tokens", '
        '"colors": "", "mechanics": ["treasure"], "budget_total": 150, '
        '"bracket": 2, "commander_query": "is:commander t:pirate", '
        '"flavor_terms": ["heist"], "fantasy": "board of pirates swinging", '
        '"playstyle": "proactive", "strategy_shape": "aggro", '
        '"commander_name": "", "table": "kitchen table"}}'
    )
    got = app.extract_brief_payload(good)
    eq("brief present → still_asking false", got["still_asking"], False)
    eq("brief present → no error", got["brief_error"], None)
    eq("brief present → theme", (got["brief"] or {}).get("theme"), "pirate")
    eq("brief present → strategy_shape",
       (got["brief"] or {}).get("strategy_shape"), "aggro")
    eq("brief present → prose from reply", got["text"].startswith("Pirates"), True)

    asking = (
        '{"reply": "What does a turn look like when it is going well?", '
        '"still_asking": true, "brief": null}'
    )
    got = app.extract_brief_payload(asking)
    eq("still asking → brief null", got["brief"], None)
    eq("still asking → still_asking true", got["still_asking"], True)
    eq("still asking → no error", got["brief_error"], None)

    # Deliberately malformed — must surface as error, never brief:null alone.
    broken = (
        'Sounds good.\n\n```brief\n{"theme": "pirate", "angle": "x", '
        '"colors": "UB",\n```'
    )
    got = app.extract_brief_payload(broken)
    eq("malformed fence → brief null", got["brief"], None)
    eq("malformed fence → not still_asking", got["still_asking"], False)
    eq("malformed fence → brief_error", got["brief_error"], "unparseable")
    (ok if got["text"].startswith("Sounds good") else bad)(
        "malformed fence keeps the prose", repr(got["text"][:40]))

    # Prose only (legacy CLI mid-interrogation) is still asking, not a failure.
    got = app.extract_brief_payload(
        "Tell me more about the board you want to see.")
    eq("prose only → still asking", got["still_asking"], True)
    eq("prose only → no error", got["brief_error"], None)
    eq("prose only → no brief", got["brief"], None)

    # Ready flag with null brief is a failure, not "still asking".
    hollow = '{"reply": "All set.", "still_asking": false, "brief": null}'
    got = app.extract_brief_payload(hollow)
    eq("ready+null brief → error", got["brief_error"], "missing_brief")
    eq("ready+null brief → not still_asking", got["still_asking"], False)

    # Truncation is named, not scored as zero.
    got = app.extract_brief_payload(
        '{"reply": "Almost-", "still_asking": false, "brief": {"theme":',
        stop_reason="max_tokens")
    eq("truncated → brief_error", got["brief_error"], "truncated")
    eq("truncated → not still_asking", got["still_asking"], False)

    # Legacy happy path still works (CLI that still emits the fence).
    legacy = (
        'Pirates it is.\n\n```brief\n'
        '{"theme": "pirate", "angle": "steal", "colors": "UB", '
        '"mechanics": [], "budget_total": 100, "bracket": 2, '
        '"commander_query": "is:commander t:pirate", "flavor_terms": [], '
        '"fantasy": "", "playstyle": "proactive", "commander_name": "", '
        '"table": ""}\n```'
    )
    got = app.extract_brief_payload(legacy)
    eq("legacy fence → theme", (got["brief"] or {}).get("theme"), "pirate")
    eq("legacy fence → no error", got["brief_error"], None)
    eq("legacy fence → still_asking false", got["still_asking"], False)


    # CLI sometimes emits prose then a trailing envelope — peel it.
    prose_then_asking = (
        "What colors are you thinking?\n\n"
        '{"reply": "What colors are you thinking?", '
        '"still_asking": true, "brief": null}'
    )
    got = app.extract_brief_payload(prose_then_asking)
    eq("prose+envelope asking → text is reply only", "{" not in got["text"], True)
    eq("prose+envelope asking → still_asking", got["still_asking"], True)
    eq("prose+envelope asking → no error", got["brief_error"], None)
    eq("prose+envelope asking → no brief", got["brief"], None)
    eq("prose+envelope asking → reply text",
       got["text"], "What colors are you thinking?")

    prose_then_brief = (
        "Pirates that steal treasure — sound right?\n\n"
        '{"reply": "Pirates that steal treasure — sound right?", '
        '"still_asking": false, '
        '"brief": {"theme": "pirate", "angle": "treasure tokens", '
        '"colors": "", "mechanics": ["treasure"], "budget_total": 150, '
        '"bracket": 2, "commander_query": "is:commander t:pirate", '
        '"flavor_terms": ["heist"], "fantasy": "board of pirates swinging", '
        '"playstyle": "proactive", "strategy_shape": "aggro", '
        '"commander_name": "", "table": "kitchen table"}}'
    )
    got = app.extract_brief_payload(prose_then_brief)
    eq("prose+envelope brief → text is reply only", "{" not in got["text"], True)
    eq("prose+envelope brief → still_asking false", got["still_asking"], False)
    eq("prose+envelope brief → no error", got["brief_error"], None)
    eq("prose+envelope brief → theme", (got["brief"] or {}).get("theme"), "pirate")

    # prose then a fenced envelope — peel must accept the closing ```
    # and recover still_asking / brief. Prompt already forbids fences; model
    # fences anyway.
    fenced_json = (
        "crew needs some teeth to protect itself or can just be pure beatdown.\n\n"
        "```json\n"
        '{"reply": "Which feels right — teeth or pure beatdown?", '
        '"still_asking": true, "brief": null}\n'
        "```"
    )
    got = app.extract_brief_payload(fenced_json)
    eq("prose+```json envelope → no fence in text", "```" not in got["text"], True)
    eq("prose+```json envelope → no raw JSON in text", "{" not in got["text"], True)
    eq("prose+```json envelope → still_asking", got["still_asking"], True)
    eq("prose+```json envelope → no error", got["brief_error"], None)
    eq("prose+```json envelope → reply text",
       got["text"], "Which feels right — teeth or pure beatdown?")

    fenced_bare = (
        "What colors are you thinking?\n\n"
        "```\n"
        '{"reply": "What colors are you thinking?", '
        '"still_asking": true, "brief": null}\n'
        "```"
    )
    got = app.extract_brief_payload(fenced_bare)
    eq("prose+``` envelope → peeled", "```" not in got["text"], True)
    eq("prose+``` envelope → still_asking", got["still_asking"], True)
    eq("prose+``` envelope → reply text",
       got["text"], "What colors are you thinking?")

    spaced_key = (
        "Sound right?\n\n"
        '{ "reply": "Sound right?", "still_asking": true, "brief": null}'
    )
    got = app.extract_brief_payload(spaced_key)
    eq("{ \"reply\" spaced → peeled", "{" not in got["text"], True)
    eq("{ \"reply\" spaced → still_asking", got["still_asking"], True)
    eq("{ \"reply\" spaced → reply text", got["text"], "Sound right?")

    fenced_json_brief = (
        "Pirates that steal treasure — sound right?\n\n"
        "```json\n"
        '{"reply": "Pirates that steal treasure — sound right?", '
        '"still_asking": false, '
        '"brief": {"theme": "pirate", "angle": "treasure tokens", '
        '"colors": "", "mechanics": ["treasure"], "budget_total": 150, '
        '"bracket": 2, "commander_query": "is:commander t:pirate", '
        '"flavor_terms": ["heist"], "fantasy": "board of pirates swinging", '
        '"playstyle": "proactive", "strategy_shape": "aggro", '
        '"commander_name": "", "table": "kitchen table"}}\n'
        "```"
    )
    got = app.extract_brief_payload(fenced_json_brief)
    eq("fenced envelope with brief → theme recovered",
       (got["brief"] or {}).get("theme"), "pirate")
    eq("fenced envelope with brief → still_asking false", got["still_asking"], False)
    eq("fenced envelope with brief → no fence in text", "```" not in got["text"], True)

    # Non-envelope fenced code must stay in the prose — do not strip any
    # trailing fence just because one is present.
    not_envelope = (
        "Here is a sample query shape:\n\n"
        "```json\n"
        '{"theme": "pirate", "colors": "UB"}\n'
        "```"
    )
    got = app.extract_brief_payload(not_envelope)
    eq("non-envelope fence → left alone", "```" in got["text"], True)
    eq("non-envelope fence → still asking", got["still_asking"], True)
    eq("non-envelope fence → no brief", got["brief"], None)
    eq("non-envelope fence → no error", got["brief_error"], None)


    # The response shape the endpoint returns must keep the three states
    # distinguishable even when brief is null.
    def distinguishable(payload):
        if payload.get("brief") is not None:
            return "present"
        if payload.get("brief_error"):
            return "failed"
        if payload.get("still_asking"):
            return "asking"
        return "silent"  # the bug

    eq("states: present", distinguishable(
        app.extract_brief_payload(good)), "present")
    eq("states: asking", distinguishable(
        app.extract_brief_payload(asking)), "asking")
    eq("states: failed", distinguishable(
        app.extract_brief_payload(broken)), "failed")
    eq("never silent on malformed", distinguishable(
        app.extract_brief_payload(broken)) != "silent", True)


def test_llm_backend_choice():
    """Which meter runs. This broke the live app and nothing would have caught it."""
    print("\nllm.py  — Max or API, and never a silent swap")
    import shutil, os
    from deckwright import llm

    real_which, real_env = shutil.which, dict(os.environ)

    def fake_which(name):
        return "/Users/x/.local/bin/claude" if (name == "claude" and _HAS_CLI[0]) else None

    _HAS_CLI = [True]
    shutil.which = fake_which
    try:
        # The subscription is the default. A key sitting in the Keychain is a
        # fallback for machines without the CLI, not a reason to start billing.
        os.environ.pop("DECKWRIGHT_LLM", None)
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        client, how = llm.make_client()
        eq("CLI present and a key set still uses the CLI", client.via, "cli")
        (ok if ("Claude" in how or "CLI" in how or "API" in how) else bad)(
            "says which adapter is running", how)

        # The escape hatch stays honest.
        os.environ["DECKWRIGHT_LLM"] = "api"
        client, how = llm.make_client()
        eq("DECKWRIGHT_LLM=api forces the API", client.via, "api")
        (ok if "DECKWRIGHT_LLM=api" in how else bad)("says why it chose the API", how)

        # No CLI: fall back, and say so. This is the state launchd was in —
        # its PATH has neither ~/.local/bin nor Homebrew, so the CLI was
        # invisible, every call silently went to the API, and the API had no
        # credit. The app looked broken; it was looking in the wrong place.
        os.environ.pop("DECKWRIGHT_LLM", None)
        _HAS_CLI[0] = False
        client, how = llm.make_client()
        eq("no CLI falls back to the API", client.via, "api")
        (ok if "no `claude` binary" in how else bad)(
            "names the missing binary rather than failing quietly", how)
    finally:
        shutil.which = real_which
        os.environ.clear()
        os.environ.update(real_env)


async def test_llm_cli_stdin_and_timeout():
    """CLI path must not inherit stdin or wait forever.

    /api/build hung with two asyncio tasks in subprocess._read_stream — the
    signature of communicate() on a child that never exits. Forge is not on
    the build path; llm.py's `claude -p` is. Same class as a CLI that
    also reads stdin blocks when the parent's stdin never EOFs.
    """
    print("\nllm.py stdin closed, hung CLI times out")
    import os, stat, tempfile, pathlib
    from deckwright import llm

    fake_dir = pathlib.Path(tempfile.mkdtemp(prefix="dw-claude-"))
    # Reads stdin then emits JSON. With DEVNULL, readline returns immediately.
    # Without it, a never-EOF parent stdin wedges the build.
    reader = fake_dir / "claude-reader"
    reader.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'result': '[\"Sol Ring\"]', 'is_error': False}))\n"
    )
    reader.chmod(reader.stat().st_mode | stat.S_IXUSR)

    sleeper = fake_dir / "claude-sleeper"
    sleeper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, time\n"
        "time.sleep(600)\n"
        "print(json.dumps({'result': 'late', 'is_error': False}))\n"
    )
    sleeper.chmod(sleeper.stat().st_mode | stat.S_IXUSR)

    real_exec = asyncio.create_subprocess_exec
    seen = []

    async def spy(*args, **kwargs):
        seen.append(kwargs)
        return await real_exec(*args, **kwargs)

    real_env = dict(os.environ)
    # Parent stdin that never EOFs — the launchd/uvicorn hang shape.
    r_fd, w_fd = os.pipe()
    old_stdin = os.dup(0)
    os.dup2(r_fd, 0)
    try:
        asyncio.create_subprocess_exec = spy  # type: ignore[assignment]
        client = llm._CLIClient(str(reader))
        t0 = time.time()
        resp = await asyncio.wait_for(
            client.messages.create(
                model="claude-test",
                messages=[{"role": "user", "content": "hi"}],
            ),
            timeout=3,
        )
        elapsed = time.time() - t0
        eq("CLI result text", resp.content[0].text, '["Sol Ring"]')
        (ok if elapsed < 2 else bad)(
            "reader CLI returns promptly under never-EOF parent stdin",
            f"took {elapsed:.2f}s")
        (ok if seen and seen[0].get("stdin") is asyncio.subprocess.DEVNULL
         else bad)(
            "create_subprocess_exec gets stdin=DEVNULL",
            f"kwargs={seen[0] if seen else None}")

        # A wedged child must not park /api/build for 13 minutes.
        os.environ["DECKWRIGHT_LLM_TIMEOUT"] = "1"
        seen.clear()
        client = llm._CLIClient(str(sleeper))
        t0 = time.time()
        raised = None
        try:
            await client.messages.create(
                model="claude-test",
                messages=[{"role": "user", "content": "hi"}],
            )
        except Exception as e:
            raised = e
        elapsed = time.time() - t0
        (ok if raised is not None else bad)(
            "hung CLI raises instead of awaiting forever", "")
        (ok if raised is not None and "timeout" in str(raised).lower()
         else bad)(
            "timeout named in the error",
            f"got {raised!r}")
        (ok if elapsed < 5 else bad)(
            "timeout fires within a few seconds",
            f"took {elapsed:.2f}s")
    except asyncio.TimeoutError:
        bad("reader CLI returns promptly under never-EOF parent stdin",
            "hung — stdin still inherited")
        (ok if seen and seen[0].get("stdin") is asyncio.subprocess.DEVNULL
         else bad)(
            "create_subprocess_exec gets stdin=DEVNULL",
            f"kwargs={seen[0] if seen else None}")
    finally:
        asyncio.create_subprocess_exec = real_exec  # type: ignore[assignment]
        os.dup2(old_stdin, 0)
        os.close(old_stdin)
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass
        os.environ.clear()
        os.environ.update(real_env)

def test_sheet():
    """The shopping list — the thing she actually carries into a shop."""
    print("\nsheet.py  — the printed list")
    from deckwright import sheet

    # Files under the type a buyer looks in. An Artifact Creature lives in the
    # creature box, not the artifact box.
    eq("artifact creature files as a creature",
       sheet.split_type("Legendary Artifact Creature — Golem")[0], "Creature")
    eq("keeps the subtype line",
       sheet.split_type("Legendary Creature — Human Villain")[1].strip(), "Human Villain")
    eq("plain land", sheet.split_type("Basic Land — Swamp")[0], "Land")

    # Alphabetical the way a shop files singles: leading articles ignored, so
    # "The Spot" sits under S where she will look for it.
    names = ["Zealous Conscripts", "The Spot", "Arcane Signet", "A Little Chat"]
    got = [c["name"] for c in sorted(({"name": n} for n in names), key=sheet.sort_key)]
    eq("ignores a leading article", got,
       ["Arcane Signet", "A Little Chat", "The Spot", "Zealous Conscripts"])

    # Mana sources counted against the commander's identity, not raw.
    # Scryfall says Command Tower produces all five colours because its text
    # reads "any color in your commander's color identity" — counted raw, a
    # mono-white deck claims twelve blue sources.
    deck = [
        {"name": "Command Tower", "type": "Land",
         "produced_mana": ["W", "U", "B", "R", "G"]},
        {"name": "Plains", "type": "Basic Land — Plains", "produced_mana": ["W"]},
        {"name": "Swamp", "type": "Basic Land — Swamp", "produced_mana": ["B"]},
    ]
    counts = dict(sheet.sources(deck, ["W", "B"]))
    # Reported by full colour name, which is what a shop list says.
    (ok if not {"Blue", "Red", "Green"} & set(counts) else bad)(
        "never claims a colour the commander cannot cast", counts)
    (ok if counts.get("White", 0) >= 2 and counts.get("Black", 0) >= 2 else bad)(
        "counts the colours it really makes", counts)

def test_coverage():
    """Coverage arithmetic — every number here was wrong at least once."""
    print("\ncoverage.py  — what a named card pool can actually fill")
    from deckwright import coverage as cov

    sets = [
        {"code": "spm", "name": "Marvel's Spider-Man", "card_count": 286,
         "set_type": "expansion"},
        {"code": "aspm", "name": "Marvel's Spider-Man Art Series", "card_count": 54,
         "set_type": "memorabilia"},
        {"code": "spe", "name": "Marvel's Spider-Man Eternal", "card_count": 26,
         "set_type": "expansion"},
        {"code": "blb", "name": "Bloomburrow", "card_count": 398,
         "set_type": "expansion"},
    ]

    # A whole sentence, not just the set name — she types "I want to use only
    # Spider-Man booster pack cards", and requiring containment found nothing,
    # which is silently identical to her never having asked.
    for phrasing in ("I want to use only Spider-Man booster pack cards",
                     "only spider-man cards", "spm", "just Spider-Man please"):
        got = cov.resolve_set(phrasing, sets)
        eq(f"resolves {phrasing[:22]!r}", (got or {}).get("code"), "spm")

    # Never an art series or a token set: those match the name perfectly and
    # are not cards anyone can put in a deck.
    eq("prefers the playable set over its art series",
       (cov.resolve_set("Marvel's Spider-Man Art Series", sets) or {}).get("code"), "spm")
    # Bigger playable set wins a tie — "Spider-Man" means the 286-card
    # expansion, not the 26-card supplement.
    eq("prefers the larger playable set",
       (cov.resolve_set("spider-man", sets) or {}).get("code"), "spm")

    for nothing in ("make it cheaper", "is this deck fast", "", "add more angels"):
        eq(f"no set in {nothing[:18]!r}", cov.resolve_set(nothing, sets), None)

    # Matched by name: deck entries have never carried a set code, and matching
    # on an always-absent field returned zero every single time.
    deck = [{"name": "Venom, Evil Unleashed", "role": "theme"},
            {"name": "Sol Ring", "role": "ramp"},
            {"name": "Swamp", "role": "basic"}]
    eq("counts overlap by name",
       cov.deck_from_pool(deck, {"Venom, Evil Unleashed", "Black Cat"}),
       ["Venom, Evil Unleashed"])
    eq("no overlap is empty, not everything", cov.deck_from_pool(deck, set()), [])

    # The mana base is not up for trade. Counting land slots as fillable is what
    # promised 70 of 99 from a set that delivered 40.
    out = cov.summarise(pool_total=193, in_identity=70, deck_size=99, already=14,
                        role_now={"ramp": 10, "theme": 18, "lands": 20, "basic": 18},
                        role_pool={"ramp": 3, "theme": 3},
                        land_count=38)
    eq("spells are the tradeable slots", out["spell_slots"], 61)
    eq("lands are reported and untouched", out["land_slots"], 38)
    (ok if "lands" not in out["role_deltas"] and "basic" not in out["role_deltas"]
     else bad)("mana base stays out of the role trade", out["role_deltas"])

    # A ceiling only, never a prediction. This number has been stated four ways
    # — 70, 61, 24, then a 24-61 range — against actuals of 40 and 23, and the
    # range failed too: a rebuild came in below its own floor. The build tops up
    # from her colours rather than exhausting the set per role, so how much of
    # the set it uses is not derivable from these counts.
    (ok if out["from_pool_at_most"] <= out["spell_slots"] else bad)(
        "never claims more than there are spell slots", out["from_pool_at_most"])
    (ok if out["from_pool_at_most"] <= 70 else bad)(
        "never claims more than the set has legal", out["from_pool_at_most"])
    (ok if "from_pool_at_least" not in out else bad)(
        "states no floor it cannot honour", sorted(out))

    # A set that cannot fill the deck must say so rather than rounding up.
    thin = cov.summarise(pool_total=26, in_identity=8, deck_size=99, already=0,
                         role_now={"theme": 18}, role_pool={"theme": 2},
                         land_count=38)
    eq("a thin set fills only what it has", thin["from_pool_at_most"], 8)
    (ok if thin["shortfall"] >= 53 else bad)("the rest is her colours", thin["shortfall"])


async def test_coverage_role_count_unknown_aborts():
    """a failed role count is unknown, not an empty role."""
    print("\ncoverage  — role count None aborts, never becomes 0")
    from unittest.mock import AsyncMock, patch

    deck = [{"name": "Sol Ring", "type": "Artifact", "cmc": 1,
             "color_identity": ["W", "B"], "text": ""}]
    brief = {"theme": "pirates", "colors": "WB", "budget_each": 12}

    calls = {"n": 0}

    async def count_side(q):
        calls["n"] += 1
        # First two calls are pool_total / in_identity — succeed.
        if calls["n"] <= 2:
            return 40
        # First role count fails — must abort, not write 0.
        return None

    async def fake_scry(path, **kw):
        if path == "/sets":
            return {"data": [
                {"code": "blb", "name": "Bloomburrow", "card_count": 398,
                 "set_type": "expansion"},
            ]}
        return {}

    with patch.object(app, "scry", AsyncMock(side_effect=fake_scry)), \
         patch.object(app, "count_matching", AsyncMock(side_effect=count_side)), \
         patch.object(app, "search", AsyncMock(return_value=[])), \
         patch.object(app, "role_query",
                      return_value=["t:pirate id<=WB"]):
        out = await app.compute_coverage(
            "only Bloomburrow cards", deck, brief, identity="WB")
    eq("failed role count returns None", out, None)


def test_calendar():
    """overlay merge, future unknowns, debut filter honesty."""
    print("\ncalendar.py  — release calendar data layer")
    from datetime import date

    overlay_sets = {
        "hob": {"released_at": "2026-08-14"},
        "zzz": {"released_at": "2027-01-01", "name": "Hand Named Set"},
    }
    scry_row = {
        "name": "The Hobbit", "code": "hob", "set_type": "expansion",
        "released_at": "2026-08-01", "card_count": 300,
    }
    merged = release_calendar.merge_set_record(scry_row, overlay_sets)
    eq("overlay wins on released_at", merged["released_at"], "2026-08-14")
    eq("overlay can rename", release_calendar.merge_set_record(
        {"name": "X", "code": "zzz", "set_type": "expansion",
         "released_at": "", "card_count": 1}, overlay_sets)["name"],
        "Hand Named Set")

    today = date(2026, 8, 6)
    eq("future set flagged", release_calendar.is_future_set(
        {"released_at": "2026-08-14"}, today), True)
    eq("released set not future", release_calendar.is_future_set(
        {"released_at": "2026-06-01"}, today), False)

    # Warp debuts with EOE; Flying does not; Station same-day EOC counts.
    eq("same set is debut", release_calendar.is_debut_for_set(
        "eoe", "2025-08-01", "eoe", "2025-08-01"), True)
    eq("same-day companion is debut", release_calendar.is_debut_for_set(
        "eoe", "2025-08-01", "eoc", "2025-08-01"), True)
    eq("old keyword is not debut", release_calendar.is_debut_for_set(
        "eoe", "2025-08-01", "lea", "1993-08-05"), False)

    names, unchecked = release_calendar.filter_debut_names(
        "eoe", "2025-08-01",
        ["Flying", "Warp", "Station"],
        {
            "Flying": {"status": "known", "first_set": "lea",
                       "first_released_at": "1993-08-05"},
            "Warp": {"status": "known", "first_set": "eoe",
                     "first_released_at": "2025-08-01"},
            "Station": {"status": "known", "first_set": "eoc",
                        "first_released_at": "2025-08-01"},
        },
    )
    eq("debut names drop Flying", names, ["Station", "Warp"])
    eq("no unchecked when all known", unchecked, 0)

    none_names, n_un = release_calendar.filter_debut_names(
        "eoe", "2025-08-01", ["Warp"],
        {"Warp": {"status": "unknown"}},
    )
    eq("all-unknown filter is None not empty", none_names, None)
    eq("unchecked counted", n_un, 1)

    # Partial unknown: Flying known-reprint + Warp unread → unknown, not "none".
    partial, p_un = release_calendar.filter_debut_names(
        "eoe", "2025-08-01",
        ["Flying", "Warp"],
        {
            "Flying": {"status": "known", "first_set": "lea",
                       "first_released_at": "1993-08-05"},
            "Warp": {"status": "unknown"},
        },
    )
    eq("partial-unknown is None not empty zero", partial, None)
    eq("partial unchecked counted", p_un, 1)

    # Overlay survives a failed/unknown mechanics payload (Hobbit → Storied).
    buried = release_calendar.merge_overlay_mechanics(
        {"status": "unknown", "names": None},
        ["Storied"],
        set_code="hob",
    )
    eq("overlay beats unknown", buried.get("status"), "known")
    eq("Storied survives unknown enrich", buried.get("names"), ["Storied"])
    kept = release_calendar.merge_overlay_mechanics(
        {"status": "known", "names": ["Warp"]},
        ["Storied"],
        set_code="eoe",
    )
    eq("overlay unions with known debuts", sorted(kept.get("names") or []),
       ["Storied", "Warp"])

    kws = release_calendar.keywords_from_cards([
        {"keywords": ["Warp", "Flying"]},
        {"keywords": ["Flying", "Station"]},
        {"keywords": None},
    ])
    eq("keywords unique sorted", kws, ["Flying", "Station", "Warp"])

    # Hobbit: Storied lives in oracle, not keywords — still a candidate.
    oracle_names = release_calendar.oracle_ability_words([{
        "oracle_text": (
            "Storied (If you control three or more artifacts, legendaries, "
            "and/or Sagas, you have an enduring story.)\n"
            "When Balin enters, draw a card."
        ),
        "keywords": [],
    }])
    eq("Storied from oracle", oracle_names, ["Storied"])
    cands = release_calendar.mechanic_candidates_from_cards([{
        "keywords": ["Flying"],
        "oracle_text": "Storied (You have an enduring story.)\nFlying",
    }])
    eq("candidates merge keyword+oracle", cands, ["Flying", "Storied"])
    href = release_calendar.mechanic_href("hob", "Storied")
    (ok if href.startswith("https://scryfall.com/search?q=") and "Storied" in href
     else bad)("mechanic link is Scryfall search", href)

    merged_hob = release_calendar.merge_set_record(
        {"name": "The Hobbit", "code": "hob", "set_type": "expansion",
         "released_at": "2026-08-14", "card_count": 300},
        {"hob": {"mechanics": ["Storied"]}},
    )
    eq("overlay carries Storied", merged_hob.get("overlay_mechanics"), ["Storied"])

    eligible = release_calendar.calendar_eligible(
        {"set_type": "expansion", "released_at": "2026-08-14"}, today)
    eq("future expansion eligible", eligible, True)
    eq("token set excluded",
       release_calendar.calendar_eligible(
           {"set_type": "token", "released_at": "2026-08-14"}, today), False)

    card = {
        "image_uris": {"art_crop": "https://cards.scryfall.io/art_crop/test.jpg"},
    }
    eq("art_crop from card", release_calendar.art_crop_from_card(card),
       "https://cards.scryfall.io/art_crop/test.jpg")


async def test_calendar_scry_failure_not_zero():
    """A failed Scryfall mechanics look must be unknown, not an empty debut list."""
    print("\ncalendar — Scryfall failure is unknown")
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock

    fail = AsyncMock(side_effect=RuntimeError("down"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        release_calendar.SAVE_DIR = tmp_path
        release_calendar.MECHANICS_CACHE_PATH = tmp_path / "calendar_mechanics.json"
        cache = release_calendar.read_mechanics_cache()
        got = await release_calendar.fetch_set_debut_mechanics(
            fail, "eoe", "2025-08-01", cache, persist=True)
        eq("set search failure is unknown", got["status"], "unknown")
        eq("unknown has no names key", "names" in got, False)


def panel_kind(row):
    """Mirrors index.html calPanelKind — mechanics panel."""
    m = row.get("mechanics") or {}
    status = m.get("status") or "pending"
    names = m.get("names") or []
    if row.get("future"):
        if status == "known" and names:
            return "announced"
        return "future"
    if status == "pending":
        return "pending"
    if status == "unknown":
        return "unknown"
    if status == "known":
        return "hits" if names else "none"
    return "pending"


def test_calendar_panel_states():
    """future / announced / pending / unknown / empty / hits must not collapse."""

    eq("future without overlay is future", panel_kind({"future": True}), "future")
    eq("future with Storied is announced", panel_kind({
        "future": True,
        "mechanics": {"status": "known", "names": ["Storied"]},
    }), "announced")
    eq("known empty debut is none", panel_kind({
        "future": False,
        "mechanics": {"status": "known", "names": []},
    }), "none")
    eq("failed look is unknown not none", panel_kind({
        "future": False,
        "mechanics": {"status": "unknown"},
    }), "unknown")
    eq("pending is not none and not unknown", panel_kind({
        "future": False,
        "mechanics": {"status": "pending"},
    }), "pending")
    eq("debut names are hits", panel_kind({
        "future": False,
        "mechanics": {"status": "known", "names": ["Warp", "Void"]},
    }), "hits")


async def test_calendar_first_paint_fast():
    """First paint must not Scryfall-search cards — mechanics come from cache/enrich."""
    print("\ncalendar — first paint under budget")
    import tempfile
    from datetime import date
    from pathlib import Path
    today = date(2026, 8, 6)
    raw_sets = []
    for i in range(6):
        raw_sets.append({
            "name": f"Future {i}", "code": f"f{i:02d}", "set_type": "expansion",
            "released_at": f"2026-09-{i+1:02d}", "card_count": 100 + i,
        })
    for i in range(23):
        month = ((i % 12) + 1)
        year = 2026 if i < 7 else 2025
        day = min(28, 1 + (i % 27))
        raw_sets.append({
            "name": f"Released {i}", "code": f"r{i:02d}", "set_type": "expansion",
            "released_at": f"{year}-{month:02d}-{day:02d}", "card_count": 200 + i,
        })

    calls: list[tuple] = []

    async def scry(path, **params):
        calls.append((path, params.get("q")))
        if path == "/sets":
            return {"data": raw_sets}
        raise AssertionError(f"first paint must not Scryfall-search: {path} {params}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        release_calendar.SAVE_DIR = tmp_path
        release_calendar.SETS_CACHE_PATH = tmp_path / "calendar_sets.json"
        release_calendar.MECHANICS_CACHE_PATH = tmp_path / "calendar_mechanics.json"

        t0 = time.time()
        payload = await release_calendar.build_calendar(
            scry, None, today=today, force_sets=True)
        elapsed = time.time() - t0

        (ok if elapsed < 3.0 else bad)(
            f"cold first paint under 3s (got {elapsed:.2f}s)", elapsed)
        eq("only /sets on first paint", [c[0] for c in calls], ["/sets"])
        eq("released page capped", len(payload["released"]),
           release_calendar.RELEASED_PAGE_SIZE)
        (ok if payload["released_total"] >= 20 else bad)(
            "released_total keeps the rest for show older",
            payload["released_total"])
        eq("has_more for show older", payload["released_has_more"], True)
        eq("mechanics not complete when cold", payload["mechanics_complete"], False)

        for row in payload["future"]:
            eq("future flagged", row["future"], True)

        pending = [
            row["mechanics"] for row in payload["released"]
            if (row.get("mechanics") or {}).get("status") == "pending"
        ]
        (ok if pending else bad)("cache misses are pending", len(pending))
        eq("pending has no names list", "names" in pending[0], False)

        page_codes = [r["code"] for r in payload["released"]]
        cache = {"fetched_at": time.time(), "sets": {}, "debut": {}}
        for code in page_codes:
            cache["sets"][code] = {
                "status": "known", "names": ["Warp"],
                "fetched_at": time.time(),
            }
        release_calendar.write_mechanics_cache(cache)

        calls.clear()

        async def scry_warm(path, **params):
            calls.append((path, params.get("q")))
            if path == "/sets":
                return {"data": raw_sets}
            raise AssertionError("warm first paint must not search cards")

        t1 = time.time()
        warm = await release_calendar.build_calendar(
            scry_warm, None, today=today, force_sets=False)
        warm_elapsed = time.time() - t1
        (ok if warm_elapsed < 3.0 else bad)(
            f"warm first paint under 3s (got {warm_elapsed:.2f}s)", warm_elapsed)
        eq("warm uses sets disk cache (no /sets)", calls, [])
        eq("warm mechanics complete", warm["mechanics_complete"], True)
        eq("warm mechanics known", warm["released"][0]["mechanics"]["status"], "known")
        eq("warm keeps debut names", warm["released"][0]["mechanics"]["names"], ["Warp"])


async def test_calendar_enrich_debut_mechanics():
    """Enrich keeps debuts (keywords + oracle words) — does not query saved decks."""
    print("\ncalendar — enrich debut mechanics")
    import tempfile
    from pathlib import Path

    rows = [
        {"name": "Edge of Eternities", "code": "eoe", "set_type": "expansion",
         "released_at": "2025-08-01", "card_count": 100},
        {"name": "The Hobbit", "code": "hob", "set_type": "expansion",
         "released_at": "2026-08-14", "card_count": 300},
    ]
    search_queries: list[str] = []

    async def scry(path, **params):
        if path == "/cards/search":
            q = params.get("q") or ""
            search_queries.append(q)
            if q.startswith("set:eoe"):
                return {"data": [
                    {"set": "eoe", "keywords": ["Warp", "Flying"],
                     "name": "A", "released_at": "2025-08-01", "oracle_text": ""},
                    {"set": "eoe", "keywords": ["Station", "Warp"],
                     "name": "B", "released_at": "2025-08-01", "oracle_text": ""},
                ], "total_cards": 2, "has_more": False}
            if q.startswith("set:hob"):
                return {"data": [{
                    "set": "hob", "keywords": [], "name": "Balin, Loremaster",
                    "released_at": "2026-08-14",
                    "oracle_text": "Storied (You have an enduring story.)\nWhen Balin enters, draw a card.",
                }], "total_cards": 1, "has_more": False}
            if 'keyword:"Warp"' in q:
                return {"data": [{
                    "set": "eoe", "released_at": "2025-08-01", "name": "Warp Card",
                }], "total_cards": 1, "has_more": False}
            if 'keyword:"Station"' in q:
                return {"data": [{
                    "set": "eoc", "released_at": "2025-08-01", "name": "Station Card",
                }], "total_cards": 1, "has_more": False}
            if 'keyword:"Flying"' in q:
                return {"data": [{
                    "set": "lea", "released_at": "1993-08-05", "name": "Mesa Pegasus",
                }], "total_cards": 1, "has_more": False}
            if 'keyword:"Storied"' in q:
                # Not indexed as a keyword yet — fall through to oracle query.
                return {"data": [], "total_cards": 0, "has_more": False}
            if 'o:"Storied"' in q:
                return {"data": [{
                    "set": "hob", "released_at": "2026-08-14", "name": "Balin, Loremaster",
                }], "total_cards": 1, "has_more": False}
            if "-is:token" in q:
                return {"data": [{
                    "image_uris": {"art_crop": "https://example.com/a.jpg"},
                }], "total_cards": 1}
            return {"data": [], "total_cards": 0, "has_more": False}
        raise AssertionError(path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        release_calendar.SAVE_DIR = tmp_path
        release_calendar.SETS_CACHE_PATH = tmp_path / "calendar_sets.json"
        release_calendar.MECHANICS_CACHE_PATH = tmp_path / "calendar_mechanics.json"

        filled = await release_calendar.enrich_released_mechanics(
            scry, rows, fetch_art=True)

        eq("eoe enriched", "eoe" in filled, True)
        mech = filled["eoe"]["mechanics"]
        eq("mechanics known", mech["status"], "known")
        eq("debuts are Warp+Station", mech["names"], ["Station", "Warp"])
        eq("items carry href", bool(mech.get("items") and mech["items"][0].get("href")), True)
        hob = filled["hob"]["mechanics"]
        eq("Hobbit debut is Storied", hob["names"], ["Storied"])
        (ok if not any("id<=" in q for q in search_queries)
         else bad)("no colour-identity deck queries", search_queries)

        before = len(search_queries)
        await release_calendar.enrich_released_mechanics(
            scry, rows, fetch_art=False)
        eq("cache spares set+debut searches",
           len([q for q in search_queries[before:]
                if q.startswith("set:") or q.startswith("keyword:") or q.startswith("o:")]),
           0)

        async def scry_fail(path, **params):
            raise RuntimeError("429")

        release_calendar.MECHANICS_CACHE_PATH.write_text("{}")
        failed = await release_calendar.enrich_released_mechanics(
            scry_fail, rows[:1], fetch_art=False)
        eq("failed enrich is unknown", failed["eoe"]["mechanics"]["status"], "unknown")
        eq("failed enrich has no names", "names" in failed["eoe"]["mechanics"], False)


async def test_calendar_endpoint_returns():
    """The FastAPI route must complete — the hang was invisible to unit tests."""
    print("\ncalendar — GET /api/calendar completes")
    import tempfile
    from datetime import date
    from pathlib import Path
    from fastapi.testclient import TestClient

    today = date(2026, 8, 6)
    raw_sets = [
        {"name": "Soon", "code": "soon", "set_type": "expansion",
         "released_at": "2026-09-01", "card_count": 10},
    ] + [
        {"name": f"Out {i}", "code": f"o{i:02d}", "set_type": "expansion",
         "released_at": f"2026-0{(i % 6) + 1}-15", "card_count": 50}
        for i in range(12)
    ]

    async def fake_scry(path, **params):
        if path == "/sets":
            return {"data": raw_sets}
        if path == "/cards/search":
            return {"data": [], "total_cards": 0, "has_more": False}
        return {"data": [], "total_cards": 0}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        release_calendar.SAVE_DIR = tmp_path
        release_calendar.SETS_CACHE_PATH = tmp_path / "calendar_sets.json"
        release_calendar.MECHANICS_CACHE_PATH = tmp_path / "calendar_mechanics.json"

        real_save = app.SAVE_DIR
        real_scry = app.scry
        orig_today = release_calendar._today
        app.SAVE_DIR = tmp_path
        app.scry = fake_scry
        release_calendar._today = lambda: today
        try:
            client = TestClient(app.app)
            t0 = time.time()
            r = client.get("/api/calendar")
            elapsed = time.time() - t0
            eq("calendar HTTP 200", r.status_code, 200)
            (ok if elapsed < 3.0 else bad)(
                f"endpoint under 3s (got {elapsed:.2f}s)", elapsed)
            body = r.json()
            eq("future present", len(body.get("future") or []) >= 1, True)
            eq("released capped", len(body.get("released") or []),
               min(12, release_calendar.RELEASED_PAGE_SIZE))
            eq("has released_total", "released_total" in body, True)
            eq("no coverage taxonomy blob", "coverage" in body, False)
            eq("mechanics pending on cold",
               body["released"][0]["mechanics"]["status"], "pending")

            er = client.get("/api/calendar/enrich?codes=o00,o01")
            eq("enrich HTTP 200", er.status_code, 200)
            ebody = er.json()
            eq("enrich returns asked codes", ebody.get("codes"), ["o00", "o01"])
            eq("enrich carries mechanics",
               "mechanics" in (ebody.get("sets") or [{}])[0], True)
        finally:
            app.SAVE_DIR = real_save
            app.scry = real_scry
            release_calendar._today = orig_today
    eq("hits beat empty", panel_kind({
        "future": False,
        "mechanics": {"status": "known", "names": ["Warp"]},
    }), "hits")


def test_playstyle_skeleton():
    """how she wants to play moves the skeleton, not just the flavour."""
    print("\nskeleton_for()  — playstyle shapes the deck")

    base = {n: c for n, c, _ in app.ROLES if n != "lands"}
    base_total = sum(base.values())

    # A brief with no playstyle must build exactly the deck it built before.
    plain = {n: c for n, c, _ in app.skeleton_for({}) if n != "lands"}
    eq("no playstyle changes nothing", plain, base)
    eq("unknown playstyle changes nothing",
       {n: c for n, c, _ in app.skeleton_for({"playstyle": "vibes"}) if n != "lands"}, base)

    shapes = {}
    for style in ("proactive", "reactive", "grindy"):
        got = {n: c for n, c, _ in app.skeleton_for({"playstyle": style}) if n != "lands"}
        shapes[style] = got
        # The deck is still 99 cards. A shift that does not balance would push
        # the spell count past the mana base and silently shrink the lands.
        eq(f"{style} keeps the spell count", sum(got.values()), base_total)
        # No role may be emptied. A deck with no removal at all is a worse
        # answer than a proactive deck with a little.
        (ok if all(v >= 2 for v in got.values()) else bad)(
            f"{style} leaves every role standing", got)

    # The point of the card: two people, same theme, different decks.
    (ok if shapes["reactive"]["spot"] > shapes["proactive"]["spot"] else bad)(
        "reactive gets more removal than proactive",
        f"{shapes['reactive']['spot']} vs {shapes['proactive']['spot']}")
    (ok if shapes["proactive"]["plan"] > shapes["reactive"]["plan"] else bad)(
        "proactive gets more of its own plan",
        f"{shapes['proactive']['plan']} vs {shapes['reactive']['plan']}")
    (ok if shapes["grindy"]["draw"] > shapes["proactive"]["draw"] else bad)(
        "grindy draws more cards",
        f"{shapes['grindy']['draw']} vs {shapes['proactive']['draw']}")

def test_apply_tweak():
    """sliders swap real pool cards by type — never invent or duplicate."""
    print("\napply_tweak()  — real composition swaps")
    from deckwright import tweak as tw

    def full(name, typ, role="theme", text="", usd="1.00", cmc=2):
        return {"name": name, "type": typ, "text": text, "role": role,
                "usd": usd, "cmc": cmc, "cost": f"{{{cmc}}}",
                "color_identity": ["U", "B"], "legal": "legal"}

    # Skeleton: 4 lands, 3 creatures, 3 instants, 2 artifacts, 2 enchantments,
    # plus flexible theme fillers for donors.
    deck = [
        full("Command Tower", "Land", "lands", "{T}: Add one mana."),
        full("Reliquary Tower", "Land", "lands", "You have no maximum hand size."),
        full("Dimir Aqueduct", "Land", "lands", "enters tapped"),
        full("Island", "Basic Land — Island", "basic"),
        full("Thief One", "Creature — Human Rogue", "theme"),
        full("Thief Two", "Creature — Human Rogue", "theme"),
        full("Thief Three", "Creature — Human Rogue", "plan"),
        full("Negate", "Instant", "spot", "Counter target noncreature spell."),
        full("Counterspell", "Instant", "spot", "Counter target spell."),
        full("Brainstorm", "Instant", "draw", "Draw three cards"),
        full("Sol Ring", "Artifact", "ramp", "{T}: Add {C}{C}."),
        full("Arcane Signet", "Artifact", "ramp", "{T}: Add one mana."),
        full("Rhystic Study", "Enchantment", "draw", "draw a card"),
        full("Propaganda", "Enchantment", "theme", "creatures can't attack"),
        full("Flavor A", "Sorcery", "theme", "Draw a card."),
        full("Flavor B", "Sorcery", "deepcut", "Surveil 2."),
        full("Flavor C", "Sorcery", "wipe", "Destroy all creatures."),
    ]
    before_len = len(deck)
    before_names = {c["name"] for c in deck}

    # Unused pool cards — real Scryfall-shaped dicts waiting off the list.
    role_pools = {
        "theme": [
            full("Pool Pirate", "Creature — Pirate", text="Menace"),
            full("Pool Corsair", "Creature — Pirate", text="Flying"),
            full("Pool Raider", "Creature — Pirate", text="Haste"),
            full("Pool Charm", "Enchantment", text="At the beginning"),
        ],
        "ramp": [
            full("Mind Stone", "Artifact", "ramp", "{T}: Add {C}."),
            full("Fellwar Stone", "Artifact", "ramp", "{T}: Add one mana."),
        ],
        "lands": [
            full("Sunken Hollow", "Land", "lands", "{T}: Add {U} or {B}."),
            full("Drowned Catacomb", "Land", "lands", "enters tapped"),
            full("Watery Grave", "Land — Island Swamp", "lands"),
        ],
        "draw": [
            full("Read the Bones", "Sorcery", "draw", "Draw two cards"),
            full("Sign in Blood", "Sorcery", "draw", "Draw two cards"),
        ],
        "spot": [
            full("Doom Blade", "Instant", "spot", "Destroy target nonblack creature."),
            full("Go for the Throat", "Instant", "spot", "Destroy target nonartifact creature."),
        ],
    }

    # Axes match the rail: lands / creatures / instants_sorceries / artifacts / enchantments
    eq("card_axis land", tw.card_axis(full("X", "Land")), "lands")
    eq("card_axis creature", tw.card_axis(full("X", "Creature — Pirate")), "creatures")
    eq("card_axis artifact creature is creature",
       tw.card_axis(full("X", "Artifact Creature — Golem")), "creatures")
    eq("card_axis instant", tw.card_axis(full("X", "Instant")), "instants_sorceries")
    eq("card_axis sorcery", tw.card_axis(full("X", "Sorcery")), "instants_sorceries")
    eq("card_axis artifact", tw.card_axis(full("X", "Artifact")), "artifacts")
    eq("card_axis enchantment", tw.card_axis(full("X", "Enchantment")), "enchantments")

    base = tw.composition_of(deck)
    eq("base lands", base["lands"], 4)
    eq("base creatures", base["creatures"], 3)
    eq("base instants_sorceries", base["instants_sorceries"], 6)  # 3 instants + 3 sorceries
    eq("base artifacts", base["artifacts"], 2)
    eq("base enchantments", base["enchantments"], 2)

    # +2 creatures: take real creatures from the pool, drop non-creatures.
    r = tw.apply_tweak(deck, {"creatures": 2}, role_pools)
    eq("len preserved on add", len(r["deck"]), before_len)
    eq("applied +2 creatures", r["applied"].get("creatures"), 2)
    eq("ok when pool covers", r["ok"], True)
    eq("blocked empty", r["blocked"], [])
    after = r["composition"]
    eq("creatures rose by 2", after["creatures"], base["creatures"] + 2)
    names = [c["name"] for c in r["deck"]]
    eq("no duplicate names", len(names), len(set(names)))
    (ok if "Pool Pirate" in names or "Pool Corsair" in names else bad)(
        "real pool creature entered", names)
    for n in names:
        (ok if n in before_names
              or n in {c["name"] for pool in role_pools.values() for c in pool}
         else bad)(f"every name was real ({n})", n)
    # No fabricated blanks — every card keeps a name and a type line.
    for c in r["deck"]:
        (ok if c.get("name") and c.get("type") else bad)(
            "no blank stand-in", c)

    # Composition target is the seam for the single-card swap / the rebuild work rebuilds.
    for axis in tw.COMPOSITION_AXES:
        (ok if axis in after else bad)(f"composition has {axis}", after)

    # Exhaust the creature pool: ask for more than remain.
    # After +2, one pool creature may still be free; ask for +5 more from a
    # fresh deck so the block path is deterministic.
    tiny_pools = {"theme": [full("Only One", "Creature — Pirate")]}
    blocked = tw.apply_tweak(deck, {"creatures": 3}, tiny_pools)
    eq("partial apply when pool short", blocked["applied"].get("creatures"), 1)
    eq("not ok when short", blocked["ok"], False)
    eq("one blocked entry", len(blocked["blocked"]), 1)
    reason = (blocked["blocked"][0].get("reason") or "").lower()
    (ok if "pool" in reason or "cannot" in reason or "no " in reason else bad)(
        "blocked reason names the limit", blocked["blocked"][0])

    # Negative delta: remove creatures, replace with real non-creatures from pool.
    # Every real deck runs repeated basics — 8 Mountains, 20 Plains, one had 31.
    # The fixture above has a single Island, so a singleton check on raw names
    # passed here while raising on all fifteen saved decks. A crash inside
    # /api/simulate takes the whole panel down, sliders included, which is how
    # this reached Brad as "the sliders are gone".
    real = [dict(c) for c in deck] + [full("Island", "Basic Land — Island", "basic")
                                      for _ in range(12)]
    many = tw.apply_tweak(real, {"creatures": 1}, role_pools)
    eq("repeated basics are not duplicates", many["applied"].get("creatures"), 1)
    eq("length preserved with basics", len(many["deck"]), len(real))

    # Basics are an unlimited legal source, so the lands slider works even with
    # no pools at all — the case every deck saved before pools existed hits.
    up = tw.apply_tweak(real, {"lands": 4}, {})
    eq("lands slider works without pools", up["applied"].get("lands"), 4)
    eq("length preserved adding lands", len(up["deck"]), len(real))
    nonbasic = [c["name"] for c in up["deck"] if c["name"] not in tw.BASICS]
    eq("no nonbasic duplicates", len(nonbasic), len(set(nonbasic)))

    down = tw.apply_tweak(deck, {"creatures": -2}, role_pools)
    eq("len preserved on remove", len(down["deck"]), before_len)
    eq("applied -2 creatures", down["applied"].get("creatures"), -2)
    eq("creatures fell by 2", down["composition"]["creatures"], base["creatures"] - 2)
    down_names = {c["name"] for c in down["deck"]}
    eq("no duplicates on remove", len(down_names), before_len)
    # Removed creatures must not become nameless 3-mana Creatures.
    for c in down["deck"]:
        (ok if c.get("name") and not (
            c.get("name") in ("", None) or
            (c.get("type") == "Creature" and c.get("text") == "" and c.get("cmc") == 3
             and c.get("name") not in before_names
             and c.get("name") not in {x["name"] for p in role_pools.values() for x in p})
        ) else bad)("no fabricated victim", c)

    # Lands axis uses the lands pool, not role-name cloning.
    land_up = tw.apply_tweak(deck, {"lands": 2}, role_pools)
    eq("lands +2 applied", land_up["applied"].get("lands"), 2)
    eq("lands count +2", land_up["composition"]["lands"], base["lands"] + 2)
    land_names = {c["name"] for c in land_up["deck"]}
    (ok if "Sunken Hollow" in land_names or "Drowned Catacomb" in land_names
          or "Watery Grave" in land_names else bad)("real land from pool", land_names)

    # No pools + non-zero tweak → refuse rather than invent.
    naked = tw.apply_tweak(deck, {"artifacts": 1}, {})
    eq("no pools refuses", naked["ok"], False)
    eq("no pools applied nothing", naked["applied"].get("artifacts", 0), 0)
    eq("deck unchanged without pools",
       [c["name"] for c in naked["deck"]], [c["name"] for c in deck])

    # Unknown axis (old ramp role key) does not fabricate.
    old = tw.apply_tweak(deck, {"ramp": 3}, role_pools)
    eq("legacy ramp axis blocked", old["ok"], False)
    eq("legacy ramp not applied", old["applied"].get("ramp", 0), 0)

    # app.apply_tweak is the simulate entry — same contract, returns result dict.
    via_app = app.apply_tweak(deck, {"creatures": 1}, role_pools)
    eq("app.apply_tweak returns dict", isinstance(via_app, dict), True)
    eq("app.apply_tweak has deck", "deck" in via_app and "composition" in via_app, True)
    eq("app.apply_tweak singleton",
       len({c["name"] for c in via_app["deck"]}), len(via_app["deck"]))
def test_card_swap():
    """replace one card with a verified pool alternate; refuse fiction."""
    print("\ncard swap")
    import tempfile
    from pathlib import Path

    def pool_card(name, *, cmc=2, ci=None, rank=100, text="", typ="Artifact"):
        return {"name": name, "cost": f"{{{cmc}}}", "cmc": cmc,
                "type": typ, "text": text or f"{{{cmc}}}, {name}",
                "color_identity": ci or ["U", "B"], "usd": "1.00",
                "edhrec_rank": rank, "legal": "legal", "gc": False,
                "keywords": [], "produced_mana": [], "layout": "normal",
                "power": "", "toughness": "", "purchase_uris": {},
                "image": None, "art": None}

    ci = "UB"
    sol = _card("Sol Ring", typ="Artifact", role="ramp", ci=["U", "B"], usd="1.50")
    sol["cmc"] = 1
    sol["text"] = "{T}: Add {C}{C}."
    bolt = _card("Lightning Bolt", typ="Instant", role="spot", ci=["R"])
    plan = _card("Strategy Piece", typ="Creature", role="plan", ci=["U", "B"])
    island = _card("Island", typ="Basic Land — Island", role="basic", ci=["U"])
    deck = [sol, bolt, plan, island]

    ramp_pool = [
        pool_card("Sol Ring", cmc=1, rank=10, text="{T}: Add {C}{C}."),
        pool_card("Arcane Signet", cmc=2, rank=50,
                  text="{T}: Add one mana of any color in your commander's color identity."),
        pool_card("Talisman of Dominance", cmc=2, rank=80,
                  text="{T}: Add {C}. {1}, {T}: Add {U} or {B}."),
        pool_card("Dimir Signet", cmc=2, rank=90,
                  text="{1}, {T}: Add {U}{B}."),
        pool_card("Cultivate", cmc=3, rank=200, typ="Sorcery",
                  text="Search your library for up to two basic land cards."),
    ]
    spot_pool = [
        pool_card("Lightning Bolt", cmc=1, ci=["R"], typ="Instant"),
        pool_card("Go for the Throat", cmc=2, ci=["B"], typ="Instant"),
        pool_card("Fatal Push", cmc=1, ci=["B"], typ="Instant"),
    ]
    role_pools = {"ramp": ramp_pool, "spot": spot_pool, "plan": [plan],
                  "basic": [], "lands": []}

    # Alternatives are full pool cards, not shopping stubs, and never invent.
    alts = swap.alternatives(sol, role_pools, deck, ci)
    eq("alts available", len(alts) >= 2, True)
    (ok if all("text" in a for a in alts) else bad)("alts carry oracle text", alts)
    (ok if all(a["name"] != "Sol Ring" for a in alts) else bad)("alts exclude self", alts)
    (ok if all(a["name"] != "Cultivate" for a in alts) else bad)(
        "alts respect cmc band", [a["name"] for a in alts])

    # Happy path: Sol Ring → Arcane Signet, role preserved, why/sim_pick cleared.
    sol_tagged = dict(sol)
    sol_tagged["why"] = "old rationale"
    sol_tagged["sim_pick"] = {"delta_pct": 1.0}
    deck_tagged = [sol_tagged, bolt, plan, island]
    result = swap.apply_swap(deck_tagged, "Sol Ring", "Arcane Signet", role_pools, ci=ci)
    eq("swap ok deck size", len(result["deck"]), len(deck_tagged))
    eq("removed name", result["removed"], "Sol Ring")
    eq("added name", result["added"]["name"], "Arcane Signet")
    eq("role preserved", result["added"]["role"], "ramp")
    eq("why cleared", "why" in result["added"], False)
    eq("sim_pick cleared", "sim_pick" in result["added"], False)
    names = [c["name"] for c in result["deck"]]
    (ok if "Sol Ring" not in names else bad)("old card gone", names)
    (ok if "Arcane Signet" in names else bad)("new card in", names)
    (ok if names.count("Arcane Signet") == 1 else bad)("singleton after swap", names)

    # Refusals.
    try:
        swap.apply_swap(deck, "Sol Ring", "Black Lotus", role_pools, ci=ci)
        bad("invented name refused", "accepted Black Lotus")
    except swap.SwapError as e:
        ok("invented name refused", str(e))

    try:
        swap.apply_swap(deck, "Sol Ring", "Lightning Bolt", role_pools, ci=ci)
        bad("singleton refused", "accepted duplicate")
    except swap.SwapError as e:
        ok("singleton refused", str(e))

    try:
        swap.apply_swap(deck, "Missing Card", "Arcane Signet", role_pools, ci=ci)
        bad("missing remove refused", "accepted")
    except swap.SwapError as e:
        ok("missing remove refused", str(e))

    try:
        swap.apply_swap(deck, "Strategy Piece", "Arcane Signet", role_pools, ci=ci)
        bad("core plan refused", "accepted")
    except swap.SwapError as e:
        ok("core plan refused", str(e))

    # Spot pool card into a ramp slot is refused — wrong pool.
    try:
        swap.apply_swap(deck, "Sol Ring", "Fatal Push", role_pools, ci=ci)
        bad("wrong-role pool refused", "accepted Fatal Push into ramp")
    except swap.SwapError as e:
        ok("wrong-role pool refused", str(e))

    # Persist path: swap updates the saved list, drops stale overview, rewrites
    # substitutions, and leaves the old simulation hash mismatched until a resim.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = {
            "commander": "Malcolm, Keen-Eyed Navigator",
            "deck": [dict(c) for c in deck],
            "price": 4.50,
            "buyable": True,
            "card_pool": "tournament_legal",
            "brief": {"theme": "pirates", "colors": "UB"},
            "role_pools": role_pools,
            "substitutions": substitution.build_substitutions(deck, role_pools, ci),
            "overview": {"how_it_works": "pirates do pirate things",
                         "combos": [], "pros": ["fast"], "cons": []},
            "commander_card": {"name": "Malcolm, Keen-Eyed Navigator",
                               "cmc": 3, "color_identity": ["U", "B"]},
        }
        app.persist_deck(out, d)
        slug = out["id"]
        thesis = {"payoff_cards": ["Sol Ring"],
                  "conditions": [{"label": "mana", "note": "have mana",
                                  "check": {"mana_available": 3}}]}
        fake_sim = {
            "runs": 10,
            "curves": [{"label": "mana", "note": "have mana",
                        "points": [10] * 10, "median_turn": 5}],
            "diagnostics": {"lands": 1, "mana_rocks": 1, "land_ramp": 0,
                            "extra_drops": 0, "payoffs": 1, "avg_cmc": 1.0},
            "assumptions": sim.ASSUMPTIONS,
            "review": {"sound": True, "caveat": ""},
        }
        app.store_sim_baseline(slug, thesis, fake_sim, d)
        before = app.load_saved_deck(slug, d)
        eq("baseline present before swap", bool(before.get("simulation")), True)

        swapped = app.perform_swap(
            before, "Sol Ring", "Arcane Signet",
            payoff_cards=thesis["payoff_cards"],
            conditions=thesis["conditions"],
            runs=50,
            directory=d,
        )
        eq("perform_swap ok", swapped["ok"], True)
        eq("price refreshed", isinstance(swapped["price"], (int, float)), True)
        eq("overview marked stale", swapped.get("overview_stale"), True)
        eq("overview cleared on disk payload", bool(swapped.get("overview")), False)
        eq("substitutions rebuilt",
           "Arcane Signet" in (swapped.get("substitutions") or {}), True)
        eq("Sol Ring gone from subs",
           "Sol Ring" in (swapped.get("substitutions") or {}), False)
        eq("diagnostics present", bool(swapped.get("diagnostics")), True)
        eq("curves recomputed", bool(swapped.get("curves")), True)

        on_disk = app.load_saved_deck(slug, d)
        eq("deck persisted",
           any(c["name"] == "Arcane Signet" for c in on_disk["deck"]), True)
        eq("old card not persisted",
           any(c["name"] == "Sol Ring" for c in on_disk["deck"]), False)
        # Fresh baseline for the new list — hash matches the swapped 99.
        exposed = app.expose_saved_deck(on_disk)
        eq("new baseline exposed", bool(exposed.get("simulation")), True)
        eq("baseline hash matches new list",
           exposed["simulation"]["deck_hash"], bake.deck_list_hash(on_disk["deck"]))
        eq("overview_stale flag on disk", on_disk.get("overview_stale"), True)


def test_plain_commander_reason():
    """write-up fallback is name + type + label — facts, not prose."""
    print("\nplain_commander_reason()")
    card = {"name": "Lyra Dawnbringer",
            "type": "Legendary Creature — Angel"}
    got = app.plain_commander_reason(
        card, "best fit for your theme",
        'is:commander (t:creature or o:"can be your commander") t:angel')
    (ok if "Lyra Dawnbringer" in got else bad)("names the card", got)
    (ok if "Angel" in got else bad)("type line", got)
    (ok if "best fit" in got else bad)("label rung", got)
    # IS_COMMANDER boilerplate must not beat the theme clause.
    (ok if "angel" in got.lower() and "can be your commander" not in got.lower()
     else bad)("theme rung, not commander boilerplate", got)
    # Query explanation is optional spice — must not invent a card story.
    eq("no invented strategy voice", "you would" in got.lower(), False)


async def test_commander_writeup_fallback():
    """forced write-up failure still returns five choosable commanders."""
    print("\ncommander write-up fallback stub raises, picks survive")
    from unittest.mock import AsyncMock, patch

    brief = {
        "theme": "angel",
        "angle": "angels that gain life",
        "commander_query": "is:commander t:angel",
        "budget_total": 100,
        "bracket": 2,
        "flavor_terms": [],
    }
    boom = AsyncMock(side_effect=RuntimeError("forced write-up failure"))
    with patch.object(app.client.messages, "create", boom):
        resp = await app.commanders(brief)

    picks = resp.get("picks") or []
    eq("five picks despite write-up failure", len(picks), 5)
    # One retry each → 2 attempts per labelled commander.
    eq("write-up retried once per commander", boom.call_count, 10)
    for p in picks:
        eq(f"{p['name']}: empty strategy", p.get("strategy") or "", "")
        eq(f"{p['name']}: empty does", p.get("does") or "", "")
        fits = p.get("fits") or ""
        (ok if p["name"] in fits else bad)(
            f"{p['name']}: plain fits names the card", fits)
        (ok if (p.get("type") or "") and (p["type"] in fits) else bad)(
            f"{p['name']}: plain fits has type line", fits)
        (ok if p.get("reason") else bad)(
            f"{p['name']}: keeps label reason", p.get("reason"))
    note = (resp.get("note") or "").lower()
    (ok if "write-up" in note else bad)(
        "note says write-ups are missing", resp.get("note"))


def _ci_subset(card: dict, allowed: str) -> bool:
    """True when card colour identity ⊆ allowed WUBRG letters."""
    return set(card.get("color_identity") or []).issubset(set(allowed.upper()))


async def test_commander_ladder_colors():
    """named colours scope every rung; absent colours stay unscoped."""
    print("\ncommander ladder colours UB / WB / GW legality + unscoped")
    await app.load_vocab()

    fixtures = [
        {
            "label": "pirates that steal",
            "brief": {
                "theme": "pirates that steal",
                "colors": "UB",
                "commander_query": "is:commander t:pirate",
                "flavor_terms": [],
            },
        },
        {
            "label": "lifegain",
            "brief": {
                "theme": "lifegain",
                "colors": "WB",
                "commander_query": 'is:commander o:"gain life"',
                "flavor_terms": [],
            },
        },
        {
            "label": "go wide with tokens",
            "brief": {
                "theme": "go wide with tokens",
                "colors": "GW",
                "commander_query": "is:commander o:token",
                "flavor_terms": [],
            },
        },
    ]

    for fix in fixtures:
        brief = fix["brief"]
        ci = app.brief_color_identity(brief)
        ladder, generic = app.commander_ladder(brief)
        (ok if all(f"id<={ci}" in q for q in ladder) else bad)(
            f"{fix['label']}: every rung has id<={ci}",
            " | ".join(ladder[:4]))
        if "pirate" in (brief.get("theme") or "").lower():
            (ok if any("t:pirate" in q for q in ladder) else bad)(
                f"{fix['label']}: t:pirate still on ladder",
                " | ".join(ladder))
        try:
            cards, used = await app.search_ladder(
                ladder, limit=25, floor=app.COMMANDER_FLOOR)
        except app.ScryfallError as e:
            live_skip(f"commander colours {fix['label']}", e)
            continue
        cards = app.legal_commanders_only(cards)
        labelled = app.label_commander_picks(cards, brief, n=app.COMMANDER_PICKS)
        picks = [c for c, _ in labelled]
        eq(f"{fix['label']}: {app.COMMANDER_PICKS} picks",
           len(picks), app.COMMANDER_PICKS)
        illegal = [c["name"] for c in picks if not _ci_subset(c, ci)]
        (ok if not illegal else bad)(
            f"{fix['label']}: {len(picks)} of {len(picks)} picks legal in {ci}",
            f"illegal={illegal} used={used!r}")
        # Lifegain previously passed by luck — keep it green AND assert colours.
        if fix["label"] == "lifegain":
            (ok if all(_ci_subset(c, "WB") for c in picks) else bad)(
                "lifegain WB legality is asserted, not luck",
                str([c["name"] for c in picks]))

    # No colours: picks still return; response says they span colours.
    from unittest.mock import AsyncMock, patch
    bare = {
        "theme": "pirate",
        "angle": "steal treasure",
        "commander_query": "is:commander t:pirate",
        "budget_total": 100,
        "bracket": 2,
        "flavor_terms": [],
    }
    bare_ladder, _ = app.commander_ladder(bare)
    (ok if all("id<=" not in q for q in bare_ladder) else bad)(
        "no-colours ladder stays unscoped", " | ".join(bare_ladder[:3]))
    boom = AsyncMock(side_effect=RuntimeError("forced write-up failure"))
    with patch.object(app.client.messages, "create", boom):
        try:
            resp = await app.commanders(bare)
        except app.ScryfallError as e:
            live_skip("commander colours unscoped note", e)
            return
    picks = resp.get("picks") or []
    (ok if picks else bad)("no-colours still returns picks", str(resp.get("note")))
    note = (resp.get("note") or "").lower()
    (ok if "span" in note and "colour" in note else bad)(
        "response says picks are unscoped / span colours", resp.get("note"))


async def test_commander_ladder_compose():
    """commander ladder composes thing + behaviour before type alone.

    Asserts the *rung used*, not merely that cards came back. Fixtures cover
    angels (compose), vampires (already composed), pirates (no invented
    behaviour), and a thin composition that degrades to the type rung.
    """
    print("\ncommander ladder — compose first, degrade honestly")
    await app.load_vocab()
    from unittest.mock import AsyncMock, patch

    def _theme_payload(q: str) -> str:
        """Strip commander shell + colour/pool noise so we can assert clauses."""
        q = q.replace(app.IS_COMMANDER, "")
        q = re.sub(r"\bf:commander\b", "", q, flags=re.I)
        q = re.sub(r"\bid<=[WUBRG]+\b", "", q, flags=re.I)
        q = re.sub(r"\s+", " ", q).strip()
        # Theme clause is parenthesised: (...). Empty → generic rung.
        m = re.search(r"\((.*)\)\s*$", q)
        return (m.group(1).strip() if m else q.strip(" ()"))

    # --- 1. angels that gain life: composition leads; rung used is composed ---
    angels = {
        "theme": "angels that gain life",
        "colors": "WB",
        # Model wrote type alone — the bug: ladder used to stop here.
        "commander_query": "is:commander t:angel",
        "flavor_terms": [],
    }
    aladder, ageneric = app.commander_ladder(angels)
    a0 = _theme_payload(aladder[0]) if aladder else ""
    eq("angels: composed rung is first", a0, "t:angel otag:lifegain")
    (ok if any(_theme_payload(q) == "t:angel" for q in aladder) else bad)(
        "angels: type-alone rung present", " | ".join(aladder[:4]))
    (ok if any(_theme_payload(q) == "otag:lifegain" for q in aladder) else bad)(
        "angels: behaviour-alone rung present", " | ".join(aladder[:4]))
    try:
        acards, aused = await app.search_ladder(
            aladder, limit=25, floor=app.COMMANDER_FLOOR)
    except app.ScryfallError as e:
        live_skip("commander compose angels", e)
    else:
        acards = app.legal_commanders_only(acards)
        eq("angels: used composed rung",
           "otag:lifegain" in aused and "t:angel" in aused, True)
        (ok if aused not in ageneric else bad)(
            "angels: not generic", aused)
        # Picks are angels with lifegain text — not bare-tribe Avacyn/Giada alone.
        texts = " ".join(
            ((c.get("text") or "") + " " + (c.get("type") or "")).lower()
            for c in acards[:8])
        types = " ".join((c.get("type") or "").lower() for c in acards[:8])
        (ok if "angel" in types else bad)(
            "angels: returned Angel types", str([c["name"] for c in acards[:5]]))
        (ok if ("gain" in texts and "life" in texts) or "lifelink" in texts
         else bad)(
            "angels: pool carries lifegain text",
            str([c["name"] for c in acards[:5]]))

    # --- 2. vampires that drain: still composed (unchanged) ---
    vampires = {
        "theme": "vampires that drain",
        "colors": "WB",
        "commander_query": "is:commander t:vampire otag:lifedrain",
        "flavor_terms": [],
    }
    vladder, _ = app.commander_ladder(vampires)
    v0 = _theme_payload(vladder[0]) if vladder else ""
    eq("vampires: composed rung is first", v0, "t:vampire otag:lifedrain")

    # --- 3. pirates alone: type only, no invented behaviour ---
    pirates = {
        "theme": "pirates",
        "colors": "UB",
        "commander_query": "is:commander t:pirate",
        "flavor_terms": [],
    }
    pladder, _ = app.commander_ladder(pirates)
    (ok if any(_theme_payload(q) == "t:pirate" for q in pladder) else bad)(
        "pirates: t:pirate on ladder", " | ".join(pladder[:3]))
    (ok if not any("otag:" in _theme_payload(q) for q in pladder) else bad)(
        "pirates: no invented behaviour clause", " | ".join(pladder[:4]))

    # --- 4. thin composition degrades to type rung and says so ---
    thin_ladder = [
        f'{app.IS_COMMANDER} (t:angel otag:lifegain) id<=WB',
        f'{app.IS_COMMANDER} (t:angel) id<=WB',
        f'{app.IS_COMMANDER} (otag:lifegain) id<=WB',
        f'{app.IS_COMMANDER} id<=WB',
    ]
    composed_q, type_q = thin_ladder[0], thin_ladder[1]

    async def fake_search(q, limit=60):
        if q == composed_q:
            return []  # composition too thin — zero hits
        if q == type_q:
            return [{"name": f"Angel {i}", "type": "Legendary Creature — Angel"}
                    for i in range(10)]
        return [{"name": f"Other {i}", "type": "Legendary Creature"} for i in range(10)]

    with patch.object(app, "search", AsyncMock(side_effect=fake_search)):
        _, used_thin = await app.search_ladder(
            thin_ladder, limit=25, floor=app.COMMANDER_FLOOR)
    eq("thin composition degrades to type rung", used_thin, type_q)
    (ok if "otag:lifegain" not in used_thin else bad)(
        "thin composition does not pretend it matched", used_thin)


async def test_commander_ladder():
    """relax the model query; keep thin on-theme hits; generic stays honest."""
    print("\ncommander ladder angels phrase + expensive fallback")
    await app.load_vocab()

    brief = {
        "theme": "angels that gain life",
        "angle": ("an attacking angel commander converts its own life gain "
                  "directly into opponent life loss"),
        "commander_query": (
            'is:commander t:angel o:"whenever" o:"gain" o:"life" o:"loses"'
        ),
        "budget_total": 100,
        "bracket": 2,
    }
    ladder, generic = app.commander_ladder(brief)
    try:
        cards, used = await app.search_ladder(
            ladder, limit=25, floor=app.COMMANDER_FLOOR)
    except app.ScryfallError as e:
        live_skip("commander ladder the commander ladder fix", e)
        return
    names = [c["name"] for c in cards]
    types = " ".join(c.get("type") or "" for c in cards).lower()
    (ok if cards and used not in generic else bad)(
        "angels brief stays on-theme", f"used={used!r} n={len(cards)}")
    (ok if "angel" in types else bad)(
        "returned commanders include Angel type", str(names[:5]))
    (ok if "Syr Konrad" not in names else bad)(
        "Syr Konrad not foisted by generic rung", str(names[:5]))

    # Top-up: a 1-hit rung must not be discarded for a later full page.
    thin_then_wide = [
        'is:commander t:angel o:"whenever" o:"gain" o:"life" o:"loses"',
        "is:commander t:angel",
    ]
    try:
        topped, used_top = await app.search_ladder(thin_then_wide, limit=25, floor=8)
        strict = await app.search(thin_then_wide[0], limit=5)
    except app.ScryfallError as e:
        live_skip("commander ladder top-up", e)
        return
    eq("top-up reaches floor", len(topped) >= 8, True)
    # The single strict-query hit must still be present after widening.
    if strict:
        (ok if any(c["name"] == strict[0]["name"] for c in topped) else bad)(
            "top-up keeps the strict-rung hit",
            f"kept={strict[0]['name']!r} used={used_top!r}")

    # Abstract theme with no type anchor: generic rung must still explain itself.
    exp_brief = {
        "theme": "expensive",
        "commander_query": "is:commander o:expensive",
    }
    eladder, egeneric = app.commander_ladder(exp_brief)
    try:
        ecards, eused = await app.search_ladder(
            eladder, limit=25, floor=app.COMMANDER_FLOOR)
    except app.ScryfallError as e:
        live_skip("commander ladder expensive fallback", e)
        return
    if ecards and eused in egeneric:
        ok("expensive falls to generic with notice path", eused)
    elif ecards:
        # o:expensive might match something — still not a failure, but say so.
        ok("expensive found non-generic hits", eused)
    else:
        bad("expensive ladder returned nothing", str(eladder[:3]))


async def test_pressure():
    """Forge pressure test — .dck export, parse, absent engine, progressive poll."""
    print("\npressure dck / parse / absent / progressive")
    import tempfile
    import time as _time
    from pathlib import Path
    from unittest.mock import patch

    # --- .dck export ---
    dck = pressure.to_dck(
        "Test Deck",
        "Ellivere of the Wild Court",
        [{"name": "Sol Ring"}, {"name": "Forest"}, {"name": "Aura Gnarlid"}],
    )
    (ok if dck.startswith("[metadata]\nName=Test Deck\n") else bad)(
        "dck metadata header", dck[:40])
    eq("dck commander section", "1 Ellivere of the Wild Court" in dck, True)
    eq("dck main has Sol Ring", "1 Sol Ring" in dck, True)
    eq("dck deck type commander", "Deck Type=Commander" in dck, True)

    # --- stdout parse: win + turns ---
    sample_win = (
        "Game Outcome: Turn 12\n"
        "Game Outcome: Ai(1)-Deckwright Hero has won the game.\n"
        "Match Result: Ai(1)-Deckwright Hero: 1 Ai(2)-Ahoy Mateys: 0\n"
        "\nGame Result: Game 1 ended in 4321 ms. Ai(1)-Deckwright Hero has won!\n"
    )
    got = pressure.parse_sim_output(sample_win)
    eq("parse win", got["won"], True)
    eq("parse turns", got["turns"], 12)

    sample_loss = (
        "Game Outcome: Turn 9\n"
        "Game Result: Game 1 ended in 2100 ms. Ai(2)-Virtue and Valor has won!\n"
    )
    got = pressure.parse_sim_output(sample_loss)
    eq("parse loss", got["won"], False)
    eq("parse loss turns", got["turns"], 9)

    sample_draw = "Game Result: Game 1 ended in a Draw! Took 120000 ms.\n"
    got = pressure.parse_sim_output(sample_draw)
    eq("parse draw is not a win", got["won"], False)

    try:
        pressure.parse_sim_output("no result here\n")
        bad("parse missing result should raise")
    except ValueError:
        ok("parse missing result raises")

    # --- fixed opponents: one of each archetype ---
    # Baked precon JSON is not committed in the public tree (fetch/bake locally).
    archs = [o["archetype"] for o in pressure.OPONENTS]
    eq("three opponents", len(pressure.OPONENTS), 3)
    eq("archetypes aggro/midrange/control", sorted(archs),
       ["aggro", "control", "midrange"])
    missing_bakes = [
        opp for opp in pressure.OPONENTS
        if not (precon_bake.read_bake(opp["id"]) or {}).get("deck")
    ]
    if missing_bakes:
        print("  skip progressive Forge lobby — precon bakes not in public tree "
              f"({', '.join(o['id'] for o in missing_bakes)})")
    else:
        for opp in pressure.OPONENTS:
            baked = precon_bake.read_bake(opp["id"])
            (ok if baked and baked.get("deck") else bad)(
                f"opponent bake present ({opp['name']})", opp["id"])

    sched = pressure.opponent_schedule(10)
    eq("schedule length 10", len(sched), 10)
    counts = {o["name"]: sched.count(o) for o in pressure.OPONENTS}
    eq("schedule covers all three", len(counts), 3)
    eq("schedule totals 10", sum(counts.values()), 10)

    # --- absent engine ---
    def _no_java():
        return {"available": False, "reason": "java_missing",
                "detail": "Java is not installed; Forge needs a JRE to run."}

    def _no_forge():
        return {"available": False, "reason": "forge_missing",
                "detail": "Forge is not installed; set DECKWRIGHT_FORGE to the forge.jar path."}

    old_detect = pressure._detect_engine_fn
    old_run = pressure._run_one_game_fn
    old_store = pressure.STORE
    try:
        pressure._detect_engine_fn = _no_java
        store = pressure.PressureStore()
        out = store.start(commander="Krenko, Mob Boss",
                          deck=[{"name": "Mountain"}], games=1)
        eq("absent java available false", out.get("available"), False)
        eq("absent java reason", out.get("reason"), "java_missing")
        eq("absent java has detail", isinstance(out.get("detail"), str)
           and "Java" in out["detail"], True)
        eq("absent java no run_id", "run_id" not in out, True)

        pressure._detect_engine_fn = _no_forge
        out = store.start(commander="Krenko, Mob Boss",
                          deck=[{"name": "Mountain"}], games=1)
        eq("absent forge reason", out.get("reason"), "forge_missing")

        pressure._detect_engine_fn = _no_java
        post = await app.pressure_start(
            {"deck": [{"name": "Mountain"}], "commander": "Krenko, Mob Boss"})
        eq("POST absent available", post.get("available"), False)
        eq("POST absent reason", post.get("reason"), "java_missing")
        get = await app.pressure_status("nope")
        eq("GET absent available", get.get("available"), False)
        eq("GET absent reason", get.get("reason"), "java_missing")

        # --- progressive results with mocked Forge games ---
        # Needs baked precon opponents; those JSON files are not in the public tree.
        if missing_bakes:
            cmd = pressure.build_forge_cmd(
                "/bin/java", "/tmp/forge.jar", "hero.dck", "opp.dck")
            eq("cmd starts java -jar", cmd[:3],
               ["/bin/java", "-jar", "/tmp/forge.jar"])
            eq("cmd is sim", cmd[3], "sim")
            eq("cmd one game", "-n" in cmd and cmd[cmd.index("-n") + 1] == "1", True)
            eq("cmd commander format",
               "-f" in cmd and cmd[cmd.index("-f") + 1] == "Commander", True)
        else:
            results = [
                {"won": True, "turns": 12},
                {"won": False, "turns": 9},
                {"won": True, "turns": 14},
            ]
            calls = {"n": 0}

            def _fake_engine():
                return {"available": True, "java": "/bin/java", "forge": "/tmp/forge.jar"}

            def _fake_game(**kwargs):
                i = calls["n"]
                calls["n"] += 1
                _time.sleep(0.05)
                return results[i]

            pressure._detect_engine_fn = _fake_engine
            pressure._run_one_game_fn = _fake_game

            with tempfile.TemporaryDirectory() as tmp:
                deck_dir = Path(tmp) / "commander"
                deck_dir.mkdir()
                with patch.object(pressure, "forge_commander_dir", return_value=deck_dir):
                    store = pressure.PressureStore()
                    pressure.STORE = store
                    started = store.start(
                        commander="Ellivere of the Wild Court",
                        deck=[{"name": "Sol Ring"}, {"name": "Forest"}],
                        games=3,
                    )
                    eq("start available", started.get("available"), True)
                    run_id = started.get("run_id")
                    (ok if run_id else bad)("start returns run_id", str(started))

                    again = store.start(
                        commander="Ellivere of the Wild Court",
                        deck=[{"name": "Sol Ring"}, {"name": "Forest"}],
                        games=3,
                    )
                    eq("rate-limit same run_id", again.get("run_id"), run_id)

                    saw_partial = False
                    final = None
                    for _ in range(80):
                        view = store.get(run_id)
                        if view and view["done"] > 0 and not view["finished"]:
                            saw_partial = True
                        if view and view["finished"]:
                            final = view
                            break
                        _time.sleep(0.05)

                    (ok if saw_partial else bad)(
                        "finished games readable while later ones run",
                        f"done progression seen={saw_partial}")
                    (ok if final else bad)("run finished", str(final))
                    if final:
                        eq("record won", final["record"]["won"], 2)
                        eq("record lost", final["record"]["lost"], 1)
                        eq("turns min", final["turns"]["min"], 9)
                        eq("turns max", final["turns"]["max"], 14)
                        eq("done equals total", final["done"], 3)
                        eq("three opponent slips", len(final["opponents"]), 3)
                        eq("opponents ordered by archetype",
                           [o["archetype"] for o in final["opponents"]],
                           ["aggro", "midrange", "control"])
                        landed = sum(len(o["games"]) for o in final["opponents"])
                        eq("all games on opponent slips", landed, 3)

                    cmd = pressure.build_forge_cmd(
                        "/bin/java", "/tmp/forge.jar", "hero.dck", "opp.dck")
                    eq("cmd starts java -jar", cmd[:3],
                       ["/bin/java", "-jar", "/tmp/forge.jar"])
                    eq("cmd is sim", cmd[3], "sim")
                    eq("cmd one game", "-n" in cmd and cmd[cmd.index("-n") + 1] == "1", True)
                    eq("cmd commander format",
                       "-f" in cmd and cmd[cmd.index("-f") + 1] == "Commander", True)

        # Live absent-engine path on this machine (Forge is not installed).
        pressure._detect_engine_fn = None
        live = pressure.detect_engine()
        if not live.get("available"):
            eq("live detect returns reason",
               live.get("reason") in ("java_missing", "forge_missing"), True)
            (ok if isinstance(live.get("detail"), str) and live["detail"] else bad)(
                "live detect has plain detail", str(live))
        else:
            ok("live detect found Forge", live.get("forge", ""))

    finally:
        pressure._detect_engine_fn = old_detect
        pressure._run_one_game_fn = old_run
        pressure.STORE = old_store


async def test_scry_rate_limit():
    """throttled Scryfall must raise, never masquerade as zero cards."""
    print("\nscry()  — rate limit is loud, 404 is a real zero")
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    # with a loaded local corpus, ordinary searches never reach httpx.
    # Force the live path so this test still grades the rate-limit / 404 client.
    old_corpus = os.environ.get("DECKWRIGHT_CORPUS")
    os.environ["DECKWRIGHT_CORPUS"] = "off"
    try:
        # 404 → empty data, not an error.
        resp404 = MagicMock(status_code=404)
        with patch("deckwright.app.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=resp404)
            got = await app.scry("/cards/search", q="idontexistqqq")
        eq("404 returns empty data", got, {"data": []})

        # Persistent 429 → ScryfallError, not {"data": []}.
        resp429 = MagicMock(status_code=429, headers={"Retry-After": "0"})
        with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
             patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=resp429)
            try:
                await app.scry("/cards/search", q="t:elf")
                bad("429 should raise ScryfallError")
            except app.ScryfallError as e:
                eq("429 raises ScryfallError", e.status, 429)
                (ok if e.kind == "rate_limit"
                      or "unreachable" in str(e).lower() else bad)(
                    "429 message names failure", f"kind={e.kind} {e}")

        # Plain 503 (not a maintenance page) after retries → ScryfallError.
        resp503 = MagicMock(status_code=503, headers={}, text="")
        with patch("deckwright.app.httpx.AsyncClient") as mock_client, \
             patch("deckwright.app.asyncio.sleep", new_callable=AsyncMock):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=resp503)
            try:
                await app.scry("/cards/search", q="t:elf")
                bad("503 should raise ScryfallError")
            except app.ScryfallError as e:
                eq("503 raises ScryfallError", e.status, 503)
    finally:
        if old_corpus is None:
            os.environ.pop("DECKWRIGHT_CORPUS", None)
        else:
            os.environ["DECKWRIGHT_CORPUS"] = old_corpus


async def test_search_transport_failure_is_not_empty():
    """could-not-look must never read as there-are-none.

    `search()` used to swallow every non-Scryfall exception into `[]`. The
    commander ladder reads `[]` as "this rung has no cards" and walks past a
    rung it never actually saw — a thin pool then gets reported as a real one.
    An HTTP failure already raised ScryfallError, so callers tolerate it; a
    transport failure now raises the same thing.
    """
    print("\nsearch()  — transport failure is unknown, not zero")
    real = app.scry

    async def boom(*a, **k):
        raise RuntimeError("connection reset by peer")

    app.scry = boom
    try:
        await app.search("t:pirate")
        bad("transport failure raises rather than returning []",
            "returned normally — a failed look still reads as zero results")
    except app.ScryfallError as e:
        ok("transport failure raises ScryfallError", str(e))
    except Exception as e:  # noqa: BLE001 — any other type is also a failure
        bad("transport failure raises ScryfallError", f"got {type(e).__name__}: {e}")
    finally:
        app.scry = real

    # The other half: a real empty answer must still be an empty list, or we
    # have traded one lie for another.
    async def empty(*a, **k):
        return {"data": []}

    app.scry = empty
    try:
        eq("genuine empty result is still []", await app.search("t:notacreature"), [])
    finally:
        app.scry = real


async def main():
    await test_search_transport_failure_is_not_empty()
    test_singulars()
    test_budget_report()
    test_budget_price_ceiling()
    test_tcgplayer_mass_entry()
    test_commander_selection()
    await test_commander_unscoped_note()
    test_plain_commander_reason()
    test_validate_deck()
    test_partner_commanders()
    test_card_pool_modes()
    test_persist_deck()
    test_saved_deck_brief_name_delete()
    await test_suggested_name_never_overwrites_hers()
    test_build_returns_plan()
    test_conversation_on_saved_deck()
    test_match_notes_on_saved_deck()
    test_sim_baseline_cache()
    test_finish_mana_base()
    test_no_land_padding()
    test_mana_sizing()
    test_deck_signals()
    test_overview_verifier()
    test_substitution()
    test_land_ramp_policy()
    test_strategy_role_queries()
    test_type_targets()
    test_launchd_plist()
    await test_desktop_install_update()
    test_import_deck_parser()
    test_pick_commander()
    test_pick_commander_partners()
    test_infer_role()
    await test_scry_rate_limit()
    await app.load_vocab()
    v = app._vocab
    if v.get("type") and v.get("keyword"):
        print(f"{DIM}vocabulary: {len(v['type'])} types, {len(v['keyword'])} keywords{OFF}")
        test_classify()
        test_theme_clauses()
    else:
        warn("vocab cache empty — skipping classify/theme_clauses")
    if LIVE:
        print(f"\n{DIM}--live: running Scryfall-backed tests{OFF}")
        await test_commander_ladder()
        await test_commander_ladder_compose()
        await test_commander_ladder_colors()
        await test_commander_writeup_fallback()
        await test_vocabulary_sweep()
        await test_mechanics()
        await test_ramp_query_no_lands()
        await test_strategy_ramp_pools()
    else:
        print(f"\n{DIM}offline: skipping live Scryfall tests (pass --live to enable){OFF}")
    test_sim_baseline()
    test_sim_colored_mana()
    test_sim_sample_hands()
    test_saved_deck_slugs()
    test_precons_archidekt_conversion()
    await test_precons_cache_fallback()
    test_precon_brief_from_analysis()
    test_precon_bake_stamps()
    test_sim_seed_reproducible()
    await test_load_precon_serves_bake()
    test_import_double_faced()
    test_substitution_price_band()
    test_christen_guard()
    test_thesis_review()
    test_why_card()
    test_deck_context()
    test_sim_sees_card_draw()
    test_sim_sees_life_math()
    test_sim_sees_bodies()
    test_sim_sees_token_types()
    test_sim_sees_graveyard()
    test_sim_sees_anthems()
    test_card_score_marginal()
    test_card_score_skips_saturated()
    test_card_score_provisional_conditions()
    test_card_score_zero_delta_preserves_model_order()
    test_card_score_sim_pick_note()
    await test_card_score_derive_failed_reported()
    await test_card_score_gate_failed_open_reported()
    await test_partners_unknown_not_empty()
    await test_card_score_skip_prefers_glued()
    test_enforce_payoff_floors()
    test_enforce_tribe_floor()
    test_brief_extract_states()
    test_llm_backend_choice()
    await test_llm_cli_stdin_and_timeout()
    test_sheet()
    test_coverage()
    await test_coverage_role_count_unknown_aborts()
    test_calendar()
    await test_calendar_scry_failure_not_zero()
    test_calendar_panel_states()
    await test_calendar_first_paint_fast()
    await test_calendar_enrich_debut_mechanics()
    await test_calendar_endpoint_returns()
    test_playstyle_skeleton()
    test_apply_tweak()
    test_card_swap()
    await test_pressure()

    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {YELLOW}{warned} warned{OFF}" if warned else "")
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
