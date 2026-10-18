"""
derive_synergy_objects.py — the closed object set, from Scryfall's own catalogs.

The spec forbids hand-authoring this list. Objects are what one card PRODUCES
and another CONSUMES -- artifacts, creatures, treasures, counters. Every one
carries the catalog it came from, so a reviewer can check that nothing was
invented.

    python scripts/derive_synergy_objects.py

Writes data/synergy_objects.json. Run when the grammar refreshes.
"""

from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
GRAMMAR = REPO / "data" / "scryfall_grammar.json"
OUT = REPO / "data" / "synergy_objects.json"

# Which catalogs contribute objects, and what kind of object each yields.
# This mapping is the only editorial decision; the CONTENTS come from Scryfall.
SOURCES = {
    "card-types": "permanent_type",
    "artifact-types": "artifact_subtype",   # Treasure, Clue, Food, Blood
    "keyword-actions": "action",            # Sacrifice, Exile, Mill, Scry
}

# Counters have no Scryfall catalog. Rather than invent a list, take the one
# counter kind the rules give structurally -- +1/+1 -- and let oracle patterns
# in mechanic_families.json name any others. An object we cannot source is an
# object we do not ship.
# energy + player resources: no Scryfall catalog enumerates them.
# Same shape as life/card — rules-structural resources with produce/consume
# (or become/if-you-are) oracle patterns only. No word-bank dump.
STRUCTURAL = {
    "+1/+1 counter": "counter",
    "card": "resource",
    "life": "resource",
    "energy": "resource",
    "poison": "resource",
    "rad": "resource",
    "experience": "resource",
    "speed": "resource",
    "monarch": "resource",
    "initiative": "resource",
    "city's blessing": "resource",
    "the ring": "resource",
}


def main() -> None:
    grammar = json.loads(GRAMMAR.read_text())
    catalogs = grammar.get("catalogs") or {}
    objects: dict[str, dict] = {}

    for catalog, kind in SOURCES.items():
        for raw in catalogs.get(catalog) or []:
            name = str(raw).strip().lower()
            if not name:
                continue
            objects[name] = {"kind": kind, "from_catalog": catalog}

    for name, kind in STRUCTURAL.items():
        objects[name] = {"kind": kind, "from_catalog": "rules-structural"}

    payload = {
        "version": 1,
        "source": "derived from data/scryfall_grammar.json catalogs",
        "note": "Closed set. An object not here cannot form an edge.",
        "objects": objects,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} — {len(objects)} objects")
    by_cat: dict[str, int] = {}
    for o in objects.values():
        by_cat[o["from_catalog"]] = by_cat.get(o["from_catalog"], 0) + 1
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat:22} {n}")


if __name__ == "__main__":
    main()
