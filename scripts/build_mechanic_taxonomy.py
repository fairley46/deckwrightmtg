#!/usr/bin/env python3
"""
Build data/mechanic_taxonomy.json from the play-structure menu.

Source: Brad's mechanics menu (named mechanics only, paper, Mar 2026).
Categorized by how they play, not CR implementation.

Each leaf carries:
  name, path, how, supports{enablers, payoffs, queries}, evergreen, deciduous

Queries are Scryfall-facing shapes, never card names. Default emission picks
`keyword:` / `t:` / `o:` from Scryfall's catalogs — never invents a
`keyword:` for a type or a non-indexed mechanic. Apostrophes stay (Council's
Dilemma → keyword:"council's dilemma"). The build fails if any emitted
`keyword:` or `t:` term is absent from those catalogs.

Run: python scripts/build_mechanic_taxonomy.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "mechanic_taxonomy.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Menu as nested tuples: (label, children|None)
# Labels may include (E)/(D) and "/" composites — flattened at emit time.
# Category-only nodes have children; mechanic leaves are strings.

MENU: list[Any] = [
    ("Formats", [
        ("Casting", None),  # section header only — skipped as leaf
        ("Combat", None),
        ("Commander", [
            "Partner",
            "Friends Forever",
            "Choose a Background",
            "Eminence",
            "Lieutenant",
            "Doctor's Companion",
        ]),
        ("Planechase", ["Planeswalk"]),
        ("Archenemy", ["Set In Motion", "Abandon"]),
        ("Conspiracy", ["Hidden Agenda", "Double Agenda"]),
    ]),
    ("Additional Costs", [
        ("Non-Mana Costs", [
            "Sweep", "Conspire", "Casualty", "Bargain", "Collect Evidence",
            "Forage", "Gift", "Behold (D)",
        ]),
        ("Binary", [
            "Kicker (D)", "Spree", "Tiered", "Buyback", "Entwine", "Offspring",
        ]),
        ("Variable", [
            "Multikicker", "Replicate", "Escalate", "Strive", "Squad", "Ravenous",
        ]),
    ]),
    ("Alternate Costs", [
        ("Increased / Reduced Effect", [
            "Plot", "Impending", "Suspend", "Warp", "Evoke", "Prototype",
            "Awaken", "Dash", "Blitz", "Overload", "Bestow", "Mutate",
            "Cleave", "Adventure", "Split Cards / Fuse", "Omen",
            "More Than Meets the Eye", "Modal Double-Faced Cards",
        ]),
        ("Different Effect", None),
    ]),
    ("Face-Down Cards", [
        "Morph", "Megamorph", "Manifest / Manifest Dread", "Foretell",
        "Disguise", "Cloak",
    ]),
    ("Variable Cost Reduction", ["Affinity (D)", "Undaunted"]),
    ("Situational Reduced Costs", ["Surge", "Prowl", "Spectacle"]),
    ("Alternatives to Mana", [
        "Offering", "Convoke", "Improvise", "Delve", "Assist",
        "Emerge / Emerge from Artifact", "Phyrexian Mana / Compleated",
        "Web-Slinging / Enweb", "Waterbend", "Sneak",
    ]),
    ("Timing", ["Flash (E)", "Split Second", "Addendum"]),
    ("Mana Spent to Cast", ["Adamant", "Sunburst", "Converge"]),
    ("When You Cast This", [
        "Storm", "Ripple", "Cascade", "Gravestorm", "Demonstrate", "Radiance",
    ]),
    ("Delayed Re-Cast", ["Epic", "Rebound", "Cipher", "Paradigm"]),
    ("Can / Can't Attack / Block", [
        ("Can / Can't Attack", ["Defender (E)", "Haste (E)"]),
        ("Evasion", [
            "Landwalk", "Horsemanship", "Flying (E) / Reach (E)", "Shadow",
            "Fear", "Intimidate", "Menace (E) / Suspected", "Skulk (D)",
        ]),
    ]),
    ("Attacking Triggers", [
        ("Add +1/+1 Counter", ["Dethrone", "Mentor", "Training"]),
        ("Increase Power / Toughness", ["Exalted", "Battle Cry", "Enlist", "Melee"]),
        ("Conditional", ["Battalion", "Pack Tactics"]),
        ("Other", ["Provoke", "Annihilator", "Myriad", "Mobilize", "Firebending"]),
    ]),
    ("Blocking / Not Blocking", [
        "Frenzy", "Flanking", "Bushido", "Rampage", "Afflict",
    ]),
    ("Saboteur Abilities", ["Poisonous", "Toxic", "Renown", "Ingest"]),
    ("End of Combat", ["Decayed"]),
    ("Combat Rules Modification", [
        "Double Strike (E)", "First Strike (E)", "Trample (E)", "Vigilance (E)",
        "Banding", "Space Sculptor",
    ]),
    ("Actions", [
        ("Tokens", [
            ("Token Types", [
                "Investigate / Clue", "Incubate / Incubator", "Treasure (E)",
                "Gold", "Food", "Powerstone",
                "Role / Cursed Role / Monster Role / Royal Role / Sorcerer Role / Virtuous Role / Wicked Role / Young Hero Role",
                "Map", "Junk", "Lander", "Mutagen",
            ]),
            ("Token Actions", [
                "Populate", "Amass", "Living Weapon", "For Mirrodin!", "Job Select",
            ]),
        ]),
        ("Change Sides", ["Meld", "Transform (D)", "Flip"]),
        ("Distribute Counters", [
            "Bolster", "Support", "Endure", "Proliferate", "Time Travel",
            "Earthbend", "Blight",
        ]),
        ("Temporary Removal", ["Exert", "Detain", "Goad (D)", "Airbend"]),
        ("Top of Library Manipulation", [
            "Scry (E)", "Fateseal", "Clash", "Explore", "Surveil (D)",
            "Connive", "Parley",
        ]),
        ("Outside the Game", ["Companion", "Learn"]),
        ("New Components", [
            "Initiative", "Venture into the Dungeon / Undercity",
            "Day / Night / Daybound / Nightbound", "Ascend", "Monarch",
            "Attraction", "Contraption / Assemble", "The Ring Tempts You",
            "Start Your Engines!",
        ]),
        ("Multiplayer / Opponent", [
            "Vote", "Council's Dilemma", "Join Forces", "Tempting Offer",
            "Will of the Council", "Villainous Choice",
        ]),
        ("Cast Spells", ["Discover"]),
    ]),
    ("Activated", [
        ("Plumbing", None),
        ("Hand Abilities", [
            ("Discard for Alternate Effect", [
                "Cycling (D)", "Transmute", "Channel", "Reinforce", "Bloodrush",
                "Typecycling",
            ]),
            ("Play at Specific Time", ["Madness", "Mayhem", "Ninjutsu", "Miracle"]),
            ("Repeatable", ["Splice onto", "Forecast"]),
        ]),
        ("Cast from Graveyard", [
            ("Once", [
                ("Same as From Hand", [
                    "Jump-Start", "Flashback (D)", "Harmonize", "Embalm",
                    "Eternalize", "Unearth", "Encore", "Scavenge", "Renew",
                    "Disturb", "Aftermath",
                ]),
                ("Different from From Hand", None),
            ]),
            ("Repeatedly", ["Retrace", "Escape"]),
        ]),
        ("Return from Graveyard", ["Dredge", "Recover"]),
        ("Battlefield", [
            ("Unified Cost / Situation", [
                "Cohort", "Boast", "Grandeur", "Saddle", "Exhaust (D)",
                "Power-up", "Prepared",
            ]),
            ("Swap Out", ["Aura Swap", "Transfigure"]),
            ("Improve This Permanent", [
                "Monstrosity", "Adapt", "Level Up", "Outlast", "Craft",
            ]),
            ("Change Types", ["Reconfigure"]),
        ]),
    ]),
    ("Basic Actions", [
        "Activate (E)", "Cast (E)", "Play (E)", "Create (E)",
    ]),
    ("Movement Between Zones", [
        "Counter (E)", "Destroy (E)", "Draw (E)", "Discard (E)", "Exile (E)",
        "Mill (E)", "Sacrifice (E)", "Ante",
    ]),
    ("Manipulating Permanents", [
        "Attach (D)", "Double (D)", "Exchange (D)", "Tap (E)", "Untap (E)",
    ]),
    ("Library Manipulation", ["Shuffle (E)", "Search (E)"]),
    ("Information", ["Reveal (E)"]),
    ("Creature Related Actions", ["Fight (E)"]),
    ("Conditions", [
        ("Variables", [
            "Devotion", "Party", "Chroma", "Domain", "Undergrowth",
            "Fathomless Descent", "Vivid",
        ]),
        ("Thresholds", [
            ("Battlefield", ["Coven", "Metalcraft", "Ferocious", "Formidable"]),
            ("Graveyard", ["Delirium", "Spell Mastery", "Threshold", "Descent"]),
            ("Event Occurred this Turn", [
                "Morbid", "Raid", "Revolt", "Celebration", "Descend",
                "Expend", "Void", "Infusion",
            ]),
            ("Other", ["Hellbent", "Fateful Hour", "Corrupted"]),
        ]),
        ("Batching", ["Historic", "Modified", "Commit a Crime", "Outlaw"]),
    ]),
    ("Triggers", [
        ("Rules Modification", None),
        ("This Enters the Battlefield", [
            ("Exile Card", ["Hideaway", "Imprint"]),
            ("Remove Creature", ["Champion", "Exploit"]),
            ("Other", ["Partner With"]),
        ]),
        ("ETB with Counters", [
            ("If Conditions are Met", ["Amplify", "Bloodthirst"]),
            ("As a Choice", [
                "Devour", "Tribute", "Fabricate", "Riot", "Unleash", "Backup",
            ]),
        ]),
        ("Other Creatures ETB", ["Graft", "Evolve", "Soulbond", "Rally", "Alliance"]),
        ("Other Permanents ETB", ["Constellation", "Landfall (D)", "Eerie"]),
        ("You Cast a Spell", [
            "Extort", "Prowess (D)", "Magecraft", "Flurry", "Paradox",
            "Opus", "Repartee", "Increment",
        ]),
        ("Start of Turn", [
            ("Negative", ["Cumulative Upkeep", "Phasing", "Echo", "Fading", "Vanishing"]),
            ("Positive", ["Kinship"]),
        ]),
        ("This Dies", [
            "Modular", "Soulshift", "Haunt", "Persist", "Undying", "Afterlife",
        ]),
        ("Unique Triggers", [
            "Enrage", "Inspired", "Heroic", "Valiant", "Gotcha", "Survival",
        ]),
    ]),
    ("Protect Permanents", [
        ("From Targeting", ["Hexproof (E)", "Shroud", "Ward (E)"]),
        ("From Destruction", ["Indestructible (E)", "Totem Armor", "Regenerate"]),
        ("From Damage", ["Absorb"]),
        ("From a Few Things", ["Protection (D)"]),
    ]),
    ("Damage Modification", [
        "Deathtouch (E)", "Lifelink (E)", "Wither", "Infect",
    ]),
    ("Counters", [
        ("Permanents", [
            "+1/+1 (E)", "-1/-1", "Stun (D)", "Shield", "Ability Counters (D)",
            "Stickers / Tickets", "Finality (D)",
        ]),
        ("Players", ["Energy", "Experience", "Rad", "Poison (D)"]),
    ]),
    ("Subtypes and Properties", [
        ("With their own Rules", [
            "Equipment / Equip (E)", "Aura / Enchant (E)", "Fortification / Fortify",
            "Saga / Read Ahead (D)", "Class", "Vehicle / Crew / Living Metal (D)",
            "Augment / Host", "Battle / Siege", "Spacecraft / Station",
            "Case", "Room",
        ]),
        ("As a Tag", ["Curse", "Trap", "Tribal", "Changeling", "Devoid"]),
    ]),
]

# Pure section headers that must never become mechanic leaves.
SKIP_AS_LEAF = frozenset({
    "Casting", "Combat", "Plumbing", "Rules Modification", "Different Effect",
    "Different from From Hand",
})

# Path prefixes → short "how" templates and support shapes.
HOW_BY_PREFIX: list[tuple[tuple[str, ...], str, dict[str, list[str]]]] = [
    (("Additional Costs",), "Pay an extra cost for a mode or bonus",
     {"enablers": ["mana", "sacrifice fodder", "tokens"], "payoffs": ["modal spells"], "queries": []}),
    (("Alternate Costs",), "Replace or reshape the mana price; often changes the effect",
     {"enablers": ["alternate-cost enablers"], "payoffs": ["cheap threats"], "queries": []}),
    (("Face-Down Cards",), "Cheap face-down body or set-aside card; pay later to turn up / cast",
     {"enablers": ["face-down payoffs"], "payoffs": ["etb on turn-face-up"], "queries": []}),
    (("Variable Cost Reduction",), "Cost drops from a countable board/resource predicate",
     {"enablers": ["artifacts", "board presence"], "payoffs": ["big spells"], "queries": []}),
    (("Situational Reduced Costs",), "Cheaper when a combat or life condition is true",
     {"enablers": ["aggression", "life payment"], "payoffs": ["tempo spells"], "queries": []}),
    (("Alternatives to Mana",), "Spend non-mana resources (tap, exile, life) toward the cost",
     {"enablers": ["creatures", "artifacts", "graveyard"], "payoffs": ["under-costed spells"], "queries": []}),
    (("Timing",), "Changes when you may cast or resolve",
     {"enablers": [], "payoffs": ["interaction windows"], "queries": []}),
    (("Mana Spent to Cast",), "Value scales with colors or amount of mana spent",
     {"enablers": ["multicolor mana"], "payoffs": ["sunburst/converge counters"], "queries": []}),
    (("When You Cast This",), "Extra value on cast — chains with spell velocity",
     {"enablers": ["cheap spells", "rituals"], "payoffs": ["storm-like chains"], "queries": []}),
    (("Delayed Re-Cast",), "Cast once, get another use later",
     {"enablers": ["cantrips"], "payoffs": ["recast value"], "queries": []}),
    (("Can / Can't Attack / Block", "Evasion"), "Changes which blockers are legal",
     {"enablers": [], "payoffs": ["saboteur", "connect damage"], "queries": []}),
    (("Can / Can't Attack / Block",), "Controls attack/block legality or haste windows",
     {"enablers": [], "payoffs": ["combat connection"], "queries": []}),
    (("Attacking Triggers",), "Value on attack declaration",
     {"enablers": ["hasty bodies", "go-wide"], "payoffs": ["attack triggers"], "queries": []}),
    (("Blocking / Not Blocking",), "Combat math when blocked or unblocked",
     {"enablers": ["forced blocks"], "payoffs": ["combat damage"], "queries": []}),
    (("Saboteur Abilities",), "Value on dealing combat damage to a player",
     {"enablers": ["evasion"], "payoffs": ["poison", "counters", "exile"], "queries": []}),
    (("Combat Rules Modification",), "Changes strike order, trample, or vigilance math",
     {"enablers": [], "payoffs": ["combat damage"], "queries": []}),
    (("Actions", "Tokens"), "Creates or manipulates tokens — creature vs resource is different math",
     {"enablers": ["token makers"], "payoffs": ["anthems", "aristocrats", "convoke"], "queries": []}),
    (("Actions", "Change Sides"), "Permanent changes face or merges",
     {"enablers": ["transform payoffs"], "payoffs": ["etb on transform"], "queries": []}),
    (("Actions", "Distribute Counters"), "Moves or adds counters across permanents/players",
     {"enablers": ["counter density"], "payoffs": ["proliferate loops"], "queries": []}),
    (("Actions", "Temporary Removal"), "Soft removal or goad — tempo, not hard exile",
     {"enablers": [], "payoffs": ["attack redirection"], "queries": []}),
    (("Actions", "Top of Library Manipulation"), "Orders or filters the top of the library",
     {"enablers": [], "payoffs": ["hit lands/spells on time"], "queries": []}),
    (("Actions", "New Components"), "Introduces a tracked shared component",
     {"enablers": ["component payoffs"], "payoffs": ["monarch/initiative value"], "queries": []}),
    (("Actions", "Outside the Game"), "Touches sideboard / learning pile",
     {"enablers": ["companion legal deck"], "payoffs": ["lessons"], "queries": []}),
    (("Actions", "Multiplayer / Opponent"), "Voting / offers — multiplayer politics math",
     {"enablers": [], "payoffs": ["vote payoffs"], "queries": []}),
    (("Actions", "Cast Spells"), "Finds and casts a spell from a revealed set",
     {"enablers": ["cascade-like"], "payoffs": ["free spells"], "queries": []}),
    (("Activated", "Hand Abilities"), "Use cards from hand without (or beside) casting them normally",
     {"enablers": ["discard outlets"], "payoffs": ["yard fill", "selection"], "queries": []}),
    (("Activated", "Cast from Graveyard"), "Yard is a second hand — cast from graveyard",
     {"enablers": ["self-mill", "discard", "loot"], "payoffs": ["spell-cast triggers", "graveyard thresholds"], "queries": []}),
    (("Activated", "Return from Graveyard"), "Returns from yard without casting as the primary mode",
     {"enablers": ["self-mill"], "payoffs": ["recursion loops"], "queries": []}),
    (("Activated", "Battlefield"), "Activated ability on a permanent already in play",
     {"enablers": ["mana", "tap outlets"], "payoffs": ["growth", "swap"], "queries": []}),
    (("Conditions", "Variables"), "Scalar read off game state",
     {"enablers": ["pips", "type diversity"], "payoffs": ["scaled effects"], "queries": []}),
    (("Conditions", "Thresholds", "Battlefield"), "Board-state gate",
     {"enablers": ["board presence"], "payoffs": ["threshold payoffs"], "queries": []}),
    (("Conditions", "Thresholds", "Graveyard"), "Graveyard cardinality or diversity gate",
     {"enablers": ["self-mill", "discard"], "payoffs": ["delirium/threshold payoffs"], "queries": []}),
    (("Conditions", "Thresholds", "Event Occurred this Turn"), "Something happened this turn",
     {"enablers": ["attack", "etb", "death"], "payoffs": ["raid-like bonuses"], "queries": []}),
    (("Conditions", "Thresholds"), "State gate on a countable predicate",
     {"enablers": ["setup pieces"], "payoffs": ["gated bonuses"], "queries": []}),
    (("Conditions", "Batching"), "Card is in a named set — lineage and types matter",
     {"enablers": ["type density"], "payoffs": ["batch payoffs"], "queries": []}),
    (("Triggers", "This Enters the Battlefield"), "ETB setup on this permanent",
     {"enablers": ["blink", "reanimate"], "payoffs": ["etb value"], "queries": []}),
    (("Triggers", "ETB with Counters"), "Enters with counters by condition or choice",
     {"enablers": ["etb"], "payoffs": ["counter growth"], "queries": []}),
    (("Triggers", "Other Creatures ETB"), "Trigger when another creature enters",
     {"enablers": ["token makers", "blink"], "payoffs": ["etb engines"], "queries": []}),
    (("Triggers", "Other Permanents ETB"), "Trigger when a permanent (often enchantment/land) enters",
     {"enablers": ["enchantments", "lands"], "payoffs": ["landfall/constellation"], "queries": []}),
    (("Triggers", "You Cast a Spell"), "Trigger off casting — prowess family",
     {"enablers": ["cantrips", "noncreature spells"], "payoffs": ["prowess pumps"], "queries": []}),
    (("Triggers", "Start of Turn"), "Upkeep-shaped cost or kinship peek",
     {"enablers": [], "payoffs": ["upkeep value"], "queries": []}),
    (("Triggers", "This Dies"), "Value on death — aristocrats math",
     {"enablers": ["sacrifice outlets", "tokens"], "payoffs": ["drain", "recursion"], "queries": []}),
    (("Triggers", "Unique Triggers"), "Niche trigger conditions",
     {"enablers": [], "payoffs": ["specialized"], "queries": []}),
    (("Protect Permanents",), "Keeps permanents alive through targeting, damage, or destroy",
     {"enablers": [], "payoffs": ["voltron", "key pieces"], "queries": []}),
    (("Damage Modification",), "Changes what damage does (life, -1/-1, infect)",
     {"enablers": ["connectors"], "payoffs": ["lifegain", "poison"], "queries": []}),
    (("Counters",), "Tracks counters on permanents or players",
     {"enablers": ["proliferate"], "payoffs": ["counter payoffs"], "queries": []}),
    (("Subtypes and Properties",), "Type line rules baggage or tribal tag",
     {"enablers": ["type tutors"], "payoffs": ["typal"], "queries": []}),
    (("Formats", "Commander"), "Commander-format partner/background style rules",
     {"enablers": [], "payoffs": ["wider color identity"], "queries": []}),
    (("Formats",), "Format-specific keyword",
     {"enablers": [], "payoffs": [], "queries": []}),
    (("Basic Actions",), "Core rules action word",
     {"enablers": [], "payoffs": [], "queries": []}),
    (("Movement Between Zones",), "Moves objects between zones",
     {"enablers": [], "payoffs": [], "queries": []}),
    (("Manipulating Permanents",), "Changes attachment, control, or tap state",
     {"enablers": [], "payoffs": [], "queries": []}),
    (("Library Manipulation",), "Search or shuffle the library",
     {"enablers": [], "payoffs": ["tutors"], "queries": []}),
    (("Information",), "Reveal information",
     {"enablers": [], "payoffs": [], "queries": []}),
    (("Creature Related Actions",), "Creature-vs-creature fight",
     {"enablers": ["fight targets"], "payoffs": ["removal"], "queries": []}),
    (("End of Combat",), "End-of-combat liability or trigger",
     {"enablers": [], "payoffs": [], "queries": []}),
]

SPECIFIC_HOW: dict[str, str] = {
    "Flashback": "Cast from graveyard for flashback cost, then exile",
    "Delirium": "Gate on four or more card types among cards in your graveyard",
    "Convoke": "Tap creatures to help pay mana cost",
    "Delve": "Exile cards from graveyard to help pay generic mana",
    "Landfall": "Trigger whenever a land enters under your control",
    "Lifelink": "Damage dealt also causes you to gain that much life",
    "Flying": "Can only be blocked by creatures with flying or reach",
    "Treasure": "Artifact token sacrifice for one mana of any color",
    "Investigate": "Create a Clue token — pay to draw",
    "Partner": "Two legendary creatures as commander; color identity unions",
    "Cascade": "Exile cards until you hit a cheaper spell and cast it",
    "Storm": "Copy this spell for each spell cast before it this turn",
    "Prowess": "Gets +1/+1 until end of turn whenever you cast a noncreature spell",
    "Mentor": "Attacking creature puts a +1/+1 counter on a weaker attacker",
    "Proliferate": "Choose any number of players/permanents with counters; add one more of each kind",
    "Scry": "Look at top cards; put any on bottom, rest on top in any order",
    "Surveil": "Look at top cards; put any in graveyard, rest on top in any order",
    "Cycling": "Discard this to draw a card for its cycling cost",
    "Madness": "Cast when discarded for madness cost",
    "Undying": "Returns with a +1/+1 counter when it dies without one",
    "Persist": "Returns with a -1/-1 counter when it dies without one",
    "Hexproof": "Can't be the target of spells or abilities opponents control",
    "Indestructible": "Can't be destroyed by damage or destroy effects",
    "Deathtouch": "Any amount of combat damage to a creature is enough to destroy it",
    "Trample": "Excess combat damage may be assigned to the player/planeswalker",
    "Affinity": "Costs less for each artifact (or named type) you control",
    "Kicker": "Optionally pay extra for an additional effect",
    "Morph": "Cast face down as 2/2; turn face up for morph cost",
    "Disguise": "Cast face down; turn face up for disguise cost with ward-like tax",
    "Embalm": "Exile from yard to create a token copy that is a Zombie",
    "Unearth": "Return from yard with haste; exile at end of turn",
    "Escape": "Cast from yard by paying escape cost including exiling other yard cards",
    "Dredge": "Instead of drawing, return this from yard and mill equal to dredge N",
    "Toxic": "Combat damage to a player also gives that many poison counters",
    "Poisonous": "Combat damage to a player may give poison counters",
    "Amass": "Put +1/+1 counters on an Army you control, or create one then do so",
    "Populate": "Create a token that's a copy of a creature token you control",
    "Monarch": "Designate the monarch — card draw at end of turn; combat can steal it",
    "Initiative": "Venture on gain; undercity dungeon progress",
    "Commit a Crime": "Targeting an opponent or their stuff — batching gate",
    "Outlaw": "Assassin, Mercenary, Pirate, Rogue, or Warlock — type batch",
    "Historic": "Artifact, legendary, or Saga — type batch",
    "Modified": "Attached, equipped, or has a counter — board predicate",
}

# Special query overrides (default is keyword:<lowercase name>).
QUERY_OVERRIDE: dict[str, list[str]] = {
    "Split Cards": ["is:split"],
    "Fuse": ["o:fuse", "is:split"],
    "Modal Double-Faced Cards": ["is:mdfc"],
    "More Than Meets the Eye": ["o:\"more than meets the eye\""],
    "Phyrexian Mana": ["o:/p", "o:phyrexian"],
    "Compleated": ["o:compleated"],
    "Typecycling": ["o:cycling", "keyword:cycling"],
    "Amass": ["o:amass"],
    "Clue": ["o:\"clue token\"", "o:investigate"],
    "Incubator": ["o:incubate"],
    "Role": ["o:\"role token\""],
    "Monarch": ["o:monarch"],
    "Initiative": ["o:initiative"],
    "The Ring Tempts You": ["o:\"the ring tempts\""],
    "Start Your Engines!": ["o:\"start your engines\""],
    "Commit a Crime": ["o:crime"],
    "Read Ahead": ["o:\"read ahead\""],
    "Living Metal": ["o:\"living metal\""],
    "Equip": ["keyword:equip", "t:equipment"],
    "Enchant": ["keyword:enchant", "t:aura"],
    "Fortify": ["keyword:fortify"],
    "Crew": ["keyword:crew", "t:vehicle"],
    "+1/+1": ["o:\"+1/+1 counter\""],
    "-1/-1": ["o:\"-1/-1 counter\""],
    "Poison": ["o:poison"],
    "Ability Counters": ["o:counter"],
    "Stickers": ["o:sticker"],
    "Tickets": ["o:ticket"],
    # Catalog-or-keyword forms that Scryfall search rejects; oracle text works.
    "Friends Forever": ["o:\"friends forever\""],
    "Set In Motion": ["keyword:set in motion"],
    # Not in Scryfall's keyword catalog, but keyword:totem armor (unquoted) hits.
    "Totem Armor": ["keyword:totem armor"],
    "Enweb": ["o:web-slinging"],
    "Web-Slinging": ["o:web-slinging"],
    "Gold": ["o:\"gold token\"", "o:gold"],
    "Junk": ["o:\"junk token\"", "o:junk"],
    "Map": ["o:\"map token\"", "o:map"],
    "Absorb": ["o:absorb"],
    "Poisonous": ["o:poisonous"],
}


def parse_label(raw: str) -> list[tuple[str, bool, bool]]:
    """Split composites and strip (E)/(D). Returns (name, evergreen, deciduous)."""
    raw = raw.strip()
    # Arrow / section noise already excluded by structure.
    parts = [p.strip() for p in raw.split(" / ") if p.strip()]
    out: list[tuple[str, bool, bool]] = []
    for part in parts:
        evergreen = bool(re.search(r"\(E\)\s*$", part))
        deciduous = bool(re.search(r"\(D\)\s*$", part))
        name = re.sub(r"\s*\([ED]\)\s*$", "", part).strip()
        # Normalize a few display forms toward Scryfall-ish casing later via families.
        if name == "Poision":
            name = "Poison"
        if not name or name in SKIP_AS_LEAF:
            continue
        out.append((name, evergreen, deciduous))
    return out


def scry_term(s: str) -> str:
    """Quote a Scryfall value when it has spaces (preserves apostrophes inside)."""
    return f'"{s}"' if " " in s else s


def normalize_lookup(name: str) -> str:
    """Lowercase catalog key. Keep apostrophes — stripping them is the taxonomy apostrophe fix bug."""
    return name.lower().replace("!", "").strip()


def default_queries(name: str, vocab: dict[str, dict[str, str]] | None = None) -> list[str]:
    """Pick keyword: / t: / o: from Scryfall catalogs. Never invents a dead keyword:."""
    override = QUERY_OVERRIDE.get(name)
    if override is not None:
        return list(override)
    key = normalize_lookup(name)
    vocab = vocab or {}
    keywords = vocab.get("keyword") or {}
    types = vocab.get("type") or {}
    if key in keywords:
        return [f"keyword:{scry_term(key)}"]
    if key in types:
        return [f"t:{scry_term(key)}"]
    return [f"o:{scry_term(key)}"]


# keyword:"first strike" or keyword:flying — same for t:
_INDEXED_RE = re.compile(
    r"\b(keyword|t):(?P<q>\"[^\"]+\"|[^\s()]+)",
    re.IGNORECASE,
)
# Whole-query form: keyword:set in motion (unquoted multi-word — Scryfall
# accepts a few this way and rejects the quoted form).
_WHOLE_INDEXED_RE = re.compile(
    r"^(?P<op>keyword|t):(?P<rest>.+)$",
    re.IGNORECASE,
)


def indexed_clauses(queries: list[str]) -> list[tuple[str, str]]:
    """Return (operator, term) for every keyword:/t: clause in queries."""
    out: list[tuple[str, str]] = []
    for q in queries or []:
        q = (q or "").strip()
        if not q:
            continue
        whole = _WHOLE_INDEXED_RE.match(q)
        # Single-clause query: take the full rest (may contain spaces).
        if whole and not re.search(r"\b(?:o|otag|is|mana|m):", q):
            op = whole.group("op").lower()
            term = unwrap_term(whole.group("rest").strip()).lower()
            if term:
                out.append((op, term))
            continue
        for m in _INDEXED_RE.finditer(q):
            op = m.group(1).lower()
            raw = m.group("q")
            term = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
            out.append((op, term.lower()))
    return out


def dead_indexed_queries(
    queries: list[str],
    vocab: dict[str, dict[str, str]],
) -> list[str]:
    """Clauses whose keyword:/t: term is absent from Scryfall catalogs."""
    keywords = vocab.get("keyword") or {}
    types = vocab.get("type") or {}
    dead: list[str] = []
    for op, term in indexed_clauses(queries):
        if op == "keyword" and term not in keywords:
            dead.append(f"keyword:{scry_term(term)}")
        elif op == "t" and term not in types:
            dead.append(f"t:{scry_term(term)}")
    return dead


def validate_mechanics(
    mechanics: list[dict[str, Any]],
    vocab: dict[str, dict[str, str]],
) -> list[str]:
    """Return human-readable dead-query reports. Empty list means clean."""
    reports: list[str] = []
    for m in mechanics:
        name = m.get("name") or "?"
        qs = (m.get("supports") or {}).get("queries") or []
        for bad in dead_indexed_queries(qs, vocab):
            reports.append(f"{name}: {bad}")
    return reports


def how_and_supports(
    path: list[str],
    name: str,
    vocab: dict[str, dict[str, str]] | None = None,
) -> tuple[str, dict[str, list[str]]]:
    how = SPECIFIC_HOW.get(name)
    supports: dict[str, list[str]] | None = None
    for prefix, h, s in HOW_BY_PREFIX:
        if tuple(path[: len(prefix)]) == prefix:
            if how is None:
                how = h
            supports = {
                "enablers": list(s["enablers"]),
                "payoffs": list(s["payoffs"]),
                "queries": list(s["queries"]),
            }
            break
    if how is None:
        how = f"Named mechanic under {' / '.join(path)}" if path else "Named mechanic"
    if supports is None:
        supports = {"enablers": [], "payoffs": [], "queries": []}
    supports["queries"] = default_queries(name, vocab)
    return how, supports


def walk(
    node: Any,
    path: list[str],
    sink: list[dict[str, Any]],
    vocab: dict[str, dict[str, str]] | None = None,
) -> None:
    if isinstance(node, str):
        for name, evergreen, deciduous in parse_label(node):
            how, supports = how_and_supports(path, name, vocab)
            sink.append({
                "name": name,
                "path": list(path),
                "how": how,
                "supports": supports,
                "evergreen": evergreen,
                "deciduous": deciduous,
            })
        return
    if not isinstance(node, (list, tuple)) or len(node) != 2:
        return
    label, children = node
    # Expand label if it itself is a mechanic with children — rare; treat as category.
    label_name = re.sub(r"\s*\([ED]\)\s*$", "", label).strip()
    # Skip pure formatting arrows in path
    label_name = label_name.lstrip("↑↓").strip()
    new_path = path + [label_name] if label_name else path
    if children is None:
        # Category header only — not a leaf unless it's a known named mechanic alone.
        if label_name and label_name not in SKIP_AS_LEAF and label_name not in {
            "Formats", "Additional Costs", "Alternate Costs", "Actions", "Activated",
            "Conditions", "Triggers", "Protect Permanents", "Damage Modification",
            "Counters", "Subtypes and Properties", "Basic Actions",
            "Movement Between Zones", "Manipulating Permanents", "Library Manipulation",
            "Information", "Creature Related Actions", "Token Types", "Token Actions",
            "Non-Mana Costs", "Binary", "Variable", "Increased / Reduced Effect",
            "Can / Can't Attack", "Evasion", "Add +1/+1 Counter",
            "Increase Power / Toughness", "Conditional", "Other",
            "Discard for Alternate Effect", "Play at Specific Time", "Repeatable",
            "Once", "Same as From Hand", "Repeatedly", "Unified Cost / Situation",
            "Swap Out", "Improve This Permanent", "Change Types", "Variables",
            "Thresholds", "Battlefield", "Graveyard", "Event Occurred this Turn",
            "Batching", "This Enters the Battlefield", "Exile Card", "Remove Creature",
            "ETB with Counters", "If Conditions are Met", "As a Choice",
            "Other Creatures ETB", "Other Permanents ETB", "You Cast a Spell",
            "Start of Turn", "Negative", "Positive", "This Dies", "Unique Triggers",
            "From Targeting", "From Destruction", "From Damage", "From a Few Things",
            "Permanents", "Players", "With their own Rules", "As a Tag",
            "Face-Down Cards", "Variable Cost Reduction", "Situational Reduced Costs",
            "Alternatives to Mana", "Timing", "Mana Spent to Cast", "When You Cast This",
            "Delayed Re-Cast", "Can / Can't Attack / Block", "Attacking Triggers",
            "Blocking / Not Blocking", "Saboteur Abilities", "End of Combat",
            "Combat Rules Modification", "Hand Abilities", "Cast from Graveyard",
            "Return from Graveyard", "Battlefield", "Commander", "Planechase",
            "Archenemy", "Conspiracy", "Multiplayer / Opponent", "Outside the Game",
            "New Components", "Top of Library Manipulation", "Temporary Removal",
            "Distribute Counters", "Change Sides", "Cast Spells", "Tokens",
        }:
            # Lone labeled node with no children — treat as mechanic leaf.
            for name, evergreen, deciduous in parse_label(label):
                how, supports = how_and_supports(path, name, vocab)
                sink.append({
                    "name": name,
                    "path": list(path),
                    "how": how,
                    "supports": supports,
                    "evergreen": evergreen,
                    "deciduous": deciduous,
                })
        return
    for child in children:
        walk(child, new_path, sink, vocab)


def build_mechanics(
    vocab: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for top in MENU:
        walk(top, [], leaves, vocab)
    # Dedupe by lowercase name — first path wins; keep evergreen/deciduous OR.
    seen: dict[str, dict[str, Any]] = {}
    for leaf in leaves:
        key = leaf["name"].lower()
        if key in seen:
            seen[key]["evergreen"] = seen[key]["evergreen"] or leaf["evergreen"]
            seen[key]["deciduous"] = seen[key]["deciduous"] or leaf["deciduous"]
            continue
        seen[key] = leaf
    return sorted(seen.values(), key=lambda m: m["name"].lower())


def unwrap_term(raw: str) -> str:
    """Strip one layer of Scryfall double quotes."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return raw


def heal_candidates(q: str) -> list[str]:
    """Alternate query shapes to try when Scryfall rejects q (400 or zero).

    Scryfall's keyword catalog includes terms that `keyword:` search rejects
    (play, shuffle, cast, … — HTTP 400). Some multi-word keywords only work
    unquoted; some types in the type catalog still return zero for `t:`.
    Fall back toward oracle text; never invent card names.
    """
    q = (q or "").strip()
    if not q:
        return []
    out: list[str] = []

    def add(cand: str) -> None:
        if cand and cand != q and cand not in out:
            out.append(cand)

    if q.startswith("keyword:"):
        term = unwrap_term(q[len("keyword:"):])
        # Unquoted multi-word form (totem armor / set in motion).
        if " " in term:
            add(f"keyword:{term}")
        add(f"o:{scry_term(term)}")
        add(f"o:{term}")
    elif q.startswith("t:"):
        term = unwrap_term(q[len("t:"):])
        add(f"o:{scry_term(term)}")
        add(f'o:"{term} token"' if " " not in term else f"o:{scry_term(term)}")
    elif q.startswith("otag:"):
        term = unwrap_term(q[len("otag:"):])
        add(f"o:{scry_term(term)}")
    elif q == "mana=/p":
        add("o:/p")
        add("o:phyrexian")
    return out


def rewrite_dead_queries(
    mechanics: list[dict[str, Any]],
    replacements: dict[str, str],
) -> int:
    """Apply query rewrites in place. Returns number of clause substitutions."""
    n = 0
    for m in mechanics:
        supports = m.get("supports") or {}
        qs = supports.get("queries") or []
        new_qs = []
        for q in qs:
            rq = replacements.get(q, q)
            if rq != q:
                n += 1
            if rq not in new_qs:
                new_qs.append(rq)
        supports["queries"] = new_qs
        m["supports"] = supports
    return n


def load_build_vocab() -> dict[str, dict[str, str]]:
    """Scryfall catalogs via app.load_vocab (cached under ~/.deckwright)."""
    from deckwright import app  # local import — keeps the script runnable from repo root

    return asyncio.run(app.load_vocab())


async def probe_query(q: str) -> str:
    """Hit Scryfall once. Returns ok | zero | bad_request | unknown.

    Box/#69 gate: fail the build on 400-or-zero. Network/throttle failures are
    unknown — never scored as zero (same rule as the full-sweep vocabulary grading / test_deckwright).
    """
    import time

    import httpx
    from deckwright import app

    async with app._gate:
        gap = time.time() - app._last[0]
        if gap < 0.12:
            await asyncio.sleep(0.12 - gap)
        app._last[0] = time.time()
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.get(
                    f"{app.SCRYFALL}/cards/search",
                    params={"q": q, "unique": "cards", "page": 1},
                    headers=app.UA,
                )
        except httpx.HTTPError:
            return "unknown"
    if r.status_code == 400:
        return "bad_request"
    if r.status_code == 404:
        return "zero"
    if r.status_code == 429:
        await asyncio.sleep(1.2)
        return await probe_query(q)
    if r.status_code >= 500 or r.status_code != 200:
        return "unknown"
    try:
        body = r.json()
    except ValueError:
        return "unknown"
    total = body.get("total_cards")
    if total is None:
        total = len(body.get("data") or [])
    return "ok" if total else "zero"


async def live_validate_queries(
    mechanics: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Probe every unique supports.queries string. Returns (dead, unknown)."""
    unique: list[str] = []
    seen: set[str] = set()
    for m in mechanics:
        for q in (m.get("supports") or {}).get("queries") or []:
            if q and q not in seen:
                seen.add(q)
                unique.append(q)
    dead: list[str] = []
    unknown: list[str] = []
    for i, q in enumerate(unique, 1):
        status = await probe_query(q)
        if status in ("zero", "bad_request"):
            dead.append(f"{status}: {q}")
        elif status == "unknown":
            unknown.append(q)
        if i % 50 == 0 or i == len(unique):
            print(f"  probed {i}/{len(unique)} …", flush=True)
    return dead, unknown


def collect_queries(mechanics: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for m in mechanics:
        out.extend((m.get("supports") or {}).get("queries") or [])
    return out


async def heal_until_clean(
    mechanics: list[dict[str, Any]],
    *,
    rounds: int = 2,
) -> tuple[list[str], list[str]]:
    """Probe, rewrite 400-or-zero queries, re-probe. Returns (dead, unknown)."""
    dead: list[str] = []
    unknown: list[str] = []
    for round_i in range(rounds):
        print(f"live gate round {round_i + 1}: "
              f"{len(set(collect_queries(mechanics)))} unique quer(ies)…")
        dead, unknown = await live_validate_queries(mechanics)
        if unknown:
            return dead, unknown
        if not dead:
            return [], []
        replacements: dict[str, str] = {}
        still: list[str] = []
        for line in dead:
            # "bad_request: keyword:play" / "zero: t:gold"
            status, _, q = line.partition(": ")
            q = q.strip()
            healed = None
            for cand in heal_candidates(q):
                st = await probe_query(cand)
                if st == "ok":
                    healed = cand
                    break
                if st == "unknown":
                    unknown.append(cand)
                    return dead, unknown
            if healed:
                replacements[q] = healed
                print(f"  heal {q} → {healed}")
            else:
                still.append(line)
        if not replacements:
            return still, unknown
        n = rewrite_dead_queries(mechanics, replacements)
        print(f"  rewrote {n} clause(s); {len(still)} still dead this round")
        if still and round_i == rounds - 1:
            return still, unknown
    return dead, unknown


def main() -> int:
    skip_live = "--skip-live" in sys.argv  # offline emit / unit helpers
    vocab = load_build_vocab()
    if not (vocab.get("keyword") and vocab.get("type")):
        print("error: Scryfall keyword/type catalogs unavailable — cannot gate queries",
              file=sys.stderr)
        return 2
    mechanics = build_mechanics(vocab)
    # Catalog membership is how we *choose* keyword:/t:, not the merge gate.
    # Scryfall search rejects some catalogued keywords (play/shuffle → 400) and
    # accepts a few uncatalogued forms (totem armor). Live 400-or-zero is law.
    catalog_dead = validate_mechanics(mechanics, vocab)
    if catalog_dead:
        print(f"note: {len(catalog_dead)} keyword:/t: term(s) absent from catalogs "
              f"— live gate decides", file=sys.stderr)
        for line in catalog_dead[:10]:
            print(f"  {line}", file=sys.stderr)
    if not skip_live:
        live_dead, unknown = asyncio.run(heal_until_clean(mechanics))
        if unknown:
            print(f"error: {len(unknown)} quer(ies) unreachable — not grading as "
                  f"zero; refusing to write a map we could not prove",
                  file=sys.stderr)
            for q in unknown[:20]:
                print(f"  unknown: {q}", file=sys.stderr)
            return 2
        if live_dead:
            print(f"error: {len(live_dead)} quer(ies) still 400-or-zero after heal "
                  f"— refusing to write", file=sys.stderr)
            for line in live_dead[:40]:
                print(f"  {line}", file=sys.stderr)
            if len(live_dead) > 40:
                print(f"  … and {len(live_dead) - 40} more", file=sys.stderr)
            return 1
    payload = {
        "version": 1,
        "updated": "2026-03",
        "source": "play taxonomy menu — how they play, not CR implementation",
        "notes": [
            "Named mechanics only; paper cards only.",
            "(E) evergreen, (D) deciduous.",
            "how = game math in plain language; supports uses shapes and Scryfall queries, never card names.",
            "Map is source of truth; mechanic_families.json reviews plan_role/math_kind; adapter merges.",
            "supports.queries gated at build time against Scryfall (fail on HTTP 400 or zero cards); keyword:/t: healed to o: when search rejects the indexed form.",
        ],
        "mechanics": mechanics,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(f"wrote {OUT} ({len(mechanics)} mechanics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
