#!/usr/bin/env python3
"""Rebuild quickdraw index - write directly to file, no numpy arrays for results."""
import json
import struct
import sys
import numpy as np
from pathlib import Path
import time

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
META_PATH = DATA_DIR / "quickdraw-meta.json"
INDEX_PATH = DATA_DIR / "quickdraw-index.bin"

sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, COARSE_DIM, FINE_DIM, MAGIC

IMG_PREFIXES = [
    ("/static/german_cards/images/", DATA_DIR / "german_cards" / "images"),
    ("/static/pocket/", DATA_DIR / "images" / "pocket"),
    ("/static/card_images/", DATA_DIR / "images"),
    ("/static/clip_images/", DATA_DIR / "clip_images"),
]

def _local_image(card):
    img_path = card.get("image", "")
    if not img_path:
        return None
    for prefix, dir_base in IMG_PREFIXES:
        if prefix in img_path:
            try:
                p = dir_base / img_path.split(prefix, 1)[1]
                return p if p.exists() else None
            except:
                return None
    return None

if __name__ == "__main__":
    with open(META_PATH) as f:
        cards = json.load(f)["cards"]
    
    print(f"Total cards in meta: {len(cards)}")
    
    # First pass: just count valid images
    valid_count = 0
    for card in cards:
        if _local_image(card) is not None:
            valid_count += 1
    
    print(f"Cards with images: {valid_count}")
    
    # Pre-allocate buffers
    meta_indices = np.zeros(valid_count, dtype=np.uint32)
    coarse_buf = np.zeros((valid_count, 96), dtype=np.int8)  # 96 = COARSE_DIM
    fine_buf = np.zeros((valid_count, 384), dtype=np.int8)  # 384 = FINE_DIM
    
    start = time.time()
    count = 0
    
    for i, card in enumerate(cards):
        p = _local_image(card)
        if p is not None:
            try:
                rgba = _rgba(Image.open(p))
                tables, w, h = _integral(rgba)
                r = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
                c, f = _hypothesis(tables, w, h, r)
                meta_indices[count] = i
                coarse_buf[count] = c
                fine_buf[count] = f
                count += 1
            except:
                pass
        
        if (i + 1) % 5000 == 0:
            elapsed = time.time() - start
            print(f"Progress: {i+1}/{len(cards)} cards, {count} with images, {elapsed:.1f}s elapsed")
    
    elapsed = time.time() - start
    print(f"Processing complete in {elapsed:.1f}s")
    
    # Write final index
    hdr = struct.pack("<8sIIII", MAGIC, 1, count, COARSE_DIM, FINE_DIM)
    tmp = INDEX_PATH.with_suffix(".tmp")
    
    with open(tmp, "wb") as f:
        f.write(hdr)
        f.write(meta_indices.tobytes())
        f.write(np.zeros(count, dtype=np.uint16).tobytes())
        f.write(coarse_buf.tobytes())
        f.write(fine_buf.tobytes())
    
    tmp.replace(INDEX_PATH)
    size = INDEX_PATH.stat().st_size
    print(f"SUCCESS: {count} cards in {size:,} bytes")