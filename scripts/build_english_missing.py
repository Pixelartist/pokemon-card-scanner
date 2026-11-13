#!/usr/bin/env python3
"""Rebuild English cards only - append to existing index."""
import json
import struct
import numpy as np
from pathlib import Path
from multiprocessing import Pool
import time

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
META_PATH = DATA_DIR / "quickdraw-meta.json"
INDEX_PATH = DATA_DIR / "quickdraw-index.bin"

sys_path_patch = False
import sys
sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC, DATA_DIR as QM_DATA_DIR

# Image path prefixes - must match _IMG_PREFIXES in quickdraw_matcher.py
IMG_PREFIXES = [
    ("/static/german_cards/images/", QM_DATA_DIR / "german_cards" / "images"),
    ("/static/pocket/", QM_DATA_DIR / "images" / "pocket"),
    ("/static/card_images/", QM_DATA_DIR / "images"),
    ("/static/clip_images/", QM_DATA_DIR / "clip_images"),
]

def _local_image(card):
    """Resolve card image path to actual file on disk."""
    img_path = card.get("image", "")
    if not img_path:
        return None
    for prefix, base in IMG_PREFIXES:
        if prefix in img_path:
            try:
                p = base / img_path.split(prefix, 1)[1]
                return p if p.exists() else None
            except Exception:
                return None
    return None

def process_card(args):
    """Process a single card - returns (coarse_bytes, fine_bytes) or None."""
    meta_idx, card = args
    img_path = _local_image(card)
    if img_path is None:
        return None
    
    try:
        rgba = _rgba(Image.open(img_path))
        tables, w, h = _integral(rgba)
        r = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}  # full image region
        c, f = _hypothesis(tables, w, h, r)
        return (c.astype(np.int8).tobytes(), f.astype(np.int8).tobytes())
    except Exception:
        return None

if __name__ == "__main__":
    # Load meta
    with open(META_PATH) as f:
        meta = json.load(f)
    cards = meta["cards"]
    
    # Find English cards that are NOT in current index
    # Current index has 30,565 cards (0-13216 English, 13217-30564 German)
    # So English cards 0-13216 are there, missing 13217-22942
    
    english_cards = []
    for i, card in enumerate(cards):
        if not card['id'].startswith('de_'):
            if i >= 13217:  # Missing from index
                english_cards.append((i, card))
    
    print(f"Total cards in meta: {len(cards)}")
    print(f"English cards missing from index: {len(english_cards)}")
    
    if not english_cards:
        print("No missing cards to process")
        exit(0)
    
    # Process in parallel
    NUM_WORKERS = 4
    CHUNK = 100
    
    results = []
    total = len(english_cards)
    start_time = time.time()
    
    with Pool(NUM_WORKERS) as pool:
        for i, result in enumerate(pool.imap(process_card, english_cards, chunksize=CHUNK)):
            if result is not None:
                results.append((english_cards[i][0], result[0], result[1]))
            if (i + 1) % 1000 == 0:
                elapsed = time.time() - start_time
                print(f"Progress: {i+1}/{total} cards, {len(results)} with images, {elapsed:.1f}s elapsed")
    
    elapsed = time.time() - start_time
    print(f"Processing complete in {elapsed:.1f}s")
    print(f"Total processed: {len(results)} cards with images")
    
    if not results:
        print("ERROR: No cards processed")
        exit(1)
    
    # Read current index header
    with open(INDEX_PATH, "rb") as f:
        hdr = f.read(32)  # 8s + 4*4 = 32 bytes
        old_count = struct.unpack("<8sIIII", hdr)[2]
    
    print(f"Current index has {old_count} cards, adding {len(results)} more")
    
    # Sort by meta index
    results.sort(key=lambda x: x[0])
    meta_indices = np.array([r[0] for r in results], dtype=np.uint32)
    coarse_rows = np.array([np.frombuffer(r[1], np.int8) for r in results], dtype=np.int8)
    fine_rows = np.array([np.frombuffer(r[2], np.int8) for r in results], dtype=np.int8)
    
    # Write new index (full rebuild)
    count = len(results)
    new_count = count  # Just the new cards
    hdr = struct.pack("<8sIIII", MAGIC, 1, new_count, COARSE_DIM, FINE_DIM)
    tmp = INDEX_PATH.with_suffix(".tmp")
    
    with open(tmp, "wb") as f:
        f.write(hdr)
        f.write(meta_indices.tobytes())
        f.write(np.zeros(count, dtype=np.uint16).tobytes())
        f.write(coarse_rows.tobytes())
        f.write(fine_rows.tobytes())
    
    tmp.replace(INDEX_PATH)
    size = INDEX_PATH.stat().st_size
    print(f"SUCCESS: {count} cards in {size:,} bytes")