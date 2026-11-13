#!/usr/bin/env python3
"""Full rebuild of quickdraw index - write incrementally to temp file."""
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
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC

LUMA = np.array([0.299, 0.587, 0.114])

def _row_weight(size: int) -> np.ndarray:
    y = np.arange(size)[:, None]
    return np.where((y >= size * 0.375) & (y < size * 0.625), 0.2, 1.0)

def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}

def _local_image(card):
    img_path = card.get("image", "")
    if not img_path:
        return None
    
    IMG_PREFIXES = [
        ("/static/german_cards/images/", DATA_DIR / "german_cards" / "images"),
        ("/static/pocket/", DATA_DIR / "images" / "pocket"),
        ("/static/card_images/", DATA_DIR / "images"),
        ("/static/clip_images/", DATA_DIR / "clip_images"),
    ]
    
    for prefix, base in IMG_PREFIXES:
        if prefix in img_path:
            try:
                p = base / img_path.split(prefix, 1)[1]
                return p if p.exists() else None
            except:
                return None
    return None

def _rgba(img):
    """Convert to RGBA with proper transparency handling."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return np.asarray(img)

if __name__ == "__main__":
    with open(META_PATH) as f:
        cards = json.load(f)["cards"]
    
    print(f"Total cards in meta: {len(cards)}")
    
    # Count cards with images first
    valid_count = 0
    for card in cards:
        if _local_image(card) is not None:
            valid_count += 1
    
    print(f"Cards with images: {valid_count}")
    
    # Allocate numpy arrays
    meta_indices = np.zeros(valid_count, dtype=np.uint32)
    coarse_buf = np.zeros((valid_count, 96), dtype=np.int8)
    fine_buf = np.zeros((valid_count, 384), dtype=np.int8)
    
    start = time.time()
    count = 0
    
    for i, card in enumerate(cards):
        p = _local_image(card)
        if p is not None:
            try:
                rgba = _rgba(Image.open(p))
                tables, w, h = _integral(rgba)
                r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
                c, f = _hypothesis(tables, w, h, r)
                meta_indices[count] = i
                coarse_buf[count] = c
                fine_buf[count] = f
                count += 1
            except Exception as e:
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