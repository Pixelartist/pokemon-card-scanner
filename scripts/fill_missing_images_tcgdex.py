#!/opt/data/pokemon-card-scanner/.venv/bin/python
"""Fill missing card images from TCGdex assets (en).

Covers vintage/intl sets our DB never got images for: base*, gym*, neo*,
ecard*, ex*, dp*, pl*, bw*, hgss*, pop*, np, col*, basep, swsh extras.
Japanese-only vintage (PMCG/E/VS/web/PCG/ADV) have no TCGdex scans — skipped.

Writes to data/images/tcgdex/en/{ptcg_id}.webp, URL /static/card_images/tcgdex/en/{ptcg_id}.webp.
Overrides existing images so we can replace placeholder/german with TCGdex webps.
"""
import json, sqlite3, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests

ROOT = Path('/opt/data/pokemon-card-scanner')
IMG_DIR = ROOT / 'data' / 'images' / 'tcgdex' / 'en'
DB = ROOT / 'data' / 'pokemon_cards.db'
API = 'https://api.tcgdex.net/v2/en/sets/{}'
S = requests.Session()
S.headers['User-Agent'] = 'pokemon-card-scanner/1.0'

def local_id_from_ptcg(ptcg_id):
    """Extract numeric localId from DB ptcg_id, stripping leading zeros."""
    if '-' not in ptcg_id:
        return None
    num_part = ptcg_id.rsplit('-', 1)[1]
    try:
        return str(int(num_part))
    except ValueError:
        return num_part  # non-numeric like XY99, HGSS01 stays as-is

def fetch_set(code):
    """Return {localId: image_base_url} for a TCGdex set, or None."""
    for attempt in range(3):
        try:
            r = S.get(API.format(code), timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            out = {}
            for c in r.json().get('cards', []):
                if c.get('image'):
                    out[c['localId']] = c['image']
            return out
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None

def download(url, dest):
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    try:
        r = S.get(url + '/high.webp', timeout=30)
        if r.status_code == 200 and len(r.content) > 1000:
            dest.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False

# Set codes to process: these are on TCGdex but need images in DB
TARGET_SETS = [
    'base1', 'base2', 'base3', 'base4', 'base5', 'basep',
    'gym1', 'gym2',
    'neo1', 'neo2', 'neo3', 'neo4',
    'ecard1', 'ecard2', 'ecard3',
    'ex1', 'ex2', 'ex3', 'ex4', 'ex5', 'ex6',
    'dp1', 'dp2', 'dp5', 'dp6', 'dp7', 'dpp',
    'pl1', 'pl2', 'pl3', 'pl4',
    'bw11', 'bwp',
    'hgss1', 'hgss2', 'hgss3', 'hgss4', 'hgssp',
    'pop1', 'pop2', 'pop3', 'pop4', 'pop5', 'pop6',
    'np', 'col1',
    'sm1', 'sma', 'smp',
    'swshp',
    'xyp',
    'sv', 'svp',
    'B2', 'A4a', 'B1a',
    '30th',
]

def main():
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    total_dl = total_up = 0
    for code in TARGET_SETS:
        # Get all cards in this set from DB
        cards = cur.execute("""
            SELECT c.id, c.ptcg_id FROM cards c 
            JOIN sets s ON c.set_id=s.id
            WHERE s.ptcg_id=?""",
            (code,)).fetchall()
        if not cards:
            print(f"  {code}: no cards in DB — skip")
            continue

        tcg = fetch_set(code)
        if not tcg:
            print(f"  {code}: not on TCGdex — skip")
            continue

        dl = up = 0
        for db_id, ptcg in cards:
            local = local_id_from_ptcg(ptcg)
            if not local:
                continue
            img_url = tcg.get(local)
            if not img_url:
                continue
            dest = IMG_DIR / f"{ptcg}.webp"
            if download(img_url, dest):
                dl += 1
                cur.execute("UPDATE cards SET images=? WHERE id=?",
                    (json.dumps({'small': f'/static/card_images/tcgdex/en/{ptcg}.webp',
                                 'large': f'/static/card_images/tcgdex/en/{ptcg}.webp'}), db_id))
                up += 1
        conn.commit()
        total_dl += dl; total_up += up
        print(f"  {code}: {up}/{len(cards)} images")

    conn.close()
    print(f"Done: {total_up} cards updated with {total_dl} downloaded images")

if __name__ == '__main__':
    main()
