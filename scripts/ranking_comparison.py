#!/usr/bin/env python3
"""
Measure the real-world ranking difference between:
  A) cloud matcher (open_clip + float32 cosine, full re-rank on 38420)
  B) browser matcher (Xenova ONNX → int8 query, dot/127² on int8 index)
Both using the EXACT same uploaded image bytes.
"""
import io, json, urllib.request, time
from pathlib import Path
from collections import Counter

import numpy as np
import torch
import open_clip
from PIL import Image

# ---- Setup ----
model, _, preproc = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')
emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
ids = [str(i) for i in emb['card_ids']]
mat_f = emb['embeddings'].astype(np.float32)
mat_f /= np.clip(np.linalg.norm(mat_f, axis=1, keepdims=True), 1e-9, None)
q_idx = np.clip(np.rint(mat_f * 127.0), -127, 127).astype(np.int8).astype(np.float32)

TEST = ['gym2-1', 'ex6-18', 'base2-3', 'sm12-180', 'de_sv02-055', 'xy6-47']

# Load ONNX model (same one transformers.js uses)
ONNX = Path('/opt/data/pokemon-card-scanner/data/models/Xenova/clip-vit-base-patch32/onnx/vision_model_quantized.onnx')
if not ONNX.exists():
    # download from HF
    import subprocess, tempfile
    print("Downloading ONNX model to data/models/Xenova/...")
    subprocess.run(['wget', '-q', '--show-progress',
        'https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx',
        '-O', str(ONNX)])
    print("Done")

import onnxruntime as ort
sess = ort.InferenceSession(str(ONNX))
input_name = sess.get_inputs()[0].name
output_name = sess.get_outputs()[0].name
print(f"ONNX: input={input_name} output={output_name}")

def cloud_query(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    t = preproc(img).unsqueeze(0)
    with torch.no_grad():
        q = model.encode_image(t).squeeze().cpu().numpy().astype(np.float32)
    return q

def browser_query(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    # resize to 224x224 (CLIP requirement)
    img = img.resize((224, 224), Image.Resampling.BICUBIC)
    arr = np.array(img, dtype=np.float32)
    # CLIP normalize: (x / 255 - mean) / std, where mean=[0.481,0.458,0.408], std=[0.268,0.269,0.273]
    MEAN = np.array([0.48109378, 0.45795686, 0.40789984])
    STD  = np.array([0.26862954, 0.26130258, 0.27577711])
    q = ((arr / 255.0 - MEAN) / STD).transpose(2, 0, 1).reshape(3, 224, 224)
    # ONNX expects NCHW float32, output is 512-dim
    out = sess.run([output_name], {input_name: q.astype(np.float32).reshape(1, 3, 224, 224)})[0][0]
    # L2-normalise (ONNX doesn't include the final norm layer)
    n = np.linalg.norm(out)
    if n > 1e-9:
        out = out / n
    # quantize to int8 for comparison with browser index
    qq = np.clip(np.rint(out * 127.0), -127, 127).astype(np.int8).astype(np.float32)
    return out, qq

def rank_float(query, top_k=10):
    sims = mat_f @ query
    order = np.argsort(-sims)
    return [(ids[i], float(sims[i])) for i in order[:top_k]]

def rank_int8(q8, top_k=10):
    sims = (q_idx @ q8) / (127.0 * 127.0)
    order = np.argsort(-sims)
    return [(ids[i], float(sims[i])) for i in order[:top_k]]

# ---- Run tests ----
print("\nPer-card results (cloud top-1 vs browser top-1):\n")
for cid in TEST:
    url = f'http://localhost:5005/static/card_images/official/en/{cid}.png'
    try:
        img_bytes = urllib.request.urlopen(url, timeout=10).read()
    except Exception as e:
        print(f'{cid}: skip (fetch failed: {e})'); continue
    if len(img_bytes) < 1000:
        print(f'{cid}: skip (tiny image {len(img_bytes)}b)'); continue

    qf, qq8 = browser_query(img_bytes)

    cloud_top = rank_float(qf, 5)
    bw_top    = rank_int8(qq8, 5)

    agree = cloud_top[0][0] == bw_top[0][0]
    c_sim, b_sim = cloud_top[0][1], bw_top[0][1]
    c_id,  b_id  = cloud_top[0][0], bw_top[0][0]

    print(f"{cid:15s} | cloud top-1: {c_id:15s} {c_sim:.4f} | browser top-1: {b_id:15s} {b_sim:.4f} | agree={agree}")
    if not agree:
        # show browser's rank of the correct card
        correct_rank = next((r+1 for r, (i, _) in enumerate(bw_top) if i == c_id), '?')
        print(f"              | correct card ({c_id}) is rank {correct_rank} in browser")

print("\nDone.")