"""
Deckwright — the shopping sheet.

Turns a saved deck into a printable HTML sheet, sorted the way a person shops
rather than the way the deck was built.

The app groups the 99 by *role* — lands, ramp, draw, the plan. That is the
builder's model and it is the right one on screen, where you are asking "does
this deck have enough removal." It is the wrong one at a shop counter, where
you are asking "do you have this card." Shops file singles by colour and then
alphabetically, so that is how this sorts, with each card's type printed on its
own row. Walk the page in the order the boxes are in.

Mana is shown per card and summarised up front — the curve and the pip counts —
because the two questions a mana base has to answer are "how expensive is this
deck" and "what colours does it actually demand."

Usage:  python sheet.py <saved-deck-name> [out.html]
"""

import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

from deckwright import instance

SAVE_DIR = instance.home_dir()

# Shops file singles in this order. Creatures first because that is the bulk of
# a Commander deck and the section people page through longest.
TYPE_ORDER = ["Creature", "Planeswalker", "Artifact", "Enchantment",
              "Instant", "Sorcery", "Battle", "Land"]

# Which section a multi-type card files under. Lands beat artifacts because a
# shop keeps Ancient Den with the lands, not the rocks — but a creature land is
# still a creature. This is classification order; TYPE_ORDER is display order.
FILE_UNDER = ["Creature", "Planeswalker", "Land", "Artifact", "Enchantment",
              "Instant", "Sorcery", "Battle"]

COLOR_NAME = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def split_type(line: str) -> tuple[str, str]:
    """('Legendary Creature — Angel') -> ('Creature', 'Angel').

    Returns the primary card type and the subtype line. A card with several
    types (Artifact Creature) files under the first one a shop would use, which
    is the creature — that is the section a buyer looks in.
    """
    head, _, sub = line.partition("—")
    words = head.replace("//", " ").split()
    for t in FILE_UNDER:
        if t in words:
            return t, sub.strip()
    return (words[-1] if words else "Other"), sub.strip()


def short_type(c: dict) -> str:
    """A compact type for the row — 'Angel', 'Instant', 'Artifact'.

    The list is grouped by colour now, so the type has to travel on the card
    itself. For creatures the creature type is the useful half (nobody needs to
    be told an Angel is a creature); everything else uses its card type.
    """
    prim, sub = split_type(c.get("type", ""))
    if prim == "Creature" and sub:
        return sub.split("//")[0].strip()
    return prim


def sort_key(c: dict) -> tuple:
    """Alphabetical, ignoring a leading article and any leading punctuation."""
    n = re.sub(r"^(the|a|an)\s+", "", c["name"].lower())
    return (re.sub(r"^[^a-z0-9]+", "", n), c["name"])


SYMBOL_CACHE = SAVE_DIR / "symbols.json"
_symbols: dict[str, str] = {}


def load_symbols() -> dict[str, str]:
    """Scryfall's official mana symbols, inlined as data URIs.

    The symbols are the real ones from `/symbology` — the same art the app
    renders and the same art printed on the cards, generic costs included:
    {2} is a grey circle with a numeral in it, not the text "2".

    They are base64'd into the page rather than linked. A printed sheet has no
    network, and Chrome's PDF renderer will happily print a page whose remote
    images never arrived — which looks like a bug in this file rather than a
    missing fetch. Embedding makes the sheet self-contained.
    """
    global _symbols
    if _symbols:
        return _symbols
    if SYMBOL_CACHE.exists():
        try:
            _symbols = json.loads(SYMBOL_CACHE.read_text())
            return _symbols
        except (json.JSONDecodeError, OSError):
            pass

    import base64
    import urllib.request
    hdr = {"User-Agent": "Deckwright/1.0 (local personal deckbuilder)",
           "Accept": "application/json"}
    try:
        req = urllib.request.Request("https://api.scryfall.com/symbology", headers=hdr)
        data = json.loads(urllib.request.urlopen(req, timeout=25).read())["data"]
    except Exception:
        return {}                       # fall back to text; the sheet still prints
    out = {}
    for sym in data:
        try:
            svg = urllib.request.urlopen(
                urllib.request.Request(sym["svg_uri"], headers=hdr), timeout=25).read()
        except Exception:
            continue
        out[sym["symbol"]] = ("data:image/svg+xml;base64,"
                              + base64.b64encode(svg).decode())
    if out:
        _symbols = out
        try:
            SYMBOL_CACHE.write_text(json.dumps(out))
        except OSError:
            pass
    return _symbols


def pips(cost: str) -> str:
    """A mana cost as the symbols themselves, falling back to its own text."""
    if not cost:
        return ""
    syms = load_symbols()
    out = ""
    for token in re.findall(r"\{[^}]+\}", cost):
        uri = syms.get(token)
        out += (f'<img class="pip" src="{uri}" alt="{esc(token)}">' if uri
                else f'<span class="pip-txt">{esc(token)}</span>')
    return out or esc(cost)


def curve(deck: list) -> list[tuple[str, int]]:
    """Cards at each mana value, lands excluded — they have no cost to pay."""
    spells = [c for c in deck if "Land" not in c.get("type", "")]
    buckets = Counter()
    for c in spells:
        v = int(c.get("cmc") or 0)
        buckets["7+" if v >= 7 else str(v)] += 1
    order = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    return [(k, buckets.get(k, 0)) for k in order]


def pip_counts(deck: list) -> list[tuple[str, int]]:
    """Coloured mana symbols the deck demands, across every card's cost.

    This is the number that says what the mana base has to produce. A deck can
    be 'mono-white' on paper and still ask for {W}{W}{W} on turn four.
    """
    n = Counter()
    for c in deck:
        for sym in re.findall(r"\{([^}]+)\}", c.get("cost") or ""):
            for ch in sym.split("/"):
                if ch in COLOR_NAME:
                    n[ch] += 1
    return [(COLOR_NAME[k], v) for k, v in
            sorted(n.items(), key=lambda kv: -kv[1])]


def sources(deck: list, identity: list) -> list[tuple[str, int]]:
    """Lands and rocks producing each colour, counted honestly.

    Scryfall's `produced_mana` reports what a card *could* make, not what it
    makes here: Command Tower lists all five colours because its text reads
    "any color in your commander's color identity." Counted raw, a mono-white
    deck claims twelve blue sources. Intersecting with the commander's identity
    is the difference between a number and a lie.
    """
    ci = set(identity) or set(COLOR_NAME)
    n = Counter()
    for c in deck:
        for m in c.get("produced_mana") or []:
            if m in COLOR_NAME and m in ci:
                n[m] += 1
    return [(COLOR_NAME[k], v) for k, v in
            sorted(n.items(), key=lambda kv: -kv[1])]


# Colour sections, in WUBRG — Magic's own order, and the one every shop's
# boxes are already in. Lands come last because that is where they live in a
# singles case regardless of what colour they tap for.
COLOR_SECTIONS = [("W", "White"), ("U", "Blue"), ("B", "Black"),
                  ("R", "Red"), ("G", "Green")]


def bucket(c: dict) -> str:
    """Which shop box this card lives in.

    A playtest correction that beat the first guess: shops file singles by
    colour and then alphabetically, not by card type. Sorting a shopping list
    by type meant scanning eight sections for every card she picked up.

    Lands are their own box whatever they produce. A card with two or more
    colours in its identity is in the multicolour box, not filed twice.
    """
    if "Land" in (c.get("type") or ""):
        return "Land"
    ci = [x for x in (c.get("color_identity") or []) if x in COLOR_NAME]
    if not ci:
        return "Colourless"
    if len(ci) > 1:
        return "Multicolour"
    return ci[0]


def group(deck: list) -> list[tuple[str, str, list]]:
    """(section, subtitle, cards) in the order a shop's boxes are in.

    Colour first, alphabetical inside. Each row still carries its card type, so
    the deck stays legible as a deck — but the page is walked in the order the
    boxes are, which is the whole point of taking it to a counter.
    """
    boxes = defaultdict(list)
    for c in deck:
        boxes[bucket(c)].append(c)

    order = [k for k, _ in COLOR_SECTIONS] + ["Multicolour", "Colourless", "Land"]
    label = dict(COLOR_SECTIONS) | {"Multicolour": "Multicolour",
                                    "Colourless": "Colourless", "Land": "Lands"}
    out = []
    for key in order:
        if boxes.get(key):
            out.append((label[key], "", sorted(boxes.pop(key), key=sort_key)))
    for key, cards in boxes.items():             # anything unexpected, last
        out.append((key, "", sorted(cards, key=sort_key)))
    return out


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


CSS = """
@page{size:letter;margin:13mm 12mm 11mm}
:root{--ink:#161210;--gold:#8A6E3E;--gold-lt:#C6A46A;--mute:#6E6154;--line:#DCD2C0;
 --serif:"EB Garamond",Georgia,serif;--sans:"IBM Plex Sans",-apple-system,sans-serif;
 --mono:"IBM Plex Mono",Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:10.5px/1.5 var(--sans);
 -webkit-print-color-adjust:exact;print-color-adjust:exact}
.mast{border-bottom:2px solid var(--ink);padding-bottom:9px;margin-bottom:13px;
 display:flex;align-items:baseline;justify-content:space-between;gap:16px}
h1{font:600 26px/1 var(--serif);margin:0}
.tag{font:italic 400 11px var(--serif);color:var(--mute)}
.brand{font:600 8.5px var(--sans);letter-spacing:.2em;text-transform:uppercase;color:var(--gold)}
h2{font:600 8.5px var(--sans);letter-spacing:.2em;text-transform:uppercase;color:var(--gold);
 margin:0 0 8px;display:flex;align-items:center;gap:9px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.strip{display:flex;border:1px solid var(--line);border-radius:2px;margin-bottom:15px}
.cell{flex:1;padding:8px 11px;border-right:1px solid var(--line)}
.cell:last-child{border-right:0}
.cell .k{font:600 7.5px var(--sans);letter-spacing:.15em;text-transform:uppercase;
 color:var(--mute);margin-bottom:3px}
.cell .v{font:600 14px var(--mono)}
.cell .n{font-size:8.5px;color:var(--mute);margin-top:2px}

/* mana */
.mana{display:grid;grid-template-columns:1.35fr 1fr 1fr;gap:18px;margin-bottom:16px;
 break-inside:avoid}
.curve{display:flex;align-items:flex-end;gap:5px;height:58px}
.bar{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:3px}
.bar .b{width:100%;background:var(--gold-lt);border-radius:1px 1px 0 0;min-height:1px}
.bar .n{font:600 8px var(--mono);color:var(--mute)}
.bar .x{font:8px var(--mono);color:var(--mute)}
.kv{font-size:9.5px}
.kv div{display:flex;justify-content:space-between;padding:2.5px 0;
 border-bottom:.5px solid #EFE9DC}
.kv b{font-family:var(--mono);font-weight:600}
.subhead{font:600 7.5px var(--sans);letter-spacing:.15em;text-transform:uppercase;
 color:var(--mute);margin-bottom:5px}
.note{font:italic 400 9.5px var(--serif);color:var(--mute);line-height:1.5;margin-top:5px}

/* list */
.cols{column-count:3;column-gap:15px}
/* Groups must be allowed to break. Under type grouping every section was a
   handful of cards and break-inside:avoid was free; under colour grouping a
   mono-coloured deck puts 48 cards in one section, taller than a column, and
   Chrome responds by dropping the whole thing — the list rendered as nothing
   but the commander. Keep the heading with its first rows instead. */
.grp{margin-bottom:11px}
.grp-h{font:600 7.5px var(--sans);letter-spacing:.15em;text-transform:uppercase;color:#fff;
 background:var(--ink);padding:2.5px 6px;border-radius:2px;display:inline-block;margin-bottom:4px;
 break-after:avoid}
.grp-h .ct{color:var(--gold-lt);font-weight:400;margin-left:5px}
.grp-s{font:600 7.5px var(--sans);letter-spacing:.13em;text-transform:uppercase;
 color:var(--gold);margin:5px 0 3px;padding-top:3px;border-top:1px solid var(--line)}
.c{display:flex;align-items:baseline;gap:5px;padding:2px 0;
 border-bottom:.5px solid #EFE9DC;font-size:9px;break-inside:avoid}
.c .bx{width:7px;height:7px;border:1px solid var(--line);border-radius:1px;flex:none}
.c .nm{flex:1}
.c .ty{display:block;font:400 7.5px var(--sans);letter-spacing:.08em;
 text-transform:uppercase;color:var(--mute);margin-top:1px}
.c .cost{white-space:nowrap;display:inline-flex;align-items:center;gap:1.5px;flex:none}
.pip{width:10px;height:10px;border-radius:50%;box-shadow:0 0 0 .4px rgba(0,0,0,.25)}
.pip-txt{font-family:var(--mono);font-size:8px;color:var(--mute)}
.c .pr{font-family:var(--mono);font-size:8px;white-space:nowrap;color:var(--mute);
 min-width:28px;text-align:right}
/* Our prices are TCGplayer market. A shop charges its own, so the sheet leaves
   a line to write what you actually paid — otherwise the total on this page is
   a number you will be disappointed by at the counter. */
.c .paid{border-bottom:.5px solid var(--line);width:26px;flex:none}
.grp-h.cmdr{background:var(--gold)}
.legend{font:italic 400 9.5px var(--serif);color:var(--mute);line-height:1.5;
 margin:-3px 0 10px;max-width:78ch}
.tally{display:flex;gap:26px;margin-top:14px;padding-top:11px;border-top:1px solid var(--line);
 break-inside:avoid}
.tally > div{display:flex;align-items:baseline;gap:8px}
.tally .tl{font:600 7.5px var(--sans);letter-spacing:.15em;text-transform:uppercase;color:var(--mute)}
.tally .tb{width:72px;border-bottom:1px solid var(--ink);height:15px}
.tally .of{font:9px var(--mono);color:var(--mute)}
footer{margin-top:14px;padding-top:8px;border-top:1px solid var(--line);font-size:8.5px;
 color:var(--mute);display:flex;justify-content:space-between}
"""


def render(d: dict) -> str:
    deck = d["deck"]
    cmdr = d.get("commander_card") or {}
    total = d.get("price") or 0
    lands = sum(1 for c in deck if "Land" in c.get("type", ""))
    spells = [c for c in deck if "Land" not in c.get("type", "")]
    avg = round(sum(c.get("cmc") or 0 for c in spells) / max(1, len(spells)), 2)

    cv = curve(deck)
    peak = max(n for _, n in cv) or 1
    bars = "".join(
        f'<div class="bar"><span class="n">{n or ""}</span>'
        f'<span class="b" style="height:{round(n / peak * 44)}px"></span>'
        f'<span class="x">{k}</span></div>' for k, n in cv)

    def kv(rows, empty):
        if not rows:
            return f'<div class="note">{empty}</div>'
        return '<div class="kv">' + "".join(
            f"<div><span>{esc(k)}</span><b>{v}</b></div>" for k, v in rows) + "</div>"

    # The commander was not on the list. The header said "100 cards incl.
    # commander" and printed 99 rows — you could buy the entire deck and walk
    # out without the card it is built around, which is usually the dearest one
    # on the sheet. It goes first, in its own section, and it is not optional.
    sections = ""
    if cmdr.get("name"):
        c_price = f'${esc(cmdr["usd"])}' if cmdr.get("usd") else "—"
        sections += (
            '<div class="grp"><span class="grp-h cmdr">Commander'
            '<span class="ct">1</span></span>'
            f'<div class="c"><span class="bx"></span>'
            f'<span class="nm"><b>{esc(cmdr["name"])}</b></span>'
            f'<span class="cost">{pips(cmdr.get("cost"))}</span>'
            f'<span class="pr">{c_price}</span><span class="paid"></span></div>'
            '<div class="note">Buy this one first. Without it there is no deck.</div></div>')
    for prim, sub, cards in group(deck):
        head = (f'<span class="grp-h">{esc(prim)}<span class="ct">{len(cards)}</span></span>'
                if not sub else f'<div class="grp-s">{esc(prim)} — {esc(sub)} · {len(cards)}</div>')
        # Basics repeat. Four identical Plains lines is four things to read and
        # one thing to buy, so they collapse to a quantity.
        qty = Counter(c["name"] for c in cards)
        uniq, done = [], set()
        for c in cards:
            if c["name"] not in done:
                done.add(c["name"])
                uniq.append(c)
        rows = ""
        for c in uniq:
            n = qty[c["name"]]
            name = esc(c["name"]) + (f" &times;{n}" if n > 1 else "")
            price = f'${esc(c["usd"])}' if c.get("usd") else "—"
            rows += (f'<div class="c"><span class="bx"></span>'
                     f'<span class="nm">{name}<span class="ty">{esc(short_type(c))}</span></span>'
                     f'<span class="cost">{pips(c.get("cost"))}</span>'
                     f'<span class="pr">{price}</span><span class="paid"></span></div>')
        sections += f'<div class="grp">{head}{rows}</div>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{esc(d['commander'])} — shopping list</title>
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>

<div class="mast">
  <div><h1>{esc(d['commander'])}</h1>
    <div class="tag">shopping list · {len(deck) + 1} cards</div></div>
  <div class="brand">Deckwright</div>
</div>

<div class="strip">
  <div class="cell"><div class="k">Total</div><div class="v">${total:.2f}</div>
    <div class="n">{len(deck) + 1} cards incl. commander</div></div>
  <div class="cell"><div class="k">Lands</div><div class="v">{lands}</div>
    <div class="n">of {len(deck)} in the 99</div></div>
  <div class="cell"><div class="k">Avg cost</div><div class="v">{avg}</div>
    <div class="n">across {len(spells)} spells</div></div>
  <div class="cell"><div class="k">Colours</div><div class="v">{''.join(cmdr.get('color_identity') or []) or 'C'}</div>
    <div class="n">set by the commander</div></div>
</div>

<h2>What the mana looks like</h2>
<div class="mana">
  <div><div class="subhead">Cards by mana value</div>
    <div class="curve">{bars}</div>
    <div class="note">Lands excluded — they have no cost to pay.</div></div>
  <div><div class="subhead">Coloured pips demanded</div>
    {kv(pip_counts(deck), 'No coloured mana required.')}
    <div class="note">Every coloured symbol in the deck's costs. This is what the mana base has to produce.</div></div>
  <div><div class="subhead">Sources that produce it</div>
    {kv(sources(deck, cmdr.get('color_identity') or []), 'No coloured sources found.')}
    <div class="note">Lands and rocks, limited to the commander&rsquo;s colours &mdash; a Command Tower only makes what the commander allows.</div></div>
</div>

<h2>The cards · by colour, then alphabetical</h2>
<div class="legend">Grouped the way a shop's boxes are &mdash; by colour, then alphabetical &mdash;
with each card's type under its name. Two price columns: what Scryfall says it's worth, and a blank
to write what you actually paid. A shop sets its own prices, so the total on this page is
a guide, not the bill. Tick the box when you have the card in hand.</div>
<div class="cols">{sections}</div>

<div class="tally">
  <div><span class="tl">Cards found</span><span class="tb"></span><span class="of">of {len(deck) + 1}</span></div>
  <div><span class="tl">Actually spent</span><span class="tb"></span><span class="of">est. ${total:.2f}</span></div>
</div>

<footer><span>Deckwright · card data and prices from Scryfall, refreshed daily. Totals are ballparks, never receipts.</span>
<span style="font-family:var(--mono)">${total:.2f}</span></footer>
</body></html>"""


def main() -> None:
    if len(sys.argv) < 2:
        names = sorted(p.stem for p in SAVE_DIR.glob("*.json") if p.stem != "vocab")
        print("usage: python sheet.py <deck>\nsaved: " + ", ".join(names))
        raise SystemExit(1)
    src = SAVE_DIR / f"{sys.argv[1]}.json"
    if not src.exists():
        raise SystemExit(f"no saved deck at {src}")
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else f"{sys.argv[1]}-list.html")
    out.write_text(render(json.loads(src.read_text())))
    print(out)


if __name__ == "__main__":
    main()
