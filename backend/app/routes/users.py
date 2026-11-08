"""In-app user administration (director only): lists, invites, and edits Keycloak users without
leaving DeliveryOps. Backed by app.keycloak_admin; returns 501 if that isn't configured, so the
platform still runs with users managed directly in the Keycloak console instead."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import audit, keycloak_admin as kc
from app.auth import User, require_admin
from app.config import get_settings
from app.db import get_db
from app.validators import Email

router = APIRouter(prefix="/api/admin")
settings = get_settings()
ROLES = ("director", "delivery_manager", "consultant")
_USERNAME = re.compile(r"^[a-z0-9._-]{3,40}$")


def _guard() -> None:
    if not kc.configured():
        raise HTTPException(501, "Keycloak admin API is not configured on this deployment (KEYCLOAK_ADMIN_BASE_URL / KEYCLOAK_ADMIN_CLIENT_SECRET)")


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except kc.AdminUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except kc.AdminAPIError as exc:
        raise HTTPException(exc.status_code if exc.status_code < 500 else 502, exc.detail) from exc


class UserIn(BaseModel):
    username: str
    email: Email
    name: str
    role: str
    clients: list[str] = []
    send_invite: bool = True

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        if not _USERNAME.match(v):
            raise ValueError("username must be 3-40 characters: lowercase letters, digits, dot, underscore, hyphen")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name is required")
        return v.strip()


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    clients: list[str] | None = None
    enabled: bool | None = None

    @field_validator("role")
    @classmethod
    def _role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return v


@router.get("/status")
def status(admin: User = Depends(require_admin)):
    return {"configured": kc.configured()}


@router.get("/users")
def list_users(search: str = "", admin: User = Depends(require_admin)):
    _guard()
    return _wrap(kc.list_users, search=search)


@router.post("/users")
def create_user(body: UserIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _guard()
    if body.role != "director" and not body.clients:
        raise HTTPException(400, "clients is required for delivery_manager and consultant roles")
    user = _wrap(kc.create_user, username=body.username, email=body.email, name=body.name, role=body.role, clients=body.clients, send_invite=body.send_invite)
    audit.log(db, admin.username, "user.created", None, "user", user["id"], username=body.username, role=body.role, clients=body.clients)
    db.commit()
    return user


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _guard()
    if user_id == admin.subject and body.role is not None and body.role != "director":
        raise HTTPException(400, "You cannot remove your own director role")
    if user_id == admin.subject and body.enabled is False:
        raise HTTPException(400, "You cannot disable your own account")
    user = _wrap(kc.update_user, user_id, name=body.name, clients=body.clients, role=body.role, enabled=body.enabled)
    audit.log(db, admin.username, "user.updated", None, "user", user_id, **body.model_dump(exclude_none=True))
    db.commit()
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _guard()
    if user_id == admin.subject:
        raise HTTPException(400, "You cannot delete your own account")
    _wrap(kc.delete_user, user_id)
    audit.log(db, admin.username, "user.deleted", None, "user", user_id)
    db.commit()
    return {"deleted": True}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _guard()
    _wrap(kc.send_password_reset, user_id)
    audit.log(db, admin.username, "user.password_reset_sent", None, "user", user_id)
    db.commit()
    return {"sent": True}
