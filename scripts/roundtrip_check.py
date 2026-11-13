#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Fresh-embed the official image for a card id and compare with its npz row.
If cos ~1.0, index rows are aligned with ids; if not, the index is garbage."""
import sys, json, os
sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
import numpy as np
from services.clip_matcher import get_clip_matcher

m = get_clip_matcher()
assert m.load_index_fast()
m._load_model()
ids = json.load(open("/opt/data/pokemon-card-scanner/data/card-ids.json"))
ci = {c["card_id"]: c for c in json.load(open("/opt/data/pokemon-card-scanner/data/clip_card_index.json"))["cards"]}
mat = m._get_norm_matrix()

def local_image(url):
    if not url or "placeholder" in url:
        return None
    p = "/opt/data/pokemon-card-scanner/src/static" + url[len("/static"):] if url.startswith("/static") else url
    return p if os.path.exists(p) else None

for target in ("ex14-56", "sv08-233", "dp4-17", "pop9-1", "ex14-50"):
    c = ci.get(target)
    if not c:
        print(target, "not in index"); continue
    p = local_image(c.get("image_url", ""))
    i = ids.index(target) if target in ids else None
    print(f"{target}: name={c.get('name')} img={c.get('image_url','')[-40:]} local={'Y' if p else 'N'} row={i}")
    if not p or i is None:
        continue
    emb = np.asarray(m._extract_embedding(open(p, "rb").read()), dtype=np.float32)
    emb /= np.linalg.norm(emb) + 1e-9
    print(f"   cos(fresh_embed(official_image), index_row) = {float(mat[i] @ emb):.4f}")
