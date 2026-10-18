# Gate 5B — Bello solo: the MV-floor arm, finally measured

**Run:** `docs/benchmarks/runs/20260825T003009Z` · harness `3224c59`
**Green:** `gate4b-integration` @ `e44e92d` — floors #305 + coverage #307 + steer stamps #304
**Contestants:** E, BLUE (`:8001` off), B (`:8002` candidate=B) · filter `bello` · **3/3 ok, no UNKNOWN**

Header confirms provenance:
`green_urls: BLUE → http://127.0.0.1:8001, B → http://127.0.0.1:8002` · `commanders (filter): bello`
`build_timeout_s: 1800` · `recommended_llm_timeout_s: 600`

Routing verified independently: `:8001` and `:8002` each logged **exactly 1** build against pre-recorded baselines (77 / 51).

---

## Result — the floor arm works

| contestant | `mv_geq_4` violations | feeder density | keepable | wall-clock |
|---|---|---|---|---|
| E (EDHREC) | 4 | 0.192 | 69.5% | 2 s |
| BLUE (V1) | **17** | 0.343 | 83.5% | 571 s |
| **B (steer)** | **8** | 0.303 | 83.5% | 315 s |

**BLUE 17 → B 8. A 53% cut in floor violations.**

These are real `op: geq` violations, not churn. The nine cards steer removed are precisely the ones Bello can never animate — sub-4 mana rocks and cheap permanents:

> `Commander's Sphere`, `Everflowing Chalice`, `Mind Stone`, `Ichor Wellspring`, `Herd Heirloom`, `Klothys`, `Anax`, `Azusa's Many Journeys`, `Jugan Defends the Temple`

Bello animates only non-Equipment artifacts and non-Aura enchantments with **MV ≥ 4**. A 2-mana Mind Stone is dead weight for that plan, and V1 put five such rocks in the list. Steer took them out for the stated reason.

**This is the first time `op: geq` has run end to end against a real build.** #305's actual contribution is now measured, not inferred. Tayam's earlier 10 → 7 was a *ceiling*; this is the floor.

---

## Why Gate 5B failed here and this did not

Gate 5B's Bello rows were UNKNOWN on both ports: `claude cli exited 1` on `:8001`, `timed out after 180s` on `:8002`.

**BLUE took 571 s in this run.** That is 3.2× the old 180 s `DECKWRIGHT_LLM_TIMEOUT`, so the timeout diagnosis was exactly right — Bello is simply the heaviest build in the frozen set, and the limit sat below it. Raising the green servers to 600 s resolved it. The harness's own 1800 s HTTP budget was never the constraint and was left alone.

Verified on the **running processes**, not just the plists — `ps eww` on both python PIDs showed `DECKWRIGHT_LLM_TIMEOUT=600`.

That 600 must live in the **repo plist templates**
(`(local launchd label)` / `green-b.plist`). Hand-restoring
`~/Library/LaunchAgents/` alone is wiped by the next `./install-green-*.sh`
run — which is exactly how Ship B re-broke Bello.

---

## Honest caveats

* **B still trails E on the metric.** 8 violations against EDHREC's 4. Steer halved the gap; it did not close it.
* **Feeder density went slightly down** — 0.343 → 0.303. On this commander the bound arm traded against the input arm. One data point, but worth watching: the two arms are not automatically aligned.
* **Keepable hands unchanged** at 83.5% — no castability cost.
* **n = 1.** This is one commander, and the only MV-floor commander in the frozen 24. It is decisive for "does the floor arm function", not for "how much does it help in general".
* E's row carries `unknown: 1` inside its violation detail; B and BLUE are `unknown: 0`.

---

## What this does and does not unlock

**Does:** closes the floor-case measurement. The floor case is measured. B's bound arm demonstrably works on both a ceiling (Tayam 10 → 7) and a floor (Bello 17 → 8).

**Does not:** justify C. Four of six bound-declaring commanders still extract `NONE` (Meren, Sliver, Shirei, Maralen). The cheap next move remains extending bound vocabulary — not a CardFacts rewrite. The long tail is still 3-of-4 unread.

**Does not:** justify promotion. B trails E on the bound metric here and gave up a little feeder density. Blue stays.

---

## Summary

| question | answer |
|---|---|
| Is the MV-floor arm measured? | **Yes** — first time |
| Does it work? | **Yes** — 17 → 8, 53% cut, correct cards removed |
| Was the Gate 5B failure explained? | **Yes** — BLUE needs 571 s, old limit was 180 s |
| Cost? | B *faster* than BLUE (315 s vs 571 s) |
| Start C? | **No** |
| Promote? | **No** |
