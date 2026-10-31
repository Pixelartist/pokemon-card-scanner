#!/usr/bin/env python3
"""Download missing TCG Pocket images from the repo."""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path('/opt/data/pokemon-card-scanner/data')
REPO_CARDS_PATH = DATA_DIR / 'repo_cards.json'
IMAGE_DIR = DATA_DIR / 'images' / 'pocket' / 'en-US'
RAW_BASE = "https://raw.githubusercontent.com/marcelpanse/tcg-pocket-collection-tracker/main/frontend/public/images"

def to_our_id(card_id):
    """Convert A1-1 to A1-001"""
    parts = card_id.rsplit('-', 1)
    if len(parts) == 2:
        try:
            return f"{parts[0]}-{int(parts[1]):03d}"
        except ValueError:
            return card_id
    return card_id

def download_image(repo_id, our_id):
    """Download a single image."""
    dest = IMAGE_DIR / f"{our_id}.webp"
    if dest.exists():
        return True, our_id
    
    url = f"{RAW_BASE}/en-US/{repo_id}.webp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(data)
        return True, our_id
    except Exception as e:
        return False, our_id

def main():
    # Load repo cards
    with open(REPO_CARDS_PATH) as f:
        repo = json.load(f)
    
    # Find cards missing images
    missing = []
    for card in repo:
        our_id = to_our_id(card['card_id'])
        img_path = IMAGE_DIR / f"{our_id}.webp"
        if not img_path.exists():
            missing.append((card['card_id'], our_id))
    
    print(f"Cards to download: {len(missing)}")
    
    if not missing:
        print("Nothing to download!")
        return
    
    # Download in parallel
    success = 0
    failed = 0
    
    def worker(item):
        repo_id, our_id = item
        return download_image(repo_id, our_id)
    
    start = time.time()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(worker, item): item for item in missing}
        for i, future in enumerate(as_completed(futures)):
            if i % 100 == 0:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(missing) - i) / rate if rate > 0 else 0
                print(f"  Progress: {i}/{len(missing)} ({rate:.1f}/s, ETA: {eta:.0f}s)")
            try:
                ok, our_id = future.result()
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"  Error: {e}")
                failed += 1
    
    print(f"\nDownloaded: {success}, Failed: {failed}")
    
    # Count by set
    from collections import Counter
    sets_downloaded = Counter()
    for f in IMAGE_DIR.glob('*.webp'):
        setc = f.stem.split('-')[0]
        sets_downloaded[setc] += 1
    
    print("\nImages by set:")
    for s, c in sorted(sets_downloaded.items()):
        print(f"  {s}: {c}")

if __name__ == '__main__':
    main()
