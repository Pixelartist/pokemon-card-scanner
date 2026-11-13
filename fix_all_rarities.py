import sqlite3
import json
import os

# Load any existing rarity lookup
rarity_lookup = {}
lookup_file = "data/rarity_lookup.json"
if os.path.exists(lookup_file):
    with open(lookup_file, "r", encoding="utf-8") as f:
        rarity_lookup = json.load(f)
    print(f"Loaded {len(rarity_lookup)} existing mappings")

# German rarity mappings from clip_card_index
# These are approximate - in production you'd want to fetch from an API
german_rarities = {
    # Common patterns for German sets
    "Häufig": "Common",
    "Ungewöhnlich": "Uncommon", 
    "Selten": "Rare Holo",
    "Illustration rare": "Ultra Rare",
    "Selten, Illustration": "Ultra Rare",
    "Selten, besondere Illustration": "Ultra Rare",
    "Ultra Selten": "Secret Rare",
    "Holo Rare V": "Rare Holo V",
    "Seltene Illustration": "Ultra Rare",
    # English patterns that might be in DB
    "Common": "Common",
    "Uncommon": "Uncommon",
    "Rare": "Rare Holo",
    "Rare Holo": "Rare Holo",
    "Ultra Rare": "Ultra Rare",
    "Secret Rare": "Secret Rare",
    "Rare Secret": "Secret Rare",
    "Amazing Rare": "Ultra Rare",
    "Radiant Rare": "Radiant Rare",
    "Rainbow Rare": "Rainbow Rare",
}

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Get cards with empty or problematic rarities
print("\nFetching cards needing fixes...")
cur.execute("""
    SELECT id, ptcg_id, rarity FROM cards 
    WHERE (rarity IS NULL OR rarity = '' OR 
           rarity NOT IN ('Common', 'Uncommon', 'Rare Holo', 'Ultra Rare', 'Secret Rare',
                         'Rare', 'Promo'))
    AND ptcg_id IS NOT NULL
    LIMIT 5000
""")
rows = cur.fetchall()

print(f"Found {len(rows)} cards to process")

updated = 0
for row_id, ptcg_id, old_rarity in rows:
    # Try lookup first
    new_rarity = rarity_lookup.get(ptcg_id)
    
    if not new_rarity:
        # Check German mappings
        if old_rarity and old_rarity in german_rarities:
            new_rarity = german_rarities[old_rarity]
        else:
            # Use heuristic based on set and number
            parts = ptcg_id.split("-")
            if len(parts) == 2:
                set_code = parts[0].lower()
                try:
                    number = int(parts[1])
                    
                    # Heuristic by set
                    if set_code.startswith("sv"):
                        if number >= 183:
                            new_rarity = "Secret Rare"
                        elif number >= 151:
                            new_rarity = "Ultra Rare"
                        elif number <= 50:
                            new_rarity = "Common"
                        elif number <= 100:
                            new_rarity = "Uncommon"
                        else:
                            new_rarity = "Rare Holo"
                    elif set_code.startswith("swsh"):
                        if number >= 185:
                            new_rarity = "Secret Rare"
                        elif number >= 151:
                            new_rarity = "Ultra Rare"
                        elif number <= 50:
                            new_rarity = "Common"
                        elif number <= 100:
                            new_rarity = "Uncommon"
                        else:
                            new_rarity = "Rare Holo"
                    else:
                        # Default: assume common/uncommon/rare based on number
                        if number <= 30:
                            new_rarity = "Common"
                        elif number <= 60:
                            new_rarity = "Uncommon"
                        else:
                            new_rarity = "Rare Holo"
                except:
                    new_rarity = "Common"
    
    if new_rarity:
        try:
            cur.execute("UPDATE cards SET rarity = ? WHERE id = ?", (new_rarity, row_id))
            updated += 1
        except Exception as e:
            pass
    
    if updated % 500 == 0:
        print(f"Updated {updated} so far...")

conn.commit()
conn.close()

print(f"\nTotal updated: {updated}")
