#!/usr/bin/env python3
"""How consistent are card-ids.json / cards_meta.json / the actual image files?"""
import json, os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATIC = BASE / "src" / "static"

# url prefix -> directory, in the order src/api/main.py mounts them: the image
# trees are served out of data/, so joining a /static URL onto src/static finds
# nothing.
MOUNTS = (
    ("/static/clip_images/", BASE / "data" / "clip_images"),
    ("/static/card_images/", BASE / "data" / "images"),
    ("/static/german_cards/images/", BASE / "data" / "german_cards" / "images"),
    ("/static/pocket/", BASE / "data" / "images" / "pocket"),
    ("/static/tcgdex/", BASE / "data" / "images" / "tcgdex"),
    ("/static/images/", STATIC / "images"),
    ("/static/", STATIC),
)

ids = json.loads((STATIC / "card-ids.json").read_text())
meta = json.loads((STATIC / "cards_meta.json").read_text())


def image_path(url):
    """The file a served card-image URL points at, or None when none serves it."""
    for prefix, directory in MOUNTS:
        if url.startswith(prefix):
            return directory / url[len(prefix):]
    return None

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
    path = image_path(img)
    if path and path.is_file():
        exist += 1
    if not (set_ok and num_ok):
        mismatch += 1
        if len(bad_samples) < 5:
            bad_samples.append((cid, m.get("set"), m.get("number"), img))

print(f"ids={len(ids)} meta={len(meta)} placeholder={placeholder} file_exists={exist} filename_mismatch={mismatch}")
for s in bad_samples:
    print("  mismatch:", s)
