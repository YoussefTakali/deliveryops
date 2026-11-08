"""Sign-in with Microsoft / Google end to end, in its own process (AUTH_MODE=native + providers set
before any app import). The identity provider is simulated: `authorize_url` / `exchange_code` are
replaced, everything else (state cookie, account matching, sessions) is the real code."""

import os
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

tmp = tempfile.mkdtemp()
os.environ.update(
    AUTH_MODE="native", DATABASE_URL=f"sqlite:///{tmp}/sso.db", AUTH_SECRET="test-secret-sso-0123456789abcdef",
    INITIAL_ADMIN_EMAIL="admin@acme.com", INITIAL_ADMIN_PASSWORD="Admin!2345",
    LLM_MODE="mock", WATCH_ENABLED="false", SEED_DEMO_DATA="false", ENVIRONMENT="development",
    EMBEDDING_PROVIDER="hash", EMBEDDING_DIM="384", FRONTEND_URL="http://testserver",
    AUTH_PROVIDERS="password,microsoft,google", AUTH_ALLOWED_DOMAINS="acme.com",
    MICROSOFT_TENANT_ID="common", MICROSOFT_CLIENT_ID="ms-client", MICROSOFT_CLIENT_SECRET="ms-secret",
    GOOGLE_CLIENT_ID="g-client", GOOGLE_CLIENT_SECRET="g-secret",
)

from fastapi.testclient import TestClient  # noqa: E402

from app import oidc  # noqa: E402

CLAIMS: dict[str, dict] = {}
oidc.authorize_url = lambda p, *, redirect_uri, state, nonce, code_challenge: f"https://idp.test/{p.name}/authorize?state={state}"
oidc.exchange_code = lambda p, *, code, redirect_uri, code_verifier, nonce: CLAIMS[code]

from app.main import app  # noqa: E402

GOOGLE = {"sub": "g-111", "email": "nina@acme.com", "email_verified": True, "name": "Nina"}
MS_EVE = {"oid": "oid-eve", "tid": "t-acme", "preferred_username": "eve@acme.com", "name": "Eve", "sub": "s1"}
MS_NOAUTH = {"oid": "oid-attacker", "tid": "t-evil", "preferred_username": "x_evil#EXT#@evil.onmicrosoft.com",
             "email": "eve@acme.com", "name": "Not Eve", "sub": "s2"}


def sign_in(client: TestClient, provider: str, claims: dict, next: str = "/", link: bool = False) -> str:
    """Run start → provider → callback; returns the final redirect target."""
    code = f"code-{len(CLAIMS)}"
    CLAIMS[code] = claims
    start = client.get(f"/api/auth/oidc/{provider}/start", params={"next": next, "link": link}, follow_redirects=False)
    assert start.status_code == 303, start.text
    location = start.headers["location"]
    if "auth_error" in location:
        return location
    state = parse_qs(urlparse(location).query)["state"][0]
    done = client.get(f"/api/auth/oidc/{provider}/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert done.status_code == 303, done.text
    return done.headers["location"]


def main() -> None:
    with TestClient(app) as admin_c:
        cfg = admin_c.get("/api/config").json()["sign_in"]
        assert cfg["password"] is True and [p["id"] for p in cfg["providers"]] == ["microsoft", "google"]

        r = admin_c.post("/api/auth/login", json={"email": "admin@acme.com", "password": "Admin!2345"})
        A = {"Authorization": f"Bearer {r.json()['access_token']}"}
        eve = admin_c.post("/api/admin/users", json={"email": "eve@acme.com", "name": "Eve", "role": "director"}, headers=A).json()

        # 1. an unknown Google user of an allowed domain: signed in, but no access until an admin assigns a role
        nina = TestClient(app)
        assert sign_in(nina, "google", GOOGLE, next="/projects") == "http://testserver/projects"
        me = nina.get("/api/me", headers={"Authorization": f"Bearer {nina.post('/api/auth/refresh').json()['access_token']}"}).json()
        assert me["role"] == "unassigned" and me["permissions"] == []
        pending = [n for n in admin_c.get("/api/notifications", headers=A).json()["items"] if n["kind"] == "user_pending_approval"]
        assert any("Nina" in n["title"] for n in pending)

        # 2. a pre-created account is linked by its domain-verified Microsoft UPN — role kept
        eve_c = TestClient(app)
        assert sign_in(eve_c, "microsoft", MS_EVE) == "http://testserver/"
        me = eve_c.get("/api/me", headers={"Authorization": f"Bearer {eve_c.post('/api/auth/refresh').json()['access_token']}"}).json()
        assert me["role"] == "director" and me["email"] == "eve@acme.com"
        # later sign-ins match the immutable oid, even if the UPN changes
        assert sign_in(TestClient(app), "microsoft", {**MS_EVE, "preferred_username": "eve.new@acme.com"}) == "http://testserver/"

        # 3. nOAuth: an unverified `email` claim never takes over an existing account
        assert "auth_error=email_unverified" in sign_in(TestClient(app), "microsoft", MS_NOAUTH)
        # unverified Google e-mail, disallowed domain
        assert "auth_error=email_unverified" in sign_in(TestClient(app), "google", {**GOOGLE, "sub": "g-2", "email_verified": False})
        assert "auth_error=domain_not_allowed" in sign_in(TestClient(app), "google", {**GOOGLE, "sub": "g-3", "email": "bob@gmail.com"})

        # 4. forged/expired flow state, cancelled consent, open redirect
        c = TestClient(app)
        start = c.get("/api/auth/oidc/google/start", follow_redirects=False)
        bad = c.get("/api/auth/oidc/google/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
        assert "auth_error=session_expired" in bad.headers["location"]
        start = c.get("/api/auth/oidc/google/start", follow_redirects=False)  # the forged attempt cleared the flow
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        cancelled = c.get("/api/auth/oidc/google/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
        assert "auth_error=cancelled" in cancelled.headers["location"]
        assert sign_in(TestClient(app), "google", GOOGLE, next="//evil.example") == "http://testserver/"
        assert "auth_error=method_disabled" in TestClient(app).get("/api/auth/oidc/github/start", follow_redirects=False).headers["location"]

        # 5. link a second method from Mon compte, see it, and it can't be stolen by another account
        assert sign_in(admin_c, "google", {**GOOGLE, "sub": "g-admin", "email": "admin@acme.com"}, link=True) == "http://testserver/account?linked=google"
        methods = admin_c.get("/api/account/sign-in-methods", headers=A).json()
        assert methods["password"]["set"] and [i["provider"] for i in methods["identities"]] == ["google"]
        assert [m["provider"] for m in methods["can_link"]] == ["microsoft"]
        assert "auth_error=identity_in_use" in sign_in(eve_c, "google", {**GOOGLE, "sub": "g-admin"}, link=True)
        assert "auth_error=session_required" in sign_in(TestClient(app), "google", GOOGLE, link=True)
        # unlinking: fine when a password remains, refused when it's the only way in
        assert admin_c.delete(f"/api/account/sign-in-methods/{methods['identities'][0]['id']}", headers=A).status_code == 200
        N = {"Authorization": f"Bearer {nina.post('/api/auth/refresh').json()['access_token']}"}
        only = nina.get("/api/account/sign-in-methods", headers=N).json()["identities"][0]
        assert nina.delete(f"/api/account/sign-in-methods/{only['id']}", headers=N).status_code == 400

        # 6. admins see the methods; a disabled account can't sign in with Microsoft either
        users = {u["email"]: u for u in admin_c.get("/api/admin/users", headers=A).json()}
        assert users["eve@acme.com"]["sign_in_methods"] == ["password", "microsoft"]
        assert users["nina@acme.com"]["sign_in_methods"] == ["google"] and users["nina@acme.com"]["role"] == "unassigned"
        admin_c.put(f"/api/admin/users/{eve['id']}", json={"active": False}, headers=A)
        assert "auth_error=account_disabled" in sign_in(TestClient(app), "microsoft", MS_EVE)

        # 7. coming from AUTH_MODE=microsoft: existing Microsoft users are carried over, once
        from app import rbac
        from app.db import SessionLocal
        from app.models import AuthUser, ExternalIdentity, MicrosoftUser, Role
        with SessionLocal() as db:
            manager = db.query(Role).filter(Role.name == "delivery_manager").one()
            db.add(MicrosoftUser(email="max@acme.com", subject="oid-max", name="Max", role_id=manager.id, clients=["cli-1"]))
            db.commit()
            assert rbac.import_microsoft_users(db) == 1 and rbac.import_microsoft_users(db) == 0
            maxu = db.query(AuthUser).filter(AuthUser.email == "max@acme.com").one()
            assert maxu.role.name == "delivery_manager" and maxu.clients == ["cli-1"] and not maxu.password_hash
            assert db.query(ExternalIdentity).filter(ExternalIdentity.subject == "oid-max").one().user_id == maxu.id
        max_c = TestClient(app)
        assert sign_in(max_c, "microsoft", {"oid": "oid-max", "tid": "t-acme", "preferred_username": "max@acme.com", "sub": "s3"}) == "http://testserver/"

        # 8. SSO-only organisation: no password sign-in, new accounts get no temporary password
        from app.config import get_settings
        get_settings().auth_providers = "microsoft,google"
        assert admin_c.post("/api/auth/login", json={"email": "admin@acme.com", "password": "Admin!2345"}).status_code == 400
        created = admin_c.post("/api/admin/users", json={"email": "zoe@acme.com", "name": "Zoé", "role": "director"}, headers=A).json()
        assert "temporary_password" not in created and created["sign_in_methods"] == []
        assert admin_c.get("/api/config").json()["sign_in"]["password"] is False

    print("SSO FLOW OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        import traceback
        traceback.print_exc()
        print("FAILED")
        sys.exit(1)
