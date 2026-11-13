#!/usr/bin/env python3
"""Parallel build of quickdraw index - processes English and German cards simultaneously."""
import json
import logging
import struct
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

sys_path_patch = False
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from services.quickdraw_matcher import (
    _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC, _JITTERS
)

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def process_card(card):
    """Process a single card, return (index, coarse, fine) or None if no image."""
    from services.quickdraw_matcher import QuickdrawMatcher
    
    m = QuickdrawMatcher()
    p = m._local_image(card)
    if p is None:
        return None
    
    try:
        rgba = _rgba(Image.open(p))
        tables, w, h = _integral(rgba)
        # Single region for speed
        from services.quickdraw_matcher import _region
        r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
        c, f = _hypothesis(tables, w, h, r)
        return (m.cards.index(card), c.astype(np.int8), f.astype(np.int8))
    except Exception as e:
        return None


def build_parallel():
    """Build index using multiprocessing."""
    # Load meta
    cards = json.loads(open(DATA_DIR / "quickdraw-meta.json").read())["cards"]
    logging.info(f"Processing {len(cards)} cards with parallel workers")
    
    t0 = time.time()
    card_indices, coarse_rows, fine_rows = [], [], []
    
    # Process in parallel
    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_card, c): i for i, c in enumerate(cards)}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result is not None:
                idx, c, f = result
                card_indices.append(idx)
                coarse_rows.append(c)
                fine_rows.append(f)
            if (i + 1) % 5000 == 0:
                logging.info(f"Processed {i+1}/{len(cards)} cards, {len(card_indices)} with images")
    
    count = len(card_indices)
    logging.info(f"Built {count} fingerprints in {time.time()-t0:.1f}s")
    
    # Sort by index to maintain order
    sorted_data = sorted(zip(card_indices, coarse_rows, fine_rows), key=lambda x: x[0])
    card_indices = np.array([x[0] for x in sorted_data], dtype=np.uint32)
    coarse_rows = np.array([x[1] for x in sorted_data], dtype=np.int8)
    fine_rows = np.array([x[2] for x in sorted_data], dtype=np.int8)
    
    # Write
    hdr = struct.pack("<8sIIII", MAGIC, 1, count, COARSE_DIM, FINE_DIM)
    tmp = DATA_DIR / "quickdraw-index.tmp"
    with open(tmp, "wb") as f:
        f.write(hdr)
        f.write(card_indices.tobytes())
        f.write(np.zeros(count, dtype=np.uint16).tobytes())
        f.write(coarse_rows.tobytes())
        f.write(fine_rows.tobytes())
    
    tmp.replace(DATA_DIR / "quickdraw-index.bin")
    logging.info(f"SUCCESS: {count} cards in {tmp.stat().st_size:,} bytes")
    return True


if __name__ == "__main__":
    import sys
    build_parallel()