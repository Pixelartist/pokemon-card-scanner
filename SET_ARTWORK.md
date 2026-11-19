# Set artwork on the Sets page

How the sets overview gets its logos, release dates and booster pack art, and
what each script under `scripts/` owns. Everything here is regenerated from
outside sources and cached locally; `data/` is gitignored, so a fresh checkout
starts with plain set boxes until the scripts are run once.

## What the page shows

Each box in the Sets tab is built by `renderSets()` in `src/static/js/main.js`:

| Part | Source | Notes |
| --- | --- | --- |
| Pack art on the right | `sets.images.boosters` from `/api/sets` | one art picked at random per page view, remembered per set so filtering does not reshuffle |
| Set mark | `sets.images.symbol`, else `logo` | both share one fixed 28px slot, so wide logos and square symbols line up |
| Set name | `name` | two-line clamp, the human name sits where the old code badge was |
| Card count | `total` | falls back to the number of cards stored when TCGdex reports a placeholder |
| Release date | `release_date` | rendered as `Sep 2026`; sets without one sort to the bottom |

Header controls: a search box, a **language** picker (from the `de_`-style code
prefix, English when there is none) and a **released** picker (decade buckets
built from the data, plus a "No date" bucket when needed), with a live
`N of M sets` count. Options are built from the data that is present, so a new
language or decade cannot leave an empty dropdown behind.

Sorting is release date descending, applied in `loadSets()`.

## API

- `GET /api/sets` — the full list. Every entry may carry
  `images: {logo, symbol, boosters}`, `logo_url`, `symbol_url`, `booster_urls`.
- `GET /api/sets/{code}/cards` — the set header plus its cards; `images` is the
  same object, so the detail page can show the banner logo.
- `/static/sets/` serves `data/images/sets`, `/static/boosters/` serves
  `data/images/boosters`. Both are plain `StaticFiles` mounts; `.webp` is
  registered explicitly because Windows' `mimetypes` does not know it.

Logo and symbol come from the `sets` table (`images` column). Booster art comes
from one manifest, `data/cache/booster_art.json`, read by `_local_booster_art()`.
It is a separate store because many sets (older Japanese ones) are listed from
the catalogue without ever getting a row in `sets`, and because the manifest
covers set art that is not a per-set database fact.

## Scripts

All three share the same conventions: public paths are recorded as
`/static/...`, images are re-encoded to WebP at card size, `--force` re-downloads,
`--limit N` is a smoke test, `--db` points at another SQLite file.

### `scripts/fetch_set_images.py`

Downloads the TCGdex **logo** (wide banner) and **symbol** (square badge) for
every set in `data/cache/tcgdex/sets_en.json`, stores them under
`data/images/sets/<tcgdex id>_logo.webp|png` and `/symbol.png`, and writes the
paths into `sets.images`. `de_` rows reuse the English artwork instead of
downloading it twice.

```bash
python scripts/fetch_set_images.py
python scripts/fetch_set_images.py --force --workers 8
```

Coverage at the time of writing: 305 files, 291 set rows, ~5% of cards sit in
sets TCGdex publishes no artwork for.

### `scripts/fetch_set_dates.py`

TCGdex only publishes `releaseDate` on the per-set endpoint, so the catalogue
cache has none. This fetches each set's detail (falling back to the `ja` locale
for Japanese-only ids), keeps the responses in `data/cache/tcgdex/set_detail_<locale>_<id>.json`,
and writes the date into both `sets_en.json` and `sets.release_date`.

```bash
python scripts/fetch_set_dates.py
python scripts/fetch_set_dates.py --force     # ignore cached detail files
```

Coverage at the time of writing: 387 of 400 TCGdex ids dated (514 catalogue
rows, German duplicates inherit the English date); only `sv` and `tk` remain
unknown.

### `scripts/fetch_booster_art.py`

The only booster-art fetcher. It walks three sources in priority order and each
one only fills sets that are still empty, so a good modern scan is never
replaced by an older one:

1. **pokesymbols.com** — modern English sets, matched by set name (series
   prefixes like `Scarlet & Violet—` are ignored, `Base Set` is aliased).
2. **Bulbagarden Archives** — older and Japanese scans found by walking the
   `TCG booster packs` category tree and matching the set code that starts the
   filename (`ADV1 Expansion Pack pack.jpg`); non-English variants are skipped.
3. **packart.tech** — Japanese expansions from its structured catalogue, paired
   by release date because their names are English translations.

Images land in `data/images/boosters/` and the set → file mapping is written to
`data/cache/booster_art.json` (the only store; nothing else keeps a copy). A
full run also deletes image files the manifest no longer references.

```bash
python scripts/fetch_booster_art.py                    # all sources, full run
python scripts/fetch_booster_art.py --source packart    # one source (no writes outside images)
python scripts/fetch_booster_art.py --force             # re-download
python scripts/fetch_booster_art.py --limit 5           # smoke test, manifest untouched
```

Coverage at the time of writing: 296 sets, 706 images (~30 MB). The sets left
without art are promos, trainer kits, McDonald's collections, gallery/shiny
subsets and energy sets — those never shipped booster packs, so the script
excludes them via `NOT_BOOSTERS` rather than borrowing a parent set's pack.

## After changing art or code

- New images are picked up on the next page load; the API reads the manifest
  once per process, so restart the service after a fetch to see changes in a
  long-running instance.
- `src/api/main.py` changes (new mounts, endpoints) always need a restart.
- The pack art belongs to The Pokémon Company / Creatures / GAME FREAK and the
  scans to their respective uploaders. pokesymbols.com and packart.tech allow
  crawling; Bulbagarden Archives is a wiki whose text is openly licensed but
  whose images keep their own copyright. This is fine for a personal collection
  tool, but check before republishing anything.
