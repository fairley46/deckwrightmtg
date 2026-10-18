"""Know Magic lap 2 admission."""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAP = json.loads((ROOT / "data" / "know_magic_lap2.json").read_text())
SIGS = json.loads((ROOT / "data" / "mechanic_families.json").read_text())[
    "oracle_signals"
]


class KnowMagicLap2(unittest.TestCase):
    def test_meta(self):
        self.assertIn("admitted", LAP)
        self.assertNotIn("issue", LAP)

    def test_signals_admitted(self):
        for name in LAP["admitted"]["oracle_signals"]:
            self.assertIn(name, SIGS)

    def test_keyword_catalog_on(self):
        self.assertTrue(LAP["admitted"]["keyword_catalog_reading"])

    def test_coverage_moved(self):
        after = LAP["coverage"]["after_lap"]
        self.assertGreaterEqual(after["at_least_one_signal_pct"], 66.0)
        self.assertGreaterEqual(after["signal_or_keyword_pct"], 80.0)
        self.assertTrue(LAP["precision"]["held"])

    def test_laps_left_stated(self):
        self.assertIn("productive_laps_left", LAP["laps_remaining_estimate"])


if __name__ == "__main__":
    unittest.main()
