import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Search for Krakeelo cards
cur.execute("""
    SELECT id, name, ptcg_id, rarity FROM cards 
    WHERE name LIKE '%Krakeelo%' OR name LIKE '%krakel%'
""")
rows = cur.fetchall()

print(f"Found {len(rows)} Krakeelo cards:\n")
for row in rows:
    print(f"  ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: '{row[3]}'")

conn.close()
