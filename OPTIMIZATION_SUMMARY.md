# Pokemon Card Scanner - API Optimization Summary

## Performance Results

| Set | Before | After | Speedup |
|-----|--------|-------|---------|
| PMCG1 (Japanese Base Set) | ~800ms | ~40ms | **19x faster** |
| de_bw4 (German BW4) | ~800ms | ~35ms | **23x faster** |
| MC (Modern Cards) | ~800ms | ~200ms | **4x faster** |
| PMCG-E1 (Evolving Skies JP) | ~800ms | ~600ms | **1.3x faster** |

## Optimizations Applied

### 1. Shared SQLite Connection (`_get_db()`)
- Single connection reused across requests instead of creating/closing per request
- WAL mode enabled for concurrent read performance
- 200MB page cache configured
- 256MB mmap size for file-backed caching

### 2. In-Memory Set ID Cache (`_SET_ID_CACHE`)
- Set code → set_id mapping cached in memory
- Avoids repeated DB lookups for set resolution
- Cached on first access, persists for server lifetime

### 3. One-Time Meta Index (`_META_IMG_INDEX`)
- `cards_meta.json` loaded once at startup into a dict
- Previously: O(N) scan per card to find image (N = 40k+ cards)
- Now: O(1) lookup after single build (~28ms one-time cost)
- `_resolve_placeholder()` uses this index

### 4. Query Optimization
- `json_extract()` instead of `json_object()` for image column
- Direct column selection (only needed fields)
- Application-level sorting instead of ORDER BY in SQL

### 5. Connection Management
- Removed `conn.close()` on shared connection
- Fixed intermittent 500 errors from closed connections

## How to Restart Server

```bash
cd /opt/data/pokemon-card-scanner
export PYTHONPATH=/opt/data/pokemon-card-scanner/src
/opt/data/pokemon-card-scanner/.venv/bin/python -m uvicorn api.main:app \
  --host 0.0.0.0 --port 5005 --workers 1 --log-level info
```

Or use the daemon:
```bash
cd /opt/data/pokemon-card-scanner
/opt/data/pokemon-card-scanner/.venv/bin/python daemon.py
```

## Verification

```bash
# Health check
curl http://localhost:5005/health

# Benchmark set load
curl -w "\n%{time_total}s\n" http://localhost:5005/api/sets/PMCG1/cards

# Full benchmark suite
python3 /tmp/full_bench.py
```

## Database Stats

- **Total cards:** 56,040
- **Total sets:** 449
- **Image coverage:** 98.7% (55,325 / 56,040)
- **Image sources:**
  - artofpkm.com: 4,448 images (JP vintage)
  - TCGdex: 5,843 images (EN vintage)
  - Pocket repo: 4,317 images (TCG Pocket)
  - German PNGs: 17,386 images (128 de_* sets)
  - CLIP: 12,186 embeddings
  - Placeholder fallback for ~715 promo cards

## Files Modified

- `src/api/main.py`: Added `_get_db()`, `_SET_ID_CACHE`, `_META_IMG_INDEX`, `_resolve_placeholder()`
- `src/api/models/database.py`: Added WAL pragmas, 200MB cache, mmap
- `scripts/bench_optimized.py`: Benchmark script for testing

## Remaining Work

- ~715 promo cards (SMP, SWHP, SVP, MEP, BWP) still need images
- Consider adding pagination for large sets (MC has 774 cards)
- Consider response compression (gzip) for JSON payloads
