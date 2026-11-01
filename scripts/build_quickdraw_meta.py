#!/usr/bin/env python3
"""Regenerate data/quickdraw-meta.json from the canonical card catalogue.

Why this exists: the server-side quickdraw matcher decides /api/scan's `decision`,
and its meta carried 13,217 English-only cards while the canonical catalogue has
42,487 entries including 17,348 German. A German card was therefore *structurally*
unidentifiable server-side no matter how good the photo was — the browser's weak
105-bit pHash was the only half of the system that had ever seen it.

Identity: cards_meta.json is a plain list with no id field, and card-ids.json has a
different length (40,489 vs 42,487) — zipping them positionally is the exact drift
that already mislabelled slots once. The image filename stem IS the canonical id
(verified: 40,291 unique ids, zero collisions), so derive it from there.

Usage:  python scripts/build_quickdraw_meta.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META_SRC = ROOT / "src" / "static" / "cards_meta.json"
OUT = ROOT / "data" / "quickdraw-meta.json"

# static URL prefix -> on-disk base dir (same map the browser cascade uses)
PREFIXES = (
    ("/static/german_cards/images/", ROOT / "data" / "german_cards" / "images"),
    ("/static/pocket/", ROOT / "data" / "images" / "pocket"),
    ("/static/card_images/", ROOT / "data" / "images"),
    ("/static/clip_images/", ROOT / "data" / "clip_images"),
)


def local_path(url: str):
    """Resolve a /static/... card image URL to an existing file, else None."""
    if not url:
        return None
    for prefix, base in PREFIXES:
        if prefix in url:
            p = base / url.split(prefix, 1)[1]
            return p if p.exists() else None
    return None


def card_id(url: str) -> str:
    """Canonical id from the image filename stem: .../en/me05-015.png -> me05-015."""
    return url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if url else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if OUT.is_symlink():
        sys.exit(f"refusing to write through symlink: {OUT} -> {os.readlink(OUT)}")
    if not META_SRC.exists():
        sys.exit(f"missing canonical catalogue: {META_SRC}")

    entries = json.loads(META_SRC.read_text())
    if isinstance(entries, dict):
        entries = list(entries.values())

    cards, seen, no_image, missing_file = [], set(), 0, 0
    for e in entries:
        url = e.get("image") or ""
        cid = card_id(url)
        if not cid:
            no_image += 1
            continue
        if cid in seen:          # twins are pixel-identical; keep first
            continue
        if local_path(url) is None:
            missing_file += 1
            continue
        seen.add(cid)
        cards.append({
            "ptcg_id": cid,
            "id": cid,
            "name": e.get("name", ""),
            "set": e.get("set", ""),
            "number": e.get("number", ""),
            "image": url,
            "index": len(cards),
        })

    de = sum(1 for c in cards if c["ptcg_id"].startswith("de_"))
    print(f"catalogue={len(entries)}  indexed={len(cards)}  "
          f"german={de}  no_image={no_image}  file_missing={missing_file}")

    if args.dry_run:
        print("dry run, nothing written")
        return 0

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": str(META_SRC.relative_to(ROOT)),
        "cards": cards,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB) — now rebuild the index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
