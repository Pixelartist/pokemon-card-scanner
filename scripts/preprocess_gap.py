"""Quantify preprocessing divergence: cloud (open_clip bicubic+centercrop) vs browser variants.

Runs the Xenova ONNX vision model the browser uses with different pixel pipelines
and compares each output to the stored cloud float32 embedding for the same card.
Cosine ~= 1.0 means the browser would produce the same ranking as the cloud.
"""
from __future__ import annotations
import numpy as np
from PIL import Image
import onnxruntime as ort

DATA = "/opt/data/pokemon-card-scanner/data"
import sys
ONNX = f"{DATA}/models/Xenova/clip-vit-base-patch32/onnx/" + (sys.argv[1] if len(sys.argv)>1 else "vision_model.onnx")
CARD = "de_sv01-075"
IMG = f"{DATA}/german_cards/images/de_sv01-075.png"

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

d = np.load(f"{DATA}/clip_embeddings.npz", allow_pickle=True)
ids = [str(x) for x in d["card_ids"]]
stored = d["embeddings"].astype(np.float32)[ids.index(CARD)]
stored_n = stored / np.linalg.norm(stored)

img = Image.open(IMG).convert("RGB")

def norm(a):
    a = a.astype(np.float32) / 255.0
    a = (a - MEAN) / STD
    return a.transpose(2, 0, 1)[None]

sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
in_name = sess.get_inputs()[0].name

def encode(x):
    out = sess.run(None, {in_name: x})[0]
    return out[0]  # pooler_output

def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

results = {}

# 1) Cloud pipeline: open_clip val transform — BICUBIC resize shortest->224, CENTER CROP 224
w, h = img.size
short = min(w, h)
scale = 224 / short
resized = img.resize((int(round(w * scale)), int(round(h * scale))), Image.BICUBIC)
cw, ch = resized.size
left = (cw - 224) // 2
top = (ch - 224) // 2
crop = resized.crop((left, top, left + 224, top + 224))
results["open_clip bicubic+centercrop"] = encode(norm(np.asarray(crop)))

# 2) HF/Xenova processor default: resize shortest->224 (bicubic per CLIP preprocessor config), center crop
resized_b = img.resize((int(round(w * scale)), int(round(h * scale))), Image.BICUBIC)
results["hf short-edge resize (no crop)"] = encode(norm(np.asarray(
    resized_b.resize((224, 224), Image.BILINEAR))))

# 3) Naive browser: direct BILINEAR squash to 224x224 (no shortest-edge, no crop)
squash_bl = img.resize((224, 224), Image.BILINEAR)
results["naive bilinear squash 224"] = encode(norm(np.asarray(squash_bl)))

# 4) Naive browser with bicubic squash
squash_bc = img.resize((224, 224), Image.BICUBIC)
results["naive bicubic squash 224"] = encode(norm(np.asarray(squash_bc)))

# 5) BILINEAR resize shortest + center crop
resized_bl = img.resize((int(round(w * scale)), int(round(h * scale))), Image.BILINEAR)
crop_bl = resized_bl.crop((left, top, left + 224, top + 224)) if resized_bl.size != (224, 224) else resized_bl
results["bilinear shortest+centercrop"] = encode(norm(np.asarray(crop_bl)))

print(f"card: {CARD}  image size: {img.size}")
for name, vec in results.items():
    print(f"  {name:35s} cos vs cloud embedding = {cosine(vec, stored_n):.4f}")
