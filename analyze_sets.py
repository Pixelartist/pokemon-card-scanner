import sqlite3
import json
from collections import defaultdict

# Load current state
conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Count rarities by set
cur.execute("""
    SELECT 
        SUBSTR(ptcg_id, 1, INSTR(ptcg_id || '-', '-') - 1) as set_code,
        COALESCE(rarity, '(empty)') as rarity,
        COUNT(*) as count
    FROM cards
    WHERE ptcg_id IS NOT NULL
    GROUP BY set_code, rarity
    ORDER BY set_code, count DESC
    LIMIT 50
""")

print("Sample of rarities by set:")
current_set = None
for set_code, rarity, count in cur.fetchall():
    if set_code != current_set:
        print(f"\n{set_code}:")
        current_set = set_code
    print(f"  {repr(rarity)[:30]:<30} {count:>5}")

# Count empty rarities by set
print("\n\nSets with most empty rarities:")
cur.execute("""
    SELECT 
        SUBSTR(ptcg_id, 1, INSTR(ptcg_id || '-', '-') - 1) as set_code,
        COUNT(*) as empty_count
    FROM cards
    WHERE (rarity IS NULL OR rarity = '')
    AND ptcg_id IS NOT NULL
    GROUP BY set_code
    ORDER BY empty_count DESC
    LIMIT 20
""")

for set_code, count in cur.fetchall():
    print(f"  {set_code}: {count:,} cards with empty rarity")

conn.close()
