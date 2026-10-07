"""Database models for Pokemon Card Scanner."""
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Calculate path from database.py to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = Path(BASE_DIR) / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = DATA_DIR / "pokemon_cards.db"


def init_db():
    """Initialize SQLite database with required tables and indexes."""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Create sets table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ptcg_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            series TEXT,
            release_date TEXT,
            total_cards INTEGER,
            images TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create cards table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ptcg_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            number TEXT,
            rarity TEXT,
            set_id INTEGER,
            hp TEXT,
            types TEXT,
            artist TEXT,
            rarity_code TEXT,
            images TEXT,
            legal TEXT,
            set_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (set_id) REFERENCES sets (id)
        )
    """)
    
    # Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            full_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create collection_items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            condition TEXT DEFAULT 'Near Mint',
            language TEXT DEFAULT 'EN',
            quantity INTEGER DEFAULT 1,
            notes TEXT,
            is_reverse_holo BOOLEAN DEFAULT FALSE,
            is_first_edition BOOLEAN DEFAULT FALSE,
            date_acquired TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            scan_image_path TEXT,
            FOREIGN KEY (card_id) REFERENCES cards (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # Create binders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS binders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            user_id INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # Create binder_items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS binder_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            binder_id INTEGER NOT NULL,
            collection_item_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (binder_id) REFERENCES binders (id) ON DELETE CASCADE,
            FOREIGN KEY (collection_item_id) REFERENCES collection_items (id) ON DELETE CASCADE
        )
    """)
    
    # Create refresh_tokens table (httpOnly cookie sessions)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # Create indexes for performance
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_cards_ptcg_id ON cards(ptcg_id)",
        "CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)",
        "CREATE INDEX IF NOT EXISTS idx_cards_set_id ON cards(set_id)",
        "CREATE INDEX IF NOT EXISTS idx_cards_set_name ON cards(set_name)",
        "CREATE INDEX IF NOT EXISTS idx_collection_items_card_id ON collection_items(card_id)",
        "CREATE INDEX IF NOT EXISTS idx_collection_items_user_id ON collection_items(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_collection_items_user_card ON collection_items(user_id, card_id)",
        "CREATE INDEX IF NOT EXISTS idx_binders_user_id ON binders(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_binder_items_binder_id ON binder_items(binder_id)",
        "CREATE INDEX IF NOT EXISTS idx_binder_items_collection_id ON binder_items(collection_item_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id)",
    ]
    
    for idx_sql in indexes:
        cursor.execute(idx_sql)
    
    conn.commit()
    conn.close()
    print(f"Database initialized successfully at {DATABASE_URL}")


def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn


# Legacy model classes for backward compatibility
class Card:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Set:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class User:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Binder:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class CollectionItem:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class BinderItem:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
