"""Native-account security end to end, in its own process (AUTH_MODE=native before any app import):
internal-domain e-mails, lockout, forgot/reset password, two-factor (TOTP + recovery codes), and
per-device sessions. Exits 0 and prints the marker on success."""

import os
import re
import sys
import tempfile
import time

tmp = tempfile.mkdtemp()
os.environ.update(
    AUTH_MODE="native", DATABASE_URL=f"sqlite:///{tmp}/s.db", AUTH_SECRET="test-secret-security-0123456789",
    INITIAL_ADMIN_EMAIL="admin@deliveryops.local", INITIAL_ADMIN_PASSWORD="Admin!2345",
    LLM_MODE="mock", WATCH_ENABLED="false", SEED_DEMO_DATA="false", ENVIRONMENT="development",
    AUTH_LOCKOUT_ATTEMPTS="3", EMBEDDING_PROVIDER="hash", EMBEDDING_DIM="384",
)

from fastapi.testclient import TestClient  # noqa: E402

from app import mailer, totp  # noqa: E402
from app.main import app  # noqa: E402

sent: list[dict] = []
mailer.configured = lambda: True
mailer.send = lambda to, subject, text: sent.append({"to": to, "text": text})


STEP0 = int(time.time() // totp.STEP)  # fixed reference, so a 30 s boundary mid-test can't flip results


def code_for(secret: str, offset: int = 0) -> str:
    return totp._code(secret, STEP0 + offset)


def main() -> None:
    with TestClient(app) as c:
        # 1. the default first-boot admin (internal ".local" domain) can sign in
        r = c.post("/api/auth/login", json={"email": "admin@deliveryops.local", "password": "Admin!2345"})
        assert r.status_code == 200, r.text
        A = {"Authorization": f"Bearer {r.json()['access_token']}"}
        u = c.post("/api/admin/users", json={"email": "eve@corp.lan", "name": "Eve", "role": "director", "password": "Eve!23456"}, headers=A)
        assert u.status_code == 200, u.text
        eve_id = u.json()["id"]

        # 2. lockout after repeated failures — even the right password is refused while locked
        for _ in range(3):
            assert c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "wrong"}).status_code == 401
        locked = c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Eve!23456"})
        assert locked.status_code == 429 and "verrouillé" in locked.json()["detail"], locked.text
        users = {x["id"]: x for x in c.get("/api/admin/users", headers=A).json()}
        assert users[eve_id]["locked"] is True
        assert c.post(f"/api/admin/users/{eve_id}/unlock", headers=A).json()["locked"] is False
        assert c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Eve!23456"}).status_code == 200

        # 3. forgot password: same answer for unknown accounts, one-time link for real ones
        unknown = c.post("/api/auth/forgot-password", json={"email": "nobody@corp.lan"}).json()
        known = c.post("/api/auth/forgot-password", json={"email": "eve@corp.lan"}).json()
        assert unknown == known and len(sent) == 1 and sent[0]["to"] == "eve@corp.lan"
        c.post("/api/auth/forgot-password", json={"email": "eve@corp.lan"})
        assert len(sent) == 1                                          # no mailbox flooding
        token = re.search(r"token=([\w-]+)", sent[0]["text"]).group(1)
        assert c.post("/api/auth/reset-password", json={"token": "bogus", "new_password": "Whatever!1"}).status_code == 400
        reset = c.post("/api/auth/reset-password", json={"token": token, "new_password": "Reset!2345"})
        assert reset.status_code == 200 and "access_token" in reset.json(), reset.text
        assert c.post("/api/auth/reset-password", json={"token": token, "new_password": "Again!2345"}).status_code == 400  # single use
        assert c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Eve!23456"}).status_code == 401
        E = {"Authorization": f"Bearer {reset.json()['access_token']}"}

        # 4. two-factor: setup needs the password, enabling needs a code, login then needs a code
        assert c.post("/api/account/mfa/setup", json={"password": "nope"}, headers=E).status_code == 400
        setup = c.post("/api/account/mfa/setup", json={"password": "Reset!2345"}, headers=E).json()
        assert setup["qr"].startswith("data:image/svg+xml") and setup["otpauth_uri"].startswith("otpauth://totp/")
        secret = setup["secret"]
        assert c.post("/api/account/mfa/enable", json={"code": "000000"}, headers=E).status_code == 400
        enabled = c.post("/api/account/mfa/enable", json={"code": code_for(secret)}, headers=E).json()
        recovery = enabled["recovery_codes"]
        assert len(recovery) == 10 and c.get("/api/account", headers=E).json()["mfa_enabled"] is True

        step1 = c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Reset!2345"}).json()
        assert step1.get("mfa_required") is True and "access_token" not in step1
        assert c.post("/api/auth/mfa", json={"mfa_token": step1["mfa_token"], "code": code_for(secret)}).status_code == 401  # replayed code
        ok = c.post("/api/auth/mfa", json={"mfa_token": step1["mfa_token"], "code": code_for(secret, +1)})
        assert ok.status_code == 200, ok.text
        step2 = c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Reset!2345"}).json()
        assert c.post("/api/auth/mfa", json={"mfa_token": step2["mfa_token"], "code": recovery[0]}).status_code == 200
        step3 = c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Reset!2345"}).json()
        assert c.post("/api/auth/mfa", json={"mfa_token": step3["mfa_token"], "code": recovery[0]}).status_code == 401  # used up
        assert c.post("/api/auth/mfa", json={"mfa_token": "forged", "code": "123456"}).status_code == 401

        # a reset link must not bypass the second factor
        c.post("/api/auth/forgot-password", json={"email": "eve@corp.lan"})  # rate limit window: drop the pending link first
        from app.db import SessionLocal
        from app.models import PasswordResetToken
        with SessionLocal() as db:
            db.query(PasswordResetToken).delete()
            db.commit()
        c.post("/api/auth/forgot-password", json={"email": "eve@corp.lan"})
        token2 = re.search(r"token=([\w-]+)", sent[-1]["text"]).group(1)
        assert c.post("/api/auth/reset-password", json={"token": token2, "new_password": "Third!2345"}).json().get("mfa_required") is True

        # 5. devices: this browser is flagged, another device can be signed out individually
        other = TestClient(app)
        s = other.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Third!2345"}).json()
        other.post("/api/auth/mfa", json={"mfa_token": s["mfa_token"], "code": recovery[1]})
        mine = c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Third!2345"}).json()
        me = c.post("/api/auth/mfa", json={"mfa_token": mine["mfa_token"], "code": recovery[2]}).json()
        H = {"Authorization": f"Bearer {me['access_token']}"}
        sessions = c.get("/api/auth/sessions", headers=H).json()
        assert sum(1 for x in sessions if x["current"]) == 1 and len(sessions) >= 2
        target = next(x for x in sessions if not x["current"])
        assert c.delete(f"/api/auth/sessions/{target['id']}", headers=H).status_code == 200
        assert c.delete(f"/api/auth/sessions/{target['id'] + 999}", headers=H).status_code == 404

        # 6. admin resets a lost second factor; the owner turns it off themselves with password + code
        assert c.post(f"/api/admin/users/{eve_id}/reset-mfa", headers=A).json()["mfa_enabled"] is False
        assert "access_token" in c.post("/api/auth/login", json={"email": "eve@corp.lan", "password": "Third!2345"}).json()

    print("NATIVE SECURITY FLOW OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        import traceback
        traceback.print_exc()
        print("FAILED:", exc)
        sys.exit(1)
