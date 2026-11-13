#!/usr/bin/env python3
"""Add a single German card (Wailmer) to verify the fix works."""
import json
import struct
import numpy as np
from pathlib import Path

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
OLD_IDX = DATA_DIR / "quickdraw-index.bin"
META_PATH = DATA_DIR / "quickdraw-meta.json"

import sys
sys.path.insert(0, str(BASE_DIR / "src"))
from PIL import Image
from services.quickdraw_matcher import _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, MAGIC

# Load existing
with open(OLD_IDX, "rb") as f:
    buf = f.read()
    magic, ver, en_count, cdim, fdim = struct.unpack("<8sIIII", buf[:24])
    old_indices = np.frombuffer(buf, np.uint32, en_count, 24)
    old_coarse = np.frombuffer(buf, np.int8, en_count * cdim, 24 + 4 * en_count).reshape(en_count, cdim)
    old_fine = np.frombuffer(buf, np.int8, en_count * fdim, 24 + 4 * en_count + en_count * cdim).reshape(en_count, fdim)

print(f"Existing English cards: {en_count}")

# Find Wailmer German card position
meta = json.loads(open(META_PATH).read())["cards"]
wailmer_idx = None
for i, card in enumerate(meta):
    if card.get("id") == "de_me05-015":
        wailmer_idx = i
        break

if wailmer_idx is None:
    print("Wailmer card not found in meta!")
    sys.exit(1)

print(f"Wailmer position in meta: {wailmer_idx}")

# Process Wailmer
path = DATA_DIR / "german_cards" / "images" / "de_me05-015.png"
rgba = _rgba(Image.open(path))
tables, w, h = _integral(rgba)
r = {"x": 0.115, "y": 0.182, "w": 0.77, "h": 0.53}
coarse, fine = _hypothesis(tables, w, h, r)

print(f"Wailmer fingerprint computed")

# Check if it already exists (unlikely)
if wailmer_idx in old_indices:
    print("Wailmer already in index!")
else:
    print(f"Wailmer NOT in current index (positions go up to {max(old_indices)})")

# Add Wailmer
german_coarse = coarse.astype(np.int8).reshape(1, -1)
german_fine = fine.astype(np.int8).reshape(1, -1)
german_idx = np.array([wailmer_idx], dtype=np.uint32)

total = en_count + 1
card_idx = np.concatenate([old_indices, german_idx])
combined_coarse = np.vstack([old_coarse, german_coarse])
combined_fine = np.vstack([old_fine, german_fine])

# Write
hdr = struct.pack("<8sIIII", MAGIC, 1, total, COARSE_DIM, FINE_DIM)
tmp = DATA_DIR / "quickdraw-index.bin.new"
with open(tmp, "wb") as f:
    f.write(hdr)
    f.write(card_idx.tobytes())
    f.write(np.zeros(total, dtype=np.uint16).tobytes())
    f.write(combined_coarse.tobytes())
    f.write(combined_fine.tobytes())

tmp.replace(OLD_IDX)
print(f"SUCCESS: Added Wailmer, now {total} cards in {OLD_IDX.stat().st_size:,} bytes")