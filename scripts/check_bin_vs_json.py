#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Is card-index.bin the npz embeddings scaled to int16? (positional)"""
import numpy as np
d = "/opt/data/pokemon-card-scanner/data/"
npz = np.load(d + "clip_embeddings.npz", allow_pickle=True)
emb = npz["embeddings"].astype(np.float32)
n = emb.shape[0]
norms = np.linalg.norm(emb[:50], axis=1)
print("npz:", emb.shape, "first-50 row norms:", norms.min(), "-", norms.max())
b = np.memmap(d + "card-index.bin", dtype=np.int16, mode="r", shape=(n, 512))
for i in (0, 100, n // 2, n - 1):
    bv = b[i].astype(np.float32) / 32767.0
    ev = emb[i] / (np.linalg.norm(emb[i]) + 1e-9)
    print(f"row {i}: cos(bin_row, npz_row) = {float(bv @ ev):.4f}")
