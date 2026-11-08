"""Sign-in with Microsoft (Entra ID, work/school or personal accounts) and Google, over OpenID
Connect: authorization code flow + PKCE, run by the server (confidential client), ending in a
normal DeliveryOps session (see routes/auth.py).

Trust rules for the e-mail used to match an existing account — a provider must vouch for it:
  - Google: `email` with `email_verified: true` (and `hd` when GOOGLE_HOSTED_DOMAIN is set).
  - Microsoft: `preferred_username` (the UPN — its domain must be verified in the tenant), or
    `email` only when the token says the domain owner verified it (`xms_edov`). The plain `email`
    claim is NOT trusted: a tenant admin can set it to any address ("nOAuth").
Identities are then stored by the provider's immutable id (Microsoft `oid`, Google `sub`).
"""

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from app.config import get_settings

settings = get_settings()

LABELS = {"microsoft": "Microsoft", "google": "Google"}
_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"  # personal Microsoft accounts
_DISCOVERY_TTL = 3600


class OIDCError(Exception):
    """`code` is shown to the SPA (mapped to a message there); `detail` is for the logs."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    name: str
    client_id: str
    client_secret: str
    discovery_url: str
    extra_auth_params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    email: str | None      # trusted e-mail (see module doc), lower-case; None when the provider doesn't vouch for one
    name: str
    claimed_email: str     # whatever address the token shows, for messages only


def provider(name: str) -> Provider | None:
    if name not in settings.sign_in_methods:
        return None
    if name == "microsoft":
        tenant = settings.microsoft_tenant_id or "common"
        return Provider("microsoft", settings.microsoft_client_id or "", settings.microsoft_client_secret or "",
                        f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration")
    if name == "google":
        extra = (("hd", settings.google_hosted_domain),) if settings.google_hosted_domain else ()
        return Provider("google", settings.google_client_id or "", settings.google_client_secret or "",
                        "https://accounts.google.com/.well-known/openid-configuration", extra)
    return None


def external_providers() -> list[str]:
    return [p for p in settings.sign_in_methods if p != "password"]


# ---------------------------------------------------------------- discovery & keys (cached)

_lock = threading.Lock()
_discovery: dict[str, tuple[float, dict]] = {}
_jwks: dict[str, jwt.PyJWKClient] = {}


def discovery(p: Provider) -> dict:
    now = time.monotonic()
    with _lock:
        hit = _discovery.get(p.discovery_url)
        if hit and hit[0] > now:
            return hit[1]
    try:
        doc = httpx.get(p.discovery_url, timeout=10).raise_for_status().json()
    except httpx.HTTPError as exc:
        raise OIDCError("provider_unavailable", f"{p.name} discovery failed: {exc}") from exc
    with _lock:
        _discovery[p.discovery_url] = (now + _DISCOVERY_TTL, doc)
    return doc


def _jwks_client(uri: str) -> jwt.PyJWKClient:
    with _lock:
        client = _jwks.get(uri)
        if client is None:
            client = _jwks[uri] = jwt.PyJWKClient(uri, cache_keys=True, lifespan=3600, timeout=10)
    return client


# ---------------------------------------------------------------- the flow

def new_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) for S256."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(p: Provider, *, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": p.client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "select_account",
        **dict(p.extra_auth_params),
    }
    return f"{discovery(p)['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(p: Provider, *, code: str, redirect_uri: str, code_verifier: str, nonce: str) -> dict:
    """Trade the authorization code for tokens and return the validated ID-token claims."""
    doc = discovery(p)
    try:
        resp = httpx.post(doc["token_endpoint"], timeout=15, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": p.client_id, "client_secret": p.client_secret, "code_verifier": code_verifier,
        })
    except httpx.HTTPError as exc:
        raise OIDCError("provider_unavailable", f"{p.name} token endpoint unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise OIDCError("token_exchange_failed", f"{p.name} token endpoint: {resp.status_code} {resp.text[:300]}")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise OIDCError("token_exchange_failed", f"{p.name} returned no id_token")
    return validate_id_token(p, id_token, nonce, _jwks_client(doc["jwks_uri"]))


def validate_id_token(p: Provider, token: str, nonce: str, jwks: jwt.PyJWKClient) -> dict:
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=p.client_id, leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_iss": False})
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise OIDCError("invalid_token", f"{p.name} id_token rejected: {exc}") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("invalid_token", "nonce mismatch")
    _check_issuer(p, claims)
    return claims


def _check_issuer(p: Provider, claims: dict) -> None:
    iss = claims.get("iss", "")
    if p.name == "google":
        ok = iss in ("https://accounts.google.com", "accounts.google.com")
        if ok and settings.google_hosted_domain and claims.get("hd") != settings.google_hosted_domain:
            raise OIDCError("domain_not_allowed", "Google account outside GOOGLE_HOSTED_DOMAIN")
    else:
        tid = str(claims.get("tid", ""))
        ok = bool(tid) and iss == f"https://login.microsoftonline.com/{tid}/v2.0"
        tenant = (settings.microsoft_tenant_id or "common").lower()
        if ok and tenant == "organizations" and tid == _CONSUMER_TENANT:
            ok = False
        elif ok and tenant == "consumers" and tid != _CONSUMER_TENANT:
            ok = False
        elif ok and tenant not in ("common", "organizations", "consumers") and tid.lower() != tenant:
            ok = False  # single-tenant deployment: only that organisation
    if not ok:
        raise OIDCError("invalid_token", f"unexpected issuer {iss!r}")


def identity(p: Provider, claims: dict) -> Identity:
    claimed = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    name = str(claims.get("name") or "").strip()
    if p.name == "google":
        subject = str(claims["sub"])
        trusted = claimed if claims.get("email_verified") is True and "@" in claimed else None
    else:
        subject = str(claims.get("oid") or f"sub:{claims['sub']}")
        upn = str(claims.get("preferred_username") or "").strip().lower()
        if "@" in upn and "#ext#" not in upn:
            trusted = upn
        elif claims.get("xms_edov") in (True, "true", "1", 1) and "@" in str(claims.get("email") or ""):
            trusted = str(claims["email"]).strip().lower()
        else:
            trusted = None
    return Identity(p.name, subject, trusted, name or (trusted or claimed).split("@")[0], claimed)


def domain_allowed(email: str) -> bool:
    allowed = {d.strip().lower().lstrip("@") for d in settings.auth_allowed_domains.split(",") if d.strip()}
    return not allowed or email.rsplit("@", 1)[-1] in allowed
