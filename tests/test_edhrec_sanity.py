"""
test_edhrec_sanity.py — transport vs catalogue for EDHREC 403s.

EDHREC serves static JSON from S3. A 403 AccessDenied is "no page" for a
missing key — correct. Across thousands of requests, a throttle that also
returns 403 would record every miss as "missing" and the run would look
honest while being wrong. The missing-rate check catches that.

    python test_edhrec_sanity.py
"""
from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load the builder module by path — it is not a package.
_spec = importlib.util.spec_from_file_location(
    "build_commanders_kb", ROOT / "scripts" / "build_commanders_kb.py"
)
assert _spec and _spec.loader
bkb = importlib.util.module_from_spec(_spec)
# Avoid running network imports that pull app at import time if possible —
# the module imports app. Load it the same way the builder is run.
sys.path.insert(0, str(ROOT / "scripts"))
_spec.loader.exec_module(bkb)

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
passed = failed = 0


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"{GREEN}  pass{OFF} {label}" + (f" {extra}" if extra else ""))


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{OFF} {label}" + (f" {extra}" if extra else ""))


def eq(label, got, want):
    (ok if got == want else bad)(label, f"got {got!r}, want {want!r}")


def test_missing_rate():
    print("\nedhrec missing-rate — transport vs catalogue")
    # Small samples: do not judge.
    v = bkb.edhrec_transport_check(ok=10, missing=8, failed=0)
    eq("small sample is skip", v["status"], "ok")
    eq("small sample not suspicious", v["suspicious"], False)

    # Measured on the commander knowledge base: ~6% missing is catalogue, not throttle.
    v = bkb.edhrec_transport_check(ok=3113, missing=208, failed=0)
    eq("6% missing is ok", v["status"], "ok")
    eq("6% not suspicious", v["suspicious"], False)

    # Half the catalogue "missing" with zero failures → transport lie.
    v = bkb.edhrec_transport_check(ok=1600, missing=1600, failed=0)
    eq("50% missing is fail", v["status"], "fail")
    eq("50% is suspicious", v["suspicious"], True)

    # High unknown rate is also a transport problem (different signal).
    v = bkb.edhrec_transport_check(ok=2000, missing=100, failed=1200)
    eq("high failed is fail", v["status"], "fail")
    eq("high failed is suspicious", v["suspicious"], True)


def main() -> int:
    test_missing_rate()
    print(f"\n{GREEN}{passed} passed{OFF}"
          + (f", {RED}{failed} FAILED{OFF}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
