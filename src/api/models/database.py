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



def _ensure_wal(budget=1.0):
    """Switch the file to WAL, waiting out anyone starting beside us.

    PRAGMA journal_mode needs an exclusive lock, so workers booting together
    each lost the race, the caller swallowed the error, and the file stayed in
    rollback-journal mode until some later connection happened to win. A
    moment of retrying settles that: a sibling holds the lock for milliseconds.

    That moment is a second in total, not a count of attempts. Something
    outside the process is the only thing that holds the lock longer, and it
    will not hand it over in the next few seconds either: retrying further only
    delays the schema work below, which cannot run until the lock is gone, so
    startup spent twenty seconds here before reporting what it already knew.
    It uses its own short-lived connection so it never has to borrow, and then
    restore, the timeout of the caller's connection.
    """
    conn = sqlite3.connect(DATABASE_URL, timeout=0.25)
    try:
        last = None
        deadline = time.monotonic() + budget
        while True:
            try:
                if str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() == "wal":
                    return
            except sqlite3.OperationalError as exc:
                last = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        logging.warning(
            "Could not switch the database to WAL within %ss (%s): it keeps "
            "its current journal mode, so readers queue behind a writer "
            "instead of reading a snapshot", budget, last)
    finally:
        conn.close()


def _add_column(cursor, table, column, ddl):
    """Add a column, treating "already there" as success."""
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def _create_schema():
    """Create the tables, indexes and columns the app needs.

    Split from init_db so a locked database can be retried: everything here is
    CREATE ... IF NOT EXISTS, or a migration that treats "already there" as
    success, so running the whole sequence again after a failure is safe.
    """
    # Two seconds, not the five-second default: init_db does the waiting, so a
    # single attempt should not also sit on the lock.
    conn = sqlite3.connect(DATABASE_URL, timeout=2.0)
    try:
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

        # Column migrations for pre-existing databases: CREATE TABLE IF NOT EXISTS
        # skips them, so each needs an explicit ALTER. Asking the table first
        # whether the column is there is not needed, and was a race: in WAL another
        # worker copies the schema while one alters it, so four workers starting
        # together all saw the old columns, all ran the same ALTER, and the losers
        # died on "duplicate column name" -- one worker dead per cold start.
        for column, ddl in (
            ("review_status", "TEXT DEFAULT 'pending'"),
            ("reviewed_by", "INTEGER"),
            ("reviewed_at", "TIMESTAMP"),
            ("admin_note", "TEXT"),
        ):
            _add_column(cursor, "misreads", column, ddl)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_misreads_status ON misreads(review_status)")

        # Keep in sync with the CREATE TABLE statements above.
        for table, column, ddl in (
            ("collection_items", "scan_image_path", "TEXT"),
            ("collection_items", "crop_image_path", "TEXT"),
            ("users", "preferred_language", "TEXT DEFAULT 'EN'"),
            ("users", "is_admin", "INTEGER DEFAULT 0"),
            ("refresh_tokens", "revoked", "INTEGER DEFAULT 0"),
        ):
            _add_column(cursor, table, column, ddl)

        # Admin bootstrap is explicit: an account holds the flag only because the
        # owner named it in ADMIN_USERNAMES or ADMIN_EMAILS. Promoting the oldest
        # row was a guess, and after an admin was deleted it handed the flag to
        # whichever test account happened to be oldest.
        names = [n.strip() for n in os.environ.get("ADMIN_USERNAMES", "").split(",") if n.strip()]
        emails = [e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
        for name in names:
            cursor.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (name,))
        for email in emails:
            cursor.execute("UPDATE users SET is_admin = 1 WHERE lower(email) = lower(?)", (email,))

        cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        if cursor.fetchone()[0] == 0:
            cursor.execute("SELECT COUNT(*) FROM users")
            if cursor.fetchone()[0] == 0:
                # The register route hands the flag to the very first account, so a
                # fresh install still has an owner without any setting.
                logging.info("No accounts yet: the first one registered becomes admin")
            elif names or emails:
                logging.warning(
                    "ADMIN_USERNAMES=%s and ADMIN_EMAILS=%s match no account, so nobody "
                    "can use /api/admin: register that account, then restart",
                    ",".join(names) or "-", ",".join(emails) or "-",
                )
            else:
                logging.warning(
                    "No admin account and neither ADMIN_USERNAMES nor ADMIN_EMAILS is "
                    "set, so nobody can use /api/admin: set one to an existing account "
                    "and restart",
                )
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize SQLite database with required tables and indexes.

    A cold start can meet a lock held by another process, and dying on it only
    sent uvicorn to respawn the worker into the same lock, so wait for it: the
    schema is what every request needs and nothing is served without it.
    """
    # WAL belongs to the file, so it is set here, once, before any schema work:
    # the per-request connections only ask for it best effort, which is not
    # enough when four workers start at the same moment.
    _ensure_wal()
    # Long enough for a restore or a backup to finish, short enough that a path
    # pointing at another process's locked file still ends in a clear failure.
    wait = 30
    deadline = time.monotonic() + wait
    waiting_since = None
    while True:
        try:
            _create_schema()
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if waiting_since is None:
                waiting_since = time.monotonic()
                logging.warning(
                    "Database is locked by another process (%s): waiting up to "
                    "%ds for it before setting up the schema", exc, wait)
            if time.monotonic() >= deadline:
                logging.error(
                    "Gave up on the schema after waiting %ds for the database "
                    "lock: %s", wait, exc)
                raise
            time.sleep(0.5)
    if waiting_since is not None:
        # The schema got the lock, so WAL can have it now as well: the first
        # attempt gave up while the file was held.
        _ensure_wal()
        logging.info("Schema ready after waiting %.1fs for the database lock",
                     time.monotonic() - waiting_since)
    logging.info("Database initialized at %s", DATABASE_URL)


def get_db_connection():
    """Get a database connection with the pragmas the app expects."""
    # Setup must not queue behind a lock: each pragma waits out the busy
    # timeout, so with the 5 s default a new connection spent it three times
    # over before the statement that wanted the lock even ran. Setup gets a
    # short grace, and the work itself gets the real wait back at the end.
    conn = sqlite3.connect(DATABASE_URL, timeout=0.25, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # A connection opened while another process holds the lock can prepare none
    # of these, so all are best effort: a busy answer means somebody else is
    # already there or is about to be, and the current settings still serve.
    # journal_mode is the file's and init_db sets it, repeated here only in
    # case that lost the lock; synchronous and cache_size are per connection.
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
