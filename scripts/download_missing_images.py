#!/usr/bin/env python3
"""Download card images for cards listed in data/image_url_map.json.

Saves to data/clip_images/{ptcg_id}.jpg (served by the app at
/static/clip_images) resized to max 600x825 JPEG q85.
Resumable: skips ids whose file already exists.
Progress: data/download_images_progress.json
"""
import io
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('imgdl')

PROJECT = Path("/opt/data/pokemon-card-scanner")
OUT_DIR = PROJECT / "data/clip_images"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAP_PATH = PROJECT / "data/image_url_map.json"
PROGRESS = PROJECT / "data/download_images_progress.json"

MAX_W = 600
MAX_H = 825
WORKERS = 8
TIMEOUT = 25

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) pokemon-card-scanner/1.0"

lock = threading.Lock()
stats = {"done": 0, "failed": 0, "skipped": 0, "bytes": 0}


def fetch_one(ptcg_id: str, url: str) -> str:
    out = OUT_DIR / f"{ptcg_id}.jpg"
    if out.exists() and out.stat().st_size > 5000:
        return "skip"
    urls = [url]
    if "assets.tcgdex.net" in url and url.endswith(".png"):
        # JP locales often only serve the /high.png variant
        urls.insert(0, url[: -len(".png")] + "/high.png")
    for u in urls:
        try:
            r = session.get(u, timeout=TIMEOUT)
            if r.status_code != 200 or len(r.content) < 3000:
                continue
            im = Image.open(io.BytesIO(r.content))
            if im.width < 200 or im.height < 280:  # error page / placeholder
                continue
            im = im.convert("RGB")
            im.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
            im.save(out, "JPEG", quality=85, optimize=True)
            return "ok"
        except Exception:
            continue
    return "fail"


def main():
    url_map = json.loads(MAP_PATH.read_text())
    items = sorted(url_map.items())
    log.info(f"{len(items)} images to fetch")

    def work(pair):
        pid, url = pair
        res = fetch_one(pid, url)
        with lock:
            stats[res if res != "ok" else "done"] = stats.get(res if res != "ok" else "done", 0) + 1
            n = sum(stats.values())
            if n % 200 == 0:
                PROGRESS.write_text(json.dumps({**stats, "total": len(items), "ts": time.time()}))
                log.info(f"progress {n}/{len(items)}: {stats}")
        return res

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(work, p) for p in items]
        for _ in as_completed(futures):
            pass

    PROGRESS.write_text(json.dumps({**stats, "total": len(items), "ts": time.time(), "finished": True}))
    log.info(f"finished: {stats}")


if __name__ == "__main__":
    main()
