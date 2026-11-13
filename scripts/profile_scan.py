#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Profile each stage of /api/scan for one image to find the timeout."""
import sys, time
sys.path.insert(0, "/opt/data/pokemon-card-scanner/src")

img_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/data/images/upload_20261023_124013_1.png"
image_bytes = open(img_path, "rb").read()
print(f"Image: {img_path} ({len(image_bytes)/1024:.0f} KB)")

from PIL import Image
from io import BytesIO
im = Image.open(BytesIO(image_bytes))
print(f"Resolution: {im.size}")

t0 = time.time()
from services.card_detector import get_detector
detector = get_detector()
detection = detector.detect_card(image_bytes)
print(f"1. detect_card: {time.time()-t0:.1f}s found={detection.get('found')} conf={detection.get('confidence')}")

t0 = time.time()
from services.clip_matcher import get_clip_matcher
m = get_clip_matcher()
result = m.match_card(image_bytes)
print(f"2. CLIP match_card (FULL frame): {time.time()-t0:.1f}s decision={result.get('decision')}")
for c in result.get("candidates", [])[:3]:
    print(f"   {c['name']} ({c['set']} {c['number']}) sim={c['similarity']}")

t0 = time.time()
m._load_model()
emb1 = m._extract_embedding(image_bytes)
print(f"3. _extract_embedding only: {time.time()-t0:.1f}s")
