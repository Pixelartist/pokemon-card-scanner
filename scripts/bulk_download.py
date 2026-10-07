#!/usr/bin/env python3
"""Bulk card downloader for offline CLIP matcher.
Downloads card images from pokemontcg.io CDN and extracts features.
"""
import os
import sys
import json
import time
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from PIL import Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_DIR = Path("/opt/data/pokemon-card-scanner")
DATA_DIR = PROJECT_DIR / "data"
INDEX_PATH = DATA_DIR / "clip_card_index.json"
PROGRESS_PATH = DATA_DIR / "bulk_download_progress.json"
STATE_PATH = DATA_DIR / "bulk_download_state.json"
IMAGE_DIR = DATA_DIR / "clip_images"

# Settings
MAX_WORKERS = 8  # Parallel downloads
REQUEST_TIMEOUT = 15  # seconds
SUCCESS_RATE_THRESHOLD = 0.7  # minimum success rate to continue
IMAGE_QUALITY = 85  # JPEG quality
MAX_IMAGE_SIZE = 200  # KB - skip oversized images


class BulkCardDownloader:
    def __init__(self):
        self.index = {"cards": [], "metadata": {}}
        self.progress = {"downloaded": 0, "failed": 0, "skipped": 0, "total": 0}
        self.loaded = False
        self._load_state()
    
    def _load_state(self):
        """Load existing index and progress state."""
        if INDEX_PATH.exists():
            with open(INDEX_PATH) as f:
                self.index = json.load(f)
            self.loaded = True
            logger.info(f"Loaded existing index: {len(self.index.get('cards', []))} cards")
        
        if PROGRESS_PATH.exists():
            with open(PROGRESS_PATH) as f:
                self.progress = json.load(f)
            logger.info(f"Loaded progress: {self.progress}")
    
    def _save_state(self):
        """Save current state."""
        with open(INDEX_PATH, 'w') as f:
            json.dump(self.index, f, indent=2)
        with open(PROGRESS_PATH, 'w') as f:
            json.dump(self.progress, f, indent=2)
    
    def _download_card_image(self, set_code: str, card_number: str, card_id: str) -> Optional[Path]:
        """Download a single card image."""
        # Try multiple URL patterns
        url_patterns = [
            f"https://images.pokemontcg.io/{set_code}/{card_number}.png",
            f"https://images.pokemontcg.io/{set_code}/{card_number}_hires.png",
        ]
        
        for url in url_patterns:
            try:
                r = requests.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200 and len(r.content) > 1000:
                    # Check size
                    if len(r.content) > MAX_IMAGE_SIZE * 1024:
                        logger.debug(f"Skipping {card_id}: too large ({len(r.content)/1024:.0f}KB)")
                        continue
                    
                    # Convert to JPEG
                    img = Image.open(r.raw).convert("RGB")
                    img_path = IMAGE_DIR / f"{card_id}.jpg"
                    
                    if not img_path.exists():
                        img.save(img_path, "JPEG", quality=IMAGE_QUALITY)
                        return img_path
                    else:
                        # Already exists, check size
                        existing_size = img_path.stat().st_size
                        if existing_size > 500:  # reasonable min
                            return img_path
            except Exception as e:
                logger.debug(f"Download failed for {url}: {e}")
                continue
        
        return None
    
    def _extract_features(self, img_path: Path, card_data: dict) -> Optional[dict]:
        """Extract features from a card image."""
        try:
            img = Image.open(img_path).convert("RGB")
            w, h = img.size
            
            # Resize for feature extraction
            max_dim = 128
            if h > w:
                new_w = max_dim * w // h
                new_h = max_dim
            else:
                new_w = max_dim
                new_h = max_dim * h // w
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            
            # HSV histogram
            hsv = img_resized.convert("HSV")
            histogram = [0.0] * 64
            h_bins, s_bins, v_bins = 4, 4, 4
            h_range = 180 // h_bins
            s_range = 255 // s_bins
            v_range = 255 // v_bins
            
            # Sample pixels
            pixels = list(hsv.getdata())
            for i in range(0, len(pixels), 4):
                h_val, s_val, v_val = pixels[i]
                h = min(h_val // h_range, h_bins - 1)
                s = min(s_val // s_range, s_bins - 1)
                v = min(v_val // v_range, v_bins - 1)
                bin_idx = h * s_bins * v_bins + s * v_bins + v
                histogram[bin_idx] += 1
            
            # Normalize
            total = sum(histogram)
            if total > 0:
                histogram = [c / total for c in histogram]
            
            # Dominant colors
            img_small = img.resize((32, 32), Image.LANCZOS)
            pixels = list(img_small.getdata())
            color_counts: Dict[tuple, int] = {}
            for p in pixels:
                color = (p[0] // 32 * 32, p[1] // 32 * 32, p[2] // 32 * 32)
                color_counts[color] = color_counts.get(color, 0) + 1
            
            dominant = sorted(color_counts.items(), key=lambda x: -x[1])[:5]
            dominant_colors = [c for c, _ in dominant]
            
            # Content hash
            content_hash = hashlib.md5(img.tobytes()).hexdigest()[:16]
            
            return {
                "card_id": card_data.get("id", ""),
                "name": card_data.get("name", ""),
                "set_code": set_code,
                "number": card_number,
                "rarity": card_data.get("rarity", ""),
                "hp": str(card_data.get("hp", 0)) if card_data.get("hp") else None,
                "types": card_data.get("types", []),
                "histogram": histogram,
                "dominant_colors": [tuple(c) for c in dominant_colors],
                "avg_color": tuple(int(sum(c) / len(c)) for c in zip(*dominant_colors)) if dominant_colors else (0, 0, 0),
                "width": w,
                "height": h,
                "edge_density": 0.0,  # TODO: implement edge detection
                "content_hash": content_hash
            }
        except Exception as e:
            logger.debug(f"Feature extraction failed for {img_path}: {e}")
            return None
    
    def fetch_sets_from_pkmncards(self) -> List[dict]:
        """Fetch available sets from pkmncards.com."""
        logger.info("Fetching sets from pkmncards...")
        try:
            r = requests.get("https://pkmncards.com/api/sets", timeout=30)
            if r.status_code == 200:
                # Parse the response - might be HTML with embedded JSON
                text = r.text
                # Try to find JSON in the response
                if '{"' in text or '[' in text:
                    # Look for JSON arrays/objects
                    import re
                    json_matches = re.findall(r'(\{.*\}|\[.*\])', text, re.DOTALL)
                    for match in json_matches:
                        try:
                            data = json.loads(match)
                            if isinstance(data, list) and len(data) > 0:
                                logger.info(f"Found {len(data)} sets in JSON response")
                                return data
                            elif isinstance(data, dict) and 'data' in data:
                                logger.info(f"Found sets in nested JSON: {len(data['data'])} items")
                                return data['data']
                        except json.JSONDecodeError:
                            continue
                logger.warning("Could not parse pkmncards response")
                return []
        except Exception as e:
            logger.error(f"Failed to fetch sets: {e}")
        return []
    
    def download_card(self, card_info: dict) -> tuple:
        """Download and process a single card. Returns (success, card_id)."""
        set_code = card_info.get("symbolImages", {}).get("small", "").split("/")[-1].replace(".svg", "")
        number = card_info.get("number", "")
        card_id = f"{set_code}-{number}"
        
        # Skip if already indexed
        if any(c.get("card_id") == card_id for c in self.index.get("cards", [])):
            return (True, card_id, "skipped")
        
        # Download image
        img_path = self._download_card_image(set_code, number, card_id)
        if not img_path:
            return (False, card_id, "failed")
        
        # Extract features
        features = self._extract_features(img_path, card_info)
        if not features:
            img_path.unlink(missing_ok=True)
            return (False, card_id, "failed")
        
        return (True, card_id, "added")
    
    def run(self, target_cards: int = 10000, max_sets: int = None):
        """Run the bulk download process."""
        logger.info(f"Starting bulk download - target: {target_cards} cards")
        
        # Get sets from pkmncards
        sets_data = self.fetch_sets_from_pkmncards()
        if not sets_data:
            logger.error("No sets found from pkmncards")
            return False
        
        # Build card list
        cards_to_download = []
        for set_info in sets_data:
            cards = set_info.get("cards", [])
            for card in cards:
                card_info = {
                    "id": f"{set_info.get('key', '')}-{card.get('number', '')}",
                    "name": card.get("name", ""),
                    "number": card.get("number", ""),
                    "symbolImages": set_info.get("symbolImages", {}),
                    "rarity": card.get("rarity", ""),
                    "hp": card.get("hp"),
                    "types": card.get("types", []),
                }
                cards_to_download.append(card_info)
        
        logger.info(f"Found {len(cards_to_download)} total cards from pkmncards")
        
        # Filter out already indexed
        existing_ids = {c.get("card_id") for c in self.index.get("cards", [])}
        new_cards = [c for c in cards_to_download if c["id"] not in existing_ids]
        logger.info(f"New cards to process: {len(new_cards)}")
        
        if max_sets:
            # Limit to specific sets if requested
            new_cards = new_cards[:max_sets * 100]  # rough estimate
        
        # Process in batches
        self.progress["total"] = min(len(new_cards), target_cards)
        processed = 0
        downloaded = 0
        failed = 0
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit tasks
            futures = {executor.submit(self.download_card, card): card for card in new_cards[:target_cards]}
            
            for future in as_completed(futures):
                success, card_id, status = future.result()
                processed += 1
                
                if success:
                    downloaded += 1
                    if status == "added":
                        # Add to index
                        card_data = futures[future]
                        features = self._extract_features(
                            IMAGE_DIR / f"{card_id}.jpg",
                            card_data
                        )
                        if features:
                            self.index["cards"].append(features)
                else:
                    failed += 1
                
                # Progress reporting
                if processed % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (self.progress["total"] - processed) / rate if rate > 0 else 0
                    logger.info(
                        f"Progress: {processed}/{self.progress['total']} "
                        f"({processed/self.progress['total']*100:.1f}%) | "
                        f"Rate: {rate:.1f} cards/sec | "
                        f"ETA: {eta/60:.1f}min | "
                        f"Success: {downloaded}, Failed: {failed}"
                    )
                
                # Save progress every 500 cards
                if processed % 500 == 0:
                    self._save_state()
        
        # Final save
        self._save_state()
        
        elapsed = time.time() - start_time
        logger.info(f"\n{'='*50}")
        logger.info(f"Bulk download complete!")
        logger.info(f"Total processed: {processed}")
        logger.info(f"Successfully added: {downloaded}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Time: {elapsed/60:.1f} minutes ({elapsed/3600:.2f} hours)")
        logger.info(f"Average rate: {processed/elapsed:.1f} cards/sec")
        logger.info(f"Total cards in index: {len(self.index['cards'])}")
        logger.info(f"{'='*50}")
        
        return downloaded > 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bulk card downloader for Pokemon Card Scanner")
    parser.add_argument("--target", type=int, default=10000, help="Target number of cards to download")
    parser.add_argument("--sets", type=int, default=None, help="Limit to N sets")
    parser.add_argument("--resume", action="store_true", help="Resume from previous state")
    args = parser.parse_args()
    
    downloader = BulkCardDownloader()
    downloader.run(target_cards=args.target, max_sets=args.sets)
