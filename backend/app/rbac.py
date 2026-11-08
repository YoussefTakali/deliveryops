"""RBAC seeding and runtime permission resolution for native auth."""

import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import permissions as perms
from app.models import AuditLog, AuthUser, MicrosoftUser, Notification, Role, RolePermission
from app.security import hash_password

log = logging.getLogger(__name__)


def ensure_roles(db: Session) -> None:
    """Create the system roles once and seed their default permissions. Never overwrites permissions
    an admin has already customised — a role is only seeded the first time it's created."""
    existing = {r.name: r for r in db.scalars(select(Role))}
    for name in perms.SYSTEM_ROLES:
        role = existing.get(name)
        if role is None:
            role = Role(name=name, label=perms.ROLE_LABELS.get(name, name), is_system=True)
            db.add(role)
            db.flush()
            for code in sorted(perms.default_permissions_for(name)):
                db.add(RolePermission(role_id=role.id, permission_code=code))
    db.flush()
    _backfill_permissions(db)
    db.commit()


def _backfill_permissions(db: Session) -> None:
    """Grant permissions added after a deployment's roles were first seeded (see
    perms.BACKFILLED_PERMISSIONS) to the system roles whose preset includes them — exactly once per
    permission, tracked by an audit entry, so an admin's later removal is respected."""
    for code in perms.BACKFILLED_PERMISSIONS:
        done = db.scalar(select(AuditLog.id).where(AuditLog.action == "rbac.permission_backfill", AuditLog.entity_id == code).limit(1))
        if done:
            continue
        granted = []
        holders = set(db.scalars(select(RolePermission.role_id).where(RolePermission.permission_code == code)))
        for role in db.scalars(select(Role).where(Role.name.in_(perms.SYSTEM_ROLES))):
            if code in perms.default_permissions_for(role.name) and role.id not in holders:
                db.add(RolePermission(role_id=role.id, permission_code=code))
                granted.append(role.name)
        db.add(AuditLog(actor="system", action="rbac.permission_backfill", entity="permission", entity_id=code, details={"roles": granted}))
        if granted:
            log.info("Granted new permission %s to roles %s", code, ", ".join(granted))


def ensure_admin(db: Session) -> tuple[str, str] | None:
    """Create the first admin from settings if there are no users yet. Returns (email, password)
    only when a password had to be generated (so it can be logged once)."""
    from app.config import get_settings

    settings = get_settings()
    if db.scalar(select(func.count()).select_from(AuthUser)):
        return None
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    if admin_role is None:
        ensure_roles(db)
        admin_role = db.scalar(select(Role).where(Role.name == "admin"))

    generated = None
    password = settings.initial_admin_password
    if not password:
        password = generated = secrets.token_urlsafe(12)
    db.add(AuthUser(
        email=settings.initial_admin_email.lower().strip(), name=settings.initial_admin_name,
        password_hash=hash_password(password), role_id=admin_role.id, clients=["*"], active=True,
        must_change_password=bool(generated), created_by="system",
    ))
    db.commit()
    if generated:
        log.warning("Seeded initial admin %s with a generated password: %s  (change it on first login)",
                    settings.initial_admin_email, generated)
    return (settings.initial_admin_email, password) if generated else None


def ensure_microsoft_user(db: Session, email: str, subject: str, name: str) -> MicrosoftUser:
    """Resolve (creating if needed) the MicrosoftUser row for a signed-in Microsoft account.

    Security-critical default: the very first person ever to sign in via Microsoft becomes `admin`
    (bootstrap — otherwise nobody could ever assign roles); everyone after that starts at
    `unassigned` (zero permissions) until an admin grants them a real role in the Utilisateurs page.
    A returning user's role is never changed here — only their profile snapshot (name) and
    last-login timestamp are refreshed.
    """
    email = email.lower().strip()
    mu = db.scalar(select(MicrosoftUser).where(MicrosoftUser.email == email))
    if mu is not None:
        mu.name = name or mu.name
        mu.subject = subject or mu.subject
        mu.last_login_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(mu)
        return mu

    is_first_ever = db.scalar(select(func.count()).select_from(MicrosoftUser)) == 0
    role_name = "admin" if is_first_ever else "unassigned"
    role = db.scalar(select(Role).where(Role.name == role_name))
    if role is None:  # defensive: ensure_roles() always seeds these, but never assume
        ensure_roles(db)
        role = db.scalar(select(Role).where(Role.name == role_name))
    mu = MicrosoftUser(
        email=email, subject=subject, name=name, role_id=role.id,
        clients=["*"] if role_name == "admin" else [], last_login_at=datetime.now(timezone.utc),
    )
    db.add(mu)
    if is_first_ever:
        log.warning("First Microsoft sign-in (%s) bootstrapped as admin.", email)
    else:
        # Nobody is otherwise told a new account is waiting for a role — raise a global (no
        # perimeter) notification so admins see it in the bell without having to remember to check
        # the Utilisateurs page. Flush first so mu.id is populated for the deep link.
        db.flush()
        _pending_notification(db, mu.id, name, email, "Microsoft", "/admin/microsoft-users")
    db.commit()
    db.refresh(mu)
    return mu


def _pending_notification(db: Session, account_id: str, name: str, email: str, method: str, link: str) -> None:
    db.add(Notification(
        perimeter_id=None, kind="user_pending_approval", severity="medium",
        title=f"Nouvel utilisateur en attente : {name or email}",
        body=f"{name or email} ({email}) s'est connecté via {method} et attend l'attribution d'un rôle.",
        link=link, fingerprint=f"user_pending_approval:{account_id}",
    ))


def notify_pending_user(db: Session, au: AuthUser, method: str) -> None:
    """Tell user admins a native account created by a Microsoft/Google sign-in is waiting for a role."""
    _pending_notification(db, au.id, au.name, au.email, method, "/admin/accounts")


def import_microsoft_users(db: Session) -> int:
    """Switching a deployment from AUTH_MODE=microsoft to native (with Microsoft sign-in): carry every
    MicrosoftUser over as a native account with its role, clients and Microsoft identity, so nobody
    loses access. Idempotent — runs at startup, does nothing once everyone is carried over."""
    from app.models import ExternalIdentity

    imported = 0
    for mu in db.scalars(select(MicrosoftUser)):
        if mu.subject and db.scalar(select(ExternalIdentity.id).where(
                ExternalIdentity.provider == "microsoft", ExternalIdentity.subject == mu.subject)):
            continue
        au = db.scalar(select(AuthUser).where(func.lower(AuthUser.email) == mu.email.lower()))
        if au is None:
            au = AuthUser(email=mu.email.lower(), name=mu.name, password_hash="", role_id=mu.role_id, clients=list(mu.clients or []),
                          active=mu.active, created_by="import:microsoft", created_at=mu.created_at, last_login_at=mu.last_login_at)
            db.add(au)
            db.flush()
        if mu.subject:
            db.add(ExternalIdentity(user_id=au.id, provider="microsoft", subject=mu.subject, email=mu.email.lower()))
        imported += 1
    if imported:
        db.commit()
        log.warning("Imported %d Microsoft account(s) as native accounts with Microsoft sign-in.", imported)
    return imported


def role_permissions(db: Session, role: Role) -> set[str]:
    """Effective permissions for a role. The `admin` role is always the full catalog (locked), so an
    admin can never accidentally revoke their own ability to administer."""
    if role.name in perms.LOCKED_ROLES:
        return set(perms.ALL_PERMISSIONS)
    return {rp.permission_code for rp in role.permissions} & perms.ALL_PERMISSIONS


def seed(db: Session) -> None:
    ensure_roles(db)
    ensure_admin(db)
