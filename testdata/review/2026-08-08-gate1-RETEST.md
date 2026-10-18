# CON-215 Gate 1 — retest after CON-223 / CON-227 / GA stack

**Date:** 2026-08-08 · **main tip at retest:** post-`9de8b13` stack (CON-223…230)
**Method:** full UI, Playwright, real model + real Scryfall. No API shortcuts.

## Verdict

**Gate criterion PASSES.** Unclassifiable **0 / 0 / 0** across all three runs.
BUG-3 is closed, and with it the thing that failed Gate 1 on the first pass.

Prior product/UX defects from `2026-08-07-gate1-FULL-REPORT.md` are **fixed and
verified**. Four new defects opened below (BUG-8…11). CON-219/220 verified on
the precon path.

**Harness note:** BUG-8 looks like a test artifact at first glance. It is not.
`extract_brief_payload` fails to peel a trailing chat envelope when the CLI
model tapes JSON after prose; the frontend renders `r.text` honestly. Playwright
only photographed what the product painted.

---

## Fixed and verified

| Was | Now |
|---|---|
| BUG-1 readback duplicated | Three distinct rows + Theme as heard: pirates that steal |
| BUG-2 Fits your each strategy | Fits your artifact strategy |
| BUG-3 unclassifiable | 0 / 0 / 0 |
| BUG-4 long phrase in labels | 57 strategy only, not pirate |
| BUG-6 artifacts false positive | Cavalry now matches counters/lifegain; two commanders honestly show strategy: 0 |
| No build time estimate | "Usually 4–6 minutes" |
| No cohesion | 1643 glue edges |
| Auto-saves unlabelled | auto-saved · 2026-08-08 02:53 |

CON-219/220 both verified on the precon path, with the exact copy specified:
"Already in this deck — you don't need to buy these" — and Reckless Fireweaver
is in that list. Every add pairs with a cut.

---

## New defects

### BUG-8 · raw JSON in the chat (HIGH) — run 2 · CON-232

Run 2 printed the prose answer and then the model's envelope verbatim:

```json
{"reply": "…", "still_asking": true, "brief": null}
```

Primary surface, ugliest possible failure. Screenshot captured.

Not a harness bug: product parse miss on the CLI path (see Linear CON-232).

### BUG-9 · a named tribe was dropped (HIGH) — run 2 · CON-234

Brief says "horses and knights"; readback returned `cavalry knights` →
`t:knight` → 526 only. CON-209 still works — it just wasn't given a second
tribe, because extraction wrote "cavalry knights". The first Gate 1 pass
correctly gave 526 + 59 → 585.

### BUG-10 · budget regressed sharply (HIGH) — all runs · CON-233

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| First pass | +$30.85 | +$17.60 | +$6.67 |
| Retest | +$24.28 | +$58.98 | +$117.74 |

Run 3 is 78% over a $150 brief. Likely suspects are the things that changed
selection since — CON-214 steering, CON-230 glued backfill, CON-225
`TARGET_TURN` 10. Preferring glued cards plausibly prefers expensive staples.

Build times also crept (405s / 540s / ~640s) and break the 4–6 minute promise
the UI now makes.

### BUG-11 · contradictory advice (MEDIUM) — precon path · CON-235

Sai, Master Thopterist is listed as good synergy you already own and as the
cut for Padeem.

---

## My read (Claude)

The gate's stated criterion passes; b done. BUG-8 is a credibility failure on
the surface she uses first, and BUG-10 means the deck she's handed isn't the
one she asked to pay for. Both are worse than the thing that was failing the
gate.

---

## Follow-up cards

| Bug | Linear | Severity |
|---|---|---|
| Notes + GA-PATH gate green | CON-231 | — |
| BUG-8 raw JSON | CON-232 | HIGH |
| BUG-10 budget | CON-233 | HIGH |
| BUG-9 horses dropped | CON-234 | HIGH |
| BUG-11 already_in vs cut | CON-235 | MEDIUM |
