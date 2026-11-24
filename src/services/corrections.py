"""Review of user-submitted card corrections (the ``misreads`` table).

Every row is one wrong identification a user corrected: the wrong card, the
card they say it is, the score the matcher gave it and the crop it was cut from.
That is training data, so the admin panel vets it before it is trusted — a
correction can be confirmed, rejected, re-pointed at a different card, or
deleted.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from api.models.database import get_db_connection

logger = logging.getLogger(__name__)

REVIEW_STATES = ("pending", "confirmed", "rejected")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def correction_counts() -> Dict[str, int]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN COALESCE(review_status, 'pending') = 'pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN review_status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
              SUM(CASE WHEN review_status = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM misreads
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": int(row["total"] or 0),
        "pending": int(row["pending"] or 0),
        "confirmed": int(row["confirmed"] or 0),
        "rejected": int(row["rejected"] or 0),
    }


def list_corrections(status: str = "all", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Corrections newest first, with the submitting username resolved."""
    status = (status or "all").lower()
    if status not in ("all",) + REVIEW_STATES:
        status = "all"
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where = ""
    params: List[Any] = []
    if status != "all":
        where = "WHERE COALESCE(m.review_status, 'pending') = ?"
        params.append(status)

    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT m.id, m.scan_image_path, m.crop_image_path,
                   m.wrong_id, m.wrong_name, m.correct_id, m.correct_name,
                   m.score, m.source, m.user_id, m.created_at,
                   COALESCE(m.review_status, 'pending') AS review_status,
                   m.reviewed_at, m.admin_note,
                   u.username AS username,
                   r.username AS reviewed_by_name
            FROM misreads m
            LEFT JOIN users u ON u.id = m.user_id
            LEFT JOIN users r ON r.id = m.reviewed_by
            {where}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    finally:
        conn.close()

    return {
        "corrections": [dict(row) for row in rows],
        "count": len(rows),
        "counts": correction_counts(),
        "status": status,
        "limit": limit,
        "offset": offset,
    }


def card_exists(ptcg_id: str) -> bool:
    conn = get_db_connection()
    try:
        return conn.execute("SELECT 1 FROM cards WHERE ptcg_id = ?", (ptcg_id,)).fetchone() is not None
    finally:
        conn.close()


def update_correction(
    correction_id: int,
    *,
    review_status: Optional[str] = None,
    correct_id: Optional[str] = None,
    correct_name: Optional[str] = None,
    admin_note: Optional[str] = None,
    reviewed_by: Optional[int] = None,
) -> Optional[Tuple[Dict[str, Any], bool]]:
    """Apply a vetting decision or re-point the correction.

    Returns ``(row, training_changed)``, or None when the correction is gone.
    ``training_changed`` is False when the edit only writes the admin note, or
    repeats a value the row already has: the confuser index is built from
    ``review_status`` and ``correct_id``, and the note is not training data.
    """
    sets: List[str] = []
    params: List[Any] = []

    conn = get_db_connection()
    try:
        current = conn.execute(
            "SELECT review_status, correct_id FROM misreads WHERE id = ?", (correction_id,)
        ).fetchone()
        if current is None:
            return None

        training_changed = False
        if review_status is not None:
            if review_status not in REVIEW_STATES:
                raise ValueError(f"review_status must be one of {', '.join(REVIEW_STATES)}")
            training_changed = review_status != (current["review_status"] or "pending")
            sets += ["review_status = ?", "reviewed_by = ?", "reviewed_at = ?"]
            params += [review_status, reviewed_by, _now()]

        if correct_id is not None:
            training_changed = training_changed or correct_id != (current["correct_id"] or "")
            sets.append("correct_id = ?")
            params.append(correct_id)
        if correct_name is not None:
            sets.append("correct_name = ?")
            params.append(correct_name)
        if admin_note is not None:
            sets.append("admin_note = ?")
            params.append(admin_note[:500])

        if not sets:
            return None

        params.append(correction_id)
        conn.execute(f"UPDATE misreads SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM misreads WHERE id = ?", (correction_id,)).fetchone()
        return (dict(row), training_changed) if row else None
    finally:
        conn.close()


def delete_correction(correction_id: int) -> bool:
    conn = get_db_connection()
    try:
        cur = conn.execute("DELETE FROM misreads WHERE id = ?", (correction_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def search_cards(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Find cards by name or id, for re-pointing a correction."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    like = f"%{q.lower()}%"
    conn = get_db_connection()
    try:
        # A name can have many printings (Nebulak has 20) and the picker only
        # asks for 8, so an exact name lists them all instead of a cut.
        exact_name = conn.execute(
            "SELECT 1 FROM cards WHERE LOWER(name) = ? LIMIT 1", (q.lower(),)
        ).fetchone() is not None
        rows = conn.execute(
            """
            SELECT ptcg_id, name, set_name, number, rarity, images
            FROM cards
            WHERE LOWER(name) LIKE ? OR LOWER(ptcg_id) LIKE ?
            ORDER BY CASE WHEN LOWER(ptcg_id) = ? THEN 0 ELSE 1 END,
                     name, set_name, number
            LIMIT ?
            """,
            (like, like, q.lower(), 50 if exact_name else limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
