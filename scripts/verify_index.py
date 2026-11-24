#!/usr/bin/env python3
"""Prove the CLIP index files still agree with each other, in one command.

card-index.bin is raw int16 with no header, so which card each row describes
exists only in the files beside it. Every way they can drift is checked here:
the row count against the file size, the two id lists, the metadata rows and the
manifest. One PASS/FAIL per invariant, and exit code 1 if any fails, so a
rebuild or a restore can be gated on it.

It also compares the manifest's checkpoint with the constants the loader loads,
because a manifest naming another model passes every file-shape check and is
still refused at startup.

This replaces the one-off checkers that each covered a piece of it and mostly
wanted clip_embeddings.npz, which is no longer in this data directory.

Usage: python scripts/verify_index.py [data-dir]
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The checkpoint comes from the loader, not a copy here: a verifier with its own
# idea of the model could pass a manifest the loader refuses.
sys.path.insert(0, str(ROOT / "src"))
try:
    from services.clip_matcher import CLIP_EMBED_DIM, CLIP_MODEL_NAME, CLIP_PRETRAINED
except Exception as exc:  # nothing here means anything without the model
    sys.exit(f"cannot read the loader's checkpoint constants: {exc}")

DIM, ITEM = CLIP_EMBED_DIM, 2  # card-index.bin is (rows, CLIP_EMBED_DIM) int16


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_ids(ids):
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()


def row_key(m):
    k = f"{m.get('set', '')}-{m.get('number', '')}".lower()
    return "de_" + k if "/german_cards/" in (m.get("image") or "") else k


def has_meta(keys, cid):
    """An id owns a row under its own key or the de_ form of it."""
    k = cid.lower()
    return k in keys or (k[3:] if k.startswith("de_") else "de_" + k) in keys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    data = Path(argv[1]) if len(argv) > 1 else ROOT / "data"
    print(f"index: {data}\n")
    results = []

    def record(name, ok, detail):
        results.append(ok)
        print(f"{name:<12}{'PASS' if ok else 'FAIL'}  {detail}")

    bin_path = data / "card-index.bin"
    size = bin_path.stat().st_size if bin_path.exists() else 0
    rows, rem = divmod(size, DIM * ITEM)
    record("bin", rem == 0 and rows > 0,
           f"{rows} rows of {DIM} int16, {size} bytes" if rem == 0 and rows else
           f"{bin_path.name} missing" if not size else
           f"{size} bytes is not a whole number of {DIM}xint16 rows")

    clip_ids, card_ids = [], []
    try:
        clip_ids = [c.get("card_id") or "" for c in
                    (load(data / "clip_card_index.json").get("cards") or [])]
        ids_path = data / "card-ids.json"
        if not ids_path.exists():  # unreadable symlink outside the container
            ids_path = ROOT / "src" / "static" / "card-ids.json"
        card_ids = load(ids_path)
        dups = len(clip_ids) - len(set(clip_ids))
        first = next((i for i, (a, b) in enumerate(zip(clip_ids, card_ids)) if a != b), None)
        if first is None and len(clip_ids) > len(card_ids):
            first = len(card_ids)  # the index names more rows than the id file has
        problems = []
        if len(clip_ids) != rows:
            problems.append(f"{len(clip_ids)} ids vs {rows} bin rows")
        if first is not None:
            problems.append(f"diverges from {ids_path.name} at row {first}")
        if dups:
            problems.append(f"{dups} duplicate id(s)")
        record("id list", not problems,
               f"{len(clip_ids)} ids == {ids_path.name}[:{len(clip_ids)}], {dups} duplicates"
               if not problems else ", ".join(problems))
    except Exception as e:  # a broken file is a failed check, not a crash
        record("id list", False, f"{type(e).__name__}: {e}")

    try:
        meta = load(data / "cards_meta.json")
        keys = {row_key(m) for m in meta}
        without = sum(1 for cid in clip_ids if not has_meta(keys, cid))
        record("meta counts", len(meta) == len(card_ids) and not without,
               f"{len(meta)} rows == {len(card_ids)} ids, "
               f"{without} of {len(clip_ids)} index rows without metadata")
    except Exception as e:
        record("meta counts", False, f"{type(e).__name__}: {e}")

    try:
        man = load(data / "card-index.manifest.json")
        man_ids = man.get("ids") or []
        problems = []
        model, pretrained = man.get("model"), man.get("pretrained")
        if model != CLIP_MODEL_NAME or pretrained != CLIP_PRETRAINED:
            problems.append(f"model {model}/{pretrained}, this build loads "
                            f"{CLIP_MODEL_NAME}/{CLIP_PRETRAINED}")
        if man.get("dim") != CLIP_EMBED_DIM:
            problems.append(f"dim {man.get('dim')}, this build embeds {CLIP_EMBED_DIM}")
        if man.get("rows") != rows:
            problems.append(f"rows {man.get('rows')} vs {rows}")
        if man.get("ids_sha1") != sha1_ids(man_ids):
            problems.append("id list does not match its own hash")
        elif man_ids != clip_ids:
            problems.append("id list differs from clip_card_index.json")
        if man.get("bin_sha1") != sha1_file(bin_path):
            problems.append("bin sha1")
        record("manifest", not problems,
               f"rows, bin sha1, id list and checkpoint "
               f"{CLIP_MODEL_NAME}/{CLIP_PRETRAINED} at {CLIP_EMBED_DIM}d match"
               if not problems else ", ".join(problems))
    except Exception as e:
        record("manifest", False, f"{type(e).__name__}: {e}")

    passed = sum(results)
    print(f"\n{'PASS' if passed == len(results) else 'FAIL'} {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
