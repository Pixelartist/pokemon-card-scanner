#!/usr/bin/env python3
"""Record the id list card-index.bin was built from.

The bin is raw int16 with no header, so how its rows map to cards exists only in
the order clip_card_index.json happened to be in when it was built. The loader
names rows from that list by position, which means rebuilding either file alone
makes every scan report the wrong card with no error anywhere. This manifest
pins the ordered ids and both hashes; load_index_fast refuses to load when they
disagree.

Run it after building or replacing card-index.bin. Rerunnable and idempotent.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BIN = DATA / "card-index.bin"
IDX = DATA / "clip_card_index.json"
OUT = DATA / "card-index.manifest.json"

# The checkpoint comes from the loader, not a copy here: the whole point of the
# manifest is that the loader can compare what it loads with what built the bin.
sys.path.insert(0, str(ROOT / "src"))
from services.clip_matcher import (  # noqa: E402
    CLIP_EMBED_DIM, CLIP_MODEL_NAME, CLIP_PRETRAINED,
)

ITEMSIZE = 2  # card-index.bin is int16
DIM = CLIP_EMBED_DIM


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_ids(ids):
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()


def write_atomic(path, text, owner_of):
    """Replace in one step, keeping the owner the neighbouring index file has."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            geteuid = getattr(os, "geteuid", None)
            if geteuid and geteuid() == 0:
                st = owner_of.stat()
                os.fchown(f.fileno(), st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main():
    size = BIN.stat().st_size
    rows, rem = divmod(size, DIM * ITEMSIZE)
    if rem:
        sys.exit(f"{BIN.name} is {size} bytes, not a whole number of {DIM}x{DTYPE} rows")
    cards = json.loads(IDX.read_text(encoding="utf-8"))["cards"]
    ids = [c["card_id"] for c in cards if c.get("card_id")]
    if len(ids) != rows:
        sys.exit(f"{IDX.name} has {len(ids)} ids but the bin has {rows} rows — "
                 f"rebuild one of them before recording a manifest")
    if len(set(ids)) != len(ids):
        sys.exit(f"{IDX.name} repeats an id, so rows cannot be named unambiguously")

    # Only what a reader uses: the checkpoint and id list the rows belong to,
    # both hashes, and when it was written. size is implied by rows and checked
    # against the file, dtype is implied by the int16 memmap.
    manifest = {
        "model": CLIP_MODEL_NAME,
        "pretrained": CLIP_PRETRAINED,
        "dim": DIM,
        "rows": rows,
        "bin_sha1": sha1_file(BIN),
        "ids_sha1": sha1_ids(ids),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ids": ids,
    }
    write_atomic(OUT, json.dumps(manifest, ensure_ascii=False, indent=1), BIN)
    print(f"wrote {OUT.relative_to(ROOT)}: {rows} ids, bin {size} bytes "
          f"sha1 {manifest['bin_sha1'][:12]}, ids sha1 {manifest['ids_sha1'][:12]}, "
          f"model {CLIP_MODEL_NAME} ({CLIP_PRETRAINED}) at {DIM}d")


if __name__ == "__main__":
    main()
