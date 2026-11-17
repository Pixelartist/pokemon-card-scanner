#!/usr/bin/env python3
"""Load missing TCG Pocket cards from repo_cards.json into the database."""

import json
import sqlite3
import time
from collections import defaultdict

DB_PATH = '/opt/data/pokemon-card-scanner/data/pokemon_cards.db'
REPO_PATH = '/opt/data/pokemon-card-scanner/data/repo_cards.json'
MISSING_PATH = '/opt/data/pokemon-card-scanner/data/missing_from_db.json'
POCKET_IMG_BASE = 'http://localhost:5005/static/pocket/en-US'


def norm_id(cid):
    """Normalize card ID: strip leading zeros from number part."""
    if cid.startswith('de_'):
        cid = cid[3:]
    parts = cid.rsplit('-', 1)
    if len(parts) == 2:
        setc, num = parts
        try:
            num = str(int(num))
        except ValueError:
            pass
        return f"{setc}-{num}"
    return cid


def main():
    # Load data
    with open(REPO_PATH) as f:
        repo = json.load(f)

    with open(MISSING_PATH) as f:
        missing = json.load(f)

    print(f"Repo cards: {len(repo)}")
    print(f"Missing cards: {len(missing)}")

    # Build normalized repo map
    repo_by_norm = {}
    for c in repo:
        n = norm_id(c['card_id'])
        repo_by_norm[n] = c

    # Connect to DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get existing ptcg_ids
    cur.execute("SELECT ptcg_id FROM cards")
    existing = {row[0] for row in cur.fetchall()}

    # Collect missing cards with repo data
    new_cards = []
    sets_data = defaultdict(lambda: {'cards': [], 'names': set(), 'series': 'TCG Pocket'})

    for cid in missing:
        n = norm_id(cid)
        if n not in repo_by_norm:
            continue
        repo_card = repo_by_norm[n]

        # Skip if already in DB
        if cid in existing or n in existing:
            continue

        # Use the image path from repo, adjust for localhost URL
        img_path = repo_card.get('image', '').lstrip('/')
        image_url = f"{POCKET_IMG_BASE}/{img_path}"

        new_cards.append({
            'ptcg_id': cid,
            'name': repo_card['name'],
            'number': str(repo_card.get('card_id', '').split('-')[-1]),
            'rarity': repo_card.get('rarity', ''),
            'hp': str(repo_card.get('hp', '')),
            'types': json.dumps([repo_card.get('energy', '')]),
            'set_name': repo_card.get('expansion', ''),
            'artist': repo_card.get('artist', ''),
            'images': json.dumps({'large': image_url})
        })

        setc = cid.rsplit('-', 1)[0]
        sets_data[setc]['cards'].append(cid)
        sets_data[setc]['names'].add(repo_card['name'])

    print(f"New cards to insert: {len(new_cards)}")

    # Insert cards
    inserted = 0
    failed = 0
    batch_size = 100

    for i in range(0, len(new_cards), batch_size):
        batch = new_cards[i:i + batch_size]
        for card in batch:
            try:
                setc = card['set_name']
                # Check if set exists
                cur.execute("SELECT id FROM sets WHERE ptcg_id = ?", (setc,))
                set_row = cur.fetchone()

                if set_row:
                    set_id = set_row['id']
                else:
                    # Insert new set
                    cur.execute(
                        "INSERT INTO sets (ptcg_id, name, series, total_cards) VALUES (?, ?, ?, ?)",
                        (setc, f"TCG Pocket {setc}", "TCG Pocket", len(sets_data[setc]['cards']))
                    )
                    set_id = cur.lastrowid

                # Insert card
                cur.execute(
                    """INSERT INTO cards
                       (ptcg_id, name, number, rarity, set_id, hp, types, artist, images, set_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        card['ptcg_id'],
                        card['name'],
                        card['number'],
                        card['rarity'],
                        set_id,
                        card['hp'],
                        card['types'],
                        '',
                        card['images'],
                        setc,
                    )
                )
                inserted += 1
            except Exception as e:
                print(f"  Error inserting {card['ptcg_id']}: {e}")
                failed += 1

        conn.commit()
        elapsed = time.time() - start_time if inserted > 0 else 0
        print(f"  Inserted {inserted}/{len(new_cards)} cards in {elapsed:.1f}s")

    conn.close()

    print(f"\nDone! Inserted {inserted} cards, {failed} failed")

    # Show summary by set
    print("\nCards per set:")
    for setc in sorted(sets_data.keys()):
        count = len(sets_data[setc]['cards'])
        print(f"  {setc}: {count}")


if __name__ == '__main__':
    start_time = time.time()
    main()
