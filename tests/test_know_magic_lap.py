"""Know Magic lap admission record."""

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
LAP = json.loads((ROOT / "data" / "know_magic_lap.json").read_text())
SIGS = json.loads((ROOT / "data" / "mechanic_families.json").read_text())[
    "oracle_signals"
]


class KnowMagicLap(unittest.TestCase):
    def test_meta(self):
        self.assertIn("admitted_oracle_signals", LAP)
        self.assertIn("coverage", LAP)
        self.assertNotIn("issue", LAP)
        self.assertNotIn("parent", LAP)

    def test_admitted_signals_exist(self):
        for batch, names in LAP["admitted_oracle_signals"].items():
            for name in names:
                with self.subTest(batch=batch, name=name):
                    self.assertIn(name, SIGS)

    def test_coverage_moved(self):
        after = LAP["coverage"]["after_lap"]
        delta = LAP["coverage"]["delta_vs_pre_con245_full"]
        self.assertGreaterEqual(after["at_least_one_signal_pct"], 64.0)
        self.assertGreaterEqual(delta["at_least_one_signal_pp"], 13.0)
        self.assertEqual(after["unknown_unreadable"], 0)

    def test_precision_held(self):
        self.assertTrue(LAP["precision"]["held"])
        self.assertGreaterEqual(
            LAP["precision"]["after_pct"],
            LAP["precision"]["floor_pct"],
        )

    def test_next_batch_named(self):
        self.assertTrue(LAP["next_batch"]["priority"])
        self.assertFalse(LAP["load_model"]["sqlite"])


if __name__ == "__main__":
    unittest.main()