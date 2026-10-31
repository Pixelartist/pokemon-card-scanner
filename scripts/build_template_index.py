#!/usr/bin/env python
"""Build data/template-index.bin: L2-normalised grayscale templates (96x132, float16)
for every indexed card that has a local image, + data/template-rows.json (row -> card_list index).
Query-time matching is one matmul; see CLIPCardMatcher.match_template."""
import json, os, sys, time
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from services.clip_matcher import get_clip_matcher

W, H = 96, 132
D = os.path.join(ROOT, "data")

def prep(img):
    a = np.asarray(img.convert("L").resize((W, H)), dtype=np.float32)
    a -= a.mean()
    n = np.linalg.norm(a)
    return a / n if n > 0 else a

def card_img(cid):
    for p in (os.path.join(D, "german_cards/images", cid + ".png"),
              os.path.join(D, "images/official/en", cid + ".png"),
              os.path.join(D, "images/official/en", cid + ".jpg")):
        if os.path.exists(p):
            return p
    return None

def main():
    m = get_clip_matcher()
    if not m.load_index_fast():
        print("index load failed"); return 1
    rows, card_idx = [], []
    t0 = time.time()
    for i, c in enumerate(m.card_list):
        p = card_img(c.card_id)
        if not p:
            continue
        try:
            rows.append(prep(Image.open(p)))
        except Exception:
            continue
        card_idx.append(i)
    T = np.stack(rows).astype(np.float16)
    T.tofile(os.path.join(D, "template-index.bin"))
    json.dump(card_idx, open(os.path.join(D, "template-rows.json"), "w"))
    print(f"templates: {T.shape} in {time.time()-t0:.0f}s -> template-index.bin")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
