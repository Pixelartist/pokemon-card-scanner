#!/opt/data/projects/finn-house-scraper/.venv/bin/python
"""Scrape Japanese vintage card images from artofpkm.com and update DB."""
import requests, re, json, sqlite3, html as htmlmod, time, os

HEADERS = {"User-Agent": "Mozilla/5.0"}
ART = "https://www.artofpkm.com"
DB = "/opt/data/pokemon-card-scanner/data/pokemon_cards.db"
IMG_DIR = "/opt/data/pokemon-card-scanner/data/images/artofpkm"
os.makedirs(IMG_DIR, exist_ok=True)

# Manual mapping: DB ptcg_id -> artofpkm set id
# Built from artofpkm.com/cards listing page
MANUAL_MAP = {
    'PMCG1': 6, 'PMCG2': 8, 'PMCG3': 9, 'PMCG4': 10,
    'PMCG5': 18, 'PMCG6': 25,
    'E1': 51, 'E2': 56, 'E3': 57, 'E4': 59, 'E5': 61,
    'VS1': 46, 'web1': 50,
    'PCG1': 88, 'PCG2': 91, 'PCG3': 100, 'PCG4': 116,
    'PCG5': 117, 'PCG6': 127, 'PCG7': 130, 'PCG8': 134, 'PCG9': 137,
    'M': 573, 'M6': 595, 'M6a': 592, 'MC': 581,
    'P': 89, 'p': 3,
    'tk': 324, 'bog': 447, 'cel25cc': 446,
    'B2a': 456,
    '2011bw': 455, '2012bw': 399, '2013bw': 400,
    '2014xy': 404, '2015xy': 405, '2016xy': 410,
    '2017sm': 415, '2018sm': 427, '2019sm': 432,
    '2021swsh': 442, '2022swsh': 453, '2023sv': 481, '2024sv': 493,
    'ex5.5': 79, 'exu': 76, 'mfb': 311,
    'miscp': 69, 'sm7.5': 360, 'sve': 477,
    'swsh10tg': 462, 'swsh11tg': 473, 'swsh12tg': 479,
    'swsh12.5gg': 451, 'swsh4.5sv': 419, 'swsh9tg': 456,
}


def fetch_batch(sid, offset):
    url = f"{ART}/sets/{sid}/card_batches"
    r = requests.get(url, params={"direction": "asc", "group": "none",
                                  "offset": offset, "sort": "number"},
                     headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return []
    html = r.text
    out = []
    for m in re.finditer(
            r'data-lightbox-src="(https://cdn\.artofpkm\.com/[a-z0-9]+)"'
            r'[^>]*data-lightbox-url="(/sets/\d+/card/\d+)"', html):
        out.append(m.group(1))
    return out


def fetch_all_cards(sid, total):
    cards, off = [], 0
    while len(cards) < total:
        batch = fetch_batch(sid, off)
        if not batch:
            break
        cards.extend(batch)
        off += 100
        time.sleep(0.05)
    return cards


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    total_dl = total_up = 0
    for ptcg_id, sid in MANUAL_MAP.items():
        # Get DB cards for this set
        cur.execute("SELECT c.id, c.ptcg_id FROM cards c "
                    "JOIN sets s ON c.set_id=s.id WHERE s.ptcg_id=? ORDER BY c.id",
                    (ptcg_id,))
        db_rows = cur.fetchall()
        if not db_rows:
            print(f"  {ptcg_id}: no DB cards")
            continue

        cards = fetch_all_cards(sid, len(db_rows))
        if len(cards) != len(db_rows):
            print(f"  {ptcg_id}: WARNING got {len(cards)}/{len(db_rows)} cards")

        dl = up = 0
        for (db_id, db_ptcg), img_url in zip(db_rows, cards):
            if not img_url:
                continue
            dest = os.path.join(IMG_DIR, f"{db_ptcg}.webp")
            if os.path.exists(dest):
                cur.execute("SELECT images FROM cards WHERE id=?", (db_id,))
                row = cur.fetchone()
                if row and row[0] and '/static/artofpkm' in row[0]:
                    up += 1
                    continue
            try:
                r = requests.get(img_url, headers=HEADERS, timeout=30)
                if r.status_code != 200:
                    continue
                with open(dest, 'wb') as f:
                    f.write(r.content)
                dl += 1
            except Exception:
                continue
            url = f"/static/card_images/artofpkm/{db_ptcg}.webp"
            cur.execute("UPDATE cards SET images=? WHERE id=?",
                        (json.dumps({"small": url, "large": url}), db_id))
            up += 1
        conn.commit()
        total_dl += dl
        total_up += up
        print(f"  {ptcg_id}: {len(db_rows)} cards, {dl} downloaded, {up} updated")

    conn.close()
    print(f"\nDone: {total_dl} downloaded, {total_up} cards updated")


if __name__ == "__main__":
    main()