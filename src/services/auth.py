"""Authentication service for Pokemon Card Scanner."""
import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.models.database import get_db_connection, DATABASE_URL
from api.models.schemas import UserRegister, UserLogin, UserResponse

import sqlite3


def _db():
    """DB connection with name-based row access (SQLite Row factory)."""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    return conn

# JWT Configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30   # short-lived bearer token, auto-refreshed by the frontend
REFRESH_TOKEN_EXPIRE_DAYS = 30     # httpOnly cookie + DB row keeps users signed in

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600

# Where the persistent JWT secret lives (survives container/server restarts)
SECRET_FILE = os.path.join(os.path.dirname(str(DATABASE_URL)), "jwt_secret.key")

def _load_secret_key() -> str:
    """Resolve the JWT signing secret: env var, then a persisted file.

    The file is created on first run and reused across restarts so tokens
    stay valid; a random-per-boot secret would log everyone out.
    """
    env_secret = os.environ.get("JWT_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if env_secret:
        return env_secret
    try:
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE) as f:
                stored = f.read().strip()
            if stored:
                return stored
        new_secret = secrets.token_hex(32)
        os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
        with open(SECRET_FILE, "w") as f:
            f.write(new_secret)
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
        return new_secret
    except OSError as exc:
        # Fail closed. Falling back to a secret published in this repo would
        # let anyone mint a valid JWT for any user id.
        raise RuntimeError(
            "Cannot persist the JWT secret: set JWT_SECRET_KEY or make "
            f"{os.path.dirname(SECRET_FILE)} writable."
        ) from exc


SECRET_KEY = _load_secret_key()

security = HTTPBearer(auto_error=False)


def create_access_token(data: Dict[str, Any]) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Dict[str, Any]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )


# ---------------------------------------------------------------------------
# Refresh tokens (httpOnly cookie + SQLite allowlist, hashed at rest)
# ---------------------------------------------------------------------------

def hash_refresh_token(token: str) -> str:
    """SHA-256 a refresh token for storage (raw value never touches the DB)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token(user_id: int) -> str:
    """Issue an opaque high-entropy refresh token and persist its hash."""
    raw = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    conn = get_db_connection()
    try:
        conn.cursor().execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, hash_refresh_token(raw), expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        # Opportunistic cleanup of expired/revoked rows
        conn.cursor().execute(
            "DELETE FROM refresh_tokens WHERE expires_at < ? OR revoked = 1",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.commit()
    finally:
        conn.close()
    return raw


def validate_refresh_token(token: str) -> Optional[int]:
    """Return stored row's user_id for a valid live refresh token, else None."""
    if not token:
        return None
    conn = get_db_connection()
    try:
        row = conn.cursor().execute(
            "SELECT user_id, revoked, expires_at FROM refresh_tokens WHERE token_hash = ?",
            (hash_refresh_token(token),),
        ).fetchone()
    finally:
        conn.close()
    if not row or row["revoked"]:
        return None
    # sqlite stores expires_at as UTC 'YYYY-MM-DD HH:MM:SS' -> lexicographic compare is safe
    if row["expires_at"] < datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        return None
    return row["user_id"]


def revoke_refresh_token(token: str) -> None:
    """Revoke a refresh token (logout)."""
    if not token:
        return
    conn = get_db_connection()
    try:
        conn.cursor().execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (hash_refresh_token(token),),
        )
        conn.commit()
    finally:
        conn.close()


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> UserResponse:
    """Resolve the current user from a bearer token.

    If the bearer token is missing or expired but the httpOnly refresh cookie
    holds a valid session, a fresh access token is issued and attached to the
    response via the ``X-Renew-Access-Token`` header (the frontend picks it up
    transparently), so sessions survive server restarts and token expiry.
    """
    user = None
    token_error: Optional[HTTPException] = None

    if credentials is not None:
        try:
            payload = verify_token(credentials.credentials)
            user_id_str = payload.get("sub")
            if user_id_str is not None:
                auth = AuthService()
                user = auth.get_user(int(user_id_str))
        except HTTPException as e:
            token_error = e

    if user is None:
        raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
        user_id = validate_refresh_token(raw_refresh) if raw_refresh else None
        if user_id is not None:
            user = AuthService().get_user(user_id)
            if user is not None:
                request.state.renew_access_token = create_access_token({"sub": user_id})

    if user is None:
        raise token_error or HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return user


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        hashed_password.encode('utf-8')
    )


def validate_username(username: str) -> bool:
    """Validate username format."""
    if len(username) < 3 or len(username) > 50:
        return False
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', username))


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


class AuthService:
    """Handles user authentication operations."""
    
    def _get_conn(self):
        conn = get_db_connection()
        conn.row_factory = None
        return conn

    def register(self, user_data: UserRegister) -> UserResponse:
        """Register a new user."""
        # Validate inputs
        if not validate_username(user_data.username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username must be 3-50 characters and contain only letters, numbers, underscores, and hyphens"
            )
        
        if not validate_email(user_data.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email format"
            )
        
        if len(user_data.password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters"
            )
        
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            # Check if username exists
            cursor.execute("SELECT id FROM users WHERE username = ?", (user_data.username,))
            if cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Username already exists"
                )
            
            # Check if email exists
            cursor.execute("SELECT id FROM users WHERE email = ?", (user_data.email,))
            if cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered"
                )
            
            # The very first account owns the deployment; giving it the admin
            # flag is what keeps a fresh install from having no way in.
            cursor.execute("SELECT 1 FROM users LIMIT 1")
            is_first_user = cursor.fetchone() is None

            # Create new user
            hashed_pw = hash_password(user_data.password)
            cursor.execute(
                "INSERT INTO users (username, email, hashed_password, full_name, is_admin) VALUES (?, ?, ?, ?, ?)",
                (user_data.username, user_data.email.strip(), hashed_pw,
                 user_data.full_name, 1 if is_first_user else 0)
            )
            conn.commit()
            user_id = cursor.lastrowid
            
            return UserResponse(
                id=user_id,
                username=user_data.username,
                email=user_data.email.strip(),
                full_name=user_data.full_name,
                is_admin=is_first_user,
                created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            )
        finally:
            conn.close()
    
    def login(self, login_data: UserLogin) -> Dict[str, Any]:
        """Login and return access token."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            # Find user by username
            cursor.execute(
                "SELECT id, username, email, hashed_password, full_name, created_at, preferred_language, is_admin FROM users WHERE username = ?",
                (login_data.username,)
            )
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password"
                )
            
            # Verify password
            if not verify_password(login_data.password, row[3]):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password"
                )
            
            # Create token
            token = create_access_token({"sub": row[0]})
            
            return {
                "access_token": token,
                "token_type": "bearer",
                "user": UserResponse(
                    id=row[0],
                    username=row[1],
                    email=row[2],
                    full_name=row[4],
                    preferred_language=row[6] or "EN",
                    is_admin=bool(row[7]),
                    created_at=row[5]
                )
            }
        finally:
            conn.close()
    
    def get_user(self, user_id: int) -> Optional[UserResponse]:
        """Get user details by ID."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, email, full_name, created_at, preferred_language, is_admin FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return UserResponse(
                id=row[0],
                username=row[1],
                email=row[2],
                full_name=row[3],
                preferred_language=row[5] or "EN",
                is_admin=bool(row[6]),
                created_at=row[4]
            )
        finally:
            conn.close()

    def set_preferred_language(self, user_id: int, language: str) -> Optional[UserResponse]:
        """Update the user's preferred card language (EN, DE, ...)."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET preferred_language = ? WHERE id = ?",
                (language, user_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            return self.get_user(user_id)
        finally:
            conn.close()

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        """Change the user's password after verifying the current one.

        Returns True on success, False if current password is wrong or new password is too short.
        """
        if len(new_password) < 8:
            return False
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            # Verify current password by checking the stored bcrypt hash
            cursor.execute("SELECT hashed_password FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                return False
            if not verify_password(current_password, row[0]):
                return False
            # Hash the new password and update
            new_hash = hash_password(new_password)
            cursor.execute(
                "UPDATE users SET hashed_password = ? WHERE id = ?",
                (new_hash, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def reset_password_by_email(self, email: str, new_password: str) -> bool:
        """Reset a user password using their email address.

        Operator-only: there is no public endpoint calling this. An
        unauthenticated email-based reset let anyone who knew an address take
        over the account.
        """
        if len(new_password) < 8:
            return False
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            if not row:
                return False
            new_hash = hash_password(new_password)
            cursor.execute(
                "UPDATE users SET hashed_password = ? WHERE id = ?",
                (new_hash, row[0])
            )
            conn.commit()
            # Invalidate any active refresh sessions so the old session is gone
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (row[0],)
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Admin operations (called only from the /api/admin endpoints, which are
    # gated by get_current_admin)
    # ------------------------------------------------------------------

    def list_users(self) -> List[Dict[str, Any]]:
        """Every account with the size of its collection (newest account last)."""
        conn = self._get_conn()
        try:
            rows = conn.cursor().execute(
                """
                SELECT u.id, u.username, u.email, u.full_name, u.is_admin,
                       u.created_at, u.preferred_language,
                       COUNT(ci.id) AS item_count,
                       COALESCE(SUM(ci.quantity), 0) AS card_count
                FROM users u
                LEFT JOIN collection_items ci ON ci.user_id = u.id
                GROUP BY u.id
                ORDER BY u.id
                """
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": row[0],
                "username": row[1],
                "email": row[2],
                "full_name": row[3],
                "is_admin": bool(row[4]),
                "created_at": row[5],
                "preferred_language": row[6] or "EN",
                "item_count": int(row[7] or 0),
                "card_count": int(row[8] or 0),
            }
            for row in rows
        ]

    def count_admins(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.cursor().execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1"
            ).fetchone()
        finally:
            conn.close()
        return int(row[0] if row else 0)

    def set_user_password(self, user_id: int, new_password: str) -> bool:
        """Set any account's password and end that account's sessions."""
        if len(new_password) < 8:
            return False
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET hashed_password = ? WHERE id = ?",
                (hash_password(new_password), user_id),
            )
            if cursor.rowcount == 0:
                return False
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def set_user_admin(self, user_id: int, is_admin: bool) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?",
                (1 if is_admin else 0, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_user(self, user_id: int) -> bool:
        """Delete an account and everything that belongs to it.

        collection_items references users without ON DELETE CASCADE, so the rows
        are removed explicitly rather than leaving the delete to fail on the
        foreign key (which is enforced by get_db_connection).
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM binder_items WHERE binder_id IN (SELECT id FROM binders WHERE user_id = ?)",
                (user_id,),
            )
            cursor.execute("DELETE FROM binders WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM collection_items WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def admin_stats(self) -> Dict[str, Any]:
        """Server-side facts the admin panel shows at a glance."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            def one(sql: str) -> int:
                row = cursor.execute(sql).fetchone()
                return int(row[0] or 0) if row else 0

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            stats = {
                "users": one("SELECT COUNT(*) FROM users"),
                "admins": one("SELECT COUNT(*) FROM users WHERE is_admin = 1"),
                "collection_items": one("SELECT COUNT(*) FROM collection_items"),
                "cards_owned": one("SELECT COALESCE(SUM(quantity), 0) FROM collection_items"),
                "cards": one("SELECT COUNT(*) FROM cards"),
                "sets": one("SELECT COUNT(*) FROM sets"),
                "active_sessions": one(
                    f"SELECT COUNT(*) FROM refresh_tokens WHERE revoked = 0 AND expires_at > '{now}'"
                ),
                "review_items": one("SELECT COUNT(*) FROM misreads"),
            }
        finally:
            conn.close()
        try:
            stats["database_mb"] = round(os.path.getsize(str(DATABASE_URL)) / (1024 * 1024), 1)
        except OSError:
            stats["database_mb"] = None
        return stats
