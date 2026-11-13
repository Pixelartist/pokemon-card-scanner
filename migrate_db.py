import sqlite3
import os

DB_PATH = "data/pokemon_cards.db"

def migrate():
    print(f"Migrating database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Add supertype column
    try:
        cur.execute('ALTER TABLE cards ADD COLUMN supertype TEXT DEFAULT "Pokémon"')
        print("Added supertype column")
    except sqlite3.OperationalError:
        print("supertype column already exists")

    # Update supertype for all cards
    cur.execute('UPDATE cards SET supertype = "Pokémon"')
    print(f"Updated {cur.rowcount} cards to supertype 'Pokémon'")

    conn.commit()
    conn.close()
    print("Migration complete")

if __name__ == "__main__":
    migrate()
