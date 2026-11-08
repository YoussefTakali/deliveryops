"""Two-factor authentication: time-based one-time codes (TOTP, RFC 6238 — the codes shown by
Microsoft Authenticator, Google Authenticator, 1Password…) and single-use recovery codes."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30      # seconds per code
DIGITS = 6
WINDOW = 1     # accept the previous/next code too (clock drift)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret: str, code: str, last_step: int = 0, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None. Steps at or before `last_step` are refused, so a
    code can't be replayed."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "DeliveryOps") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(uri: str) -> str:
    """Scannable QR code (SVG data URI) for the authenticator app."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#111", light="#fff")


def new_recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(n)]


def hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()
