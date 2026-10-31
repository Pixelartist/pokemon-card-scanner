#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Round-trip check: embed a known card image, compare with its index row."""
import sys, time
sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
from services.clip_matcher import get_clip_matcher
import numpy as np, json, os

m = get_clip_matcher()
assert m.load_index_fast()
m._load_model()
ids = m.card_ids if hasattr(m, "card_ids") else json.load(open("/opt/data/pokemon-card-scanner/data/card-ids.json"))
meta = json.load(open("/opt/data/pokemon-card-scanner/data/cards_meta.json"))
mat = m._get_norm_matrix()

for target in ["ex14-56", "sv08-233", "dp4-17"]:
    i = ids.index(target)
    img_rel = meta[i].get("image", "")
    p = "/opt/data/pokemon-card-scanner/src/static" + img_rel.lstrip("/") if img_rel.startswith("/static") else img_rel
    ok = os.path.exists(p)
    print(f"{target}: image={img_rel} exists={ok}")
    if not ok:
        continue
    emb = np.asarray(m._extract_embedding(open(p, "rb").read()), dtype=np.float32)
    emb /= np.linalg.norm(emb) + 1e-9
    cos_own = float(mat[i] @ emb)
    sims = mat @ emb
    top = np.argsort(-sims)[:3]
    print(f"  cos(index_row, fresh_embed) = {cos_own:.3f}; top1={ids[top[0]]} sim={sims[top[0]]:.3f}")
