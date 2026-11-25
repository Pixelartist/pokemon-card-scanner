#!/usr/bin/env python3
"""Round-trip check: embed a card's own artwork and compare it with its index row.

Each target is the artwork the index itself records for that row, mapped
through the same mounts the app serves it from. The card is embedded fresh; its
own row has to come out on top, which is what breaks when the bin, the id list
or the checkpoint drift apart.

One target is taken from the first, middle and last third of the rows so a
rotation anywhere shows up, and a third with no artwork in reach is reported
rather than passed over. Finding nothing at all is a failure, not a pass.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
import numpy as np

from services.clip_matcher import get_clip_matcher

DATA = str(BASE / "data")
# url prefix -> directory, as src/api/main.py mounts them
MOUNTS = (
    ("/static/card_images/", "images"),
    ("/static/tcgdex/", "images/tcgdex"),
    ("/static/pocket/", "images/pocket"),
    ("/static/german_cards/images/", "german_cards/images"),
)
PROBE = 200  # rows to look at per third: the image tree is checked one file at a time


def image_path(url):
    """The file behind a served card image URL, or None when there is none."""
    for prefix, sub in MOUNTS:
        if url and url.startswith(prefix):
            path = os.path.join(DATA, sub, url[len(prefix):])
            return path if os.path.isfile(path) else None
    return None


def main():
    m = get_clip_matcher()
    assert m.load_index_fast(), "index not loaded"
    m._load_model()
    mat = m._get_norm_matrix()
    ids = [c.card_id for c in m.card_list]
    urls = [c.get("image_url") or "" for c in m._read_clip_index()]

    n = len(ids)
    targets, empty = [], []
    for start in (0, n // 3, 2 * n // 3):
        for i in range(start, min(start + PROBE, n)):
            path = image_path(urls[i]) if i < len(urls) else None
            if path:
                targets.append((i, path))
                break
        else:
            empty.append(start)
    if not targets:
        sys.exit("no card image found under the mounted image trees — nothing to compare")
    if empty:
        print(f"note: no artwork in the first {PROBE} rows at offsets {empty}")

    failed = 0
    for i, path in targets:
        emb = np.asarray(m._extract_embedding(open(path, "rb").read()), dtype=np.float32)
        emb /= np.linalg.norm(emb) + 1e-9
        sims = mat @ emb
        top = int(np.argmax(sims))
        ok = top == i
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {ids[i]}: own row {float(mat[i] @ emb):.3f}, "
              f"top1 {ids[top]} {float(sims[top]):.3f}")
    print(f"\n{len(targets) - failed}/{len(targets)} targets ranked their own row first")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
