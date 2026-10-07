#!/usr/bin/env python3
"""
Fetch Pokémon TCG card catalog from multiple languages.
Uses TCGdex API for comprehensive multilingual coverage.
"""

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TCGdex endpoints
TCGDEX_LANGUAGES = ['en', 'de', 'fr', 'es', 'it', 'pt', 'ja']
TCGDEX_API_BASE = "https://api.tcgdex.net/v2"

# Rate limiting configuration
RATE_LIMIT_DELAY = 1.0  # seconds between requests per language
REQUEST_TIMEOUT = 30

# Output paths
DATA_DIR = Path("data")
CARD_CATALOG_PATH = DATA_DIR / "card_catalog.json"
CARD_INDEX_PATH = DATA_DIR / "clip_card_index.json"
IMAGE_DIR = DATA_DIR / "images"
CACHE_DIR = DATA_DIR / "cache"

# Create directories
DATA_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(exist_ok=True)
(CACHE_DIR / "tcgdex").mkdir(parents=True, exist_ok=True)
(CACHE_DIR / "pkmncards").mkdir(parents=True, exist_ok=True)

class TCGdexCatalogFetcher:
    def __init__(self):
        self.cards_by_id = {}
        self.card_catalog = {
            "cards": [],
            "last_updated": None,
            "sources": []
        }
        self.total_cards_fetched = 0
        
    def fetch_all_languages(self) -> Dict[str, Any]:
        """Fetch card catalogs from all languages."""
        logger.info("Starting catalog fetch from all TCGdex languages")
        
        # First fetch sets to understand catalog structure
        sets_by_language = self._fetch_all_sets()
        
        # Fetch cards from each language
        for language in TCGDEX_LANGUAGES:
            logger.info(f"Fetching {language} card catalog...")
            cards = self._fetch_language_cards(language)
            self._process_language_cards(cards, language, sets_by_language)
            
        # Resolve duplicates and prioritize
        self._resolve_duplicates_and_prioritize()
        
        # Save catalog
        self._save_catalog()
        
        return {
            "total_cards": len(self.card_catalog["cards"]),
            "languages_processed": list(self.cards_by_id.keys()),
            "duplicates_resolved": self.card_catalog.get("duplicates_resolved", 0)
        }
    
    def _fetch_all_sets(self) -> Dict[str, Any]:
        """Fetch all sets for each language."""
        sets_by_language = {}
        
        for language in TCGDEX_LANGUAGES:
            try:
                logger.info(f"Fetching {language} sets...")
                response = requests.get(
                    f"{TCGDEX_API_BASE}/{language}/sets",
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                
                sets = response.json()
                sets_by_language[language] = {
                    "sets": sets,
                    "timestamp": datetime.now().isoformat()
                }
                
                # Cache sets
                cache_file = CACHE_DIR / "tcgdex" / f"sets_{language}.json"
                with open(cache_file, 'w') as f:
                    json.dump(sets_by_language[language], f, indent=2)
                    
                # Rate limiting
                import time
                time.sleep(RATE_LIMIT_DELAY)
                
            except Exception as e:
                logger.error(f"Failed to fetch {language} sets: {e}")
                # Try to load from cache
                cache_file = CACHE_DIR / "tcgdex" / f"sets_{language}.json"
                if cache_file.exists():
                    with open(cache_file) as f:
                        sets_by_language[language] = json.load(f)
                        logger.info(f"Loaded {language} sets from cache")
                else:
                    logger.warning(f"No cache for {language} sets - continuing")
                    
        return sets_by_language
    
    def _fetch_language_cards(self, language: str) -> List[Dict]:
        """Fetch cards for a specific language."""
        try:
            logger.info(f"Fetching {language} cards...")
            response = requests.get(
                f"{TCGDEX_API_BASE}/{language}/cards",
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            cards = response.json()
            logger.info(f"Fetched {len(cards)} cards from {language}")
            return cards
            
        except Exception as e:
            logger.error(f"Failed to fetch {language} cards: {e}")
            # Try cache
            cache_file = CACHE_DIR / "tcgdex" / f"cards_{language}.json"
            if cache_file.exists():
                with open(cache_file) as f:
                    logger.info(f"Loaded {language} cards from cache")
                    return json.load(f)
            return []
    
    def _process_language_cards(self, cards: List[Dict], language: str, sets_by_language: Dict):
        """Process cards from a specific language."""
        language_cards = []
        
        for card in cards:
            # Normalize card structure
            processed_card = self._normalize_card(card, language, sets_by_language)
            language_cards.append(processed_card)
            
            # Store by TCGdex ID for easy lookup
            tcg_id = card.get("id", "")
            if tcg_id:
                self.cards_by_id.setdefault(tcg_id, {})
                self.cards_by_id[tcg_id][language] = processed_card
                
        logger.info(f"Processed {len(language_cards)} cards from {language}")
    
    def _normalize_card(self, card: Dict, language: str, sets_by_language: Dict) -> Dict:
        """Normalize card structure to our internal format."""
        # Extract set information
        set_info = card.get("set", {})
        set_id = set_info.get("id", "")
        set_name = set_info.get("name", "")
        
        # Extract number from TCGdex ID (e.g., "base1-1")
        tcg_id = card.get("id", "")
        if "-" in tcg_id:
            set_code, number = tcg_id.split("-", 1)
        else:
            # Fallback for cards without proper ID
            set_code = set_id.lower()
            number = "unknown"
        
        # Extract names from card data
        names = card.get("name", {})
        if isinstance(names, dict):
            # TCGdex often has localized names
            card_name = names.get(language, card.get("name", "Unknown"))
            translated_names = {lang: names.get(lang, "") for lang in TCGDEX_LANGUAGES}
        else:
            card_name = str(names)
            translated_names = {lang: "" for lang in TCGDEX_LANGUAGES}
            translated_names[language] = card_name
        
        # Process images
        images = card.get("images", {})
        image_urls = {}
        for img_lang in TCGDEX_LANGUAGES:
            img_data = images.get(img_lang, {}) if isinstance(images, dict) else {}
            if isinstance(img_data, dict):
                image_urls[img_lang] = img_data.get("small", "")
            else:
                image_urls[img_lang] = ""
        
        # Build metadata
        processed = {
            "id": card.get("id", ""),
            "tcgid": card.get("id", ""),
            "name": card_name,
            "names": translated_names,
            "set_id": set_id,
            "set_name": set_name,
            "set_code": set_code,
            "number": number,
            "types": card.get("types", []),
            "rarity": card.get("rarity", ""),
            "hp": card.get("hp", 0),
            "attacks": card.get("attacks", []),
            "weaknesses": card.get("weaknesses", []),
            "retreat": card.get("retreat", 0),
            "images": image_urls,
            "language": language,
            "pricing": card.get("pricing", {}),
            "colors": card.get("colors", []),
            "subtypes": card.get("subtypes", []),
            "artist": card.get("artist", ""),
            "illustration": card.get("illustration", ""),
            "tcgplayer_id": card.get("tcgplayerId", ""),
            "cardmarket_id": card.get("cardmarketId", ""),
            "added_at": datetime.now().isoformat(),
            "sources": ["tcgdex"]
        }
        
        return processed
    
    def _resolve_duplicates_and_prioritize(self):
        """Resolve duplicates across languages and prioritize primary languages."""
        logger.info("Resolving duplicates and prioritizing languages")
        
        final_cards = []
        duplicates_resolved = 0
        
        for card_id, language_versions in self.cards_by_id.items():
            # Prioritize English, then German, then others
            priority_order = ['en', 'de', 'fr', 'es', 'it', 'pt', 'ja']
            
            selected_card = None
            selected_language = None
            
            for lang in priority_order:
                if lang in language_versions:
                    selected_card = language_versions[lang]
                    selected_language = lang
                    break
            
            if not selected_card and language_versions:
                # Fallback to first available language
                selected_language = list(language_versions.keys())[0]
                selected_card = language_versions[selected_language]
            
            if selected_card:
                # Add metadata about other language versions
                other_languages = [
                    lang for lang in language_versions.keys() 
                    if lang != selected_language
                ]
                if other_languages:
                    duplicates_resolved += 1
                
                final_cards.append(selected_card)
        
        self.card_catalog["cards"] = final_cards
        self.card_catalog["duplicates_resolved"] = duplicates_resolved
        self.card_catalog["last_updated"] = datetime.now().isoformat()
        self.card_catalog["sources"] = ["tcgdex"]
        
        logger.info(f"Final catalog: {len(final_cards)} unique cards, "
                   f"{duplicates_resolved} duplicates resolved")
    
    def _save_catalog(self):
        """Save catalog to disk."""
        # Save as JSON
        with open(CARD_CATALOG_PATH, 'w') as f:
            json.dump(self.card_catalog, f, indent=2)
        
        logger.info(f"Catalog saved to {CARD_CATALOG_PATH}")
        
        # Also update clip_card_index.json for backward compatibility
        if CARD_INDEX_PATH.exists():
            with open(CARD_INDEX_PATH) as f:
                existing_index = json.load(f)
            
            # Merge new cards, avoiding duplicates
            existing_cards = {c["card_id"]: c for c in existing_index.get("cards", [])}
            
            for card in self.card_catalog["cards"]:
                card_id = f"{card['set_code']}-{card['number']}"
                if card_id not in existing_cards:
                    # Convert to clip_card_index format
                    existing_cards[card_id] = {
                        "card_id": card_id,
                        "name": card["name"],
                        "set_code": card["set_code"],
                        "number": card["number"],
                        "rarity": card["rarity"],
                        "types": card["types"],
                        "set_name": card["set_name"],
                        "images": {
                            "clip": f"/static/images/tcydex/{card['language']}/{card_id}.jpg"
                        },
                        "content_hash": "",
                        "embedding": None,
                        "pricing": card["pricing"]
                    }
            
            with open(CARD_INDEX_PATH, 'w') as f:
                json.dump({"cards": list(existing_cards.values())}, f, indent=2)
        
        logger.info("Updated clip_card_index.json for backward compatibility")

def main():
    """Main execution function."""
    print("=== Pokémon TCG Catalog Fetcher ===")
    print(f"Target languages: {', '.join(TCGDEX_LANGUAGES)}")
    print(f"Output: {CARD_CATALOG_PATH}")
    
    fetcher = TCGdexCatalogFetcher()
    
    try:
        result = fetcher.fetch_all_languages()
        
        print("\n=== Fetch Complete ===")
        print(f"Total cards processed: {result['total_cards']}")
        print(f"Languages processed: {', '.join(result['languages_processed'])}")
        print(f"Duplicates resolved: {result['duplicates_resolved']}")
        print(f"Catalog saved to: {CARD_CATALOG_PATH}")
        
        # Print sample of cards
        if fetcher.card_catalog["cards"]:
            print(f"\nSample card:")
            sample = fetcher.card_catalog["cards"][0]
            print(f"  ID: {sample['id']}")
            print(f"  Name: {sample['name']} ({sample['language']})")
            print(f"  Set: {sample['set_name']} ({sample['set_code']})")
            print(f"  Number: {sample['number']}")
            print(f"  Languages available: {sample['language']}")
        
    except Exception as e:
        logger.error(f"Catalog fetch failed: {e}")
        raise

if __name__ == "__main__":
    main()
