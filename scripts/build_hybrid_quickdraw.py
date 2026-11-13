#!/usr/bin/env python3
"""Build hybrid quickdraw index: copy English entries from existing index,
add German entries from scratch. Uses the actual quickdraw_matcher fingerprint.
"""
import json
import logging
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from services.quickdraw_matcher import (
    _rgba, _integral, _hypothesis, ART_CROPS, COARSE_DIM, FINE_DIM, _JITTERS, MAGIC
)

BASE_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = BASE_DIR / "data"
OLD_IDX = DATA_DIR / "quickdraw-index.bin"
NEW_IDX = DATA_DIR / "quickdraw-index.bin"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# Read old English index
with open(OLD_IDX, "rb") as f:
    hdr = f.read(24)
    magic, ver, en_count, coarse_dim, fine_dim = struct.unpack("<8sIIII", hdr)
    logging.info(f"Old index: {en_count} English cards")

    raw_indices = f.read(en_count * 4)
    old_indices = np.frombuffer(raw_indices, np.uint32)
    f.read(en_count * 2)  # skip placeholder
    old_coarse = np.frombuffer(f.read(en_count * coarse_dim), np.int8).reshape(en_count, coarse_dim)
    old_fine = np.frombuffer(f.read(en_count * fine_dim), np.int8).reshape(en_count, fine_dim)

# Meta with ALL cards
meta_cards = json.loads(open(DATA_DIR / "quickdraw-meta.json").read())["cards"]
logging.info(f"Full meta: {len(meta_cards)} cards")

# Build German fingerprints
german_coarse = []
german_fine = []
german_positions = []

def _region(base, dx=0.0, dy=0.0, scale=1.0):
    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
    nw, nh = w * scale, h * scale
    return {"x": x + (w - nw) / 2 + dx, "y": y + (h - nh) / 2 + dy, "w": nw, "h": nh}

german_count = 0
for pos, card in enumerate(meta_cards):
    pid = card.get("id", "")
    if not pid.startswith("de_"):
        continue

    img_url = card.get("image", "")
    if not img_url or "german_cards" not in img_url:
        continue

    path = BASE_DIR / "data" / "german_cards" / "images" / img_url.split("german_cards/images/")[-1]
    if not path.exists():
        continue

    try:
        rgba = _rgba(Image.open(path))
        tables, w, h = _integral(rgba)

        # Single region fingerprint (faster than jitter loop)
        r = _region(ART_CROPS["standard"], 0.0, 0.0, 1.0)
        c, f = _hypothesis(tables, w, h, r)
        german_coarse.append(c.astype(np.int8))
        german_fine.append(f.astype(np.int8))
        german_positions.append(pos)
        german_count += 1
    except Exception as e:
        logging.warning(f"skip {pid}: {e}")

    if german_count % 1000 == 0:
        logging.info(f"built {german_count} German entries")

logging.info(f"German entries: {german_count}")

# Combine
total = en_count + german_count
card_idx = np.concatenate([old_indices, np.array(german_positions, dtype=np.uint32)])
combined_coarse = np.vstack([old_coarse, np.array(german_coarse, dtype=np.int8)])
combined_fine = np.vstack([old_fine, np.array(german_fine, dtype=np.int8)])

# Write
hdr = struct.pack("<8sIIII", MAGIC, 1, total, COARSE_DIM, FINE_DIM)
tmp = NEW_IDX.with_suffix(".tmp")
with open(tmp, "wb") as f:
    f.write(hdr)
    f.write(card_idx.tobytes())
    f.write(np.zeros(total, dtype=np.uint16).tobytes())
    f.write(combined_coarse.tobytes())
    f.write(combined_fine.tobytes())

tmp.replace(NEW_IDX)
logging.info(f"SUCCESS: {total} cards in {NEW_IDX.stat().st_size:,} bytes")