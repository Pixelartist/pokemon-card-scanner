import sqlite3
import json

# 1. Check database
print("=== Database Check ===")
conn = sqlite3.connect("data/pokemon_cards.db")
cur = conn.cursor()
cur.execute("SELECT id, name, ptcg_id, rarity FROM cards WHERE ptcg_id = 'sv04-212'")
row = cur.fetchone()
if row:
    print(f"sv04-212: Name={row[1]}, Rarity={repr(row[3])}")
else:
    print("sv04-212 NOT FOUND in database!")
conn.close()

# 2. Check CSS
print("\n=== CSS Check ===")
with open("src/static/css/holo-effects.css", "r", encoding="utf-8") as f:
    css = f.read()

if "rare ultra" in css.lower():
    print("Found 'rare ultra' in holo-effects.css")
    # Find the relevant section
    idx = css.lower().find("rare ultra")
    print(f"Context: ...{css[max(0,idx-100):idx+200]}...")
else:
    print("WARNING: 'rare ultra' NOT found in holo-effects.css!")

# 3. Check JS
print("\n=== JS Check ===")
with open("src/static/js/main.js", "r", encoding="utf-8") as f:
    js = f.read()

if "classifyHoloEffect" in js:
    print("Found classifyHoloEffect function")
    if "rare ultra" in js:
        print("Found 'rare ultra' return value")
    else:
        print("WARNING: 'rare ultra' NOT found in classifyHoloEffect!")
else:
    print("WARNING: classifyHoloEffect function NOT found!")

# 4. Check HTML
print("\n=== HTML Check ===")
with open("src/templates/index.html", "r", encoding="utf-8") as f:
    html = f.read()

if 'scan-overlay' in html:
    print("Found scan-overlay in HTML")
    if 'hidden' in html.split('scan-overlay')[1].split('>')[0]:
        print("Found 'hidden' class on scan-overlay")
    else:
        print("WARNING: 'hidden' class NOT found on scan-overlay!")
else:
    print("WARNING: scan-overlay NOT found in HTML!")
