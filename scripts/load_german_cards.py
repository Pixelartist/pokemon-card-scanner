#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Load de_* German cards from cards_meta.json into the SQLite DB."""
import json, sqlite3, os

ROOT = '/opt/data/pokemon-card-scanner'
DB = os.path.join(ROOT, 'data', 'pokemon_cards.db')
META = os.path.join(ROOT, 'src', 'static', 'cards_meta.json')

with open(META) as f:
    meta = json.load(f)
by_key = {(m['set'], m['number']): m for m in meta}

conn = sqlite3.connect(DB)
cur = conn.cursor()

# English set names for label reuse
cur.execute("SELECT ptcg_id, name, series FROM sets")
en_sets = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

de = [m for m in meta if m.get('set', '').startswith('de_')]
# unique ptcg_ids (cards_meta may have dupes)
seen = set()
rows = []
for m in de:
    ptcg_id = f"{m['set']}-{m['number']}"
    if ptcg_id not in seen:
        seen.add(ptcg_id)
        rows.append((ptcg_id, m))

set_codes = sorted({m['set'] for _, m in rows})
inserted_sets = inserted_cards = skipped = 0
for code in set_codes:
    en_code = code[3:]
    name, series = en_sets.get(en_code, (code, ''))
    cur.execute("SELECT id FROM sets WHERE ptcg_id = ?", (code,))
    row = cur.fetchone()
    if row:
        set_pk = row[0]
    else:
        cur.execute(
            "INSERT INTO sets (ptcg_id, name, series, total_cards) VALUES (?, ?, ?, ?)",
            (code, f"DE {name}", series, 0))
        set_pk = cur.lastrowid
        inserted_sets += 1
    for _, m in [r for r in rows if r[1]['set'] == code]:
        ptcg_id = f"{m['set']}-{m['number']}"
        cur.execute("SELECT 1 FROM cards WHERE ptcg_id = ?", (ptcg_id,))
        if cur.fetchone():
            skipped += 1
            continue
        en = by_key.get((en_code, m['number']))
        name_val = (en or {}).get('name') or ptcg_id  # shortcut: no German name source, English twin or id as label
        rarity = m.get('rarity') or (en or {}).get('rarity') or ''
        images = json.dumps({"small": m['image'], "large": m['image']})
        cur.execute(
            """INSERT INTO cards (ptcg_id, name, number, rarity, set_id, hp, types,
                                  artist, rarity_code, images, legal, set_name)
               VALUES (?, ?, ?, ?, ?, '', '[]', '', '', ?, '{}', ?)""",
            (ptcg_id, name_val, m['number'], rarity, set_pk, images, f"DE {code[3:]}"))
        inserted_cards += 1
    cur.execute("UPDATE sets SET total_cards = (SELECT COUNT(*) FROM cards WHERE set_id = ?) WHERE id = ?",
                (set_pk, set_pk))

conn.commit()
cur.execute("SELECT COUNT(*) FROM cards")
total = cur.fetchone()[0]
conn.close()
print(f"German sets inserted: {inserted_sets}, cards inserted: {inserted_cards}, skipped(existing): {skipped}")
print(f"DB total cards now: {total}")
