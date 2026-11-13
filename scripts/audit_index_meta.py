#!/usr/bin/env python3
"""How consistent are card-ids.json / cards_meta.json / the actual image files?"""
import json, os, collections

d = "/opt/data/pokemon-card-scanner/data/"
ids = json.load(open(d + "card-ids.json"))
meta = json.load(open(d + "cards_meta.json"))
STATIC = "/opt/data/pokemon-card-scanner/src/static"

exist = mismatch = placeholder = 0
bad_samples = []
for i, (cid, m) in enumerate(zip(ids, meta)):
    img = m.get("image", "")
    if "placeholder" in img:
        placeholder += 1
        continue
    # does the set/number in meta match the set/number in the image filename?
    stem = os.path.splitext(os.path.basename(img))[0]
    set_ok = str(m.get("set", "")).replace(".", "") in stem.replace(".", "").lower()
    num_ok = str(m.get("number", "")).lstrip("0") in stem.split("-")[-1].lstrip("0")
    path = STATIC + img[len("/static"):] if img.startswith("/static") else ""
    if path and os.path.exists(path):
        exist += 1
    if not (set_ok and num_ok):
        mismatch += 1
        if len(bad_samples) < 5:
            bad_samples.append((cid, m.get("set"), m.get("number"), img))

print(f"ids={len(ids)} meta={len(meta)} placeholder={placeholder} file_exists={exist} filename_mismatch={mismatch}")
for s in bad_samples:
    print("  mismatch:", s)
