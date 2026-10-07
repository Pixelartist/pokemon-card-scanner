#!/usr/bin/env python3
"""Download all Pokemon card images from pkmncards.com"""
import requests
import re
import time
from pathlib import Path
import json

BASE_URL = "https://pkmncards.com/sets/"
IMAGE_DIR = Path("/opt/data/pokemon-card-scanner/data/clip_images/pkmncards")
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = Path("/opt/data/pokemon-card-scanner/data/pkmncards_download.log")
STATE_FILE = Path("/opt/data/pokemon-card-scanner/data/pkmncards_state.json")

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def log(msg):
    with open(LOG_FILE, 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    print(msg)

# Load state if exists
state = {'processed': 0, 'downloaded': 0, 'failed': [], 'set_index': 0}
if STATE_FILE.exists():
    with open(STATE_FILE) as f:
        state = json.load(f)
    log(f"Resuming from set index {state['set_index']}, already downloaded {state['downloaded']}")

log("Fetching set list...")
r = requests.get(BASE_URL, timeout=30, headers=headers)
html = r.text

# Find all set links
set_links = re.findall(r'href="(https://pkmncards\.com/set/[^"]+)"', html)
unique_sets = list(dict.fromkeys(set_links))
log(f"Found {len(unique_sets)} unique set pages")

total_downloaded = state['downloaded']
total_cards = 0

for i in range(state['set_index'], len(unique_sets)):
    set_url = unique_sets[i]
    log(f"[{i+1}/{len(unique_sets)}] Processing: {set_url}")
    
    try:
        r = requests.get(set_url, timeout=30, headers=headers)
        if r.status_code != 200:
            log(f"  Failed: HTTP {r.status_code}")
            state['failed'].append(set_url)
            continue
        
        html = r.text
        
        # Find card images
        img_pattern = r'<img[^>]+src="(https://pkmncards\.com/wp-content/uploads/[^"]+?_std\.jpg)"'
        images = re.findall(img_pattern, html)
        
        if not images:
            img_pattern = r'href="https://pkmncards\.com/wp-content/uploads/[^"]+?_std\.jpg"'
            images = [m.replace('href="', '').replace('"', '') for m in re.findall(img_pattern, html)]
        
        total_cards += len(images)
        log(f"  Found {len(images)} cards")
        
        for img_url in images:
            filename = img_url.split('/')[-1]
            save_path = IMAGE_DIR / filename
            
            if not save_path.exists():
                try:
                    r = requests.get(img_url, timeout=30, stream=True, headers=headers)
                    if r.status_code == 200:
                        with open(save_path, 'wb') as f:
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                        total_downloaded += 1
                except Exception as e:
                    log(f"  Failed to download {filename}: {e}")
            
            time.sleep(0.05)
        
        # Save state
        state['set_index'] = i + 1
        state['downloaded'] = total_downloaded
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
            
    except Exception as e:
        log(f"  Error processing set: {e}")
        state['failed'].append(set_url)
        continue

log(f"\n{'='*50}")
log(f"SCRAPING COMPLETE!")
log(f"Sets processed: {len(unique_sets)}")
log(f"Total cards found: {total_cards}")
log(f"Successfully downloaded: {total_downloaded}")
log(f"Failed sets: {len(state['failed'])}")
log(f"Images saved to: {IMAGE_DIR}")
