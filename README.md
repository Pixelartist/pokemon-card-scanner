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
   python run.py
   # Or: uvicorn src.api.main:app --reload
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
├── run.py                     # Entry point
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
