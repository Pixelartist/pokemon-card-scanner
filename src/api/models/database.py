"""Database models for Pokemon Card Scanner."""
import os
import sqlite3
import logging
import time
from pathlib import Path
from datetime import datetime

# Calculate path from database.py to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# Honours DATA_DIR so the DB lives with the rest of the data root (api.main
# reads the same variable); defaults to ./data next to the project.
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(BASE_DIR) / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = DATA_DIR / "pokemon_cards.db"


def _ensure_wal(conn, attempts=5):
    """Switch the file to WAL, waiting out anyone starting beside us.

    PRAGMA journal_mode needs an exclusive lock, so workers booting together
    each lost the race, the caller swallowed the error, and the file stayed in
    rollback-journal mode until some later connection happened to win. Retrying
    with a bounded wait makes the switch stick; when it truly cannot be made
    (an outside process is holding the file) the mode that remains gets logged,
    because WAL is what keeps readers from queueing behind a writer.
    """
    try:
        conn.execute("PRAGMA busy_timeout = 2000")
        last = None
        for attempt in range(attempts):
            try:
                mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            except sqlite3.OperationalError as exc:
                last, mode = exc, None
            if str(mode).lower() == "wal":
                return True
            time.sleep(0.2 * (attempt + 1))
        logging.warning(
            "Could not switch the database to WAL after %d attempts (%s): it "
            "keeps its current journal mode, so readers queue behind a writer "
            "instead of reading a snapshot", attempts, last)
        return False
    finally:
        conn.execute("PRAGMA busy_timeout = 5000")


def init_db():
    """Initialize SQLite database with required tables and indexes."""
    conn = sqlite3.connect(DATABASE_URL, timeout=2.0)
    # WAL belongs to the file, and the per-request connections only ask for it
    # best effort (get_db_connection), which is not enough when four workers
    # start at the same moment. Asking again here, before any schema work and
    # with retries, is what makes the mode actually take effect at startup.
    _ensure_wal(conn)
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
            preferred_language TEXT DEFAULT 'EN',
            is_admin INTEGER DEFAULT 0,
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
            crop_image_path TEXT,
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

    # Create card_prices table (persistent price cache for stable valuations)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_prices (
            card_id TEXT PRIMARY KEY,
            prices TEXT NOT NULL,
            fetched_at TEXT NOT NULL
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
        # /api/collection orders by added_at DESC per user; without this the
        # query sorts the whole table on every page load.
        "CREATE INDEX IF NOT EXISTS idx_collection_items_user_added ON collection_items(user_id, added_at DESC)",
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
    
    # False detections corrected by the user in the review queue. One row per
    # correction: the scan/crop image plus wrong and correct card ids. This is
    # the training set for index hardening (re-embed crops, add confuser pairs).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS misreads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_image_path TEXT,
            crop_image_path TEXT,
            wrong_id TEXT NOT NULL,
            wrong_name TEXT,
            correct_id TEXT NOT NULL,
            correct_name TEXT,
            score REAL,
            source TEXT,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_misreads_created ON misreads(created_at)")

    # Admin vetting state for user-submitted corrections. Databases created
    # before the review workflow have the table but none of these columns.
    cursor.execute("PRAGMA table_info(misreads)")
    mr_columns = {r[1] for r in cursor.fetchall()}
    for column, ddl in (
        ("review_status", "TEXT DEFAULT 'pending'"),
        ("reviewed_by", "INTEGER"),
        ("reviewed_at", "TIMESTAMP"),
        ("admin_note", "TEXT"),
    ):
        if column not in mr_columns:
            cursor.execute(f"ALTER TABLE misreads ADD COLUMN {column} {ddl}")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_misreads_status ON misreads(review_status)")

    # Column migrations for pre-existing databases (CREATE TABLE IF NOT
    # skips them, so SQLite needs an explicit ALTER TABLE, one column at a
    # time). Keep in sync with the collection_items schema above.
    cursor.execute("PRAGMA table_info(collection_items)")
    ci_columns = {r[1] for r in cursor.fetchall()}
    if 'scan_image_path' not in ci_columns:
        cursor.execute("ALTER TABLE collection_items ADD COLUMN scan_image_path TEXT")
    if 'crop_image_path' not in ci_columns:
        cursor.execute("ALTER TABLE collection_items ADD COLUMN crop_image_path TEXT")

    cursor.execute("PRAGMA table_info(users)")
    user_columns = {r[1] for r in cursor.fetchall()}
    if 'preferred_language' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN preferred_language TEXT DEFAULT 'EN'")
    if 'is_admin' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")

    # Same story for refresh_tokens: create_refresh_token() deletes revoked rows
    # on every login/registration, so a database created before that flag existed
    # raised "no such column: revoked" *after* the user row had been committed.
    # Registration answered 500 while the account existed.
    cursor.execute("PRAGMA table_info(refresh_tokens)")
    rt_columns = {r[1] for r in cursor.fetchall()}
    if 'revoked' not in rt_columns:
        cursor.execute("ALTER TABLE refresh_tokens ADD COLUMN revoked INTEGER DEFAULT 0")

    # Bootstrap admins: nobody can reach the admin endpoints without the flag, so
    # promote the oldest account (the owner's) when no admin exists yet. An
    # ADMIN_USERNAMES override covers a database whose first account is a test
    # user, and lets a fresh deployment pick its owner explicitly.
    override = [name.strip() for name in os.environ.get("ADMIN_USERNAMES", "").split(",") if name.strip()]
    for name in override:
        cursor.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (name,))
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute("SELECT MIN(id) FROM users")
        first_id = cursor.fetchone()[0]
        if first_id is not None:
            cursor.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_id,))
            logging.info("No admin user existed: promoted the oldest account (id %s) to admin", first_id)

    conn.commit()
    conn.close()
    logging.info("Database initialized at %s", DATABASE_URL)


def get_db_connection():
    """Get a database connection with WAL mode and optimized pragmas."""
    # Opening must not queue behind a lock either: each pragma below waits out
    # the busy timeout, so an outside writer made a new connection spend it
    # three times over before the statement that wanted the lock even ran.
    # Setup gets a short grace period, and the connection takes the real wait
    # back at the end for the work it was opened to do.
    conn = sqlite3.connect(DATABASE_URL, timeout=0.25, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL belongs to the database file, not to this connection, and asking to
    # switch needs an exclusive lock: a connection opened while another was
    # mid-write raised "database is locked", which failed 7 of 8 simultaneous
    # sign-ups. A new connection also cannot prepare synchronous or cache_size
    # while an outside process holds that exclusive lock, since it cannot read
    # the schema, so all three are best effort — a busy answer means somebody
    # else is already there or is about to be, and the defaults still serve.
    for pragma in ("journal_mode = WAL", "synchronous = NORMAL",
                   "cache_size = -200000"):  # 200 MB page cache
        try:
            conn.execute("PRAGMA " + pragma)
        except sqlite3.OperationalError:
            pass
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA mmap_size = 268435456")  # 256 MB mmap
    conn.execute("PRAGMA busy_timeout = 5000")  # a blocked write still waits
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
