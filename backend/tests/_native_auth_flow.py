"""Native-auth end-to-end flow, run in its own process (see test_native_auth.py) so AUTH_MODE=native
is set before any app import. Exits 0 on success, 1 on failure."""

import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(
    AUTH_MODE="native", DATABASE_URL=f"sqlite:///{tmp}/n.db", AUTH_SECRET="test-secret-xyz",
    INITIAL_ADMIN_EMAIL="admin@x.io", INITIAL_ADMIN_PASSWORD="Admin!2345",
    LLM_MODE="mock", WATCH_ENABLED="false", SEED_DEMO_DATA="false", ENVIRONMENT="development",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"email": "admin@x.io", "password": "wrong"}).status_code == 401

        r = c.post("/api/auth/login", json={"email": "admin@x.io", "password": "Admin!2345"})
        assert r.status_code == 200, r.text
        data = r.json()
        token = data["access_token"]
        assert data["user"]["role"] == "admin" and "roles.manage" in data["user"]["permissions"]
        H = {"Authorization": f"Bearer {token}"}

        assert c.get("/api/portfolio").status_code == 401  # no token
        assert c.get("/api/portfolio", headers=H).status_code == 200
        assert c.get("/api/me", headers=H).json()["is_admin"] is True

        roles = c.get("/api/admin/roles", headers=H).json()
        names = {r_["name"] for r_ in roles}
        assert {"admin", "director", "delivery_manager", "consultant"} <= names
        consultant = next(r_ for r_ in roles if r_["name"] == "consultant")
        assert "recommendation.decide" not in consultant["permissions"]

        # admin grants the "decide" functionality to the consultant role
        up = c.put(f"/api/admin/roles/{consultant['id']}/permissions",
                   json={"permissions": consultant["permissions"] + ["recommendation.decide"]}, headers=H)
        assert up.status_code == 200 and "recommendation.decide" in up.json()["permissions"], up.text

        # the locked admin role cannot be stripped
        admin_role = next(r_ for r_ in roles if r_["name"] == "admin")
        assert c.put(f"/api/admin/roles/{admin_role['id']}/permissions", json={"permissions": []}, headers=H).status_code == 400

        # create a consultant user
        cu = c.post("/api/admin/users", json={"email": "bob@x.io", "name": "Bob", "role": "consultant", "clients": ["cli-atlas"]}, headers=H)
        assert cu.status_code == 200, cu.text
        temp = cu.json()["temporary_password"]

        rb = c.post("/api/auth/login", json={"email": "bob@x.io", "password": temp})
        assert rb.status_code == 200
        BH = {"Authorization": f"Bearer {rb.json()['access_token']}"}
        bob = c.get("/api/me", headers=BH).json()
        assert "recommendation.decide" in bob["permissions"]  # inherited the freshly-granted role permission
        assert bob["is_admin"] is False
        assert bob["must_change_password"] is True  # temporary password: the UI forces a change
        assert c.get("/api/admin/users", headers=BH).status_code == 403  # consultant can't administer

        assert c.post("/api/auth/refresh").status_code == 200  # rotates the (bob) refresh cookie

        cp = c.post("/api/auth/change-password", json={"current_password": temp, "new_password": "NewPass!234"}, headers=BH)
        assert cp.status_code == 200, cp.text
        assert c.post("/api/auth/login", json={"email": "bob@x.io", "password": temp}).status_code == 401  # old pw dead
        BH = {"Authorization": f"Bearer {cp.json()['access_token']}"}

        # self-service account page
        acc = c.get("/api/account", headers=BH).json()
        assert acc["must_change_password"] is False and acc["can_edit_name"] is True and acc["active_sessions"] == 1
        assert c.put("/api/account", json={"name": "Robert"}, headers=BH).json()["name"] == "Robert"
        assert c.get("/api/me", headers=BH).json()["name"] == "Robert"

        # sign out every other device, keep this one
        other_device = TestClient(app)  # separate cookie jar = another browser
        assert other_device.post("/api/auth/login", json={"email": "bob@x.io", "password": "NewPass!234"}).status_code == 200
        assert c.post("/api/auth/logout-others", headers=BH).json()["revoked"] == 1
        assert other_device.post("/api/auth/refresh").status_code == 401
        assert c.post("/api/auth/refresh").status_code == 200

        assert c.post("/api/auth/logout").status_code == 200

    print("NATIVE AUTH FLOW OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAILED:", exc)
        sys.exit(1)
