"""FastAPI application for Pokemon Card Scanner."""
import os
import sqlite3
import logging
import time
import asyncio
import json
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException

# Load environment variables
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Data root and env file are overridable so the app runs outside the NAS layout
# (inside the container PROJECT_ROOT resolves to the same mounted root anyway).
DATA_ROOT = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
load_dotenv(os.environ.get("ENV_FILE", PROJECT_ROOT / ".env"))

import requests
from datetime import datetime, timezone
from urllib.parse import urlsplit

from api.models.database import init_db, get_db_connection, DATABASE_URL
db_path = DATABASE_URL  # backward-compatible alias
from api.models.schemas import CollectionItemCreate, BatchCollectionCreate, UserRegister, UserLogin, AuthToken, UserResponse, UserProfileUpdate, BinderCreate, BinderUpdate, AdminPasswordSet, AdminUserUpdate, AdminCorrectionUpdate
from services import identify_jobs
from services import i18n as ui_i18n
from services.card_query import split_name_number
from services.ptcg_client import get_client
from services.collection import CollectionService
from services.auth import (
    AuthService, get_current_user, security, create_access_token,
    create_refresh_token, validate_refresh_token, revoke_refresh_token,
    verify_token,
    REFRESH_COOKIE_NAME, REFRESH_COOKIE_MAX_AGE,
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------- guard rails
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024


class _RateLimiter:
    """Tiny in-process sliding-window limiter.

    One uvicorn worker per container, so an in-memory counter is enough; a
    multi-worker deployment would need a shared store (Redis, etc.).
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.max_calls:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            # Keep the dict bounded on a long-running process
            if len(self._hits) > 4096:
                for k in [k for k, v in self._hits.items() if now - v[-1] >= self.window]:
                    self._hits.pop(k, None)
            return True


# Heavy inference endpoints share one bucket: every scan writes an image to disk.
_scan_limiter = _RateLimiter(60, 60)
_login_limiter = _RateLimiter(10, 60)      # keyed by username
_register_limiter = _RateLimiter(5, 3600)  # keyed by email


def _enforce(limiter: _RateLimiter, key: str, detail: str) -> None:
    """Raise 429 when `key` has exhausted its budget."""
    if not limiter.allow(key):
        raise HTTPException(status_code=429, detail=detail)


def _check_upload(image_bytes: bytes) -> None:
    """Reject oversized uploads before they reach the matcher or the disk."""
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

# Initialize database
init_db()

# Create scan images directory
SCAN_DIR = DATA_ROOT / "scans"
SCAN_DIR.mkdir(parents=True, exist_ok=True)

# Card crops cut out of scan photos by the detector (shown in detail modals)
CROP_DIR = SCAN_DIR / "crops"
CROP_DIR.mkdir(parents=True, exist_ok=True)

# Crops the browser's presence gate refused. The gate logged a verdict but kept
# no image, so a refusal could only be argued about; one file per refusal, served
# at /data/scans/refused/... for the on-screen thumbnail.
REFUSED_DIR = SCAN_DIR / "refused"
REFUSED_DIR.mkdir(parents=True, exist_ok=True)
REFUSED_KEEP = 50


def save_refused_crop(image_bytes: bytes) -> Optional[str]:
    """Store a refused crop; return its data-relative path, or None on failure.

    Never raises: a diagnostic save must not change the match response.
    """
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        (REFUSED_DIR / f"refused_{stamp}.jpg").write_bytes(image_bytes)
        prune_refused_crops()
        return f"scans/refused/refused_{stamp}.jpg"
    except Exception as e:
        logger.warning("Refused crop save failed: %s", e)
        return None


def prune_refused_crops() -> None:
    """Keep only the newest REFUSED_KEEP refused crops.

    Names carry a fixed-width timestamp, so name order is age order. Never
    raises: pruning must not change the match response.
    """
    try:
        for old in sorted(REFUSED_DIR.glob("refused_*.jpg"), reverse=True)[REFUSED_KEEP:]:
            old.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Refused crop prune failed: %s", e)


def _safe_upload_name(filename: Optional[str]) -> str:
    """Return a filesystem-safe basename for a client-supplied upload name.

    UploadFile.filename is attacker-controlled: absolute paths and '../'
    segments must never influence where the scan is written.
    """
    base = os.path.basename((filename or "").replace("\\", "/")).strip()
    base = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)
    base = base.lstrip(".").strip("_")
    return base[:100] or "upload"


def crop_card_image(image_bytes: bytes, timestamp: str) -> Optional[str]:
    """Run the detector on a scan image and save the cropped card.

    Returns the relative path (for the URL /data/scans/crops/...) or None if
    no card could be cut out. Never raises: a failed crop must not fail the
    scan itself.
    """
    try:
        import io
        from PIL import Image
        from services.card_detector import get_detector

        detector = get_detector()
        result = detector.detect_card(image_bytes)
        if not result.get("found") or not result.get("bbox"):
            return None

        x, y, w, h = result["bbox"]
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img_w, img_h = img.size
        pad = max(2, int(0.01 * max(w, h)))  # 1% breathing room
        left, top = max(0, x - pad), max(0, y - pad)
        right, bottom = min(img_w, x + w + pad), min(img_h, y + h + pad)
        if right - left < 40 or bottom - top < 40:
            return None  # degenerate detection

        crop = img.crop((left, top, right, bottom))
        # Keep file sizes sane on the NAS without losing detail
        if max(crop.size) > 1200:
            crop.thumbnail((1200, 1200), Image.LANCZOS)
        crop_path = CROP_DIR / f"crop_{timestamp}.jpg"
        crop.save(crop_path, "JPEG", quality=88)
        return f"scans/crops/crop_{timestamp}.jpg"
    except Exception as e:
        logger.warning(f"Card crop failed: {e}")
        return None

def _decode_image_data_url(data_url: str, max_bytes: int = 8 * 1024 * 1024):
    """(bytes, extension) for an inline image, or (None, None).

    Only real images pass: the browser sends `data:image/jpeg;base64,...`, and
    the caller writes the result to disk, so the magic bytes are checked too.
    """
    import base64

    header, _, payload = (data_url or "").partition(",")
    if "base64" not in header or not payload or not header.lower().startswith("data:image/"):
        return None, None
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None, None
    if not raw or len(raw) > max_bytes:
        return None, None
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, "jpg"
    if raw.startswith(b"\x89PNG"):
        return raw, "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "webp"
    return None, None


def _save_browser_image(data_url: str, kind: str) -> Optional[str]:
    """Write an inline browser image under data/scans and return its path.

    The camera path of the scanner matches in the browser and never uploads its
    frame, so the frame and its cut exist only as data URLs until here: the row
    needs a file for both, and the identify worker needs the cut on disk.
    """
    raw, ext = _decode_image_data_url(data_url)
    if not raw:
        return None
    folder = CROP_DIR if kind == "crop" else SCAN_DIR
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    name = f"crop_{stamp}_q.{ext}" if kind == "crop" else f"scan_{stamp}_browser.{ext}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(raw)
    except OSError as e:
        logger.warning(f"Browser {kind} save failed: {e}")
        return None
    return f"scans/crops/{name}" if kind == "crop" else f"scans/{name}"


def _persist_capture_path(path_value: Optional[str],
                          data_url: Optional[str],
                          kind: str) -> Optional[str]:
    """Store-or-drop a capture field, always returning a relative path or None.

    A collection row may only point at a file under data/scans/. The browser
    sends either a path the server already wrote or an inline data URL it wants
    stored; anything else (absolute paths, remote URLs, a base64 blob in the
    path column) is dropped instead of being written to the row — that is how
    `/data/data:image/jpeg;base64,...` used to end up as a broken image.
    """
    candidate = (path_value or "").strip()
    if candidate.startswith("data:image/"):
        candidate, data_url = "", candidate
    if candidate:
        if candidate.startswith("/") or ".." in candidate or not candidate.startswith("scans/"):
            return None
        return candidate if (SCAN_DIR.parent / candidate).is_file() else None
    if data_url:
        return _save_browser_image(data_url, kind)
    return None


# Create FastAPI app
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application):
    """Startup/shutdown hooks (replaces the deprecated @app.on_event API)."""
    logger.info("Pokemon Card Scanner starting up...")
    # Warm the CLIP index (fast binary load ~1s). The YOLO detector is loaded
    # lazily on the first scan request so startup stays fast.
    try:
        from services.clip_matcher import get_clip_matcher
        matcher = get_clip_matcher()
        await asyncio.to_thread(matcher.load_index_fast)
        logger.info("✓ CLIP matcher warmed")
    except Exception as e:
        logger.warning(f"CLIP warm-up failed: {e}")
    # Say what the background identify queue can actually reach, so a misbuilt
    # image (tesseract binary or tessdata missing) is visible at boot rather
    # than as rows that silently exhaust their attempts.
    providers = [name for name, _ in identify_jobs.providers()]
    logger.info("identify providers: %s", providers or "none (OCR/LLM unavailable)")
    logger.info("Startup complete - ready to serve requests")
    try:
        yield
    finally:
        global _db_conn
        if _db_conn is not None:
            logger.info("Closing DB connection on shutdown")
            try:
                _db_conn.close()
            except Exception as e:
                logger.warning(f"Error closing DB connection: {e}")
            _db_conn = None


app = FastAPI(title="Pokemon Card Scanner", version="1.0.0", lifespan=lifespan)

# Origins allowed to make credentialed cross-origin calls. One list, shared
# with the admin write guard below so the two cannot drift apart.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "https://pixelartist.myqnapcloud.com:679,http://localhost:5005",
    ).split(",")
    if o.strip()
]
# Extra origins the admin area accepts writes from. Only needed when the reverse
# proxy rewrites Host without forwarding X-Forwarded-Host, so the app cannot
# tell which origin the browser actually used.
ADMIN_EXTRA_ORIGINS = [
    o.strip() for o in os.environ.get("ADMIN_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# CORS middleware - explicit origins only. Never combine "*" with
# allow_credentials=True: Starlette then reflects any Origin, which lets any
# website make credentialed cross-origin calls with the refresh cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# A write to /api/admin has to prove it came from this deployment. The refresh
# cookie is SameSite=None (the QNAP proxy needs that) and get_current_user
# accepts it on every route, so without this check a page on any other site
# could ride the cookie into a password reset or a correction rewrite; CORS and
# the JSON body alone were what stood in the way. Browsers always send Origin on
# a non-GET request, so it is the proof we can rely on: the scheme is ignored
# (the proxy terminates TLS) but the port is kept, because the NAS serves other
# apps on the same host.
_ADMIN_PATH = "/api/admin"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _origin_key(value: Optional[str]) -> Optional[str]:
    """``https://host:676/x`` -> ``host:676``; None when it is not an origin.

    ``Origin: null`` (a sandboxed iframe, a file:// page) has no host and is
    rejected with it: it proves nothing about who sent the request.
    """
    parts = urlsplit((value or "").strip())
    if not parts.hostname:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname.lower()}:{port}"


def _admin_write_origins(request: Request) -> set:
    """Origins this request may legitimately have come from."""
    keys = {_origin_key(o) for o in ALLOWED_ORIGINS + ADMIN_EXTRA_ORIGINS}
    # What the browser addressed us as: the proxy that keeps Host, or one that
    # forwards the original host, needs no configuration.
    for raw in (request.headers.get("x-forwarded-host"), request.headers.get("host")):
        if raw:
            keys.add(_origin_key("//" + raw.split(",")[0].strip()))
    keys.discard(None)
    return keys


@app.middleware("http")
async def guard_admin_writes(request: Request, call_next):
    """Reject a cookie-authenticated admin write that came from another site."""
    if (
        request.method not in _SAFE_METHODS
        and request.url.path.startswith(_ADMIN_PATH + "/")
        and REFRESH_COOKIE_NAME in request.cookies
    ):
        origin = request.headers.get("origin")
        # No Origin at all means a script or CLI client: there is no ambient
        # session for another site to ride, and an attacker cannot strip the
        # cookie a browser sends.
        if origin is not None:
            allowed = _admin_write_origins(request)
            if _origin_key(origin) not in allowed:
                logger.warning(
                    "Rejected %s %s: Origin %r is not this app (allowed: %s). "
                    "Behind a proxy that rewrites Host, add the public origin to "
                    "ADMIN_ALLOWED_ORIGINS.",
                    request.method, request.url.path, origin, ", ".join(sorted(allowed)),
                )
                return JSONResponse(
                    {"detail": "Cross-site admin request rejected"}, status_code=403
                )
    return await call_next(request)

# Mount static files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

# Mount scan images directory
SCAN_MOUNT_DIR = DATA_ROOT
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
    app.mount("/card_images", StaticFiles(directory=card_images_dir), name="card_images_root")

# Mount German card images (index references /static/german_cards/images/de_*.png)
german_images_dir = os.path.join(BASE_DIR, "..", "data", "german_cards", "images")
if os.path.exists(german_images_dir):
    app.mount("/static/german_cards/images", StaticFiles(directory=german_images_dir), name="german_images")
    # TCG Pocket images downloaded from marcelpanse/tcg-pocket-collection-tracker
    pocket_images_dir = DATA_ROOT / "images" / "pocket"
    pocket_images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/pocket", StaticFiles(directory=pocket_images_dir), name="pocket_images")
    # TCGdex English-side vintage card images (base, gym, neo, ecard, ex, dp, pl, bw, hgss, pop)
    tcgdex_images_dir = DATA_ROOT / "images" / "tcgdex"
    if tcgdex_images_dir.exists():
        app.mount("/static/tcgdex", StaticFiles(directory=tcgdex_images_dir), name="tcgdex_images")
    # Placeholder images for cards without scans
    placeholder_dir = Path(static_dir) / "images"
    placeholder_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/images", StaticFiles(directory=placeholder_dir), name="placeholder_images")

# Mount set logos/symbols (scripts/fetch_set_images.py writes them to data/images/sets)
# Windows' mimetypes registry has no .webp, so StaticFiles would label the set
# logos application/octet-stream and some browsers refuse to render them.
import mimetypes
mimetypes.add_type("image/webp", ".webp")
sets_images_dir = DATA_ROOT / "images" / "sets"
if sets_images_dir.exists():
    app.mount("/static/sets", StaticFiles(directory=sets_images_dir), name="set_images")

# Booster pack art used as the set-box backdrop (scripts/fetch_booster_art.py)
booster_images_dir = DATA_ROOT / "images" / "boosters"
if booster_images_dir.exists():
    app.mount("/static/boosters", StaticFiles(directory=booster_images_dir), name="booster_images")

# Mount static files (JS, CSS, etc.) - MUST be after /static/clip_images
if os.path.exists(static_dir):
    class NoCacheStaticFiles(StaticFiles):
        """No-cache headers so browser picks up edits immediately."""
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    app.mount("/static", NoCacheStaticFiles(directory=static_dir), name="static")

# Mount browser-local scanner at /scanner (reuses existing /static/card_images)
scanner_dir = os.environ.get(
    "SCANNER_DIR",
    str(PROJECT_ROOT / ".worktrees" / "local-scanner" / "node-scanner" / "public"),
)
if os.path.exists(scanner_dir):
    # follow_symlink: the scanner's index/metadata and image dirs are symlinks to
    # the canonical copies in src/static and data, so /scanner can never serve a
    # drifted snapshot of the card index.
    app.mount("/scanner", StaticFiles(directory=scanner_dir, html=True, follow_symlink=True), name="scanner")

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
        # Interface language: ?lang= -> ui_lang cookie -> Accept-Language -> en.
        # The catalog is inlined so the page renders translated without a
        # second request, and the page's own picker can switch without reloading.
        ui_lang = ui_i18n.resolve_request(request)
        html = template.render(
            request=request,
            stats=stats,
            cache_bust=int(time.time()),
            timestamp=int(time.time()),
            ui_lang=ui_lang,
            i18n_catalog=ui_i18n.catalog_json(ui_lang),
        )
        response = HTMLResponse(content=html)
        requested = ui_i18n.normalize(request.query_params.get("lang"))
        if requested in ui_i18n.SUPPORTED:
            # ?lang= is an explicit choice, same as the in-app picker.
            response.set_cookie(
                ui_i18n.COOKIE,
                requested,
                max_age=ui_i18n.COOKIE_MAX_AGE,
                samesite="lax",
                path="/",
            )
        return response
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
    lang: Optional[str] = Form(None),
    browser_crop: Optional[UploadFile] = Form(None),
):
    """Identify a Pokemon card from an uploaded image.

    The browser-side scanner (YOLO11 OBB + pHash) already computes a
    straightened crop of the card before this endpoint is called. When the
    caller sends it as `browser_crop`, the server stores THAT crop instead of
    re-running its own detector on the raw frame — which historically
    produced garbage 140x175 crops of table edges because the numpy fallback
    mis-detected on frames that don't even contain a card.

    If `lang` is set (e.g. 'en' or 'de'), restricts matching to cards of that
    language — English and German cards share the same artwork and their
    fingerprints are nearly identical, so without this an English card image
    falsely matches the German variant.
    """
    try:
        _enforce(_scan_limiter, "scan", "Too many scans in a row. Try again in a moment.")
        image_bytes = await image.read()
        if len(image_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty image file")
        _check_upload(image_bytes)

        # Save original scan image
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        scan_filename = f"scan_{ts}_{_safe_upload_name(image.filename)}"
        scan_path = SCAN_DIR / scan_filename
        with open(scan_path, "wb") as f:
            f.write(image_bytes)
        
        # Return relative path for storage
        scan_relative_path = f"scans/{scan_filename}"

        client = get_client()

        # Prefer the straightened crop the browser computed (YOLO11 OBB):
        # CLIP must embed the CARD, not the hand/background behind it —
        # full-frame embeddings produced confidently-wrong matches.
        crop_bytes = b""
        if browser_crop is not None:
            crop_bytes = await browser_crop.read()

        if crop_bytes:
            crop_ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')
            crop_path = CROP_DIR / f"crop_{crop_ts}.jpg"
            with open(crop_path, "wb") as f:
                f.write(crop_bytes)
            crop_relative_path = f"scans/crops/crop_{crop_ts}.jpg"
        else:
            from services.card_detector import get_detector
            detector = get_detector()
            detection = await asyncio.to_thread(detector.detect_card, image_bytes)
            crop_relative_path = await asyncio.to_thread(crop_card_image, image_bytes, datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f'))
            # Identify on the card crop when the server found one too.
            if detection.get("found") and crop_relative_path:
                try:
                    crop_bytes = (SCAN_DIR.parent / crop_relative_path).read_bytes()
                except OSError:
                    crop_bytes = b""

        # Run CLIP/inference work off the event loop so /health stays responsive
        result = await asyncio.to_thread(
            client.identify_card,
            image_bytes=crop_bytes or image_bytes,
            top_k=5,
            set_hint=set_hint,
            region=region,
            lang=lang,
        )

        # The cloud reader and the near-miss queue answer with id shapes the
        # catalogue never uses ("ex14:56" for "ex14-56", or a queue key like
        # "unknown:de_sv04-212:30"). Fix them before hydration looks them up,
        # so the correction the user made lands on the real card.
        _canonicalize_candidate_ids(result)

        # If we got candidates from offline matcher, they already have image_url and name
        # If from API, we need to hydrate with full card details
        if result.get("candidates"):
            meta = result.get("meta", {})
            source = meta.get("source", "api")

            if source not in ("clip_matcher", "offline_matcher", "quickdraw_matcher"):
                candidate_ids = [c["id"] for c in result["candidates"]]
                cards = await asyncio.to_thread(client.get_cards_batch, candidate_ids)
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
        if crop_relative_path:
            result["crop_image_path"] = crop_relative_path

        return JSONResponse(result)

    except HTTPException:
        raise  # 400/413/429 from the guards above are answers, not failures
    except Exception as e:
        raise _fail("Scan error", e)


def _resolve_card_id(card_id):
    """Labels join to the card index by ptcg_id.

    The add form posts either a ptcg_id ("sv10-099") or the numeric database id
    the browser resolved for it (15032) - a correction opened from the detail
    modal posts the latter. Resolve a numeric id and keep anything else as given.
    """
    if not card_id or not str(card_id).isdigit():
        return card_id
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT ptcg_id FROM cards WHERE id = ?", (int(card_id),)).fetchone()
        conn.close()
        return row["ptcg_id"] if row and row["ptcg_id"] else card_id
    except Exception as exc:
        logger.warning("scan label: could not resolve card id %s: %s", card_id, exc)
        return card_id


def _known_card_ids(card_ids: List[str]) -> set:
    """Which of these ptcg ids exist in the catalogue."""
    ids = [cid for cid in card_ids if cid]
    if not ids:
        return set()
    try:
        conn = get_db_connection()
        try:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT ptcg_id FROM cards WHERE ptcg_id IN ({marks})", ids
            ).fetchall()
            return {r["ptcg_id"] for r in rows}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("scan: could not check candidate ids: %s", exc)
        return set()


def _canonicalize_candidate_ids(result: Dict[str, Any]) -> None:
    """Rewrite reader id shapes onto catalogue ids, in place.

    Only an id the catalogue does not know is touched, and only when its
    canonical form does exist - so "me02.5-088" and other valid ids stay as
    they are. The guess the reader made is kept in ``id_normalized_from``.
    """
    if not isinstance(result, dict):
        return
    candidates = result.get("candidates") or []
    raw_ids = [str(c.get("id") or "") for c in candidates if isinstance(c, dict)]
    raw_ids = [cid for cid in raw_ids if cid]
    if not raw_ids:
        return
    try:
        from services.misread_training import canonical_card_id
    except Exception as exc:  # pragma: no cover - module always ships
        logger.debug("scan: canonical ids unavailable: %s", exc)
        return
    changed = {}
    for cid in set(raw_ids):
        canon = canonical_card_id(cid)
        if canon and canon != cid:
            changed[cid] = canon
    if not changed:
        return
    known = _known_card_ids(list(set(raw_ids) | set(changed.values())))
    if not known:
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("id") or "")
        canon = changed.get(cid)
        if canon and cid not in known and canon in known:
            candidate["id"] = canon
            candidate["id_normalized_from"] = cid
            logger.info("scan: candidate id %s normalized to %s", cid, canon)

    # Normalising can leave the same card twice (raw guess plus real id); keep
    # the better-scoring one so the top-k is not spent on a duplicate.
    kept: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("id") or "")
        if cid not in kept:
            kept[cid] = candidate
            order.append(cid)
        elif float(candidate.get("similarity") or 0) > float(kept[cid].get("similarity") or 0):
            kept[cid] = candidate
    if len(order) != len(candidates):
        result["candidates"] = [kept[cid] for cid in order]


def _write_scan_label(scan_path, user_id, labels):
    """Write <scan>.json beside the stored frame: what was filed, what the gate proposed.

    Why it is stored: the 78 similarity floor and the 2-point lead margin can only
    be re-tuned against captures whose true card is known, and a card the user
    actually filed is exactly that. Recording the gate's proposal next to the
    final choice turns ordinary use into a labelled corpus - "accepted",
    "corrected" and "picked" rows on real frames, which is what the handful of
    stored scans cannot provide. The runner-up's id is recorded with its score
    because a score alone cannot show whether the margin rejected the card that
    was filed or some other one. Never let a logging failure break the add.
    """
    if not scan_path:
        return
    # scan_path arrives from the browser: keep it inside data/scans/.
    if ".." in scan_path or not scan_path.startswith("scans/"):
        return
    try:
        target = DATA_ROOT / (scan_path + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "scan": scan_path,
            "crop": labels.get("crop"),
            "filed_as": _resolve_card_id(labels.get("filed_as")),
            "match_source": labels.get("match_source") or "unknown",
            "proposed_id": _resolve_card_id(labels.get("proposed_id")),
            "proposed_similarity": labels.get("proposed_similarity"),
            "proposed_second_id": _resolve_card_id(labels.get("proposed_second_id")),
            "proposed_second_similarity": labels.get("proposed_second_similarity"),
            "user_id": user_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("scan label not written for %s: %s", scan_path, exc)


@app.post("/api/collection/add")
async def add_to_collection(card_id: str = Form(...),
                            condition: str = Form("Near Mint"),
                            language: str = Form("EN"),
                            quantity: int = Form(1),
                            notes: Optional[str] = Form(None),
                            is_reverse_holo: bool = Form(False),
                            is_first_edition: bool = Form(False),
                            scan_image_path: Optional[str] = Form(None),
                            crop_image_path: Optional[str] = Form(None),
                            scan_data_url: Optional[str] = Form(None),
                            crop_data_url: Optional[str] = Form(None),
                            match_source: Optional[str] = Form(None),
                            match_proposed_id: Optional[str] = Form(None),
                            match_proposed_second_id: Optional[str] = Form(None),
                            match_proposed_similarity: Optional[float] = Form(None),
                            match_proposed_second_similarity: Optional[float] = Form(None),
                            current_user: UserResponse = Depends(get_current_user)):
    """Add a card to the collection."""
    try:
        # Resolve card_id - handle both string ptcg_id and integer DB ID
        import sqlite3
        from pathlib import Path
        conn = get_db_connection()
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
                # Fall back to CLIP index for cards not in the API. The load is
                # blocking (~100s worst case), so it runs off the event loop.
                clip_card = await asyncio.to_thread(_clip_index_card, card_id)
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

        # The capture the user filed lives either on disk already (an uploaded
        # scan) or inline in the browser (a camera-side match). Both shapes are
        # accepted, but the row only ever stores a path: an inline crop used to
        # be written to crop_image_path verbatim and the details view then asked
        # for /data/data:image/jpeg;base64,... which cannot resolve.
        scan_image_path = _persist_capture_path(scan_image_path, scan_data_url, "scan")
        crop_image_path = _persist_capture_path(crop_image_path, crop_data_url, "crop")

        item_data = CollectionItemCreate(
            card_id=card_db_id,
            condition=condition,
            language=language,
            quantity=quantity,
            notes=notes,
            is_reverse_holo=is_reverse_holo,
            is_first_edition=is_first_edition,
            scan_image_path=scan_image_path,
            crop_image_path=crop_image_path,
        )
        item = collection_service.add_card(item_data, user_id=current_user.id)
        _write_scan_label(scan_image_path, current_user.id, {
            "crop": crop_image_path,
            "filed_as": card_id,
            "match_source": match_source,
            "proposed_id": match_proposed_id,
            "proposed_similarity": match_proposed_similarity,
            "proposed_second_id": match_proposed_second_id,
            "proposed_second_similarity": match_proposed_second_similarity,
        })
        return {"success": True, "id": item["id"]}
    except Exception as e:
        raise _fail("Add to collection error", e)


@app.post("/api/collection/add-batch")
def add_to_collection_batch(
    items: List[CollectionItemCreate],
    current_user: UserResponse = Depends(get_current_user)
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
        raise _fail("Batch add error", e)


@app.get("/api/collection")
def get_collection(limit: int = 100, offset: int = 0,
                         current_user: UserResponse = Depends(get_current_user)):
    """Get all collection items."""
    items = collection_service.get_all_items(limit=limit, offset=offset, user_id=current_user.id)
    return {"items": items}


@app.get("/api/collection/export")
def export_collection(format: str = "json",
                            current_user: UserResponse = Depends(get_current_user)):
    """Export collection in JSON or CSV format."""
    # No cap: the previous limit=1000 silently truncated larger collections.
    items = collection_service.get_all_items(limit=None, user_id=current_user.id)
    
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
def search_collection(query: str,
                            current_user: UserResponse = Depends(get_current_user)):
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
            from services.collection import _normalize_images
            item["images"] = _normalize_images(item["images"], item.get("ptcg_id") or "")
        results.append(item)
    
    return {"results": results, "count": len(results)}


@app.get("/api/collection/crop/{card_id}")
def get_card_crop(card_id: str,
                        current_user: UserResponse = Depends(get_current_user)):
    """Most recent user scan of this card, preferring the detector crop.

    card_id may be a ptcg_id (with or without the de_ German-index prefix).
    Returns {found: false} when the user never scanned this card.
    """
    import sqlite3
    from pathlib import Path
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        candidates = [card_id]
        if card_id.startswith("de_"):
            candidates.append(card_id[3:])
        placeholders = ",".join("?" * len(candidates))
        cursor.execute(
            f"""SELECT ci.scan_image_path, ci.crop_image_path, ci.added_at
                FROM collection_items ci
                JOIN cards c ON ci.card_id = c.id
                WHERE ci.user_id = ? AND c.ptcg_id IN ({placeholders})
                  AND (ci.crop_image_path IS NOT NULL OR ci.scan_image_path IS NOT NULL)
                ORDER BY ci.added_at DESC LIMIT 1""",
            (current_user.id, *candidates),
        )
        row = cursor.fetchone()
        if not row:
            return {"found": False}
        def _stored_path(value):
            # Rows written before the add endpoint validated this can hold an
            # inline data URL; it is not a path and must not become a URL.
            value = (value or "").strip()
            if not value or value.startswith("/") or ".." in value or not value.startswith("scans/"):
                return None
            return value if (SCAN_DIR.parent / value).is_file() else None

        path = _stored_path(row["crop_image_path"]) or _stored_path(row["scan_image_path"])
        if not path:
            return {"found": False}
        kind = "crop" if _stored_path(row["crop_image_path"]) else "scan"
        # Paths are stored relative to data/ which is mounted at /data
        return {"found": True, "url": f"/data/{path}", "kind": kind, "date": row["added_at"]}
    finally:
        conn.close()


@app.get("/api/collection/{item_id}")
def get_collection_item(item_id: int,
                              current_user: UserResponse = Depends(get_current_user)):
    """Get a single collection item."""
    item = collection_service.get_item(item_id, user_id=current_user.id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.delete("/api/collection/{item_id}")
async def delete_collection_item(item_id: int,
                                 current_user: UserResponse = Depends(get_current_user)):
    """Remove a card from the collection."""
    success = collection_service.delete_item(item_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True}


# ==================== Binder Endpoints ====================

@app.post("/api/binders")
def create_binder(binder_data: BinderCreate,
                        current_user: UserResponse = Depends(get_current_user)):
    """Create a new personal binder."""
    try:
        binder = collection_service.create_binder(
            name=binder_data.name,
            description=binder_data.description or "",
            user_id=current_user.id
        )
        return binder
    except Exception as e:
        raise _fail("Binder create error", e)


@app.get("/api/binders")
def get_binders(current_user: UserResponse = Depends(get_current_user)):
    """Get all binders for the current user."""
    binders = collection_service.get_binders(user_id=current_user.id)
    return {"binders": binders}


@app.get("/api/binders/{binder_id}")
def get_binder(binder_id: int,
                     current_user: UserResponse = Depends(get_current_user)):
    """Get a specific binder with its items."""
    binder = collection_service.get_binder(binder_id, user_id=current_user.id)
    if not binder:
        raise HTTPException(status_code=404, detail="Binder not found")
    return binder


@app.put("/api/binders/{binder_id}")
async def update_binder(binder_id: int,
                        binder_data: BinderUpdate,
                        current_user: UserResponse = Depends(get_current_user)):
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
                        current_user: UserResponse = Depends(get_current_user)):
    """Delete a binder."""
    success = collection_service.delete_binder(binder_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Binder not found")
    return {"success": True}


@app.get("/api/stats")
def get_stats(current_user: UserResponse = Depends(get_current_user)):
    """Get collection statistics."""
    return collection_service.get_stats(user_id=current_user.id)


_SET_IMAGES_CACHE: dict = {}


def _local_set_images() -> dict:
    """ptcg_id (lowercase, de_ prefix dropped) -> {"logo": url, "symbol": url}.

    Built once from the sets table, which scripts/fetch_set_images.py fills with
    the TCGdex artwork it downloaded to data/images/sets.
    """
    if _SET_IMAGES_CACHE:
        return _SET_IMAGES_CACHE
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT ptcg_id, images FROM sets WHERE images IS NOT NULL AND images != ''"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            try:
                images = json.loads(row["images"])
            except (TypeError, ValueError):
                continue
            if not isinstance(images, dict):
                continue
            code = (row["ptcg_id"] or "").lower()
            if code.startswith("de_"):
                code = code[3:]
            _SET_IMAGES_CACHE[code] = images
    except Exception as e:
        logger.warning(f"set image index failed: {e}")
    return _SET_IMAGES_CACHE


_BOOSTER_INDEX: Dict[str, List[str]] = {}


def _local_booster_art() -> Dict[str, List[str]]:
    """set code -> booster art paths, written by scripts/fetch_booster_art.py.

    The manifest is the single store for pack art: unlike the sets table it
    also covers sets that never got a row there (older Japanese sets).
    """
    if _BOOSTER_INDEX:
        return _BOOSTER_INDEX
    try:
        index_path = DATA_ROOT / "cache" / "booster_art.json"
        if index_path.exists():
            with open(index_path) as f:
                for code, files in json.load(f).items():
                    _BOOSTER_INDEX[code.lower()] = files
    except Exception as e:
        logger.warning(f"booster art index failed: {e}")
    return _BOOSTER_INDEX


def _annotate_set_images(sets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach locally cached set artwork to each set in a listing."""
    index = _local_set_images()
    boosters = _local_booster_art()
    for s in sets:
        code = str(s.get("code") or s.get("id") or "").lower()
        if code.startswith("de_"):
            code = code[3:]
        existing = s.get("images") if isinstance(s.get("images"), dict) else {}
        images = {**existing, **(index.get(code) or {})}
        if boosters.get(code):
            images["boosters"] = boosters[code]
        if not images:
            continue
        s["images"] = images
        s["logo_url"] = images.get("logo")
        s["symbol_url"] = images.get("symbol")
        s["booster_urls"] = images.get("boosters") or []
    return sets


def _local_set_card_counts(codes: List[str]) -> Dict[str, int]:
    """ptcg_id -> number of cards actually stored for those sets."""
    if not codes:
        return {}
    try:
        conn = get_db_connection()
        try:
            placeholders = ",".join("?" for _ in codes)
            rows = conn.execute(
                f"SELECT s.ptcg_id, COUNT(c.id) AS n FROM sets s "
                f"LEFT JOIN cards c ON c.set_id = s.id "
                f"WHERE LOWER(s.ptcg_id) IN ({placeholders}) GROUP BY s.id",
                codes,
            ).fetchall()
        finally:
            conn.close()
        return {(row["ptcg_id"] or "").lower(): row["n"] for row in rows}
    except Exception as e:
        logger.warning(f"set card counts failed: {e}")
        return {}


def _annotate_set_totals(sets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace unusable TCGdex set sizes with what is actually stored locally.

    TCGdex reports cardCount.total = 1 for a handful of German sets whose card
    list it never filled in (de_30th, de_sv03, ...), which the Sets tab showed
    as "1 card". Only counts that low are replaced, so official sizes stand.
    """
    missing = []
    for s in sets:
        if (s.get("total") or 0) > 1:
            continue
        code = str(s.get("code") or s.get("id") or "").lower()
        if code:
            missing.append((code, s))
    counts = _local_set_card_counts([code for code, _ in missing])
    for code, s in missing:
        local = counts.get(code) or 0
        if local > (s.get("total") or 0):
            s["total"] = local
    return sets


@app.get("/api/sets")
def get_sets():
    """Get all available sets."""
    from pathlib import Path
    import json
    
    # First try API
    try:
        client = get_client()
        sets = client.get_sets()
        if sets:
            return {"sets": _annotate_set_totals(_annotate_set_images(sets))}
    except Exception as e:
        import logging
        logging.warning(f"API sets fetch failed: {e}")
    
    # Fall back to cached TCGdex sets
    try:
        cache_path = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "tcgdex" / "sets_en.json"
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
                tcgdex_sets = data.get('sets', [])
                
                # Convert TCGdex format to API format
                api_format_sets = []
                for s in tcgdex_sets:
                    api_format_sets.append({
                        'id': s.get('id', ''),
                        'code': s.get('id', ''),
                        'name': s.get('name', ''),
                        'series': s.get('series', ''),
                        'release_date': s.get('releaseDate', ''),
                        'total': s.get('cardCount', {}).get('total', 0),
                        'printed_total': s.get('cardCount', {}).get('official', 0),
                        'ptcgo_code': None,
                        'symbol_url': None,
                        'logo_url': None,
                    })
                return {"sets": _annotate_set_totals(_annotate_set_images(api_format_sets))}
    except Exception as e:
        import logging
        logging.error(f"Failed to load cached sets: {e}")
    
    # Last resort: return empty
    return {"sets": []}


_SET_ID_CACHE: dict = {}
_META_IMG_INDEX: dict = {}   # ptcg_id -> image url, built once
_db_conn = None


def _get_db():
    """One shared SQLite connection (WAL + pragmas set in get_db_connection)."""
    global _db_conn
    while True:
        if _db_conn is None:
            import logging
            logging.info("Creating new DB connection")
            _db_conn = get_db_connection()
        else:
            import logging
            logging.info(f"Reusing DB connection: {id(_db_conn)}")
        try:
            _db_conn.execute("SELECT 1")
            return _db_conn
        except Exception as e:
            import logging
            logging.error(f"DB connection is closed: {e}")
            _db_conn = None
            # loop and retry


def _get_set_id(set_code: str):
    return _SET_ID_CACHE.get(set_code) or _SET_ID_CACHE.get(set_code.lower())


def _cache_set_id(set_code: str, set_id: int):
    _SET_ID_CACHE[set_code] = set_id
    _SET_ID_CACHE[set_code.lower()] = set_id


def _meta_index():
    """Lazily build ptcg_id -> image index from cards_meta.json (was a per-card O(N) rescan)."""
    if not _META_IMG_INDEX:
        try:
            from pathlib import Path
            import json as _json
            meta_path = Path(__file__).resolve().parent.parent.parent / "src" / "static" / "cards_meta.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    for m in _json.load(f):
                        if m.get("set") and m.get("number") and m.get("image"):
                            _META_IMG_INDEX[f"{m['set']}-{m['number']}"] = m["image"]
        except Exception as e:
            import logging
            logging.warning(f"cards_meta index build failed: {e}")
    return _META_IMG_INDEX


def _resolve_placeholder(ptcg_id: str) -> str:
    img = _meta_index().get(ptcg_id)
    return img or "/static/images/placeholder.png"


@app.get("/api/sets/{set_code}/cards")
async def get_set_cards(set_code: str):
    """Get all cards in a set with basic info."""
    conn = _get_db()
    cursor = conn.cursor()

    # Get set info from local DB (cached in memory)
    set_id = _get_set_id(set_code)
    if set_id is None:
        set_row = cursor.execute(
            "SELECT * FROM sets WHERE ptcg_id = ?", (set_code,)
        ).fetchone()
        if not set_row:
            set_row = cursor.execute(
                "SELECT * FROM sets WHERE LOWER(ptcg_id) = LOWER(?)", (set_code,)
            ).fetchone()
        if set_row:
            set_id = set_row["id"]
            _cache_set_id(set_code, set_id)
    else:
        set_row = cursor.execute(
            "SELECT * FROM sets WHERE id = ?", (set_id,)
        ).fetchone()

    if not set_row:
        # Set not found in local DB — try external API fallback
        return await _get_set_cards_fallback(set_code)

    # Optimized query: select only needed columns, pre-extract images
    cursor.execute(
        """SELECT c.ptcg_id, c.name, c.number, c.rarity,
                  json_extract(c.images, '$.small') as small,
                  json_extract(c.images, '$.large') as large
           FROM cards c
           WHERE c.set_id = ?
           ORDER BY CAST(c.number AS INTEGER)""",
        (set_id,),
    )

    cards = []
    for row in cursor.fetchall():
        small = row["small"] or row["large"] or ""
        large = row["large"] or row["small"] or ""
        if not small:
            small = _resolve_placeholder(row["ptcg_id"])
            large = _resolve_placeholder(row["ptcg_id"])
        cards.append({
            "ptcg_id": row["ptcg_id"],
            "name": row["name"],
            "number": row["number"],
            "rarity": row["rarity"],
            "images": {"small": small, "large": large},
        })

    try:
        set_images = json.loads(set_row["images"]) if set_row["images"] else {}
    except (TypeError, ValueError):
        set_images = {}
    if not isinstance(set_images, dict):
        set_images = {}

    return {
        "set": {
            "id": set_row["id"],
            "ptcg_id": set_row["ptcg_id"],
            "name": set_row["name"],
            "series": set_row["series"],
            "release_date": set_row["release_date"],
            "total_cards": set_row["total_cards"],
            "images": set_images,
            "logo_url": set_images.get("logo"),
            "symbol_url": set_images.get("symbol"),
        },
        "cards": cards,
        "total": len(cards),
    }


async def _get_set_cards_fallback(set_code: str):
    """Fetch cards from external API or card catalog when set not in local DB."""
    try:
        client = get_client()
        sets = client.get_sets()
        set_info = None
        for s in sets:
            if s.get('id') == set_code or s.get('code') == set_code:
                set_info = s
                break

        if set_info:
            import requests
            from services.ptcg_client import _get_api_key
            api_key = _get_api_key()
            headers = {"X-Api-Key": api_key}

            all_cards = []
            limit = 250
            cursor = None
            while True:
                params = {'set': set_code, 'limit': limit}
                if cursor:
                    params['cursor'] = cursor

                resp = requests.get(
                    "https://api.pokemontcgapi.com/v1/cards",
                    headers=headers,
                    params=params,
                    timeout=30
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                cards_page = data.get('data', [])
                if not cards_page:
                    break
                all_cards.extend(cards_page)
                if len(cards_page) < limit:
                    break
                links = data.get('links', {})
                next_link = links.get('next')
                if not next_link:
                    break
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(next_link)
                query_params = parse_qs(parsed.query)
                cursor = query_params.get('cursor', [None])[0]
                if not cursor:
                    break

            all_cards.sort(key=lambda x: x.get('number', ''))
            return {
                "set": {
                    "id": set_info.get('id'),
                    "ptcg_id": set_info.get('code'),
                    "name": set_info.get('name'),
                    "series": set_info.get('series'),
                    "release_date": set_info.get('release_date'),
                    "total_cards": set_info.get('total'),
                },
                "cards": all_cards,
                "total": len(all_cards),
                "source": "api"
            }
    except Exception as e:
        import logging
        logging.error(f"API fetch failed for set {set_code}: {e}")
        logging.exception(e)

    # Fallback: card catalog
    try:
        from pathlib import Path
        import json

        catalog_path = Path(__file__).resolve().parent.parent.parent / "data" / "card_catalog.json"
        if catalog_path.exists():
            with open(catalog_path) as f:
                catalog = json.load(f)

            cards = []
            set_info = None

            for card in catalog.get('cards', []):
                if card.get('set_code') == set_code:
                    cards.append(card)
                    if not set_info:
                        set_name = card.get('set_name', '')
                        set_series = card.get('series', '')
                        set_release_date = card.get('releaseDate', '')

                        if not set_name:
                            tcgdex_cache_path = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "tcgdex" / "sets_en.json"
                            if tcgdex_cache_path.exists():
                                with open(tcgdex_cache_path) as tf:
                                    tcgdex_data = json.load(tf)
                                    for s in tcgdex_data.get('sets', []):
                                        if s.get('id') == set_code:
                                            set_name = s.get('name', set_code)
                                            set_series = s.get('series', '')
                                            set_release_date = s.get('releaseDate', '')
                                            break

                        set_info = {
                            'id': len(catalog.get('cards', [])),
                            'ptcg_id': set_code,
                            'name': set_name or set_code.upper(),
                            'series': set_series,
                            'release_date': set_release_date,
                            'total_cards': len(cards),
                            'printed_total': len(cards)
                        }

            if cards:
                def card_sort_key(card):
                    num = card.get('number', '')
                    import re
                    match = re.match(r'(\d+)', str(num))
                    if match:
                        return (0, int(match.group(1)), str(num))
                    else:
                        return (1, 0, str(num))

                cards.sort(key=card_sort_key)

                normalized_cards = []
                for card in cards:
                    ptcg_id = f"{card.get('set_code')}-{card.get('number')}"
                    image_url = "/static/images/placeholder.png"
                    try:
                        meta_path = Path(__file__).resolve().parent.parent.parent / "src" / "static" / "cards_meta.json"
                        if meta_path.exists():
                            with open(meta_path) as mf:
                                meta_data = json.load(mf)
                                for m in meta_data:
                                    if m.get('set') == card.get('set_code') and m.get('number') == card.get('number'):
                                        img = m.get('image', '')
                                        if img:
                                            image_url = img
                                        break
                    except:
                        pass

                    normalized_card = {
                        'ptcg_id': ptcg_id,
                        'name': card.get('name', ''),
                        'number': card.get('number', ''),
                        'rarity': card.get('rarity', ''),
                        'types': card.get('types', []),
                        'images': {
                            'small': image_url,
                            'large': image_url
                        }
                    }
                    normalized_cards.append(normalized_card)

                return {
                    "set": set_info,
                    "cards": normalized_cards,
                    "total": len(normalized_cards),
                    "source": "card_catalog"
                }
    except Exception as e:
        import logging
        logging.error(f"Card catalog fallback failed for set {set_code}: {e}")
        logging.exception(e)

    raise HTTPException(status_code=404, detail=f"Set '{set_code}' not found")


def _normalize_local_row_images(images, ptcg_id: str = ""):
    from services.collection import _normalize_images
    from pathlib import Path
    import json
    
    # Try original normalization first
    normalized = _normalize_images(images, ptcg_id)
    
    # If images is catalog format (language keys) or empty, try cards_meta.json
    if not normalized or (isinstance(normalized, dict) and all(not v for v in normalized.values()) if isinstance(normalized, dict) else False):
        # Check cards_meta.json for image
        try:
            meta_path = Path(__file__).resolve().parent.parent.parent / "src" / "static" / "cards_meta.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta_data = json.load(f)
                    # Find card in meta
                    for m in meta_data:
                        if m.get('set') and m.get('number'):
                            meta_ptcg = f"{m['set']}-{m['number']}"
                            if meta_ptcg == ptcg_id:
                                img = m.get('image', '')
                                if img:
                                    return {
                                        'small': img,
                                        'large': img
                                    }
                                    break
        except Exception:
            pass
    
    # If still empty, return placeholder
    if not normalized or (isinstance(normalized, dict) and not normalized.get('small') and not normalized.get('large')):
        if ptcg_id:
            # Check if it's a catalog-style images dict
            if isinstance(images, dict) and 'en' in images:
                # Catalog format - try to build from cards_meta
                try:
                    meta_path = Path(__file__).resolve().parent.parent.parent / "src" / "static" / "cards_meta.json"
                    if meta_path.exists():
                        with open(meta_path) as f:
                            meta_data = json.load(f)
                            for m in meta_data:
                                if m.get('set') and m.get('number'):
                                    meta_ptcg = f"{m['set']}-{m['number']}"
                                    if meta_ptcg == ptcg_id:
                                        img = m.get('image', '')
                                        if img:
                                            return {
                                                'small': img,
                                                'large': img
                                            }
                                        break
                except:
                    pass
        # Fallback to placeholder
        return {
            'small': '/static/images/placeholder.png',
            'large': '/static/images/placeholder.png'
        }
    
    return normalized or {}


def _normalize_prices_for_frontend(prices: dict) -> dict:
    """Convert TCGdex price format to pokemontcg format for frontend compatibility."""
    if not prices:
        return prices
    
    normalized = {"source": prices.get("source", "tcgdex")}
    
    # Normalize cardmarket
    cm = prices.get("cardmarket", {})
    if cm:
        if "trend" in cm or "avg30" in cm:
            # TCGdex format: flat fields
            normalized["cardmarket"] = {
                "prices": {
                    "trendPrice": cm.get("trend"),
                    "avg30": cm.get("avg30"),
                    "averageSellPrice": cm.get("avg"),
                    "lowPrice": cm.get("low"),
                }
            }
        else:
            # Already pokemontcg format
            normalized["cardmarket"] = cm
    
    # Normalize tcgplayer
    tcg = prices.get("tcgplayer", {})
    if tcg:
        if "holofoil" in tcg or "normal" in tcg:
            # TCGdex format: direct variant keys
            normalized["tcgplayer"] = {
                "prices": {
                    "normal": {
                        "market": tcg.get("normal", {}).get("marketPrice"),
                        "low": tcg.get("normal", {}).get("lowPrice"),
                        "high": tcg.get("normal", {}).get("highPrice"),
                        "mid": tcg.get("normal", {}).get("midPrice"),
                    } if "normal" in tcg else None,
                    "holofoil": {
                        "market": tcg.get("holofoil", {}).get("marketPrice"),
                        "low": tcg.get("holofoil", {}).get("lowPrice"),
                        "high": tcg.get("holofoil", {}).get("highPrice"),
                        "mid": tcg.get("holofoil", {}).get("midPrice"),
                    } if "holofoil" in tcg else None,
                    "reverseHolofoil": {
                        "market": tcg.get("reverse-holofoil", {}).get("marketPrice"),
                        "low": tcg.get("reverse-holofoil", {}).get("lowPrice"),
                        "high": tcg.get("reverse-holofoil", {}).get("highPrice"),
                        "mid": tcg.get("reverse-holofoil", {}).get("midPrice"),
                    } if "reverse-holofoil" in tcg else None,
                }
            }
        else:
            # Already pokemontcg format
            normalized["tcgplayer"] = tcg
    
    return normalized


def _resolve_tcgdex_image_url(image_url: str, ptcg_id: str = "") -> str:
    """Normalize a TCGdex image URL.

    The TCGdex API returns image URLs WITHOUT the file extension
    (e.g. 'https://assets.tcgdex.net/en/swsh/swsh10/046'), and that
    bare URL serves an HTML error page, not an image. Append the
    extension so the URL actually resolves to the image.
    """
    if not image_url:
        return ""
    if not image_url.startswith("http"):
        return image_url
    # Already has an image extension?
    if image_url.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "webp"):
        return image_url
    # Try to infer the extension from the card id (e.g. swsh10-046.png)
    if ptcg_id:
        return f"{image_url}.png"
    # Fall back to the common .png
    return f"{image_url}.png"


from services.clip_matcher import resolve_card_image_url

def _resolve_local_card_image(ptcg_id: str, img_url: str) -> str:
    """Prefer locally-served card images; fall back to resolve_card_image_url."""
    if ptcg_id:
        resolved = resolve_card_image_url(ptcg_id, img_url)
        if resolved:
            return resolved
    return img_url or ""


_DE_NAME_CACHE = None
_GERMAN_HIT_LIMIT = 400


def _german_card_names() -> dict:
    """Map de_<set>-<num> -> German card name.

    Every de_* row in the cards table keeps the English name (the prints share
    their artwork), so the German name printed on the card only exists in
    static/cards_meta.json. Loaded once and cached.
    """
    global _DE_NAME_CACHE
    if _DE_NAME_CACHE is not None:
        return _DE_NAME_CACHE
    names = {}
    try:
        import json
        from pathlib import Path

        meta_path = Path(__file__).resolve().parent.parent / "static" / "cards_meta.json"
        with open(meta_path, encoding="utf-8") as fh:
            for card in json.load(fh):
                if "/german_cards/" not in (card.get("image") or ""):
                    continue
                name = card.get("name") or ""
                if not (name and card.get("set") and card.get("number")):
                    continue
                names[f"de_{card['set']}-{card['number']}".lower()] = name
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("German card names unavailable: %s", exc)
        names = {}
    _DE_NAME_CACHE = names
    return names


def _shape_card_row(row, german_names) -> dict:
    """Normalise one `cards` row for the search response.

    Parses the images JSON, points any upstream image URL at our own copy, and
    swaps in the German print's name where the index has one.
    """
    import json

    d = dict(row)
    if d.get("images"):
        try:
            images = json.loads(d["images"]) if isinstance(d["images"], str) else d["images"]
        except Exception:
            images = {}
        # One resolver for every surface: it keeps stored local art, fills
        # small/large from language-keyed maps, and repairs remote URLs.
        from services.collection import _normalize_images

        d["images"] = _normalize_images(images, d.get("ptcg_id") or "")
    if german_names:
        german = german_names.get((d.get("ptcg_id") or "").lower())
        if german:
            d["name"] = german
    return d


# A search scans ~21k CLIP index entries, so the handler is a plain def and
# FastAPI runs it in the worker pool: it cannot stall the event loop. The lock
# serialises the cold load and the snapshot that follows it.
_search_index_lock = threading.Lock()


def _clip_index_card(card_id: str):
    """One CLIP index entry by id; German rows also answer to their bare id.

    Runs through load_index_fast(), the same loader the rest of the server uses,
    so a request never triggers the JSON+NPZ load. Callers run it in a thread.
    """
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    with _search_index_lock:
        if not matcher.load_index_fast():
            return None
        card = matcher.cards.get(card_id)
        if card is None and card_id.startswith("de_"):
            card = matcher.cards.get(card_id[3:])
        return card


@app.get("/api/cards/search")
def search_cards(query: str, limit: int = 20):
    """Search cards by name, set code, or number (public endpoint, no auth).
    Searches both the local DB and the full CLIP card index (~21k cards)."""
    q = query.strip().lower()
    name_q, number = split_name_number(q)
    results = []
    seen = set()
    de_names = _german_card_names()

    # 1) Database results
    conn = get_db_connection()
    cursor = conn.cursor()
    if number is None:
        search_param = f"%{q}%"
        where = "c.name LIKE ? OR c.number LIKE ? OR c.set_name LIKE ? OR c.ptcg_id LIKE ?"
        params = [search_param] * 4
    else:
        # The query names a printing, not only a name: the number is what
        # separates twenty Nebulak from the one on the table.
        where = "CAST(c.number AS INTEGER) = ?"
        params = [number]
        if name_q:
            where += " AND c.name LIKE ?"
            params.append(f"%{name_q}%")
    # A name can have many printings (Nebulak has 20) and a picker only asks
    # for a handful, so an exact name lists them all instead of a cut.
    fetch_limit = limit
    if number is None:
        cursor.execute("SELECT 1 FROM cards WHERE LOWER(name) = ? LIMIT 1", (q,))
        fetch_limit = 50 if cursor.fetchone() else limit
    cursor.execute(
        f"""
        SELECT c.id, c.ptcg_id, c.name, c.number, c.rarity, c.set_name, c.hp, c.types, c.images
        FROM cards c
        WHERE {where}
        ORDER BY c.name, c.set_name, c.number
        LIMIT ?
        """,
        (*params, fetch_limit)
    )
    for row in cursor.fetchall():
        d = _shape_card_row(row, de_names)
        if d.get("ptcg_id"):
            seen.add(d["ptcg_id"].lower())
        results.append(d)

    # German names are not in this table, so match them against the scanner's
    # index and fetch the matching rows in one query instead of one per hit.
    if number is None and q and de_names and len(results) < limit:
        # A cap on the IN list, not on results: plenty of index keys have no
        # cards row, so asking for exactly `limit` keys under-fills the page.
        german_hits = [
            key for key, name in de_names.items()
            if key not in seen and q in name.lower()
        ][:_GERMAN_HIT_LIMIT]
        if german_hits:
            placeholders = ",".join("?" * len(german_hits))
            cursor.execute(
                f"""
                SELECT c.id, c.ptcg_id, c.name, c.number, c.rarity, c.set_name, c.hp, c.types, c.images
                FROM cards c
                WHERE LOWER(c.ptcg_id) IN ({placeholders})
                """,
                german_hits,
            )
            for row in cursor.fetchall():
                if len(results) >= limit:
                    break
                d = _shape_card_row(row, de_names)
                seen.add(d["ptcg_id"].lower())
                results.append(d)
    conn.close()

    # 2) CLIP index results (covers cards not yet in the DB). An empty query
    # matches every index entry, so it would only cost the full scan without
    # adding anything the DB rows above do not already cover.
    if number is None and q and len(results) < limit:
        try:
            from services.clip_matcher import get_clip_matcher

            matcher = get_clip_matcher()
            with _search_index_lock:
                # Snapshot: a concurrent load refills matcher.cards, which would
                # make iterating the live dict raise.
                index_cards = list(matcher.cards.values()) if matcher.has_usable_index() else []
            for card in index_cards:
                if len(results) >= limit:
                    break
                if card.card_id.lower() in seen:
                    continue
                haystacks = [
                    (card.name or "").lower(),
                    (card.number or "").lower(),
                    (card.set_code or "").lower(),
                    (card.set_name or "").lower(),
                    card.card_id.lower(),
                ]
                if any(q in h for h in haystacks):
                    seen.add(card.card_id.lower())
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
    results = results[:fetch_limit]

    return {"results": results, "count": len(results)}


# ------------------------------------------------------------------ misreads
_MISREADS_DDL = """
CREATE TABLE IF NOT EXISTS misreads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_image_path TEXT,
    crop_image_path TEXT,
    wrong_id TEXT NOT NULL,
    wrong_name TEXT,
    correct_id TEXT NOT NULL,
    correct_name TEXT,
    score REAL,
    source TEXT,
    user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    review_status TEXT DEFAULT 'pending',
    reviewed_by INTEGER,
    reviewed_at TIMESTAMP,
    admin_note TEXT
)
"""


def _misreads_conn():
    conn = get_db_connection()
    conn.execute(_MISREADS_DDL)  # idempotent, covers DBs created before the table existed
    return conn


@app.post("/api/misreads")
async def log_misread(payload: Dict[str, Any], request: Request):
    """Record a wrong identification corrected by the user in the review queue.

    The crop image + (wrong_id -> correct_id) pair is hard-training data:
    re-embedding these crops or pinning confuser pairs directly improves the
    CLIP index. User is optional (the /scanner/ page works without login).
    """
    wrong_id = (payload.get("wrong_id") or "").strip()
    correct_id = (payload.get("correct_id") or "").strip()
    if not wrong_id or not correct_id:
        raise HTTPException(status_code=400, detail="wrong_id and correct_id are required")
    if wrong_id == correct_id:
        return {"status": "ignored", "reason": "same card"}

    def _valid_rel_path(p):
        # Only ever accept paths under data/scans/, never absolute or traversing
        if not p or not isinstance(p, str):
            return None
        p = p.strip()
        if p.startswith("/") or ".." in p or not p.startswith("scans/"):
            return None
        return p

    user_id = None
    try:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            claims = verify_token(auth[7:])
            user_id = claims.get("sub")
    except Exception:
        pass  # anonymous scanner sessions are fine — correction is still useful signal

    conn = _misreads_conn()
    try:
        cur = conn.execute(
            """INSERT INTO misreads
               (scan_image_path, crop_image_path, wrong_id, wrong_name,
                correct_id, correct_name, score, source, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _valid_rel_path(payload.get("scan_image_path")),
                _valid_rel_path(payload.get("crop_image_path")),
                wrong_id[:120], (payload.get("wrong_name") or "")[:200],
                correct_id[:120], (payload.get("correct_name") or "")[:200],
                float(payload.get("score") or 0), (payload.get("source") or "")[:20],
                user_id,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
    finally:
        conn.close()

    # The matcher reads data/confuser_pairs.json on every scan. Rebuild it off
    # the request path so this correction pins the pair without a manual run;
    # the scheduler folds rapid corrections into one rebuild.
    _schedule_confuser_rebuild()
    return {"status": "ok", "id": row_id}


@app.get("/api/misreads")
def list_misreads(limit: int = 100,
                        current_user: UserResponse = Depends(get_current_user)):
    """Admin view of corrected false detections (training candidates)."""
    conn = _misreads_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM misreads ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return {"misreads": [dict(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


# --------------------------------------------------- background identification
# A capture the matcher could not name is queued here. The worker reads the
# printed set code and number with OCR, then asks a vision LLM, and writes the
# outcome back; the review queue polls it so it can say when the attempts are
# used up instead of leaving the row looking busy.
@app.post("/api/identify/enqueue")
async def enqueue_identify(payload: Dict[str, Any],
                           current_user: UserResponse = Depends(get_current_user)):
    crop = ((payload or {}).get("crop_image_path")
            or _save_browser_image((payload or {}).get("crop_data_url"), "crop"))
    if not crop:
        raise HTTPException(status_code=400,
                            detail="crop_image_path or crop_data_url is required")
    job_id = identify_jobs.enqueue(db_path, crop,
                                   (payload or {}).get("scan_image_path"),
                                   (payload or {}).get("card_key"))
    return {"job_id": job_id, "max_attempts": identify_jobs.MAX_ATTEMPTS}


@app.get("/api/identify/jobs")
def identify_job_status(ids: str = "", limit: int = 50,
                              current_user: UserResponse = Depends(get_current_user)):
    wanted = [int(part) for part in ids.split(",") if part.strip().isdigit()] if ids else None
    return {"jobs": identify_jobs.statuses(db_path, wanted, max(1, min(limit, 200)))}


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    """Get a single card by ID (supports both ptcg_id like "me05-015" and DB PK)."""
    import sqlite3
    from pathlib import Path
    
    # Try to resolve as integer DB ID first
    try:
        db_id = int(card_id)
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (db_id,)).fetchone()
        conn.close()
        if row:
            card_dict = dict(row)
            import json as _json
            _imgs = card_dict.get("images")
            if isinstance(_imgs, str):
                try:
                    _imgs = _json.loads(_imgs)
                except Exception:
                    _imgs = {}
            _imgs = _normalize_local_row_images(_imgs, card_dict.get("ptcg_id") or "")
            return {
                "ptcg_id": card_dict.get("ptcg_id"),
                "name": card_dict.get("name"),
                "number": card_dict.get("number"),
                "set": {"code": "", "name": card_dict.get("set_name") or ""},
                "rarity": card_dict.get("rarity") or "",
                "types": card_dict.get("types") or [],
                "hp": card_dict.get("hp") or None,
                "images": _imgs or {},
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
    local = await asyncio.to_thread(_clip_index_card, card_id)
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
def create_or_get_card(card_id: str):
    """Find or create a card in the local database. Returns DB ID for collection."""
    try:
        # Schema is created once at startup (see the lifespan handler).
        
        collection = CollectionService()
        
        # Try to get card details from TCGdex API
        r = requests.get(_tcgdex_card_url(card_id), timeout=10)
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
        image_url = _resolve_tcgdex_image_url(card_data.get("image", ""), card_id)

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
        raise _fail("Error creating card", e)



@app.post("/api/scan/advanced")
async def scan_card_advanced(
    image: UploadFile = File(...),
    set_hint: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    include_prices: bool = Form(True),
):
    """Enhanced card scan with full details, prices, and structured output."""
    try:
        _enforce(_scan_limiter, "scan", "Too many scans in a row. Try again in a moment.")
        image_bytes = await image.read()
        if len(image_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty image file")
        _check_upload(image_bytes)

        client = get_client()
        # Identification is seconds of CPU (CLIP/quickdraw); off the loop it
        # cannot stall health checks or other requests while it runs.
        result = await asyncio.to_thread(
            client.identify_card,
            image_bytes=image_bytes,
            top_k=5,
            set_hint=set_hint,
            region=region,
        )

        if result.get("candidates"):
            candidate_ids = [c["id"] for c in result["candidates"]]
            cards = await asyncio.to_thread(client.get_cards_batch, candidate_ids)
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
        raise _fail("Advanced scan error", e)



# ============================================================
# CARD METADATA CACHE (SQLite-backed)
# ============================================================
_CACHE_TTL_HOURS = 24


def _cache_get(card_id: str) -> Optional[Dict[str, Any]]:
    """Get cached card metadata from SQLite."""
    try:
        import sqlite3 as _sqlite3
        if not db_path.exists():
            return None
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM card_metadata WHERE card_id = ?",
            (card_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            fetched_at = row["fetched_at"]
            if fetched_at:
                try:
                    from datetime import datetime, timedelta, timezone
                    fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) - fetched_dt > timedelta(hours=_CACHE_TTL_HOURS):
                        return None  # Stale
                except Exception:
                    pass
            import json as _json
            return {
                "card_id": row["card_id"],
                "name": row["name"],
                "set_name": row["set_name"],
                "set_code": row["set_code"],
                "rarity": row["rarity"],
                "hp": row["hp"],
                "types": _json.loads(row["types"]) if row["types"] else [],
                "artist": row["artist"],
                "legalities": _json.loads(row["legalities"]) if row["legalities"] else {},
                "tcgdex_data": _json.loads(row["tcgdex_data"]) if row["tcgdex_data"] else {},
            }
    except Exception as e:
        logging.debug(f"Cache get failed for {card_id}: {e}")
    return None


def _cache_set(card_id: str, data: Dict[str, Any]):
    """Store card metadata in SQLite cache."""
    try:
        import sqlite3 as _sqlite3
        if not db_path.exists():
            return
        conn = get_db_connection()
        cursor = conn.cursor()
        import json as _json
        from datetime import datetime
        cursor.execute(
            """INSERT OR REPLACE INTO card_metadata
               (card_id, name, set_name, set_code, rarity, hp, types, artist, legalities, tcgdex_data, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id,
                data.get("name"),
                data.get("set_name"),
                data.get("set_code"),
                data.get("rarity"),
                data.get("hp"),
                _json.dumps(data.get("types", [])),
                data.get("artist"),
                _json.dumps(data.get("legalities", {})),
                _json.dumps(data.get("tcgdex_data", {})),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.debug(f"Cache set failed for {card_id}: {e}")


def _tcgdex_card_url(card_id: str) -> str:
    """TCGdex address for one card, in the printing's own language.

    TCGdex files printings per language and takes no "de_" prefix: the
    app's de_me04-010 lives at /v2/de/cards/me04-010, and the /v2/en path
    for it answers 404.
    """
    lang = "de" if card_id.startswith("de_") else "en"
    return f"https://api.tcgdex.net/v2/{lang}/cards/{card_id.removeprefix('de_')}"


def _fetch_card_metadata_from_tcgdex(card_id: str) -> Optional[Dict[str, Any]]:
    """Fetch and cache card metadata from TCGdex API."""
    try:
        import requests
        resp = requests.get(_tcgdex_card_url(card_id), timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            metadata = {
                "name": d.get("name"),
                "set_name": d.get("set", {}).get("name"),
                "set_code": d.get("set", {}).get("id"),
                "rarity": d.get("rarity"),
                "hp": d.get("hp"),
                "types": d.get("types", []),
                "artist": d.get("illustrator"),
                "legalities": d.get("legalities", {}),
                "tcgdex_data": d,
            }
            _cache_set(card_id, metadata)
            return metadata
    except Exception as e:
        logging.debug(f"TCGdex fetch failed for {card_id}: {e}")
    return None


def _local_card_details(row, fallback_id: str = "") -> Dict[str, Any]:
    """Build the /details payload for a card row from the local DB.

    Shared by the ptcg_id and numeric-PK branches of get_card_details, which
    previously carried byte-identical copies of this block.
    """
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

    ptcg_id = row["ptcg_id"] or ""
    row_key = ptcg_id or fallback_id
    details = {
        "basic_info": {
            "id": str(row["id"]),
            "name": row["name"],
            "number": row["number"],
            "rarity": row["rarity"] or None,
            "hp": row["hp"] or None,
            "types": types,
            "artist": None,
            "set": {
                "name": row["set_display_name"] or row["set_name"] or "",
                "code": row["set_ptcg_id"] or (ptcg_id.split("-")[0] if "-" in ptcg_id else ""),
            },
            "images": _normalize_local_row_images(images, row_key),
        },
        "legalities": None,
        "prices": None,
        "source": "local_db",
    }
    # Fetch prices (cached; falls back to stale cache on API failure)
    try:
        from services.price_cache import get_prices_cached
        prices = get_prices_cached(ptcg_id)
        if prices:
            details["prices"] = _normalize_prices_for_frontend(prices)
    except Exception as e:
        logger.debug(f"Price fetch skipped for local card {ptcg_id}: {e}")
    return details


@app.get("/api/cards/{card_id}/details")
async def get_card_details(card_id: str):
    """Get detailed card information including evolution chain and legalities."""
    # Quickdraw pattern: resolve local first (DB → cards_meta.json → CLIP) before API.
    # Check local DB by ptcg_id first — covers Pocket, promos, German, etc.
    if not card_id.isdigit():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT c.id, c.ptcg_id, c.name, c.number, c.rarity, c.set_name, c.hp, c.types, c.images, "
                "s.name as set_display_name, s.ptcg_id as set_ptcg_id "
                "FROM cards c LEFT JOIN sets s ON c.set_id = s.id OR (c.set_id IS NULL AND c.set_name = s.ptcg_id) "
                "WHERE c.ptcg_id = ?", (card_id,)
            ).fetchone()
            conn.close()
            if row:
                return _local_card_details(row, card_id)
        except Exception:
            pass

    # If the ID is numeric, treat it as a local DB PK and resolve to ptcg_id
    if card_id.isdigit():
        import sqlite3
        from pathlib import Path
        try:
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT c.id, c.ptcg_id, c.name, c.number, c.rarity, c.set_name, c.hp, c.types, c.images, "
                "s.name as set_display_name, s.ptcg_id as set_ptcg_id "
                "FROM cards c LEFT JOIN sets s ON c.set_id = s.id OR (c.set_id IS NULL AND c.set_name = s.ptcg_id) "
                "WHERE c.id = ?", (int(card_id),)
            ).fetchone()
            conn.close()
            if row:
                if row["ptcg_id"]:
                    # Continue with the real ptcg_id below (API/index lookup)
                    card_id = row["ptcg_id"]
                else:
                    return _local_card_details(row, card_id)
        except Exception:
            pass
    client = get_client()
    card = client.get_card(card_id)
    
    # If found in local DB but missing metadata, try cache then TCGdex API
    if card and card.get("source") == "local_db":
        # Check cache first
        cached = _cache_get(card_id)
        if cached:
            card["set"] = cached.get("set_name") and {"name": cached["set_name"]} or card.get("set")
            card["rarity"] = cached.get("rarity") or card.get("rarity")
            card["types"] = cached.get("types") or card.get("types")
            card["hp"] = cached.get("hp") or card.get("hp")
            card["artist"] = cached.get("artist") or card.get("artist")
            card["legalities"] = cached.get("legalities") or card.get("legalities")
        else:
            # Fetch from TCGdex and cache
            metadata = _fetch_card_metadata_from_tcgdex(card_id)
            if metadata:
                card["set"] = metadata.get("set_name") and {"name": metadata["set_name"]} or card.get("set")
                card["rarity"] = metadata.get("rarity") or card.get("rarity")
                card["types"] = metadata.get("types") or card.get("types")
                card["hp"] = metadata.get("hp") or card.get("hp")
                card["artist"] = metadata.get("artist") or card.get("artist")
                card["legalities"] = metadata.get("legalities") or card.get("legalities")
        
        # Fetch prices for local_db cards (normalize them)
        try:
            from services.price_cache import get_prices_cached
            prices = get_prices_cached(card_id)
            if prices:
                card["prices"] = _normalize_prices_for_frontend(prices)
        except Exception as e:
            logger.debug(f"Price fetch skipped for local DB card {card_id}: {e}")
    
    if not card:
        # Fall back to the local CLIP index (covers German 'de_*' cards and
        # any card the TCGdex API lookup misses)
        # Index stores German cards under their bare ID (e.g. 'bw5-1') while
        # image URLs use the 'de_' prefix; _clip_index_card handles that.
        local = await asyncio.to_thread(_clip_index_card, card_id)
        if local:
            details = {
                "basic_info": {
                    "id": local.card_id,
                    "name": local.name,
                    "number": local.number,
                    "rarity": local.rarity or None,
                    "hp": local.hp if getattr(local, "hp", None) else None,
                    "types": getattr(local, "types", None) or None,
                    "artist": getattr(local, "artist", None) or None,
                    "set": {"name": local.set_name or local.set_code, "code": local.set_code},
                    "images": {"small": _resolve_local_card_image(local.card_id, local.image_url or ""),
                               "large": _resolve_local_card_image(local.card_id, local.image_url or "")},
                },
                "legalities": None,
                "prices": None,
                "source": "local_index",
            }
            # Try cache first, then TCGdex for enrichment
            cached = _cache_get(local.card_id)
            if cached:
                if not details["basic_info"]["set"]["name"] or details["basic_info"]["set"]["name"] == local.set_code:
                    details["basic_info"]["set"]["name"] = cached.get("set_name") or details["basic_info"]["set"]["name"]
                if not details["basic_info"]["rarity"]:
                    details["basic_info"]["rarity"] = cached.get("rarity")
                if not details["basic_info"]["types"]:
                    details["basic_info"]["types"] = cached.get("types")
                if not details["basic_info"]["hp"]:
                    details["basic_info"]["hp"] = cached.get("hp")
                if not details["basic_info"]["artist"]:
                    details["basic_info"]["artist"] = cached.get("artist")
            else:
                # Fetch from TCGdex and cache
                metadata = _fetch_card_metadata_from_tcgdex(local.card_id)
                if metadata:
                    if not details["basic_info"]["set"]["name"] or details["basic_info"]["set"]["name"] == local.set_code:
                        details["basic_info"]["set"]["name"] = metadata.get("set_name") or details["basic_info"]["set"]["name"]
                    if not details["basic_info"]["rarity"]:
                        details["basic_info"]["rarity"] = metadata.get("rarity")
                    if not details["basic_info"]["types"]:
                        details["basic_info"]["types"] = metadata.get("types")
                    if not details["basic_info"]["hp"]:
                        details["basic_info"]["hp"] = metadata.get("hp")
                    if not details["basic_info"]["artist"]:
                        details["basic_info"]["artist"] = metadata.get("artist")
            # Fetch prices (cached; falls back to stale cache on API failure)
            try:
                from services.price_cache import get_prices_cached
                prices = get_prices_cached(local.card_id)
                if prices:
                    details["prices"] = _normalize_prices_for_frontend(prices)
            except Exception as e:
                logger.debug(f"Price fetch skipped for local_index card {local.card_id}: {e}")
            return details
        raise HTTPException(status_code=404, detail="Card not found")
    
    # Add structured details
    details = {
        "basic_info": {
            "ptcg_id": card.get("ptcg_id") or card_id,
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
    
    # Normalize prices to pokemontcg format for frontend compatibility
    if details.get("prices"):
        details["prices"] = _normalize_prices_for_frontend(details["prices"])

    # Ensure images is parsed from string if needed
    if isinstance(details["basic_info"]["images"], str):
        try:
            import json
            details["basic_info"]["images"] = json.loads(details["basic_info"]["images"])
        except Exception:
            pass
    from services.collection import _normalize_images
    _bi = details["basic_info"]
    _pid = _bi.get("ptcg_id") or ""
    _bi_imgs = _normalize_images(_bi.get("images"), _pid)
    if isinstance(_bi_imgs, dict):
        for _k in ("small", "large", "hd"):
            if _bi_imgs.get(_k):
                _bi_imgs[_k] = _resolve_local_card_image(_pid, _bi_imgs[_k])
    _bi["images"] = _bi_imgs

    # Prime the cache when the card lookup itself carried prices
    if details.get("prices"):
        try:
            from services.price_cache import prime_cache
            prime_cache(card_id, details["prices"])
        except Exception:
            pass

    # Fetch prices if not already present (cached; stale-cache fallback on failure)
    if not details.get("prices"):
        try:
            from services.price_cache import get_prices_cached
            prices = get_prices_cached(card_id)
            if prices:
                details["prices"] = prices
        except Exception as e:
            logger.debug(f"Price fetch skipped for {card_id}: {e}")

    return details


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the app icon to avoid 404 noise in the browser console."""
    icon = os.path.join(static_dir, "images", "icon.png")
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
    status_info = await asyncio.to_thread(matcher.status)

    return {
        "status": "ok",
        "service": "Pokemon Card Scanner",
        "offline_matcher": {
            "available": status_info["usable"],
            "cards_indexed": status_info["indexed"],
            "loading": status_info["loading"],
            "index_error": status_info.get("index_error"),
            "index_drift": status_info.get("index_drift"),
            "device": status_info["device"],
        },
        "api_key_configured": bool(os.environ.get("PTCG_API_KEY", "")),
    }


@app.post("/api/index/build")
async def build_index(max_cards: int = 500,
                      current_user: UserResponse = Depends(get_current_user)):
    """Trigger background build of the CLIP card index."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    ok = matcher.build_index(max_cards=max_cards, in_background=True)
    return {"status": "started" if ok else "already_running"}


@app.get("/api/quickdraw/status")
async def quickdraw_status():
    """Quickdraw fingerprint index status."""
    from services.quickdraw_matcher import get_quickdraw_matcher

    matcher = get_quickdraw_matcher()
    await asyncio.to_thread(matcher.load_index)
    return {
        "matcher": "quickdraw",
        "index_available": matcher.index_path.exists(),
        "loaded": matcher.status()["available"],
        "cards_indexed": matcher.status()["cards_indexed"],
    }


@app.post("/api/quickdraw/build")
async def quickdraw_build(current_user: UserResponse = Depends(get_current_user)):
    """Build the Quickdraw fingerprint index (background thread)."""
    from services.quickdraw_matcher import get_quickdraw_matcher

    matcher = get_quickdraw_matcher()
    started = matcher.build_in_background()
    return {"status": "started" if started else "already_running_or_loaded"}


@app.post("/api/quickdraw/match")
async def quickdraw_match(image: UploadFile = File(...), top_k: int = Query(5),
                          limit: float = Query(0.20)):
    """Direct Quickdraw-style fingerprint match — pure CPU, no model, ~10-50ms.

    `limit` is the cutoff the client's presence gate applies. A crop it refuses
    (no candidate, or a top distance beyond `limit`) is saved under
    data/scans/refused/ and returned as `refused_crop`, so the next failure
    leaves both the distance and the image behind.
    """
    from services.quickdraw_matcher import get_quickdraw_matcher

    _enforce(_scan_limiter, "scan", "Too many scans in a row. Try again in a moment.")
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")
    _check_upload(image_bytes)

    matcher = get_quickdraw_matcher()
    if not await asyncio.to_thread(matcher.load_index):
        raise HTTPException(status_code=503, detail="Quickdraw index not built — POST /api/quickdraw/build")

    result = await asyncio.to_thread(matcher.recognize, image_bytes, top_k)
    top = (result.get("candidates") or [{}])[0]
    distance = top.get("distance")
    payload = dict(result)
    # The client's presence gate calls this on every burst it cannot confirm, so
    # record what it was told and keep the image it judged: a refusal used to
    # leave no trace beyond a log line.
    refused = distance is None or float(distance) > limit
    if refused:
        saved = await asyncio.to_thread(save_refused_crop, image_bytes)
        payload["refused_crop"] = saved
        logger.info("quickdraw match: REFUSED decision=%s bytes=%d top=%s "
                    "distance=%s limit=%.2f crop=%s",
                    result.get("decision"), len(image_bytes), top.get("id"),
                    distance, limit, saved)
    else:
        logger.info("quickdraw match: decision=%s bytes=%d top=%s distance=%s",
                    result.get("decision"), len(image_bytes), top.get("id"),
                    distance)
    return JSONResponse(payload)


@app.get("/api/index/status")
async def index_status():
    """Get CLIP index build progress."""
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    return await asyncio.to_thread(matcher.status)


@app.post("/api/index/enrich")
async def enrich_metadata(current_user: UserResponse = Depends(get_current_user)):
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
async def build_german_index(max_cards: int = 2500,
                             current_user: UserResponse = Depends(get_current_user)):
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
        _enforce(_scan_limiter, "scan", "Too many scans in a row. Try again in a moment.")
        image_bytes = await image.read()
        _check_upload(image_bytes)

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

    except HTTPException:
        raise  # 413/429 from the guards above are answers, not failures
    except Exception as e:
        raise _fail("Detection error", e)


# ============================================================
# Authentication Endpoints
# ============================================================

@app.post("/api/auth/register")
# Sync on purpose: this route is nothing but SQLite calls, and FastAPI runs a
# sync endpoint in its thread pool, so waiting out a lock holds a thread instead
# of the event loop that every other request in this worker needs.
def register_user(user_data: UserRegister):
    """Register a new user account."""
    _enforce(_register_limiter, user_data.email.strip().lower(),
             "Too many sign-up attempts. Try again later.")
    auth_service = AuthService()
    try:
        user = auth_service.register(user_data)
        response = JSONResponse(content={
            "access_token": create_access_token({"sub": user.id}),
            "token_type": "bearer",
            "expiration_minutes": 30,
            "user": user.model_dump(mode="json"),
        })
        # The account and its access token already exist at this point: a failed
        # refresh cookie (older database, locked file) must not turn a completed
        # registration into "Internal server error" — the session simply lasts
        # only as long as this page.
        try:
            set_refresh_cookie(response, create_refresh_token(user.id))
        except Exception as e:
            logger.error("Registration: refresh token could not be issued: %s", e, exc_info=True)
        return response
    except HTTPException:
        raise
    except sqlite3.IntegrityError as e:
        # Two sign-ups racing for the same username/email: the UNIQUE index is
        # the real answer, so report the conflict instead of a server error.
        logger.warning("Registration conflict for %s: %s", user_data.username, e)
        raise HTTPException(status_code=409, detail="Username or email already exists")
    except Exception as e:
        raise _fail("Registration error", e)


@app.post("/api/auth/login")
def login_user(login_data: UserLogin):
    """Login and receive access token + httpOnly refresh cookie."""
    _enforce(_login_limiter, login_data.username.strip().lower(),
             "Too many login attempts. Wait a minute and try again.")
    auth_service = AuthService()
    try:
        result = auth_service.login(login_data)
        response = JSONResponse(content={
            "access_token": result["access_token"],
            "token_type": result["token_type"],
            "expiration_minutes": 30,
            "user": result["user"].model_dump(mode="json"),
        })
        try:
            set_refresh_cookie(response, create_refresh_token(result["user"].id))
        except Exception as e:
            logger.error("Login: refresh token could not be issued: %s", e, exc_info=True)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise _fail("Login error", e)


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
async def get_me(current_user: UserResponse = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        preferred_language=current_user.preferred_language,
        is_admin=current_user.is_admin,
        created_at=current_user.created_at
    )


ALLOWED_LANGUAGES = {"EN", "DE", "FR", "IT", "ES", "JA", "ZH"}


@app.patch("/api/auth/me/language", response_model=UserResponse)
async def set_my_language(
    profile: UserProfileUpdate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Update the current user's preferred card language."""
    lang = (profile.preferred_language or "").upper()
    if lang not in ALLOWED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{profile.preferred_language}'. Allowed: {', '.join(sorted(ALLOWED_LANGUAGES))}",
        )
    updated = AuthService().set_preferred_language(current_user.id, lang)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


# ============================================================
# Admin Endpoints (accounts with the is_admin flag only)
# ============================================================

def get_current_admin(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    """Gate every /api/admin route on the account's admin flag."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _schedule_confuser_rebuild() -> None:
    """Rebuild the confuser index after the training data changed.

    Coalesced: a burst of admin edits produces one rebuild, not one per edit.
    """
    try:
        from services.misread_training import schedule_confuser_rebuild
        schedule_confuser_rebuild()
    except Exception as exc:
        logger.warning("confuser rebuild not started: %s", exc)


@app.get("/api/admin/users")
def admin_list_users(admin: UserResponse = Depends(get_current_admin)):
    """All accounts with their collection size, for the admin panel."""
    return {"users": AuthService().list_users()}


@app.get("/api/admin/stats")
def admin_get_stats(admin: UserResponse = Depends(get_current_admin)):
    """Database and usage counters shown above the user list."""
    return AuthService().admin_stats()


@app.post("/api/admin/users/{user_id}/password")
async def admin_set_password(
    user_id: int,
    payload: AdminPasswordSet,
    admin: UserResponse = Depends(get_current_admin),
):
    """Set another account's password (and sign its sessions out)."""
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not AuthService().set_user_password(user_id, payload.new_password):
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("Admin %s (id %s) reset the password of user id %s", admin.username, admin.id, user_id)
    return {"success": True, "detail": "Password updated. Their saved login was revoked; an open tab keeps access until its token expires."}


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    payload: AdminUserUpdate,
    admin: UserResponse = Depends(get_current_admin),
):
    """Grant or revoke an account's admin rights."""
    auth = AuthService()
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.is_admin is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if user_id == admin.id and not payload.is_admin:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin rights")
    if target.is_admin and not payload.is_admin and auth.count_admins() <= 1:
        raise HTTPException(status_code=400, detail="At least one admin account is required")
    if not auth.set_user_admin(user_id, payload.is_admin):
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("Admin %s (id %s) set admin=%s for user id %s",
                admin.username, admin.id, payload.is_admin, user_id)
    return {"success": True, "user": auth.get_user(user_id).model_dump(mode="json")}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    admin: UserResponse = Depends(get_current_admin),
):
    """Delete an account together with its collection, binders and sessions."""
    auth = AuthService()
    target = auth.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if target.is_admin and auth.count_admins() <= 1:
        raise HTTPException(status_code=400, detail="At least one admin account is required")
    if not auth.delete_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    logger.warning("Admin %s (id %s) deleted user %s (id %s)",
                   admin.username, admin.id, target.username, user_id)
    return {"success": True}


@app.get("/api/admin/database")
async def admin_database_stats(
    force: bool = False,
    admin: UserResponse = Depends(get_current_admin),
):
    """Card/price/image/correction counters for the database section.

    ``force=true`` re-scans card images instead of reusing the 60s cache.
    """
    from services import db_stats
    return await asyncio.to_thread(db_stats.database_stats, force)


@app.get("/api/admin/corrections")
async def admin_list_corrections(
    status: str = "all",
    limit: int = 50,
    offset: int = 0,
    admin: UserResponse = Depends(get_current_admin),
):
    """User-submitted corrections, newest first, with vetting state per row."""
    from services import corrections as corrections_service
    return await asyncio.to_thread(
        corrections_service.list_corrections, status, limit, offset
    )


@app.patch("/api/admin/corrections/{correction_id}")
async def admin_update_correction(
    correction_id: int,
    payload: AdminCorrectionUpdate,
    admin: UserResponse = Depends(get_current_admin),
):
    """Confirm/reject a correction, or re-point it at the right card."""
    from services import corrections as corrections_service

    correct_id = payload.correct_id
    if correct_id is not None:
        correct_id = correct_id.strip()
        if not correct_id or not corrections_service.card_exists(correct_id):
            raise HTTPException(status_code=400, detail=f"Unknown card id '{payload.correct_id}'")

    try:
        result = corrections_service.update_correction(
            correction_id,
            review_status=payload.review_status,
            correct_id=correct_id,
            correct_name=payload.correct_name,
            admin_note=payload.admin_note,
            reviewed_by=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result is None:
        raise HTTPException(status_code=404, detail="Correction not found or nothing to update")

    updated, training_changed = result
    logger.info(
        "Admin %s (id %s) vetted correction %s: status=%s correct_id=%s%s",
        admin.username, admin.id, correction_id,
        payload.review_status, updated.get("correct_id"),
        "" if training_changed else " (no training change, no rebuild)",
    )
    # The matcher reads confuser_pairs.json, and a pair is built from the review
    # status and the corrected card. Only those edits can change the file, so a
    # note edit must not start a rebuild.
    if training_changed:
        _schedule_confuser_rebuild()
    return {
        "success": True,
        "correction": updated,
        "counts": corrections_service.correction_counts(),
    }


@app.delete("/api/admin/corrections/{correction_id}")
async def admin_delete_correction(
    correction_id: int,
    admin: UserResponse = Depends(get_current_admin),
):
    """Drop a correction that is not training-worthy (wrong card, junk crop)."""
    from services import corrections as corrections_service

    if not corrections_service.delete_correction(correction_id):
        raise HTTPException(status_code=404, detail="Correction not found")
    logger.warning("Admin %s (id %s) deleted correction %s",
                   admin.username, admin.id, correction_id)
    _schedule_confuser_rebuild()
    return {"success": True, "counts": corrections_service.correction_counts()}


@app.get("/api/admin/cards/search")
def admin_search_cards(
    q: str = "",
    limit: int = 20,
    admin: UserResponse = Depends(get_current_admin),
):
    """Find a card by name or id, for correcting a misread."""
    from services import corrections as corrections_service

    results = corrections_service.search_cards(q, limit=limit)
    for card in results:
        try:
            payload = json.loads(card.get("images") or "{}") or {}
        except (TypeError, ValueError):
            payload = {}
        card["image"] = payload.get("small") or payload.get("large") or ""
        card.pop("images", None)
    return {"results": results, "count": len(results)}


# Global exception handlers to prevent HTML error pages
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    # Headers such as Retry-After on a 503 are part of the answer, so pass them
    # through instead of dropping them here.
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error": "HTTP Exception"},
        headers=getattr(exc, "headers", None)
    )


@app.get("/api/health")
async def health_check():
    """Health check endpoint - shows server readiness including model status."""
    from services.clip_matcher import get_clip_matcher
    from services.card_detector import get_detector
    matcher = get_clip_matcher()
    clip = await asyncio.to_thread(matcher.status)  # same source as /api/status
    try:
        detector = get_detector()
        detector_status = "ready" if detector else "not_loaded"
    except Exception as e:
        detector_status = f"error: {e}"

    return {
        "status": "healthy",
        "detector": detector_status,
        "clip_matcher": {
            "clip_ready": clip["usable"],
            "cards_indexed": clip["indexed"],
        },
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    # exc.errors() can carry bytes (raw input slices) -> plain json.dumps raises,
    # and an exception inside this handler takes the whole server down.
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "error": "Validation Error"}
    )


# The words SQLite uses when the lock it wanted was held by another connection.
_BUSY_SIGNS = ("database is locked", "database table is locked",
               "database schema is locked", "database is busy")


def _is_busy_db(exc: BaseException) -> bool:
    """Was this a write that waited out SQLite's busy timeout?"""
    return isinstance(exc, sqlite3.OperationalError) and any(
        sign in str(exc).lower() for sign in _BUSY_SIGNS)


def _fail(context: str, exc: Exception) -> HTTPException:
    """Log a failure and turn it into the answer the client should get.

    A lock is contention, not a bug: the same request usually succeeds a second
    later, so it answers 503 with Retry-After and reads as a warning in the log.
    Anything else is a real failure: it keeps its traceback and answers 500.
    """
    if _is_busy_db(exc):
        logger.warning("%s: database busy, answering 503: %s", context, exc)
        return HTTPException(
            status_code=503, detail="Database is busy, retry shortly",
            headers={"Retry-After": "2"})
    logger.error("%s: %s", context, exc, exc_info=True)
    return HTTPException(status_code=500, detail="Internal server error")


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_busy_handler(request, exc):
    """Same answer for the routes that let the lock escape unwrapped."""
    if not _is_busy_db(exc):
        return await general_exception_handler(request, exc)
    return await http_exception_handler(
        request, _fail(f"{request.method} {request.url.path}", exc))


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": "Internal Server Error"}
    )

@app.post("/api/auth/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: UserResponse = Depends(get_current_user),
):
    """Change the current user's password.

    Validates the current password, then updates to the new password.
    Returns success/error states.
    """
    auth_service = AuthService()
    ok = auth_service.change_password(current_user.id, current_password, new_password)
    if ok:
        return {"success": True, "detail": "Password updated successfully"}
    return {"success": False, "detail": "Invalid current password or new password too short (< 8 chars)"}


@app.post("/api/auth/reset-password")
async def reset_password(
    email: str = Form(...),
    new_password: str = Form(...),
):
    """Disabled: unauthenticated email-based reset allowed account takeover.

    Anyone who knew an email address could set that account's password, so the
    public flow is closed. Resets are performed server-side by the operator via
    AuthService.reset_password_by_email; a self-service flow would need a
    single-use token delivered out of band (email), which this app does not have.
    """
    raise HTTPException(
        status_code=403,
        detail="Password reset by email is disabled. Ask the server owner to reset it for you.",
    )
