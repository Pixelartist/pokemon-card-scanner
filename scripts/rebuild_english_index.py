"""Rebuild English card fingerprints from current images.

The original index was built before the English card images were updated.
This script re-extracts fingerprints for all English cards (non-de_ prefix)
and writes a new index file.
"""
import json
import struct
import sys
import os
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.quickdraw_matcher import (
    _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM,
    _JITTERS, _region, _weighted_sq_rows
)

BASE_DIR = Path(__file__).resolve().parent.parent
META_PATH = BASE_DIR / "data" / "quickdraw-meta.json"
INDEX_PATH = BASE_DIR / "data" / "quickdraw-index.bin"

# Load meta
with open(META_PATH) as f:
    meta_data = json.load(f)
cards_meta = meta_data["cards"]
print(f"Loaded {len(cards_meta)} cards from meta")

# Separate English and German cards
english_cards = [c for c in cards_meta if not c.get("id", "").startswith("de_")]
german_cards = [c for c in cards_meta if c.get("id", "").startswith("de_")]
print(f"English cards: {len(english_cards)}, German cards: {len(german_cards)}")

# Build fingerprints for English cards
def build_fingerprint(card):
    """Build fingerprint from card's image path."""
    img_path = BASE_DIR / card.get("image", "").lstrip("/")
    if not img_path.exists():
        return None
    
    try:
        img = Image.open(img_path).convert("RGBA")
        rgba = np.dstack([
            np.asarray(img.convert("RGB")),
            np.full(img.size[::-1] + (1,), 255, np.uint8)
        ])
        tables, w, h = _integral(rgba)
        hyps = [
            _hypothesis(tables, w, h, _region(ART_CROPS["standard"], dx, dy, s))
            for dx, dy, s in _JITTERS
        ]
        c = np.stack([c for c, _ in hyps])
        f = np.stack([f for _, f in hyps])
        return c, f
    except Exception as e:
        print(f"  ERROR building {card['id']}: {e}")
        return None

# Process English cards
en_fingerprints = []
en_indices = []
for i, card in enumerate(english_cards):
    if i % 1000 == 0:
        print(f"Processing English card {i}/{len(english_cards)}...")
    fp = build_fingerprint(card)
    if fp is not None:
        en_fingerprints.append(fp)
        en_indices.append(i)

print(f"Built {len(en_fingerprints)} English fingerprints")

# Process German cards
de_fingerprints = []
de_indices = []
for i, card in enumerate(german_cards):
    if i % 1000 == 0:
        print(f"Processing German card {i}/{len(german_cards)}...")
    fp = build_fingerprint(card)
    if fp is not None:
        de_fingerprints.append(fp)
        de_indices.append(i)

print(f"Built {len(de_fingerprints)} German fingerprints")

# Combine in order: English first, then German
all_fingerprints = en_fingerprints + de_fingerprints
all_indices = en_indices + [len(english_cards) + i for i in de_indices]

# Build the index file
count = len(all_fingerprints)
coarse_dim = COARSE_DIM
fine_dim = FINE_DIM

# Header: magic(8) + version(4) + count(4) + coarse_dim(4) + fine_dim(4)
header = struct.pack("<8sIIII", b"DLYGIDX1", 1, count, coarse_dim, fine_dim)

# Card indices (uint32)
card_idx_bytes = struct.pack(f"<{count}I", *all_indices)

# Coarse vectors (int8)
coarse_data = np.stack([c for c, _ in all_fingerprints]).astype(np.int8).tobytes()

# Fine vectors (int8)
fine_data = np.stack([f for _, f in all_fingerprints]).astype(np.int8).tobytes()

# Write
with open(INDEX_PATH, "wb") as f:
    f.write(header)
    f.write(card_idx_bytes)
    f.write(coarse_data)
    f.write(fine_data)

total_bytes = os.path.getsize(INDEX_PATH)
print(f"\nSUCCESS: {count} cards in {total_bytes} bytes")
print(f"  English: {len(en_fingerprints)}")
print(f"  German: {len(de_fingerprints)}")