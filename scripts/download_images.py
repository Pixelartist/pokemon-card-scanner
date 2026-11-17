#!/usr/bin/env python3
"""
Download Pokémon card images from multiple sources.
Handles Pokemon TCG official CDN and pkmncards.com real photos.
Supports multilingual images and organized storage.
"""

import asyncio
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = Path("data")
CARD_CATALOG_PATH = DATA_DIR / "card_catalog.json"
IMAGE_DIR = DATA_DIR / "images"
CACHE_DIR = DATA_DIR / "cache"
DOWNLOAD_LOG_PATH = DATA_DIR / "pkmncards_download.log"

# Directories for different image sources
(IMAGE_DIR / "official").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "en").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "de").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "fr").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "es").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "it").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "pt").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "official" / "ja").mkdir(parents=True, exist_ok=True)

(IMAGE_DIR / "pkmncards").mkdir(parents=True, exist_ok=True)
(IMAGE_DIR / "user_scans").mkdir(parents=True, exist_ok=True)

# Download sources
POKEMON_TCG_OFFICIAL_BASE = "https://images.pokemontcg.io"
PKMNCARDS_BASE = "https://pkmncards.com"  # pkmncards.com base URL

# Configuration
MAX_WORKERS = 8
BATCH_SIZE = 32
REQUEST_TIMEOUT = 30
RATE_LIMIT_DELAY = 1.0  # seconds between requests (respectful)
TARGET_IMAGE_WIDTH = 512
TARGET_IMAGE_HEIGHT = 724  # Standard card aspect ratio (4:3)

class ImageDownloader:
    def __init__(self):
        self.download_stats = {
            "total_attempted": 0,
            "total_downloaded": 0,
            "total_failed": 0,
            "languages": {},
            "sources": {}
        }
        self.download_log = []
        
    def download_all_images(self, card_catalog: Dict) -> Dict:
        """Download all card images from catalog."""
        cards = card_catalog.get("cards", [])
        logger.info(f"Starting image download for {len(cards)} cards")
        
        # Organize cards by language and source
        cards_by_language = self._organize_cards_by_language(cards)
        
        # Download images in parallel by language
        for language, language_cards in cards_by_language.items():
            logger.info(f"Downloading images for {len(language_cards)} {language} cards")
            self._download_language_images(language, language_cards)
            
        # Save download log
        self._save_download_log()
        
        return self.download_stats
    
    def _organize_cards_by_language(self, cards: List[Dict]) -> Dict[str, List[Dict]]:
        """Organize cards by their primary language."""
        cards_by_language = {}
        
        for card in cards:
            # Use card's primary language (stored in catalog)
            language = card.get("language", "en")
            cards_by_language.setdefault(language, []).append(card)
            
        logger.info(f"Cards by language: {cards_by_language}")
        return cards_by_language
    
    def _download_language_images(self, language: str, cards: List[Dict]):
        """Download all images for a specific language."""
        logger.info(f"Processing {language} images with {MAX_WORKERS} workers")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all download tasks
            future_to_card = {
                executor.submit(self._download_single_image, card): card 
                for card in cards
            }
            
            # Process completed downloads
            for future in as_completed(future_to_card):
                card = future_to_card[future]
                try:
                    result = future.result(timeout=300)
                    if result:
                        self._update_stats(result, language)
                        
                        # Log successful download
                        log_entry = {
                            "timestamp": datetime.now().isoformat(),
                            "card_id": card.get("id", ""),
                            "name": card.get("name", ""),
                            "set_code": card.get("set_code", ""),
                            "number": card.get("number", ""),
                            "language": language,
                            "status": "success",
                            "local_path": result.get("local_path", "")
                        }
                        self.download_log.append(log_entry)
                        
                except Exception as e:
                    logger.error(f"Failed to download {card.get('id', '')}: {e}")
                    self.download_stats["total_failed"] += 1
                    
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "card_id": card.get("id", ""),
                        "name": card.get("name", ""),
                        "set_code": card.get("set_code", ""),
                        "number": card.get("number", ""),
                        "language": language,
                        "status": "failed",
                        "error": str(e)
                    }
                    self.download_log.append(log_entry)
    
    def _download_single_image(self, card: Dict) -> Optional[Dict]:
        """Download a single card's images from official CDN."""
        card_id = card.get("id", "")
        set_code = card.get("set_code", "")
        number = card.get("number", "")
        language = card.get("language", "en")
        
        if not card_id or not set_code or not number:
            logger.warning(f"Missing card data: {card_id}")
            return None
            
        # Try Pokemon TCG official CDN
        official_url = f"{POKEMON_TCG_OFFICIAL_BASE}/{set_code}/{number}.png"
        result = self._download_official_image(
            official_url, set_code, number, language
        )
        
        if result:
            return {
                "card_id": card_id,
                "local_path": result["local_path"],
                "source": "official",
                "language": language
            }
            
        return None
    
    def _download_tcgdex_image(self, url: str, set_code: str, number: str, language: str) -> Optional[Dict]:
        """Download image from TCGdex CDN (deprecated - kept for compatibility)."""
        logger.warning(f"TCGdex image download called - this source is no longer used. Use official CDN instead.")
        return None
    
    def _download_official_image(self, url: str, set_code: str, number: str, language: str) -> Optional[Dict]:
        """Download image from Pokemon TCG official CDN."""
        if not url:
            return None
            
        try:
            # Create filename
            filename = f"{set_code}-{number}.png"
            local_path = IMAGE_DIR / "official" / language / filename
            
            # Skip if already exists
            if local_path.exists():
                logger.debug(f"Official image already exists: {local_path}")
                return {
                    "local_path": str(local_path),
                    "source": "official",
                    "language": language,
                    "url": url
                }
            
            # Download with retry
            for attempt in range(3):
                response = requests.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                
                if len(response.content) < 500:  # Too small to be valid image
                    logger.warning(f"Official image too small from {url} (attempt {attempt + 1})")
                    continue
                
                # Process and save image
                image_data = self._process_image(response.content)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(local_path, 'wb') as f:
                    f.write(image_data)
                
                logger.debug(f"Downloaded official image: {local_path}")
                return {
                    "local_path": str(local_path),
                    "source": "official",
                    "language": language,
                    "url": url,
                    "size": len(image_data)
                }
                
        except Exception as e:
            logger.warning(f"Failed to download official image {url}: {e}")
            
        return None
    
    def _download_pkmncards_image(self, url: str, set_code: str, number: str, source: str) -> Optional[Dict]:
        """Download image from pkmncards.com."""
        if not url:
            return None
            
        try:
            # Create filename - use .jpg for pkmncards source
            filename = f"{set_code}-{number}.jpg"
            local_path = IMAGE_DIR / "pkmncards" / filename
            
            # Skip if already exists
            if local_path.exists():
                logger.debug(f"Pkmncards image already exists: {local_path}")
                return {
                    "local_path": str(local_path),
                    "source": source,
                    "url": url
                }
            
            # Download with retry
            for attempt in range(3):
                response = requests.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                
                if len(response.content) < 500:  # Too small to be valid image
                    logger.warning(f"Pkmncards image too small from {url} (attempt {attempt + 1})")
                    continue
                
                # Process and save image
                image_data = self._process_image(response.content)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(local_path, 'wb') as f:
                    f.write(image_data)
                
                logger.debug(f"Downloaded pkmncards image: {local_path}")
                return {
                    "local_path": str(local_path),
                    "source": source,
                    "url": url,
                    "size": len(image_data)
                }
                
        except Exception as e:
            logger.warning(f"Failed to download pkmncards image {url}: {e}")
            
        return None
    
    def _process_image(self, image_bytes: bytes) -> bytes:
        """Process and optimize image."""
        try:
            image = Image.open(BytesIO(image_bytes)).convert("RGB")
            
            # Resize to standard card dimensions if needed
            if image.size != (TARGET_IMAGE_WIDTH, TARGET_IMAGE_HEIGHT):
                image = image.resize(
                    (TARGET_IMAGE_WIDTH, TARGET_IMAGE_HEIGHT),
                    Image.Resampling.LANCZOS
                )
            
            # Optimize for storage
            output = BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            
            return output.getvalue()
            
        except Exception as e:
            logger.warning(f"Image processing failed: {e}")
            return image_bytes  # Return original if processing fails
    
    def _update_stats(self, result: Dict, language: str):
        """Update download statistics."""
        self.download_stats["total_downloaded"] += 1
        
        # Language stats
        if language not in self.download_stats["languages"]:
            self.download_stats["languages"][language] = 0
        self.download_stats["languages"][language] += 1
        
        # Source stats
        source = result.get("source", "unknown")
        if source not in self.download_stats["sources"]:
            self.download_stats["sources"][source] = 0
        self.download_stats["sources"][source] += 1
    
    def _save_download_log(self):
        """Save download log to file."""
        with open(DOWNLOAD_LOG_PATH, 'w') as f:
            json.dump(self.download_log, f, indent=2)
        
        logger.info(f"Download log saved to {DOWNLOAD_LOG_PATH}")
        
        # Also create a summary file
        summary_path = DATA_DIR / "download_summary.txt"
        with open(summary_path, 'w') as f:
            f.write(f"Pokémon Card Image Download Summary\n")
            f.write(f"Date: {datetime.now().isoformat()}\n\n")
            f.write(f"Total attempted: {self.download_stats['total_attempted']}\n")
            f.write(f"Total downloaded: {self.download_stats['total_downloaded']}\n")
            f.write(f"Total failed: {self.download_stats['total_failed']}\n\n")
            
            f.write(f"By language:\n")
            for lang, count in self.download_stats["languages"].items():
                f.write(f"  {lang}: {count}\n")
                
            f.write(f"\nBy source:\n")
            for source, count in self.download_stats["sources"].items():
                f.write(f"  {source}: {count}\n")
        
        logger.info(f"Summary saved to {summary_path}")

def main():
    """Main execution function."""
    print("=== Pokémon Card Image Downloader ===")
    
    try:
        # Load card catalog
        if not CARD_CATALOG_PATH.exists():
            print(f"Error: Card catalog not found at {CARD_CATALOG_PATH}")
            print("Run fetch_catalog.py first to create the catalog")
            return
        
        with open(CARD_CATALOG_PATH) as f:
            card_catalog = json.load(f)
        
        print(f"Loaded {len(card_catalog.get('cards', []))} cards from catalog")
        
        # Start download
        downloader = ImageDownloader()
        stats = downloader.download_all_images(card_catalog)
        
        print("\n=== Download Complete ===")
        print(f"Total attempted: {stats['total_attempted']}")
        print(f"Total downloaded: {stats['total_downloaded']}")
        print(f"Total failed: {stats['total_failed']}")
        
        print(f"\nBy language:")
        for lang, count in stats["languages"].items():
            print(f"  {lang}: {count}")
            
        print(f"\nBy source:")
        for source, count in stats["sources"].items():
            print(f"  {source}: {count}")
            
        print(f"\nImages saved to: {IMAGE_DIR}")
        print(f"Download log: {DOWNLOAD_LOG_PATH}")
        
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise

if __name__ == "__main__":
    main()

