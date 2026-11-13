import json
import re

# 1. Check JS output for createGalleryItem
print("=== JS createGalleryItem Check ===")
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

idx = js.find("createGalleryItem")
if idx != -1:
    func_text = js[idx:idx+1500] # Check a snippet
    if 'data-supertype="${supertype}"' in func_text and 'data-subtypes="${subtypes}"' in func_text:
        print("createGalleryItem correctly sets data-supertype and data-subtypes.")
        
        # Check default value
        if 'supertype = (card.supertype || \'Pokémon\')' in func_text:
            print("  Default supertype is 'Pokémon'.")
        else:
            print("  WARNING: Default supertype not explicitly set to 'Pokémon' in JS.")
    else:
        print("WARNING: createGalleryItem does NOT set data-supertype or data-subtypes correctly!")
else:
    print("WARNING: createGalleryItem function not found.")

# 2. Check CSS for normalized selectors
print("\n=== CSS Normalization Check ===")
with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    css = f.read()

if 'data-supertype="pokemon"' in css and not 'data-supertype="pokémon"' in css:
    print("CSS selectors are normalized to 'pokemon'.")
else:
    print("WARNING: CSS selectors are NOT normalized or still contain accented 'pokémon'!")

# 3. Check for specific Krakeelo card in DB
print("\n=== Krakeelo DB Check ===")
import sqlite3
conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()
cur.execute("SELECT id, name, ptcg_id, rarity, supertype FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
conn.close()

if row:
    print(f"sv04-212: Name={row[1]}, Rarity={row[3]}, Supertype={row[4]}")
    if row[3] == 'Ultra Rare' and row[4] == 'Pokémon':
        print("  Krakeelo (Loudred) in DB is 'Ultra Rare' and 'Pokémon' supertype.")
        print("  This should trigger the Full Art effect.")
    else:
        print("  WARNING: Krakeelo (Loudred) has incorrect rarity or supertype in DB.")
else:
    print("sv04-212 NOT FOUND in database!")
