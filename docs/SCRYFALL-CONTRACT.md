# Scryfall card contract

What Deckwright keeps from a Scryfall card object, and what it deliberately drops.
`trim_card()` in `app.py` is the only place that projects a raw card. Adding a field
means updating this document and `trim_card()` together.

There are **two projections**. The full trim feeds the UI and legality. Model prompts
get a narrower view — every field in a prompt is multiplied across ~45 cards × 7 role
calls, so UI-only fields there are pure token cost.

---

## Already kept (unchanged)

| Trim key | Scryfall source |
|---|---|
| `name` | `name` |
| `cost` | `mana_cost` via `_face_field` |
| `cmc` | `cmc` |
| `type` | `type_line` via `_face_field` |
| `text` | `oracle_text` via `_face_field` (full text — no char cap) |
| `usd` | `prices.usd` |
| `gc` | `game_changer` (boolean on the card object, not legalities) |
| `color_identity` | `color_identity` |
| `legal` | `legalities.commander` |

---

## KEEP — model-facing

These ride into every model prompt via `for_model()`.

| Trim key | Scryfall source | Why |
|---|---|---|
| `keywords` | `keywords` | Scryfall's parsed list. Beats regexing oracle text. Feeds CON-68 / CON-79. |
| `produced_mana` | `produced_mana` | Colored-mana modelling (CON-69/70). Absent on non-producers. |
| `layout` | `layout` | Tells you when other fields lie (CON-72). Keep always. |
| `power` | `power` via `_face_field` | **String.** Printed values include `*`, `1+*`, `X`. Do not coerce to int. |
| `toughness` | `toughness` via `_face_field` | Same as power. |

---

## KEEP — UI / logic only (EXCLUDE from model prompts)

| Trim key | Scryfall source | Why |
|---|---|---|
| `purchase_uris` | `purchase_uris` | CON-65 buy links. |
| `image` | `image_uris.normal` (or `card_faces[0].image_uris.normal` on DFCs) | CON-65. **Only** the `normal` size — not all six. |
| `edhrec_rank` | `edhrec_rank` | CON-80 "most played" label needs a source. |

`for_model()` strips these three before any `json.dumps` into a prompt.

---

## LATER — note only, do not add

| Field | Why deferred |
|---|---|
| `all_parts` | Tokens / meld. Speculative until CON-68 exists. |

---

## IGNORE

Everything else. Print and catalog metadata, including but not limited to:

`artist`, `artist_ids`, `illustration_id`, `frame`, `frame_effects`,
`border_color`, `collector_number`, `set`, `set_id`, `set_name`, `set_type`, `set_uri`,
`set_search_uri`, `scryfall_set_uri`, `uri`, `scryfall_uri`, `prints_search_uri`,
`rulings_uri`, `released_at`, `booster`, `promo`, `promo_types`, `finishes`, `foil`,
`nonfoil`, `digital`, `lang`, `games`, `rarity`, `reserved`, `textless`, `variation`,
`variation_of`, `oversized`, `highres_image`, `image_status`, `penny_rank`,
`security_stamp`, `card_back_id`, `oracle_id`, `id`, `multiverse_ids`, `mtgo_id`,
`arena_id`, `tcgplayer_id`, `cardmarket_id`, `preview`, `prices` (other than `.usd`),
`related_uris`, `attraction_lights`, `color_indicator`, `hand_modifier`, `life_modifier`,
`content_warning`, `flavor_text`, `flavor_name`, `printed_*`, `reprint`, `story_spotlight`.

---

## Layouts — where fields are ABSENT or PER-FACE

Measured against live Scryfall on 2026-08-03. This is where silent bugs live.
`_face_field(card, key, joiner)` reads top-level when the key is present and not
`null`, otherwise joins non-empty face values.

### `normal`

| Field | Top-level | Notes |
|---|---|---|
| `oracle_text` | present | |
| `mana_cost` | present (may be `""` on lands) | |
| `type_line` | present | |
| `power` / `toughness` | present on creatures; else `null` | Values are **strings** (`"2"`, `"*"`, `"1+*"`). |
| `image_uris` | present | |
| `card_faces` | absent | |

### `modal_dfc` (e.g. Agadeem's Awakening)

| Field | Top-level | Faces |
|---|---|---|
| `oracle_text` | **absent** | per face |
| `mana_cost` | **absent** (`null`) | per face (back often `""`) |
| `type_line` | composed `"Front // Back"` | per face |
| `power` / `toughness` | **absent** | per face (null on non-creatures) |
| `image_uris` | **absent** | per face |
| `produced_mana` | present when either face produces | |

### `transform` (e.g. Delver of Secrets)

| Field | Top-level | Faces |
|---|---|---|
| `oracle_text` | **absent** | per face |
| `mana_cost` | **absent** (`null`) | front has cost; back usually `""` |
| `type_line` | composed `"Front // Back"` | per face |
| `power` / `toughness` | **absent** | per face (both faces are often creatures) |
| `image_uris` | **absent** | per face |

### `split` (e.g. Fire // Ice)

| Field | Top-level | Faces |
|---|---|---|
| `oracle_text` | **absent** | per face |
| `mana_cost` | composed `"{1}{R} // {1}{U}"` | per face |
| `type_line` | composed | per face |
| `power` / `toughness` | absent | absent (instants) |
| `image_uris` | present | |

### `adventure` (e.g. Bonecrusher Giant)

| Field | Top-level | Faces |
|---|---|---|
| `oracle_text` | **absent** | creature + adventure |
| `mana_cost` | composed | per face |
| `type_line` | composed | per face |
| `power` / `toughness` | **front face only** at top level | adventure face has none |
| `image_uris` | present | |

### `flip` (e.g. Akki Lavarunner)

| Field | Top-level | Faces |
|---|---|---|
| `oracle_text` | **absent** | per face |
| `mana_cost` | front only | back `""` |
| `type_line` | composed | per face |
| `power` / `toughness` | **front face only** at top level | both faces have P/T |
| `image_uris` | present | |

---

## Invariants for code

1. Never coerce `power` / `toughness` to `int` — legal cards print `*`.
2. Never assume `oracle_text` / `mana_cost` / `image_uris` exist at top level — check `layout`.
3. Model prompts call `for_model(card)`, never dump the full trim.
4. New Keep fields require a row in this file and a matching key in `trim_card()`.
