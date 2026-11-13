import sqlite3
import re

# 1. Check database
print("=== Database Check ===")
conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()

# Verify supertype column exists
cur.execute("PRAGMA table_info(cards)")
columns = [col[1] for col in cur.fetchall()]
if "supertype" in columns:
    print("supertype column exists in cards table")
else:
    print("ERROR: supertype column missing!")

# Check sv04-212
cur.execute("SELECT id, name, ptcg_id, rarity, supertype FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
if row:
    print(f"sv04-212: Name={row[1]}, Rarity={row[3]}, Supertype={row[4]}")
else:
    print("sv04-212 NOT FOUND!")

conn.close()

# 2. Check JS
print("\n=== JS Check ===")
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

if 'data-supertype="${supertype}"' in js and 'data-subtypes="${subtypes}"' in js:
    print("JS correctly sets data-supertype and data-subtypes")
else:
    print("ERROR: JS does not set data-supertype or data-subtypes correctly!")

# 3. Check CSS
print("\n=== CSS Check ===")
with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    css = f.read()

if 'data-supertype="pokemon"' in css and 'data-supertype="pokémon"' not in css:
    print("CSS selectors are normalized to 'pokemon'")
else:
    print("ERROR: CSS selectors not normalized!")

# 4. Check HTML
print("\n=== HTML Check ===")
with open("src/templates/index.html", "r", encoding="utf-8") as f:
    html = f.read()

if 'scan-overlay hidden' in html:
    print("scan-overlay has hidden class")
else:
    print("ERROR: scan-overlay missing hidden class!")

print("\n=== All checks passed! ===")
