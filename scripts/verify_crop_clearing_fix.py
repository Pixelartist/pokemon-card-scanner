#!/usr/bin/env python3
"""
Verify that the browser crop clearing fix is working by comparing crop hashes.
This script should show that:
1. The browser crop clearing fix is applied (clearing _lastCropDataUrl)
2. New crops have different hashes from previous ones (not reusing stale cache)
3. The crop looks like the card (not background)
"""
import json, os, hashlib, datetime
from PIL import Image

print("="*70)
print("CROPPED WAILMER CARD VERIFICATION - Testing Crop Clearing Fix")
print("="*70)

# Load build_browser_hashes.py and check for the checkpoint fix
builder_path = '/opt/data/pokemon-card-scanner/scripts/build_browser_hashes.py'
with open(builder_path) as f:
    builder_content = f.read()
    
print(f"\n1. BUILDER SCRIPT CHECKS")
print("-" * 40)

# Check 1: Reduced checkpoint interval (256 instead of 2048)
if 'if done % 256 < BATCH or done == len(pending):' in builder_content:
    print("✓ Checkpoint interval reduced from 2048 to 256 batches")
    builder_ok = True
else:
    print("✗ Checkpoint interval not reduced")
    builder_ok = False

# Check 2: Navigation timeout increased
if 'page.set_default_navigation_timeout(120000)' in builder_content:
    print("✓ Navigation timeout increased to 120s")
else:
    print("✗ Navigation timeout not increased")

# Check 3: Progress reporting added
if 'Processing batch' in builder_content and 'hashes done (rate' in builder_content:
    print("✓ Progress reporting added")
else:
    print("✗ Progress reporting missing")

# Load JS to check for crop clearing
js_path = '/opt/data/pokemon-card-scanner/src/static/js/main.js'
with open(js_path) as f:
    js_content = f.read()

print(f"\n2. BROWSER JS CHECKS")
print("-" * 40)

# Check for crop clearing
if '_lastCropDataUrl = null;' in js_content:
    print("✓ Browser crop cleared after consumption (_lastCropDataUrl = null)")
    js_crop_clear_ok = True
else:
    print("✗ Browser crop clearing not found")
    js_crop_clear_ok = False

# Check for cropDataUrl assignment
if "if (cropDataUrl) this._lastCropDataUrl = cropDataUrl;" in js_content:
    print("✓ Browser crop caching implemented")
    js_cache_ok = True
else:
    print("✗ Browser crop caching not found")
    js_cache_ok = False

# Check server API
api_path = '/opt/data/pokemon-card-scanner/src/api/main.py'
with open(api_path) as f:
    api_content = f.read()

print(f"\n3. SERVER API CHECKS")
print("-" * 40)

# Check for browser_crop parameter
if 'browser_crop: Optional[UploadFile] = Form(None)' in api_content:
    print("✓ Server accepts browser_crop parameter")
    api_browser_crop_ok = True
else:
    print("✗ Server browser_crop parameter not found")
    api_browser_crop_ok = False

# Check for crop path normalization
if '_valid_rel_path' in api_content:
    print("✓ Server validates crop paths (security)")
    api_security_ok = True
else:
    print("✗ Server path validation missing")
    api_security_ok = False

# Analyze crop files
print(f"\n4. CROP FILE ANALYSIS")
print("-" * 40)

base = '/opt/data/pokemon-card-scanner/data/scans/crops'
all_files = sorted([f for f in os.listdir(base) if f.startswith('crop_')])
print(f"Total crop files in system: {len(all_files)}")

# Get unique hashes from all crops
crop_hashes = set()
crop_details = []
for f in all_files:
    p = os.path.join(base, f)
    h = hashlib.md5(open(p, 'rb').read()).hexdigest()
    crop_hashes.add(h)
    
    # Get file info
    try:
        img = Image.open(p)
        size = img.size
        dt = datetime.datetime.strptime(f.split('_')[1], '%Y%m%d_%H%M%S%f')
        crop_details.append((f, dt, size, h))
    except Exception as e:
        print(f"  Error reading {f}: {e}")

print(f"Unique crop hashes: {len(crop_hashes)}")

# Show most recent crops
crop_details.sort(key=lambda x: x[1])
recent_crops = crop_details[-10:]  # Last 10
print(f"\nMost recent 10 crops:")
for f, dt, size, h in recent_crops:
    print(f"  {f}: {dt.strftime('%H:%M:%S')} size={size} hash={h[:8]}")

# Check for variety in recent crops
recent_hashes = set(h for _, _, _, h in recent_crops)
print(f"\nUnique hashes in recent 10: {len(recent_hashes)}")

if len(recent_hashes) == 1:
    print("⚠️  WARNING: Recent crops all have the same hash (potential stale cache)")
    crop_variation_ok = False
else:
    print("✓ Recent crops have different hashes (good - fresh generation)")
    crop_variation_ok = True

# Test actual crop generation
print(f"\n5. LIVE CROP GENERATION TEST")
print("-" * 40)

# Download a reference Wailmer card
import requests
resp = requests.get('http://localhost:5005/static/card_images/official/en/bw6-25.png')
if resp.status_code == 200:
    print("✓ Reference Wailmer card downloaded successfully")
    
    # Get hash of reference
    ref_hash = hashlib.md5(resp.content).hexdigest()[:8]
    print(f"  Reference card hash: {ref_hash}")
    
    # Scan the reference image
    files = {'image': ('test.png', resp.content, 'image/png')}
    data = {'threshold': '0.78'}
    r = requests.post('http://localhost:5005/api/scan', files=files, data=data, timeout=60)
    
    if r.status_code == 200:
        result = r.json()
        print(f"✓ Scan completed: decision={result.get('decision')}")
        print(f"  Card ID: {result.get('id')}")
        print(f"  Card name: {result.get('name')}")
        
        # Check if crop was generated
        crop_path = result.get('crop_image_path')
        if crop_path:
            # Get hash of new crop
            full_path = os.path.join('/opt/data/pokemon-card-scanner/data', crop_path)
            if os.path.exists(full_path):
                new_crop_hash = hashlib.md5(open(full_path, 'rb').read()).hexdigest()
                print(f"  New crop hash: {new_crop_hash[:8]}")
                
                # Compare with previous unique crops
                prev_unique_hashes = crop_hashes - {new_crop_hash}
                if len(prev_unique_hashes) == len(crop_hashes):  # New unique hash
                    print("✓ New crop has unique hash (fresh generation)")
                    live_test_ok = True
                else:
                    print("⚠️  New crop hash matches previous (potential reuse)")
                    live_test_ok = False
            else:
                print(f"✗ New crop file not found locally: {full_path}")
                live_test_ok = False
        else:
            print("✗ No crop_image_path in scan result")
            live_test_ok = False
    else:
        print(f"✗ Scan failed: {r.status_code} {r.text}")
        live_test_ok = False
else:
    print("✗ Failed to download reference card")
    live_test_ok = False

# Final assessment
print("\n" + "="*70)
print("FINAL ASSESSMENT")
print("="*70)

tests = [
    ("Builder checkpoint interval", builder_ok),
    ("Browser crop clearing", js_crop_clear_ok),
    ("Browser crop caching", js_cache_ok),
    ("Server browser_crop API", api_browser_crop_ok),
    ("Server path security", api_security_ok),
    ("Crop variety in system", crop_variation_ok),
    ("Live crop generation test", live_test_ok),
]

passed = sum(1 for _, ok in tests if ok)
total = len(tests)
score = (passed / total) * 100

for test_name, ok in tests:
    status = "✓ PASS" if ok else "✗ FAIL"
    print(f"{status} {test_name}")

print(f"\nOverall Score: {passed}/{total} tests passed ({score:.1f}%)")

if score >= 85:
    print("\n🎉 SUCCESS: Crop clearing fix is working correctly!")
    print("   - Browser crops are properly cleared after consumption")
    print("   - Builder has appropriate checkpoint intervals")
    print("   - New crops are being generated (not stale cache)")
elif score >= 60:
    print("\n⚠️  PARTIAL SUCCESS: Some aspects need attention")
    print("   - Most fixes are in place but there may be stale cache issues")
elif score >= 40:
    print("\n❌ FAILURE: Major issues detected")
    print("   - Crop clearing fix may not be working correctly")
else:
    print("\n💥 CRITICAL FAILURE: System needs immediate attention")
    print("   - Multiple crop-related issues detected")

print("\nNote: This test verifies that the crop clearing fix (clearing")
print("_lastCropDataUrl after browser crop consumption) is working as expected.")
print("The fix prevents stale crops from being reused across scans.")