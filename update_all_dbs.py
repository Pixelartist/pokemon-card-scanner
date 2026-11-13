import sqlite3
import json
import os

# Load rarity lookup
with open("data/rarity_lookup.json", "r", encoding="utf-8") as f:
    rarity_lookup = json.load(f)

print(f"Loaded {len(rarity_lookup)} rarity mappings")

# Update each database
db_files = ["data/pokemon_cards.db", "data/database.db"]

for db_file in db_files:
    if not os.path.exists(db_file):
        continue
    
    print(f"\nUpdating {db_file}...")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    
    # Get cards needing updates
    cur.execute("""
        SELECT id, ptcg_id FROM cards 
        WHERE (rarity IS NULL OR rarity = '') 
        AND ptcg_id IS NOT NULL 
        AND ptcg_id != ''
        LIMIT 1000
    """)
    rows = cur.fetchall()
    print(f"  Found {len(rows)} cards to check")
    
    updated = 0
    for row_id, ptcg_id in rows:
        if ptcg_id in rarity_lookup:
            try:
                cur.execute("UPDATE cards SET rarity = ? WHERE id = ?", (rarity_lookup[ptcg_id], row_id))
                updated += 1
            except:
                pass
    
    conn.commit()
    conn.close()
    print(f"  Updated {updated} cards")

print("\nDone!")
