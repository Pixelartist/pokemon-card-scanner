#!/usr/bin/env python3
"""
Download Pokemon card images from TCGdex API.
Supplements pokemontcg.io with sets not available there.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = Path("/opt/data/pokemon-card-scanner/data")
CARD_CATALOG_PATH = DATA_DIR / "card_catalog.json"
IMAGE_DIR = DATA_DIR / "images"
TCGDEX_DIR = IMAGE_DIR / "tcgdex"
LOG_PATH = DATA_DIR / "tcgdex_download_output.log"

# API URLs
API_BASE = "https://api.tcgdex.net/v2"
ASSETS_BASE = "https://assets.tcgdex.net"

# Rate limiting
RATE_LIMIT_DELAY = 0.5  # 500ms between requests to be respectful
REQUEST_TIMEOUT = 15
MAX_WORKERS = 16

class TCGdexDownloader:
    def __init__(self):
        self.stats = {
            "total_attempted": 0,
            "total_downloaded": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "by_set": {},
            "start_time": None,
            "end_time": None
        }
        self.tcgdex_sets = set()
        
    def load_catalog(self):
        """Load card catalog."""
        logger.info(f"Loading catalog from {CARD_CATALOG_PATH}")
        with open(CARD_CATALOG_PATH) as f:
            catalog = json.load(f)
        return catalog
    
    def fetch_tcgdex_sets(self):
        """Fetch all TCGdex sets to get available IDs."""
        logger.info("Fetching TCGdex sets from API...")
        try:
            resp = requests.get(f"{API_BASE}/en/sets", timeout=30)
            if resp.status_code == 200:
                sets_data = resp.json()
                self.tcgdex_sets = set(s.get("id", "") for s in sets_data if s.get("id"))
                logger.info(f"Loaded {len(self.tcgdex_sets)} sets from TCGdex")
                return self.tcgdex_sets
            else:
                logger.error(f"Failed to fetch TCGdex sets: {resp.status_code}")
                return set()
        except Exception as e:
            logger.error(f"Error fetching TCGdex sets: {e}")
            return set()
    
    def rate_limit(self):
        """Rate limiting."""
        time.sleep(RATE_LIMIT_DELAY)
    
    def download_card_image(self, card_info: dict) -> bool:
        """Download a single card image from TCGdex."""
        set_code = card_info["set_code"]
        number = card_info["number"]
        lang = card_info["lang"]
        
        # Check if TCGdex has this set
        if set_code not in self.tcgdex_sets:
            return False
        
        # Get card details from API
        api_url = f"{API_BASE}/{lang}/cards/{set_code}-{number}"
        
        try:
            self.rate_limit()
            resp = requests.get(api_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.debug(f"Card not found: {api_url}")
                return False
            
            data = resp.json()
            image_url = data.get("image", "")
            if not image_url:
                return False
            
            # Append quality/extension to the base URL
            # Pattern: https://assets.tcgdex.net/en/set/set_id/number/{quality}.{ext}
            final_url = f"{image_url}/high.png"
            
            # Download image
            self.rate_limit()
            img_resp = requests.get(final_url, timeout=REQUEST_TIMEOUT)
            if img_resp.status_code != 200:
                return False
            
            if len(img_resp.content) < 500:
                logger.debug(f"Skipping small file: {final_url}")
                return False
            
            # Save image
            local_path = TCGDEX_DIR / lang / f"{set_code}-{number}.png"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(local_path, 'wb') as f:
                f.write(img_resp.content)
            
            self.stats["total_downloaded"] += 1
            self.stats["by_set"][set_code] = self.stats["by_set"].get(set_code, 0) + 1
            return True
            
        except Exception as e:
            logger.debug(f"Failed to download {set_code}-{number}: {e}")
            return False
    
    def download_all(self, card_catalog: dict) -> dict:
        """Download all missing images from TCGdex."""
        cards = card_catalog.get("cards", [])
        
        # Filter cards that are missing images but in TCGdex sets
        missing_cards = []
        official_dir = IMAGE_DIR / "official"
        pkmncards_dir = IMAGE_DIR / "pkmncards"
        
        for card in cards:
            set_code = card.get("set_code", "")
            number = card.get("number", "")
            lang = card.get("language", "en")
            
            if not set_code or not number:
                continue
            
            # Clean number
            num_match = re.search(r'(\d+)', number)
            clean_number = num_match.group(1) if num_match else number
            
            # Check if already have image
            official_path = official_dir / lang / f"{set_code}-{clean_number}.png"
            pkmn_path = pkmncards_dir / f"{set_code}-{clean_number}.png"
            
            if official_path.exists() or pkmn_path.exists():
                continue
            
            # Check if TCGdex has this set
            if set_code in self.tcgdex_sets:
                missing_cards.append({
                    "set_code": set_code,
                    "number": clean_number,
                    "lang": lang
                })
        
        logger.info(f"Starting parallel download for {len(missing_cards)} cards ({MAX_WORKERS} workers)")
        
        self.stats["start_time"] = datetime.now()
        self.stats["total_attempted"] = len(missing_cards)
        
        # Download using thread pool
        downloaded = 0
        failed = 0
        completed = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(self.download_card_image, card): card 
                for card in missing_cards
            }
            
            for future in as_completed(futures):
                card = futures[future]
                try:
                    success = future.result()
                    if success:
                        downloaded += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    logger.debug(f"Future failed: {e}")
                
                completed += 1
                
                # Progress reporting
                if completed % 500 == 0 or completed == len(missing_cards):
                    elapsed = datetime.now() - self.stats["start_time"]
                    rate = completed / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
                    eta = (len(missing_cards) - completed) / rate if rate > 0 else 0
                    logger.info(
                        f"Progress: {completed}/{len(missing_cards)} ({completed*100/len(missing_cards):.1f}%) "
                        f"| Downloaded: {downloaded}, Failed: {failed} "
                        f"| Rate: {rate:.1f}/s | ETA: {eta:.0f}s"
                    )
                    # Force flush
                    for handler in logger.handlers:
                        handler.flush()
        
        self.stats["end_time"] = datetime.now()
        self.stats["total_failed"] = len(missing_cards) - downloaded
        
        return self.stats

def main():
    """Main execution function."""
    print("=" * 60)
    print("Pokemon Card TCGdex Image Downloader")
    print("=" * 60)
    
    downloader = TCGdexDownloader()
    
    # Load catalog
    catalog = downloader.load_catalog()
    logger.info(f"Loaded {len(catalog.get('cards', []))} cards from catalog")
    
    # Fetch TCGdex sets
    tcgdex_sets = downloader.fetch_tcgdex_sets()
    if not tcgdex_sets:
        logger.error("Failed to fetch TCGdex sets. Aborting.")
        return
    
    # Download images
    stats = downloader.download_all(catalog)
    
    # Print summary
    print("\n" + "=" * 60)
    print("Download Complete")
    print("=" * 60)
    print(f"Total attempted: {stats['total_attempted']}")
    print(f"Successfully downloaded: {stats['total_downloaded']}")
    print(f"Failed: {stats['total_failed']}")
    
    elapsed = stats["end_time"] - stats["start_time"]
    print(f"Elapsed time: {elapsed.total_seconds():.1f}s")
    
    # Save stats
    stats_path = DATA_DIR / "tcgdex_download_stats.json"
    with open(stats_path, 'w') as f:
        json.dump(stats, f, indent=2, default=str)
    logger.info(f"Stats saved to {stats_path}")

if __name__ == "__main__":
    main()
