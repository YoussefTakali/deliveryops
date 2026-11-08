"""The signed-in user's own account (/api/account), in every auth mode: profile, access scope,
sessions, AI consumption and where to change the password. Native accounts manage their password
and name here; Microsoft / Keycloak accounts are managed by that identity provider, so this points
the user to it instead."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, governance, oidc, secret_box, security, totp
from app.auth import User, current_user
from app.config import get_settings
from app.db import get_db
from app.models import AuthUser, ExternalIdentity, MicrosoftUser, RefreshToken, Role, UsageLedger
from app.routes.auth import verify_second_factor

router = APIRouter(prefix="/api/account")
settings = get_settings()

MICROSOFT_PASSWORD_URL = "https://mysignins.microsoft.com/security-info/password/change"
MICROSOFT_ACCOUNT_URL = "https://myaccount.microsoft.com/"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _keycloak_account_url() -> str:
    issuer = (settings.keycloak_public_url or settings.keycloak_issuer).rstrip("/")
    return f"{issuer}/account"


def _identity(db: Session, user: User) -> dict:
    """Mode-specific facts: account dates, sessions, and who manages the password."""
    mode = settings.auth_mode
    out: dict = {"managed_by": mode, "can_edit_name": False, "password_url": None, "account_url": None,
                 "created_at": None, "last_login_at": None, "active_sessions": None,
                 "mfa_enabled": None, "recovery_codes_left": None, "has_password": None}
    if mode == "native":
        au = db.get(AuthUser, user.subject)
        if au:
            now = datetime.now(timezone.utc)
            out.update(can_edit_name=True, created_at=_iso(au.created_at), last_login_at=_iso(au.last_login_at),
                       active_sessions=db.scalar(select(func.count()).select_from(RefreshToken).where(
                           RefreshToken.user_id == au.id, RefreshToken.revoked.is_(False), RefreshToken.expires_at > now)) or 0,
                       mfa_enabled=au.mfa_enabled, recovery_codes_left=len(au.mfa_recovery or []) if au.mfa_enabled else None,
                       has_password=bool(au.password_hash))
    elif mode == "microsoft":
        mu = db.scalar(select(MicrosoftUser).where(func.lower(MicrosoftUser.email) == user.username.lower()))
        out.update(password_url=MICROSOFT_PASSWORD_URL, account_url=MICROSOFT_ACCOUNT_URL,
                   created_at=_iso(mu.created_at) if mu else None, last_login_at=_iso(mu.last_login_at) if mu else None)
    elif mode == "keycloak":
        url = _keycloak_account_url()
        out.update(password_url=url, account_url=url)
    return out


def _ai(db: Session, username: str) -> dict:
    rules = governance.rules_for(username)
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    week = db.scalar(select(func.coalesce(func.sum(UsageLedger.tokens), 0)).where(
        UsageLedger.username == username, UsageLedger.ts >= since)) or 0
    return {
        "enabled": rules["ai_enabled"] and not rules["ai_blocked"],
        "org_enabled": rules["ai_enabled"], "blocked": rules["ai_blocked"],
        "used_today": governance.usage_today(username=username), "used_7d": int(week),
        "daily_quota": rules["quota"],  # 0 = unlimited
    }


@router.get("")
def get_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    role = db.scalar(select(Role).where(Role.name == user.role))
    return {
        "username": user.username, "name": user.name, "email": user.email or user.username,
        "role": user.role, "role_label": role.label if role and role.label else user.role,
        "clients": user.clients, "permissions": sorted(user.permissions),
        "must_change_password": user.extra.get("must_change_password", False),
        "auth_mode": settings.auth_mode,
        **_identity(db, user),
        "ai": _ai(db, user.username),
    }


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.put("")
def update_account(body: AccountIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if settings.auth_mode != "native":
        raise HTTPException(400, "Votre nom est géré par votre fournisseur d'identité (Microsoft / Keycloak).")
    au = db.get(AuthUser, user.subject)
    if not au:
        raise HTTPException(404, "Compte introuvable")
    au.name = body.name.strip()
    if not au.name:
        raise HTTPException(400, "Le nom ne peut pas être vide")
    audit.log(db, user.username, "account.updated", None, "user", au.id, name=au.name)
    db.commit()
    return {"name": au.name}


# ---------------------------------------------------------------- sign-in methods (native accounts)

@router.get("/sign-in-methods")
def sign_in_methods(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """How this account can sign in: its password and linked Microsoft/Google identities, plus the
    methods the organisation offers that aren't linked yet."""
    au = _native_account(db, user)
    linked = db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id == au.id).order_by(ExternalIdentity.created_at)).all()
    return {
        "password": {"available": "password" in settings.sign_in_methods, "set": bool(au.password_hash)},
        "identities": [{"id": i.id, "provider": i.provider, "label": oidc.LABELS.get(i.provider, i.provider), "email": i.email,
                        "created_at": _iso(i.created_at), "last_used_at": _iso(i.last_used_at)} for i in linked],
        "can_link": [{"provider": p, "label": oidc.LABELS[p]} for p in oidc.external_providers()
                     if not any(i.provider == p for i in linked)],
    }


@router.delete("/sign-in-methods/{identity_id}")
def unlink_identity(identity_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    au = _native_account(db, user)
    ident = db.get(ExternalIdentity, identity_id)
    if not ident or ident.user_id != au.id:
        raise HTTPException(404, "Méthode de connexion introuvable")
    others = db.scalar(select(func.count()).select_from(ExternalIdentity).where(
        ExternalIdentity.user_id == au.id, ExternalIdentity.id != ident.id, ExternalIdentity.provider.in_(oidc.external_providers()))) or 0
    password_usable = bool(au.password_hash) and "password" in settings.sign_in_methods
    if not others and not password_usable:
        raise HTTPException(400, "C'est votre seule façon de vous connecter : liez d'abord une autre méthode.")
    audit.log(db, user.username, "auth.method_unlinked", None, "user", au.id, method=ident.provider)
    db.delete(ident)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- two-factor authentication (native accounts)

def _native_account(db: Session, user: User) -> AuthUser:
    if settings.auth_mode != "native":
        raise HTTPException(400, "La double authentification est gérée par votre fournisseur d'identité (Microsoft / Keycloak).")
    au = db.get(AuthUser, user.subject)
    if not au:
        raise HTTPException(404, "Compte introuvable")
    return au


class PasswordConfirmIn(BaseModel):
    password: str = Field(max_length=200)


@router.post("/mfa/setup")
def mfa_setup(body: PasswordConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Step 1: a new secret (not active yet) and its QR code. Re-asks the password: an unattended
    open session must not be enough to take over the second factor."""
    au = _native_account(db, user)
    if au.mfa_enabled:
        raise HTTPException(400, "La double authentification est déjà activée.")
    if not security.verify_password(body.password, au.password_hash):
        raise HTTPException(400, "Mot de passe incorrect")
    secret = totp.new_secret()
    au.mfa_secret_enc, au.mfa_last_step = secret_box.encrypt(secret), 0
    db.commit()
    uri = totp.provisioning_uri(secret, au.email)
    return {"secret": secret, "otpauth_uri": uri, "qr": totp.qr_svg(uri)}


class CodeIn(BaseModel):
    code: str = Field(max_length=40)


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Step 2: the first code from the app proves it is set up; returns single-use recovery codes, once."""
    au = _native_account(db, user)
    secret = secret_box.decrypt(au.mfa_secret_enc)
    if au.mfa_enabled or not secret:
        raise HTTPException(400, "Recommencez la configuration de la double authentification.")
    step = totp.verify(secret, body.code)
    if step is None:
        raise HTTPException(400, "Code incorrect : vérifiez l'heure de votre téléphone et réessayez.")
    codes = totp.new_recovery_codes()
    au.mfa_enabled, au.mfa_last_step, au.mfa_recovery = True, step, [totp.hash_recovery(c) for c in codes]
    audit.log(db, user.username, "account.mfa_enabled", None, "user", au.id)
    db.commit()
    return {"enabled": True, "recovery_codes": codes}


class MfaDisableIn(BaseModel):
    password: str = Field(max_length=200)
    code: str = Field(max_length=40)


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisableIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    au = _native_account(db, user)
    if not au.mfa_enabled:
        return {"enabled": False}
    if not security.verify_password(body.password, au.password_hash) or not verify_second_factor(db, au, body.code):
        raise HTTPException(400, "Mot de passe ou code incorrect")
    au.mfa_enabled, au.mfa_secret_enc, au.mfa_recovery, au.mfa_last_step = False, "", None, 0
    audit.log(db, user.username, "account.mfa_disabled", None, "user", au.id)
    db.commit()
    return {"enabled": False}


@router.post("/mfa/recovery-codes")
def mfa_new_recovery_codes(body: CodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Replace the recovery codes (the old ones stop working)."""
    au = _native_account(db, user)
    if not au.mfa_enabled or not verify_second_factor(db, au, body.code):
        raise HTTPException(400, "Code incorrect")
    codes = totp.new_recovery_codes()
    au.mfa_recovery = [totp.hash_recovery(c) for c in codes]
    audit.log(db, user.username, "account.mfa_recovery_regenerated", None, "user", au.id)
    db.commit()
    return {"recovery_codes": codes}
