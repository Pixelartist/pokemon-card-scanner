#!/usr/bin/env python3
"""Recompute quickdraw fingerprints after card artwork changes.

data/quickdraw-index.bin is a flat array of per-card fingerprints keyed by the
card's index in data/quickdraw-meta.json. When art is replaced (e.g. English
fallback swapped for the German scan) the stale row has to be recomputed, or the
scanner keeps comparing photos against artwork it no longer serves.

This mirrors QuickdrawMatcher.build() exactly (same ART_CROPS["standard"]
region, same int8 layout) — scripts/rebuild_index.py fingerprints the full frame
instead, which produces rows the matcher was not built around.

  python scripts/refresh_quickdraw_index.py --manifest data/german_art_sources.json
  python scripts/refresh_quickdraw_index.py --all
"""
import argparse
import json
import struct
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402
from services.quickdraw_matcher import (  # noqa: E402
    COARSE_DIM, FINE_DIM, MAGIC, VERSION, QuickdrawMatcher, _fingerprint, _rgba)

DATA = ROOT / "data"
META_PATH = DATA / "quickdraw-meta.json"
INDEX_PATH = DATA / "quickdraw-index.bin"


def fingerprint(args):
    i, path = args
    try:
        c, f = _fingerprint(_rgba(Image.open(path)))
        return i, c.astype(np.int8).tobytes(), f.astype(np.int8).tobytes()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(DATA / "german_art_sources.json"),
                    help="only refresh cards listed in this source manifest")
    ap.add_argument("--all", action="store_true", help="rebuild every row")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cards = json.loads(META_PATH.read_text())["cards"]
    matcher = QuickdrawMatcher()
    print(f"meta cards: {len(cards)}")

    wanted = None
    if not args.all:
        wanted = set(json.loads(Path(args.manifest).read_text())["cards"])

    ids = {}
    for i, card in enumerate(cards):
        cid = card.get("ptcg_id", "")
        if cid and (wanted is None or cid in wanted):
            p = matcher._local_image(card)
            if p is not None:
                ids[i] = p
    print(f"rows to fingerprint: {len(ids)}")

    buf = INDEX_PATH.read_bytes()
    magic, version, count, cdim, fdim = struct.unpack_from("<8sIIII", buf, 0)
    if (magic, version, cdim, fdim) != (MAGIC, VERSION, COARSE_DIM, FINE_DIM):
        sys.exit("unexpected index header")

    card_idx = np.frombuffer(buf, np.uint32, count, 24).copy()
    art_idx = np.frombuffer(buf, np.uint16, count, 24 + 4 * count).copy()
    base = 24 + 6 * count
    coarse = np.frombuffer(buf, np.int8, count * COARSE_DIM, base) \
        .reshape(count, COARSE_DIM).copy()
    fine = np.frombuffer(buf, np.int8, count * FINE_DIM,
                         base + count * COARSE_DIM).reshape(count, FINE_DIM).copy()

    t0 = time.time()
    done = 0
    new_rows = []
    with Pool(args.workers) as pool:
        for res in pool.imap(fingerprint, sorted(ids.items()), chunksize=100):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(ids)}  {time.time() - t0:.1f}s", flush=True)
            if res is not None:
                new_rows.append(res)

    pos = {int(m): i for i, m in enumerate(card_idx)}
    added = 0
    for meta_i, c, f in new_rows:
        row_c = np.frombuffer(c, np.int8).reshape(COARSE_DIM)
        row_f = np.frombuffer(f, np.int8).reshape(FINE_DIM)
        if meta_i in pos:
            coarse[pos[meta_i]] = row_c
            fine[pos[meta_i]] = row_f
        else:                       # card was previously unindexed
            card_idx = np.append(card_idx, np.uint32(meta_i))
            art_idx = np.append(art_idx, np.uint16(0))
            coarse = np.vstack([coarse, row_c])
            fine = np.vstack([fine, row_f])
            pos[meta_i] = len(card_idx) - 1
            added += 1

    count = len(card_idx)
    tmp = INDEX_PATH.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(struct.pack("<8sIIII", MAGIC, VERSION, count, COARSE_DIM, FINE_DIM))
        fh.write(card_idx.astype(np.uint32).tobytes())
        fh.write(art_idx.astype(np.uint16).tobytes())
        fh.write(coarse.tobytes())
        fh.write(fine.tobytes())
    tmp.replace(INDEX_PATH)
    print(f"SUCCESS: {len(new_rows)} rows refreshed ({added} new), "
          f"{count} cards, {INDEX_PATH.stat().st_size:,} bytes "
          f"in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
