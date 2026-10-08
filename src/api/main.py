"""FastAPI application for Pokemon Card Scanner."""
import os
import sys
import logging
import tempfile
import shutil
import time
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import requests
from datetime import datetime

from api.models.database import init_db, get_db_connection, User, Binder
from api.models.schemas import CollectionItemCreate, BatchCollectionCreate, UserRegister, UserLogin, AuthToken, UserResponse, BinderCreate, BinderUpdate
from services.ptcg_client import get_client
from services.collection import CollectionService
from services.auth import (
    AuthService, get_current_user, security, create_access_token,
    create_refresh_token, validate_refresh_token, revoke_refresh_token,
    REFRESH_COOKIE_NAME, REFRESH_COOKIE_MAX_AGE,
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database
init_db()

# Create scan images directory
SCAN_DIR = Path("/opt/data/pokemon-card-scanner/data/scans")
SCAN_DIR.mkdir(parents=True, exist_ok=True)

# Create FastAPI app
app = FastAPI(title="Pokemon Card Scanner", version="1.0.0")

# CORS middleware - allow the external URL origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://pixelartist.myqnapcloud.com:679", "http://localhost:5005", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

# Mount scan images directory
SCAN_MOUNT_DIR = Path("/opt/data/pokemon-card-scanner/data")
SCAN_MOUNT_PATH = str(SCAN_MOUNT_DIR)
if os.path.exists(SCAN_MOUNT_PATH):
    app.mount("/data", StaticFiles(directory=SCAN_MOUNT_PATH, check_dir=False), name="scans")

# Mount cached CLIP images FIRST (before general /static to avoid shadowing)
clip_images_dir = os.path.join(BASE_DIR, "..", "data", "clip_images")
if os.path.exists(clip_images_dir):
    app.mount("/static/clip_images", StaticFiles(directory=clip_images_dir), name="clip_images")

# Mount actual card images (the index may reference them with stale/typo'd URLs)
card_images_dir = os.path.join(BASE_DIR, "..", "data", "images")
if os.path.exists(card_images_dir):
    app.mount("/static/card_images", StaticFiles(directory=card_images_dir), name="card_images")

# Mount German card images (index references /static/german_cards/images/de_*.png)
german_images_dir = os.path.join(BASE_DIR, "..", "data", "german_cards", "images")
if os.path.exists(german_images_dir):
    app.mount("/static/german_cards/images", StaticFiles(directory=german_images_dir), name="german_images")

# Mount static files (JS, CSS, etc.) - MUST be after /static/clip_images
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Use Jinja2 directly for template rendering
import jinja2
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(templates_dir),
    cache_size=0
)

# Collection service
collection_service = CollectionService()


def set_refresh_cookie(response, raw_token: str):
    """Attach the refresh token as an httpOnly SameSite=None cookie.

    SameSite=None is required because the app is reached through the QNAP
    cloud proxy on a non-standard port; Secure is kept for the HTTPS URL.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


@app.middleware("http")
async def renew_access_token_middleware(request: Request, call_next):
    """Propagate a silently-renewed access token to the frontend.

    When cookie-based re-auth happens in get_current_user it stashes a fresh
    JWT on request.state; we forward it as a response header the client stores
    in memory, so expired bearer tokens never surface as a login prompt.
    """
    response = await call_next(request)
    renewed = getattr(request.state, "renew_access_token", None)
    if renewed:
        response.headers["X-Renew-Access-Token"] = renewed
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main page."""
    try:
        stats = collection_service.get_stats()
        template = jinja_env.get_template("index.html")
        html = template.render(request=request, stats=stats, cache_bust=int(time.time()), timestamp=int(time.time()))
        return HTMLResponse(content=html)
    except Exception:
        return HTMLResponse(content="""<!DOCTYPE html>
<html><head><title>Pokemon Card Scanner</title>
<style>body{font-family:system-ui,sans-serif;background:#1a1a2e;color:#eee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#16213e;padding:40px;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.4);text-align:center;max-width:480px}
h1{color:#ffd700;margin-bottom:8px} p{color:#aaa;margin-top:16px}
code{background:#0f3460;padding:2px 6px;border-radius:4px;color:#7ec8e3}</style>
</head><body><div class="card">
<h1>Pokemon Card Scanner</h1>
<p>API is running. Open the interactive docs at <code>/docs</code>.</p>
<p>Register an account at <code>POST /api/auth/register</code>, then scan cards with <code>POST /api/scan</code>.</p>
</div></body></html>""")


@app.post("/api/scan")
async def scan_card(
    image: UploadFile = File(...),
    set_hint: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
):
    """Identify a Pokemon card from an uploaded image."""
    try:
        image_bytes = await image.read()
        if len(image_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty image file")

        # Save original scan image
        scan_filename = f"scan_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{image.filename}"
        scan_path = SCAN_DIR / scan_filename
        with open(scan_path, "wb") as f:
            f.write(image_bytes)
        
        # Return relative path for storage
        scan_relative_path = f"scans/{scan_filename}"

        client = get_client()
        # Run CLIP/inference work off the event loop so /health stays responsive
        result = await asyncio.to_thread(
            client.identify_card,
            image_bytes=image_bytes,
            top_k=5,
            set_hint=set_hint,
            region=region,
        )

        # If we got candidates from offline matcher, they already have image_url and name
        # If from API, we need to hydrate with full card details
        if result.get("candidates"):
            meta = result.get("meta", {})
            source = meta.get("source", "api")

            if source not in ("clip_matcher", "offline_matcher"):
                candidate_ids = [c["id"] for c in result["candidates"]]
                cards = client.get_cards_batch(candidate_ids)
                card_map = {c["id"]: c for c in cards}

                for candidate in result["candidates"]:
                    card_data = card_map.get(candidate["id"])
                    if card_data:
                        candidate["card"] = card_data
            else:
                # Offline matchers already provide image_url and name
                for candidate in result["candidates"]:
                    if "card" not in candidate:
                        candidate["card"] = {
                            "id": candidate["id"],
                            "name": candidate["name"],
                            "set": candidate.get("set", ""),
                            "number": candidate.get("number", ""),
                            "images": {
                                "small": candidate.get("image_url", ""),
                                "large": candidate.get("image_url", ""),
                            }
                        }

        # Add scan path to response
        result["scan_image_path"] = scan_relative_path

        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collection/add")
async def add_to_collection(card_id: str = Form(...),
                            condition: str = Form("Near Mint"),
                            language: str = Form("EN"),
                            quantity: int = Form(1),
                            notes: Optional[str] = Form(None),
                            is_reverse_holo: bool = Form(False),
                            is_first_edition: bool = Form(False),
                            scan_image_path: Optional[str] = Form(None),
                            current_user: User = Depends(get_current_user)):
    """Add a card to the collection."""
    try:
        # Resolve card_id - handle both string ptcg_id and integer DB ID
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parent.parent.parent / "data" / "pokemon_cards.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Try to resolve as integer DB ID first
        try:
            db_id = int(card_id)
            row = cursor.execute("SELECT id FROM cards WHERE id = ?", (db_id,)).fetchone()
            if row:
                card_db_id = row["id"]
            else:
                # Try as ptcg_id
                row = cursor.execute("SELECT id FROM cards WHERE ptcg_id = ?", (card_id,)).fetchone()
                if row:
                    card_db_id = row["id"]
                else:
                    card_db_id = None
        except (ValueError, TypeError):
            # Treat as ptcg_id
            row = cursor.execute("SELECT id FROM cards WHERE ptcg_id = ?", (card_id,)).fetchone()
            if row:
                card_db_id = row["id"]
            else:
                card_db_id = None
        
        if not card_db_id:
            # Try fetching from API and adding to database
            from services.ptcg_client import get_client
            api_card = None
            try:
                client = get_client()
                api_card = client.get_card(card_id)
            except Exception:
                pass
            if api_card:
                cursor.execute(
                    """INSERT INTO cards
                       (ptcg_id, name, number, rarity, hp, types, artist, set_name, images)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        api_card.get("ptcg_id", card_id),
                        api_card.get("name", "Unknown"),
                        api_card.get("number", ""),
                        api_card.get("rarity", ""),
                        api_card.get("hp", ""),
                        api_card.get("types", ""),
                        api_card.get("artist", ""),
                        api_card.get("set", {}).get("name", "") if isinstance(api_card.get("set"), dict) else "",
                        api_card.get("images", {}),
                    )
                )
                conn.commit()
                card_db_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                # Fall back to CLIP index for cards not in the API
                from services.clip_matcher import get_clip_matcher
                matcher = get_clip_matcher()
                matcher.load_index()
                clip_card = matcher.cards.get(card_id)
                if not clip_card and card_id.startswith("de_"):
                    clip_card = matcher.cards.get(card_id[3:])
                if clip_card:
                    cursor.execute(
                        """INSERT OR IGNORE INTO cards (ptcg_id, name, number, rarity, hp, types, artist, set_name, images)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            clip_card.card_id,
                            clip_card.name or "Unknown",
                            clip_card.number or "",
                            clip_card.rarity or "",
                            clip_card.hp or "",
                            clip_card.types or "",
                            clip_card.artist or "",
                            clip_card.set_name or clip_card.set_code or "",
                            '{"small": "' + (clip_card.image_url or '') + '", "large": "' + (clip_card.image_url or '') + '"}',
                        )
                    )
                    conn.commit()
                    row = cursor.execute("SELECT id FROM cards WHERE ptcg_id = ?", (card_id,)).fetchone()
                    card_db_id = row["id"] if row else None
                else:
                    conn.close()
                    raise HTTPException(status_code=404, detail=f"Card {card_id} not found")
        conn.close()

        item_data = CollectionItemCreate(
            card_id=card_db_id,
            condition=condition,
            language=language,
            quantity=quantity,
            notes=notes,
            is_reverse_holo=is_reverse_holo,
            is_first_edition=is_first_edition,
            scan_image_path=scan_image_path,
        )
        item = collection_service.add_card(item_data, user_id=current_user.id)
        return {"success": True, "id": item["id"]}
    except Exception as e:
        logger.error(f"Add to collection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collection/add-batch")
async def add_to_collection_batch(
    items: List[CollectionItemCreate],
    current_user: User = Depends(get_current_user)
):
    """Add multiple cards to the collection in a single transaction."""
    try:
        saved = collection_service.add_cards_batch(items, user_id=current_user.id)
        return {
            "success": True,
            "added": len(saved),
            "ids": [item.id for item in saved]
        }
    except Exception as e:
        logger.error(f"Batch add error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collection")
async def get_collection(limit: int = 100, offset: int = 0,
                         current_user: User = Depends(get_current_user)):
    """Get all collection items."""
    items = collection_service.get_all_items(limit=limit, offset=offset, user_id=current_user.id)
    return {"items": items}


@app.get("/api/collection/export")
async def export_collection(format: str = "json",
                            current_user: User = Depends(get_current_user)):
    """Export collection in JSON or CSV format."""
    items = collection_service.get_all_items(limit=1000, user_id=current_user.id)
    
    if format == "csv":
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Card Name", "Set", "Number", "Rarity", "Condition", "Language", "Quantity", "Reverse Holo", "First Edition"])
        
        for item in items:
            card = item.get("card", {})
            writer.writerow([
                item["id"],
                card.get("name", ""),
                card.get("set_name", ""),
                card.get("number", ""),
                card.get("rarity", ""),
                item["condition"],
                item["language"],
                item["quantity"],
                item["is_reverse_holo"],
                item["is_first_edition"],
            ])
        
        from fastapi.responses import Response
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=collection.csv"}
        )
    
    return {"items": items}


@app.get("/api/collection/search")
async def search_collection(query: str,
                            current_user: User = Depends(get_current_user)):
    """Search collection by card name, set, or number (SQL-based)."""
    user_id = current_user.id
    query_lower = query.lower().strip()
    
    if not query_lower:
        return {"results": [], "count": 0}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Use SQL LIKE for server-side search
    search_param = f"%{query_lower}%"
    cursor.execute(
        """
        SELECT ci.id, ci.card_id, ci.condition, ci.language, ci.quantity,
               ci.notes, ci.is_reverse_holo, ci.is_first_edition,
               ci.date_acquired, ci.added_at, ci.scan_image_path,
               c.name as card_name, c.ptcg_id, c.rarity, c.number,
               c.set_name, c.hp, c.types, c.images, c.artist,
               s.name as set_full_name
        FROM collection_items ci
        JOIN cards c ON ci.card_id = c.id
        LEFT JOIN sets s ON c.set_id = s.id
        WHERE ci.user_id = ? AND (
            LOWER(c.name) LIKE ? OR
            LOWER(c.set_name) LIKE ? OR
            LOWER(COALESCE(c.number, '')) LIKE ?
        )
        ORDER BY c.name ASC
        """,
        (user_id, search_param, search_param, search_param)
    )
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        item = dict(row)
        item["is_reverse_holo"] = bool(item["is_reverse_holo"])
        item["is_first_edition"] = bool(item["is_first_edition"])
        if item.get("images"):
            try:
                item["images"] = json.loads(item["images"])
            except:
                item["images"] = []
        results.append(item)
    
    return {"results": results, "count": len(results)}


@app.get("/api/collection/{item_id}")
async def get_collection_item(item_id: int,
                              current_user: User = Depends(get_current_user)):
    """Get a single collection item."""
    item = collection_service.get_item(item_id, user_id=current_user.id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.delete("/api/collection/{item_id}")
async def delete_collection_item(item_id: int,
                                 current_user: User = Depends(get_current_user)):
    """Remove a card from the collection."""
    success = collection_service.delete_item(item_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True}


# ==================== Binder Endpoints ====================

@app.post("/api/binders")
async def create_binder(binder_data: BinderCreate,
                        current_user: User = Depends(get_current_user)):
    """Create a new personal binder."""
    try:
        binder = collection_service.create_binder(
            name=binder_data.name,
            description=binder_data.description or "",
            user_id=current_user.id
        )
        return binder
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binders")
async def get_binders(current_user: User = Depends(get_current_user)):
    """Get all binders for the current user."""
    binders = collection_service.get_binders(user_id=current_user.id)
    return {"binders": binders}


@app.get("/api/binders/{binder_id}")
async def get_binder(binder_id: int,
                     current_user: User = Depends(get_current_user)):
    """Get a specific binder with its items."""
    binder = collection_service.get_binder(binder_id, user_id=current_user.id)
    if not binder:
        raise HTTPException(status_code=404, detail="Binder not found")
    return binder


@app.put("/api/binders/{binder_id}")
async def update_binder(binder_id: int,
                        binder_data: BinderUpdate,
                        current_user: User = Depends(get_current_user)):
    """Update a binder (name, description, or items)."""
    binder = collection_service.update_binder(
        binder_id=binder_id,
        user_id=current_user.id,
        name=binder_data.name,
        description=binder_data.description,
        item_ids=binder_data.item_ids
    )
    if not binder:
        raise HTTPException(status_code=404, detail="Binder not found")
    return binder


@app.delete("/api/binders/{binder_id}")
async def delete_binder(binder_id: int,
                        current_user: User = Depends(get_current_user)):
    """Delete a binder."""
    success = collection_service.delete_binder(binder_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Binder not found")
    return {"success": True}


@app.get("/api/stats")
async def get_stats(current_user: User = Depends(get_current_user)):
    """Get collection statistics."""
    return collection_service.get_stats(user_id=current_user.id)


@app.get("/api/sets")
async def get_sets():
    """Get all available sets."""
    client = get_client()
    sets = client.get_sets()
    return {"sets": sets}


def _resolve_local_card_image(ptcg_id: str, img_url: str) -> str:
    """Prefer locally-served card images; fall back to the remote URL."""
    if ptcg_id:
        base = Path(__file__).resolve().parent.parent.parent / "data"
        candidates = [
            (base / "clip_images" / f"{ptcg_id}.jpg", f"/static/clip_images/{ptcg_id}.jpg"),
            (base / "images" / "tcgdex" / "en" / f"{ptcg_id}.png", f"/static/card_images/tcgdex/en/{ptcg_id}.png"),
            (base / "images" / "tcgdex" / "en" / f"{ptcg_id}.jpg", f"/static/card_images/tcgdex/en/{ptcg_id}.jpg"),
        ]
        for full, url in candidates:
            if full.exists():
                return url
    return img_url or ""


@app.get("/api/cards/search")
async def search_cards(query: str, limit: int = 20):
    """Search cards by name, set code, or number (public endpoint, no auth).
    Searches both the local DB and the full CLIP card index (~21k cards)."""
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "pokemon_cards.db"

    q = query.strip().lower()
    results = []
    seen = set()

    # 1) Database results
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    search_param = f"%{q}%"
    cursor.execute(
        """
        SELECT c.id, c.ptcg_id, c.name, c.number, c.rarity, c.set_name, c.hp, c.types, c.images
        FROM cards c
        WHERE c.name LIKE ? OR c.number LIKE ? OR c.set_name LIKE ? OR c.ptcg_id LIKE ?
        ORDER BY c.name ASC
        LIMIT ?
        """,
        (search_param, search_param, search_param, search_param, limit)
    )
    for row in cursor.fetchall():
        d = dict(row)
        if d.get("images"):
            try:
                d["images"] = __import__("json").loads(d["images"])
            except Exception:
                d["images"] = {}
            # Resolve external image URLs to local static paths
            for img_key in ("small", "large"):
                img_url = d["images"].get(img_key, "")
                if img_url and img_url.startswith("https://"):
                    d["images"][img_key] = _resolve_local_card_image(d.get("ptcg_id") or "", img_url)
        if d.get("ptcg_id"):
            seen.add(d["ptcg_id"])
        results.append(d)
    conn.close()

    # 2) CLIP index results (covers cards not yet in the DB)
    if len(results) < limit:
        try:
            from services.clip_matcher import get_clip_matcher
            matcher = get_clip_matcher()
            matcher.load_index()
            for card in matcher.cards.values():
                if len(results) >= limit:
                    break
                if card.card_id in seen:
                    continue
                haystacks = [
                    (card.name or "").lower(),
                    (card.number or "").lower(),
                    (card.set_code or "").lower(),
                    (card.set_name or "").lower(),
                    card.card_id.lower(),
                ]
                if any(q in h for h in haystacks):
                    seen.add(card.card_id)
                    img = _resolve_local_card_image(card.card_id, card.image_url or "")
                    results.append({
                        "id": None,
                        "ptcg_id": card.card_id,
                        "name": card.name,
                        "number": card.number,
                        "rarity": card.rarity or "",
                        "set_name": card.set_name or card.set_code,
                        "types": getattr(card, "types", None) or [],
                        "images": {"small": img, "large": img},
                    })
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("CLIP index search failed: %s", e)

    # Sort: exact-ish name matches first
    results.sort(key=lambda d: (0 if q in (d.get("name") or "").lower() else 1, d.get("name") or ""))
    results = results[:limit]

    return {"results": results, "count": len(results)}


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    """Get a single card by ID (supports both ptcg_id like "me05-015" and DB PK)."""
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "pokemon_cards.db"
    
    # Try to resolve as integer DB ID first
    try:
        db_id = int(card_id)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (db_id,)).fetchone()
        conn.close()
        if row:
            card_dict = dict(row)
            return {
                "ptcg_id": card_dict.get("ptcg_id"),
                "name": card_dict.get("name"),
                "number": card_dict.get("number"),
                "set": {"code": "", "name": card_dict.get("set_name") or ""},
                "rarity": card_dict.get("rarity") or "",
                "types": card_dict.get("types") or [],
                "hp": card_dict.get("hp") or None,
                "images": card_dict.get("images") or {},
                "source": "database"
            }
    except (ValueError, TypeError):
        pass
    
    # Try ptcg_id lookup via client
    client = get_client()
    card = client.get_card(card_id)
    if card:
        return card
    
    # Fall back to CLIP index (covers German cards and unknown sets)
    from services.clip_matcher import get_clip_matcher
    matcher = get_clip_matcher()
    matcher.load_index()
    local = matcher.cards.get(card_id)
    if not local and card_id.startswith("de_"):
        local = matcher.cards.get(card_id[3:])
    if local:
        return {
            "ptcg_id": local.card_id,
            "name": local.name,
            "number": local.number,
            "set": {"code": local.set_code, "name": local.set_name or local.set_code},
            "rarity": local.rarity or "",
            "types": getattr(local, "types", None) or [],
            "hp": local.hp if getattr(local, "hp", None) else None,
            "images": {"small": local.image_url, "large": local.image_url},
            "source": "local_index"
        }
    raise HTTPException(status_code=404, detail="Card not found")


@app.post("/api/cards/{card_id}/create-or-get")
async def create_or_get_card(card_id: str):
    """Find or create a card in the local database. Returns DB ID for collection."""
    try:
        from api.models.database import init_db
        init_db()
        
        collection = CollectionService()
        
        # Try to get card details from TCGdex API
        r = requests.get(f"https://api.tcgdex.net/v2/en/cards/{card_id}", timeout=10)
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Card {card_id} not found in TCGdex")
        
        card_data = r.json()
        
        # Parse fields
        name = card_data.get("name", "")
        set_code = card_id.rsplit("-", 1)[0] if "-" in card_id else ""
        local_id = card_id.rsplit("-", 1)[-1] if "-" in card_id else ""
        set_name = card_data.get("set", {}).get("name", set_code)
        rarity = card_data.get("rarity", "")
        hp = card_data.get("hp", 0)
        types = card_data.get("types", [])
        image_url = card_data.get("image", "")
        
        # Create or get in database
        db_id = collection.get_or_create_card(
            card_id=card_id,
            name=name,
            set_code=set_code,
            set_name=set_name,
            number=local_id,
            rarity=rarity,
            hp=hp,
            types=types,
            image_url=image_url,
        )
        
        return {
            "success": True,
            "card_id": db_id,
            "ptcg_id": card_id,
            "name": name,
            "set": set_code,
            "number": local_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating card: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/scan/advanced")
async def scan_card_advanced(
    image: UploadFile = File(...),
    set_hint: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    include_prices: bool = Form(True),
):
    """Enhanced card scan with full details, prices, and structured output."""
    try:
        image_bytes = await image.read()
        if len(image_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty image file")

        client = get_client()
        result = client.identify_card(
            image_bytes=image_bytes,
            top_k=5,
            set_hint=set_hint,
            region=region,
        )

        if result.get("candidates"):
            candidate_ids = [c["id"] for c in result["candidates"]]
            cards = client.get_cards_batch(candidate_ids)
            card_map = {c["id"]: c for c in cards}

            for candidate in result["candidates"]:
                card_data = card_map.get(candidate["id"])
                if card_data:
                    candidate["card"] = card_data

        # Add structured output
        if result.get("candidates") and result["candidates"][0].get("card"):
            card = result["candidates"][0]["card"]
            result["structured"] = {
                "card_name": card.get("name"),
                "set": {
                    "name": card.get("set_name"),
                    "code": card.get("set", {}).get("code") if isinstance(card.get("set"), dict) else None,
                    "release_date": card.get("set", {}).get("release_date") if isinstance(card.get("set"), dict) else None,
                },
                "number": card.get("number"),
                "rarity": card.get("rarity"),
                "hp": card.get("hp"),
                "types": card.get("types"),
                "artist": card.get("artist"),
                "legal": card.get("legal"),
                "images": card.get("images"),
            }

        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Advanced scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cards/{card_id}/details")
async def get_card_details(card_id: str):
    """Get detailed card information including evolution chain and legalities."""
    # If the ID is numeric, treat it as a local DB PK and resolve to ptcg_id
    if card_id.isdigit():
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parent.parent.parent / "data" / "pokemon_cards.db"
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, ptcg_id, name, number, rarity, set_name, hp, types, images "
                "FROM cards WHERE id = ?", (int(card_id),)
            ).fetchone()
            conn.close()
            if row:
                if row["ptcg_id"]:
                    # Continue with the real ptcg_id below (API/index lookup)
                    card_id = row["ptcg_id"]
                else:
                    import json as _json
                    images = row["images"]
                    if isinstance(images, str):
                        try:
                            images = _json.loads(images)
                        except Exception:
                            images = None
                    types = row["types"]
                    if isinstance(types, str):
                        try:
                            types = _json.loads(types)
                        except Exception:
                            types = [types] if types else None
                    return {
                        "basic_info": {
                            "id": str(row["id"]),
                            "name": row["name"],
                            "number": row["number"],
                            "rarity": row["rarity"] or None,
                            "hp": row["hp"] or None,
                            "types": types,
                            "artist": None,
                            "set": {"name": row["set_name"], "code": row["set_name"]},
                            "images": images,
                        },
                        "legalities": None,
                        "prices": None,
                        "source": "local_db",
                    }
        except Exception:
            pass
    client = get_client()
    card = client.get_card(card_id)
    if not card:
        # Fall back to the local CLIP index (covers German 'de_*' cards and
        # any card the TCGdex API lookup misses)
        from services.clip_matcher import get_clip_matcher
        matcher = get_clip_matcher()
        matcher.load_index()
        local = matcher.cards.get(card_id)
        if not local and card_id.startswith("de_"):
            # Index stores German cards under their bare ID (e.g. 'bw5-1')
            # while image URLs use the 'de_' prefix
            local = matcher.cards.get(card_id[3:])
        if local:
            return {
                "basic_info": {
                    "id": local.card_id,
                    "name": local.name,
                    "number": local.number,
                    "rarity": local.rarity or None,
                    "hp": local.hp if getattr(local, "hp", None) else None,
                    "types": getattr(local, "types", None) or None,
                    "artist": getattr(local, "artist", None) or None,
                    "set": {"name": local.set_name or local.set_code, "code": local.set_code},
                    "images": {"small": local.image_url, "large": local.image_url},
                },
                "legalities": None,
                "prices": None,
                "source": "local_index",
            }
        raise HTTPException(status_code=404, detail="Card not found")
    
    # Add structured details
    details = {
        "basic_info": {
            "name": card.get("name"),
            "number": card.get("number"),
            "rarity": card.get("rarity"),
            "hp": card.get("hp"),
            "types": card.get("types"),
            "artist": card.get("artist"),
            "set": card.get("set"),
            "images": card.get("images"),
        },
        "legalities": card.get("legalities"),
        "prices": card.get("prices"),
        "source": card.get("source", "api"),
    }

    # Ensure images is parsed from string if needed
    if isinstance(details["basic_info"]["images"], str):
        try:
            import json
            details["basic_info"]["images"] = json.loads(details["basic_info"]["images"])
        except Exception:
            pass
    
    return details


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the app icon to avoid 404 noise in the browser console."""
    icon = os.path.join(static_dir, "images", "logo.png")
    if os.path.exists(icon):
        return FileResponse(icon, media_type="image/png")
    return Response(status_code=204)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Pokemon Card Scanner"}


@app.get("/api/status")
async def get_status():
    """Get scanner status including offline matcher availability."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    status_info = matcher.status()

    return {
        "status": "ok",
        "service": "Pokemon Card Scanner",
        "offline_matcher": {
            "available": status_info["usable"],
            "cards_indexed": status_info["indexed"],
            "loading": status_info["loading"],
            "device": status_info["device"],
        },
        "api_key_configured": bool(os.environ.get("PTCG_API_KEY", "")),
    }


@app.post("/api/index/build")
async def build_index(max_cards: int = 500):
    """Trigger background build of the CLIP card index."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    ok = matcher.build_index(max_cards=max_cards, in_background=True)
    return {"status": "started" if ok else "already_running"}


@app.get("/api/index/status")
async def index_status():
    """Get CLIP index build progress."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    return matcher.status()


@app.post("/api/index/enrich")
async def enrich_metadata():
    """Fetch TCGdex metadata for all indexed cards (rarity, HP, attacks, pricing)."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    enriched = matcher.enrich_metadata()
    return {
        "status": "complete",
        "enriched": enriched,
        "total": len(matcher.cards),
        "indexed": len(matcher.cards),
        "usable": len(matcher.cards) >= 20,
    }


@app.post("/api/index/build-german")
async def build_german_index(max_cards: int = 2500):
    """Build CLIP index from German TCGdex cards. Adds to existing English index."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    ok = matcher.build_german_index(max_cards=max_cards, in_background=True)
    return {"status": "started" if ok else "already_running"}


# Detect is CPU-heavy on the N5105 (~20s). Allow only one detection at a
# time; new requests get an immediate 'busy' response instead of piling up.
_detect_lock = asyncio.Lock()


@app.post("/api/detect")
async def detect_card(
    image: UploadFile = File(...)
):
    """Detect card in image and return bounding box with confidence."""
    try:
        image_bytes = await image.read()

        # Drop the request immediately if another detection is running
        if _detect_lock.locked():
            return JSONResponse({
                "found": False, "bbox": None, "confidence": 0.0,
                "score": 0.0, "busy": True,
            })

        async with _detect_lock:
            from services.card_detector import get_detector, _convert_numpy_types
            detector = get_detector()  # singleton: model loads once, not per request
            # Run blocking torch/numpy work off the event loop so /health stays up
            result = await asyncio.to_thread(detector.detect_card, image_bytes)

        return JSONResponse(_convert_numpy_types(result))

    except Exception as e:
        logger.error(f"Detection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Authentication Endpoints
# ============================================================

@app.post("/api/auth/register")
async def register_user(user_data: UserRegister):
    """Register a new user account."""
    auth_service = AuthService()
    try:
        user = auth_service.register(user_data)
        response = JSONResponse(content={
            "access_token": create_access_token({"sub": user.id}),
            "token_type": "bearer",
            "expiration_minutes": 30,
            "user": user.model_dump(mode="json"),
        })
        set_refresh_cookie(response, create_refresh_token(user.id))
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/login")
async def login_user(login_data: UserLogin):
    """Login and receive access token + httpOnly refresh cookie."""
    auth_service = AuthService()
    try:
        result = auth_service.login(login_data)
        response = JSONResponse(content={
            "access_token": result["access_token"],
            "token_type": result["token_type"],
            "expiration_minutes": 30,
            "user": result["user"].model_dump(mode="json"),
        })
        set_refresh_cookie(response, create_refresh_token(result["user"].id))
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/refresh")
async def refresh_session(request: Request):
    """Exchange a valid refresh cookie for a new access token (and rotated cookie)."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    user_id = validate_refresh_token(raw)
    if user_id is None:
        raise HTTPException(status_code=401, detail="No active session")

    # Rotate: revoke the presented token, issue a fresh one
    revoke_refresh_token(raw)
    new_refresh = create_refresh_token(user_id)

    user = AuthService().get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    response = JSONResponse(content={
        "access_token": create_access_token({"sub": user_id}),
        "token_type": "bearer",
        "expiration_minutes": 30,
        "user": user.model_dump(mode="json"),
    })
    set_refresh_cookie(response, new_refresh)
    return response


@app.post("/api/auth/logout")
async def logout_user(request: Request):
    """Revoke the refresh session and clear the cookie."""
    revoke_refresh_token(request.cookies.get(REFRESH_COOKIE_NAME))
    response = JSONResponse(content={"success": True})
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/", samesite="none", secure=True)
    return response


@app.get("/api/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        created_at=current_user.created_at
    )


# Keep a reference to the warm-up task so it isn't garbage-collected
# before completion (Python may collect a fire-and-forget task).
_warmup_task = None


@app.on_event("startup")
async def startup_event():
    """On startup, sync some basic data."""
    global _warmup_task
    logger.info("Pokemon Card Scanner starting up...")
    # Warm heavy models in a background task so the port binds immediately
    # (avoids proxy 502s during the ~40s model load) and the first real
    # request doesn't pay the load cost.
    async def _warm_models():
        try:
            from services.card_detector import get_detector
            await asyncio.to_thread(get_detector)
            logger.info("✓ Card detector warmed")
        except Exception as e:
            logger.warning(f"Detector warm-up failed: {e}")
        try:
            from services.clip_matcher import get_clip_matcher
            matcher = get_clip_matcher()

            def _warm_clip():
                matcher.load_index()
                matcher._load_model()  # the ~40s CLIP load, done before first scan
            await asyncio.to_thread(_warm_clip)
            logger.info("✓ CLIP matcher warmed")
        except Exception as e:
            logger.warning(f"CLIP warm-up failed: {e}")

    _warmup_task = asyncio.create_task(_warm_models())


# Global exception handlers to prevent HTML error pages
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error": "HTTP Exception"}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "error": "Validation Error"}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )