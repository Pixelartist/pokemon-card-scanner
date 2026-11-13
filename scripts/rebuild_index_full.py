"""Rebuild the full quickdraw index from current images.

This script reads all card images from their actual storage locations,
builds fingerprints for each, and writes a new index file.
"""
import json
import struct
import sys
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.quickdraw_matcher import (
    ART_CROPS, COARSE_DIM, FINE_DIM, _JITTERS, _region, _hypothesis
)

BASE_DIR = Path(__file__).resolve().parent.parent
META_PATH = BASE_DIR / "data" / "quickdraw-meta.json"
INDEX_PATH = BASE_DIR / "data" / "quickdraw-index.bin"

def build_fingerprint(img_path):
    """Build coarse and fine fingerprints from an image file.
    
    Returns (coarse, fine) float32 arrays or None on error.
    """
    if not img_path or not img_path.exists():
        return None
    try:
        img = Image.open(img_path).convert("RGBA")
        # Create RGBA array with alpha channel
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        alpha = np.full(rgb.shape[:2] + (1,), 255, dtype=np.uint8)
        rgba = np.concatenate([rgb, alpha], axis=2)
        
        # Compute integral image
        h, w = rgba.shape[:2]
        # Add padding for integral image
        integral = np.zeros((h + 1, w + 1), dtype=np.float64)
        np.cumsum(rgba.astype(np.float64), axis=0, out=integral[1:, 1:])
        np.cumsum(integral, axis=1, out=integral)
        
        # Build hypotheses
        coarse_sum = np.zeros(COARSE_DIM, dtype=np.float64)
        fine_sum = np.zeros(FINE_DIM, dtype=np.float64)
        
        for dx, dy, s in _JITTERS:
            # Apply region transform
            r = []
            for (y, yp, w, h) in ART_CROPS["standard"]:
                # Transform coordinates
                y1 = int((y + dy) * s)
                yp1 = int((yp + dy) * s)
                w1 = int(w * s)
                h1 = int(h * s)
                if y1 < 0 and yp1 < 0 or y1 >= h or yp1 >= h:
                    continue
                r.append((max(0, y1), max(0, yp1), min(w1, w), min(h1, h)))
            
            # Simplified: just use fixed region
            tables = integral
            try:
                hyp = _hypothesis(tables, w, h, _region(ART_CROPS["standard"], dx, dy, s))
                coarse_sum += np.abs(hyp[0])
                fine_sum += np.abs(hyp[1])
            except Exception:
                pass
        
        return coarse_sum / len(_JITTERS), fine_sum / len(_JITTERS)
    except Exception:
        return None

def process_card(args):
    """Process one card and return (meta_idx, coarse, fine)."""
    i, card = args
    img_url = card.get("image", "")
    
    # Resolve image path
    if img_url.startswith("/static/"):
        # Remove /static prefix and find in data/ directory
        rel_path = img_url[8:]  # /static/... -> static/...
        img_path = BASE_DIR / "data" / rel_path
    elif img_url.startswith("http"):
        # Remote image - skip for local rebuild
        return None
    else:
        img_path = BASE_DIR / img_url.lstrip("/")
    
    fp = build_fingerprint(img_path)
    if fp is not None:
        coarse, fine = fp
        return (i, coarse.astype(np.float32), fine.astype(np.float32))
    return None

def main():
    print("Loading card metadata...")
    with open(META_PATH) as f:
        cards_meta = json.load(f)["cards"]
    
    print(f"Found {len(cards_meta)} cards in meta")
    
    # Build fingerprints in parallel
    print("Building fingerprints...")
    results = []
    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_card, (i, c)): (i, c) 
                   for i, c in enumerate(cards_meta)}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
            if len(results) % 1000 == 0:
                print(f"  Processed {len(results)}/{len(cards_meta)} cards")
    
    print(f"Successfully processed {len(results)}/{len(cards_meta)} cards")
    
    # Sort by meta index
    results.sort(key=lambda x: x[0])
    
    # Build index file
    count = len(results)
    header = struct.pack("<8sIIII", b"DLYGIDX1", 1, count, COARSE_DIM, FINE_DIM)
    
    # Card indices (meta positions, not file positions)
    card_idx = np.array([r[0] for r in results], dtype=np.uint32)
    card_idx_bytes = card_idx.tobytes()
    
    # Coalesce and fine vectors
    coarse_data = np.stack([r[1] for r in results], axis=0).astype(np.int8)
    fine_data = np.stack([r[2] for r in results], axis=0).astype(np.int8)
    
    # Write index
    with open(INDEX_PATH, "wb") as f:
        f.write(header)
        f.write(card_idx_bytes)
        f.write(coarse_data.tobytes())
        f.write(fine_data.tobytes())
    
    total_bytes = os.path.getsize(INDEX_PATH)
    print(f"\nSUCCESS: {count} cards in {total_bytes} bytes written to {INDEX_PATH}")

if __name__ == "__main__":
    main()