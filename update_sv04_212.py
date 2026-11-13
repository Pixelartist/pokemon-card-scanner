import sqlite3

conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Update sv04-212 to have Ultra Rare / Full Art rarity
cur.execute("""
    UPDATE cards 
    SET rarity = 'Ultra Rare' 
    WHERE ptcg_id = 'sv04-212'
""")

# Verify
cur.execute("SELECT id, name, ptcg_id, rarity FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
if row:
    print(f"Updated successfully!")
    print(f"  ID: {row[0]}")
    print(f"  Name: {row[1]}")
    print(f"  PTCG_ID: {row[2]}")
    print(f"  Rarity: {repr(row[3])}")
else:
    print("Error: Card not found")

conn.commit()
conn.close()
