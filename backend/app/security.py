"""Password hashing (Argon2) and access-token signing (JWT HS256) for native authentication.

Access tokens are short-lived and carry only the subject (user id) — roles, permissions and the
active flag are loaded from the database on every request, so a role change, permission change, or
account deactivation takes effect immediately (no waiting for a token to expire). Refresh tokens are
opaque random strings stored hashed in the database and rotated on each use.
"""

import hashlib
import secrets
import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

settings = get_settings()
_hasher = PasswordHasher()  # sensible defaults (Argon2id)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, Exception):  # noqa: BLE001 — any parse/verify failure = not valid
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:  # noqa: BLE001
        return False


class TokenError(Exception):
    pass


def create_access_token(user_id: str) -> tuple[str, int]:
    """Returns (jwt, expires_in_seconds)."""
    now = int(time.time())
    ttl = settings.access_token_ttl_minutes * 60
    payload = {"sub": user_id, "iat": now, "exp": now + ttl, "typ": "access"}
    return jwt.encode(payload, settings.auth_secret, algorithm="HS256"), ttl


def decode_access_token(token: str) -> str:
    """Returns the subject (user id), or raises TokenError."""
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != "access":
        raise TokenError("wrong token type")
    return claims["sub"]


MFA_TOKEN_TTL = 300


def create_mfa_token(user_id: str) -> str:
    """Proof that the password was right, exchanged for a session once the 2FA code is too."""
    now = int(time.time())
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + MFA_TOKEN_TTL, "typ": "mfa"}, settings.auth_secret, algorithm="HS256")


def decode_mfa_token(token: str) -> str:
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != "mfa":
        raise TokenError("wrong token type")
    return claims["sub"]


def new_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Store the hash; hand the raw token to the client once."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
