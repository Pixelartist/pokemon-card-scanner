import sqlite3
from collections import Counter

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Count by rarity
cur.execute("""
    SELECT COALESCE(rarity, '(empty)') as rarity, COUNT(*) as count 
    FROM cards 
    GROUP BY rarity 
    ORDER BY count DESC
""")
results = cur.fetchall()

print(f"Total unique rarities: {len(results)}")
print("\nTop 25 rarities:")
for i, (rarity, count) in enumerate(results[:25]):
    # Safe output - replace problematic chars
    safe_rarity = str(rarity).replace('\u25ca', 'CA').replace('\u2605', '*').replace('\u266b', 'B')
    print(f"{i+1}. [{safe_rarity}]: {count:,}")

conn.close()
