#!/usr/bin/env python3
"""
Extract CLIP embeddings for Pokémon card images.
Processes downloaded images and saves embeddings to disk.
"""

import asyncio
import json
import logging
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import torch
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = Path("data")
CARD_CATALOG_PATH = DATA_DIR / "card_catalog.json"
IMAGE_DIR = DATA_DIR / "images"
EMBEDDINGS_PATH = DATA_DIR / "clip_embeddings.npz"
EMBEDDINGS_JSON_PATH = DATA_DIR / "clip_embeddings.json"
EXTRACTION_LOG_PATH = DATA_DIR / "extraction_log.json"
CHECKPOINT_PATH = DATA_DIR / "clip_embeddings_checkpoint.npz"

# Model configuration
MODEL_NAME = "openai/clip-vit-base-patch32"
BATCH_SIZE = 32
TARGET_IMAGE_SIZE = (224, 224)  # CLIP standard input size
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# CLIP preprocessing
import torchvision.transforms as transforms
preprocess = transforms.Compose([
    transforms.Resize((TARGET_IMAGE_SIZE[0], TARGET_IMAGE_SIZE[1]), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.ToTensor(),
    transforms.Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711))
])

class EmbeddingExtractor:
    def __init__(self):
        self.embeddings = {}
        self.extraction_stats = {
            "total_attempted": 0,
            "total_extracted": 0,
            "total_failed": 0,
            "average_confidence": 0.0,
            "images_processed": []
        }
        self.extraction_log = []
        
        # Check CUDA availability
        if DEVICE.type == "cuda":
            logger.info(f"Using CUDA for embedding extraction")
            torch.backends.cudnn.benchmark = True
        else:
            logger.info(f"Using CPU for embedding extraction (consider using GPU for better performance)")
            
    async def initialize_model(self):
        """Initialize CLIP model."""
        logger.info(f"Loading CLIP model: {MODEL_NAME}")
        
        try:
            # Try to load from Hugging Face
            from transformers import CLIPProcessor, CLIPModel
            
            self.processor = CLIPProcessor.from_pretrained(MODEL_NAME)
            self.model = CLIPModel.from_pretrained(MODEL_NAME)
            self.model.to(DEVICE)
            self.model.eval()
            
            logger.info("CLIP model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load CLIP model: {e}")
            # Check if model exists locally
            local_model_dir = Path.home() / ".cache" / "huggingface" / "models" / MODEL_NAME.replace("/", "_")
            if local_model_dir.exists():
                logger.info(f"Found local model at {local_model_dir}")
                from transformers import CLIPProcessor, CLIPModel
                self.processor = CLIPProcessor.from_pretrained(local_model_dir)
                self.model = CLIPModel.from_pretrained(local_model_dir)
                self.model.to(DEVICE)
                self.model.eval()
            else:
                raise
    
    def extract_all_embeddings(self, card_catalog: Dict) -> Dict:
        """Extract embeddings for all cards in catalog."""
        cards = card_catalog.get("cards", [])
        logger.info(f"Starting embedding extraction for {len(cards)} cards")

        # Check for existing checkpoint
        loaded_count = 0
        if CHECKPOINT_PATH.exists() or EMBEDDINGS_PATH.exists():
            checkpoint = self._load_checkpoint()
            loaded_count = len(checkpoint)
            if loaded_count > 0:
                self.embeddings.update(checkpoint)
                logger.info(f"Loaded {loaded_count} embeddings from checkpoint")

        # Organize images for batch processing
        image_batches = self._organize_images_for_batching(cards)

        logger.info(f"Organized into {len(image_batches)} batches")

        # Process batches with periodic checkpoints
        checkpoint_interval = 50  # Save checkpoint every 50 batches
        for batch_idx, batch in enumerate(image_batches):
            logger.info(f"Processing batch {batch_idx + 1}/{len(image_batches)}")
            self._process_image_batch(batch, batch_idx)

            # Save checkpoint periodically
            if (batch_idx + 1) % checkpoint_interval == 0:
                self._save_checkpoint()
                logger.info(f"Checkpoint saved after batch {batch_idx + 1}")

            # Log progress every 100 batches
            if (batch_idx + 1) % 100 == 0:
                logger.info(f"Progress: {batch_idx + 1}/{len(image_batches)} batches, "
                           f"{len(self.embeddings)} embeddings so far")

        # Final save
        self._save_embeddings()

        return self.extraction_stats
    
    def _organize_images_for_batching(self, cards: List[Dict]) -> List[List[Dict]]:
        """Organize cards into batches for efficient processing."""
        batches = []
        current_batch = []
        
        for card in cards:
            # Check if we have image for this card
            image_path = self._get_best_image_for_card(card)
            if image_path and Path(image_path).exists():
                card_info = {
                    "card_id": card.get("id", ""),
                    "image_path": image_path,
                    "card_data": card,
                    "language": card.get("language", "en")
                }
                current_batch.append(card_info)
                
                if len(current_batch) >= BATCH_SIZE:
                    batches.append(current_batch)
                    current_batch = []
        
        # Add remaining cards
        if current_batch:
            batches.append(current_batch)
            
        logger.info(f"Created {len(batches)} batches with total {sum(len(b) for b in batches)} images")
        return batches
    
    def _get_best_image_for_card(self, card: Dict) -> Optional[str]:
        """Get the best available image for a card (Matches download_images_v2.py convention)."""
        set_code = card.get("set_code", "")
        number = card.get("number", "")
        language = card.get("language", "en")
        
        if not set_code or not number:
            return None
            
        # Handle special characters in numbers to match download_images_v2.py
        import re
        num_match = re.search(r'(\d+)', number)
        if num_match:
            clean_number = num_match.group(1)
        else:
            return None
            
        # 1. Official images (.png)
        official_path = IMAGE_DIR / "official" / language / f"{set_code}-{clean_number}.png"
        if official_path.exists():
            return str(official_path)
            
        # Fallback to English official if not found
        if language != "en":
            en_official = IMAGE_DIR / "official" / "en" / f"{set_code}-{clean_number}.png"
            if en_official.exists():
                return str(en_official)
                
        # 2. Pkmncards fallback (.png or .jpg)
        pkmn_png = IMAGE_DIR / "pkmncards" / f"{set_code}-{clean_number}.png"
        if pkmn_png.exists():
            return str(pkmn_png)
            
        pkmn_jpg = IMAGE_DIR / "pkmncards" / f"{set_code}-{clean_number}.jpg"
        if pkmn_jpg.exists():
            return str(pkmn_jpg)
        
        # 3. TCGdex fallback (new source)
        tcgdex_path = IMAGE_DIR / "tcgdex" / language / f"{set_code}-{clean_number}.png"
        if tcgdex_path.exists():
            return str(tcgdex_path)
        
        # Also check TCGdex with different naming pattern
        tcgdex_alt = IMAGE_DIR / "tcgdex" / language / f"{set_code}-{number}.png"
        if tcgdex_alt.exists():
            return str(tcgdex_alt)
            
        return None
    
    def _process_image_batch(self, batch: List[Dict], batch_idx: int):
        """Process a batch of images and extract embeddings."""
        logger.info(f"Processing batch {batch_idx + 1} with {len(batch)} images")
        
        # Count attempted
        self.extraction_stats["total_attempted"] += len(batch)
        
        # Prepare batch data
        image_paths = [item["image_path"] for item in batch]
        card_ids = [item["card_id"] for item in batch]
        
        # Extract embeddings
        try:
            embeddings = self._extract_batch_embeddings(image_paths)
            
            # Store embeddings
            for i, (card_id, embedding) in enumerate(zip(card_ids, embeddings)):
                if embedding is not None:
                    self.embeddings[card_id] = {
                        "embedding": embedding,
                        "card_id": card_id,
                        "image_path": image_paths[i],
                        "language": batch[i].get("language", "en"),
                        "confidence": 1.0,  # CLIP confidence (simplified)
                        "extracted_at": datetime.now().isoformat()
                    }
                    self.extraction_stats["total_extracted"] += 1
                    
                    # Log extraction
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "card_id": card_id,
                        "image_path": image_paths[i],
                        "language": batch[i].get("language", "en"),
                        "status": "success",
                        "embedding_dimension": len(embedding) if embedding is not None else 0
                    }
                    self.extraction_log.append(log_entry)
                else:
                    self.extraction_stats["total_failed"] += 1
                    
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "card_id": card_id,
                        "image_path": image_paths[i],
                        "language": batch[i].get("language", "en"),
                        "status": "failed",
                        "error": "Embedding extraction failed"
                    }
                    self.extraction_log.append(log_entry)
                    
        except Exception as e:
            logger.error(f"Failed to process batch {batch_idx + 1}: {e}")
            self.extraction_stats["total_failed"] += len(batch)
    
    def _extract_batch_embeddings(self, image_paths: List[str]) -> List[Optional[np.ndarray]]:
        """Extract embeddings for a batch of images."""
        embeddings = []
        
        # Process images in smaller micro-batches for memory efficiency
        micro_batch_size = 8
        for i in range(0, len(image_paths), micro_batch_size):
            micro_batch_paths = image_paths[i:i + micro_batch_size]
            
            # Load and preprocess images
            images = []
            valid_indices = []
            
            for j, path in enumerate(micro_batch_paths):
                try:
                    image = Image.open(path).convert("RGB")
                    processed_image = preprocess(image)
                    images.append(processed_image)
                    valid_indices.append(j)
                except Exception as e:
                    logger.warning(f"Failed to load image {path}: {e}")
                    
            if not images:
                # No valid images in this micro-batch
                for _ in micro_batch_paths:
                    embeddings.append(None)
                continue
                
            # Stack images and move to device
            try:
                batch_tensor = torch.stack(images).to(DEVICE)
                
                # Extract embeddings
                with torch.no_grad():
                    outputs = self.model.get_image_features(batch_tensor)
                    # Handle different return types from CLIP model
                    image_features = None
                    if isinstance(outputs, torch.Tensor):
                        image_features = outputs
                    elif hasattr(outputs, 'pooler_output'):
                        image_features = outputs.pooler_output
                    elif isinstance(outputs, tuple) and len(outputs) > 0:
                        image_features = outputs[0]
                    elif hasattr(outputs, 'last_hidden_state'):
                        image_features = outputs.last_hidden_state
                    else:
                        # Last resort: try to get tensor from output
                        image_features = outputs

                    if image_features is None:
                        logger.error(f"Could not extract features from model output: {type(outputs)}")
                        for _ in micro_batch_paths:
                            embeddings.append(None)
                        continue

                    # Normalize embeddings
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                    
                    # Convert to numpy
                    batch_embeddings = image_features.cpu().numpy()
                    
                    # Store each embedding
                    for idx in valid_indices:
                        embeddings.append(batch_embeddings[idx])
                        
                    # Fill None for invalid images
                    for idx in range(len(micro_batch_paths)):
                        if idx not in valid_indices:
                            embeddings.append(None)
                            
            except Exception as e:
                logger.error(f"Failed to extract batch embeddings: {e}")
                # Fill with None for all images in this batch
                for _ in micro_batch_paths:
                    embeddings.append(None)
        
        return embeddings
    
    def _save_embeddings(self):
        """Save embeddings to disk."""
        logger.info(f"Saving {len(self.embeddings)} embeddings to {EMBEDDINGS_PATH}")
        
        if self.embeddings:
            # Convert to dict format for JSON serialization
            embeddings_dict = {
                "embeddings": {},
                "metadata": {
                    "total_cards": len(self.embeddings),
                    "embedding_dimension": 512,  # CLIP base dimension
                    "saved_at": datetime.now().isoformat(),
                    "device_used": str(DEVICE)
                }
            }
            
            for card_id, data in self.embeddings.items():
                embeddings_dict["embeddings"][card_id] = {
                    "embedding": data["embedding"].tolist(),
                    "language": data.get("language", "en"),
                    "image_path": data.get("image_path", ""),
                    "confidence": data.get("confidence", 1.0),
                    "extracted_at": data.get("extracted_at", "")
                }
            
            # Save as JSON (for easy access)
            json_path = DATA_DIR / "clip_embeddings.json"
            with open(json_path, 'w') as f:
                json.dump(embeddings_dict, f, indent=2)
            
            # Also save as NPZ (for performance)
            npz_data = {
                "embeddings": np.array([data["embedding"] for data in self.embeddings.values()]),
                "card_ids": list(self.embeddings.keys()),
                "metadata": embeddings_dict["metadata"]
            }
            
            np.savez_compressed(EMBEDDINGS_PATH, **npz_data)
            
            logger.info(f"Embeddings saved to {json_path} (JSON) and {EMBEDDINGS_PATH} (NPZ)")
        else:
            logger.warning("No embeddings to save")
        
        # Save extraction log
        with open(EXTRACTION_LOG_PATH, 'w') as f:
            json.dump(self.extraction_log, f, indent=2)
        
        logger.info(f"Extraction log saved to {EXTRACTION_LOG_PATH}")
        
        # Create summary
        self._create_extraction_summary()
    
    def _create_extraction_summary(self):
        """Create extraction summary file."""
        summary_path = DATA_DIR / "extraction_summary.txt"

        with open(summary_path, 'w') as f:
            f.write(f"CLIP Embedding Extraction Summary\n")
            f.write(f"Date: {datetime.now().isoformat()}\n\n")

            f.write(f"Total cards processed: {self.extraction_stats['total_attempted']}\n")
            f.write(f"Total embeddings extracted: {self.extraction_stats['total_extracted']}\n")
            f.write(f"Total failed: {self.extraction_stats['total_failed']}\n")

            if self.extraction_stats["total_extracted"] > 0:
                avg_confidence = self.extraction_stats["average_confidence"] / self.extraction_stats["total_extracted"]
                f.write(f"Average confidence: {avg_confidence:.2f}\n")

            f.write(f"\nImages by language:\n")
            for image_info in self.extraction_log:
                lang = image_info.get("language", "unknown")
                f.write(f"  {lang}: 1\n")

            f.write(f"\nEmbedding dimension: 512\n")
            f.write(f"Device used: {DEVICE}\n")

        logger.info(f"Extraction summary saved to {summary_path}")

    def _load_checkpoint(self) -> Dict:
        """Load checkpoint from disk."""
        checkpoint_path = CHECKPOINT_PATH if CHECKPOINT_PATH.exists() else EMBEDDINGS_PATH
        if not checkpoint_path.exists():
            return {}

        try:
            data = np.load(checkpoint_path, allow_pickle=True)
            embeddings = data['embeddings']
            card_ids = data['card_ids']
            result = {}
            for i, card_id in enumerate(card_ids):
                result[card_id] = embeddings[i]
            logger.info(f"Loaded checkpoint with {len(result)} embeddings from {checkpoint_path}")
            return result
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return {}

    def _save_checkpoint(self):
        """Save current embeddings as checkpoint."""
        if not self.embeddings:
            return

        logger.info(f"Saving checkpoint with {len(self.embeddings)} embeddings...")
        np.savez_compressed(
            CHECKPOINT_PATH,
            embeddings=np.array(list(self.embeddings.values())),
            card_ids=list(self.embeddings.keys()),
        )
        logger.info("Checkpoint saved")

def main():
    """Main execution function."""
    print("=== Pokémon Card CLIP Embedding Extractor ===")
    
    try:
        # Load card catalog
        if not CARD_CATALOG_PATH.exists():
            print(f"Error: Card catalog not found at {CARD_CATALOG_PATH}")
            print("Run fetch_catalog.py first to create the catalog")
            return
        
        with open(CARD_CATALOG_PATH) as f:
            card_catalog = json.load(f)
        
        print(f"Loaded {len(card_catalog.get('cards', []))} cards from catalog")
        
        # Initialize extractor
        extractor = EmbeddingExtractor()
        
        # Initialize model (async)
        import asyncio
        asyncio.run(extractor.initialize_model())
        
        # Extract embeddings
        stats = extractor.extract_all_embeddings(card_catalog)
        
        print("\n=== Extraction Complete ===")
        print(f"Total cards processed: {stats['total_attempted']}")
        print(f"Total embeddings extracted: {stats['total_extracted']}")
        print(f"Total failed: {stats['total_failed']}")
        
        print(f"\nEmbeddings saved to: {EMBEDDINGS_PATH}")
        print(f"  - JSON: {DATA_DIR / 'clip_embeddings.json'}")
        print(f"  - NPZ (compressed): {EMBEDDINGS_PATH}")
        print(f"Extraction log: {EXTRACTION_LOG_PATH}")
        print(f"Summary: {DATA_DIR / 'extraction_summary.txt'}")
        
        # Print language distribution
        languages = set()
        for card in card_catalog["cards"]:
            languages.add(card.get("language", "en"))
        
        print(f"\nLanguages processed: {', '.join(sorted(languages))}")
        
    except Exception as e:
        logger.error(f"Embedding extraction failed: {e}")
        raise

if __name__ == "__main__":
    main()
