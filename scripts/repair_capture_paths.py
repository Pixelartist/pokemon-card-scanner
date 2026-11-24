#!/usr/bin/env python3
"""Rewrite inline-image capture fields into real files.

Rows filed before ``/api/collection/add`` validated its capture fields can hold
a base64 data URL in ``collection_items.crop_image_path``. The details view asks
for ``/data/<value>``, which cannot resolve a data URL, so the card showed no
capture at all.

This decodes each one into ``data/scans/crops/crop_<stamp>_<id>_q.jpg`` and
points the row at the file. Re-running is a no-op: the column no longer holds a
data URL.

    python scripts/repair_capture_paths.py --dry-run
    python scripts/repair_capture_paths.py
"""
import argparse
import base64
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "pokemon_cards.db"
CROP_DIR = DATA_DIR / "scans" / "crops"
SCAN_DIR = DATA_DIR / "scans"

COLUMNS = ("crop_image_path", "scan_image_path")


def decode(data_url):
    """(bytes, extension) for a data:image/...;base64 URL, else (None, None)."""
    header, _, payload = (data_url or "").partition(",")
    if "base64" not in header or not payload or not header.lower().startswith("data:image/"):
        return None, None
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None, None
    if not raw:
        return None, None
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, "jpg"
    if raw.startswith(b"\x89PNG"):
        return raw, "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "webp"
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be rewritten, write nothing")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        sys.exit(f"database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, crop_image_path, scan_image_path FROM collection_items "
        "WHERE crop_image_path LIKE 'data:%' OR scan_image_path LIKE 'data:%'"
    ).fetchall()
    if not rows:
        print("nothing to repair: no inline images stored in a path column")
        return

    CROP_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    fixed = skipped = 0
    for row in rows:
        updates = {}
        for column in COLUMNS:
            value = row[column]
            if not (value or "").startswith("data:"):
                continue
            raw, ext = decode(value)
            if not raw:
                print(f"row {row['id']}: {column} is not a decodable image — left alone")
                skipped += 1
                continue
            # The row id in the name keeps two rows filed in the same second
            # from sharing (and overwriting) one file, which would recreate the
            # very problem this repairs.
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}_{row['id']}"
            if column == "crop_image_path":
                relative = f"scans/crops/crop_{stamp}_q.{ext}"
                target = CROP_DIR / f"crop_{stamp}_q.{ext}"
            else:
                relative = f"scans/scan_{stamp}_browser.{ext}"
                target = SCAN_DIR / f"scan_{stamp}_browser.{ext}"
            print(f"row {row['id']}: {column} -> {relative} ({len(raw)} bytes)")
            if args.dry_run:
                continue
            target.write_bytes(raw)
            updates[column] = relative
        if updates and not args.dry_run:
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(f"UPDATE collection_items SET {assignments} WHERE id = ?",
                         (*updates.values(), row["id"]))
            conn.commit()
            fixed += 1

    conn.close()
    if args.dry_run:
        print(f"dry run: {len(rows)} row(s) would be repaired")
    else:
        print(f"repaired {fixed} row(s), left {skipped} value(s) alone")


if __name__ == "__main__":
    main()
