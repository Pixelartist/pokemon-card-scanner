#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Verify card-index.bin rows align positionally with card-ids.json."""
import json, numpy as np
d = "/opt/data/pokemon-card-scanner/data/"
ids = json.load(open(d + "card-ids.json"))
npz = np.load(d + "clip_embeddings.npz", allow_pickle=True)
npz_ids = [str(x) for x in npz["card_ids"]]
n = len(npz_ids)
print(f"npz cards: {n}, ids: {len(ids)}, bin rows: {__import__('os').path.getsize(d+'card-index.bin')//1024}")
print(f"npz_ids == ids[:n] (positional): {npz_ids == ids[:n]}")
overlap = set(npz_ids) & set(ids[:n])
print(f"set overlap: {len(overlap)} / {n}")
# also check bin embeddings ~ match npz embeddings on a few positions
bin = np.memmap(d + "card-index.bin", dtype=np.int16, mode="r", shape=(n, 512))
emb = npz["embeddings"]
for i in (0, n // 2, n - 1):
    b = np.clip(bin[i].astype(np.float32) / 32767.0, -1, 1)
    e = emb[i].astype(np.float32)
    e = e / (np.linalg.norm(e) + 1e-9)
    print(f"pos {i}: cos(bin_row, npz_row) = {float(b @ e):.4f}")
