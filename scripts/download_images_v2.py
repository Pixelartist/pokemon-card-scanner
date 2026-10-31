#!/usr/bin/env python3
"""
Simple parallel downloader for Pokémon card images.
Uses ThreadPoolExecutor with requests.
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PIL import Image
from io import BytesIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CARD_CATALOG_PATH = DATA_DIR / "card_catalog.json"
IMAGE_DIR = DATA_DIR / "images"
OFFICIAL_DIR = IMAGE_DIR / "official"
PKMNCARDS_DIR = IMAGE_DIR / "pkmncards"

# Download sources
POKEMON_TCG_OFFICIAL_BASE = "https://images.pokemontcg.io"

# Settings
MAX_WORKERS = 8
REQUEST_TIMEOUT = 30
RATE_LIMIT_DELAY = 0.3  # seconds between requests

class ParallelDownloader:
    def __init__(self):
        self.stats = {
            "total_attempted": 0,
            "total_downloaded": 0,
            "total_failed": 0,
            "by_set": {}
        }
        self.last_request_time = 0
        
    def rate_limit(self):
        """Respectful rate limiting."""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self.last_request_time = time.time()
    
    def download_card(self, card: Dict) -> bool:
        """Download a single card image."""
        set_code = card.get("set_code", "")
        number = card.get("number", "")
        language = card.get("language", "en")
        
        if not set_code or not number:
            return False
        
        # Handle special characters in numbers
        import re
        num_match = re.search(r'(\d+)', number)
        if num_match:
            number = num_match.group(1)
        else:
            return False
        
        # Create URL
        url = f"{POKEMON_TCG_OFFICIAL_BASE}/{set_code}/{number}.png"
        
        # Create filename
        filename = f"{set_code}-{number}.png"
        local_path = OFFICIAL_DIR / language / filename
        
        # Skip if exists
        if local_path.exists():
            return True
        
        try:
            self.rate_limit()
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            if len(response.content) < 500:
                logger.debug(f"Skipping small file: {url}")
                return False
            
            # Save image
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, 'wb') as f:
                f.write(response.content)
            
            self.stats["total_downloaded"] += 1
            self.stats["by_set"][set_code] = self.stats["by_set"].get(set_code, 0) + 1
            return True
            
        except Exception as e:
            return False
    
    def download_all(self, card_catalog: Dict) -> Dict:
        """Download all card images using thread pool."""
        cards = card_catalog.get("cards", [])
        logger.info(f"Starting parallel download for {len(cards)} cards ({MAX_WORKERS} workers)")
        
        downloaded = 0
        failed = 0
        skipped = 0
        start_time = time.time()
        last_progress = time.time()
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            future_to_card = {
                executor.submit(self.download_card, card): card 
                for card in cards
            }
            
            # Process results
            for future in as_completed(future_to_card):
                card = future_to_card[future]
                try:
                    success = future.result()
                    if success:
                        downloaded += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                
                # Progress updates
                current = downloaded + failed + skipped
                if time.time() - last_progress > 5:  # Every 5 seconds
                    elapsed = time.time() - start_time
                    progress = current / len(cards) * 100
                    rate = current / elapsed if elapsed > 0 else 0
                    eta = (len(cards) - current) / rate if rate > 0 else 0
                    logger.info(f"Progress: {current}/{len(cards)} ({progress:.1f}%) | "
                               f"Downloaded: {downloaded}, Failed: {failed}, Skipped: {skipped} | "
                               f"Rate: {rate:.1f}/s | ETA: {eta:.0f}s")
                    last_progress = time.time()
        
        elapsed = time.time() - start_time
        logger.info(f"Download complete in {elapsed:.1f}s")
        
        self.stats["total_attempted"] = len(cards)
        self.stats["total_failed"] = failed
        
        return self.stats

def count_files():
    """Count existing image files."""
    official_count = 0
    for lang in ['en', 'de', 'fr', 'es', 'it', 'pt', 'ja']:
        lang_dir = OFFICIAL_DIR / lang
        if lang_dir.exists():
            official_count += sum(1 for f in lang_dir.iterdir() if f.is_file())
    
    pkmncards_count = sum(1 for f in PKMNCARDS_DIR.iterdir() if f.is_file()) if PKMNCARDS_DIR.exists() else 0
    
    return official_count, pkmncards_count

def main():
    """Main entry point."""
    print("=" * 60)
    print("Pokémon Card Image Downloader (Parallel)")
    print("=" * 60)
    
    # Check catalog
    if not CARD_CATALOG_PATH.exists():
        logger.error(f"Catalog not found at {CARD_CATALOG_PATH}")
        sys.exit(1)
    
    with open(CARD_CATALOG_PATH) as f:
        catalog = json.load(f)
    
    cards = catalog.get("cards", [])
    logger.info(f"Loaded {len(cards)} cards from catalog")
    
    # Count existing
    off_before, pkm_before = count_files()
    logger.info(f"Existing files before: Official={off_before}, Pkmncards={pkm_before}")
    
    # Create directories
    for lang in ['en', 'de', 'fr', 'es', 'it', 'pt', 'ja']:
        (OFFICIAL_DIR / lang).mkdir(parents=True, exist_ok=True)
    PKMNCARDS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Run download
    downloader = ParallelDownloader()
    stats = downloader.download_all(catalog)
    
    # Summary
    print("\n" + "=" * 60)
    print("Download Complete")
    print("=" * 60)
    print(f"Total attempted: {stats['total_attempted']}")
    print(f"Successfully downloaded: {stats['total_downloaded']}")
    print(f"Failed: {stats['total_failed']}")
    
    # Count files
    off_after, pkm_after = count_files()
    print(f"\nFiles on disk:")
    print(f"  Official: {off_after}")
    print(f"  Pkmncards: {pkm_after}")
    print(f"  Total: {off_after + pkm_after}")
    print(f"  New: Official={off_after-off_before}, Pkmncards={pkm_after-pkm_before}")
    
    # Top sets
    if stats['by_set']:
        print(f"\nTop 10 sets by new downloads:")
        sorted_sets = sorted(stats['by_set'].items(), key=lambda x: x[1], reverse=True)[:10]
        for set_code, count in sorted_sets:
            print(f"  {set_code}: {count}")

if __name__ == "__main__":
    main()
