"""Thin client for the Keycloak Admin REST API, used by the in-app user administration page.

Authenticates with a service account (client credentials grant) that must have the
`realm-management` client role `manage-users` in the DeliveryOps realm. Configure via
KEYCLOAK_ADMIN_BASE_URL / KEYCLOAK_ADMIN_CLIENT_ID / KEYCLOAK_ADMIN_CLIENT_SECRET.

This is optional: if unset, the admin routes return 501 so the rest of the app still works and
user management is done directly in the Keycloak console instead.
"""

import logging
import threading
import time

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class AdminUnavailable(RuntimeError):
    pass


class AdminAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _realm() -> str:
    # the realm segment of the issuer URL, e.g. http://host/realms/deliveryops -> deliveryops
    issuer = settings.keycloak_issuer.rstrip("/")
    return issuer.rsplit("/realms/", 1)[-1]


def configured() -> bool:
    return bool(settings.keycloak_admin_base_url and settings.keycloak_admin_client_secret)


class _TokenCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def get(self, client: httpx.Client) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 10:
                return self._token
            resp = client.post(
                f"{settings.keycloak_admin_base_url.rstrip('/')}/realms/{_realm()}/protocol/openid-connect/token",
                data={"grant_type": "client_credentials", "client_id": settings.keycloak_admin_client_id, "client_secret": settings.keycloak_admin_client_secret},
            )
            if resp.status_code != 200:
                raise AdminUnavailable(f"could not authenticate to Keycloak admin API: {resp.status_code} {resp.text[:200]}")
            data = resp.json()
            self._token, self._expires_at = data["access_token"], time.time() + data.get("expires_in", 60)
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


_tokens = _TokenCache()


def _client() -> httpx.Client:
    if not configured():
        raise AdminUnavailable("Keycloak admin API is not configured (KEYCLOAK_ADMIN_BASE_URL / KEYCLOAK_ADMIN_CLIENT_SECRET)")
    return httpx.Client(timeout=15)


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    with _client() as client:
        token = _tokens.get(client)
        url = f"{settings.keycloak_admin_base_url.rstrip('/')}/admin/realms/{_realm()}{path}"
        resp = client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if resp.status_code == 401:  # token expired between cache check and use: refresh once
            _tokens.invalidate()
            token = _tokens.get(client)
            resp = client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if resp.status_code >= 400:
        raise AdminAPIError(resp.status_code, resp.text[:300])
    return resp


def list_users(search: str = "", max_results: int = 200) -> list[dict]:
    raw_users = _request("GET", "/users", params={"max": max_results, **({"search": search} if search else {})}).json()
    out = []
    for raw in raw_users:
        raw["_dm_roles"] = [r["name"] for r in _request("GET", f"/users/{raw['id']}/role-mappings/realm").json()]
        out.append(_to_user(raw))
    return out


def _to_user(raw: dict) -> dict:
    roles = raw.get("_dm_roles", [])
    attrs = raw.get("attributes", {}) or {}
    return {
        "id": raw["id"], "username": raw.get("username"), "email": raw.get("email", ""),
        "name": " ".join(filter(None, [raw.get("firstName"), raw.get("lastName")])) or raw.get("username"),
        "enabled": raw.get("enabled", True),
        "role": next((r for r in ("director", "delivery_manager", "consultant") if r in roles), None),
        "clients": attrs.get("clients", []),
    }


def get_user(user_id: str) -> dict:
    raw = _request("GET", f"/users/{user_id}").json()
    raw["_dm_roles"] = [r["name"] for r in _request("GET", f"/users/{user_id}/role-mappings/realm").json()]
    return _to_user(raw)


def create_user(*, username: str, email: str, name: str, role: str, clients: list[str], send_invite: bool) -> dict:
    first, _, last = name.partition(" ")
    resp = _request("POST", "/users", json={
        "username": username, "email": email, "firstName": first, "lastName": last or first,
        "enabled": True, "emailVerified": False, "attributes": {"clients": clients},
    })
    user_id = resp.headers["Location"].rsplit("/", 1)[-1]
    set_role(user_id, role)
    if send_invite:
        try:
            _request("PUT", f"/users/{user_id}/execute-actions-email", json=["UPDATE_PASSWORD", "VERIFY_EMAIL"])
        except AdminAPIError as exc:
            log.warning("invite email not sent for %s (SMTP likely unconfigured): %s", username, exc.detail)
    return get_user(user_id)


def update_user(user_id: str, *, name: str | None = None, clients: list[str] | None = None, role: str | None = None, enabled: bool | None = None) -> dict:
    """Keycloak's PUT /users/{id} replaces the whole representation — any field missing from the
    body is cleared, not left alone. So we fetch the current representation and merge changes
    into it rather than PUTting a sparse patch (which would silently wipe email/name/etc.)."""
    if name is not None or clients is not None or enabled is not None:
        current = _request("GET", f"/users/{user_id}").json()
        if name is not None:
            first, _, last = name.partition(" ")
            current["firstName"], current["lastName"] = first, last or first
        if clients is not None:
            current.setdefault("attributes", {})["clients"] = clients
        if enabled is not None:
            current["enabled"] = enabled
        _request("PUT", f"/users/{user_id}", json=current)
    if role is not None:
        set_role(user_id, role)
    return get_user(user_id)


def set_role(user_id: str, role: str) -> None:
    with _client() as client:
        token = _tokens.get(client)
        realm_roles = client.get(f"{settings.keycloak_admin_base_url.rstrip('/')}/admin/realms/{_realm()}/roles", headers={"Authorization": f"Bearer {token}"}).json()
    by_name = {r["name"]: r for r in realm_roles}
    dm_roles = {"director", "delivery_manager", "consultant"}
    missing = dm_roles - by_name.keys()
    if missing:
        raise AdminAPIError(500, f"realm roles missing in Keycloak: {', '.join(sorted(missing))}")
    if role not in dm_roles:
        raise AdminAPIError(400, f"unknown role {role!r}")
    current = _request("GET", f"/users/{user_id}/role-mappings/realm").json()
    to_remove = [r for r in current if r["name"] in dm_roles]
    if to_remove:
        _request("DELETE", f"/users/{user_id}/role-mappings/realm", json=to_remove)
    _request("POST", f"/users/{user_id}/role-mappings/realm", json=[{"id": by_name[role]["id"], "name": role}])


def delete_user(user_id: str) -> None:
    _request("DELETE", f"/users/{user_id}")


def send_password_reset(user_id: str) -> None:
    _request("PUT", f"/users/{user_id}/execute-actions-email", json=["UPDATE_PASSWORD"])
