"""Authentication service for Pokemon Card Scanner."""
import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

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

# Legacy default kept so tokens issued before secret persistence still verify
_LEGACY_DEFAULT_SECRET = "pokemon-card-scanner-dev-secret-change-in-production"


def _load_secret_key() -> str:
    """Resolve the JWT signing secret: env var, then a persisted file, then legacy default.

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
    except OSError:
        return _LEGACY_DEFAULT_SECRET


SECRET_KEY = _load_secret_key()

security = HTTPBearer(auto_error=False)


def create_access_token(data: Dict[str, Any]) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
) -> Dict[str, Any]:
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
        
        if len(user_data.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters"
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
            
            # Create new user
            hashed_pw = hash_password(user_data.password)
            cursor.execute(
                "INSERT INTO users (username, email, hashed_password, full_name) VALUES (?, ?, ?, ?)",
                (user_data.username, user_data.email, hashed_pw, user_data.full_name)
            )
            conn.commit()
            user_id = cursor.lastrowid
            
            return UserResponse(
                id=user_id,
                username=user_data.username,
                email=user_data.email,
                full_name=user_data.full_name,
                created_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
                "SELECT id, username, email, hashed_password, full_name, created_at FROM users WHERE username = ?",
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
                "SELECT id, username, email, full_name, created_at FROM users WHERE id = ?",
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
                created_at=row[4]
            )
        finally:
            conn.close()