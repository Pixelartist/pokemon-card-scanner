#!/usr/bin/env python3
"""Add a single German TCGdex card to the CLIP index under a 'de_<id>' key.

Usage: python scripts/add_german_card.py sm1-116
Downloads the German artwork, embeds it with CLIP, and appends it to
clip_card_index.json + clip_embeddings.npz. Idempotent (skips if present).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
DB = Path("/opt/data/pokemon-card-scanner/data")
INDEX_PATH = DB / "clip_card_index.json"
EMB_PATH = DB / "clip_embeddings.npz"
DE_IMG_DIR = DB / "german_cards" / "images"


def main(card_id: str):
    de_id = f"de_{card_id}"
    data = json.load(open(INDEX_PATH))
    cards = data["cards"]
    if any(c["card_id"] == de_id for c in cards):
        print(f"{de_id} already indexed")
        return

    # Fetch German card metadata (gives the exact image URL)
    r = requests.get(f"https://api.tcgdex.net/v2/de/cards/{card_id}", timeout=15)
    if r.status_code != 200:
        sys.exit(f"TCGdex de lookup failed: HTTP {r.status_code}")
    meta = r.json()
    base_img = meta.get("image", "")  # e.g. https://assets.tcgdex.net/de/sm/sm1/116
    if not base_img:
        sys.exit("No image URL in TCGdex response")

    # Download the German artwork (versioned 'high.png' variant)
    img_path = DE_IMG_DIR / f"{de_id}.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    ok = False
    for url in (f"{base_img}/high.png", f"{base_img}/normal.png", base_img):
        rr = requests.get(url, timeout=20)
        if rr.status_code == 200 and len(rr.content) > 5000:
            img_path.write_bytes(rr.content)
            ok = True
            break
    if not ok:
        sys.exit(f"Could not download art for {card_id}")
    print(f"Art saved: {img_path}")

    # Embed with CLIP using the live matcher class
    from services.clip_matcher import get_clip_matcher
    matcher = get_clip_matcher()
    matcher._load_model()
    emb = matcher._extract_embedding(img_path.read_bytes())
    if emb is None:
        sys.exit("CLIP embedding failed")

    set_info = meta.get("set", {}) or {}
    cards.append({
        "card_id": de_id,
        "name": meta.get("name", card_id),
        "set_code": set_info.get("id", ""),
        "number": str(meta.get("localId", "")),
        "embedding": None,
        "image_url": f"/static/german_cards/images/{de_id}.png",
        "content_hash": "",
        "set_name": set_info.get("name", ""),
        "rarity": meta.get("rarity", ""),
        "types": meta.get("types", []),
        "hp": int(meta.get("hp") or 0),
        "artist": meta.get("illustrator", ""),
        "attacks": meta.get("attacks", []),
        "weaknesses": meta.get("weaknesses", []),
        "retreat": int(meta.get("retreat") or 0),
        "pricing": meta.get("pricing", {}),
    })
    data["metadata"] = {"total": len(cards), "updated_at": time.ctime()}
    tmp = INDEX_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    tmp.rename(INDEX_PATH)

    npz = np.load(EMB_PATH, allow_pickle=True)
    mat = np.vstack([npz["embeddings"], np.array([emb], dtype=np.float32)])
    ids = np.concatenate([npz["card_ids"].astype("<U64"), np.array([de_id], dtype="<U64")])
    emb_tmp = EMB_PATH.with_suffix(".tmp.npz")
    np.savez(emb_tmp, embeddings=mat, card_ids=ids)
    emb_tmp.replace(EMB_PATH)
    print(f"Indexed {de_id}: {meta.get('name')} ({len(cards)} cards total)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sm1-116")
