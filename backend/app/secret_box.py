"""Symmetric encryption for secrets stored in the database (LLM API keys entered in the UI).

Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from SECRETS_KEY, or AUTH_SECRET when that is
unset. The plaintext never leaves the backend: the API only ever returns a masked hint."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_WEAK = {"", "dev-insecure-change-me"}


def _secret() -> str:
    return settings.secrets_key or settings.auth_secret or ""


def strong_secret_configured() -> bool:
    secret = _secret()
    return secret not in _WEAK and len(secret) >= 16


def _fernet() -> Fernet:
    digest = hashlib.sha256(("deliveryops-secrets:" + _secret()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """None when the value can't be read (encryption secret changed since it was stored)."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        log.warning("a stored secret could not be decrypted (SECRETS_KEY/AUTH_SECRET changed?)")
        return None


def mask(secret: str) -> str:
    """Enough to recognise which key is stored, never enough to use it."""
    if len(secret) <= 12:
        return "••••"
    return f"{secret[:4]}…{secret[-4:]}"
