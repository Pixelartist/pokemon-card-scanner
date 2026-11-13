#!/usr/bin/env python3
"""Rebuild full quickdraw index with correct region."""
import json
import struct
import numpy as np
from pathlib import Path
from multiprocessing import Pool
import sys
import time

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
META_PATH = DATA_DIR / "quickdraw-meta.json"
INDEX_PATH = DATA_DIR / "quickdraw-index.bin"

sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC

def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}

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
    for prefix, base in IMG_PREFIXES:
        if prefix in img_path:
            try:
                p = base / img_path.split(prefix, 1)[1]
                return p if p.exists() else None
            except:
                return None
    return None

def process_card(args):
    meta_idx, card = args
    p = _local_image(card)
    if p is None:
        return None
    try:
        rgba = _rgba(Image.open(p))
        tables, w, h = _integral(rgba)
        r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
        c, f = _hypothesis(tables, w, h, r)
        return (meta_idx, c.astype(np.int8).tobytes(), f.astype(np.int8).tobytes())
    except Exception as e:
        return None

if __name__ == "__main__":
    with open(META_PATH) as f:
        cards = json.load(f)["cards"]
    
    print(f"Total cards in meta: {len(cards)}")
    
    # Process in chunks
    CHUNK = 500
    NUM_WORKERS = 4
    results = []
    start = time.time()
    
    for chunk_start in range(0, len(cards), CHUNK * NUM_WORKERS):
        chunk_end = min(chunk_start + CHUNK * NUM_WORKERS, len(cards))
        chunk = [(i, cards[i]) for i in range(chunk_start, chunk_end)]
        
        with Pool(NUM_WORKERS) as pool:
            batch_results = pool.map(process_card, chunk, chunksize=1)
        
        valid = [r for r in batch_results if r is not None]
        results.extend(valid)
        
        elapsed = time.time() - start
        print(f"Progress: {chunk_end}/{len(cards)} cards, {len(results)} with images, {elapsed:.1f}s elapsed")
    
    print(f"Total processed: {len(results)}")
    
    # Sort and write
    results.sort(key=lambda x: x[0])
    meta_indices = np.array([r[0] for r in results], dtype=np.uint32)
    coarse_rows = np.array([np.frombuffer(r[1], np.int8) for r in results], dtype=np.int8)
    fine_rows = np.array([np.frombuffer(r[2], np.int8) for r in results], dtype=np.int8)
    
    count = len(results)
    hdr = struct.pack("<8sIIII", MAGIC, 1, count, COARSE_DIM, FINE_DIM)
    
    tmp_path = INDEX_PATH.with_suffix(".tmp")
    with open(tmp_path, "wb") as f:
        f.write(hdr)
        f.write(meta_indices.tobytes())
        f.write(np.zeros(count, dtype=np.uint16).tobytes())
        f.write(coarse_rows.tobytes())
        f.write(fine_rows.tobytes())
    
    tmp_path.replace(INDEX_PATH)
    print(f"SUCCESS: {count} cards in {INDEX_PATH.stat().st_size:,} bytes")