#!/usr/bin/env python3
"""Database schema verification script."""
import sqlite3
from pathlib import Path

DATA_DIR = Path('data')
db_path = DATA_DIR / 'pokemon_cards.db'

print("=" * 60)
print("DATABASE SCHEMA VERIFICATION")
print("=" * 60)

if not db_path.exists():
    print(f"✗ Database not found at {db_path}")
    exit(1)

print(f"✓ Database path: {db_path}")

conn = sqlite3.connect(str(db_path))

# Get all tables
print("\n=== Tables ===")
cursor = conn.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [row[0] for row in cursor.fetchall()]
print(f"Tables found: {tables}")

# Check specific table structures
for table_name in tables:
    print(f"\n=== {table_name} schema ===")
    try:
        cursor = conn.execute(f'PRAGMA table_info({table_name})')
        columns = cursor.fetchall()
        print("Columns:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
    except Exception as e:
        print(f"  Error getting schema: {e}")

# Check if users table has expected columns
print("\n=== Checking User table structure ===")
if 'users' in tables:
    try:
        cursor = conn.execute('PRAGMA table_info(users)')
        columns = cursor.fetchall()
        user_columns = [col[1] for col in columns]
        print(f"User columns: {user_columns}")
        
        # Check if there are actually users
        cursor = conn.execute('SELECT COUNT(*) FROM users')
        user_count = cursor.fetchone()[0]
        print(f"User count: {user_count}")
        
        if user_count > 0:
            cursor = conn.execute('SELECT * FROM users LIMIT 3')
            users = cursor.fetchall()
            print("Sample users:")
            for user in users:
                print(f"  {user}")
    except Exception as e:
        print(f"Error checking users table: {e}")

# Check if collection_items table exists and has user_id
print("\n=== Checking CollectionItem table structure ===")
if 'collection_items' in tables:
    try:
        cursor = conn.execute('PRAGMA table_info(collection_items)')
        columns = cursor.fetchall()
        collection_columns = [col[1] for col in columns]
        print(f"CollectionItem columns: {collection_columns}")
        
        # Check if user_id column exists
        if 'user_id' in collection_columns:
            print("✓ user_id column exists in collection_items")
        else:
            print("✗ user_id column missing in collection_items")
            
        # Check if there are any collection items
        cursor = conn.execute('SELECT COUNT(*) FROM collection_items')
        item_count = cursor.fetchone()[0]
        print(f"CollectionItem count: {item_count}")
    except Exception as e:
        print(f"Error checking collection_items table: {e}")
else:
    print("✗ collection_items table does not exist")

# Check if cards table exists
print("\n=== Checking Card table ===")
if 'cards' in tables:
    try:
        cursor = conn.execute('SELECT COUNT(*) FROM cards')
        card_count = cursor.fetchone()[0]
        print(f"Card count: {card_count}")
        if card_count > 0:
            cursor = conn.execute('SELECT id, ptcg_id, name FROM cards LIMIT 5')
            cards = cursor.fetchall()
            print("Sample cards:")
            for card in cards:
                print(f"  ID: {card[0]}, TCG ID: {card[1]}, Name: {card[2]}")
    except Exception as e:
        print(f"Error checking cards table: {e}")

conn.close()

print("\n" + "=" * 60)
print("SCHEMA VERIFICATION COMPLETE")
print("=" * 60)