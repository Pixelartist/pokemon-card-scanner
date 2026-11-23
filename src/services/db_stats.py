"""Database statistics for the admin panel.

Two of the questions the panel asks cannot be answered by SQL alone:

* whether a card's image actually exists — the ``images`` column is a JSON blob
  written by the index build, and a row can point at a file that was never
  fetched or has been deleted;
* whether a card's cached price is usable — ``card_prices`` stores failed
  lookups as an empty object so a missing price is not retried constantly.

The filesystem side is done by listing each image directory once (not one stat
per card, which is slow over the network share) and caching that listing. Even
so, walking ~56k rows over a network share takes tens of seconds, so the scan
runs in a background thread: the endpoint answers immediately with the last
result (or ``cards_without_image: null`` while the first scan is in flight) and
the admin panel fills the number in when it lands.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from api.models.database import DATABASE_URL, get_db_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))

# URL prefix -> directories the app serves that prefix from. Mirrors the
# StaticFiles mounts in api.main; the second candidate covers a deployment whose
# DATA_DIR moved away from <project>/data.
_IMAGE_MOUNTS = {
    "/static/german_cards/images/": [
        PROJECT_ROOT / "data" / "german_cards" / "images",
        DATA_ROOT / "german_cards" / "images",
    ],
    "/static/clip_images/": [
        PROJECT_ROOT / "data" / "clip_images",
        DATA_ROOT / "clip_images",
    ],
    "/static/card_images/": [
        PROJECT_ROOT / "data" / "images",
        DATA_ROOT / "images",
    ],
    "/card_images/": [
        PROJECT_ROOT / "data" / "images",
        DATA_ROOT / "images",
    ],
    "/static/pocket/": [DATA_ROOT / "images" / "pocket"],
    "/static/tcgdex/": [DATA_ROOT / "images" / "tcgdex"],
    "/static/images/": [PROJECT_ROOT / "src" / "static" / "images"],
}

_LISTING_TTL = 1800          # seconds a directory listing is reused
_LISTING_CACHE: Dict[str, tuple] = {}

_IMAGE_SCAN_TTL = 300        # seconds the image scan result is reused
_IMAGE_SCAN_CACHE: Dict[str, Any] = {
    "at": 0.0, "missing": None, "total": 0, "examples": [], "running": False,
}
_SCAN_LOCK = threading.Lock()


def _listing(directory: Path) -> set:
    key = str(directory)
    now = time.monotonic()
    cached = _LISTING_CACHE.get(key)
    if cached and now - cached[0] < _LISTING_TTL:
        return cached[1]
    try:
        files = set(os.listdir(directory))
    except OSError:
        files = set()
    _LISTING_CACHE[key] = (now, files)
    return files


def _image_exists(url: str) -> bool:
    """Is the image a card row points at actually served by this app?"""
    if not url:
        return False
    if url.startswith(("http://", "https://")):
        return True  # remote art cannot be checked cheaply; do not call it missing
    rel = url.split("?", 1)[0]
    for prefix, directories in _IMAGE_MOUNTS.items():
        if not rel.startswith(prefix):
            continue
        name = rel[len(prefix):]
        if not name:
            return False
        if "/" in name:  # nested path: one stat is fine, there are few of them
            return any((d / name).is_file() for d in directories)
        return any(name in _listing(d) for d in directories)
    return False


def _run_image_scan() -> None:
    """Count cards whose image is empty or points at a file that is not there."""
    total = 0
    missing = 0
    examples: List[Dict[str, Any]] = []
    conn = get_db_connection()
    try:
        for ptcg_id, name, images in conn.execute(
            "SELECT ptcg_id, name, images FROM cards"
        ):
            total += 1
            try:
                payload = json.loads(images or "{}") or {}
            except (TypeError, ValueError):
                payload = {}
            url = payload.get("large") or payload.get("small") or ""
            if _image_exists(url):
                continue
            missing += 1
            if len(examples) < 12:
                examples.append({"ptcg_id": ptcg_id, "name": name, "images": url})
    finally:
        conn.close()

    with _SCAN_LOCK:
        _IMAGE_SCAN_CACHE.update(
            {"at": time.monotonic(), "missing": missing,
             "total": total, "examples": examples, "running": False}
        )


def _schedule_image_scan(force: bool = False) -> None:
    """Start the (slow) filesystem scan off the request path if it is stale."""
    with _SCAN_LOCK:
        if _IMAGE_SCAN_CACHE["running"]:
            return
        fresh = (
            _IMAGE_SCAN_CACHE["at"]
            and time.monotonic() - _IMAGE_SCAN_CACHE["at"] < _IMAGE_SCAN_TTL
        )
        if fresh and not force:
            return
        _IMAGE_SCAN_CACHE["running"] = True

    def _worker():
        try:
            _run_image_scan()
        except Exception as exc:  # never let a background scan die silently
            logger.warning("image scan failed: %s", exc)
            with _SCAN_LOCK:
                _IMAGE_SCAN_CACHE["running"] = False

    threading.Thread(target=_worker, daemon=True, name="image-scan").start()


def _count(conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def database_stats(force_image_scan: bool = False) -> Dict[str, Any]:
    """Counters for the admin panel's database section."""
    conn = get_db_connection()
    try:
        cards_total = _count(conn, "SELECT COUNT(*) FROM cards")
        cards_without_price = _count(
            conn,
            """
            SELECT COUNT(*) FROM cards c
            LEFT JOIN card_prices p ON p.card_id = c.ptcg_id
            WHERE p.card_id IS NULL
               OR p.prices IS NULL
               OR TRIM(p.prices) IN ('', '{}', 'null', '[]')
            """,
        )
        stats = {
            "cards_total": cards_total,
            "price_rows": _count(conn, "SELECT COUNT(*) FROM card_prices"),
            "cards_with_price": max(0, cards_total - cards_without_price),
            "cards_without_price": cards_without_price,
            "sets": _count(conn, "SELECT COUNT(*) FROM sets"),
            "collection_items": _count(conn, "SELECT COUNT(*) FROM collection_items"),
            "cards_owned": _count(conn, "SELECT COALESCE(SUM(quantity), 0) FROM collection_items"),
            "users": _count(conn, "SELECT COUNT(*) FROM users"),
            "corrections_total": _count(conn, "SELECT COUNT(*) FROM misreads"),
            "corrections_pending": _count(
                conn, "SELECT COUNT(*) FROM misreads WHERE COALESCE(review_status, 'pending') = 'pending'"
            ),
            "corrections_confirmed": _count(
                conn, "SELECT COUNT(*) FROM misreads WHERE review_status = 'confirmed'"
            ),
            "corrections_rejected": _count(
                conn, "SELECT COUNT(*) FROM misreads WHERE review_status = 'rejected'"
            ),
            "corrections_with_image": _count(
                conn,
                "SELECT COUNT(*) FROM misreads WHERE scan_image_path IS NOT NULL OR crop_image_path IS NOT NULL",
            ),
        }
    finally:
        conn.close()

    _schedule_image_scan(force=force_image_scan)
    with _SCAN_LOCK:
        scan = dict(_IMAGE_SCAN_CACHE)
    stats["cards_total"] = scan["total"] or stats["cards_total"]
    missing = scan["missing"]
    stats["cards_without_image"] = missing          # None until the first scan lands
    stats["cards_with_image"] = None if missing is None else max(0, stats["cards_total"] - missing)
    stats["missing_image_examples"] = list(scan["examples"])
    stats["images_scan_ready"] = missing is not None
    stats["images_scanning"] = scan["running"]

    try:
        stats["database_mb"] = round(os.path.getsize(str(DATABASE_URL)) / (1024 * 1024), 1)
    except OSError:
        stats["database_mb"] = None
    return stats
