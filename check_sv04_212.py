import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Find sv04-212
cur.execute("SELECT id, name, ptcg_id, rarity FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
if row:
    print(f"Current state:")
    print(f"  ID: {row[0]}")
    print(f"  Name: {row[1]}")
    print(f"  PTCG_ID: {row[2]}")
    print(f"  Rarity: {repr(row[3])}")
else:
    print("sv04-212 not found")

# Also check if there's a Krakeelo entry
cur.execute("SELECT id, name, ptcg_id, rarity FROM cards WHERE name = 'Krakeelo' AND ptcg_id LIKE 'sv04-%'")
rows = cur.fetchall()
if rows:
    print(f"\nKrakeelo entries in sv04:")
    for row in rows:
        print(f"  ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: {repr(row[3])}")

conn.close()
