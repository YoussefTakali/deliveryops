"""Admin: manage roles & their permissions (functionalities), and native user accounts.

`roles.manage` guards role/permission edits; `users.manage` guards user accounts. Mounted only when
AUTH_MODE=native (the keycloak equivalent lives in routes/users.py)."""

import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, permissions as perms, security
from app.auth import User, require_permission
from app.config import get_settings
from app.db import get_db
from app.models import AuthUser, ExternalIdentity, MicrosoftUser, RefreshToken, Role, RolePermission
from app.validators import Email

router = APIRouter(prefix="/api/admin")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,38}$")


def _temp_password() -> str:
    return secrets.token_urlsafe(9)


# ---------------------------------------------------------------- permissions catalog & roles

@router.get("/permissions")
def list_permissions(_: User = Depends(require_permission("roles.manage"))):
    return [{"code": c, "label": label, "category": cat} for c, (label, cat) in perms.PERMISSIONS.items()]


def _role_out(db: Session, role: Role) -> dict:
    locked = role.name in perms.LOCKED_ROLES
    codes = sorted(perms.ALL_PERMISSIONS) if locked else sorted(rp.permission_code for rp in role.permissions if rp.permission_code in perms.ALL_PERMISSIONS)
    # Counted across both account kinds — a deployment runs one auth mode at a time, so only one of
    # these tables ever has rows, but summing both keeps this correct regardless of which.
    native_count = db.scalar(select(func.count()).select_from(AuthUser).where(AuthUser.role_id == role.id)) or 0
    ms_count = db.scalar(select(func.count()).select_from(MicrosoftUser).where(MicrosoftUser.role_id == role.id)) or 0
    return {
        "id": role.id, "name": role.name, "label": role.label, "is_system": role.is_system, "locked": locked,
        "permissions": codes, "user_count": native_count + ms_count,
    }


@router.get("/roles")
def list_roles(user: User = Depends(require_permission("roles.manage")), db: Session = Depends(get_db)):
    roles = db.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)).all()
    return [_role_out(db, r) for r in roles]


class RoleIn(BaseModel):
    name: str
    label: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG.match(v):
            raise ValueError("name must be 2-39 chars: a lowercase letter then letters/digits/underscore")
        return v


@router.post("/roles")
def create_role(body: RoleIn, user: User = Depends(require_permission("roles.manage")), db: Session = Depends(get_db)):
    if db.scalar(select(Role).where(Role.name == body.name)):
        raise HTTPException(409, "Un rôle porte déjà ce nom")
    role = Role(name=body.name, label=body.label or body.name, is_system=False)
    db.add(role)
    db.flush()
    for code in sorted(perms.default_permissions_for("consultant")):  # start from a safe read-only base
        db.add(RolePermission(role_id=role.id, permission_code=code))
    audit.log(db, user.username, "role.created", None, "role", role.id, name=role.name)
    db.commit()
    return _role_out(db, role)


class PermissionsIn(BaseModel):
    permissions: list[str]


@router.put("/roles/{role_id}/permissions")
def set_role_permissions(role_id: str, body: PermissionsIn, user: User = Depends(require_permission("roles.manage")), db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "Rôle introuvable")
    if role.name in perms.LOCKED_ROLES:
        raise HTTPException(400, f"Le rôle « {role.name} » est verrouillé et conserve toutes les permissions")
    unknown = set(body.permissions) - perms.ALL_PERMISSIONS
    if unknown:
        raise HTTPException(400, f"Permissions inconnues : {', '.join(sorted(unknown))}")
    for rp in list(role.permissions):
        db.delete(rp)
    db.flush()
    for code in sorted(set(body.permissions)):
        db.add(RolePermission(role_id=role.id, permission_code=code))
    audit.log(db, user.username, "role.permissions_updated", None, "role", role.id, name=role.name, count=len(set(body.permissions)))
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


class RoleLabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


@router.put("/roles/{role_id}")
def rename_role(role_id: str, body: RoleLabelIn, user: User = Depends(require_permission("roles.manage")), db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "Rôle introuvable")
    role.label = body.label
    audit.log(db, user.username, "role.renamed", None, "role", role.id)
    db.commit()
    return _role_out(db, role)


@router.delete("/roles/{role_id}")
def delete_role(role_id: str, user: User = Depends(require_permission("roles.manage")), db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "Rôle introuvable")
    if role.is_system:
        raise HTTPException(400, "Les rôles système ne peuvent pas être supprimés")
    in_use = (db.scalar(select(func.count()).select_from(AuthUser).where(AuthUser.role_id == role.id)) or 0) \
        + (db.scalar(select(func.count()).select_from(MicrosoftUser).where(MicrosoftUser.role_id == role.id)) or 0)
    if in_use:
        raise HTTPException(400, "Des utilisateurs portent ce rôle ; réaffectez-les d'abord")
    audit.log(db, user.username, "role.deleted", None, "role", role.id, name=role.name)
    db.delete(role)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------- native users

def _user_out(au: AuthUser, providers: list[str] | None = None) -> dict:
    return {
        "id": au.id, "email": au.email, "name": au.name, "role": au.role.name, "role_label": au.role.label,
        "clients": au.clients or [], "active": au.active, "must_change_password": au.must_change_password,
        "last_login_at": au.last_login_at.isoformat() if au.last_login_at else None,
        "locked": _is_locked(au), "mfa_enabled": bool(au.mfa_enabled),
        "sign_in_methods": (["password"] if au.password_hash else []) + sorted(providers or []),
    }


def _password_method_on() -> bool:
    return "password" in get_settings().sign_in_methods


def _is_locked(au: AuthUser) -> bool:
    if not au.locked_until:
        return False
    until = au.locked_until if au.locked_until.tzinfo else au.locked_until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _role_by_name(db: Session, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if not role:
        raise HTTPException(400, f"Rôle inconnu : {name}")
    return role


@router.get("/users")
def list_users(search: str = "", user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    stmt = select(AuthUser).order_by(AuthUser.created_at.desc())
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(AuthUser.email).like(like) | func.lower(AuthUser.name).like(like))
    providers: dict[str, list[str]] = {}
    for user_id, provider in db.execute(select(ExternalIdentity.user_id, ExternalIdentity.provider)):
        providers.setdefault(user_id, []).append(provider)
    return [_user_out(u, providers.get(u.id)) for u in db.scalars(stmt)]


class UserIn(BaseModel):
    email: Email
    name: str = Field(min_length=1)
    role: str
    clients: list[str] = []
    password: str | None = Field(default=None, min_length=8, max_length=200)


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    if db.scalar(select(AuthUser).where(func.lower(AuthUser.email) == email)):
        raise HTTPException(409, "Un utilisateur avec cet e-mail existe déjà")
    role = _role_by_name(db, body.role)
    if role.name not in ("admin", "director") and not body.clients:
        raise HTTPException(400, "Sélectionnez au moins un client pour ce rôle")
    # Without the password method, the person signs in with Microsoft/Google using this same e-mail.
    temp = (body.password or _temp_password()) if _password_method_on() else None
    au = AuthUser(
        email=email, name=body.name.strip(), password_hash=security.hash_password(temp) if temp else "", role_id=role.id,
        clients=["*"] if role.name in ("admin", "director") else body.clients,
        must_change_password=bool(temp) and body.password is None, created_by=user.username,
    )
    db.add(au)
    audit.log(db, user.username, "user.created", None, "user", au.id, email=email, role=role.name)
    db.commit()
    out = _user_out(au)
    if temp and body.password is None:
        out["temporary_password"] = temp  # shown once so the admin can hand it over
    return out


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    clients: list[str] | None = None
    active: bool | None = None


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    au = db.get(AuthUser, user_id)
    if not au:
        raise HTTPException(404, "Utilisateur introuvable")
    if au.id == user.subject and body.active is False:
        raise HTTPException(400, "Vous ne pouvez pas désactiver votre propre compte")
    if au.id == user.subject and body.role and body.role != au.role.name:
        raise HTTPException(400, "Vous ne pouvez pas changer votre propre rôle")
    if body.name is not None:
        au.name = body.name.strip()
    if body.role is not None:
        role = _role_by_name(db, body.role)
        au.role_id = role.id
        if role.name in ("admin", "director"):
            au.clients = ["*"]
    if body.clients is not None and au.role.name not in ("admin", "director"):
        au.clients = body.clients
    if body.active is not None:
        au.active = body.active
        if not body.active:  # disabling: kill their sessions immediately
            for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == au.id, RefreshToken.revoked.is_(False))):
                rt.revoked = True
    audit.log(db, user.username, "user.updated", None, "user", au.id, **body.model_dump(exclude_none=True))
    db.commit()
    db.refresh(au)
    return _user_out(au)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    if not _password_method_on():
        raise HTTPException(400, "La connexion par mot de passe est désactivée sur cette plateforme")
    au = db.get(AuthUser, user_id)
    if not au:
        raise HTTPException(404, "Utilisateur introuvable")
    temp = _temp_password()
    au.password_hash = security.hash_password(temp)
    au.must_change_password = True
    au.failed_logins, au.locked_until = 0, None
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == au.id, RefreshToken.revoked.is_(False))):
        rt.revoked = True
    audit.log(db, user.username, "user.password_reset", None, "user", au.id)
    db.commit()
    return {"temporary_password": temp}


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: str, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    """Lift a lock caused by repeated failed sign-ins."""
    au = db.get(AuthUser, user_id)
    if not au:
        raise HTTPException(404, "Utilisateur introuvable")
    au.failed_logins, au.locked_until = 0, None
    audit.log(db, user.username, "user.unlocked", None, "user", au.id)
    db.commit()
    return _user_out(au)


@router.post("/users/{user_id}/reset-mfa")
def reset_mfa(user_id: str, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    """Turn off someone's two-factor authentication (lost phone and recovery codes); they can set it up again."""
    au = db.get(AuthUser, user_id)
    if not au:
        raise HTTPException(404, "Utilisateur introuvable")
    if au.id == user.subject:
        raise HTTPException(400, "Désactivez votre propre double authentification depuis Mon compte.")
    au.mfa_enabled, au.mfa_secret_enc, au.mfa_recovery, au.mfa_last_step = False, "", None, 0
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == au.id, RefreshToken.revoked.is_(False))):
        rt.revoked = True
    audit.log(db, user.username, "user.mfa_reset", None, "user", au.id)
    db.commit()
    return _user_out(au)


@router.delete("/users/{user_id}")
def delete_user(user_id: str, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    au = db.get(AuthUser, user_id)
    if not au:
        raise HTTPException(404, "Utilisateur introuvable")
    if au.id == user.subject:
        raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte")
    audit.log(db, user.username, "user.deleted", None, "user", au.id, email=au.email)
    db.delete(au)
    db.commit()
    return {"deleted": True}
