import requests
import json
import sqlite3
import os

# Get rarities from TCGdex API for a few example sets
example_sets = ["sv04", "sv01", "sv3.5"]
all_rarities = set()

for set_code in example_sets:
    try:
        # Try English API
        url = f"https://dc6.api.pokemontcg.io/v2/{set_code}?limit=5"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for card in data.get("data", [])[:5]:
                rarity = card.get("rarity", "")
                if rarity:
                    all_rarities.add(rarity)
    except Exception as e:
        pass

print("Example rarities from API:")
for r in sorted(all_rarities):
    print(f"  - {r}")

# Also check German metadata for reference
print("\n\nGerman rarities from metadata table:")
if os.path.exists("data/pokemon_cards.db"):
    conn = sqlite3.connect("data/pokemon_cards.db")
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT rarity FROM card_metadata ORDER BY rarity")
        rarities = cur.fetchall()
        for (r,) in rarities:
            print(f"  - {r}")
    except:
        pass
    conn.close()
