# Pokemon Card Scanner

A locally hosted website for scanning and identifying Pokemon cards, similar to PokeCardEx but self-hosted.

## Features

- **Card Scanner**: Upload images to identify Pokemon cards using the Pokemon TCG API vision service
- **Collection Management**: Track your cards with condition, language, quantity, and notes
- **Statistics**: View collection stats including total cards, unique cards, and breakdown by set
- **Offline Database**: SQLite database stores your collection locally
- **Responsive UI**: Works on desktop and mobile devices

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
