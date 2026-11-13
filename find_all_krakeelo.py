import sqlite3
import os

# Check all potential DB locations
db_paths = [
    "data/pokemon_cards.db",
    "database.db", 
    "src/models/pokemon.db",
    "data/database.db"
]

for db_path in db_paths:
    if not os.path.exists(db_path):
        continue
    
    print(f"\n=== {db_path} ===")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Get tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]
    
    for table in tables:
        if 'card' in table.lower():
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                
                # Search for Krakeelo
                cur.execute(f"SELECT id, name, ptcg_id, rarity FROM {table} WHERE name LIKE '%Krakeelo%' LIMIT 5")
                rows = cur.fetchall()
                
                if rows:
                    print(f"Table '{table}' ({count} records):")
                    for row in rows:
                        print(f"  ID: {row[0]}, Name: {row[1]}, PTCG_ID: {row[2]}, Rarity: {repr(row[3])}")
            except Exception as e:
                pass
    
    conn.close()
