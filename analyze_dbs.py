import sqlite3
import os

db_files = ["data/pokemon_cards.db", "data/database.db"]

for db_file in db_files:
    if not os.path.exists(db_file):
        print(f"\n{db_file}: NOT FOUND")
        continue
    
    print(f"\n=== {db_file} ===")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    
    # Get all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [t[0] for t in cur.fetchall()]
    print(f"Tables: {', '.join(tables)}")
    
    # Check each table for cards
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            
            # Check if it has a name column with Krakeelo
            try:
                cur.execute(f"SELECT id, name, rarity FROM {table} WHERE name LIKE '%Krakeelo%' LIMIT 3")
                rows = cur.fetchall()
                if rows:
                    print(f"\n  Table '{table}' ({count} records):")
                    print(f"    Found Krakeelo entries:")
                    for row in rows:
                        print(f"      ID: {row[0]}, Name: {row[1]}, Rarity: {repr(row[2])}")
            except:
                pass
            
        except Exception as e:
            pass
    
    conn.close()
