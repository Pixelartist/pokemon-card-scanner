"""Build card-hashes.json — browser-local perceptual hash index.

Port of the DCT pHash in local scanner public/perceptual-hash.js
(hashSize=24, freqSize=6, RGB-aware, 575-bit).
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

BASE = Path(__file__).resolve().parent.parent
PUBLIC = BASE / ".worktrees" / "local-scanner" / "node-scanner" / "public"
IMAGES = BASE / "data" / "images" / "official" / "en"

HASH_SIZE = 24
FREQ_SIZE = HASH_SIZE // 4


def _dct_row(x):
    """1-D DCT-II row vector, length n."""
    n = len(x)
    u = np.arange(n)
    k = np.arange(n)
    return ((2.0 / n) * np.cos(np.pi * u[:, None] * (2 * k + 1) / (2 * n)) @ x) * np.where(u == 0, 1.0 / np.sqrt(2), 1.0)


def dct_2d(pixels):
    """2D DCT-II per channel, returns (H,W) float64 array."""
    # Apply row DCT along axis=1 then column DCT along axis=0
    rows = _dct_row(pixels)
    return _dct_row(rows.T)


def perceptual_hash(img):
    # BILINEAR matches the browser canvas resampling (imageSmoothingQuality='high').
    # LANCZOS/NEAREST produce different pixels -> completely different hashes.
    img = img.convert("RGB").resize((HASH_SIZE, HASH_SIZE), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float64)

    # Compute DCT per channel
    dcts = [dct_2d(arr[:, :, ch]) for ch in range(3)]
    
    # Mask: 6x6 freq region, excluding DC
    mask = np.zeros((HASH_SIZE, HASH_SIZE), dtype=bool)
    mask[:FREQ_SIZE, :FREQ_SIZE] = True
    mask[0, 0] = False
    
    # Per-channel averages
    avgs = [dct[mask].mean() for dct in dcts]
    
    # JS interleaves R,G,B per frequency cell (row-major y,x order)
    hash_bits = []
    for y in range(FREQ_SIZE):
        for x in range(FREQ_SIZE):
            if x == 0 and y == 0:
                continue
            pos = y * HASH_SIZE + x
            for ch in range(3):
                hash_bits.append(1 if dcts[ch][y, x] > avgs[ch] else 0)

    return "".join(str(b) for b in hash_bits)


def main():
    ids = json.loads((PUBLIC / "card-ids.json").read_text())
    hashes = {}
    missing = 0
    errors = 0
    for i, card_id in enumerate(ids):
        img_path = IMAGES / f"{card_id}.png"
        if not img_path.exists():
            missing += 1
            continue
        try:
            hashes[card_id] = perceptual_hash(Image.open(img_path))
        except Exception as exc:
            errors += 1
        if (i + 1) % 5000 == 0:
            print(f"  ... {i+1}/{len(ids)} ({len(hashes)} hashed)")
    (PUBLIC / "card-hashes.json").write_text(json.dumps(hashes))
    print(f"Done: {len(hashes)} hashes → {PUBLIC / 'card-hashes.json'}")
    if missing:
        print(f"  Missing images: {missing}")
    if errors:
        print(f"  Errors: {errors}")


if __name__ == "__main__":
    main()