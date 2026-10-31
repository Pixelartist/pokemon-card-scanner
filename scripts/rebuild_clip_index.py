#!/usr/bin/env python3
"""Rebuild the CLIP card index from the surviving JSON embedding store."""
import json
import time
import numpy as np
from pathlib import Path

DB = Path("/opt/data/pokemon-card-scanner/data")
INDEX_PATH = DB / "./clip_card_index.json"
EMB_JSON = DB / "clip_embeddings.json"
CATALOG = DB / "card_catalog.json"
OUT_NPZ = DB / "clip_embeddings.npz"

# CardEmbedding schema expected by clip_matcher.CardEmbedding.from_dict
FIELDS = ["card_id", "name", "set_code", "number", "embedding", "image_url",
          "content_hash", "set_name", "rarity", "types", "hp", "artist",
          "attacks", "weaknesses", "retreat", "pricing"]


def image_url_for(cid: str) -> str:
    import os
    for rel in (f"tcgdex/en/{cid}.png", f"official/en/{cid}.png",
                f"pkmncards/{cid}.png"):
        if (DB / "images" / rel).is_file():
            return f"/static/card_images/{rel}"
    if (DB / "clip_images" / f"{cid}.jpg").is_file():
        return f"/static/clip_images/{cid}.jpg"
    return ""


def main():
    print("Loading embedding store...")
    store = json.load(open(EMB_JSON))["embeddings"]
    print(f"  {len(store)} embeddings")
    print("Loading catalog...")
    catalog = {}
    for c in json.load(open(CATALOG))["cards"]:
        cid = c.get("id") or c.get("tcgid")
        if cid:
            catalog[cid.lower()] = c
    print(f"  {len(catalog)} catalog entries")

    cards = []
    embs = []
    missing_meta = 0
    for cid, rec in store.items():
        emb = rec.get("embedding")
        if not emb or len(emb) != 512:
            continue
        meta = catalog.get(cid.lower(), {})
        if not meta:
            missing_meta += 1
        cards.append({
            "card_id": cid,
            "name": meta.get("name", cid),
            "set_code": meta.get("set_code") or (cid.split("-")[0] if "-" in cid else ""),
            "number": str(meta.get("number") or (cid.split("-")[-1] if "-" in cid else "")),
            "embedding": None,                     # stored separately in the npz
            "image_url": image_url_for(cid),
            "content_hash": "",
            "set_name": meta.get("set_name", ""),
            "rarity": meta.get("rarity", ""),
            "types": meta.get("types", []),
            "hp": int(meta.get("hp") or 0),
            "artist": meta.get("artist", ""),
            "attacks": meta.get("attacks", []),
            "weaknesses": meta.get("weaknesses", []),
            "retreat": int(meta.get("retreat") or 0),
            "pricing": meta.get("pricing", {}),
        })
        embs.append(emb)

    print(f"  merged {len(cards)} cards ({missing_meta} without catalog metadata)")
    ids = np.array([c["card_id"] for c in cards], dtype="<U64")
    mat = np.array(embs, dtype=np.float32)
    assert mat.shape[0] == len(cards) and mat.shape[1] == 512, mat.shape

    tmp = INDEX_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"cards": cards,
                   "metadata": {"total": len(cards), "updated_at": time.ctime()}}, f)
    tmp.rename(INDEX_PATH)
    np.savez(OUT_NPZ, embeddings=mat, card_ids=ids)
    print(f"Wrote {INDEX_PATH} ({len(cards)} cards) and {OUT_NPZ} {mat.shape}")


if __name__ == "__main__":
    main()
