"""Persistent price cache for card market prices.

Prices from api.pokemontcg.io are rate-limited and intermittently return
500s/empty bodies, which made collection-value totals fluctuate wildly
between calls. This module stores successful fetches in SQLite
(card_prices table) with a 24h TTL, and — crucially — serves the last
known good prices when a fresh fetch fails, so totals stay stable.
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from api.models.database import DATABASE_URL

logger = logging.getLogger(__name__)

# How long a cached price stays "fresh" before we try to re-fetch.
CACHE_TTL_HOURS = 24

# How long we remember a *failed* price lookup (stored as an empty dict)
# so a card with no available pricing doesn't pay the full retry latency
# on every click.
NEGATIVE_TTL_HOURS = 6


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_URL)


def get_cached_prices(card_id: str) -> Optional[Dict[str, Any]]:
    """Return cached prices for a card regardless of age, or None."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT prices FROM card_prices WHERE card_id = ?", (card_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])
    except (sqlite3.Error, ValueError) as e:
        logger.debug(f"Price cache read failed for {card_id}: {e}")
        return None
    finally:
        conn.close()


def _cache_age_hours(card_id: str) -> Optional[float]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT fetched_at FROM card_prices WHERE card_id = ?", (card_id,)
        ).fetchone()
        if not row or not row[0]:
            return None
        fetched = datetime.fromisoformat(row[0])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
    except (sqlite3.Error, ValueError):
        return None
    finally:
        conn.close()


def _store_prices(card_id: str, prices: Dict[str, Any]) -> None:
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO card_prices (card_id, prices, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(card_id) DO UPDATE SET
                 prices = excluded.prices,
                 fetched_at = excluded.fetched_at""",
            (card_id, json.dumps(prices), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.debug(f"Price cache write failed for {card_id}: {e}")
    finally:
        conn.close()


def get_prices_cached(card_id: str, ttl_hours: float = CACHE_TTL_HOURS) -> Optional[Dict[str, Any]]:
    """Get prices for a card: fresh cache first, then live fetch, stale cache last.

    Order of preference:
      1. Cache entry younger than ttl_hours — returned immediately, no network.
      2. Live fetch from the API — stored on success and returned.
      3. Stale cache entry (any age) — returned when the live fetch failed,
         so rate limiting can never zero out a known-good valuation.
    """
    if not card_id:
        return None

    age = _cache_age_hours(card_id)
    if age is not None:
        cached = get_cached_prices(card_id)
        if cached == {}:
            # Negative-cache marker: a recent fetch found no prices.
            # Keep it shorter than the success TTL so new listings surface.
            if age < NEGATIVE_TTL_HOURS:
                return None
        elif cached:
            if age < ttl_hours:
                return cached

    # Cache miss or stale: fetch live.
    from services.ptcg_client import get_client
    prices = get_client().get_prices(card_id)
    if prices and (prices.get("tcgplayer") or prices.get("cardmarket")):
        _store_prices(card_id, prices)
        return prices

    # Live fetch failed (or returned no data). Prefer any cached prices we
    # have over nothing — a slightly old price beats a missing one.
    stale = get_cached_prices(card_id)
    if stale:  # non-empty dict
        logger.debug(f"Serving stale cached prices for {card_id} (fetch failed)")
        return stale

    # Nothing available anywhere: record the failure so the next lookup is
    # instant instead of paying the full retry latency again.
    _store_prices(card_id, {})
    return None


def prime_cache(card_id: str, prices: Dict[str, Any]) -> None:
    """Store prices fetched elsewhere (e.g. a full card lookup) into the cache."""
    if card_id and prices and (prices.get("tcgplayer") or prices.get("cardmarket")):
        _store_prices(card_id, prices)
