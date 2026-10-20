#!/usr/bin/env python3
"""Compare CLIP encoder outputs: open_clip (cloud) vs onnxruntime (browser-style).

Key insight: open_clip encode_image returns L2-normalised output (unit sphere).
Xenova/transformers.js CLIPModel.get_image_features also returns unit vectors.
If they agree on adversarial queries, the model is identical and any
local-vs-cloud divergence must come from index quantization alone (measured
separately by quant_impact.py).
"""
import io, json, urllib.request
from pathlib import Path
from collections import Counter

import numpy as np
from PIL import Image

import torch
import open_clip

# ---- Cloud encoder (what clip_matcher.py uses) ----
print("Loading open_clip ViT-B-32...")
model, _, preproc = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')

# ---- Load browser-side index (pre-normalized int8, scaled x127) ----
emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
ids = [str(i) for i in emb['card_ids']]
mat = emb['embeddings'].astype(np.float32)
mat /= np.clip(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9, None)
q_idx = np.clip(np.rint(mat * 127.0), -127, 127).astype(np.int8).astype(np.float32)

# ---- Build a small adversarial set: cards from different sets, same species ----
# We'll pick random cards whose embeddings are near each other (collisions).
print("Finding close pairs in index...")
rng = np.random.default_rng(42)
# random pair, then find nearest neighbours in index
sample = rng.choice(len(ids), 500, replace=False)
for seed_i in sample[:30]:
    q = mat[seed_i]
    sims = mat @ q
    top5 = np.argsort(-sims)[:5]
    # check if any top5 card differs from seed_i in set (if set_code available)
    print(f"  seed {ids[seed_i]}: top5 sims {[float(s) for s in sims[top5]]}")