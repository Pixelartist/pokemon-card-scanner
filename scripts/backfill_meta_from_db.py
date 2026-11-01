#!/usr/bin/env python3
"""Backfill cards_meta.json from pokemon_cards.db.

The browser index (card-ids.json + card-hashes.json) covers 40,489 cards but
cards_meta.json only had 38,491 of them, so ~2k TCG Pocket scans matched a hash
and then displayed a bare id with no name or thumbnail. The DB already has every
one of those rows, so this is a local copy — no provider fetch needed.

Rerunnable: ids that already have a meta row are left untouched.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pokemon_cards.db"
META = ROOT / "src" / "static" / "cards_meta.json"
IDS = ROOT / "src" / "static" / "card-ids.json"


def key(set_code, number):
    return f"{set_code}-{number}".lower()


def main():
    ids = json.loads(IDS.read_text())
    meta = json.loads(META.read_text())

    # Mirror the browser hydration's lookup: exact, case-insensitive, and with
    # the "de_" prefix stripped (German index ids are de_<set>-<n>, meta stores
    # them unprefixed).
    have = set()
    for m in meta:
        k = key(m["set"], m["number"])
        have.add(k)
        have.add("de_" + k)

    gaps = [i for i in ids if i.lower() not in have]
    print(f"{len(ids)} index ids, {len(meta)} meta rows, {len(gaps)} to backfill")
    if not gaps:
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ptcg_id, name, rarity, hp, types, images, set_name FROM cards"
    ).fetchall()
    by_id = {}
    for r in rows:
        by_id.setdefault(r["ptcg_id"].lower(), r)
        # card-ids.json zero-pads ("B4b-001"); DB may not ("B4b-1").
        set_code, _, num = r["ptcg_id"].rpartition("-")
        try:
            padded = f"{set_code}-{int(num):03d}"
        except ValueError:
            padded = None
        if padded:
            by_id.setdefault(padded.lower(), r)

    added = unresolved = 0
    for cid in gaps:
        r = by_id.get(cid.lower()) or by_id.get(cid.lower().removeprefix("de_"))
        if r is None:
            unresolved += 1
            continue
        set_code, _, number = cid.rpartition("-")  # keep the index's own padding
        try:
            image = json.loads(r["images"] or "{}").get("large", "")
        except json.JSONDecodeError:
            image = ""
        try:
            types = json.loads(r["types"] or "[]")
        except json.JSONDecodeError:
            types = []
        meta.append({
            "name": r["name"],
            "set": set_code,
            "number": number,
            "image": image,
            "set_name": r["set_name"] or "",
            "rarity": r["rarity"] or "",
            "types": types,
            "hp": int(r["hp"]) if str(r["hp"]).isdigit() else 0,
        })
        added += 1

    # Every appended row must be findable by its index id, or the browser will
    # still show a bare id.
    gap_keys = {i.lower() for i in gaps}
    for m in meta[-added:]:
        assert key(m["set"], m["number"]) in gap_keys, m

    META.write_text(json.dumps(meta, ensure_ascii=False))
    print(f"added {added} rows -> {len(meta)} total; {unresolved} ids not in the DB")


if __name__ == "__main__":
    sys.exit(main())
