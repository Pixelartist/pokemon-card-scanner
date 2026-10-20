#!/usr/bin/env python3
"""Score the user's real uploaded photo (rosebud's Pamamo test) against the index,
using cloud pipeline (open_clip fp32) vs browser pipeline (quantized ONNX + int16)."""
import io, sys
import numpy as np
from PIL import Image
import torch, open_clip
import onnxruntime as ort

DATA = "/opt/data/pokemon-card-scanner/data"
d = np.load(f"{DATA}/clip_embeddings.npz", allow_pickle=True)
ids = [str(x) for x in d["card_ids"]]
M = d["embeddings"].astype(np.float32)
M /= np.clip(np.linalg.norm(M, axis=1, keepdims=True), 1e-9, None)
M16 = np.clip(np.rint(M * 32767.0), -32767, 32767).astype(np.float64)

_, _, preproc = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')
_model_file = sys.argv[2] if len(sys.argv) > 2 else "vision_model_quantized.onnx"
sess = ort.InferenceSession(f"{DATA}/models/Xenova/clip-vit-base-patch32/onnx/{_model_file}",
                            providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name

def top5(name, q):
    qn = q / np.linalg.norm(q)
    sims = M @ qn
    idx = np.argsort(-sims)[:5]
    print(f"  [{name}]")
    for i in idx:
        print(f"    {ids[i]:16s} {float(sims[i]):.4f}")
    return ids[idx[0]]

def browser_q(img_bytes):
    # replicate transformers.js CLIP processor: bicubic shortest-edge 224 + center crop 224 + normalize
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    s = 224 / min(w, h)
    r = img.resize((int(round(w * s)), int(round(h * s))), Image.BICUBIC)
    cw, ch = r.size
    l, t = (cw - 224) // 2, (ch - 224) // 2
    a = np.asarray(r.crop((l, t, l + 224, t + 224))).astype(np.float32) / 255.0
    a = (a - np.array([0.48145466, 0.4578275, 0.40821073], np.float32)) / np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
    x = a.transpose(2, 0, 1)[None]
    out = sess.run(None, {iname: x})[0][0]
    return out.astype(np.float32)

CLOUD_MODEL = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')[0].eval()

_paths = sys.argv[1:2] if len(sys.argv) > 2 else sys.argv[1:]
for path in _paths:
    print(path)
    b = open(path, "rb").read()
    img = Image.open(io.BytesIO(b)).convert("RGB")
    with torch.no_grad():
        q_c = CLOUD_MODEL.encode_image(preproc(img).unsqueeze(0)).squeeze(0).numpy().astype(np.float32)
    top5("cloud open_clip fp32", q_c)
    top5("browser quantized-ONNX", browser_q(b))
