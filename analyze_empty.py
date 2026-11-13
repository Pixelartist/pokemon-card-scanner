import sqlite3
import os

if os.path.exists("data/pokemon_cards.db"):
    conn = sqlite3.connect("data/pokemon_cards.db")
    cur = conn.cursor()
    
    # Count empty vs non-empty rarities
    cur.execute("SELECT COUNT(*) FROM cards WHERE rarity IS NULL OR rarity = ''")
    empty_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM cards WHERE rarity IS NOT NULL AND rarity != ''")
    filled_count = cur.fetchone()[0]
    
    print(f"Total cards: {empty_count + filled_count}")
    print(f"Empty rarity: {empty_count} ({100*empty_count/(empty_count+filled_count):.1f}%)")
    print(f"Has rarity: {filled_count} ({100*filled_count/(empty_count+filled_count):.1f}%)")
    
    # Check if there's a ptcg_id that we can use to fetch from API
    cur.execute("SELECT ptcg_id, rarity FROM cards WHERE ptcg_id IS NOT NULL AND ptcg_id != '' LIMIT 10")
    sample = cur.fetchall()
    print("\nSample cards with ptcg_id:")
    for row in sample:
        print(f"  {row[0]}: '{row[1]}'")
    
    conn.close()
else:
    print("Database not found")
