#!/usr/bin/env python3
"""
Download German Pokemon card images and metadata from TCGdex API.
Optimized with parallel requests for speed.
"""

import requests
import json
import time
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).parent / "data"
GERMAN_DIR = BASE_DIR / "german_cards"
IMAGES_DIR = GERMAN_DIR / "images"
METADATA_FILE = GERMAN_DIR / "german_cards.json"

TCGDEX_CARDS_URL = "https://api.tcgdex.net/v2/de/cards"
TCGDEX_ASSETS_BASE = "https://assets.tcgdex.net/de/{set_id}/{local_id}"


def fetch_card_list(page_size: int = 50) -> list:
    """Fetch all German card IDs (paginated)."""
    print("Fetching German card list...")
    all_cards = []
    page = 1
    while True:
        params = {"pagination:itemsPerPage": page_size}
        if page > 1:
            params["pagination:page"] = page
        r = requests.get(TCGDEX_CARDS_URL, params=params, timeout=30)
        if r.status_code != 200:
            print(f"Error fetching page {page}: {r.status_code}")
            break
        cards = r.json()
        if not cards:
            break
        all_cards.extend(cards)
        print(f"  Page {page}: {len(cards)} cards (total: {len(all_cards)})")
        page += 1
        time.sleep(0.1)  # Rate limit
    return all_cards


def fetch_card_details(card_id: str) -> dict | None:
    """Fetch full details for a single card."""
    try:
        r = requests.get(f"{TCGDEX_CARDS_URL}/{card_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def download_image(image_url: str, filename: str) -> Path | None:
    """Download a single image."""
    try:
        r = requests.get(image_url, timeout=15)
        if r.status_code == 200:
            img_path = IMAGES_DIR / filename
            img_path.write_bytes(r.content)
            return img_path
    except Exception:
        pass
    return None


def process_card(card_detail: dict) -> tuple[Path | None, str]:
    """Process a single card: download image and return path."""
    card_id = card_detail["id"]
    image_url = card_detail.get("image", "")
    
    # Extract localId from card_id
    if "-" not in card_id:
        return None, card_id
    
    local_id = card_id.split("-")[-1]
    set_id = card_detail.get("set", {}).get("id", "") or card_id.split("-")[0]
    
    # Build image URL
    img_url = f"{TCGDEX_ASSETS_BASE}/{local_id}/high.png"
    
    # Download
    img_path = download_image(img_url, f"de_{local_id}.png")
    
    return img_path, card_id


def main():
    """Main download workflow."""
    # Create directories
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check existing state
    existing_images = len(list(IMAGES_DIR.glob("*.png")))
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            existing_metadata = json.load(f)
        existing_cards = set(c.get("card_id") for c in existing_metadata)
        print(f"Found {existing_images} images, {len(existing_cards)} cards in metadata")
    else:
        existing_cards = set()
        existing_metadata = []
    
    # Fetch card list
    card_list = fetch_card_list()
    print(f"\nTotal German cards: {len(card_list)}")
    
    # Process in parallel
    print(f"\nProcessing {len(card_list)} cards...")
    start_time = time.time()
    
    new_count = 0
    error_count = 0
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {}
        for card in card_list:
            card_id = card["id"]
            if card_id in existing_cards:
                continue
            future = executor.submit(fetch_card_details, card_id)
            futures[future] = card_id
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            card_id = futures[future]
            try:
                card_detail = future.result()
                if not card_detail:
                    error_count += 1
                    continue
                
                img_path, _ = process_card(card_detail)
                if img_path:
                    new_count += 1
                    entry = {
                        "card_id": card_id,
                        "name": card_detail.get("name", ""),
                        "local_id": card_id.split("-")[-1] if "-" in card_id else "",
                        "set_name": card_detail.get("set", {}).get("name", ""),
                        "rarity": card_detail.get("rarity", ""),
                        "types": card_detail.get("types", []),
                        "hp": card_detail.get("hp", 0),
                        "attacks": card_detail.get("attacks", []),
                        "weaknesses": card_detail.get("weaknesses", []),
                        "retreat": card_detail.get("retreat", 0),
                        "pricing": card_detail.get("pricing", {}),
                        "image_path": f"images/de_{card_id.split('-')[-1]}.png" if "-" in card_id else "",
                    }
                    existing_metadata.append(entry)
                
                # Progress
                if completed % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed
                    eta = (len(card_list) - completed) / rate if rate > 0 else 0
                    print(f"  Progress: {completed}/{len(card_list)} ({completed*100/len(card_list):.1f}%) - {new_count} images - ETA: {eta:.0f}s")
                
                # Save metadata periodically
                if completed % 500 == 0:
                    with open(METADATA_FILE, "w") as f:
                        json.dump(existing_metadata, f, ensure_ascii=False, indent=2)
                    
            except Exception as e:
                error_count += 1
                print(f"  Error processing {card_id}: {e}")
    
    # Final save
    elapsed = time.time() - start_time
    print(f"\nDownload complete!")
    print(f"  Time: {elapsed:.0f}s")
    print(f"  New images: {new_count}")
    print(f"  Total images: {len(list(IMAGES_DIR.glob('*.png')))}")
    print(f"  Errors: {error_count}")
    
    # Save final metadata
    with open(METADATA_FILE, "w") as f:
        json.dump(existing_metadata, f, ensure_ascii=False, indent=2)
    
    print(f"  Metadata saved to: {METADATA_FILE}")


if __name__ == "__main__":
    main()
