import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Search for sv04 cards
cur.execute("""
    SELECT id, name, ptcg_id, rarity FROM cards 
    WHERE ptcg_id LIKE 'sv04-%' OR ptcg_id LIKE 'SV04-%'
    LIMIT 20
""")
rows = cur.fetchall()

print(f"Found {len(rows)} sv04 cards:\n")
for row in rows:
    print(f"ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: {repr(row[3])}")

# Check if any have empty rarities
cur.execute("""
    SELECT COUNT(*) FROM cards 
    WHERE (ptcg_id LIKE 'sv04-%' OR ptcg_id LIKE 'SV04-%')
    AND (rarity IS NULL OR rarity = '')
""")
empty_count = cur.fetchone()[0]
print(f"\nCards with empty rarity: {empty_count}")

conn.close()
