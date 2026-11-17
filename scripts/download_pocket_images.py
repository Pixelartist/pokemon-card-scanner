#!/usr/bin/env python3
"""
Download TCG Pocket card images from marcelpanse/tcg-pocket-collection-tracker.

Strategy:
1. Download repo metadata (cards.json, image-locales.json) — already cached
2. For each repo card, download the image from GitHub raw URL
3. Map repo IDs (A1-1) to our IDs (A1-001) and store under our naming
4. Update cards_meta.json with the new image URLs

Images available per locale:
  en-US: 4317, fr-FR: 2488, de-DE: 2196, es-ES: 1778, it-IT: 1778, pt-BR: 1778
"""
import json, os, sys, time, urllib.request, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
DATA_DIR = Path("/opt/data/pokemon-card-scanner/data")
REPO_CARDS_PATH = DATA_DIR / "repo_cards.json"
REPO_LOCALES_PATH = DATA_DIR / "repo_image_locales.json"
IMAGE_DIR = DATA_DIR / "images" / "pocket"
IDS_PATH = Path("/opt/data/pokemon-card-scanner/src/static/card-ids.json")
META_PATH = Path("/opt/data/pokemon-card-scanner/src/static/cards_meta.json")

# GitHub raw base
RAW_BASE = "https://raw.githubusercontent.com/marcelpanse/tcg-pocket-collection-tracker/main/frontend/public/images"

# Rate limiting
REQUEST_TIMEOUT = 30
MAX_WORKERS = 8
RATE_LIMIT = 0.1  # 100ms between requests

def to_our_id(card_id: str) -> str:
    """Convert A1-1 to A1-001"""
    parts = card_id.rsplit("-", 1)
    if len(parts) == 2:
        try:
            return f"{parts[0]}-{int(parts[1]):03d}"
        except ValueError:
            return card_id
    return card_id

def download_image(repo_id: str, locale: str, dest_path: Path) -> bool:
    """Download a single image from the repo."""
    if dest_path.exists():
        return True  # Already have it
    
    url = f"{RAW_BASE}/{locale}/{repo_id}.webp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"  FAIL {locale}/{repo_id}: {e}")
        return False

def main():
    # Load data
    with open(REPO_CARDS_PATH) as f:
        repo_cards = json.load(f)
    
    with open(IDS_PATH) as f:
        our_ids = set(json.load(f))
    
    with open(META_PATH) as f:
        our_meta = json.load(f)
    
    # Build lookup of what we already have
    our_images = {}
    for m in our_meta:
        if m.get('image'):
            key = f"{m['set']}-{m['number']}"
            our_images[key] = m['image']
    
    # Find repo cards that match our IDs but have no image
    to_download = []
    for c in repo_cards:
        our_id = to_our_id(c['card_id'])
        if our_id in our_ids and our_id not in our_images:
            # Determine which locale to use
            # Prefer en-US, fall back to others
            locale = "en-US"
            to_download.append((c['card_id'], locale, our_id))
    
    print(f"Total repo cards: {len(repo_cards)}")
    print(f"Cards to download: {len(to_download)}")
    
    if not to_download:
        print("Nothing to download!")
        return
    
    # Download in parallel
    success = 0
    failed = 0
    
    def worker(item):
        repo_id, locale, our_id = item
        dest = IMAGE_DIR / locale / f"{our_id}.webp"
        return download_image(repo_id, locale, dest), our_id, locale
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, item): item for item in to_download}
        for i, future in enumerate(as_completed(futures)):
            if i % 100 == 0:
                print(f"  Progress: {i}/{len(to_download)}")
            try:
                ok, our_id, locale = future.result()
                if ok:
                    success += 1
                    # Record the image URL
                    our_images[our_id] = f"/static/pocket/{locale}/{our_id}.webp"
                else:
                    failed += 1
            except Exception as e:
                print(f"  Worker error: {e}")
                failed += 1
    
    print(f"\nDownloaded: {success}, Failed: {failed}")
    
    # Update cards_meta.json
    updated = 0
    for m in our_meta:
        key = f"{m['set']}-{m['number']}"
        if key in our_images and key not in [k for k, v in our_images.items() if v == m.get('image')]:
            if not m.get('image'):
                m['image'] = our_images[key]
                updated += 1
    
    # Write back
    with open(META_PATH, 'w') as f:
        json.dump(our_meta, f, indent=2)
    
    print(f"Updated {updated} entries in cards_meta.json")
    
    # Also update card-hashes.json if it exists
    hashes_path = Path("/opt/data/pokemon-card-scanner/src/static/card-hashes.json")
    if hashes_path.exists():
        with open(hashes_path) as f:
            hashes = json.load(f)
        # Note: hash rebuild needed for new images
        print(f"\nNote: {len(hashes)} existing hashes; new images will need hash rebuild")

if __name__ == "__main__":
    main()