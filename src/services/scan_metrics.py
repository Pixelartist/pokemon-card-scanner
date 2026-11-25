"""Timing telemetry from the browser scanner.

The browser is the only place these numbers exist: the server receives an
upload and cannot tell whether a tablet spent 80 ms or 800 ms finding,
straightening and hashing the card, nor how long it spent loading the card
index before it could scan at all.

Two kinds of row share the table:

* ``capture`` — one per scan: the per-stage milliseconds and where the capture
  ended up (``outcome``, plus ``cloud_ms``/``gate_ms`` when it needed the
  server, which is what tells a local answer from a cloud one).
* ``startup`` — one per page load: how long the scanner took to become ready,
  split into module load, JSON transfer, JSON parse, index build and detector
  model load. This is what separates "slow at scanning" from "slow before the
  first capture".

``summary()`` groups both by device; ``scripts/scan_metrics_report.py`` prints
it. Nothing reads these rows at scan time, so this module is storage plus one
query, not part of the scan path.
"""
import math
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    kind TEXT DEFAULT 'capture',
    device TEXT,
    device_class TEXT,
    browser TEXT,
    user_agent TEXT,
    hardware_concurrency INTEGER,
    device_memory REAL,
    video TEXT,
    frames INTEGER,
    outcome TEXT,
    bytes INTEGER,
    grab_ms REAL,
    detect_ms REAL,
    crop_ms REAL,
    hash_ms REAL,
    gate_ms REAL,
    cloud_ms REAL,
    boot_ms REAL,
    modules_ms REAL,
    json_fetch_ms REAL,
    json_parse_ms REAL,
    index_ms REAL,
    model_ms REAL,
    total_ms REAL
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS idx_scan_metrics_created ON scan_metrics(created_at)"

# Every stage is a duration in milliseconds. A capture sums its stages over the
# frames of its burst; a startup measures each phase once.
CAPTURE_STAGES = ("grab_ms", "detect_ms", "crop_ms", "hash_ms", "gate_ms",
                  "cloud_ms", "total_ms")
STARTUP_STAGES = ("boot_ms", "modules_ms", "json_fetch_ms", "json_parse_ms",
                  "index_ms", "model_ms", "total_ms")
STAGES = tuple(dict.fromkeys(CAPTURE_STAGES + STARTUP_STAGES))  # total_ms is in both

KINDS = ("capture", "startup")

_TEXT_LIMITS = {
    "device": 120,
    "device_class": 16,
    "browser": 32,
    "user_agent": 400,
    "video": 24,
    "outcome": 24,
}

# A duration outside this is not a measurement: a wall-clock stamp, a NaN, or
# something a browser made up. Ten minutes is already far past useful.
_MAX_MS = 600000


def _connect(db_path):
    """Open the metrics table, creating or migrating it as needed."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.execute(INDEX)
    _migrate(conn)
    return conn


def _declared_columns():
    """(name, ddl) for each column SCHEMA declares, minus the primary key.

    Read off SCHEMA rather than kept in a second list, so a database created by
    an earlier version is completed from the same source instead of a list that
    can drift.
    """
    body = SCHEMA[SCHEMA.index("(") + 1:SCHEMA.rindex(")")]
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        name, _, ddl = line.partition(" ")
        if ddl and "PRIMARY KEY" not in ddl:
            yield name, ddl


def _migrate(conn) -> None:
    """Add the columns a database created by an earlier version is missing."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(scan_metrics)")}
    for column, ddl in _declared_columns():
        if column in present:
            continue
        try:
            conn.execute(f"ALTER TABLE scan_metrics ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError as exc:
            # Two workers can migrate the same file at once; losing that race is
            # success, because the column is there either way.
            if "duplicate column name" not in str(exc).lower():
                raise


def _number(payload: Dict[str, Any], field: str, limit: float) -> Optional[float]:
    """A finite, non-negative, rounded number from the payload, or None."""
    try:
        value = float(payload.get(field))
    except (TypeError, ValueError):
        return None
    if value != value or value < 0 or value > limit:  # NaN or out of range
        return None
    return round(value, 1)


def _int(raw, limit: int) -> Optional[int]:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= limit else None


def record(db_path, payload: Dict[str, Any]) -> int:
    """Store one row (a capture or a startup); returns the row id.

    The payload is untrusted browser input, so every field is trimmed and every
    number range-checked: a bad field is dropped, never the rest of the row.
    """
    payload = payload if isinstance(payload, dict) else {}
    values: Dict[str, Any] = {}
    for field, limit in _TEXT_LIMITS.items():
        raw = payload.get(field)
        values[field] = str(raw)[:limit] if raw not in (None, "") else None

    kind = str(payload.get("kind") or "capture").strip().lower()
    values["kind"] = kind if kind in KINDS else "capture"

    for field in STAGES:
        values[field] = _number(payload, field, _MAX_MS)

    values["frames"] = _int(payload.get("frames"), 100)
    values["hardware_concurrency"] = _int(payload.get("hardware_concurrency"), 256)
    values["device_memory"] = _number(payload, "device_memory", 1024)
    values["bytes"] = _int(payload.get("bytes"), 1 << 30)

    columns = list(values)
    marks = ", ".join("?" for _ in columns)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO scan_metrics ({', '.join(columns)}) VALUES ({marks})",
            tuple(values[c] for c in columns),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Interpolated percentile; None for an empty list.

    Interpolated rather than nearest-rank so a handful of samples still gives
    the median the eye expects: nearest-rank called the median of
    [100, 200, 300, 400] 300.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (pct / 100.0) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    value = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return round(value, 1)


def recent(db_path, days: int = 7, limit: int = 20000) -> List[sqlite3.Row]:
    """Rows from the last `days`, newest first."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM scan_metrics WHERE created_at >= datetime('now', ?)"
            " ORDER BY id DESC LIMIT ?",
            (f"-{int(days)} days", int(limit)),
        ).fetchall()
    finally:
        conn.close()


def _group(rows: List[sqlite3.Row], stages) -> List[Dict[str, Any]]:
    """Rows grouped by device class, device and browser.

    The question this answers is "which device is slow, and in which stage", so
    a tablet on Safari and a laptop on Chrome must not be averaged together.
    """
    groups: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row["device_class"] or "unknown",
               row["device"] or "unknown",
               row["browser"] or "unknown")
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "rows": 0,
                "outcomes": {},
                "stages": {stage: [] for stage in stages},
                "cores": [],
                "memory": [],
                "bytes": [],
            }
        group["rows"] += 1
        outcome = row["outcome"] or "unknown"
        group["outcomes"][outcome] = group["outcomes"].get(outcome, 0) + 1
        for stage in stages:
            if row[stage] is not None:
                group["stages"][stage].append(float(row[stage]))
        for column, bucket in (("hardware_concurrency", "cores"),
                               ("device_memory", "memory"),
                               ("bytes", "bytes")):
            if row[column] is not None:
                group[bucket].append(float(row[column]))

    devices = []
    for (device_class, device, browser), group in groups.items():
        devices.append({
            "device_class": device_class,
            "device": device,
            "browser": browser,
            "rows": group["rows"],
            "outcomes": dict(sorted(group["outcomes"].items(), key=lambda kv: -kv[1])),
            "cores_p50": _percentile(group["cores"], 50),
            "memory_p50": _percentile(group["memory"], 50),
            "bytes_p50": _percentile(group["bytes"], 50),
            "stages": {
                stage: {
                    "p50": _percentile(values, 50),
                    "p90": _percentile(values, 90),
                    "max": round(max(values), 1) if values else None,
                    "n": len(values),
                }
                for stage, values in group["stages"].items()
            },
        })
    devices.sort(key=lambda d: (-d["rows"], d["device"]))
    return devices


def _slowest(rows: List[sqlite3.Row], stages, limit: int = 5) -> List[Dict[str, Any]]:
    return [
        {
            "id": row["id"], "created_at": row["created_at"],
            "device_class": row["device_class"], "device": row["device"],
            "browser": row["browser"], "outcome": row["outcome"],
            "video": row["video"], "frames": row["frames"],
            **{stage: row[stage] for stage in stages},
        }
        for row in sorted(rows, key=lambda r: -(r["total_ms"] or 0))[:limit]
    ]


def summary(db_path, days: int = 7, limit: int = 20000) -> Dict[str, Any]:
    """Per-device capture timings and startup timings, kept apart.

    A startup row has no grab/detect/crop stages and a capture has no parse or
    model load, so averaging them together would describe neither.
    """
    rows = recent(db_path, days=days, limit=limit)
    captures = [row for row in rows if (row["kind"] or "capture") == "capture"]
    startups = [row for row in rows if row["kind"] == "startup"]

    return {
        "window_days": days,
        "captures": len(captures),
        "startups": len(startups),
        "devices": _group(captures, CAPTURE_STAGES),
        "startup_devices": _group(startups, STARTUP_STAGES),
        "slowest": _slowest(captures, CAPTURE_STAGES),
        "slowest_startups": _slowest(startups, STARTUP_STAGES),
    }
