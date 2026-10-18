# VOID — do not read as a result

**Run id:** `20260902T172439Z`  
**Why void:** PR #322 harness rewrite reverted CON-328 (`DEFAULT_BUILD_TIMEOUT`
300s instead of 1800s). Sweep failed with mass `TimeoutError`; servers still
finished builds into closed sockets.

See `docs/benchmarks/REGRESSION-322-harness-rewrite.md`.

Any citation of this run's BLUE/D ok counts, feeder density, or keepable % is
invalid. Re-run after timeout restore.
