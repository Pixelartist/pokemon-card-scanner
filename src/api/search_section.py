@app.get("/api/cards/search")
async def search_cards(query: str, limit: int = 20):
    """Search cards by name, set code, or number (public endpoint, no auth).
    Searches both the local DB and the full CLIP card index (~21k cards)."""
    import sqlite3
    from pathlib import Path

    q = query.strip().lower()
    results = []
    seen = set()

    # 1) Database results
    conn = get_db_connection()
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
        (search_param, search_param, search_param, search_param, limit),
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
            matcher.load_index_fast()  # Use fast binary format
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _misreads_conn():
    conn = get_db_connection()
    conn.execute(_MISREADS_DDL)  # idempotent, covers DBs created before the table existed
    return conn