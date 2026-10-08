#!/usr/bin/env python3
"""Build the German CLIP sub-index (de_<id> cards) from TCGdex.

- Enumerates all German sets (155 API calls, each returns full card list + image URL).
- Downloads card art (skips already-downloaded files), embeds with CLIP in batches.
- Appends to the MAIN clip_card_index.json + clip_embeddings.npz under 'de_<id>' keys.
- Resumable: skips card_ids already in the index; saves progress every batch.
- Priority order: sv > swsh > sm > everything else (recent, most-scanned first).

Usage: .venv/bin/python scripts/build_german_index.py [--max-cards N] [--sets sm1,sm2,...]
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


def set_priority(set_id: str) -> int:
    if set_id.startswith(("sv", "s6", "s5", "s4", "s3", "s2", "s1")):
        return 0
    if set_id.startswith("swsh") or set_id.startswith("sw"):
        return 1
    if set_id.startswith("sm"):
        return 2
    return 3


def enumerate_sets():
    r = requests.get("https://api.tcgdex.net/v2/de/sets", timeout=30)
    r.raise_for_status()
    detail = []
    for s in r.json():
        sid = s.get("id", "")
        if not sid:
            continue
        try:
            sd = requests.get(f"https://api.tcgdex.net/v2/de/sets/{sid}", timeout=30)
            if sd.status_code == 200:
                detail.append(sd.json())
        except Exception:
            pass
    return detail


def download_art(img_base: str, out_path: Path) -> bool:
    if out_path.exists() and out_path.stat().st_size > 5000:
        return True
    for url in (f"{img_base}/high.png", f"{img_base}/normal.png"):
        try:
            rr = requests.get(url, timeout=25)
            if rr.status_code == 200 and len(rr.content) > 5000:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = out_path.with_suffix(".tmp")
                tmp.write_bytes(rr.content)
                tmp.rename(out_path)
                return True
        except Exception:
            continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cards", type=int, default=100000)
    ap.add_argument("--sets", type=str, default="", help="comma-separated set ids")
    args = ap.parse_args()

    print("Loading current index...")
    data = json.load(open(INDEX_PATH))
    cards = data["cards"]
    indexed = {c["card_id"] for c in cards}
    npz = np.load(EMB_PATH, allow_pickle=True)
    mat = npz["embeddings"]
    ids = npz["card_ids"].astype("<U64")
    print(f"  {len(cards)} cards, matrix {mat.shape}")

    print("Enumerating German sets...")
    only = set(args.sets.split(",")) if args.sets else None
    sets_detail = enumerate_sets()
    todo = []
    for s in sets_detail:
        sid = s.get("id", "")
        if only and sid not in only:
            continue
        sname, serie = s.get("name", ""), (s.get("serie") or {}).get("id", "")
        for c in s.get("cards", []):
            cid, img = c.get("id", ""), c.get("image")
            if not cid or not img:
                continue
            de_id = f"de_{cid}"
            if de_id in indexed:
                continue
            todo.append((set_priority(sid), cid, de_id, c.get("name", ""), c.get("localId", ""),
                         img, sid, sname, serie))
    todo.sort(key=lambda t: t[0])
    todo = todo[: args.max_cards]
    print(f"  {len(todo)} German cards to index")

    from services.clip_matcher import get_clip_matcher
    matcher = get_clip_matcher()
    matcher._load_model()

    def persist():
        tmp = INDEX_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"cards": cards,
                       "metadata": {"total": len(cards), "updated_at": time.ctime()}}, f)
        tmp.rename(INDEX_PATH)
        emb_tmp = EMB_PATH.with_suffix(".tmp.npz")
        np.savez(emb_tmp, embeddings=mat, card_ids=ids)
        emb_tmp.replace(EMB_PATH)

    added = failed = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        # download art concurrently
        paths = [DE_IMG_DIR / f"{t[2]}.png" for t in chunk]
        with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
            oks = list(ex.map(lambda t: download_art(t[5], DE_IMG_DIR / f"{t[2]}.png"), chunk))
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
            _, cid, de_id, name, local_id, _, sid, sname, serie = t
            cards.append({
                "card_id": de_id, "name": name, "set_code": sid,
                "number": str(local_id), "embedding": None,
                "image_url": f"/static/german_cards/images/{de_id}.png",
                "content_hash": "", "set_name": sname, "rarity": "",
                "types": [], "hp": 0, "artist": "", "attacks": [],
                "weaknesses": [], "retreat": 0, "pricing": {},
            })
            mat = np.vstack([mat, np.array([emb], dtype=np.float32)])
            ids = np.concatenate([ids, np.array([de_id], dtype="<U64")])
            added += 1
        persist()
        print(f"  {i + len(chunk)}/{len(todo)} processed | +{added} indexed, {failed} failed", flush=True)

    print(f"DONE: +{added} German cards, {failed} failed. Total: {len(cards)}")


if __name__ == "__main__":
    main()
