"""Admin: assign roles to people who have signed in via Microsoft (AUTH_MODE=microsoft).

Distinct from routes/rbac_admin.py's native user CRUD: a Microsoft user isn't created by an admin
and has no password — they self-provision by signing in, an admin only assigns their role/clients
afterward (or disables them). The role/permission catalog endpoints in rbac_admin.py are reused
as-is (see app.main, which mounts that router for this auth mode too)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth import User, require_permission
from app.db import get_db
from app.models import MicrosoftUser, Role

router = APIRouter(prefix="/api/admin")


def _user_out(mu: MicrosoftUser) -> dict:
    return {
        "id": mu.id, "email": mu.email, "name": mu.name, "role": mu.role.name, "role_label": mu.role.label,
        "clients": mu.clients or [], "active": mu.active,
        "created_at": mu.created_at.isoformat() if mu.created_at else None,
        "last_login_at": mu.last_login_at.isoformat() if mu.last_login_at else None,
    }


def _role_by_name(db: Session, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if not role:
        raise HTTPException(400, f"Rôle inconnu : {name}")
    return role


@router.get("/microsoft-users")
def list_microsoft_users(search: str = "", user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    stmt = select(MicrosoftUser).order_by(MicrosoftUser.created_at.desc())
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(func.lower(MicrosoftUser.email).like(like) | func.lower(MicrosoftUser.name).like(like))
    return [_user_out(u) for u in db.scalars(stmt)]


class MicrosoftUserUpdate(BaseModel):
    role: str | None = None
    clients: list[str] | None = None
    active: bool | None = None


@router.put("/microsoft-users/{user_id}")
def update_microsoft_user(user_id: str, body: MicrosoftUserUpdate, user: User = Depends(require_permission("users.manage")), db: Session = Depends(get_db)):
    mu = db.get(MicrosoftUser, user_id)
    if not mu:
        raise HTTPException(404, "Utilisateur introuvable")
    if mu.id == user.subject and body.active is False:
        raise HTTPException(400, "Vous ne pouvez pas désactiver votre propre compte")
    if mu.id == user.subject and body.role and body.role != mu.role.name:
        raise HTTPException(400, "Vous ne pouvez pas changer votre propre rôle")
    if body.role is not None:
        role = _role_by_name(db, body.role)
        mu.role_id = role.id
        if role.name in ("admin", "director"):
            mu.clients = ["*"]
    if body.clients is not None and mu.role.name not in ("admin", "director"):
        mu.clients = body.clients
    if body.active is not None:
        mu.active = body.active
    audit.log(db, user.username, "microsoft_user.updated", None, "microsoft_user", mu.id, **body.model_dump(exclude_none=True))
    db.commit()
    db.refresh(mu)
    return _user_out(mu)
