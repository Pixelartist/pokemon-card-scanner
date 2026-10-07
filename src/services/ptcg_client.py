"""Pokemon TCG API client for card identification and database sync."""
import os
import requests
import base64
import logging
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Pokemon TCG API configuration
def _get_api_key() -> str:
    """Lazily read API key so dotenv changes are picked up."""
    return os.environ.get("PTCG_API_KEY", "")

PTCG_API_BASE = "https://api.pokemontcgapi.com/v1"


class PokemonTCGClient:
    """Client for Pokemon TCG API - vision recognition and card database."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or _get_api_key()
        # Reject placeholder/invalid keys early
        if self._api_key and self._api_key in ("your_api_key_here", "PLACEHOLDER"):
            self._api_key = ""

    @property
    def api_key(self) -> str:
        """Read key dynamically so dotenv changes are picked up."""
        return self._api_key or _get_api_key()

    @property
    def headers(self) -> dict:
        """Build headers with current API key (lazy)."""
        return {"X-Api-Key": self.api_key}

    def identify_card(self, image_bytes: bytes, top_k: int = 5,
                      set_hint: str = None, region: str = None) -> Dict[str, Any]:
        """Identify a Pokemon card from an image using the vision API.
        
        Enhanced with comprehensive error handling:
        - HTML response detection and logging
        - Automatic fallback to offline matchers
        - Detailed error diagnostics
        - Multi-layer fallback strategy
        """
        if not self.api_key:
            # No API key at all — go straight to offline
            logger.info("No API key found, using offline matcher.")
            return self._identify_offline(image_bytes, top_k)

        try:
            # Encode image to base64
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            data_url = f"data:image/jpeg;base64,{image_b64}"

            payload = {
                "image": data_url,
                "top_k": top_k,
            }
            if set_hint:
                payload["set"] = set_hint
            if region:
                payload["region"] = region

            resp = requests.post(
                f"{PTCG_API_BASE}/vision/identify",
                headers={**self.headers, "Content-Type": "application/json"},
                json=payload,
                timeout=30
            )

            # Check if response is HTML (error page) or JSON
            content_type = resp.headers.get('content-type', '')
            is_json = 'application/json' in content_type
            is_html = 'text/html' in content_type

            if resp.status_code == 200 and is_json:
                return resp.json()
            elif resp.status_code == 403:
                # Check for specific error types
                if is_json:
                    error_data = resp.json() if resp.text else {}
                else:
                    # Response is HTML - treat as API error, fall back to offline
                    logger.warning(f"API returned HTML error page for status 403")
                    return self._identify_offline(image_bytes, top_k)
                
                error_code = error_data.get('error', {}).get('code', '') if isinstance(error_data.get('error'), dict) else ''
                error_msg = error_data.get('error', {}).get('message', '') if isinstance(error_data.get('error'), dict) else str(error_data)

                if error_code == 'PLAN_REQUIRED':
                    if 'verify_email' in error_msg:
                        return {
                            "error": "Email verification required. Please verify your email at https://pokemontcgapi.com/account to unlock 5 free trial card recognitions. After verification, try again.",
                            "decision": "no_match",
                            "candidates": [],
                            "solution": "verify_email"
                        }
                    else:
                        # Trial exhausted — try offline fallback
                        logger.info("API trial exhausted, falling back to offline matcher")
                        return self._identify_offline(image_bytes, top_k)
                else:
                    # Invalid key — try offline fallback
                    logger.info("Invalid API key, falling back to offline matcher")
                    return self._identify_offline(image_bytes, top_k)
            elif resp.status_code == 422 and is_json:
                return {"error": "Invalid image", "decision": "no_match", "candidates": []}
            elif resp.status_code == 503 and is_json:
                return {"error": "Vision feature not configured", "decision": "no_match", "candidates": []}
            else:
                # Handle HTML responses or other error codes
                if is_html:
                    logger.warning(f"API returned HTML error page (status {resp.status_code}): {resp.text[:200]}")
                else:
                    logger.warning(f"API error {resp.status_code}, trying offline fallback")
                # Always fall back to offline matcher for HTML responses or unknown errors
                return self._identify_offline(image_bytes, top_k)

        except requests.exceptions.Timeout:
            return {"error": "Request timed out", "decision": "no_match", "candidates": []}
        except requests.exceptions.JSONDecodeError:
            # Specifically handle JSON decode errors (HTML response)
            logger.warning("API returned non-JSON response, falling back to offline matcher")
            return self._identify_offline(image_bytes, top_k)
        except Exception as e:
            logger.error(f"Identification error: {e}")
            return {"error": str(e), "decision": "no_match", "candidates": []}

    def _identify_offline(self, image_bytes: bytes, top_k: int = 5) -> Dict[str, Any]:
        """Fallback: identify card using offline matchers with comprehensive error handling and diagnostics."""
        
        # Try CLIP matcher with enhanced error handling
        try:
            from .clip_matcher import get_clip_matcher
            matcher = get_clip_matcher()
            
            if matcher.has_usable_index():
                try:
                    result = matcher.match_card(image_bytes, top_k=top_k)
                    # Validate result structure
                    if isinstance(result, dict) and 'candidates' in result:
                        logger.info(f"CLIP matcher successful: {len(result.get('candidates', []))} candidates")
                        return result
                    else:
                        logger.warning(f"CLIP matcher returned invalid result format: {result}")
                except Exception as e:
                    logger.warning(f"CLIP matcher execution failed: {e}")
                    # Continue to fast matcher fallback
            else:
                logger.debug("CLIP matcher index not available")
                
        except Exception as e:
            logger.debug(f"CLIP matcher setup failed: {e}")
            # Continue to fast matcher fallback

        # Try Fast matcher with enhanced error handling
        try:
            from .fast_matcher import FastCardMatcher
            matcher = FastCardMatcher()
            
            if matcher.load_index():
                try:
                    result = matcher.match_card(image_bytes, top_k=top_k)
                    # Validate result structure
                    if isinstance(result, dict) and 'candidates' in result:
                        logger.info(f"Fast matcher successful: {len(result.get('candidates', []))} candidates")
                        return result
                    else:
                        logger.warning(f"Fast matcher returned invalid result format: {result}")
                except Exception as e:
                    logger.warning(f"Fast matcher execution failed: {e}")
                    # Continue to final error state
            else:
                logger.warning("Fast matcher index loading failed")
                
        except Exception as e:
            logger.debug(f"Fast matcher setup failed: {e}")
            # Continue to final error state

        # Try CardDetector as tertiary fallback
        try:
            from .card_detector import CardDetector
            detector = CardDetector()
            
            try:
                result = detector.detect_card(image_bytes)
                # Validate result structure
                if isinstance(result, dict) and result.get('found', False):
                    logger.info(f"Card detector successful: bbox={result.get('bbox')}, confidence={result.get('confidence')}")
                    return {
                        "candidates": [{
                            "id": f"detector_{int(time.time())}",
                            "name": "Detected via computer vision",
                            "similarity": result.get('confidence', 0),
                            "source": "detector",
                            "detection_method": result.get('detector_type', 'unknown'),
                            "bbox": result.get('bbox')
                        }],
                        "decision": "match",
                        "source": "detector",
                        "meta": {
                            "detection_confidence": result.get('confidence', 0),
                            "detection_method": result.get('detector_type', 'unknown')
                        }
                    }
                else:
                    logger.debug(f"Card detector no card found: {result}")
            except Exception as e:
                logger.debug(f"Card detector execution failed: {e}")
                # Continue to final error state
        except Exception as e:
            logger.debug(f"Card detector setup failed: {e}")
            # Continue to final error state

        # Final error state with comprehensive diagnostics
        return {
            "error": "All offline matchers failed",
            "decision": "no_match",
            "candidates": [],
            "meta": {
                "hint": "Try again with a clearer image, or build index via /api/index/build",
                "debug_info": {
                    "timestamp": datetime.utcnow().isoformat(),
                    "environment": "enhanced_fallback_mode"
                }
            }
        }

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Get a single card by ID with robust error handling and fallback."""
        # First try to get from offline database if available
        try:
            # Check if we have local database
            db_path = Path("/opt/data/pokemon-card-scanner/data/pokemon_cards.db")
            if db_path.exists() and os.path.getsize(db_path) > 0:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM cards WHERE ptcg_id = ?", (card_id,))
                row = cursor.fetchone()
                
                if row:
                    # Convert row to dict
                    card_dict = dict(row)
                    # Convert SQLite types to Python types
                    for key in card_dict:
                        if isinstance(card_dict[key], (bytes, bytearray)):
                            card_dict[key] = card_dict[key].decode('utf-8')
                    conn.close()
                    return card_dict
                
                conn.close()
        except Exception as e:
            logger.debug(f"Local database lookup failed for {card_id}: {e}")

        # Fallback to API call
        try:
            resp = requests.get(
                f"{PTCG_API_BASE}/cards/{card_id}",
                headers=self.headers,
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json().get("data")
            elif resp.status_code == 404:
                logger.info(f"Card {card_id} not found in API")
                return None
            else:
                logger.warning(f"API error fetching card {card_id}: {resp.status_code}")
                return None
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching card {card_id} from API")
            return None
        except Exception as e:
            logger.error(f"Error fetching card {card_id} from API: {e}")
            return None

    def get_cards_batch(self, card_ids: List[str]) -> List[Dict[str, Any]]:
        """Get multiple cards in one request."""
        if not card_ids:
            return []
        try:
            ids_str = ",".join(card_ids)
            resp = requests.get(
                f"{PTCG_API_BASE}/cards/batch",
                params={"ids": ids_str, "include": "images,prices"},
                headers=self.headers,
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
            return []
        except Exception as e:
            logger.error(f"Error fetching batch cards: {e}")
            return []

    def get_sets(self) -> List[Dict[str, Any]]:
        """Get all card sets."""
        try:
            resp = requests.get(
                f"{PTCG_API_BASE}/sets",
                headers=self.headers,
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
            return []
        except Exception as e:
            logger.error(f"Error fetching sets: {e}")
            return []


# Singleton instance
_client: Optional[PokemonTCGClient] = None


def get_client() -> PokemonTCGClient:
    global _client
    if _client is None:
        _client = PokemonTCGClient()
    return _client