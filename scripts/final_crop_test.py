#!/usr/bin/env python3
"""Simple verification that the Wailmer crop fix works correctly."""
import requests
from PIL import Image
from io import BytesIO
import os

# Fetch the reference Wailmer (bw6-25)
resp = requests.get('http://localhost:5005/static/card_images/official/en/bw6-25.png')
if resp.status_code != 200:
    raise RuntimeError('Failed to fetch bw6-25.png')
ref_img = Image.open(BytesIO(resp.content)).convert('RGB')
print(f'Reference card size: {ref_img.size}')

# Get current crop files for comparison
from hashlib import md5
base = '/opt/data/pokemon-card-scanner/data/scans/crops'
prev_files = sorted([p for p in os.listdir(base) if p.startswith('crop_20261022_09') or p.startswith('crop_20261022_10')])
print(f'\nPrevious crop files: {len(prev_files)}')

# Scan the reference image via API
files = {'image': ('test.png', resp.content, 'image/png')}
data = {'threshold': '0.78'}
r = requests.post('http://localhost:5005/api/scan', files=files, data=data, timeout=60)
print(f'\nScan result status: {r.status_code}')
if r.status_code == 200:
    result = r.json()
    print(f"Decision: {result.get('decision')}")
    print(f"Card ID: {result.get('id')}")
    print(f"Card name: {result.get('name')}")
    print(f"Card object: {result.get('card')}")
    
    crop_path = result.get('crop_image_path')
    if crop_path:
        # Check if crop path exists locally
        full_path = os.path.join('/opt/data/pokemon-card-scanner/data', crop_path)
        if os.path.exists(full_path):
            print(f"\nLocal crop exists: {crop_path}")
            
            # Read crop from disk
            crop_img = Image.open(full_path).convert('RGB')
            print(f"Crop size: {crop_img.size}")
            
            # Compare with reference using simple metrics
            # Resize to same dimensions for fair comparison
            crop_small = crop_img.resize(ref_img.size)
            
            # Convert to grayscale and compute average brightness
            import numpy as np
            crop_arr = np.array(crop_small).astype(float)
            
            # Mean brightness (0-255)
            mean_brightness = np.mean(crop_arr)
            print(f"Average crop brightness: {mean_brightness:.1f}")
            
            # Compute difference between crop and reference
            ref_arr = np.array(ref_img).astype(float)
            diff = np.mean(np.abs(crop_arr - ref_arr))
            print(f"Mean pixel difference: {diff:.1f}")
            
            # Visual assessment
            if diff < 50:
                print("\n✓ Crop appears to be the correct Wailmer card (matches reference)")
            elif diff < 150:
                print("\n? Crop is partially similar but may not be the exact card")
            else:
                print("\n✗ Crop does not match the reference card (likely background or other image)")
            
            # Check if crop has card-like properties (not mostly uniform colors)
            # Compute standard deviation of brightness
            brightness_std = np.std(crop_arr)
            print(f"Crop brightness std dev: {brightness_std:.1f}")
            if brightness_std > 50:
                print("Crop has variation (consistent with a card)")
            else:
                print("Crop is too uniform (may be background)")
                
        else:
            print(f"\nERROR: Crop file not found locally: {full_path}")
            
        # Try to fetch crop from API directly as well
        try:
            # API endpoint for crops (if exists)
            if '/api/collection/crop/' in crop_path:
                crop_id = crop_path.split('/')[-1]
                crop_resp = requests.get(f'http://localhost:5005/api/collection/crop/{crop_id}')
                if crop_resp.status_code == 200:
                    print("Crop retrieved via API endpoint")
        except Exception as e:
            print(f"Could not fetch crop via API: {e}")
    else:
        print("\nNo crop_image_path in scan result")
        print("Available keys in result:", list(result.keys()))

# Also test that the previous crops are all identical (indicating stale cache)
print("\n" + "="*50)
print("PREVIOUS CROPS ANALYSIS")
print("="*50)
if prev_files:
    # Read first few files to check hashes
    hashes = {}
    for f in prev_files[:5]:  # Check first 5
        p = os.path.join(base, f)
        h = md5(open(p, 'rb').read()).hexdigest()[:8]
        hashes[h] = hashes.get(h, 0) + 1
        print(f"  {f}: {h}")
    
    unique_hashes = len(hashes)
    print(f"\nUnique hash patterns: {unique_hashes} out of {len(prev_files)} files")
    if unique_hashes == 1:
        print("All previous crops have the same hash (they are stale/reused)")
    else:
        print("Previous crops have different hashes (good for variety)")

print("\n" + "="*50)
print("SUMMARY")
print("="*50)
print("If the latest scan produced a new crop (different hash from previous),")
print("and the crop matches the Wailmer reference (low pixel difference),")
print("then the crop fix is working correctly.")