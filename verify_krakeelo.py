import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Find Krakeelo cards
cur.execute("""
    SELECT id, name, ptcg_id, rarity FROM cards 
    WHERE name LIKE '%Krakeelo%'
""")
rows = cur.fetchall()

print(f"Found {len(rows)} Krakeelo cards:\n")
for row in rows:
    print(f"ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: '{row[3]}'")

# Also check sv04-212 specifically
print("\n\nChecking sv04-212 specifically:")
cur.execute("""
    SELECT id, name, ptcg_id, rarity FROM cards 
    WHERE ptcg_id = 'sv04-212' OR ptcg_id = 'SV04-212'
""")
rows = cur.fetchall()
if rows:
    for row in rows:
        print(f"ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: '{row[3]}'")
else:
    print("Not found in main database")

conn.close()
