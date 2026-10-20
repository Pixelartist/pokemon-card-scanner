#!/usr/bin/env python3
"""Top-1 agreement: cloud (open_clip fp32) vs browser ONNX variants (fp32/fp16/quantized),
tested on cloud-quality crops: the 312KB Pamamo PNG and degraded versions of it.

Reports, per pipeline, per input: top-1 card, its score, and margin to #2.
"""
import io, sys
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import torch, open_clip
import onnxruntime as ort

DATA = "/opt/data/pokemon-card-scanner/data"
d = np.load(f"{DATA}/clip_embeddings.npz", allow_pickle=True)
ids = [str(x) for x in d["card_ids"]]
M = d["embeddings"].astype(np.float32)
M /= np.clip(np.linalg.norm(M, axis=1, keepdims=True), 1e-9, None)

_, _, preproc = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')
CLOUD = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')[0].eval()

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

def hf_preprocess(img):
    # CLIPFeatureExtractor: bicubic shortest-edge 224, center crop 224, rescale, normalize
    w, h = img.size
    s = 224 / min(w, h)
    r = img.resize((int(round(w * s)), int(round(h * s))), Image.BICUBIC)
    cw, ch = r.size
    l, t = (cw - 224) // 2, (ch - 224) // 2
    a = np.asarray(r.crop((l, t, l + 224, t + 224))).astype(np.float32) / 255.0
    a = (a - MEAN) / STD
    return a.transpose(2, 0, 1)[None].astype(np.float32)

SESSIONS = {}
for name, f in [("fp32", "vision_model.onnx"), ("fp16", "vision_model_fp16.onnx"),
                ("quant", "vision_model_quantized.onnx")]:
    SESSIONS[name] = ort.InferenceSession(f"{DATA}/models/Xenova/clip-vit-base-patch32/onnx/{f}",
                                          providers=["CPUExecutionProvider"])

def onnx_embed(name, img):
    sess = SESSIONS[name]
    x = hf_preprocess(img)
    inp = sess.get_inputs()[0]
    if inp.type == "tensor(float16)":
        x = x.astype(np.float16)
    out = sess.run(None, {inp.name: x})[0][0]
    return out.astype(np.float32)

def cloud_embed(img):
    with torch.no_grad():
        return CLOUD.encode_image(preproc(img).unsqueeze(0)).squeeze(0).numpy().astype(np.float32)

def top3(q):
    sims = M @ (q / np.linalg.norm(q))
    idx = np.argsort(-sims)[:3]
    return [(ids[i], float(sims[i])) for i in idx]

src = sys.argv[1] if len(sys.argv) > 1 else f"{DATA}/scans/scan_20261019_111532_blob"
img0 = Image.open(src).convert("RGB")
print("input:", src, img0.size)

variants = {
    "original": img0,
    "blur+desat": ImageEnhance.Color(ImageEnhance.Contrast(
        img0.filter(ImageFilter.GaussianBlur(1.2))).enhance(0.7)).enhance(0.8),
    "jpeg q60": None,
    "dark": ImageEnhance.Brightness(img0).enhance(0.6),
}
b = io.BytesIO(); img0.save(b, "JPEG", quality=60); b.seek(0)
variants["jpeg q60"] = Image.open(b).convert("RGB")

for vname, img in variants.items():
    print(f"\n== input variant: {vname}")
    ct = top3(cloud_embed(img))
    print(f"  cloud     : {ct[0][0]} {ct[0][1]:.4f} (margin {ct[0][1]-ct[1][1]:+.4f})")
    for mname in ("fp32", "fp16", "quant"):
        t = top3(onnx_embed(mname, img))
        ok = "OK " if t[0][0] == ct[0][0] else "DIFF"
        print(f"  {mname:9s} : {ok} {t[0][0]} {t[0][1]:.4f} (margin {t[0][1]-t[1][1]:+.4f}, cos_q={t[0][1]-ct[0][1]:+.4f})")
