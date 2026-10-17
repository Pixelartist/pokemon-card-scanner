#!/usr/bin/env python3
"""Export the CLIP card index for the browser matcher.

Writes card-index.bin (int8, rows L2-normalised x127, row-major 38420x512)
and cards_meta.json (card_id + display fields) into node-scanner/public/.
The vector space is OpenAI CLIP ViT-B/32 — identical to what
Xenova/clip-vit-base-patch32 (transformers.js) produces in the browser.
"""
import json
import os
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # node-scanner/
OUT = ROOT / "public"
# data/ is gitignored — read from the main checkout (override with PCS_DATA)
DATA = Path(os.environ.get("PCS_DATA", "/opt/data/pokemon-card-scanner/data"))

emb = np.load(DATA / "clip_embeddings.npz")["embeddings"].astype(np.float32)
ids = np.load(DATA / "clip_embeddings.npz")["card_ids"]

# L2-normalise rows, then quantise to int8
emb /= np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)
q = np.clip(np.rint(emb * 127.0), -127, 127).astype(np.int8)

(OUT / "card-index.bin").write_bytes(q.tobytes(order="C"))

# Display metadata: clip_card_index.json has name/set/image_url for every indexed card
idx = json.loads((DATA / "clip_card_index.json").read_text())
by_id = {c["card_id"]: {"name": c.get("name", ""), "set": c.get("set_name", "") or c.get("set_code", ""),
                        "number": c.get("number", ""), "rarity": c.get("rarity", ""),
                        # /static/x -> served at /x by our static server
                        "image": c.get("image_url", "").replace("/static/", "/")}
         for c in idx["cards"]}
ids_list = [str(i) for i in ids]
meta = [by_id.get(i, {"name": i}) for i in ids_list]
(OUT / "cards_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
print(f"meta rows: {len(meta)}, images: {sum(1 for m in meta if m.get('image'))}")
(OUT / "card-ids.json").write_text(json.dumps(ids_list))
print(f"index: {q.shape} int8, {(OUT/'card-index.bin').stat().st_size/1e6:.1f} MB")
