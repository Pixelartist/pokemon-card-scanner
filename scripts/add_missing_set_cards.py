#!/usr/bin/env python3
"""Insert missing card rows for sets present in the sets table but absent
from the cards table, using cached TCGdex set details."""
import json
import sqlite3
from pathlib import Path

PROJECT = Path("/opt/data/pokemon-card-scanner")
CACHE = PROJECT / "data/cache/tcgdex"
DB = PROJECT / "data/pokemon_cards.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

d = json.load(open(CACHE / "sets_en.json"))
lst = d if isinstance(d, list) else d["sets"]

added_total = 0
for s in lst:
    sid = s["id"]
    n = cur.execute("SELECT COUNT(*) FROM cards WHERE ptcg_id LIKE ?", (sid + "-%",)).fetchone()[0]
    if n:
        continue
    detail_p = CACHE / f"set_detail_en_{sid}.json"
    if not detail_p.exists():
        print(f"{sid}: no cached detail, skipping")
        continue
    detail = json.load(open(detail_p))
    for c in detail.get("cards", []):
        img = c.get("image")
        images = ""
        if img:
            images = json.dumps({"small": img + ".png", "large": img + ".png"})
        cur.execute(
            "INSERT OR IGNORE INTO cards (ptcg_id, name, number, set_id, set_name, images) VALUES (?,?,?,?,?,?)",
            (c.get("id", ""), c.get("name", ""), c.get("localId", ""), None, detail.get("name", s.get("name", "")), images),
        )
        added_total += cur.rowcount if cur.rowcount > 0 else 0
    print(f"{sid}: +{cur.execute('SELECT COUNT(*) FROM cards WHERE ptcg_id LIKE ?', (sid+'-',)).fetchone()[0]} cards")

conn.commit()
cur.execute("SELECT COUNT(*) FROM cards")
print("cards total now:", cur.fetchone()[0], f"(added {added_total})")
conn.close()
