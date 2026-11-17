#!/usr/bin/env python3
"""Build German CLIP index from froyonator/Pokemon-Card-Collector- JSON metadata.

Fills the ~5,300 vintage German cards missing from TCGdex (Base, Jungle, Fossil,
Gym, Neo, e-reader, EX series, etc.) using card metadata and image URLs from the
froyonator repository. Classic-era German cards fall back to English artwork
(which is identical) — modern DE-only sets (SW/Hyper Sets, etc.) use dedicated
de/ CDN images when available.

Usage:
    .venv/bin/python scripts/build_german_index_froyonator.py [--max-cards N]
    Run under nohup/daemon; safe to kill and restart anytime.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")
DB = Path("/opt/data/pokemon-card-scanner/data")
INDEX_PATH = DB / "clip_card_index.json"
EMB_PATH = DB / "clip_embeddings.npz"
DE_IMG_DIR = DB / "german_cards" / "images"
BATCH = 32
DL_WORKERS = 8

GEN_FILES = [
    "gen2", "gen3", "gen4", "gen5", "gen6", "gen7", "gen8", "gen9",
]
FROYONATOR_BASE = "https://raw.githubusercontent.com/froyonator/Pokemon-Card-Collector-/main/public/data/cards/de"


def fetch_gen(gen: str) -> list:
    url = f"{FROYONATOR_BASE}/{gen}.json"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        cards = []
        for _dex, c in data.items():
            cards.extend(c if isinstance(c, list) else [])
        return cards
    except Exception as e:
        print(f"  WARN {gen}: {e}")
        return []


def download_art(image_url: str, out_path: Path) -> bool:
    """Download card image. Tries original URL first, then falls back to .png."""
    if out_path.exists() and out_path.stat().st_size > 5000:
        return True
    try:
        rr = requests.get(image_url, timeout=25, stream=True)
        if rr.status_code == 200 and len(rr.content) > 5000:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_path.with_suffix(".tmp")
            tmp.write_bytes(rr.content)
            tmp.rename(out_path)
            return True
    except Exception as e:
        print(f"    WARN {image_url}: {e}")
    # Try .png fallback if original was .webp
    if image_url.endswith('.webp'):
        png_url = image_url[:-5] + '.png'
        try:
            rr = requests.get(png_url, timeout=25, stream=True)
            if rr.status_code == 200 and len(rr.content) > 5000:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = out_path.with_suffix(".tmp")
                tmp.write_bytes(rr.content)
                tmp.rename(out_path)
                return True
        except Exception as e:
            print(f"    WARN {png_url}: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cards", type=int, default=10000)
    args = ap.parse_args()

    print("Loading current index...")
    data = json.load(open(INDEX_PATH))
    cards = data["cards"]
    indexed = {c["card_id"] for c in cards}
    npz = np.load(EMB_PATH, allow_pickle=True)
    mat = npz["embeddings"]
    ids = npz["card_ids"].astype("<U64")
    print(f"  {len(cards)} cards, matrix {mat.shape}")

    print("Fetching froyonator German metadata...")
    all_cards = {}
    for gen in GEN_FILES:
        batch = fetch_gen(gen)
        for c in batch:
            sid = c.get("setId", "")
            lid = c.get("localId", "")
            key = f"{sid}-{lid}"
            if key not in all_cards:
                all_cards[key] = c
    print(f"  {len(all_cards)} unique German cards from froyonator")

    # Filter: not already indexed
    todo = []
    for key, c in all_cards.items():
        de_id = f"de_{key}"
        if de_id in indexed:
            continue
        hosted = c.get("hostedFullUrl") or c.get("hostedThumbUrl") or ""
        if not hosted:
            continue
        todo.append((key, de_id, c.get("name", ""), c.get("setId", ""),
                      c.get("setName", ""), c.get("localId", ""),
                      c.get("rarity", ""), hosted))
    todo = todo[: args.max_cards]
    print(f"  {len(todo)} German cards to index (new)")

    from services.clip_matcher import get_clip_matcher
    matcher = get_clip_matcher()
    matcher._load_model()

    # Pre-allocate to avoid vstack/concatenate memory churn (8GB cgroup limit)
    n_existing = mat.shape[0]
    n_new = len(todo)
    new_mat = np.empty((n_existing + n_new, 512), dtype=np.float32)
    new_mat[:n_existing] = mat
    new_ids = np.empty(n_existing + n_new, dtype="<U64")
    new_ids[:n_existing] = ids
    mat = new_mat
    ids = new_ids
    del new_mat, new_ids

    def persist():
        tmp = INDEX_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"cards": cards,
                       "metadata": {"total": len(cards), "updated_at": time.ctime()}}, f)
        tmp.rename(INDEX_PATH)
        emb_tmp = EMB_PATH.with_suffix(".tmp.npz")
        np.savez(emb_tmp, embeddings=mat, card_ids=ids)
        emb_tmp.replace(EMB_PATH)

    added = failed = skipped_no_image = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        paths = [DE_IMG_DIR / f"{t[1]}.png" for t in chunk]
        # Download art concurrently
        with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
            oks = list(ex.map(lambda t: download_art(t[7], paths[[p.name for p in paths].index(f"{t[1]}.png")] if f"{t[1]}.png" in [p.name for p in paths] else DE_IMG_DIR / f"{t[1]}.png"), chunk))
        batch_bytes, batch_meta = [], []
        for ok, t, p in zip(oks, chunk, paths):
            if not ok:
                failed += 1
                continue
            batch_bytes.append(p.read_bytes())
            batch_meta.append(t)
        if not batch_bytes:
            continue
        embs = matcher._embed_batch(batch_bytes)
        for emb, t in zip(embs, batch_meta):
            if emb is None:
                failed += 1
                continue
            _, de_id, name, sid, sname, local_id, rarity, _ = t
            cards.append({
                "card_id": de_id, "name": name, "set_code": sid,
                "number": str(local_id), "embedding": None,
                "image_url": f"/static/german_cards/images/{de_id}.png",
                "content_hash": "", "set_name": sname, "rarity": rarity,
                "types": [], "hp": 0, "artist": "", "attacks": [],
                "weaknesses": [], "retreat": 0, "pricing": {},
            })
            mat[n_existing + added] = np.array(emb, dtype=np.float32)
            ids[n_existing + added] = de_id
            added += 1
        persist()
        print(f"  {i + len(chunk)}/{len(todo)} processed | +{added} indexed, {failed} failed", flush=True)

    print(f"DONE: +{added} German cards, {failed} failed. Total: {len(cards)}")


if __name__ == "__main__":
    main()
