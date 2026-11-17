"""Background identification for captures the matcher could not name.

A capture is enqueued with its crop. A daemon worker reads the card's printed
identity — local OCR first, a vision LLM second — and resolves it against the
cards table. One attempt per provider, so a job that runs out of providers is
reported as `exhausted` rather than being retried forever; the review queue
shows that instead of leaving a row looking like it is still being worked on.

The worker is a plain thread, the same pattern the index builders use: one
process, SQLite, no broker to run alongside the container.
"""
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from services import card_text

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # OCR, then a vision LLM

SCHEMA = """
CREATE TABLE IF NOT EXISTS identify_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crop_image_path TEXT,
    scan_image_path TEXT,
    card_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    provider TEXT,
    result_id TEXT,
    result_name TEXT,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_worker = None
_worker_db = None
_lock = threading.Lock()


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def enqueue(db_path, crop_image_path, scan_image_path=None, card_key=None):
    """Queue one background attempt; returns the job id."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO identify_jobs (crop_image_path, scan_image_path, card_key,"
            " max_attempts) VALUES (?,?,?,?)",
            (crop_image_path, scan_image_path, card_key, MAX_ATTEMPTS),
        )
        conn.commit()
        job_id = cursor.lastrowid
    finally:
        conn.close()
    _ensure_worker(db_path)
    return job_id


def statuses(db_path, ids=None, limit=100):
    conn = _connect(db_path)
    try:
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM identify_jobs WHERE id IN ({placeholders})", tuple(ids)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM identify_jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _finish(db_path, job_id, fields):
    conn = _connect(db_path)
    try:
        assignments = ", ".join(f"{key}=?" for key in fields)
        conn.execute(
            f"UPDATE identify_jobs SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (*fields.values(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _claim(db_path):
    """Take one queued job. The status guard makes a double claim impossible."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM identify_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        claimed = conn.execute(
            "UPDATE identify_jobs SET status='running', updated_at=CURRENT_TIMESTAMP"
            " WHERE id=? AND status='queued'", (row["id"],)
        )
        conn.commit()
        return dict(row) if claimed.rowcount else None
    finally:
        conn.close()


def _file_path(path):
    """An openable path for a stored image.

    Queue rows carry paths relative to the data directory ("scans/crops/x.jpg"),
    but a test or a script may hand us one that is already resolvable.
    """
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_file():
        return str(candidate)
    # Same default as api.main: the project's data directory, so the answer does
    # not depend on the working directory the worker's process was started in.
    data_root = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
    under_data = data_root / path
    return str(under_data) if under_data.is_file() else str(candidate)


def _read_with_ocr(path):
    return card_text.parse_card_text(card_text.ocr_text(path))


def _read_with_llm(path):
    return card_text.vision_llm(path)


def providers():
    """The providers that are actually usable in this process, cheapest first."""
    chain = []
    if card_text.ocr_available():
        chain.append(("ocr", _read_with_ocr))
    if card_text.llm_available():
        chain.append(("llm", _read_with_llm))
    return chain


def _read_sources(db_path, read, sources):
    """Try one reader over each source until one resolves to a card.

    The stored crop is what the user reviewed, but it is clipped above the
    collector line often enough that the number is simply not in the image; the
    frame it was cut from has it. Returns (row, parsed, source) or (None, ...).
    """
    for source, label in sources:
        parsed = read(source)
        if not parsed:
            continue
        conn = _connect(db_path)
        try:
            row = card_text.resolve_card(conn, parsed, name=parsed.get("name"))
        finally:
            conn.close()
        if row:
            return row, parsed, label
    return None, None, None


def process(db_path, job):
    """Run the provider chain for one job; returns the fields to persist."""
    chain = providers()
    if not chain:
        return {"status": "unavailable",
                "detail": "no OCR or vision LLM configured on the server"}
    # Crop first (what the row shows), then the frame it came from.
    sources = []
    for field, label in (("crop_image_path", "crop"), ("scan_image_path", "scan")):
        path = _file_path(job.get(field))
        if path and all(path != existing for existing, _ in sources):
            sources.append((path, label))
    attempts = int(job.get("attempts") or 0)
    notes = []
    for name, read in chain:
        attempts += 1
        row, parsed, source = _read_sources(db_path, read, sources)
        if row:
            return {"status": "done", "attempts": attempts, "provider": name,
                    "result_id": row["ptcg_id"], "result_name": row["name"],
                    "detail": f"{name} read {parsed.get('number')} from the {source}"}
        notes.append(f"{name}: inconclusive")
    return {"status": "exhausted", "attempts": attempts,
            "provider": chain[-1][0], "detail": "; ".join(notes)}


def _run(db_path):
    while True:
        job = _claim(db_path)
        if not job:
            time.sleep(1.0)
            continue
        try:
            fields = process(db_path, job)
        except Exception as exc:  # a bad image must not kill the worker
            logger.exception("identify job %s failed", job["id"])
            fields = {"status": "error", "detail": str(exc)[:200]}
        _finish(db_path, job["id"], fields)


def _ensure_worker(db_path):
    global _worker, _worker_db
    with _lock:
        if _worker is not None and _worker.is_alive() and _worker_db == db_path:
            return
        _worker_db = db_path
        _worker = threading.Thread(target=_run, args=(db_path,), name="identify-jobs",
                                   daemon=True)
        _worker.start()


def wait_for_idle(db_path, timeout=10.0):
    """Test helper: block until nothing is queued or running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = _connect(db_path)
        try:
            pending = conn.execute(
                "SELECT COUNT(*) FROM identify_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
        finally:
            conn.close()
        if not pending:
            return True
        time.sleep(0.05)
    return False
