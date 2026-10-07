import requests
import json
import time
import subprocess

# Check download progress
try:
    with open("/tmp/german_download_v3.log", "r") as f:
        log = f.read()
    if log:
        print(log[-1000:])
    else:
        print("Log not found or empty")
except Exception as e:
    print(f"Error reading log: {e}")

# Check images
import os
images_dir = "/opt/data/pokemon-card-scanner/data/german_cards/images"
if os.path.exists(images_dir):
    count = len(os.listdir(images_dir))
    print(f"\nImages downloaded: {count}")
else:
    print("\nNo images directory found")

# Check metadata
metadata_file = "/opt/data/pokemon-card-scanner/data/german_cards/german_cards.json"
if os.path.exists(metadata_file):
    with open(metadata_file) as f:
        data = json.load(f)
    print(f"Metadata entries: {len(data)}")
else:
    print("No metadata file found")

# Check if process is running
result = subprocess.run(["pgrep", "-f", "download_german"], capture_output=True, text=True)
print(f"\nDownload process running: {bool(result.stdout.strip())}")
