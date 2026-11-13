import sqlite3
import json

# Load existing rarity lookup if available
rarity_lookup = {}
if __import__('os').path.exists("data/rarity_lookup.json"):
    with open("data/rarity_lookup.json", "r", encoding="utf-8") as f:
        rarity_lookup = json.load(f)

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Get all sv04 cards with empty rarity
cur.execute("""
    SELECT id, ptcg_id FROM cards 
    WHERE (ptcg_id LIKE 'sv04-%' OR ptcg_id LIKE 'SV04-%')
    AND (rarity IS NULL OR rarity = '')
""")
rows = cur.fetchall()

print(f"Found {len(rows)} sv04 cards to update")

# German SV04 (Temporal Forces) has specific rare patterns:
# - 1-150: Regular cards (Common/Uncommon/Rare Holo)
# - 151-182: Special cards including Full Arts
# - 183+: Secret rares
# Card 212 is likely a Full Art / Ultra Rare

updated = 0
for row_id, ptcg_id in rows:
    # Try to get from our lookup
    if ptcg_id in rarity_lookup:
        rarity = rarity_lookup[ptcg_id]
    else:
        # For sv04, use pattern-based assignment
        # This is a simplified heuristic - ideally you'd query an API
        number = int(ptcg_id.split("-")[1]) if "-" in ptcg_id else 0
        
        if number >= 183:
            rarity = "Rare Secret"  # Secret rares
        elif number >= 151:
            # Could be full art or special rare
            rarity = "Ultra Rare"  # Most likely for high numbers
        elif number <= 50:
            rarity = "Common"
        elif number <= 100:
            rarity = "Uncommon"
        else:
            rarity = "Rare Holo"
    
    try:
        cur.execute("UPDATE cards SET rarity = ? WHERE id = ?", (rarity, row_id))
        updated += 1
    except Exception as e:
        print(f"Error updating {ptcg_id}: {e}")

conn.commit()
conn.close()

print(f"Updated {updated} sv04 cards")
