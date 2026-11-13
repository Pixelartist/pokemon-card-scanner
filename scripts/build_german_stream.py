#!/usr/bin/env python3
"""Stream build German fingerprints - write intermediate results to avoid OOM."""
import json
import struct
import numpy as np
from pathlib import Path

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
OLD_IDX = DATA_DIR / "quickdraw-index.bin"

import sys
sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC

# Load existing English index
with open(OLD_IDX, "rb") as f:
    buf = f.read()
    magic, ver, en_count, cdim, fdim = struct.unpack("<8sIIII", buf[:24])
    old_indices = np.frombuffer(buf, np.uint32, en_count, 24)
    old_coarse = np.frombuffer(buf, np.int8, en_count * cdim, 24 + 4 * en_count).reshape(en_count, cdim)
    old_fine = np.frombuffer(buf, np.int8, en_count * fdim, 24 + 4 * en_count + en_count * cdim).reshape(en_count, fdim)

print(f"Existing English cards: {en_count}")

# Load German card candidates
meta_cards = json.loads(open(DATA_DIR / "quickdraw-meta.json").read())["cards"]
german_cards = [(i, c) for i, c in enumerate(meta_cards) if c.get("id", "").startswith("de_")]
print(f"Total German candidates in meta: {len(german_cards)}")

def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}

# Process and write incrementally
german_indices = []
german_coarse_list = []
german_fine_list = []
processed = 0
skipped = 0

for pos, card in german_cards:
    img_url = card.get("image", "")
    if "german_cards" not in img_url:
        skipped += 1
        continue
    fname = img_url.split("german_cards/images/")[-1]
    path = DATA_DIR / "german_cards" / "images" / fname
    
    if not path.exists():
        skipped += 1
        continue
    
    try:
        rgba = _rgba(Image.open(path))
        tables, w, h = _integral(rgba)
        r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
        c, f = _hypothesis(tables, w, h, r)
        german_indices.append(pos)
        german_coarse_list.append(c.astype(np.int8))
        german_fine_list.append(f.astype(np.int8))
        processed += 1
    except Exception as e:
        skipped += 1
        if skipped % 100 == 0:
            print(f"Skip {skipped}: {e}")

print(f"Processed {processed} German cards, skipped {skipped}")

# Combine
german_coarse = np.array(german_coarse_list, dtype=np.int8)
german_fine = np.array(german_fine_list, dtype=np.int8)
german_indices_arr = np.array(german_indices, dtype=np.uint32)

total = en_count + len(german_indices)
card_idx = np.concatenate([old_indices, german_indices_arr])
combined_coarse = np.vstack([old_coarse, german_coarse])
combined_fine = np.vstack([old_fine, german_fine])

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