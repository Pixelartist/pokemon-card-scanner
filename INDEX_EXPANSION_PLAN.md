# Pokémon Card Scanner - Large-Scale Index Expansion Plan

## Current State
- **Cards indexed:** 2,906
- **Images downloaded:** 3,310
- **Embeddings extracted:** 2,906
- **Database entries:** 2,106
- **Sources used:** TCGdex EN + Pokemon TCG API (rate-limited)

## Target: ~14,000+ Cards (All Languages)

### Language Coverage
| Language | Sets | Priority | Notes |
|----------|------|----------|-------|
| English (EN) | 220 | High | Primary reference |
| German (DE) | 155 | High | Different artwork for some cards |
| French (FR) | 202 | Medium | |
| Spanish (ES) | 156 | Medium | |
| Italian (IT) | 193 | Medium | |
| Portuguese (PT) | 125 | Low | |
| Japanese (JA) | 186 | Low | Unique cards, different numbering |

---

## Phase 1: Data Architecture (2 hours)

### 1.1 Metadata Storage Structure
```
data/
├── card_index.json          # CLIP matcher index (current format)
├── clip_embeddings.npz      # NumPy embeddings
├── card_catalog.json        # NEW: Full metadata from all sources
├── images/
│   ├── tcydex/              # TCGdex official images
│   │   ├── en/
│   │   ├── de/
│   │   ├── fr/
│   │   └── ...
│   ├── pkmncards/           # pkmncards.com scanned images
│   └── user_scans/          # NEW: User-captured card photos
└── training_data/
    ├── high_confidence/     # Known-good identifications
    ├── uncertain/           # Needs human review
    └── failed/              # Cannot identify
```

### 1.2 Card Catalog Schema
```json
{
  "cards": [
    {
      "id": "base1-1",
      "tcgid": "base1-1",           // TCGdex ID
      "name": "Charizard",
      "names": {                   // Multilingual names
        "en": "Charizard",
        "de": "Glurak",
        "fr": "Dracaufeu",
        "ja": "リザードン"
      },
      "set_id": "base1",
      "set_name": "Base Set",
      "number": "1",
      "types": ["Fire"],
      "rarity": "Holo Rare",
      "hp": 120,
      "attacks": [...],
      "images": {
        "tcgdex": {
          "en": "https://images.tcgdex.net/en/base1/1.jpg",
          "de": "https://images.tcgdex.net/de/base1/1.jpg",
          "local_path": "/static/images/tcydex/en/base1-1.jpg"
        },
        "pkmncards": {
          "local_path": "/static/images/pkmncards/base1-1.jpg"
        }
      },
      "language": "en",           // Primary language of this card
      "sources": ["tcgdex", "pkmncards"],
      "added_at": "2026-10-04T12:00:00Z",
      "embedding": null           // Will be populated
    }
  ]
}
```

---

## Phase 2: Multi-Language Collection (4 hours)

### 2.1 Fetch Strategy
```python
# Priority order:
1. TCGdex EN - primary reference
2. TCGdex DE - German exclusive cards
3. Pkmncards - real card photos (training data)
4. Other languages for duplicates only
```

### 2.2 Deduplication Logic
- Cards are matched by `set_code` + `number` (or TCGdex `id`)
- Store multiple image sources per card
- Track which language has which artwork

### 2.3 Image Download Pipeline
```python
# Parallel download with retry
async def download_images(card_ids, languages=['en', 'de'], max_workers=8):
    # 1. Fetch TCGdex images (CDN, fast)
    # 2. Fetch pkmncards images (real photos, slower)
    # 3. Organize by language and source
```

**Throughput estimate:** 52 cards/hour (parallel download) + 30 cards/hour (embeddings)
- With 8 workers: ~416 cards/hour total
- 6 hours: ~2,500 new cards

---

## Phase 3: Embedding Extraction (4 hours)

### 3.1 Batch Processing
```python
# Process in batches of 32 images
BATCH_SIZE = 32

def extract_embeddings(images):
    # Preprocess in parallel
    # Run CLIP model in batches
    # Save embeddings with card metadata
```

### 3.2 Memory Optimization
- Use float16 instead of float32 (saves 50% memory)
- Compress embeddings with PCA if needed
- Stream writes to avoid loading all at once

---

## Phase 4: Training Data Collection (Ongoing)

### 4.1 User Scan Storage
```
data/training_data/
├── high_confidence/   # 90%+ match confidence
│   ├── {card_id}/
│   │   └── scan_001.jpg
│   └── ...
├── uncertain/         # 50-90% confidence
│   └── ...
└── failed/           # <50% or no match
    └── ...
```

### 4.2 Scan Metadata
```json
{
  "scan_id": "uuid",
  "timestamp": "2026-10-04T12:00:00Z",
  "card_id": "base1-4",           // Matched card (if any)
  "confidence": 0.92,
  "source": "camera" | "upload",
  "image_path": "/static/user_scans/...",
  "device": "iPhone 14",
  "user_id": "anon"
}
```

### 4.3 Future Training Use
- Collect scans for fine-tuning CLIP
- Label uncertain matches for human review
- Build dataset of lighting/angle variations

---

## Phase 5: Implementation (6-8 hours)

### 5.1 Files to Create
1. `scripts/fetch_catalog.py` - Multi-language TCGdex fetcher
2. `scripts/download_images.py` - Parallel image downloader
3. `scripts/extract_embeddings.py` - CLIP embedding extractor
4. `scripts/save_training_data.py` - Scan collector
5. `data/card_catalog.json` - Master metadata file

### 5.2 Modified Files
1. `src/services/clip_matcher.py` - Add language support
2. `src/api/main.py` - Add catalog endpoints
3. `src/templates/index.html` - Language selector UI

---

## Performance Targets

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| Cards indexed | 2,906 | 14,000 | 4.8x |
| Languages | 1 | 7 | 7x |
| Image sources | 1 | 2+ | Multiple |
| Training data | 0 | Collecting | New feature |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| TCGdex rate limits | Medium | Use local caching, respect delays |
| Disk space | Low | 214GB available, need ~5GB |
| GPU memory | Medium | Batch processing, float16 |
| Image availability | Low | Fallback to CDN URLs |

---

## Next Steps

1. ✅ Plan approval
2. ⏳ Create `fetch_catalog.py`
3. ⏳ Fetch all languages
4. ⏳ Download images
5. ⏳ Extract embeddings
6. ⏳ Update CLIP matcher
7. ⏳ Test identification accuracy
