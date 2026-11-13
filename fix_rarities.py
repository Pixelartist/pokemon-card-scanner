import sqlite3
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = "data/pokemon_cards.db"

def get_rarity_from_api(ptcg_id):
    """Fetch rarity from Pokemon TCG API"""
    if not ptcg_id or "-" not in str(ptcg_id):
        return None
    
    set_code = str(ptcg_id).split("-")[0].upper()
    card_number = str(ptcg_id).split("-")[1]
    
    try:
        url = f"https://api.pokemontcg.io/v2/cards?set={set_code}&name={card_number}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("data"):
                for card in data["data"]:
                    if str(card.get("number", "")) == card_number:
                        return card.get("rarity", "")
        
        # Try alternative endpoint
        url = f"https://api.pokemontcg.io/v2/cards/{ptcg_id.lower()}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("rarity", "")
            
    except Exception as e:
        pass
    
    return None

def batch_update_rarities():
    """Update all cards with missing rarities"""
    print(f"Opening database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get all cards with ptcg_id but no rarity
    print("Fetching cards with missing rarities...")
    cur.execute("""
        SELECT id, ptcg_id FROM cards 
        WHERE (rarity IS NULL OR rarity = '') 
        AND ptcg_id IS NOT NULL 
        AND ptcg_id != ''
        LIMIT 1000
    """)
    
    rows = cur.fetchall()
    print(f"Found {len(rows)} cards to update")
    
    updated = 0
    errors = 0
    
    for i, (row_id, ptcg_id) in enumerate(rows):
        rarity = get_rarity_from_api(ptcg_id)
        
        if rarity:
            try:
                cur.execute("UPDATE cards SET rarity = ? WHERE id = ?", (rarity, row_id))
                updated += 1
            except Exception as e:
                errors += 1
        
        if (i + 1) % 100 == 0:
            print(f"Processed {i+1}/{len(rows)}, Updated: {updated}, Errors: {errors}")
        
        time.sleep(0.1)  # Be nice to the API
    
    conn.commit()
    conn.close()
    
    print(f"\nDone! Updated: {updated}, Errors: {errors}")

if __name__ == "__main__":
    batch_update_rarities()
