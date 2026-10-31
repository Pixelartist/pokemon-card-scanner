#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Rebuild card-index.bin from clip_embeddings.npz with proper normalization.

The .bin was built from stale embeddings; English rows (non-de_) were
re-embedded by reembed_english_index.py but the bin wasn't updated.
"""
import json
import sys
import numpy as np
from pathlib import Path

DATA = Path("/opt/data/pokemon-card-scanner/data")
EMB_PATH = DATA / "clip_embeddings.npz"
BIN_PATH = DATA / "card-index.bin"
IDS_PATH = DATA / "card-ids.json"

def main() -> None:
    old = np.load(EMB_PATH, allow_pickle=True)
    ids = [str(i) for i in old["card_ids"]]
    mat = old["embeddings"].astype(np.float32)

    # Normalize all rows to unit length, then quantize to int16
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    normalized = mat / norms
    quantized = np.clip(np.rint(normalized * 32767.0), -32768, 32767).astype(np.int16)

    # Verify
    check_norms = np.linalg.norm(quantized.astype(np.float32) / 32767.0, axis=1)
    print(f"Rows: {len(ids)}, quantized shape: {quantized.shape}")
    print(f"Check norms: min={check_norms.min():.4f} max={check_norms.max():.4f}")

    # Write bin
    quantized.tofile(BIN_PATH)
    print(f"Wrote {BIN_PATH.stat().st_size} bytes")

    # Verify against a known card
    i = ids.index("ex14-56") if "ex14-56" in ids else 0
    print(f"Sample row {i} norm: {check_norms[i]:.4f}")

if __name__ == "__main__":
    main()
