#!/usr/bin/env python3
"""Download all TCGdex card images using the correct URL pattern.

The image URL from the card API is the full base URL (e.g., 
https://assets.tcgdex.net/en/base/base4/1). Append /high.webp to get the
high-resolution image. The set code in the card ID (e.g., "base4-1") is
NOT the same as the path prefix in the image URL (e.g., "base/base4").
"""
import os, sys, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_DIR = '/opt/data/pokemon-card-scanner/data/clip_images'
os.makedirs(CACHE_DIR, exist_ok=True)

print("Fetching TCGdex catalog...")
r = requests.get("https://api.tcgdex.net/v2/en/cards", timeout=60)
all_cards = r.json()
print(f"Got {len(all_cards)} cards")

# Build download tasks using the image URL from each card
tasks = []
for card in all_cards:
    img_base = card.get("image", "")
    if not img_base:
        continue
    card_id = card.get("id", "")
    safe_id = card_id.replace("/", "_").replace("!", "")
    img_path = os.path.join(CACHE_DIR, f"{safe_id}.jpg")
    if os.path.exists(img_path):
        continue
    url = f"{img_base}/high.webp"
    tasks.append((url, img_path, card_id))

print(f"Need to download {len(tasks)} images (already cached: {len(all_cards) - len(tasks)})")

def download(task):
    url, path, card_id = task
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            with open(path, 'wb') as f:
                f.write(r.content)
            return True
    except:
        pass
    return False

start = time.time()
downloaded = 0
failed = 0
with ThreadPoolExecutor(max_workers=20) as ex:
    futures = {ex.submit(download, t): t for t in tasks}
    for i, f in enumerate(as_completed(futures)):
        if f.result():
            downloaded += 1
        else:
            failed += 1
        if (i+1) % 1000 == 0:
            elapsed = time.time() - start
            rate = (i+1) / elapsed
            eta = (len(tasks) - i - 1) / rate if rate > 0 else 0
            print(f"  {i+1}/{len(tasks)} ({rate:.1f}/s, ETA {eta:.0f}s, failed={failed})")

elapsed = time.time() - start
print(f"\nDone! Downloaded {downloaded}, failed {failed} in {elapsed:.0f}s")
print(f"Total cached: {len(os.listdir(CACHE_DIR))}")