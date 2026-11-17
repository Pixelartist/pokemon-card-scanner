#!/usr/bin/env python3
"""Re-embed stale English CLIP rows using the production clip_matcher encoder.

The English rows in clip_embeddings.npz were built with a different HF
pipeline than the German rows and the current clip_matcher encoder:
a fresh embedding of gym2-1 scores only 0.86 against its own stored row
and the true card ranks #68. Both the cloud /api/scan and the browser
scanner return the same wrong top-1 because the index is wrong.

German rows are already correct (self-sim 1.0000, built with the same
production encoder) and are preserved untouched. Only English rows
(card_id not starting with "de_") are re-embedded, in place, keeping the
existing row order and card_ids array.

Resumable: progress is tracked in clip_reembed_done.txt. On restart the
script skips already-processed rows. Each batch saves the full npz
atomically (tmp + rename) so a crash never corrupts the index.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

DATA = Path("/opt/data/pokemon-card-scanner/data")
EMB_PATH = DATA / "clip_embeddings.npz"
DONE_FILE = DATA / "clip_reembed_done.txt"
BATCH = 16


def image_path_for(card_id: str) -> str | None:
    """Resolve a local image path for a card, matching rebuild_clip_index.py."""
    for rel in (
        f"images/official/en/{card_id}.png",
        f"images/tcgdex/en/{card_id}.png",
        f"images/pkmncards/{card_id}.png",
        f"images/pkmncards/{card_id}.jpg",
        f"clip_images/{card_id}.jpg",
    ):
        p = DATA / rel
        if p.is_file():
            return str(p)
    return None


def main() -> None:
    sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
    from services.clip_matcher import get_clip_matcher

    matcher = get_clip_matcher()
    matcher._load_model()

    old = np.load(EMB_PATH)
    ids = [str(i) for i in old["card_ids"]]
    mat = old["embeddings"].astype(np.float32)

    english_indices = [i for i, cid in enumerate(ids) if not cid.startswith("de_")]
    print(f"{len(english_indices)} English rows to re-embed "
          f"({len(ids) - len(english_indices)} German rows preserved)")

    done_set: set[int] = set()
    if DONE_FILE.exists():
        done_set = {int(x) for x in DONE_FILE.read_text().split() if x}
        print(f"Resuming: {len(done_set)} already done")

    remaining = [i for i in english_indices if i not in done_set]
    if not remaining:
        print("Nothing to do.")
        return

    failed = 0
    t0 = time.time()
    for start in range(0, len(remaining), BATCH):
        batch_idx = remaining[start : start + BATCH]
        paths, valid = [], []
        for i in batch_idx:
            p = image_path_for(ids[i])
            if p:
                paths.append(p)
                valid.append(i)
            else:
                print(f"  no image for {ids[i]}, leaving stale row")

        if not paths:
            for i in batch_idx:
                done_set.add(i)
            continue

        with open(paths[0], "rb") as f:
            _ = f.read()  # warm cache
        batch_bytes = [open(p, "rb").read() for p in paths]
        embs = matcher._embed_batch(batch_bytes)

        for i, emb in zip(valid, embs):
            if emb is None:
                failed += 1
                continue
            mat[i] = np.array(emb, dtype=np.float32)
            done_set.add(i)

        # Atomic save: tmp + rename, same pattern as build_german_index.py
        tmp = EMB_PATH.with_suffix(".tmp.npz")
        np.savez(tmp, embeddings=mat, card_ids=np.array(ids, dtype="<U64"))
        tmp.replace(EMB_PATH)

        DONE_FILE.write_text(" ".join(str(x) for x in sorted(done_set)))

        done = len(done_set)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(english_indices) - done) / rate if rate > 0 else 0
        print(f"  {done}/{len(english_indices)} | {rate:.1f} rows/s | "
              f"ETA {eta/3600:.1f}h | failed={failed}", flush=True)

    print(f"\nDONE: {len(done_set)}/{len(english_indices)} English rows re-embedded, "
          f"{failed} failed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)