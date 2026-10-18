"""
check.py — run this first.

Tests the things that can be broken independently:
  1. Can we reach Scryfall and does it like our queries?
  2. Can we reach Claude (CLI by default, API as fallback)?
  3. Does the simulator produce sane numbers?
  4. Can this machine play real games? (optional — Java + Forge)

    python check.py            # generic pirate theme
    python check.py landfall UG # your theme, your colors
"""

import os, sys, asyncio, json
import httpx

THEME = sys.argv[1] if len(sys.argv) > 1 else "pirate"
CI = sys.argv[2] if len(sys.argv) > 2 else "UB"
UA = {"User-Agent": "Deckwright/1.0 (local personal deckbuilder)", "Accept": "application/json"}

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


async def q(client, label, query):
    try:
        r = await client.get("https://api.scryfall.com/cards/search",
                             params={"q": query, "order": "edhrec"}, headers=UA, timeout=20)
        if r.status_code == 404:
            print(f"{RED}  0  {OFF}{label:10} {DIM}{query}{OFF}")
            return 0
        r.raise_for_status()
        j = r.json()
        n = j.get("total_cards", 0)
        sample = j["data"][0]["name"] if j.get("data") else ""
        tone = GREEN if n >= 10 else RED
        print(f"{tone}{n:5}{OFF} {label:10} {DIM}{query}{OFF}")
        if sample:
            print(f"      {DIM}e.g. {sample}{OFF}")
        return n
    except Exception as e:
        print(f"{RED}  ERR{OFF} {label:10} {e}")
        return 0


async def main():
    print(f"\nTheme {THEME!r}, color identity {CI!r}\n")

    # Import the real queries from app.py rather than re-typing them here.
    # A hand-copied duplicate drifts, and then check.py passes while the app fails.
    from deckwright import app
    brief = {"theme": THEME, "colors": CI, "flavor_terms": []}
    cmdr_ladder, generic = app.commander_ladder(brief)
    ladders = {"commander": cmdr_ladder}
    floors = {"commander": app.COMMANDER_FLOOR}
    for role, target, _ in app.ROLES:
        ladders[role] = app.role_query(role, CI, brief, 5.0)
        floors[role] = min(target, 10)

    async with httpx.AsyncClient() as c:
        counts, landed = {}, {}
        for label, ladder in ladders.items():
            best = 0
            for query in ladder:
                n = await q(c, label, query)
                await asyncio.sleep(0.15)      # stay under Scryfall's rate limit
                if n > best:
                    best, landed[label] = n, query
                if n >= floors[label]:
                    break                      # this rung is good enough; stop climbing
            counts[label] = best

    thin = [k for k, v in counts.items() if v < floors[k]]
    if landed.get("commander") in generic:
        print(f"\n{RED}Commander search fell through to generic — the app will warn the")
        print(f"user their commanders are off-theme.{OFF}")
    fellback = [k for k, l in ladders.items() if len(l) > 1 and landed.get(k) not in (l[0], None)]
    print()
    if fellback:
        print(f"{DIM}Fell back past the first query for: {', '.join(fellback)}{OFF}")
    if thin:
        print(f"{RED}Thin or empty after every fallback: {', '.join(thin)}{OFF}")
        print(f"{DIM}Add a rung to role_query() in app.py for these roles.")
        print(f"Common cause: the theme is neither a creature type nor a word printed")
        print(f"on cards. 'pirate' and 'treasure' work; 'heists' and 'ships' do not.")
        print(f"Try an otag: (Scryfall's function tags) or pick a more concrete theme.{OFF}")
    else:
        print(f"{GREEN}All roles returning results.{OFF}")

    # --- model (Anthropic Claude via CLI preferred; API is fallback) ---
    print()
    from deckwright import llm
    print(f"{DIM}model calls -> {app.LLM_VIA}{OFF}")
    if not llm.can_call(app.client):
        print(f"{RED}No `claude` CLI and no ANTHROPIC_API_KEY.{OFF}")
        print(f"{DIM}Install the `claude` CLI, or set a key. Prefer ./run.sh "
              f"so PATH finds the CLI.{OFF}")
    else:
        try:
            r = await app.client.messages.create(
                model=app.MODEL, max_tokens=10,
                messages=[{"role": "user", "content": "say ok"}])
            text = (r.content[0].text if r.content else "")[:40]
            print(f"{GREEN}Model reachable.{OFF} {DIM}(reply={text!r}){OFF}")
        except Exception as e:
            print(f"{RED}Model call failed: {e}{OFF}")
            if getattr(app.client, "via", None) == "api":
                print(f"{DIM}API path failed — install the `claude` CLI and "
                      f"restart with ./run.sh (it prefers the CLI).{OFF}")

    # --- mechanics catalogs (CON-130) ---
    print()
    try:
        from deckwright import mechanics
        cat = await mechanics.fetch_catalog(app.scry)
        missing = mechanics.unreviewed(cat)
        n_ab = len(cat.get("keyword_abilities") or [])
        n_act = len(cat.get("keyword_actions") or [])
        n_aw = len(cat.get("ability_words") or [])
        print(f"{GREEN}Mechanics catalogs OK.{OFF} {DIM}{n_ab} abilities, "
              f"{n_act} actions, {n_aw} ability words; "
              f"{len(missing)} unreviewed vs family taxonomy{OFF}")
        if missing[:5]:
            print(f"      {DIM}e.g. {', '.join(missing[:5])}{OFF}")
    except Exception as e:
        print(f"{RED}Mechanics catalogs failed: {e}{OFF}")

    # --- simulator ---
    print()
    try:
        import random
        from deckwright import sim
        random.seed(1)
        deck = ([{"name": f"L{i}", "type": "Land", "cmc": 0, "text": "{T}: Add {G}."} for i in range(36)]
                + [{"name": f"R{i}", "type": "Artifact", "cmc": 2,
                    "text": "{T}: Add one mana of any color."} for i in range(10)]
                + [{"name": f"P{i}", "type": "Creature", "cmc": 3,
                    "text": "Whenever a land enters, create a token."} for i in range(12)])
        deck += [{"name": f"F{i}", "type": "Instant", "cmc": 3, "text": "Counter target spell."}
                 for i in range(99 - len(deck))]
        out = sim.run(deck, 5, {f"P{i}" for i in range(12)},
                      [{"label": "Commander down", "check": {"commander_in_play": True}},
                       {"label": "Seven mana", "check": {"mana_available": 7}}], runs=1500)
        c1, c2 = out["curves"]
        print(f"{GREEN}Simulator OK.{OFF} {DIM}Commander by T5 {c1['points'][4]}%, "
              f"seven mana by T5 {c2['points'][4]}% — measured baseline for this test deck "
              f"is ~70% and ~10%. Wildly different means extraction broke.{OFF}")
    except Exception as e:
        print(f"{RED}Simulator failed: {e}{OFF}")

    # --- the pressure test, optional by design -------------------------------
    # Whether this machine can play real games is a property of the machine, not
    # something to put in front of someone building a deck. It is reported here,
    # to whoever set the machine up, and the app simply omits the feature when
    # it is missing. Nothing else in Deckwright depends on either of these.
    print()
    import shutil
    java, forge = shutil.which("java"), shutil.which("forge")
    if java and forge:
        print(f"{GREEN}Real games available.{OFF} {DIM}java at {java}, forge at {forge}{OFF}")
    else:
        missing = " and ".join(n for n, v in (("Java", java), ("Forge", forge)) if not v)
        print(f"{DIM}Real games unavailable — {missing} not found. This is optional: "
              f"the app hides the pressure test and everything else works. "
              f"To enable it: brew install openjdk, then install Forge from "
              f"github.com/Card-Forge/forge (release tarball — no Homebrew cask). "
              f"See docs/OPERATIONS.md.{OFF}")
    print()


asyncio.run(main())
