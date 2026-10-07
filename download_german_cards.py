#!/usr/bin/env python3
"""
Download German Pokemon card images from TCGdex API.
Downloads ~18,000+ cards with their metadata for an enhanced CLIP index.
"""

import requests
import os
import time
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Configuration
API_BASE = "https://api.tcgdex.net/v2"
IMAGE_BASE = "https://assets.tcgdex.net"
LANG = "de"  # German
OUTPUT_DIR = Path("data/german_cards")
IMAGE_QUALITY = "high"  # high (600x825) or low (245x337)
IMAGE_EXTENSION = "webp"  # png, jpg, or webp
BATCH_SIZE = 50  # Cards per API request
MAX_WORKERS = 5  # Concurrent downloads
RATE_LIMIT_DELAY = 0.1  # Seconds between requests

def get_all_cards():
    """Fetch all German cards from TCGdex API with pagination."""
    print("Fetching all German cards from TCGdex API...")
    all_cards = []
    page = 1
    
    while True:
        response = requests.get(
            f"{API_BASE}/{LANG}/cards",
            params={
                "pagination:page": page,
                "pagination:itemsPerPage": BATCH_SIZE
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"Error fetching page {page}: {response.status_code}")
            break
            
        cards = response.json()
        if not cards:
            break
            
        all_cards.extend(cards)
        print(f"  Fetched page {page}: {len(cards)} cards (total: {len(all_cards)})")
        
        # Check if we've fetched all cards
        if len(cards) < BATCH_SIZE:
            break
            
        page += 1
        
        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)
    
    print(f"Total cards fetched: {len(all_cards):,}")
    return all_cards

def get_card_details(card_id):
    """Fetch full details for a single card."""
    try:
        response = requests.get(
            f"{API_BASE}/{LANG}/cards/{card_id}",
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"  Error fetching details for {card_id}: {e}")
    return None

def download_image(url, output_path):
    """Download a single image."""
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)
            return True, None
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as e:
        return False, str(e)

def get_image_url(card):
    """Build the image URL for a card."""
    card_id = card['id']  # e.g., "swsh3-136"
    set_id, local_id = card_id.split('-')
    
    # Extract series from set_id (e.g., "swsh" from "swsh3")
    series = ""
    for i, c in enumerate(set_id):
        if c.isdigit():
            series = set_id[:i]
            break
    else:
        series = set_id
    
    return f"{IMAGE_BASE}/{LANG}/{series}/{set_id}/{local_id}/{IMAGE_QUALITY}.{IMAGE_EXTENSION}"

def build_index(cards_data):
    """Build a JSON index of all cards."""
    index = {
        "language": LANG,
        "total_cards": len(cards_data),
        "quality": IMAGE_QUALITY,
        "extension": IMAGE_EXTENSION,
        "cards": []
    }
    
    for card in cards_data:
        card_info = {
            "id": card['id'],
            "name": card['name'],
            "image_url": get_image_url(card),
            "localId": card.get('localId'),
            "category": card.get('category', 'Pokemon'),
        }
        
        # Add optional fields if available
        if 'hp' in card:
            card_info['hp'] = card['hp']
        if 'types' in card:
            card_info['types'] = card['types']
        if 'rarity' in card:
            card_info['rarity'] = card['rarity']
        if 'set' in card:
            card_info['set'] = {
                'id': card['set']['id'],
                'name': card['set']['name'],
            }
        
        index["cards"].append(card_info)
    
    return index

def main():
    """Main download pipeline."""
    print("=" * 60)
    print("German Pokemon Card Image Downloader")
    print("=" * 60)
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "images").mkdir(exist_ok=True)
    (OUTPUT_DIR / "metadata").mkdir(exist_ok=True)
    
    # Step 1: Fetch all card IDs
    cards_list = get_all_cards()
    
    # Step 2: Fetch full details for each card
    print(f"\nFetching full details for {len(cards_list):,} cards...")
    cards_with_details = []
    
    for i, card in enumerate(tqdm(cards_list, desc="Fetching details")):
        details = get_card_details(card['id'])
        if details:
            cards_with_details.append(details)
        
        # Progress every 500 cards
        if (i + 1) % 500 == 0:
            print(f"  Fetched {i + 1}/{len(cards_list)} card details...")
        
        time.sleep(RATE_LIMIT_DELAY)
    
    print(f"Successfully fetched {len(cards_with_details)}/{len(cards_list)} card details")
    
    # Step 3: Build metadata index
    print("\nBuilding metadata index...")
    index = build_index(cards_with_details)
    index_path = OUTPUT_DIR / "metadata" / "german_card_index.json"
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"Index saved to {index_path}")
    
    # Step 4: Download images in parallel
    print(f"\nDownloading {len(cards_with_details):,} card images...")
    images_dir = OUTPUT_DIR / "images"
    
    # Filter out cards that already have images
    existing = list(images_dir.glob("*.webp"))
    if existing:
        print(f"  Skipping {len(existing)} existing images")
        cards_to_download = [c for c in cards_with_details 
                           if not (images_dir / f"{c['id'].replace('-', '_')}.{IMAGE_EXTENSION}").exists()]
    else:
        cards_to_download = cards_with_details
    
    print(f"  Need to download {len(cards_to_download):,} images")
    
    downloaded = 0
    failed = 0
    
    def download_one(card):
        nonlocal downloaded, failed
        
        # Create safe filename
        safe_id = card['id'].replace('-', '_')
        output_path = images_dir / f"{safe_id}.{IMAGE_EXTENSION}"
        
        if output_path.exists():
            return True, None
        
        url = get_image_url(card)
        success, error = download_image(url, output_path)
        
        if success:
            downloaded += 1
            return True, None
        else:
            failed += 1
            return False, error
    
    # Use thread pool for parallel downloads
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_one, card): card 
                   for card in cards_to_download}
        
        with tqdm(total=len(futures), desc="Downloading images") as pbar:
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"  Error: {e}")
                pbar.update(1)
                
                # Progress every 500 images
                if downloaded % 500 == 0:
                    print(f"  Downloaded {downloaded}/{len(cards_to_download)} images...")
    
    print(f"\nDownload complete:")
    print(f"  Success: {downloaded}")
    print(f"  Failed: {failed}")
    print(f"  Total images: {len(list(images_dir.glob(f'*.{IMAGE_EXTENSION}'))):,}")
    
    # Step 5: Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Language: {LANG}")
    print(f"Total cards in index: {len(cards_with_details):,}")
    print(f"Images downloaded: {downloaded:,}")
    print(f"Failed downloads: {failed:,}")
    print(f"Output directory: {OUTPUT_DIR.absolute()}")
    print(f"Index file: {index_path.absolute()}")

if __name__ == "__main__":
    main()
