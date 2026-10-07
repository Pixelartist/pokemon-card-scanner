"""
CLIP-based Pokemon card matcher (PokeScope-style, fully local).

Implements the PokeScope approach:
    - Download card artwork from the official public CDN (no API needed)
    - Embed images with OpenAI CLIP ViT-B/32 (CPU, open_clip)
    - Match a photo of a card against the embedding index by cosine similarity

The index is built incrementally: small seed first, expand in background.
"""
import os
import json
import logging
import hashlib
import time
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from urllib.parse import quote
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv

load_dotenv('/opt/data/pokemon-card-scanner/.env')

logger = logging.getLogger(__name__)

CARD_IMAGE_BASE = "https://images.pokemontcg.io"
# TCGdex: free, no-auth card catalog + image CDN (primary source)
TCGDEX_CARDS_URL = "https://api.tcgdex.net/v2/en/cards"
TCGDEX_IMAGE_BASE = "https://assets.tcgdex.net/en/{set_id}/{local_id}/high.webp"

# German TCGdex API
TCGDEX_DE_CARDS_URL = "https://api.tcgdex.net/v2/de/cards"
TCGDEX_DE_IMAGE_BASE = "https://assets.tcgdex.net/de/{set_id}/{local_id}/high.png"

DB_DIR = Path(__file__).parent.parent.parent / "data"
INDEX_PATH = DB_DIR / "clip_card_index.json"
EMBEDDINGS_PATH = DB_DIR / "clip_embeddings.npz"
CACHE_DIR = DB_DIR / "clip_images"
GERMAN_IMAGES_DIR = DB_DIR / "german_cards" / "images"

# Popular set codes to prioritize (recent + classic western sets)
POPULAR_SETS = [
    # Recent / current
    "svpt", "svi2", "svi", "sws2", "sws", "s14", "s13", "s12", "s11",
    "s10", "s09", "s08", "s07", "s06", "s05", "s04", "s03", "s02", "s01",
    # Classic
    "sm8f", "sm14", "sm13b", "sm13a", "sm13", "sm12f", "sm12b", "sm12",
    "xyf", "xy17f", "xy17", "xy16", "xy15", "xy14", "xy13", "xy12",
    "xy11f", "xy11", "xy10", "xy9", "xy8", "xy7", "xy6f", "xy6",
    "xy5", "xy4", "xy3", "xy2f", "xy2", "xy1", "bs3f", "bs3",
    "ht", "hgss", "dppt", "dphf", "dprpt", "dp4f", "dp4", "dp3",
    "e", "t", "g", "lp", "gf", "dp", "pt", "htss", "plf", "rs", "eb",
    "ex", "base", "jungle", "fossil", "teamrocket", "gym",
]

# Hardcoded fallback: set_code -> (min_number, max_number) for when API is rate-limited
SET_RANGES = {
    "base": (1, 102),
    "jungle": (1, 64),
    "fossil": (1, 62),
    "teamrocket": (1, 83),
    "gym": (1, 102),
    "gym2": (1, 112),
    "ecard": (1, 165),
    "undiscovered": (1, 115),
    "lost": (1, 100),
    "dragon": (1, 100),
    "explorers": (1, 116),
    "mantle": (1, 127),
    "sky": (1, 122),
    "diamond": (1, 107),
    "pearl": (1, 107),
    "plat": (1, 134),
    "heartgold": (1, 123),
    "soulsilver": (1, 127),
    "blackwhite": (1, 114),
    "base2": (1, 90),
    "solar": (1, 110),
    "moon": (1, 101),
    "unleashed": (1, 111),
    "unforgotten": (1, 95),
    "dragonex": (1, 100),
    "neo1": (1, 111),
    "neo2": (1, 64),
    "neo3": (1, 76),
    "neo4": (1, 59),
    "genesis": (1, 111),
    "discovery": (1, 82),
    "revelation": (1, 75),
    "destiny": (1, 113),
    "ruby": (1, 110),
    "silver": (1, 111),
    "twilight": (1, 111),
    "power": (1, 102),
    "hidden": (1, 106),
    "dragonex2": (1, 101),
    "emerald": (1, 107),
    "unbound": (1, 101),
    "deoxys": (1, 108),
    "mysterious": (1, 82),
    "secret": (1, 71),
    "legend": (1, 110),
    "holon": (1, 112),
    "dragonfrontiers": (1, 106),
    "powerkeepers": (1, 108),
    "din": (1, 100),
    "pac": (1, 107),
    "soaring": (1, 106),
    "majestic": (1, 117),
    "holonphantoms": (1, 111),
    "hiddenfates": (1, 218),
    "burningshadows": (1, 175),
    "unbrokenbindings": (1, 214),
    "unbrokenclaws": (1, 73),
    "unconflictedcodes": (1, 73),
    "ultramoon": (1, 197),
    "ultrashine": (1, 181),
    "supremevictors": (1, 138),
    "steamvault": (1, 114),
    "sunsetburst": (1, 111),
    "roaringskyrise": (1, 111),
    "primalclash": (1, 160),
    "primalcrisis": (1, 176),
    "plasmafree": (1, 73),
    "plasmapoxy": (1, 152),
    "plasmarise": (1, 138),
    "legendarytreasures": (1, 183),
    "legendsofpokemon": (1, 100),
    "legendofxatu": (1, 52),
    "dragonmajesty": (1, 132),
    "darkexplorers": (1, 134),
    "crownzenith": (1, 233),
    "celebrations": (1, 50),
    "battlestyles": (1, 81),
    "shinytreasures": (1, 104),
    "shiningfates": (1, 122),
    "secretwastes": (1, 79),
    "shiningfates2": (1, 141),
    "scarletviolet": (1, 258),
    "s11s": (1, 26),
    "s12s": (1, 23),
    "s13s": (1, 23),
    "s14s": (1, 26),
    "s1s": (1, 25),
    "s2s": (1, 34),
    "s3s": (1, 36),
    "s4s": (1, 28),
    "s5s": (1, 32),
    "s6s": (1, 24),
    "s7s": (1, 22),
    "s8s": (1, 24),
    "s9s": (1, 28),
    "s10s": (1, 22),
}


def resolve_card_image_url(card_id: str, raw_url: str = "") -> str:
    """Resolve a card image to a servable /static/card_images/... URL.

    The persisted CLIP index contains stale URLs (typo'd 'tcydex' segment,
    '.jpg' extension) that don't match the on-disk files under ../data/images/.
    Check the known on-disk locations for the card ID and return a URL the
    /static/card_images mount can actually serve. Returns '' when no file exists.
    """
    if not card_id:
        return ""
    images_dir = DB_DIR / "images"
    cid = card_id.replace("/", "_").replace("!", "")
    candidates = [
        f"tcgdex/en/{cid}.png",
        f"official/en/{cid}.png",
        f"pkmncards/{cid}.png",
        f"german_cards/images/de_{cid}.png",
    ]
    # Also try the exact path stored in the index (extension-corrected),
    # in case a card lives outside the standard locations.
    if raw_url and "/images/" in raw_url:
        rel = raw_url.split("/images/", 1)[1].replace("tcydex/", "tcgdex/")
        candidates.insert(0, rel)
        candidates.insert(1, rel.rsplit(".", 1)[0] + ".png")
    for rel in candidates:
        if (images_dir / rel).is_file():
            return f"/static/card_images/{rel}"
    # Case-insensitive fallback for IDs like 'SV8-119' vs 'sv8-119'
    cid_lower = cid.lower()
    for sub in ("tcgdex/en", "official/en", "pkmncards"):
        d = images_dir / sub
        if not d.is_dir():
            continue
        for name in (f"{cid_lower}.png", f"{cid}.png"):
            # direct check first (fast path), then lowercase scan
            if (d / name).is_file() and name == f"{cid_lower}.png" and cid_lower != cid:
                continue  # don't force lowercase when the exact ID missed later
            if (d / name).is_file():
                return f"/static/card_images/{sub}/{name}"
        # final: scan directory listing for a case-insensitive match
        try:
            for entry in os.listdir(d):
                if entry.lower() == f"{cid_lower}.png":
                    return f"/static/card_images/{sub}/{entry}"
        except OSError:
            pass
    return ""


@dataclass
class CardEmbedding:
    card_id: str
    name: str
    set_code: str
    number: str
    embedding: List[float]
    image_url: str
    content_hash: str
    set_name: str = ""
    # Optional TCGdex metadata
    rarity: str = ""
    types: List[str] = field(default_factory=list)
    hp: int = 0
    artist: str = ""
    attacks: List[Dict] = field(default_factory=list)
    weaknesses: List[Dict] = field(default_factory=list)
    retreat: int = 0
    pricing: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = asdict(self)
        # embedding is a list of floats, keep it
        return d

    @classmethod
    def from_dict(cls, data: Dict) -> 'CardEmbedding':
        return cls(
            card_id=data.get('card_id', ''),
            name=data.get('name', ''),
            set_code=data.get('set_code', ''),
            number=data.get('number', ''),
            embedding=data.get('embedding'),
            image_url=data.get('image_url', '') or resolve_card_image_url(data.get('card_id', ''), data.get('image_url', '')),
            content_hash=data.get('content_hash', ''),
            set_name=data.get('set_name', ''),
            rarity=data.get('rarity', ''),
            types=data.get('types', []),
            hp=data.get('hp', 0),
            attacks=data.get('attacks', []),
            weaknesses=data.get('weaknesses', []),
            retreat=data.get('retreat', 0),
            pricing=data.get('pricing', {}),
        )


class CLIPCardMatcher:
    """Offline card matcher using CLIP embeddings."""

    def __init__(self, db_dir: Path = None):
        self.db_dir = db_dir or DB_DIR
        self.index_path = self.db_dir / "clip_card_index.json"
        self.embeddings_path = self.db_dir / "clip_embeddings.npz"
        self.cache_dir = self.db_dir / "clip_images"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.cards: Dict[str, CardEmbedding] = {}
        self.card_list: List[CardEmbedding] = []
        self._index_loaded = False
        # Set name cache: code -> name
        self._set_names: Dict[str, str] = {}

        self._model = None
        self._preprocess = None
        import torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self._build_lock = threading.Lock()
        self._build_thread: Optional[threading.Thread] = None
        self._build_progress = {
            "running": False,
            "done": 0,
            "total": 0,
            "last_error": None,
            "api_rate_limited": False,
            "next_reset": None,
        }

    # ------------------------------------------------------------------ model

    def _load_model(self):
        """Lazily load CLIP ViT-B/32 (OpenAI weights)."""
        if self._model is not None:
            return
        import open_clip
        import torch
        logger.info("Loading CLIP ViT-B/32 on %s ...", self.device)
        result = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')
        # open_clip >= 3.x returns (model, train_preproc, val_preproc)
        if len(result) == 3:
            self._model, _, self._preprocess = result
        else:
            self._model, self._preprocess = result
        self._model.to(self.device)
        self._model.eval()
        logger.info("CLIP model ready")

    # ------------------------------------------------------------------- index

    def load_index(self) -> bool:
        """Load persisted CLIP index from disk (fast, no downloads)."""
        if self._index_loaded:
            return True
        if not (self.index_path.exists() and self.embeddings_path.exists()):
            return False
        import numpy as np
        import torch
        with open(self.index_path) as f:
            data = json.load(f)
        embeddings_data = np.load(self.embeddings_path)
        embeddings = embeddings_data["embeddings"]
        card_ids_npz = embeddings_data["card_ids"]
        
        # Build a lookup dict: card_id -> embedding
        emb_lookup = {}
        for cid, emb in zip(card_ids_npz, embeddings):
            emb_lookup[cid] = emb
        
        loaded_count = 0
        for card_data in data.get("cards", []):
            card_id = card_data.get("card_id")
            if card_id not in emb_lookup:
                continue
            ce = CardEmbedding.from_dict(card_data)
            ce.embedding = emb_lookup[card_id].tolist()
            ce.image_url = resolve_card_image_url(ce.card_id, ce.image_url)
            if ce.card_id not in self.cards:
                self.cards[ce.card_id] = ce
                self.card_list.append(ce)
            loaded_count += 1
        
        self._index_loaded = True
        logger.info("Loaded CLIP index: %d cards", len(self.cards))
        return len(self.cards) > 0

    def index_size(self) -> int:
        return len(self.cards)

    def has_usable_index(self, min_cards: int = 20) -> bool:
        return self.load_index() and len(self.cards) >= min_cards

    def status(self) -> Dict[str, Any]:
        if not self._index_loaded:
            self.load_index()
        p = self._build_progress
        return {
            "indexed": len(self.cards),
            "loading": p["running"],
            "build_done": p["done"],
            "build_total": p["total"],
            "last_error": p["last_error"],
            "api_rate_limited": p.get("api_rate_limited", False),
            "next_reset": p.get("next_reset"),
            "usable": len(self.cards) >= 20,
            "device": self.device,
        }

    def build_index(self, max_cards: int = 1000, in_background: bool = True) -> bool:
        """Fetch card list + download artwork + embed. Resumable."""
        with self._build_lock:
            if self._build_progress["running"]:
                return True  # already running
            if not in_background:
                return self._build_sync(max_cards)
            self._build_thread = threading.Thread(
                target=self._build_sync, args=(max_cards,), daemon=True)
            self._build_thread.start()
            return True

    def _build_sync(self, max_cards: int) -> bool:
        self._load_model()
        self._build_progress.update(running=True, total=max_cards, last_error=None)
        try:
            if self._index_loaded and not self.cards:
                self.load_index()

            logger.info("Building CLIP index (target %d cards)...", max_cards)
            cards = self._fetch_cards()
            if not cards:
                logger.warning("API fetch returned no cards; trying fallback ranges")
                cards = self._fetch_cards_fallback()
            if not cards:
                if self._build_progress.get("api_rate_limited"):
                    self._build_progress["last_error"] = (
                        f"API rate limited. {self._build_progress.get('next_reset', 'Wait and retry.')}"
                    )
                else:
                    self._build_progress["last_error"] = "no cards fetched"
                return False

            selected = self._select_cards(cards, max_cards)
            # Collect TCGdex image URLs for fast lookup
            self._tcgdex_images = {c.get("id"): c.get("image_url") for c in cards if c.get("image_url")}
            new = 0
            BATCH_SIZE = 32
            batch_paths, batch_cards = [], []

            def _flush_batch():
                nonlocal new, batch_paths, batch_cards
                if not batch_paths:
                    return
                img_bytes_list = []
                for p in batch_paths:
                    try:
                        with open(p, "rb") as f:
                            img_bytes_list.append(f.read())
                    except Exception:
                        continue
                embs = self._embed_batch(img_bytes_list)
                for idx, (p, c) in enumerate(zip(batch_paths, batch_cards)):
                    if embs[idx] is None:
                        continue
                    ce = c
                    ce.embedding = embs[idx]
                    ce.image_url = resolve_card_image_url(ce.card_id, ce.image_url)
                    self.cards[ce.card_id] = ce
                    self.card_list.append(ce)
                    new += 1
                    self._build_progress["done"] = new
                batch_paths, batch_cards = [], []

            for i, card in enumerate(selected):
                if self._build_progress.get("_cancelled"):
                    break
                card_id = card.get("id", "")
                if not card_id or card_id in self.cards:
                    continue
                # TCGdex uses set_id; pokemontcgapi uses set_code
                set_code = card.get("set_id") or card.get("set_code", "")
                number = card.get("number", "")
                name = card.get("name", "")
                # TCGdex card dicts carry a pre-built image_url
                card_image_url = card.get("image_url", "")

                img_path = self._download_card_image(set_code, number, card_id, card_image_url)
                if not img_path:
                    continue

                content_hash = hashlib.md5(img_path.read_bytes()).hexdigest()
                # Use local cached image path (no external URLs needed)
                local_img_url = f"/static/clip_images/{os.path.basename(img_path)}"
                # Use set name from TCGdex if available
                sname = card.get("set_name", "") or self._set_names.get(set_code, set_code)

                # Extract rich metadata from TCGdex card
                rarity = card.get("rarity", "")
                types = card.get("types", [])
                hp = card.get("hp", 0)
                attacks = card.get("attacks", [])
                weaknesses = card.get("weaknesses", [])
                retreat = card.get("retreat", 0)
                pricing = card.get("pricing", {})

                ce = CardEmbedding(
                    card_id=card_id, name=name, set_code=set_code, number=number,
                    embedding=[],  # placeholder, filled by batch
                    image_url=local_img_url,
                    content_hash=content_hash,
                    set_name=sname,
                    rarity=rarity,
                    types=types,
                    hp=hp,
                    attacks=attacks,
                    weaknesses=weaknesses,
                    retreat=retreat,
                    pricing=pricing,
                )
                batch_paths.append(img_path)
                batch_cards.append(ce)
                if len(batch_paths) >= BATCH_SIZE:
                    _flush_batch()

                if new % 50 == 0:
                    self._save_index()

            # Flush remaining
            _flush_batch()
            self._save_index()
            elapsed_msg = f"Indexed {new} new cards (total {len(self.cards)})"
            logger.info(elapsed_msg)
            return new > 0 or len(self.cards) > 0
        except Exception as e:
            logger.error("Index build failed: %s", e)
            self._build_progress["last_error"] = str(e)
            self._save_index()
            return False
        finally:
            self._build_progress["running"] = False

    def _select_cards(self, cards: List[Dict], max_cards: int) -> List[Dict]:
        """Pick popular-set cards first."""
        # TCGdex cards use set_id; legacy cards use set_code
        def set_key(c):
            return (c.get("set_id") or c.get("set_code", "")).lower()
        popular = [c for c in cards if set_key(c) in POPULAR_SETS]
        rest = [c for c in cards if set_key(c) not in POPULAR_SETS]
        selected = popular[:max_cards]
        if len(selected) < max_cards:
            selected += rest[:max_cards - len(selected)]
        return selected

    def _save_index(self):
        import numpy as np
        if not self.card_list:
            return
        data = {
            "cards": [asdict(c) | {"embedding": None} for c in self.card_list],
            "metadata": {"total": len(self.card_list), "updated_at": time.ctime()},
        }
        tmp = self.index_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.rename(self.index_path)

        embeddings = np.array([c.embedding for c in self.card_list], dtype=np.float32)
        np.savez(self.embeddings_path, embeddings=embeddings)

    # ------------------------------------------------------------------- fetch

    def enrich_metadata(self) -> int:
        """Fetch full TCGdex metadata for all indexed cards. Returns count enriched."""
        if not self._index_loaded:
            self.load_index()
        enriched = 0
        total = len(self.cards)
        logger.info("Enriching metadata for %d cards...", total)
        for i, ce in enumerate(self.card_list):
            card_id = ce.card_id
            try:
                r = requests.get(f"{TCGDEX_CARDS_URL}/{card_id}", timeout=10)
                if r.status_code == 200:
                    full = r.json()
                    has_metadata = bool(full.get("rarity") or full.get("hp") or full.get("attacks"))
                    if has_metadata:
                        ce.rarity = full.get("rarity", "")
                        ce.types = full.get("types", [])
                        ce.hp = full.get("hp", 0)
                        ce.attacks = full.get("attacks", [])
                        ce.weaknesses = full.get("weaknesses", [])
                        ce.retreat = full.get("retreat", 0)
                        ce.pricing = full.get("pricing", {})
                        enriched += 1
                elif r.status_code == 404:
                    pass  # card not found, skip
                else:
                    time.sleep(0.1)  # rate limit backoff
            except Exception as e:
                logger.debug("Failed to enrich %s: %s", card_id, e)
            if (i + 1) % 50 == 0:
                logger.info("Enriched %d/%d so far (%d with metadata)", i + 1, total, enriched)
                self._save_index()
        self._save_index()
        logger.info("Metadata enrichment complete: %d/%d cards enriched", enriched, total)
        return enriched

    def _fetch_cards_tcgdex_german(self, max_cards: int = 2500) -> List[Dict]:
        """Fetch German TCGdex catalog (paginated, up to max_cards)."""
        logger.info("Fetching German TCGdex catalog (max %d cards)...", max_cards)
        all_cards: List[Dict] = []
        page = 1
        page_size = 500

        while len(all_cards) < max_cards:
            params = {"pagination:itemsPerPage": page_size}
            if page > 1:
                params["pagination:page"] = page
            try:
                r = requests.get(TCGDEX_DE_CARDS_URL, params=params, timeout=30)
                if r.status_code != 200:
                    logger.error("TCGdex DE fetch failed: %s", r.text[:200])
                    break
                cards = r.json()
                if not cards:
                    break
                # Only take what we need
                remaining = max_cards - len(all_cards)
                all_cards.extend(cards[:remaining])
                logger.info("Fetched page %d: %d cards (total: %d/%d)", page, len(cards), len(all_cards), max_cards)
                page += 1
                time.sleep(0.1)
            except Exception as e:
                logger.warning("Error fetching German cards: %s", e)
                break

        # Only fetch details for the cards we're going to use
        logger.info("Fetching full details for %d German cards...", len(all_cards))
        detailed_cards = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self._fetch_card_detail, c["id"]): c
                for c in all_cards
            }
            for future in as_completed(futures):
                card_detail = future.result()
                if card_detail:
                    detailed_cards.append(card_detail)
                if len(detailed_cards) % 50 == 0:
                    logger.info("Fetched details for %d/%d German cards...", len(detailed_cards), len(all_cards))

        # Build set names and serie mapping
        self._german_set_names = {}
        self._german_set_serie = {}
        try:
            sets_r = requests.get("https://api.tcgdex.net/v2/de/sets", timeout=15)
            if sets_r.status_code == 200:
                for s in sets_r.json():
                    set_id = s.get("id", "")
                    if set_id:
                        self._german_set_names[set_id] = s.get("name", "")
                logger.info("Loaded %d German set names", len(self._german_set_names))
        except Exception as e:
            logger.warning("Failed to load German TCGdex sets: %s", e)

        # Build set_id -> serie_id mapping from individual set details
        try:
            logger.info("Fetching German set details for serie mapping...")
            sets_list_r = requests.get("https://api.tcgdex.net/v2/de/sets", timeout=15)
            if sets_list_r.status_code == 200:
                for s in sets_list_r.json():
                    set_id = s.get("id", "")
                    if not set_id:
                        continue
                    # Fetch set detail to get serie
                    set_detail_r = requests.get(f"https://api.tcgdex.net/v2/de/sets/{set_id}", timeout=10)
                    if set_detail_r.status_code == 200:
                        set_detail = set_detail_r.json()
                        serie = set_detail.get("serie", {})
                        serie_id = serie.get("id", "") if serie else ""
                        if serie_id:
                            self._german_set_serie[set_id] = serie_id
                logger.info("Built serie mapping for %d German sets", len(self._german_set_serie))
        except Exception as e:
            logger.warning("Failed to load German set details: %s", e)

        logger.info("Total German cards fetched: %d", len(detailed_cards))
        return detailed_cards

    def _fetch_card_detail(self, card_id: str) -> Dict | None:
        """Fetch full details for a single card."""
        try:
            r = requests.get(f"{TCGDEX_DE_CARDS_URL}/{card_id}", timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def build_german_index(self, max_cards: int = 2500, in_background: bool = True) -> bool:
        """Build CLIP index from German TCGdex cards. Adds to existing index."""
        with self._build_lock:
            if self._build_progress["running"]:
                return False
            if not in_background:
                return self._build_german_sync(max_cards)
            self._build_thread = threading.Thread(
                target=self._build_german_sync, args=(max_cards,), daemon=True)
            self._build_thread.start()
            return True

    def _build_german_sync(self, max_cards: int) -> bool:
        """Synchronous German index build."""
        self._load_model()
        self._build_progress.update(running=True, total=max_cards, last_error=None)
        try:
            if self._index_loaded:
                self.load_index()

            logger.info("Building German CLIP index (target %d cards)...", max_cards)
            cards = self._fetch_cards_tcgdex_german()
            if not cards:
                self._build_progress["last_error"] = "No German cards fetched"
                return False

            new = 0
            BATCH_SIZE = 32
            batch_paths, batch_cards = [], []

            def _flush_batch():
                nonlocal new, batch_paths, batch_cards
                if not batch_paths:
                    return
                img_bytes_list = []
                for p in batch_paths:
                    try:
                        with open(p, "rb") as f:
                            img_bytes_list.append(f.read())
                    except Exception:
                        continue
                embs = self._embed_batch(img_bytes_list)
                for idx, (p, c) in enumerate(zip(batch_paths, batch_cards)):
                    if embs[idx] is None:
                        continue
                    ce = c
                    ce.embedding = embs[idx]
                    ce.image_url = resolve_card_image_url(ce.card_id, ce.image_url)
                    self.cards[ce.card_id] = ce
                    self.card_list.append(ce)
                    new += 1
                    self._build_progress["done"] = new
                batch_paths, batch_cards = [], []

            for i, card in enumerate(cards[:max_cards]):
                if self._build_progress.get("_cancelled"):
                    break
                card_id = card.get("id", "")
                if not card_id or card_id in self.cards:
                    continue

                local_id = card_id.split("-")[-1] if "-" in card_id else ""
                set_code = card.get("set", {}).get("id", "") or (card_id.split("-")[0] if "-" in card_id else "")

                # Build image URL with serie prefix if available
                serie_code = self._german_set_serie.get(set_code, "")
                if serie_code:
                    image_url = f"https://assets.tcgdex.net/de/{serie_code}/{set_code}/{local_id}/high.png"
                else:
                    image_url = f"https://assets.tcgdex.net/de/{set_code}/{local_id}/high.png"

                img_path = self._download_german_card_image(image_url, card_id, local_id)
                if not img_path:
                    continue

                content_hash = hashlib.md5(img_path.read_bytes()).hexdigest()
                local_img_url = f"/static/german_cards/images/{os.path.basename(img_path)}"

                sname = self._german_set_names.get(set_code, set_code)
                rarity = card.get("rarity", "")
                types = card.get("types", [])
                hp = card.get("hp", 0)
                attacks = card.get("attacks", [])
                weaknesses = card.get("weaknesses", [])
                retreat = card.get("retreat", 0)
                pricing = card.get("pricing", {})

                ce = CardEmbedding(
                    card_id=card_id,
                    name=card.get("name", ""),
                    set_code=set_code,
                    number=local_id,
                    embedding=[],
                    image_url=local_img_url,
                    content_hash=content_hash,
                    set_name=sname,
                    rarity=rarity,
                    types=types,
                    hp=hp,
                    attacks=attacks,
                    weaknesses=weaknesses,
                    retreat=retreat,
                    pricing=pricing,
                )
                batch_paths.append(img_path)
                batch_cards.append(ce)

                if len(batch_paths) >= BATCH_SIZE:
                    _flush_batch()

                if (i + 1) % 100 == 0:
                    logger.info("Progress: %d/%d German cards processed", i + 1, len(cards[:max_cards]))
                    self._save_index()

            _flush_batch()
            self._save_index()
            logger.info("German index built: %d new cards (total %d)", new, len(self.cards))
            return new > 0 or len(self.cards) > 0
        except Exception as e:
            logger.error("German index build failed: %s", e)
            self._build_progress["last_error"] = str(e)
            self._save_index()
            return False
        finally:
            self._build_progress["running"] = False

    def _download_german_card_image(self, image_url: str, card_id: str, local_id: str) -> Optional[Path]:
        """Download a single German card image."""
        filename = f"de_{card_id.replace('/', '_').replace('!', '')}.png"
        img_path = GERMAN_IMAGES_DIR / filename
        if img_path.exists():
            return img_path

        # Handle special characters in local_id
        safe_local_id = local_id.replace('!', '%21').replace('?', '%3F').replace('&', '%26')
        alt_url = image_url.replace(local_id, safe_local_id)

        urls_to_try = [image_url, alt_url]
        for url in urls_to_try:
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and len(r.content) > 500:
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    img_path.write_bytes(r.content)
                    logger.info("Downloaded %s (%d bytes)", filename, len(r.content))
                    return img_path
            except Exception as e:
                logger.debug("Failed to download %s: %s", url, e)
        return None

    def _fetch_cards_tcgdex(self) -> List[Dict]:
        """Fetch full catalog from TCGdex (free, no auth, single request).

        Card IDs have format {set_code}-{local_id} (e.g., "base4-1", "swsh3-136").
        The image CDN uses this same pattern without a prefix:
        https://assets.tcgdex.net/en/{set_code}/{local_id}/high.webp
        """
        logger.info("Fetching TCGdex catalog...")
        r = requests.get(TCGDEX_CARDS_URL, timeout=60)
        if r.status_code != 200:
            logger.error("TCGdex fetch failed: %s", r.text[:200])
            return []

        # Also fetch set names for display
        self._set_names = {}
        try:
            sets_r = requests.get("https://api.tcgdex.net/v2/en/sets", timeout=15)
            if sets_r.status_code == 200:
                for s in sets_r.json():
                    self._set_names[s.get("id", "")] = s.get("name", "")
                logger.info("Loaded %d set names from TCGdex", len(self._set_names))
        except Exception as e:
            logger.warning("Failed to load TCGdex sets: %s", e)

        raw_cards = r.json()
        valid_cards: List[Dict] = []

        for card in raw_cards:
            card_id = card.get("id", "")
            name = card.get("name", "") or ""
            if not card_id:
                continue

            # Parse card ID: {set_code}-{local_id}
            if "-" not in card_id:
                continue
            last_dash = card_id.rfind("-")
            set_code = card_id[:last_dash]
            local_id_raw = card_id[last_dash + 1:]

            # Decode URL-encoded localId (e.g., %3F -> ?)
            from urllib.parse import unquote
            local_id = unquote(local_id_raw)

            # Build TCGdex image URL using the card's image field (includes category)
            image_base = card.get("image", "")
            image_url = f"{image_base}/high.webp" if image_base else ""

            # Resolve set name
            set_name = self._set_names.get(set_code, set_code)

            # Extract rich metadata
            rarity = card.get("rarity", "")
            types = card.get("types", [])
            hp = card.get("hp", 0)
            attacks = card.get("attacks", [])
            weaknesses = card.get("weaknesses", [])
            retreat = card.get("retreat", 0)
            pricing = card.get("pricing", {})

            valid_cards.append({
                "id": card_id,
                "name": name,
                "set_id": set_code,
                "set_name": set_name,
                "number": local_id,
                "image_url": image_url,
                # Rich metadata
                "rarity": rarity,
                "types": types,
                "hp": hp,
                "attacks": attacks,
                "weaknesses": weaknesses,
                "retreat": retreat,
                "pricing": pricing,
            })

        logger.info("TCGdex returned %d valid cards", len(valid_cards))
        return valid_cards

    def _fetch_cards(self) -> List[Dict]:
        """Fetch card catalog: TCGdex first, fallback to pokemontcgapi."""
        cards = self._fetch_cards_tcgdex()
        if cards:
            return cards
        api_key = os.environ.get("PTCG_API_KEY", "")
        cards: List[Dict] = []
        next_url = "https://api.pokemontcgapi.com/v1/cards?limit=200"
        consecutive_errors = 0
        while next_url:
            r = requests.get(next_url, headers={"X-Api-Key": api_key}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                page_cards = data.get("data", [])
                if not page_cards:
                    break
                cards.extend(page_cards)
                next_url = data.get("links", {}).get("next", "")
                consecutive_errors = 0
                if len(cards) % 1000 == 0:
                    logger.info("Fetched %d cards so far...", len(cards))
            elif r.status_code == 429:
                self._build_progress["api_rate_limited"] = True
                try:
                    err_data = r.json()
                    msg = err_data.get("error", {}).get("message", "")
                    if "resets at midnight UTC" in msg:
                        self._build_progress["next_reset"] = ("tomorrow at 00:00 UTC")
                except:
                    pass
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logger.warning("API rate limited; using fallback card ranges")
                    return self._fetch_cards_fallback()
                time.sleep(5)
                next_url = "https://api.pokemontcgapi.com/v1/cards?limit=200"
            else:
                logger.error("Catalog fetch error %s: %s", r.status_code, r.text[:200])
                break
        return cards

    def _fetch_cards_fallback(self) -> List[Dict]:
        """Generate synthetic card list from SET_RANGES when API is unavailable."""
        cards = []
        import hashlib
        for set_code, (min_num, max_num) in SET_RANGES.items():
            for num in range(min_num, max_num + 1):
                # Generate a deterministic fake ID from set+number
                card_id = hashlib.md5(f"{set_code}{num}".encode()).hexdigest()[:16]
                cards.append({
                    "id": card_id,
                    "set_code": set_code.upper(),
                    "number": str(num),
                    "name": f"{set_code.upper()} #{num}",
                })
        logger.info("Generated %d fallback cards from SET_RANGES", len(cards))
        return cards

    def _download_card_image(self, set_code: str, number: str, card_id: str, image_url: str = "") -> Optional[Path]:
        img_path = self.cache_dir / f"{card_id.replace('/', '_').replace('!', '')}.jpg"
        if img_path.exists():
            return img_path
        # Build URL list: prefer TCGdex URL, then official CDN
        urls = []
        if image_url:
            urls.append(image_url)
        urls.extend([
            f"{CARD_IMAGE_BASE}/{set_code}/{number}.png",
            f"{CARD_IMAGE_BASE}/{set_code}/{number}_hires.png",
        ])
        for url in urls:
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and len(r.content) > 500:
                    Image.open(BytesIO(r.content)).convert("RGB").save(img_path, "JPEG", quality=90)
                    return img_path
            except Exception:
                continue
        return None

    # ---------------------------------------------------------------- embedding

    def _extract_embedding(self, image_bytes: bytes) -> Optional[List[float]]:
        self._load_model()
        import torch
        try:
            image = Image.open(BytesIO(image_bytes)).convert("RGB")
            tensor = self._preprocess(image).unsqueeze(0).to(self.device)
            with torch.no_grad():
                emb = self._model.encode_image(tensor)
            return emb.squeeze().cpu().numpy().tolist()
        except Exception as e:
            logger.debug("Embedding failed: %s", e)
            return None

    def _embed_batch(self, image_bytes_list: List[bytes]) -> List[Optional[List[float]]]:
        """Process multiple images in a single forward pass for speed."""
        if not image_bytes_list:
            return []
        self._load_model()
        import torch
        tensors = []
        for img_bytes in image_bytes_list:
            try:
                image = Image.open(BytesIO(img_bytes)).convert("RGB")
                tensors.append(self._preprocess(image))
            except Exception as e:
                logger.debug("Batch image load failed: %s", e)
                tensors.append(None)
        valid = [t for t in tensors if t is not None]
        if not valid:
            return [None] * len(image_bytes_list)
        try:
            stacked = torch.stack(valid).to(self.device)
            with torch.no_grad():
                embs = self._model.encode_image(stacked)
            results = []
            emb_iter = iter(embs.cpu().numpy())
            for t in tensors:
                if t is None:
                    results.append(None)
                else:
                    results.append(next(emb_iter).tolist())
            return results
        except Exception as e:
            logger.debug("Batch embedding failed: %s", e)
            return [None] * len(image_bytes_list)

    # ------------------------------------------------------------------- match

    def match_card(self, image_bytes: bytes, top_k: int = 5) -> Dict[str, Any]:
        if not self.load_index() or not self.cards:
            return {
                "decision": "no_match",
                "decision_reason": "Card index not built yet. Start a build via /api/index/build.",
                "candidates": [],
                "meta": {"source": "clip_matcher", "cards_indexed": 0},
            }

        emb = self._extract_embedding(image_bytes)
        if emb is None:
            return {"error": "Could not process image", "decision": "no_match", "candidates": []}

        import numpy as np
        query = np.array(emb, dtype=np.float32)
        matrix = np.array([c.embedding for c in self.card_list], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        qnorm = np.linalg.norm(query) + 1e-9
        sims = matrix @ query / (norms * qnorm + 1e-9)
        idx = np.argsort(-sims)[:top_k]

        candidates = []
        for i in idx:
            card = self.card_list[i]
            score = float(sims[i])
            if score >= 0.15:
                candidate = {
                    "id": card.card_id,
                    "name": card.name,
                    "set": card.set_code,
                    "set_name": card.set_name,
                    "number": card.number,
                    "similarity": round(score, 3),
                    "image_url": card.image_url,
                }
                # Add rich metadata if available
                if card.rarity:
                    candidate["rarity"] = card.rarity
                if card.types:
                    candidate["types"] = card.types
                if card.hp:
                    candidate["hp"] = card.hp
                if card.attacks:
                    candidate["attacks"] = card.attacks
                if card.weaknesses:
                    candidate["weaknesses"] = card.weaknesses
                if card.retreat:
                    candidate["retreat"] = card.retreat
                if card.pricing:
                    candidate["pricing"] = card.pricing
                candidates.append(candidate)

        top = candidates[0] if candidates else None
        if top and top["similarity"] > 0.55:
            decision, reason = "match", f"High confidence ({top['similarity']:.2f})"
        elif top:
            decision, reason = "possible_match", f"Possible match ({top['similarity']:.2f})"
        else:
            decision, reason = "no_match", "No similar card in local index"

        return {
            "decision": decision,
            "decision_reason": reason,
            "candidates": candidates,
            "meta": {"source": "clip_matcher", "cards_indexed": len(self.card_list)},
        }


# --------------------------------------------------------------- singleton

_matcher: Optional[CLIPCardMatcher] = None
_matcher_lock = threading.Lock()


def get_clip_matcher() -> CLIPCardMatcher:
    global _matcher
    if _matcher is None:
        with _matcher_lock:
            if _matcher is None:
                _matcher = CLIPCardMatcher()
    return _matcher


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    matcher = CLIPCardMatcher()
    print("Loading or building CLIP card index...")
    matcher.load_or_download_index(max_cards=2000)  # Start with 2000
    print(f"Indexed {len(matcher.cards)} cards")
