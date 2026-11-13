import sqlite3
import os

db_files = [
    "data/pokemon_cards.db",
    "data/database.db", 
    "database.db"
]

for db_file in db_files:
    if os.path.exists(db_file):
        print(f"\nChecking {db_file}...")
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        
        # Get table names
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cur.fetchall()]
        
        for table in tables:
            if 'card' in table.lower():
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cur.fetchone()[0]
                    
                    cur.execute(f"SELECT id, name, rarity FROM {table} WHERE name LIKE '%Krakeelo%' LIMIT 5")
                    rows = cur.fetchall()
                    
                    if rows:
                        print(f"  Found in table '{table}' ({count} total cards):")
                        for row in rows:
                            print(f"    ID: {row[0]}, Name: {row[1]}, Rarity: {repr(row[2])}")
                except:
                    pass
        
        conn.close()
