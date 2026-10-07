"""
Offline Pokemon card matcher using color histograms and structural features.
Pure Python implementation - no numpy/torch required.
Based on the PokeScope approach:
    - Extract color histogram features from card images
    - Compare query image against indexed card database
    - Return top-k similar cards
"""
import os
import sys
import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict

from PIL import Image
import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv('/opt/data/pokemon-card-scanner/.env')

logger = logging.getLogger(__name__)

# Card image CDN (official, no auth needed)
CARD_IMAGE_BASE = "https://images.pokemontcg.io"

# Database paths
DB_DIR = Path(__file__).parent.parent.parent / "data"
INDEX_PATH = DB_DIR / "card_index.json"
CACHE_DIR = DB_DIR / "images"


@dataclass
class CardFeatures:
    """Feature vector for a single card."""
    card_id: str
    name: str
    set_code: str
    number: str
    # Color histogram (64 bins: 4 hue × 4 saturation × 4 value)
    histogram: List[float]
    # Dominant colors (top 5 RGB as tuples)
    dominant_colors: List[Tuple[int, int, int]]
    # Average color
    avg_color: Tuple[int, int, int]
    # Image dimensions
    width: int
    height: int
    # Simple structural features
    edge_density: float
    # Hash for quick dedup
    content_hash: str


class OfflineCardMatcher:
    """
    Offline card matcher using color histograms.
    
    Architecture:
    1. Downloads all card images from the official CDN
    2. Extracts color histogram features (64-bin HSV)
    3. Builds an index for fast similarity search
    4. Matches query images against the index
    """
    
    def __init__(self, db_dir: Path = None):
        self.db_dir = db_dir or DB_DIR
        self.index_path = self.db_dir / "card_index.json"
        self.cache_dir = self.db_dir / "images"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.cards: Dict[str, CardFeatures] = {}
        self.card_list: List[CardFeatures] = []
        self._index_loaded = False
        
    def load_or_download_index(self, force_download: bool = False) -> bool:
        """Load existing index or download all card images and build index."""
        if not force_download and self.index_path.exists():
            self._load_index()
            logger.info(f"Loaded index with {len(self.cards)} cards")
            return True
        
        logger.info("Building card index from scratch...")
        return self._download_and_index()
    
    def _load_index(self):
        """Load pre-built index from disk."""
        with open(self.index_path) as f:
            data = json.load(f)
        
        for card_data in data.get("cards", []):
            cf = CardFeatures(
                card_id=card_data["card_id"],
                name=card_data["name"],
                set_code=card_data["set_code"],
                number=card_data["number"],
                histogram=card_data["histogram"],
                dominant_colors=[tuple(c) for c in card_data["dominant_colors"]],
                avg_color=tuple(card_data["avg_color"]),
                width=card_data["width"],
                height=card_data["height"],
                edge_density=card_data["edge_density"],
                content_hash=card_data["content_hash"]
            )
            self.cards[cf.card_id] = cf
            self.card_list.append(cf)
        
        self._index_loaded = True
    
    def _download_and_index(self) -> bool:
        """Download all card images and build feature index."""
        import time
        start = time.time()
        
        # Get all card IDs from API
        logger.info("Fetching card catalog from API...")
        cards = self._fetch_all_cards()
        logger.info(f"Found {len(cards)} cards")
        
        if not cards:
            logger.error("No cards found in API")
            return False
        
        # Download images and extract features
        downloaded = 0
        failed = 0
        
        for i, card in enumerate(cards):
            card_id = card.get("id", "")
            set_code = card.get("set_code", "")
            number = card.get("number", "")
            name = card.get("name", "")
            
            # Skip if already indexed
            if card_id in self.cards:
                continue
            
            # Download image
            img_path = self._download_card_image(set_code, number, card_id)
            if not img_path:
                failed += 1
                continue
            
            # Extract features
            try:
                features = self._extract_features(img_path, card_id, name, set_code, number)
                if features:
                    self.cards[card_id] = features
                    self.card_list.append(features)
                    downloaded += 1
                    
                    if (i + 1) % 1000 == 0:
                        logger.info(f"Indexed {i+1}/{len(cards)} cards ({downloaded} successful)")
            except Exception as e:
                failed += 1
                logger.debug(f"Failed to extract features for {card_id}: {e}")
            
            # Save progress every 1000 cards
            if (i + 1) % 1000 == 0:
                self._save_index_progress()
        
        # Final save
        self._save_index()
        
        elapsed = time.time() - start
        logger.info(f"Index complete: {downloaded} cards indexed, {failed} failed ({elapsed:.1f}s)")
        
        return downloaded > 0
    
    def _fetch_all_cards(self) -> List[Dict]:
        """Fetch all cards from the API with pagination."""
        cards = []
        next_url = "https://api.pokemontcgapi.com/v1/cards?limit=200"
        page = 0
        
        while next_url:
            r = requests.get(
                next_url,
                headers={"X-Api-Key": os.environ.get("PTCG_API_KEY", "")},
                timeout=30
            )
            
            if r.status_code != 200:
                logger.error(f"API error: {r.status_code} {r.text[:200]}")
                break
            
            data = r.json()
            page_cards = data.get("data", [])
            if not page_cards:
                break
            
            cards.extend(page_cards)
            
            # Check for next page - use the full next URL
            links = data.get("links", {})
            next_url = links.get("next", "")
            page += 1
            
            if page >= 300:  # Safety limit (~60k cards at 200/page)
                logger.warning("Hit safety limit, stopping pagination")
                break
        
        return cards
    
    def _download_card_image(self, set_code: str, number: str, card_id: str) -> Optional[Path]:
        """Download a card image from the CDN."""
        url_patterns = [
            f"{CARD_IMAGE_BASE}/{set_code}/{number}.png",
            f"{CARD_IMAGE_BASE}/{set_code}/{number}_hires.png",
            f"{CARD_IMAGE_BASE}/{set_code}/{number}_large.png",
        ]
        
        for url in url_patterns:
            try:
                r = requests.get(url, timeout=10, stream=True)
                if r.status_code == 200 and len(r.content) > 1000:
                    img_path = self.cache_dir / f"{card_id}.jpg"
                    if not img_path.exists():
                        img = Image.open(r.raw).convert("RGB")
                        img.save(img_path, "JPEG", quality=85)
                    return img_path
            except Exception:
                continue
        
        return None
    
    def _extract_features(self, img_path: Path, card_id: str, 
                          name: str, set_code: str, number: str) -> Optional[CardFeatures]:
        """Extract color histogram and structural features from a card image."""
        try:
            img = Image.open(img_path).convert("RGB")
            
            # Get dimensions
            w, h = img.size
            
            # Resize for feature extraction (max 128px dimension)
            max_dim = 128
            if h > w:
                new_w = max_dim * w // h
                new_h = max_dim
            else:
                new_w = max_dim
                new_h = max_dim * h // w
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            
            # Convert to HSV for color histogram
            hsv = img_resized.convert("HSV")
            
            # Build histogram: 64 bins (4 hue × 4 saturation × 4 value)
            histogram = [0.0] * 64
            h_bins, s_bins, v_bins = 4, 4, 4
            h_range = 180 // h_bins
            s_range = 255 // s_bins
            v_range = 255 // v_bins
            
            # Sample every 4th pixel for speed
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
            
            # Dominant colors using simple frequency count
            dominant = self._find_dominant_colors(img_resized, k=5)
            
            # Average color
            avg_r, avg_g, avg_b = 0, 0, 0
            for r_val, g_val, b_val in img_resized.getdata():
                avg_r += r_val
                avg_g += g_val
                avg_b += b_val
            pixel_count = img_resized.width * img_resized.height
            avg_color = (avg_r // pixel_count, avg_g // pixel_count, avg_b // pixel_count)
            
            # Edge density (simple gradient)
            gray = img_resized.convert("L")
            pixels_gray = list(gray.getdata())
            w_resized, h_resized = gray.size
            
            edge_sum = 0
            edge_count = 0
            for y in range(h_resized - 1):
                for x in range(w_resized - 1):
                    idx = y * w_resized + x
                    dx = abs(pixels_gray[idx + 1] - pixels_gray[idx])
                    dy = abs(pixels_gray[idx + w_resized] - pixels_gray[idx])
                    edge_sum += dx + dy
                    edge_count += 1
            
            edge_density = edge_sum / (edge_count * 255.0) if edge_count > 0 else 0.0
            
            # Content hash
            content_hash = hashlib.md5(img.tobytes()[:1024]).hexdigest()
            
            return CardFeatures(
                card_id=card_id,
                name=name,
                set_code=set_code,
                number=number,
                histogram=histogram,
                dominant_colors=dominant,
                avg_color=avg_color,
                width=w,
                height=h,
                edge_density=edge_density,
                content_hash=content_hash
            )
        except Exception as e:
            logger.error(f"Feature extraction failed for {card_id}: {e}")
            return None
    
    def _find_dominant_colors(self, img: Image.Image, k: int = 5) -> List[Tuple[int, int, int]]:
        """Find dominant colors using color quantization."""
        # Reduce to k colors by averaging in RGB space
        colors = defaultdict(list)
        step = 64  # Quantize to 64 levels per channel
        
        for r, g, b in img.getdata():
            key = (r // step * step, g // step * step, b // step * step)
            colors[key].append((r, g, b))
        
        # Sort by frequency
        sorted_colors = sorted(colors.items(), key=lambda x: len(x[1]), reverse=True)
        
        # Return top k
        result = []
        for key, pixels in sorted_colors[:k]:
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            result.append((avg_r, avg_g, avg_b))
        
        return result
    
    def _save_index(self):
        """Save index to disk."""
        self._save_index_progress()
        logger.info(f"Saved index: {len(self.cards)} cards")
    
    def _save_index_progress(self):
        """Save current progress (for resume on crash)."""
        data = {
            "cards": [asdict(cf) for cf in self.card_list],
            "metadata": {
                "total_cards": len(self.card_list),
                "updated_at": __import__("datetime").datetime.utcnow().isoformat()
            }
        }
        
        tmp_path = self.index_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        tmp_path.rename(self.index_path)
    
    def match_card(self, image_bytes: bytes, top_k: int = 5) -> Dict[str, Any]:
        """
        Match a card image against the indexed database.
        
        Returns dict with decision, candidates, and metadata.
        """
        if not self._index_loaded:
            if not self.load_or_download_index():
                return {
                    "error": "Card index not available. Cannot identify cards.",
                    "decision": "no_match",
                    "candidates": []
                }
        
        # Extract features from query image
        try:
            query_img = Image.open(__import__('io').BytesIO(image_bytes)).convert("RGB")
            query_features = self._extract_query_features(query_img)
        except Exception as e:
            return {
                "error": f"Failed to process image: {e}",
                "decision": "no_match",
                "candidates": []
            }
        
        if not query_features:
            return {
                "error": "Failed to extract features from image",
                "decision": "no_match",
                "candidates": []
            }
        
        # Find top-k matches using histogram intersection
        similarities = []
        for card in self.card_list:
            sim = self._histogram_intersection(query_features.histogram, card.histogram)
            edge_sim = 1.0 - abs(query_features.edge_density - card.edge_density)
            combined = 0.7 * sim + 0.3 * edge_sim
            similarities.append((combined, card))
        
        similarities.sort(reverse=True)
        
        candidates = []
        for score, card in similarities[:top_k]:
            if score > 0.1:
                candidates.append({
                    "id": card.card_id,
                    "name": card.name,
                    "set": card.set_code,
                    "number": card.number,
                    "similarity": round(score, 3),
                    "image_url": f"{CARD_IMAGE_BASE}/{card.set_code}/{card.number}.png"
                })
        
        if candidates and candidates[0]["similarity"] > 0.5:
            decision = "match"
            decision_reason = f"High confidence match ({candidates[0]['similarity']:.2f})"
        elif candidates:
            decision = "possible_match"
            decision_reason = f"Possible match ({candidates[0]['similarity']:.2f})"
        else:
            decision = "no_match"
            decision_reason = "No similar cards found"
        
        return {
            "decision": decision,
            "decision_reason": decision_reason,
            "candidates": candidates,
            "meta": {
                "cards_indexed": len(self.card_list),
                "index_loaded_at": __import__("datetime").datetime.utcnow().isoformat(),
                "source": "offline_matcher"
            }
        }
    
    def _extract_query_features(self, img: Image.Image) -> Optional[CardFeatures]:
        """Extract features from a query image."""
        w, h = img.size
        
        max_dim = 128
        if h > w:
            new_w = max_dim * w // h
            new_h = max_dim
        else:
            new_w = max_dim
            new_h = max_dim * h // w
        img_resized = img.resize((new_w, new_h), Image.LANCZOS)
        
        hsv = img_resized.convert("HSV")
        histogram = [0.0] * 64
        h_bins, s_bins, v_bins = 4, 4, 4
        h_range = 180 // h_bins
        s_range = 255 // s_bins
        v_range = 255 // v_bins
        
        pixels = list(hsv.getdata())
        for i in range(0, len(pixels), 4):
            h_val, s_val, v_val = pixels[i]
            h = min(h_val // h_range, h_bins - 1)
            s = min(s_val // s_range, s_bins - 1)
            v = min(v_val // v_range, v_bins - 1)
            bin_idx = h * s_bins * v_bins + s * v_bins + v
            histogram[bin_idx] += 1
        
        total = sum(histogram)
        if total > 0:
            histogram = [c / total for c in histogram]
        
        dominant = self._find_dominant_colors(img_resized, k=5)
        
        avg_r, avg_g, avg_b = 0, 0, 0
        for r_val, g_val, b_val in img_resized.getdata():
            avg_r += r_val
            avg_g += g_val
            avg_b += b_val
        pixel_count = img_resized.width * img_resized.height
        avg_color = (avg_r // pixel_count, avg_g // pixel_count, avg_b // pixel_count)
        
        gray = img_resized.convert("L")
        pixels_gray = list(gray.getdata())
        w_resized, h_resized = gray.size
        
        edge_sum = 0
        edge_count = 0
        for y in range(h_resized - 1):
            for x in range(w_resized - 1):
                idx = y * w_resized + x
                dx = abs(pixels_gray[idx + 1] - pixels_gray[idx])
                dy = abs(pixels_gray[idx + w_resized] - pixels_gray[idx])
                edge_sum += dx + dy
                edge_count += 1
        
        edge_density = edge_sum / (edge_count * 255.0) if edge_count > 0 else 0.0
        
        return CardFeatures(
            card_id="query",
            name="Query Card",
            set_code="",
            number="",
            histogram=histogram,
            dominant_colors=dominant,
            avg_color=avg_color,
            width=w,
            height=h,
            edge_density=edge_density,
            content_hash=""
        )
    
    @staticmethod
    def _histogram_intersection(hist1: List[float], hist2: List[float]) -> float:
        """Compute histogram intersection similarity."""
        return sum(min(a, b) for a, b in zip(hist1, hist2))
    
    def get_card_details(self, card_id: str) -> Optional[Dict]:
        """Get full card details including image URL."""
        card = self.cards.get(card_id)
        if not card:
            return None
        
        return {
            "id": card.card_id,
            "name": card.name,
            "set": card.set_code,
            "number": card.number,
            "image_url": f"{CARD_IMAGE_BASE}/{card.set_code}/{card.number}.png",
            "avg_color": card.avg_color,
            "dominant_colors": card.dominant_colors
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    matcher = OfflineCardMatcher()
    print("Loading or building card index...")
    matcher.load_or_download_index()
    print(f"Indexed {len(matcher.cards)} cards")
    
    if matcher.cards:
        print("\nSample cards:")
        for i, (card_id, cf) in enumerate(list(matcher.cards.items())[:5]):
            print(f"  {i+1}. {cf.name} ({cf.set_code} {cf.number})")
