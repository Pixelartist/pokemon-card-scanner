"""Regenerate quickdraw-meta.json with image URLs (build ran before image_url patch)."""
import json
from pathlib import Path

base = Path('/opt/data/pokemon-card-scanner')
with open(base / 'src/static/cards_meta.json') as f:
    all_meta = json.load(f)

img_base = base / 'data/images'
entries = []
for m in all_meta:
    img_path = m.get('image', '')
    if not img_path or img_path.startswith('http'):
        continue
    rel = img_path.replace('/static/card_images/', '')
    if (img_base / rel).exists():
        entries.append({
            'ptcg_id': f"{m.get('set', '')}-{m.get('number', '')}",
            'name': m.get('name', ''),
            'set': m.get('set', ''),
            'number': m.get('number', ''),
            'image': img_path,
        })

old = json.load(open(base / 'data/quickdraw-meta.json'))
old_cards = sorted(old['cards'], key=lambda c: c['index'])

mismatch = 0
for oc, ne in zip(old_cards, entries):
    if oc['ptcg_id'] != ne['ptcg_id']:
        mismatch += 1
    oc['image'] = ne['image']

print(f"cards: {len(old_cards)}, new entries: {len(entries)}, ptcg_id mismatches: {mismatch}")
old['cards'] = old_cards
with open(base / 'data/quickdraw-meta.json', 'w') as f:
    json.dump(old, f)
print("meta regenerated with image URLs")
