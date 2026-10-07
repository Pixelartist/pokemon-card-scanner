"""
Fast offline card matcher - uses pre-downloaded cards from Scryfall.
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from PIL import Image
import requests
from dotenv import load_dotenv

load_dotenv('/opt/data/pokemon-card-scanner/.env')

logger = logging.getLogger(__name__)

CARD_DIR = Path(__file__).parent.parent.parent / "data" / "scryfall_cards"
CARD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CardInfo:
    card_id: str
    name: str
    set_code: str
    number: str
    image_url: str
    avg_color: tuple


class FastCardMatcher:
    """
    Uses pre-cached card images with simple color features.
    Much faster than CLIP - no model needed.
    """
    
    def __init__(self):
        self.cards: Dict[str, CardInfo] = {}
        self._index_loaded = False
        
    def load_index(self, force_refresh: bool = False) -> bool:
        """Load or build card index from Scryfall."""
        index_path = CARD_DIR / "index.json"
        
        if not force_refresh and index_path.exists():
            with open(index_path) as f:
                data = json.load(f)
            
            for card_data in data.get("cards", []):
                ci = CardInfo(
                    card_id=card_data["card_id"],
                    name=card_data["name"],
                    set_code=card_data["set_code"],
                    number=card_data["number"],
                    image_url=card_data["image_url"],
                    avg_color=tuple(card_data["avg_color"])
                )
                self.cards[ci.card_id] = ci
            
            self._index_loaded = True
            logger.info(f"Loaded {len(self.cards)} cards from index")
            return True
        
        # Build index from Scryfall
        return self._build_index()
    
    def _build_index(self) -> bool:
        """Build index from Scryfall API (public, no auth)."""
        logger.info("Building card index from Scryfall...")
        
        # Scryfall has ~50k Pokemon cards
        # Fetch all cards
        cards = []
        next_page = "https://api.scryfall.com/cards?page=1"
        page = 0
        
        while next_page and page < 25:  # ~5000 cards
            try:
                r = requests.get(next_page, timeout=30)
                if r.status_code != 200:
                    logger.error(f"Scryfall error: {r.status_code}")
                    break
                
                data = r.json()
                cards.extend(data.get("data", []))
                next_page = data.get("next_page")
                page += 1
            except Exception as e:
                logger.error(f"Error fetching: {e}")
                break
        
        if not cards:
            return False
        
        # Download first few cards for testing
        logger.info(f"Found {len(cards)} cards, downloading samples...")
        
        # Save index
        data = {
            "cards": [
                {
                    "card_id": c["id"],
                    "name": c["name"],
                    "set_code": c.get("set", ""),
                    "number": c.get("collector_number", ""),
                    "image_url": c.get("image_uris", {}).get("normal", ""),
                    "avg_color": [128, 128, 128]
                }
                for c in cards[:100]  # First 100 for now
            ],
            "metadata": {"total": len(cards), "indexed": min(100, len(cards))}
        }
        
        with open(CARD_DIR / "index.json", "w") as f:
            json.dump(data, f)
        
        logger.info(f"Indexed {len(cards[:100])} cards")
        return True
    
    def match_card(self, image_bytes: bytes, top_k: int = 5) -> Dict[str, Any]:
        """Match a card image against cached samples."""
        if not self._index_loaded:
            self.load_index()
        
        if not self.cards:
            return {"error": "No cards indexed", "decision": "no_match", "candidates": []}
        
        # Extract average color from query image
        try:
            query_img = Image.open(__import__('io').BytesIO(image_bytes)).convert("RGB")
            pixels = list(query_img.getdata())
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            query_color = (avg_r, avg_g, avg_b)
        except Exception as e:
            return {"error": f"Failed to process image: {e}", "decision": "no_match", "candidates": []}
        
        # Find closest colors
        similarities = []
        for card in self.cards.values():
            color_dist = sum(abs(q - c) for q, c in zip(query_color, card.avg_color))
            similarities.append((color_dist, card))
        
        similarities.sort(key=lambda x: x[0])
        
        candidates = []
        for dist, card in similarities[:top_k]:
            if dist < 200:  # Reasonable color match
                candidates.append({
                    "id": card.card_id,
                    "name": card.name,
                    "set": card.set_code,
                    "number": card.number,
                    "similarity": round(1.0 - dist / 441.0, 3),  # Max possible distance ~441
                    "image_url": card.image_url
                })
        
        decision = "match" if candidates and candidates[0]["similarity"] > 0.8 else "possible_match"
        
        return {
            "decision": decision,
            "decision_reason": f"Color-based match ({candidates[0]['similarity']:.2f} confidence)" if candidates else "No similar cards",
            "candidates": candidates,
            "meta": {"source": "fast_matcher", "cards_indexed": len(self.cards)}
        }
