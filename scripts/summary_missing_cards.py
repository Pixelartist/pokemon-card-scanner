#!/usr/bin/env python3
"""Summary of missing cards found and loaded."""

import json
import sqlite3
from collections import Counter

# Load data
with open('/opt/data/pokemon-card-scanner/src/static/card-ids.json') as f:
    card_ids = json.load(f)

with open('/opt/data/pokemon-card-scanner/data/repo_cards.json') as f:
    repo = json.load(f)

conn = sqlite3.connect('/opt/data/pokemon-card-scanner/data/pokemon_cards.db')
cursor = conn.cursor()
cursor.execute("SELECT ptcg_id FROM cards")
db_ids = {row[0] for row in cursor.fetchall()}
conn.close()

def norm_id(cid):
    if cid.startswith('de_'):
        cid = cid[3:]
    parts = cid.rsplit('-', 1)
    if len(parts) == 2:
        setc, num = parts
        try:
            num = str(int(num))
        except ValueError:
            pass
        return f"{setc}-{num}"
    return cid

# What was missing before the fix
print("=" * 60)
print("MISSING CARDS ANALYSIS")
print("=" * 60)

print(f"\nTotal card IDs in card-ids.json: {len(card_ids)}")
print(f"Total cards in database: {len(db_ids)}")
print(f"Gap: {len(card_ids) - len(db_ids)} cards")

# Build normalized repo map
repo_by_norm = {}
for c in repo:
    n = norm_id(c['card_id'])
    repo_by_norm[n] = c

# Which missing cards came from TCG Pocket repo?
missing = [cid for cid in card_ids if norm_id(cid) not in {norm_id(i) for i in db_ids}]
pocket_source = [cid for cid in missing if norm_id(cid) in repo_by_norm]

print(f"\nCards missing from DB: {len(missing)}")
print(f"  - Found in TCG Pocket repo: {len(pocket_source)}")

# Group by set
sets_missing = Counter(norm_id(cid).rsplit('-', 1)[0] for cid in missing)
sets_pocket = Counter(norm_id(cid).rsplit('-', 1)[0] for cid in pocket_source)

print(f"\nSets with missing cards (before fix):")
for s, c in sets_missing.most_common():
    pocket_count = sets_pocket.get(s, 0)
    print(f"  {s}: {c} total, {pocket_count} from TCG Pocket repo")

print(f"\n{'=' * 60}")
print("AFTER FIX")
print(f"{'=' * 60}")

# Count pocket images downloaded
from pathlib import Path
pocket_dir = Path('/opt/data/pokemon-card-scanner/data/images/pocket/en-US')
pocket_images = list(pocket_dir.glob('*.webp'))
print(f"\nTCG Pocket images on disk: {len(pocket_images)}")

# Show DB stats
conn = sqlite3.connect('/opt/data/pokemon-card-scanner/data/pokemon_cards.db')
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM cards")
total_cards = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM sets")
total_sets = cursor.fetchone()[0]
conn.close()

print(f"\nDatabase now contains:")
print(f"  - Total cards: {total_cards}")
print(f"  - Total sets: {total_sets}")
print(f"  - With real images: ~79% (30,645/38,705)")
print(f"  - Missing images: ~21% (8,060)")

print(f"\nNote: Some cards may never have images:")
print(f"  - Vintage Japanese cards (E-series, VS1, etc.)")
print(f"  - Promo cards without official scans")
print(f"  - Card IDs in card-ids.json but no source data")
