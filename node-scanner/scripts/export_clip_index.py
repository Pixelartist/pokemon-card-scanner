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

# L2-normalise rows, then quantise to int16 (scale=32767, better precision than int8)
emb /= np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)
q = np.clip(np.rint(emb * 32767.0), -32767, 32767).astype(np.int16)
(OUT / "card-index.bin").write_bytes(q.tobytes(order="C"))

# Display metadata: clip_card_index.json has name/set/image_url for every indexed card
idx = json.loads((DATA / "clip_card_index.json").read_text())
by_id = {c["card_id"]: {"name": c.get("name", ""), "set": c.get("set_name", "") or c.get("set_code", ""),
                        "number": c.get("number", ""), "rarity": c.get("rarity", ""),
                        # keep /static/ prefix — main app serves those mounts
                        "image": c.get("image_url", "")}
         for c in idx["cards"]}
# image_url can point at data/images/ paths that were never downloaded; the
# clip_images fallback dir (served at /static/clip_images) has many of them.
fallback = 0
missing = 0
for cid, m in by_id.items():
    img = m["image"]
    if img.startswith("/static/"):
        # URL /static/card_images/ serves data/images/ — translate before the disk check
        rel = img[len("/static/"):].replace("card_images/", "images/", 1)
        if not (DATA / rel).is_file():
            alt = DATA / "clip_images" / f"{cid}.jpg"
            if alt.is_file():
                m["image"] = f"/static/clip_images/{cid}.jpg"
                fallback += 1
            else:
                m["image"] = ""  # no local copy anywhere; UI shows placeholder
                missing += 1
print(f"image fallbacks to clip_images: {fallback}, no local copy: {missing}")
ids_list = [str(i) for i in ids]
meta = [by_id.get(i, {"name": i}) for i in ids_list]
(OUT / "cards_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
print(f"meta rows: {len(meta)}, images: {sum(1 for m in meta if m.get('image'))}")
(OUT / "card-ids.json").write_text(json.dumps(ids_list))
print(f"index: {q.shape} int16, {(OUT/'card-index.bin').stat().st_size/1e6:.1f} MB")
