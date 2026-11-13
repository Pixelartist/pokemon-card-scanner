#!/usr/bin/env python3
"""Rebuild quickdraw index - chunked pattern proven to work for German cards."""
import json
import struct
import sys
import numpy as np
from pathlib import Path
from multiprocessing import Pool
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

def process_card(args):
    meta_idx, card = args
    img_path = _local_image(card)
    if img_path is None:
        return None
    try:
        rgba = _rgba(Image.open(img_path))
        tables, w, h = _integral(rgba)
        r = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        c, f = _hypothesis(tables, w, h, r)
        return (meta_idx, c.astype(np.int8).tobytes(), f.astype(np.int8).tobytes())
    except Exception:
        return None

def build_index():
    with open(META_PATH) as f:
        cards = json.load(f)["cards"]
    
    print(f"Total cards in meta: {len(cards)}")
    
    all_cards = [(i, c) for i, c in enumerate(cards)]
    total = len(all_cards)
    
    NUM_WORKERS = 4
    CHUNK_SIZE = 500
    
    all_results = []
    start_time = time.time()
    
    for chunk_start in range(0, total, CHUNK_SIZE * NUM_WORKERS):
        chunk_end = min(chunk_start + CHUNK_SIZE * NUM_WORKERS, total)
        chunk = all_cards[chunk_start:chunk_end]
        
        with Pool(NUM_WORKERS) as pool:
            batch_results = pool.map(process_card, chunk, chunksize=50)
        
        valid = [r for r in batch_results if r is not None]
        all_results.extend(valid)
        
        elapsed = time.time() - start_time
        print(f"Progress: {chunk_end}/{total} cards, {len(all_results)} with images, {elapsed:.1f}s elapsed")
    
    print(f"Total processed: {len(all_results)}")
    
    all_results.sort(key=lambda x: x[0])
    meta_indices = np.array([r[0] for r in all_results], dtype=np.uint32)
    coarse_rows = np.array([np.frombuffer(r[1], np.int8) for r in all_results], dtype=np.int8)
    fine_rows = np.array([np.frombuffer(r[2], np.int8) for r in all_results], dtype=np.int8)
    
    count = len(all_results)
    hdr = struct.pack("<8sIIII", MAGIC, 1, count, COARSE_DIM, FINE_DIM)
    tmp = INDEX_PATH.with_suffix(".tmp")
    
    with open(tmp, "wb") as f:
        f.write(hdr)
        f.write(meta_indices.tobytes())
        f.write(np.zeros(count, dtype=np.uint16).tobytes())
        f.write(coarse_rows.tobytes())
        f.write(fine_rows.tobytes())
    
    tmp.replace(INDEX_PATH)
    size = INDEX_PATH.stat().st_size
    print(f"SUCCESS: {count} cards in {size:,} bytes ({time.time()-start_time:.1f}s)")

if __name__ == "__main__":
    build_index()