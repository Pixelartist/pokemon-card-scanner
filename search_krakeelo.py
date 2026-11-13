import sqlite3
import os
import glob

# Find all SQLite databases
db_files = glob.glob("data/**/*.db", recursive=True)
db_files += glob.glob("src/**/*.db", recursive=True)
db_files += glob.glob("*.db", recursive=False)

for db_file in sorted(set(db_files)):
    if os.path.exists(db_file):
        print(f"\nChecking {db_file}...")
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            
            # Get table names
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cur.fetchall()]
            
            # Search in cards or similar tables
            for table in ['cards', 'card', 'pokemons', 'pokemon']:
                if table in tables:
                    try:
                        cur.execute(f"SELECT id, name, rarity, ptcg_id FROM {table} WHERE name LIKE ? OR name LIKE ?", ('%Krakeelo%', '%krakel%'))
                        results = cur.fetchall()
                        if results:
                            print(f"  Found in {table}:")
                            for row in results:
                                print(f"    ID: {row[0]}, Name: {row[1]}, Rarity: {row[2]}, PTCG_ID: {row[3]}")
                    except:
                        pass
            conn.close()
        except Exception as e:
            print(f"  Error: {e}")
