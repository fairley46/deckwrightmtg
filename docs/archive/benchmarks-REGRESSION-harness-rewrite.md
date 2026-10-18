# Regression — PR #322 rewrote the frozen harness and reverted the build-timeout floor and the per-contestant URL scoping

**Found:** 2026-09-02, validating the E/BLUE/D seed sweep.
**Run affected:** `docs/benchmarks/runs/20260902T172439Z` — **VOID, do not read as a result.**

---

## What happened

The seed sweep produced **BLUE 4 ok / 20 UNKNOWN, D 3 ok / 21 UNKNOWN** — seven usable builds out of 48, every failure `TimeoutError: timed out`.

## Root cause: a rewrite, not a patch

PR #322 added `scripts/v2_benchmark.py` with **1,606 insertions** — it recreated the file rather than editing it. Two prior fixes were lost with no diff for a reviewer to catch.

### Build timeout (found 24 Aug, cost a full sweep)

```python
# harness @ 877b024 (working):
DEFAULT_BUILD_TIMEOUT = 1800.0
def build_http(req, url, *, timeout: float = DEFAULT_BUILD_TIMEOUT) -> BuildResponse:

# main @ 2371f21 (after #322):
def build_http(req, url, *, timeout: float = 300.0) -> BuildResponse:
```

Also gone: `--build-timeout`, the `DECKWRIGHT_BUILD_TIMEOUT` env override, and `build_timeout_s` in the report header. The header printed `None` for this run — that is how it was spotted.

### Per-contestant URL scoping (found 28 Aug)

`http_build_url()`, `--green-b-url` and `--green-c-url` are gone. Only the unscoped `--green-url` remains — the exact flag that silently pointed contestant B at BLUE's port and would have produced a "steer does nothing" result nobody could explain.

## Why the run failed

Two causes compounding:

1. **Memory pressure** — 4.7 GB of 6.1 GB swap consumed with five KeepAlive servers running. Builds slowed from ~350 s to ~1,800 s.
2. **The reverted 300 s timeout** then cut off every build that ran long.

Both ports **actually completed 8 builds each**. The harness had already abandoned half of them, so the servers finished into a closed socket. That is worse than a clean failure: real work was done and thrown away.

## Fix

**Landed** (see harness tip): `DEFAULT_BUILD_TIMEOUT = 1800`, `--build-timeout`,
`DECKWRIGHT_BUILD_TIMEOUT`, `build_timeout_s` in the header; `http_build_url()`
with `--green-b-url` / `--green-c-url` / `--green-d-url`; regression tests in
`test_v2_benchmark.py` pin both floors so a rewrite cannot drop them silently.

1. Restore `DEFAULT_BUILD_TIMEOUT = 1800`, `--build-timeout`, the env override, and `build_timeout_s` in the header.
2. Restore `http_build_url()` with per-contestant scoping, plus `--green-b-url` / `--green-c-url`, and add `--green-d-url` for the new contestant.
3. **Add regression tests** asserting the default build timeout is >= 1800 and that per-contestant URL overrides do not cross-apply.

Point 3 is the one that matters. Both fixes were found by sweeps costing hours each; neither had a test pinning it. That is precisely why a rewrite could drop them silently, and why restoring them without tests would leave the same hole open.

## The wider rule

`scripts/v2_benchmark.py` is the frozen harness. `docs/product/b-then-c.md` says:

> A PR that edits `scripts/v2_benchmark.py` to help a contestant is rejected.

That was written against *tuning*, but the same reasoning covers *rewriting*. The file is frozen so measurements stay comparable across runs. A from-scratch rewrite discards accumulated fixes and presents as new-file insertions, which no reviewer reads line by line.

**Additions to the frozen harness should be edits, not replacements.** If a rewrite is genuinely warranted, it needs an explicit diff against the prior version as part of review.

## State at time of writing

Nothing promoted. `:8001` remains V1 with no seed flag. Blue `:8000` untouched, PID 1229, 25 days uptime. Green-b and green-c stopped separately — both disabled in the registry, and their resident memory contributed to the slowdown.
