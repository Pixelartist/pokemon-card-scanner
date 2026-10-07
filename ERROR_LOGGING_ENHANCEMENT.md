# Pokemon Card Scanner - Error Logging Enhancement

## Purpose
Enhanced error logging for scanning operations to help diagnose "Error scanning card: Unexpected token '<'" and other issues.

## Changes Made

### 1. Enhanced identify_card() Method
**File:** `src/services/ptcg_client.py` - lines 50-108

**Added comprehensive error logging:**
- Response content-type detection
- HTML error page logging with content snippets
- JSON decode error handling
- Detailed exception information
- Offline matcher fallback logging

**Key improvements:**
```python
# Check if response is HTML (error page) or JSON
content_type = resp.headers.get('content-type', '')
is_json = 'application/json' in content_type
is_html = 'text/html' in content_type

if is_html:
    logger.warning(f"API returned HTML error page (status {resp.status_code}): {resp.text[:200]}")
    return self._identify_offline(image_bytes, top_k)
```

### 2. Enhanced logging in main.py scan endpoint
**File:** `src/api/main.py` - lines 167-169

**Added detailed error logging:**
```python
except Exception as e:
    logger.error(f"Scan error: {e}")
    raise HTTPException(status_code=500, detail=str(e))
```

### 3. Enhanced logging in card_detector.py
**File:** `src/services/card_detector.py` - lines 124-132

**Added comprehensive detection failure logging:**
```python
except Exception as e:
    logger.error(f"Card detection error: {e}")
    import traceback
    traceback.print_exc()
```

## Expected Behavior When Issues Occur

### Scenario 1: API Returns HTML Error Page
```
2026-10-03 14:39:xx  WARNING  ptcg_client.py: identify_card()
    "API returned HTML error page (status 403): <!DOCTYPE html><html><head><title>Access Denied</title></head><body>..."

2026-10-03 14:39:xx  INFO     ptcg_client.py: identify_card()
    "Falling back to offline matcher."

2026-10-03 14:39:xx  INFO     ptcg_client.py: _identify_offline()
    "Offline matcher: CLIP detector - using pre-loaded index"
```

### Scenario 2: JSON Decode Error
```
2026-10-03 14:39:xx  WARNING  ptcg_client.py: identify_card()
    "API returned non-JSON response, falling back to offline matcher"

2026-10-03 14:39:xx  INFO     card_detector.py: detect_card()
    "Using numpy fallback detection"
```

### Scenario 3: Card Detection Failure
```
2026-10-03 14:39:xx  ERROR    card_detector.py: detect_card()
    "Card detection error: Failed to load YOLOv8 model: FileNotFoundError: No module named 'torch'"

2026-10-03 14:39:xx  INFO     card_detector.py: detect_card()
    "No card detected by any method"
```

## Troubleshooting Commands

### Check recent scan errors
```bash
curl -s "http://localhost:5005/api/scan" -X POST -H "Content-Type: multipart/form-data" -F "image=@/path/to/image.jpg"
```

### View detailed server logs
```bash
tail -f /opt/data/pokemon-card-scanner/server.log | grep -E "ERROR|WARNING" | grep -i "scan|identify"
```

### Check for common issues
```bash
grep -E "HTML error page|non-JSON response|Card detection error" /opt/data/pokemon-card-scanner/server.log
```

## Error Diagnosis Guide

### Error: "Error scanning card: Unexpected token '<'"
**Cause:** Pokemon TCG API returned HTML error page
**Fix:** Check logs for "HTML error page" message → System should automatically fallback to offline matcher

### Error: "Failed to load YOLOv8 model"
**Cause:** Missing torch/ultralytics dependencies
**Fix:** Install YOLOv8 dependencies or ensure model file exists

### Error: "Offline matcher unavailable"
**Cause:** No card index built yet
**Fix:** Run index build via `/api/index/build`

## Testing Scenarios

### Test 1: Simulate API error
```bash
curl -s -X POST http://localhost:5005/api/scan -H "Content-Type: application/json" -d '{"error": "API error", "test": true}'
```

### Test 2: Check error logging
```bash
# Make a scan request and then check logs
# Then run:
grep "ERROR" /opt/data/pokemon-card-scanner/server.log | tail -10
```

## Files Modified

1. `src/services/ptcg_client.py` - Enhanced error logging and HTML detection
2. `src/api/main.py` - Enhanced scan endpoint error logging
3. `src/services/card_detector.py` - Enhanced detection error logging

## Verification

After implementing these changes:

✅ Server runs stable on port 5005
✅ API errors are logged with detailed information
✅ HTML error pages are detected and trigger offline fallback
✅ JSON decode errors are properly handled
✅ Card detection errors are logged for debugging
✅ Users receive graceful fallbacks even when services fail

The Pokemon Card Scanner now provides comprehensive error logging and robust fallback mechanisms for production use.

---

Generated: 2026-10-03
Status: ✅ Enhanced Error Logging Complete
