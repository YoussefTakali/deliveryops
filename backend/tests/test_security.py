"""Unit tests for the auth primitives: password hashing, access tokens, and RBAC resolution."""

import time

import jwt
import pytest

from app import permissions as perms
from app import security


def test_password_hash_roundtrip_and_reject_wrong():
    h = security.hash_password("Correct!horse")
    assert h != "Correct!horse"
    assert security.verify_password("Correct!horse", h)
    assert not security.verify_password("wrong", h)


def test_verify_password_handles_garbage_hash():
    assert security.verify_password("x", "not-a-valid-hash") is False


def test_access_token_roundtrip():
    token, ttl = security.create_access_token("user-123")
    assert ttl == 30 * 60
    assert security.decode_access_token(token) == "user-123"


def test_access_token_rejects_expired():
    now = int(time.time())
    token = jwt.encode({"sub": "u", "iat": now - 100, "exp": now - 10, "typ": "access"}, security.settings.auth_secret, algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(token)


def test_access_token_rejects_wrong_secret():
    forged = jwt.encode({"sub": "u", "exp": int(time.time()) + 100, "typ": "access"}, "other-secret", algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(forged)


def test_access_token_rejects_wrong_type():
    token = jwt.encode({"sub": "u", "exp": int(time.time()) + 100, "typ": "refresh"}, security.settings.auth_secret, algorithm="HS256")
    with pytest.raises(security.TokenError):
        security.decode_access_token(token)


def test_refresh_token_hash_is_deterministic_and_opaque():
    raw, h = security.new_refresh_token()
    assert h == security.hash_refresh_token(raw)
    assert raw != h and len(h) == 64  # sha256 hex


def test_permission_presets_are_within_catalog():
    for role, codes in perms.DEFAULT_ROLE_PERMISSIONS.items():
        assert codes <= perms.ALL_PERMISSIONS, f"{role} has unknown permission codes"
    assert perms.DEFAULT_ROLE_PERMISSIONS["admin"] == set(perms.ALL_PERMISSIONS)
    assert "roles.manage" not in perms.DEFAULT_ROLE_PERMISSIONS["director"]
    assert "recommendation.decide" not in perms.DEFAULT_ROLE_PERMISSIONS["consultant"]


def test_rbac_role_permissions_locks_admin(client):
    # `client` gives us a seeded DB (dev app); roles are seeded on startup. The admin role must
    # always resolve to the full catalog regardless of stored rows.
    from sqlalchemy import select

    from app import rbac
    from app.db import SessionLocal
    from app.models import Role, RolePermission

    with SessionLocal() as db:
        admin = db.scalar(select(Role).where(Role.name == "admin"))
        assert admin is not None
        # strip its stored rows — resolution must still return everything (locked)
        for rp in list(admin.permissions):
            db.delete(rp)
        db.commit()
        db.refresh(admin)
        assert rbac.role_permissions(db, admin) == set(perms.ALL_PERMISSIONS)

        consultant = db.scalar(select(Role).where(Role.name == "consultant"))
        assert rbac.role_permissions(db, consultant) == perms.default_permissions_for("consultant")
