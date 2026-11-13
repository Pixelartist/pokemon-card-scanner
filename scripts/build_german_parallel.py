#!/usr/bin/env python3
"""Fast batch build German quickdraw fingerprints with multiprocessing."""
import json
import struct
import numpy as np
from pathlib import Path
from multiprocessing import Pool
import os

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
OLD_IDX = DATA_DIR / "quickdraw-index.bin"

# Load meta
meta_cards = json.loads(open(DATA_DIR / "quickdraw-meta.json").read())["cards"]
german_cards = [(i, c) for i, c in enumerate(meta_cards) if c.get("id", "").startswith("de_")]
print(f"Total German cards in meta: {len(german_cards)}")

# Load existing English index
import sys
sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC

with open(OLD_IDX, "rb") as f:
    buf = f.read()
    magic, ver, en_count, cdim, fdim = struct.unpack("<8sIIII", buf[:24])
    old_indices = np.frombuffer(buf, np.uint32, en_count, 24)
    old_coarse = np.frombuffer(buf, np.int8, en_count * cdim, 24 + 4 * en_count).reshape(en_count, cdim)
    old_fine = np.frombuffer(buf, np.int8, en_count * fdim, 24 + 4 * en_count + en_count * cdim).reshape(en_count, fdim)

print(f"Existing English cards: {en_count}")

def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}

def process_card(item):
    """Process a single German card - returns (coarse, fine, pos) or None."""
    pos, card = item
    img_url = card.get("image", "")
    if "german_cards" not in img_url:
        return None
    fname = img_url.split("german_cards/images/")[-1]
    path = DATA_DIR / "german_cards" / "images" / fname
    
    if not path.exists():
        return None
    
    try:
        rgba = _rgba(Image.open(path))
        tables, w, h = _integral(rgba)
        r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
        c, f = _hypothesis(tables, w, h, r)
        return (c.astype(np.int8).tobytes(), f.astype(np.int8).tobytes(), pos)
    except Exception as e:
        return None

# Use multiprocessing to speed up
NUM_WORKERS = min(4, os.cpu_count() or 1)

with Pool(NUM_WORKERS) as pool:
    results = pool.map(process_card, german_cards, chunksize=500)

# Filter out None results
valid_results = [r for r in results if r is not None]
german_coarse = [np.frombuffer(r[0], np.int8) for r in valid_results]
german_fine = [np.frombuffer(r[1], np.int8) for r in valid_results]
german_positions = [r[2] for r in valid_results]

print(f"Total German fingerprints: {len(german_coarse)}")

# Combine
total = en_count + len(german_coarse)
card_idx = np.concatenate([old_indices, np.array(german_positions, dtype=np.uint32)])
combined_coarse = np.vstack([old_coarse, np.array(german_coarse, dtype=np.int8)])
combined_fine = np.vstack([old_fine, np.array(german_fine, dtype=np.int8)])

# Write new index
hdr = struct.pack("<8sIIII", MAGIC, 1, total, COARSE_DIM, FINE_DIM)
tmp = DATA_DIR / "quickdraw-index.bin.new"
with open(tmp, "wb") as f:
    f.write(hdr)
    f.write(card_idx.tobytes())
    f.write(np.zeros(total, dtype=np.uint16).tobytes())
    f.write(combined_coarse.tobytes())
    f.write(combined_fine.tobytes())

tmp.replace(OLD_IDX)
print(f"SUCCESS: {total} cards in {OLD_IDX.stat().st_size:,} bytes")