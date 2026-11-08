from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import audit, watch
from app.api import row, visible_perimeters
from app.auth import User, current_user, require_decider
from app.db import get_db
from app.models import Notification

router = APIRouter(prefix="/api")


def _visible(db: Session, user: User) -> dict:
    return {p.id: p for p in visible_perimeters(db, user)}


def _scope_filter(user: User, perimeters: dict):
    """A notification is in scope if it belongs to one of the user's visible perimeters, or if it's
    global (no perimeter) and addressed to this person (recipient), to a permission they hold
    (audience), or — neither set, e.g. `user_pending_approval` — to user admins."""
    is_global = Notification.perimeter_id.is_(None)
    conditions = [
        Notification.perimeter_id.in_(perimeters),
        and_(is_global, Notification.recipient == user.username),
        and_(is_global, Notification.recipient == "", Notification.audience.in_(sorted(user.permissions))),
    ]
    if user.has("users.manage"):
        conditions.append(and_(is_global, Notification.recipient == "", Notification.audience == ""))
    return or_(*conditions)


def _can_see(user: User, n: Notification, perimeters: dict) -> bool:
    if n.perimeter_id is not None:
        return n.perimeter_id in perimeters
    if n.recipient:
        return n.recipient == user.username
    return user.has(n.audience) if n.audience else user.has("users.manage")


@router.get("/notifications")
def list_notifications(unread: bool = False, limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
    perimeters = _visible(db, user)
    stmt = select(Notification).where(_scope_filter(user, perimeters)).order_by(Notification.ts.desc()).limit(limit)
    items = []
    for n in db.scalars(stmt):
        read = user.username in (n.read_by or [])
        if unread and read:
            continue
        p = perimeters.get(n.perimeter_id) if n.perimeter_id else None
        items.append(row(n, "read_by") | {"read": read, "perimeter": p.name if p else "", "client": p.client.name if p else ""})
    return {"unread": sum(1 for i in items if not i["read"]) if not unread else len(items), "items": items}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or not _can_see(user, n, _visible(db, user)):
        raise HTTPException(404)
    if user.username not in (n.read_by or []):
        n.read_by = [*(n.read_by or []), user.username]
        db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    perimeters = _visible(db, user)
    for n in db.scalars(select(Notification).where(_scope_filter(user, perimeters))):
        if user.username not in (n.read_by or []):
            n.read_by = [*(n.read_by or []), user.username]
    db.commit()
    return {"ok": True}


@router.post("/watch/run")
def run_watch(user: User = Depends(require_decider), db: Session = Depends(get_db)):
    created = 0
    for p in visible_perimeters(db, user):
        created += len(watch.scan(db, p))
    audit.log(db, user.username, "watch.triggered", None, "watch", "", created=created)
    db.commit()
    return {"created": created}
