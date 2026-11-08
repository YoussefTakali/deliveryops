"""Authentication, RBAC and client isolation.

Every authenticated request resolves to a runtime `User` carrying a `permissions` set (codes from
app.permissions). Endpoints enforce access with `require_permission(...)`; the frontend hides what
the user can't do. Permissions come from:
  - native   : the user's role in the database (`role_permissions`, editable by an admin)
  - keycloak : realm role mapped to the default preset for that role
  - microsoft: the user's role in `microsoft_users` (see app.rbac.ensure_microsoft_user) — the first
    person ever to sign in becomes admin (bootstrap), everyone after starts `unassigned` (zero
    permissions) until an admin assigns them a role. Never treat a successful Microsoft sign-in as
    implying any access on its own.
  - dev      : persona header (local development only; forbidden in production)
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import permissions as perms
from app.config import get_settings
from app.db import get_db
from app.models import PERSONAL_CLIENT, Perimeter

log = logging.getLogger(__name__)
settings = get_settings()

ROLES = ("director", "delivery_manager", "consultant")


@dataclass
class User:
    username: str
    name: str
    role: str
    clients: list[str]  # ["*"] = all clients
    permissions: set[str] = field(default_factory=set)
    email: str = ""
    subject: str = ""
    extra: dict = field(default_factory=dict)

    def can_see(self, client_id: str) -> bool:
        if client_id == PERSONAL_CLIENT:  # personal spaces: owner only, via /api/assistant — never listed or opened as périmètres
            return False
        return "*" in self.clients or client_id in self.clients

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def can_decide(self) -> bool:
        return "recommendation.decide" in self.permissions

    @property
    def is_admin(self) -> bool:
        return "users.manage" in self.permissions

    def public(self) -> dict:
        return {
            "username": self.username, "name": self.name, "role": self.role, "clients": self.clients,
            "email": self.email, "permissions": sorted(self.permissions),
            "can_decide": self.can_decide, "is_admin": self.is_admin,
            "can_manage_roles": "roles.manage" in self.permissions,
            "must_change_password": self.extra.get("must_change_password", False),
        }


def _dev(username: str, name: str, role: str, clients: list[str]) -> User:
    return User(username, name, role, clients, permissions=perms.default_permissions_for(role))


# Personas for local development only (AUTH_MODE=dev) — switchable from the UI to demo isolation.
DEV_USERS = {
    "sarah": _dev("sarah", "Sarah Benali", "director", ["*"]),
    "karim": _dev("karim", "Karim Haddad", "delivery_manager", ["cli-atlas"]),
    "lea": _dev("lea", "Léa Martin", "delivery_manager", ["cli-horizon"]),
    "tom": _dev("tom", "Tom Girard", "consultant", ["cli-atlas"]),
}


# ---------------------------------------------------------------- native

def load_native_user(db: Session, user_id: str) -> User:
    from app import rbac
    from app.models import AuthUser

    au = db.get(AuthUser, user_id)
    if au is None or not au.active:
        raise InvalidToken("account not found or disabled")
    return User(
        username=au.email, name=au.name or au.email, role=au.role.name,
        clients=list(au.clients or []), permissions=rbac.role_permissions(db, au.role),
        email=au.email, subject=au.id, extra={"must_change_password": au.must_change_password},
    )


# ---------------------------------------------------------------- keycloak

@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    url = settings.keycloak_jwks_url or f"{settings.keycloak_issuer}/protocol/openid-connect/certs"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=10)


class InvalidToken(Exception):
    pass


def decode_token(token: str) -> dict:
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.keycloak_audience, issuer=settings.keycloak_issuer,
            leeway=30, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientError as exc:
        log.error("cannot fetch Keycloak signing keys: %s", exc)
        raise InvalidToken("identity provider unavailable") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc


def user_from_claims(claims: dict) -> User:
    realm_roles = set(claims.get("realm_access", {}).get("roles", []))
    role = next((r for r in ROLES if r in realm_roles), None)
    if role is None:
        raise InvalidToken("no DeliveryOps role assigned")
    clients = claims.get("clients") or []
    if isinstance(clients, str):
        clients = [c.strip() for c in clients.split(",") if c.strip()]
    for group in claims.get("groups") or []:
        name = group.rsplit("/", 1)[-1]
        if name.startswith("client:"):
            clients.append(name.split(":", 1)[1])
    name = claims.get("name") or " ".join(filter(None, [claims.get("given_name"), claims.get("family_name")])) or claims.get("preferred_username", "")
    return User(
        username=claims.get("preferred_username") or claims["sub"], name=name, role=role,
        clients=["*"] if role == "director" else sorted(set(clients)), permissions=perms.default_permissions_for(role),
        email=claims.get("email", ""), subject=claims["sub"],
    )


# ---------------------------------------------------------------- microsoft

_MS_CACHE_TTL = 300
_ms_cache: dict[str, tuple[User, float]] = {}


def _microsoft_user(token: str, db: Session) -> User:
    from app import microsoft_graph, rbac

    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _ms_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        profile = microsoft_graph.me(token)
    except microsoft_graph.GraphError as exc:
        raise InvalidToken(f"microsoft: {exc.detail}") from exc
    email = profile.get("mail") or profile.get("userPrincipalName") or ""
    name = profile.get("displayName") or profile.get("userPrincipalName") or ""
    subject = profile.get("id", "")
    if not email:
        raise InvalidToken("microsoft: account has no email/UPN")

    mu = rbac.ensure_microsoft_user(db, email, subject, name)
    if not mu.active:
        raise InvalidToken("microsoft: account disabled")
    user = User(
        username=email, name=mu.name, role=mu.role.name, clients=mu.clients or [],
        permissions=rbac.role_permissions(db, mu.role), email=email, subject=mu.id,
    )
    _ms_cache[key] = (user, time.time() + _MS_CACHE_TTL)
    return user


# ---------------------------------------------------------------- dependency

def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if settings.auth_mode == "dev" and not settings.is_production:
        user = DEV_USERS.get(x_dev_user or "sarah") or DEV_USERS["sarah"]
    else:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
        token = authorization.split(" ", 1)[1]
        try:
            if settings.auth_mode == "native":
                from app.security import TokenError, decode_access_token
                try:
                    user_id = decode_access_token(token)
                except TokenError as exc:
                    raise InvalidToken(str(exc)) from exc
                user = load_native_user(db, user_id)
            elif settings.auth_mode == "microsoft":
                user = _microsoft_user(token, db)
            else:
                user = user_from_claims(decode_token(token))
        except InvalidToken as exc:
            msg = str(exc)
            forbidden = "role" in msg or "disabled" in msg
            detail = "Account has no access" if forbidden else "Invalid or expired token"
            code = status.HTTP_403_FORBIDDEN if forbidden else status.HTTP_401_UNAUTHORIZED
            raise HTTPException(code, detail, headers={"WWW-Authenticate": "Bearer"}) from exc
    request.state.user = user.username  # for access logs and rate limiting
    from app import governance, telemetry  # late import: keeps app.auth import-light
    telemetry.bind_user(user.username)     # attribute every LLM call of this request to the user
    governance.assert_app_access(user.username)  # an admin can suspend a user's access at runtime
    return user


def require_ai(user: User = Depends(current_user)) -> User:
    """Route dependency for interactive AI endpoints: a clear 403 (AI blocked) / 429 (daily quota or
    agent cap reached) instead of a silent degraded answer. The LLM choke point enforces it anyway."""
    from app import governance
    governance.raise_if_ai_denied(user.username)
    return user


def perimeter_guard(perimeter_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Perimeter:
    perimeter = db.get(Perimeter, perimeter_id)
    # 404 rather than 403: do not reveal that another client's perimeter exists
    if not perimeter or not user.can_see(perimeter.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perimeter not found")
    return perimeter


def require_permission(permission: str) -> Callable[..., User]:
    """Dependency factory: 403 unless the user holds `permission`."""
    if permission not in perms.ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")

    def dependency(user: User = Depends(current_user)) -> User:
        if permission not in user.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return user

    return dependency


# Back-compat shims, now permission-based, so existing route code keeps working.
def require_decider(user: User = Depends(current_user)) -> User:
    if not user.can_decide:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: recommendation.decide")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: users.manage")
    return user


def graph_token(authorization: str | None = Header(default=None)) -> str:
    """Extract the Microsoft Graph access token the SPA forwards (used by /api/microsoft routes)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Microsoft token required", headers={"WWW-Authenticate": "Bearer"})
    return authorization.split(" ", 1)[1]
