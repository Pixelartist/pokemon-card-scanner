#!/usr/bin/env python3
"""Backfill cards_meta.json from pokemon_cards.db.

The browser index (card-ids.json + card-hashes.json) covers 40,489 cards but
cards_meta.json only had 38,491 of them, so ~2k TCG Pocket scans matched a hash
and then displayed a bare id with no name or thumbnail. The DB already has every
one of those rows, so this is a local copy — no provider fetch needed.

Output is always one row per index id, in card-ids.json order. The CLIP loader
and the browser both pair rows with ids *by position*, so appending new rows at
the end (as this script used to) is what made the two lengths diverge and
silently switched the CLIP index off. Rows that already exist keep their place
and only gain the fields they are missing (set_name, rarity, types, hp), which
is what the older rows never got: reusing them whole left those four empty on
8,961 of them even though the DB has the values.

Rerunnable: a second run finds nothing left to fill.
"""
import json
import os
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


FILL_FIELDS = ("set_name", "rarity", "types", "hp")
ROW_FIELDS = ("name", "set", "number", "image") + FILL_FIELDS


def populated(row):
    """How many values a row carries, to prefer the richer of two duplicates.

    71 keys in cards_meta.json hold two rows because two ids derive the same
    set+number (German and English print of one card). Resolving a key to the
    first row blindly can land on the bare one while its sibling carries the
    set name or rarity; the richer row is the one worth writing.
    """
    return sum(1 for f in ROW_FIELDS if row.get(f))


def card_from_db(by_id, cid):
    """The DB's row for an index id, in cards_meta.json shape, or None."""
    r = by_id.get(cid.lower()) or by_id.get(cid.lower().removeprefix("de_"))
    if r is None:
        return None
    set_code, _, number = cid.rpartition("-")  # keep the index's own padding
    try:
        image = json.loads(r["images"] or "{}").get("large", "")
    except json.JSONDecodeError:
        image = ""
    try:
        types = json.loads(r["types"] or "[]")
    except json.JSONDecodeError:
        types = []
    return {
        "name": r["name"],
        "set": set_code,
        "number": number,
        "image": image,
        "set_name": r["set_name"] or "",
        "rarity": r["rarity"] or "",
        "types": types,
        "hp": int(r["hp"]) if str(r["hp"]).isdigit() else 0,
    }


def write_atomic(path, text):
    """Replace ``path`` in one step so no reader ever sees a half-written file.

    ``write_text`` truncates 6.8 MB in place, and the daemon reads
    data/cards_meta.json when it starts, so a restart landing inside that
    window would parse a truncated list. The bytes go to a sibling temp file
    (same filesystem, so the replace is atomic) and only then take the name.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            # A replace swaps in a new inode, so keep the owner the old file had:
            # this runs as root in the container and the daemon reads as hermes.
            geteuid = getattr(os, "geteuid", None)
            if geteuid and geteuid() == 0 and path.exists():
                st = path.stat()
                os.fchown(f.fileno(), st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def targets():
    """data/cards_meta.json is what the CLIP loader reads, src/static/ is what
    the browser reads. Keep both copies in the same order when both exist: the
    data copy is written last, so the running daemon never sees the new file
    before the tracked one is in place."""
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
        k = row_key(m)
        prev = by_key.get(k)
        if prev is None or populated(m) > populated(prev):
            by_key[k] = m  # richest row wins; equal rows keep the first

    gaps = sum(1 for cid in ids if lookup(by_key, cid) is None)
    print(f"{len(ids)} index ids, {len(meta)} meta rows, {gaps} to backfill")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    by_id = {}
    for r in conn.execute(
        "SELECT ptcg_id, name, rarity, hp, types, images, set_name FROM cards"
    ):
        by_id.setdefault(r["ptcg_id"].lower(), r)
        # card-ids.json zero-pads ("B4b-001"); DB may not ("B4b-1").
        set_code, _, num = r["ptcg_id"].rpartition("-")
        try:
            by_id.setdefault(f"{set_code}-{int(num):03d}".lower(), r)
        except ValueError:
            pass
    conn.close()

    ordered = []
    backfilled = added_fields = fill_rows = fill_fields = unresolved = 0
    for cid in ids:
        row = lookup(by_key, cid)
        card = card_from_db(by_id, cid)
        if row is None:
            if card is None:
                unresolved += 1
                ordered.append(blank_row(cid))
            else:
                backfilled += 1
                added_fields += sum(1 for f in FILL_FIELDS if card[f])
                ordered.append(card)
            continue
        if card is not None:
            # One dict decides both what to fill and what it is worth counting.
            missing = {f: card[f] for f in FILL_FIELDS if not row.get(f) and card[f]}
            if missing:
                row = {**row, **missing}
                fill_rows += 1
                fill_fields += len(missing)
        ordered.append(row)

    # Position is the join key everywhere downstream: exactly one row per index
    # id, and that row must describe that id. Never write a list that fails this.
    assert len(ordered) == len(ids), (len(ordered), len(ids))
    for cid, m in zip(ids, ordered):
        k = cid.lower()
        allowed = {k, k[3:]} if k.startswith("de_") else {k, "de_" + k}
        assert row_key(m) in allowed, (cid, m)

    out = json.dumps(ordered, ensure_ascii=False)
    if len(meta) == len(ordered):
        # Same length, so the honest count is the file diff, position by position:
        # it covers DB fills and a duplicate swap without double-counting either.
        upgraded = fields = 0
        for old, new in zip(meta, ordered):
            if old is not new and old != new:
                upgraded += 1
                fields += sum(1 for f in ROW_FIELDS if old.get(f) != new.get(f))
    else:
        # A run that adds rows has nothing to align against, so it reports adds.
        upgraded, fields = fill_rows + backfilled, fill_fields + added_fields
    dropped = max(0, len(meta) - len(ordered))
    for path in targets():
        if path == META and out == raw:
            continue
        write_atomic(path, out)
        print(f"wrote {path.relative_to(ROOT)}: {len(ordered)} rows in card-ids.json order")
    print(f"{backfilled} backfilled, {upgraded} row(s) changed in the file with "
          f"{fields} field(s) filled, {unresolved} ids not in the DB, "
          f"{dropped} unmatched row(s) dropped")


if __name__ == "__main__":
    sys.exit(main())
