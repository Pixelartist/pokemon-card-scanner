# Pokemon Card Scanner

A locally hosted website for scanning and identifying Pokemon cards, similar to PokeCardEx but self-hosted.

## Features

- **Card Scanner**: Upload images to identify Pokemon cards using the Pokemon TCG API vision service
- **Collection Management**: Track your cards with condition, language, quantity, and notes
- **Statistics**: View collection stats including total cards, unique cards, and breakdown by set
- **Offline Database**: SQLite database stores your collection locally
- **Responsive UI**: Works on desktop and mobile devices
- **Interface language**: English and German, following the browser/OS language and switchable from the user menu

## Requirements

- Python 3.11+
- UV package manager (recommended)
- Pokemon TCG API key (for card recognition)

## Setup

1. **Clone/Download**: Get the project files
2. **Create virtual environment**:
   ```bash
   cd pokemon-card-scanner
   uv venv .venv
   ```

3. **Install dependencies**:
   ```bash
   uv pip install -r requirements.txt  # Or use the pyproject.toml
   ```

4. **Configure environment**:
   ```bash
   cp .env .env.local
   # Edit .env.local and add your Pokemon TCG API key
   ```

5. **Run the server**:
   ```bash
   python daemon.py                 # foreground
   python scripts/launch_server.py  # detached, survives session teardown
   ```

6. **Access the app**: Open http://localhost:5005 in your browser

## Usage

### Scanning Cards

1. Click or drag-and-drop an image of a Pokemon card
2. Optionally specify a set hint (e.g., "sv1", "base1") to narrow down results
3. Optionally specify a region (WEST, JP, CN)
4. Click "Identify Card"
5. View the identified card and add it to your collection

### Managing Collection

- View all cards in your collection
- Delete cards you no longer own
- Track condition, language, and quantity

### Statistics

- View total cards, unique cards, and total items
- See breakdown by set

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | Main web interface |
| POST | /api/scan | Identify a card from image |
| GET | /api/collection | Get all collection items |
| POST | /api/collection/add | Add card to collection |
| GET | /api/collection/{id} | Get specific collection item |
| DELETE | /api/collection/{id} | Delete collection item |
| GET | /api/stats | Get collection statistics |
| GET | /api/sets | Get all available sets |
| GET | /api/cards/{id} | Get card details |
| GET | /health | Health check |

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| PTCG_API_KEY | Pokemon TCG API key | (required) |
| HOST | Server host | 0.0.0.0 |
| PORT | Server port | 5005 |

## Interface language (i18n)

The UI ships in English and German. Which one you get is decided in this order:
`?lang=de` on the URL → `ui_lang` cookie → the browser's `Accept-Language`
header → English. The picker under **User menu → Interface Language** writes
that cookie, so the choice survives reloads; `?lang=` also persists it.

This is separate from **Card Language**, which is a property of your collection
(which printing of a card to add, stored on the user in SQLite). A German
collector may well want an English interface and German cards.

How it works:

| Piece | Role |
| --- | --- |
| `src/services/i18n.py` | Picks the starting language for a request and loads that catalog; the catalog is inlined into the page, so the first paint needs no extra request. |
| `src/static/i18n/de.json` | Flat catalog: English source text → translation. English is the source language and has no catalog. |
| `src/static/js/i18n.js` | `t(text, params)` for code, plus a document walk and MutationObserver so markup and JS-rendered panels are translated as they appear. Switching language re-translates in place, no reload. |

Because the catalog is keyed by the English text that is already in the markup,
adding a translation means adding a key, not renaming anything. Strings built by
concatenation (counters, "Page 2 of 7") are translated by calling `t()` with
`{placeholder}` names at the point where they are built.

### Adding a language

1. Copy `src/static/i18n/de.json` to `<code>.json` and translate the values
   (leave the keys alone).
2. Add the code to `SUPPORTED` and `LABELS` in `src/static/js/i18n.js`, and to
   `SUPPORTED` in `src/services/i18n.py`.
3. Add an `<option>` for it to `#ui-lang-pref` in `src/templates/index.html`
   (the picker also fills itself from `LABELS`).

Keys that are missing from a catalog simply stay in English, so a partial
translation degrades cleanly. Messages produced by the API (`detail` strings in
error responses) are not translated and surface in English.

## Live database backups

`data/pokemon_cards.db` is the live collection database. It is untracked but
deliberately *not* ignored, so git refuses to overwrite it. Under that guard,
git hooks verify it and keep timestamped copies in `data/backups/`:

```bash
python scripts/install_git_hooks.py   # once per clone, hooks are not versioned
python scripts/backup_live_db.py      # the same code the hooks run, by hand
```

| Hook | Fires | Case it protects |
| --- | --- | --- |
| `pre-commit` | any commit, including the one that concludes a conflicted merge | a merge that clobbered the file and left a conflict — no other hook runs for it, so this is what recovers it |
| `pre-merge-commit` | a merge succeeded, before its commit | verify and snapshot before the merge result is recorded |
| `post-checkout` | checkout or branch switch (also fast-forwards) | a blob arriving through a checkout |
| `post-merge` | a non-fast-forward merge that succeeded | the tree after a merge moved |
| `pre-rebase` | a rebase starts | a copy before the series rewrites the tree |

No hook can run *before* git writes the working tree, so the script does not rely
on order: it compares the live file with the blobs git holds for it (`MERGE_HEAD`,
`CHERRY_PICK_HEAD`, `REVERT_HEAD`, `HEAD`) and copies the newest snapshot back if
the file *is* one of them. That check runs before the snapshot step, so a
clobbered database is repaired rather than archived as the newest copy.

It compares identities rather than content, because the database is ~17 MB on a
network share. `data/backups/live-identity.json` records the live file's size,
mtime, sha256 and git blob id: matching size and mtime means a hook reads
nothing at all, a matching sha256 means only metadata moved, and git is asked for
object ids (`rev-parse ref:path`) instead of blob content. Content is read only
for a file that actually changed, and compared as text only where checkout
converts line endings.

One case has no hook at all: `git merge --abort` fires nothing, so a file it
replaced stays replaced until the next hooked command. If you abort a merge, run
`python scripts/backup_live_db.py` once to restore it.

Restore by hand, with the app stopped:

```bash
ls -lt data/backups                                  # newest first
cp data/backups/pokemon_cards.db.20261117-130515 data/pokemon_cards.db
python -c "import sqlite3;print(sqlite3.connect('data/pokemon_cards.db').execute('PRAGMA integrity_check').fetchone())"
```

## German card art provenance

`data/german_art_sources.json` records where each German scan under
`data/german_cards/images/` came from: `source`, `url`, `page`, `px` and `at`
when it was fetched, written by `scripts/fetch_german_art.py`. The vintage art
was installed before that manifest existed, so its entries were rebuilt from the
files themselves — `px` from the PNG header, `at` from the file's mtime, no `url`
and no `page` — and carry `"reconstructed": true`. Those fields are generated,
not observed.

Which entries carry it: currently 14,787 of 17,348, every one with
`source: "unknown"` and an empty `url` and `page`. The other 2,561 record a real
fetch and carry no flag at all — absence means observed, so `false` is never
written, and the counts move as cards are re-fetched. An entry with that same
signature is backfilled even when the flag is missing, which is how a later
backfill can be spotted.

What an audit should do with it:

- treat a reconstructed `px` as the installed file's own size rather than a
  measurement from a source, so it can be compared against the file to catch art
  replaced behind the manifest's back;
- read the flag as "origin lost", not "art unverified": the files were installed,
  are served, and pass the same checks as any other;
- expect `scripts/verify_german_backs.py` to report how many reconstructed
  entries it checked and any whose `px` no longer matches the installed file —
  `manifest_reconstructed` and `reconstructed_px_mismatch` in
  `data/german_back_audit.json`;
- expect the flag to disappear when a card is re-fetched: a repair writes a fresh
  observed record and drops it.

## Tests

The tests need the app's requirements plus the dev ones (`requirements-dev.txt`),
in a virtualenv of their own — `.venv/` in this checkout is the container's Linux
venv and does not run on the host:

```bash
py -3.12 -m venv venv                                   # python3 -m venv venv elsewhere
venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-dev.txt
venv/Scripts/python.exe -m pytest tests/ -q              # also: python -m unittest discover -s tests
```

`tests/test_quickdraw.py` additionally needs `data/quickdraw-index.bin` and the two
Wailmer images, and its API test needs a server on :5005; each skips with a reason
when its prerequisite is missing, so the rest of the suite still reports.

## Project Structure

```
pokemon-card-scanner/
├── src/
│   ├── api/
│   │   ├── main.py           # FastAPI application
│   │   ├── models/
│   │   │   ├── database.py    # SQLAlchemy models
│   │   │   └── schemas.py     # Pydantic schemas
│   │   └── routes/            # API routes (future)
│   ├── services/
│   │   ├── ptcg_client.py     # Pokemon TCG API client
│   │   └── collection.py      # Collection management
│   ├── static/
│   │   ├── css/
│   │   │   └── main.css       # Styles
│   │   ├── js/
│   │   │   └── main.js        # Frontend JavaScript
│   │   └── images/            # Static images
│   └── templates/
│       └── index.html         # Main HTML template
├── data/
│   └── pokemon_cards.db       # SQLite database
├── daemon.py                    # Entry point (detached server)
├── .env                       # Environment template
└── test_api.py                # Test script
```

## Pokemon TCG API

This project uses the [Pokemon TCG API](https://pokemontcgapi.com/) for:
- Card identification via vision API
- Card database information
- Set information

You need a free API key from their website. The free tier includes 5 trial recognitions.

## Alternative: Local Recognition

For offline recognition without API limits, you could integrate:
- [pokemon-card-recognizer](https://github.com/prateekt/pokemon-card-recognizer) - Python-based local recognition
- [Llama-3.2-11B-Vision-PokemonCard-OCR-LoRA](https://huggingface.co/netprtony/Llama-3.2-11B-Vision-PokemonCard-OCR-LoRA) - Vision model for OCR

## License

MIT License - Feel free to use and modify for personal or commercial purposes.

## Credits

- Pokemon TCG API: https://pokemontcgapi.com/
- FastAPI: https://fastapi.tiangolo.com/
- PokeCardEx: https://www.pokecardex.com/ (inspiration)

## Docker & Portainer

The repo ships a `Dockerfile` and `docker-compose.yml`. Pushing to `main` triggers
`.github/workflows/build.yml`, which builds a CPU-only image and publishes it to
**GitHub Container Registry** (`ghcr.io/pixelartist/pokemon-card-scanner:latest`).

### Deploy in Portainer

1. **Deploy → Stacks → Create stack** → choose **"Dockerfile / docker-compose.yml"**
   (or paste `docker-compose.yml` from this repo).
2. Bind-mount the data volume so your collection, DB, and scans persist:
   ```yaml
   volumes:
     scanner:
       driver: local
       driver_opts:
         type: none
         o: bind
         device: /opt/data/pokemon-card-scanner/data
   ```
   or simply `- /opt/data/pokemon-card-scanner/data:/opt/data/pokemon-card-scanner/data`.
3. Set `PTCG_API_KEY` in the stack environment.
4. Deploy. The container auto-seeds the SQLite schema on first boot.

> The data volume must be writable by uid `1000` (the container's `appuser`).
> On the NAS: `chown -R 1000:1000 /opt/data/pokemon-card-scanner/data`.

## Auth

Sessions use short-lived JWT access tokens (in memory / `sessionStorage`) plus a
30-day refresh token stored in an `httpOnly; Secure; SameSite=None` cookie,
hashed in SQLite and rotated on every use. Survives server restarts.
