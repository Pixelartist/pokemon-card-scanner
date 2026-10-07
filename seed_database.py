#!/usr/bin/env python3
"""Seed the database from clip_card_index.json."""
import json
import sqlite3
import os

DB_PATH = "/opt/data/pokemon-card-scanner/data/pokemon_cards.db"
INDEX_PATH = "/opt/data/pokemon-card-scanner/data/clip_card_index.json"


def main():
    # Load the index
    with open(INDEX_PATH, "r") as f:
        data = json.load(f)

    cards = data.get("cards", [])
    print(f"Loaded {len(cards)} cards from index")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # Check existing counts
    cursor.execute("SELECT COUNT(*) FROM cards")
    existing_cards = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sets")
    existing_sets = cursor.fetchone()[0]
    print(f"Existing cards: {existing_cards}, Existing sets: {existing_sets}")

    if existing_cards >= len(cards):
        print("Database already has all cards, skipping seed.")
        conn.close()
        return

    # Clear existing data (optional, but ensures clean state)
    print("Clearing existing card/sets data...")
    cursor.execute("DELETE FROM binder_items")
    cursor.execute("DELETE FROM collection_items")
    cursor.execute("DELETE FROM cards")
    cursor.execute("DELETE FROM sets")
    conn.commit()

    # Create sets first
    set_map = {}
    for card in cards:
        set_code = card.get("set_code", "")
        set_name = card.get("set_name", "")
        if set_code and set_code not in set_map:
            set_map[set_code] = set_name

    print(f"Creating {len(set_map)} sets...")
    for set_code, set_name in set_map.items():
        cursor.execute(
            "INSERT OR IGNORE INTO sets (ptcg_id, name, series, total_cards) VALUES (?, ?, ?, 0)",
            (set_code, set_name, "")
        )
    conn.commit()

    # Get set IDs
    cursor.execute("SELECT ptcg_id, id FROM sets")
    for row in cursor.fetchall():
        set_map[row[0]] = {"name": set_map.get(row[0], row[0]), "id": row[1]}

    # Insert cards
    print(f"Inserting {len(cards)} cards...")
    inserted = 0
    for card in cards:
        set_code = card.get("set_code", "")
        set_info = set_map.get(set_code, {})
        set_id = set_info.get("id", None)

        ptcg_id = card.get("card_id", "")
        name = card.get("name", "")
        number = card.get("number", "")
        rarity = card.get("rarity", "")
        hp = str(card.get("hp", 0)) if card.get("hp") else None
        types = json.dumps(card.get("types", []))
        images = json.dumps({"small": card.get("image_url", ""), "large": card.get("image_url", "")})
        legal = json.dumps({})

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO cards 
                   (ptcg_id, name, number, rarity, set_id, hp, types, artist, rarity_code, images, legal, set_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ptcg_id, name, number, rarity, set_id, hp, types, "", "", images, legal, set_info.get("name", set_code))
            )
            inserted += 1
        except Exception as e:
            print(f"Error inserting card {ptcg_id}: {e}")

    conn.commit()
    print(f"Seeded {inserted} cards and {len(set_map)} sets")

    # Verify
    cursor.execute("SELECT COUNT(*) FROM cards")
    count = cursor.fetchone()[0]
    print(f"Total cards in database: {count}")

    conn.close()


if __name__ == "__main__":
    main()