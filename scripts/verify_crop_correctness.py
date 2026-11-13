#!/usr/bin/env python3
"""Test that the cropped Wailmer image is the card (not background)."""
import requests
from PIL import Image
from io import BytesIO

# Download the reference Wailmer (bw6-25)
resp = requests.get('http://localhost:5005/static/card_images/official/en/bw6-25.png')
if resp.status_code != 200:
    raise RuntimeError('Failed to fetch reference Wailmer')

ref_img = Image.open(BytesIO(resp.content)).convert('RGB')
print(f'Reference card: size {ref_img.size}, format {ref_img.format}')

# Scan the reference image via the API
files = {'image': ('ref.png', resp.content, 'image/png')}
data = {'threshold': '0.78'}
r = requests.post('http://localhost:5005/api/scan', files=files, data=data, timeout=60)
if r.status_code != 200:
    raise RuntimeError(f'Scan failed: {r.status_code} {r.text}')

result = r.json()
print(f"Scan result: id={result.get('id')} name={result.get('name')} decision={result.get('decision')}")

crop_path = result.get('crop_image_path')
if crop_path:
    # Fetch the crop image from the server
    crop_resp = requests.get(f'http://localhost:5005/{crop_path.lstrip("/")}')
    if crop_resp.status_code == 200:
        crop_img = Image.open(BytesIO(crop_resp.content)).convert('RGB')
        print(f'Crop: size {crop_img.size}')

        # Compare crop to reference using a simple metric
        # Resize both to same dimensions for comparison
        crop_small = crop_img.resize(ref_img.size)
        ref_small = ref_img.resize(ref_img.size)
        # Calculate mean absolute difference per channel
        import numpy as np
        crop_arr = np.array(crop_small).astype(float)
        ref_arr = np.array(ref_small).astype(float)
        diff = np.mean(np.abs(crop_arr - ref_arr))
        print(f'Mean pixel difference between crop and reference: {diff:.2f}')

        # If the crop is the same as reference (diff < threshold), it's probably correct
        # If the crop is background (different), diff will be higher
        # We also check if the crop looks like a card (has card-like colors/patters)
        # Simple heuristic: check if the crop contains areas with non-white/bright colors
        # (cards are colored, background is often darker or more uniform)
        # For this test, we'll just report the difference and conclude based on a threshold
        threshold = 50  # Adjust based on typical differences
        if diff < threshold:
            print('PASS: Crop appears to be the card (matches reference)')
        else:
            print('FAIL: Crop does not match reference card (likely background or other image)')
    else:
        print(f'Failed to fetch crop: {crop_resp.status_code}')
else:
    print('No crop_image_path in scan result')

# Also verify that the crop is not the same as a background/table edge
# by checking if it contains any of the typical scan background colors (dark brown/white)
# We'll do a quick color check
crop_arr = np.array(crop_small)
# If most pixels are either very dark or very bright, it's likely a background
mean_brightness = np.mean(crop_arr)
print(f'Average brightness of crop: {mean_brightness:.1f}')
if mean_brightness > 200:
    print('Crop is very bright (possible white background)')
elif mean_brightness < 80:
    print('Crop is very dark (possible black/edge background)')
else:
    print('Crop has moderate brightness (likely a card)')

# Print card info for final reference
print(f'\nReference Wailmer (bw6-25) has {len(resp.content)} bytes')
print('Scan result card object:', result.get('card'))