#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Rank a crop against the full CLIP index; show where Meditite lands."""
import sys, time
sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
from services.clip_matcher import get_clip_matcher

m = get_clip_matcher()
assert m.load_index_fast(), "index not loaded"
m._load_model()
crop = open(sys.argv[1], "rb").read()
t0 = time.time()
res = m.match_card(crop, top_k=10)
print(f"{time.time()-t0:.1f}s top10:")
for c in res["candidates"]:
    print(f"  {c['similarity']:.3f} {c['id']} {c.get('name','')}")
# where does ex14-56 rank?
import numpy as np, json
ids = m._card_ids if hasattr(m, "_card_ids") else json.load(open("/opt/data/pokemon-card-scanner/data/card-ids.json"))
mat = m._get_norm_matrix()
emb = m._extract_embedding(crop)
emb = np.asarray(emb, dtype=np.float32); emb /= np.linalg.norm(emb)
sims = mat @ emb
for target in ("en_ex14▷56", "en_ex14-56", "ex14-56"):
    if target in ids:
        rank = int((sims > sims[ids.index(target)]).sum()) + 1
        print(f"{target}: rank {rank}, sim {float(sims[ids.index(target)]):.3f}")
