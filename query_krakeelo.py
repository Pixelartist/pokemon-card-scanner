import sqlite3
import os

# Find the main database
db_files = [
    'database.db',
    'data/database.db',
    'data/pokemon_cards_en.db'
]

for db_file in db_files:
    if os.path.exists(db_file):
        print(f"Checking {db_file}...")
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute("SELECT id, name, rarity, ptcg_id FROM cards WHERE name LIKE ? OR name LIKE ?", ('%Krakeelo%', '%krakel%'))
            results = cur.fetchall()
            if results:
                print(f"\nFound in {db_file}:")
                for row in results:
                    print(f"  ID: {row[0]}, Name: {row[1]}, Rarity: {row[2]}, PTCG_ID: {row[3]}")
            else:
                print(f"No results in {db_file}")
            conn.close()
        except Exception as e:
            print(f"Error with {db_file}: {e}")
