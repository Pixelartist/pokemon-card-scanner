#!/usr/bin/env python3
"""Backfill cards_meta.json from pokemon_cards.db.

The browser index (card-ids.json + card-hashes.json) covers 40,489 cards but
cards_meta.json only had 38,491 of them, so ~2k TCG Pocket scans matched a hash
and then displayed a bare id with no name or thumbnail. The DB already has every
one of those rows, so this is a local copy — no provider fetch needed.

Output is always one row per index id, in card-ids.json order. The CLIP loader
and the browser both pair rows with ids *by position*, so appending new rows at
the end (as this script used to) is what made the two lengths diverge and
silently switched the CLIP index off. Rows that already exist are reused as-is.

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


def row_key(m):
    """Key a meta row the way the browser hydrates it: set+number, prefixed with
    ``de_`` when the row's art is German (English and German prints of one card
    share set+number, so the set+number alone lets one shadow the other)."""
    k = key(m.get("set", ""), m.get("number", ""))
    return "de_" + k if "/german_cards/" in (m.get("image") or "") else k


def lookup(by_key, cid):
    k = cid.lower()
    row = by_key.get(k)
    if row is not None:
        return row
    # The row's German-ness comes from its art path, not from the index id, and
    # older rows were stored unprefixed: an id may own a row under the other key.
    return by_key.get(k[3:]) if k.startswith("de_") else by_key.get("de_" + k)


def blank_row(cid):
    """One row per index id even when the DB has nothing to fill it with."""
    set_code, _, number = cid.rpartition("-")
    return {"name": "", "set": set_code, "number": number, "image": ""}


def targets():
    """data/cards_meta.json is what the CLIP loader reads, src/static/ is what
    the browser reads. Keep both copies in the same order when both exist."""
    out = [META]
    data_copy = ROOT / "data" / "cards_meta.json"
    if data_copy.exists() and not data_copy.samefile(META):
        out.append(data_copy)
    return out


def main():
    ids = json.loads(IDS.read_text(encoding="utf-8"))
    raw = META.read_text(encoding="utf-8")
    meta = json.loads(raw)

    by_key = {}
    for m in meta:
        by_key.setdefault(row_key(m), m)  # first row wins, as in the browser

    gaps = [i for i in ids if lookup(by_key, i) is None]
    print(f"{len(ids)} index ids, {len(meta)} meta rows, {len(gaps)} to backfill")

    filled = {}
    unresolved = 0
    if gaps:
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
            filled[cid.lower()] = {
                "name": r["name"],
                "set": set_code,
                "number": number,
                "image": image,
                "set_name": r["set_name"] or "",
                "rarity": r["rarity"] or "",
                "types": types,
                "hp": int(r["hp"]) if str(r["hp"]).isdigit() else 0,
            }

    ordered = [
        lookup(by_key, cid) or filled.get(cid.lower()) or blank_row(cid)
        for cid in ids
    ]

    # Position is the join key everywhere downstream: exactly one row per index
    # id, and that row must describe that id. Never write a list that fails this.
    assert len(ordered) == len(ids), (len(ordered), len(ids))
    for cid, m in zip(ids, ordered):
        k = cid.lower()
        allowed = {k, k[3:]} if k.startswith("de_") else {k, "de_" + k}
        assert row_key(m) in allowed, (cid, m)

    out = json.dumps(ordered, ensure_ascii=False)
    dropped = len(meta) - len(ordered)
    for path in targets():
        if path == META and out == raw:
            continue
        path.write_text(out, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}: {len(ordered)} rows in card-ids.json order")
    print(f"{len(filled)} backfilled, {unresolved} ids not in the DB, "
          f"{dropped} unmatched row(s) dropped")


if __name__ == "__main__":
    sys.exit(main())
