# German card art

How the German-language scans under `data/german_cards/images/` are fetched,
audited and recorded. Both scripts read and write
`data/german_art_sources.json`; `data/` is gitignored, so a fresh checkout has
neither the art nor the manifest.

## Where the art lives

| Part | Path |
| --- | --- |
| Files | `data/german_cards/images/de_<set>-<number>.png` |
| Served at | `/static/german_cards/images/...` (`src/api/main.py`) |
| Provenance | `data/german_art_sources.json` |

The index stores the public path, so the file on disk is the only copy: there
is no second directory under `src/static`.

## `scripts/fetch_german_art.py`

Walks a source chain per card and stores the first real German scan. TCGdex
only carries German artwork for sets it has scanned, so vintage sets fall
through to PokéWiki's file namespace and, when allowed, to Bisafans photos:

1. **tcgdex** — `api.tcgdex.net/v2/de`, the clean official asset.
2. **pokewiki** — set category, matched by card number.
3. **pokewiki-title** — file name guessed from the German card name, for sets
   the wiki files under a category we cannot derive.
4. **bisafans** — `media.bisafans.de`, German photographs; only with
   `--allow-photos` (or in upgrade mode), and never when the response is the
   site's generic card back.

A candidate must decode as a plausible card face (≥200×280, card aspect) and
be at least 500 px wide; otherwise the widest usable candidate wins.

| Mode | Use |
| --- | --- |
| `--all-missing` | the normal run: every German card whose TCGdex record has no German image |
| `--sets ex14,dp1` / `--cards de_ex14-56` | work on one set or a card list |
| `--retry-failed` | retry the ids in `data/german_art_failed.json` |
| `--upgrade-below WIDTH` | re-fetch art narrower than WIDTH; replaces only with a wider scan, and consults Bisafans too |
| `--force` | overwrite art that already exists |
| `--skip-newer-than EPOCH` | treat files modified after EPOCH as already imported |
| `--dry-run` | report what would be fetched, write nothing |
| `--limit N`, `--workers N` | smoke test and concurrency |

The manifest is saved every 25 cards, so an interrupted run keeps what it
finished.

## `scripts/verify_german_backs.py`

Bisafans answers HTTP 200 with a generic card back for artwork it does not
have, and some of those backs were installed as card art before the importer
learned to reject them. This fingerprints every installed image and compares
it with the back itself (`media.bisafans.de/.../ex-rubin-saphir/001.jpg`),
which also catches a back that arrived by any other route — an older import, a
relink, a manual copy.

| Distance to the back | Meaning |
| --- | --- |
| ≤ 0.05 | *is* the back |
| 0.05 – 0.12 | suspect, worth a look, not replaced |
| > 0.20 | good enough to replace a back |

| Mode | Use |
| --- | --- |
| (default) | every card in the manifest |
| `--all-de` | every `de_*.png` on disk, including files no manifest row points at |
| `--limit N`, `--workers N` | sample and concurrency |
| `--probe` | show what each back would be replaced with, write nothing |
| `--apply` | re-source the backs found and rewrite the manifest |
| `--from-report` | repair the cards listed in the previous report, no rescan |

A replacement is re-fetched from PokéWiki, TCGdex and the pre-upgrade manifest
backup, German sources first; TCGdex's English scan is the last resort, and
only when no German candidate clears the threshold.

Six cards are recorded that way — `de_ecard1-17`, `de_ex2-18`, `de_ex3-75`,
`de_ex9-30`, `de_ex9-65` and `de_ex9-95` — because no German scan exists for
them in TCGdex de, the PokéWiki category or title, or the pre-Bisafans
records.

Cards with no alternative stay as they are and are listed as unfixable. The
run writes `data/german_back_audit.json` with the backs, the suspects, what
was fixed and which cards could not be.

## What `data/german_art_sources.json` guarantees

One entry per installed card, keys in alphabetical order: `at`, `page`, `px`,
`source`, `url`, plus `reconstructed` where the record was rebuilt rather than
observed.

- **`source`** — `tcgdex`, `pokewiki`, `pokewiki-title`, `bisafans`,
  `tcgdex-en`, or `unknown` when nothing local recorded the origin.
- **`url`** — the image that was fetched; **`page`** — the wiki file title
  when there is one, otherwise empty.
- **`px`** — the installed PNG's size as written by the run that installed
  it.
- **`at`** — when that fetch happened.

A record is written only after the file is on disk, and a re-fetch replaces
the whole entry, so a stale `url` cannot linger. The manifest is provenance,
not an integrity proof: art replaced by hand leaves `px` behind, which is the
one thing the audit's px check detects. The `reconstructed` flag and what it
asks of an audit are described under [German card art
provenance](README.md#german-card-art-provenance) in the README.

## After changing art or code

Static art is picked up on the next page load; the API needs no restart.
Neither script runs on its own — both must be invoked by hand after a manifest
or art change, and `verify_german_backs.py` re-downloads the card back every
run, so an offline host reports that it could not fetch it rather than
passing.
