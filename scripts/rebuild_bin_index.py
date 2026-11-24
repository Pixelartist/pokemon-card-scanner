#!/usr/bin/env python3
"""Rebuild card-index.bin from clip_embeddings.npz with proper normalization.

The bin is raw int16 with no header, so the rebuild also rewrites
card-index.manifest.json: nothing else records which ids and which checkpoint
its rows belong to, and without it load_index_fast refuses the bin this script
just wrote.

    scripts/rebuild_bin_index.py [data-dir]

The directory defaults to this checkout's data/.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "data"
EMB_PATH = DATA / "clip_embeddings.npz"
BIN_PATH = DATA / "card-index.bin"
MANIFEST = ROOT / "scripts" / "write_index_manifest.py"


def main() -> None:
    if not EMB_PATH.exists():
        sys.exit(f"{EMB_PATH} is missing — build the index once in the app so it "
                 f"writes the .npz, or pass the directory that holds it")
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
    print(f"Wrote {BIN_PATH} ({BIN_PATH.stat().st_size} bytes)")

    # The manifest is what lets the loader trust these rows: same script, same
    # constants, so a rebuild cannot leave the two disagreeing.
    subprocess.run([sys.executable, str(MANIFEST), str(DATA)], check=True)


if __name__ == "__main__":
    main()
