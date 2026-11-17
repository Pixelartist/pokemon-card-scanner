#!/usr/bin/env python3
"""End-to-end divergence test: run the SAME image through the real cloud
pipeline (open_clip) and the real browser pipeline (Xenova ONNX), then
compare rankings.  This is the only honest way to find out whether the
local scanner is wrong because of the model or because of the index.
"""
import io, json, urllib.request
from pathlib import Path

import numpy as np
import torch
import open_clip
from PIL import Image

# ---- 1. Cloud pipeline: exactly what src/services/clip_matcher.py does ----
model, _, preproc = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')

emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
ids = [str(i) for i in emb['card_ids']]
mat = emb['embeddings'].astype(np.float32)

def cloud_rank(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    t = preproc(img).unsqueeze(0)
    with torch.no_grad():
        q = model.encode_image(t).squeeze().cpu().numpy().astype(np.float32)
    norms = np.linalg.norm(mat, axis=1)
    qn = np.linalg.norm(q) + 1e-9
    sims = mat @ q / (norms * qn + 1e-9)
    order = np.argsort(-sims)
    return order, sims

# ---- 2. Browser pipeline: Xenova ONNX, the model the page actually loads ----
import onnxruntime as ort
from transformers import CLIPProcessor, CLIPModel

# find the ONNX model dir
for cand in [
    '/opt/data/pokemon-card-scanner/data/models/Xenova/clip-vit-base-patch32',
    '/opt/data/pokemon-card-scanner/.worktrees/local-scanner/node-scanner/public/models/Xenova/clip-vit-base-patch32',
]:
    p = Path(cand)
    if p.is_dir():
        MODEL_DIR = p
        break
else:
    MODEL_DIR = None

if MODEL_DIR is None:
    print('NO LOCAL MODEL DIR — browser downloads from HF CDN; cannot replicate locally')
else:
    print('MODEL DIR:', MODEL_DIR)
    sess = ort.InferenceSession(str(MODEL_DIR / 'onnx' / 'vision_model_quantized.onnx'))
    proc = CLIPProcessor.from_pretrained(str(MODEL_DIR))
    clip_model = CLIPModel.from_pretrained(str(MODEL_DIR))

    def browser_rank(img_bytes):
        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        inputs = proc(images=img, return_tensors='pt')
        with torch.no_grad():
            q = clip_model.get_image_features(**inputs).squeeze().cpu().numpy().astype(np.float32)
        q /= np.linalg.norm(q) + 1e-9
        # int8 index path
        q8 = np.clip(np.rint(q * 127.0), -127, 127).astype(np.int8).astype(np.float32)
        idx = np.fromfile(
            '/opt/data/pokemon-card-scanner/.worktrees/local-scanner/node-scanner/public/card-index.bin',
            dtype=np.int8).reshape(38420, 512).astype(np.float32)
        sims = (idx @ q8) / (127.0 * 127.0)
        order = np.argsort(-sims)
        return order, sims

# ---- 3. Test cards ----
TEST = {
    'gym2-1': 'Blaine\'s Arcanine',
    'ex6-18': 'Arcanine',
    'base2-3': 'Flareon',
    'gym1-35': 'Blaine\'s Growlithe',
    'base1-12': 'Ninetales',
}
for cid, expected in TEST.items():
    url = f'http://localhost:5005/static/card_images/official/en/{cid}.png'
    try:
        img = urllib.request.urlopen(url, timeout=10).read()
    except Exception as e:
        print(f'{cid}: image fetch failed {e}')
        continue
    of, sf = cloud_rank(img)
    c_top = ids[of[0]]
    print(f'{cid}: cloud top-1 = {c_top} (expect {expected})  sim={sf[of[0]]:.3f}')
    if MODEL_DIR:
        ob, sb = browser_rank(img)
        b_top = ids[ob[0]]
        same = c_top == b_top
        overlap = len(set(of[:5].tolist()) & set(ob[:5].tolist()))
        print(f'   browser top-1 = {b_top}  same={same}  top-5 overlap={overlap}/5  sim={sb[ob[0]]:.3f}')