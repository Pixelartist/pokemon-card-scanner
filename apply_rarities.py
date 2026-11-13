import sqlite3
import json
import os

DB_PATH = "data/pokemon_cards.db"
LOOKUP_FILE = "data/rarity_lookup.json"

if not os.path.exists(LOOKUP_FILE):
    print("Error: Rarity lookup file not found")
    exit(1)

print(f"Loading lookup from {LOOKUP_FILE}...")
with open(LOOKUP_FILE, "r", encoding="utf-8") as f:
    rarity_lookup = json.load(f)

print(f"Loaded {len(rarity_lookup)} mappings")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Get cards needing updates
print("\nFetching cards with empty/null rarity...")
cur.execute("""
    SELECT id, ptcg_id FROM cards 
    WHERE (rarity IS NULL OR rarity = '') 
    AND ptcg_id IS NOT NULL 
    AND ptcg_id != ''
""")
rows = cur.fetchall()
print(f"Found {len(rows)} cards to update")

updated = 0
not_found = 0

for row_id, ptcg_id in rows:
    if ptcg_id in rarity_lookup:
        try:
            cur.execute("UPDATE cards SET rarity = ? WHERE id = ?", (rarity_lookup[ptcg_id], row_id))
            updated += 1
        except Exception as e:
            pass
    else:
        not_found += 1
    
    if (updated + not_found) % 500 == 0:
        print(f"Progress: {updated + not_found}, Updated: {updated}, Not found: {not_found}")

conn.commit()
conn.close()

print(f"\nDone! Total updated: {updated}")
print(f"Cards still missing rarity: {not_found}")
