"""Native authentication: email/password login (with lockout and optional two-factor), token
refresh, logout, self password change, "forgot password" e-mail links, and per-device sessions.

Access token → response body (the SPA keeps it in memory). Refresh token → httpOnly cookie, rotated
on each /refresh. Only active when AUTH_MODE=native; the other modes authenticate via their IdP."""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, mailer, oidc, rbac, secret_box, security, totp
from app.auth import User, current_user, load_native_user
from app.config import get_settings
from app.db import get_db
from app.models import AuthUser, ExternalIdentity, PasswordResetToken, RefreshToken, Role
from app.validators import Email

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth")
settings = get_settings()

REFRESH_COOKIE = "dm_refresh"
COOKIE_PATH = "/api/auth"
_BAD_CREDENTIALS = "Identifiants invalides"


def _native_only() -> None:
    if settings.auth_mode != "native":
        raise HTTPException(400, "Native authentication is disabled (AUTH_MODE is not 'native')")


def _password_method() -> None:
    _native_only()
    if "password" not in settings.sign_in_methods:
        raise HTTPException(400, "La connexion par mot de passe est désactivée : utilisez Microsoft ou Google.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _set_refresh_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, raw, max_age=settings.refresh_token_ttl_days * 86400, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path=COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


def _issue(db: Session, request: Request, response: Response, au: AuthUser, previous: RefreshToken | None = None) -> dict:
    """Mint an access token + a rotated refresh token for `au`, set the cookie, and return the body.
    A rotation (`previous`) keeps the device's identity and sign-in time."""
    raw, token_hash = security.new_refresh_token()
    now = _now()
    db.add(RefreshToken(
        user_id=au.id, token_hash=token_hash, expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=(request.headers.get("user-agent") or "")[:300], ip=(request.client.host if request.client else "")[:64],
        signed_in_at=(previous.signed_in_at or previous.created_at) if previous else now,
    ))
    db.commit()
    access, expires_in = security.create_access_token(au.id)
    _set_refresh_cookie(response, raw)
    return {"access_token": access, "expires_in": expires_in, "user": load_native_user(db, au.id).public()}


def _revoke_all(db: Session, user_id: str) -> None:
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))):
        rt.revoked = True


# ---------------------------------------------------------------- brute-force protection

def _locked(au: AuthUser) -> bool:
    return bool(au.locked_until and _aware(au.locked_until) > _now())


def _refuse_if_locked(au: AuthUser) -> None:
    if _locked(au):
        minutes = max(1, int((_aware(au.locked_until) - _now()).total_seconds() // 60) + 1)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Trop de tentatives : compte verrouillé. Réessayez dans {minutes} minute(s) ou demandez à un administrateur de le déverrouiller.")


def _record_failure(db: Session, au: AuthUser) -> None:
    au.failed_logins = (au.failed_logins or 0) + 1
    if au.failed_logins >= settings.auth_lockout_attempts:
        au.locked_until = _now() + timedelta(minutes=settings.auth_lockout_minutes)
        au.failed_logins = 0
        audit.log(db, au.email, "auth.locked", None, "user", au.id, minutes=settings.auth_lockout_minutes)
    db.commit()


def _record_success(au: AuthUser) -> None:
    au.failed_logins = 0
    au.locked_until = None
    au.last_login_at = _now()


def verify_second_factor(db: Session, au: AuthUser, code: str) -> bool:
    """A current authenticator code (never the same one twice) or an unused recovery code."""
    secret = secret_box.decrypt(au.mfa_secret_enc)
    step = totp.verify(secret, code, au.mfa_last_step or 0) if secret else None
    if step is not None:
        au.mfa_last_step = step
        return True
    digest = totp.hash_recovery(code)
    remaining = list(au.mfa_recovery or [])
    if digest in remaining:
        remaining.remove(digest)
        au.mfa_recovery = remaining
        audit.log(db, au.email, "auth.recovery_code_used", None, "user", au.id, remaining=len(remaining))
        return True
    return False


def _second_factor_step(db: Session, au: AuthUser) -> dict:
    db.commit()
    return {"mfa_required": True, "mfa_token": security.create_mfa_token(au.id)}


# ---------------------------------------------------------------- sign in / out

class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _password_method()
    au = db.scalar(select(AuthUser).where(func.lower(AuthUser.email) == body.email))
    if au and au.active:
        _refuse_if_locked(au)
    # The hash compare only runs for a real active user; the generic message avoids account enumeration.
    if not au or not au.active or not security.verify_password(body.password, au.password_hash):
        if au and au.active:
            _record_failure(db, au)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)
    if security.needs_rehash(au.password_hash):
        au.password_hash = security.hash_password(body.password)
    if au.mfa_enabled:
        return _second_factor_step(db, au)  # counters reset only once the code is right too
    _record_success(au)
    audit.log(db, au.email, "auth.login", None, "user", au.id)
    return _issue(db, request, response, au)


class MfaIn(BaseModel):
    mfa_token: str = Field(max_length=1000)
    code: str = Field(max_length=40)


@router.post("/mfa")
def login_second_factor(body: MfaIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _password_method()
    try:
        user_id = security.decode_mfa_token(body.mfa_token)
    except security.TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La connexion a expiré, recommencez.")
    au = db.get(AuthUser, user_id)
    if not au or not au.active or not au.mfa_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)
    _refuse_if_locked(au)
    if not verify_second_factor(db, au, body.code):
        _record_failure(db, au)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code incorrect.")
    _record_success(au)
    audit.log(db, au.email, "auth.login", None, "user", au.id, second_factor=True)
    return _issue(db, request, response, au)


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    _native_only()
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No session")
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(raw)))
    if not rt or rt.revoked or _aware(rt.expires_at) < _now():
        _clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    au = db.get(AuthUser, rt.user_id)
    if not au or not au.active:
        _clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    rt.revoked = True  # rotate: this refresh token is single-use
    return _issue(db, request, response, au, previous=rt)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(raw)))
        if rt and not rt.revoked:
            rt.revoked = True
            db.commit()
    _clear_refresh_cookie(response)
    return {"ok": True}


# ---------------------------------------------------------------- devices (sessions)

def _device(user_agent: str) -> str:
    ua = user_agent or ""
    if "DeliveryOps" in ua or "Electron" in ua:
        browser = "Application bureau"
    else:
        browser = next((name for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                                 ("Chrome/", "Chrome"), ("Safari/", "Safari")) if token in ua), "Navigateur")
    system = next((name for token, name in (("Windows", "Windows"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                            ("Mac OS X", "macOS"), ("Linux", "Linux")) if token in ua), "")
    return f"{browser} · {system}" if system else browser


def _current_hash(request: Request) -> str | None:
    raw = request.cookies.get(REFRESH_COOKIE)
    return security.hash_refresh_token(raw) if raw else None


@router.get("/sessions")
def list_sessions(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """This user's signed-in browsers/devices (one active refresh token each)."""
    _native_only()
    current = _current_hash(request)
    rows = db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.subject, RefreshToken.revoked.is_(False),
                                                 RefreshToken.expires_at > _now()).order_by(RefreshToken.created_at.desc()))
    return [{
        "id": rt.id, "device": _device(rt.user_agent), "ip": rt.ip,
        "signed_in_at": (rt.signed_in_at or rt.created_at).isoformat(), "last_active_at": rt.created_at.isoformat(),
        "current": rt.token_hash == current,
    } for rt in rows]


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _native_only()
    rt = db.get(RefreshToken, session_id)
    if not rt or rt.user_id != user.subject:
        raise HTTPException(404, "Session introuvable")
    rt.revoked = True
    audit.log(db, user.username, "auth.session_revoked", None, "user", user.subject, device=_device(rt.user_agent))
    db.commit()
    return {"ok": True}


@router.post("/logout-others")
def logout_others(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Sign out every other device/browser: revoke all of this user's sessions except the current one."""
    _native_only()
    keep = _current_hash(request)
    revoked = 0
    for rt in db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.subject, RefreshToken.revoked.is_(False))):
        if rt.token_hash != keep:
            rt.revoked = True
            revoked += 1
    audit.log(db, user.username, "auth.logout_others", None, "user", user.subject, revoked=revoked)
    db.commit()
    return {"revoked": revoked}


# ---------------------------------------------------------------- passwords

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _native_only()
    au = db.get(AuthUser, user.subject)
    if not au or not security.verify_password(body.current_password, au.password_hash):
        raise HTTPException(400, "Mot de passe actuel incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(400, "Le nouveau mot de passe doit être différent")
    au.password_hash = security.hash_password(body.new_password)
    au.must_change_password = False
    _revoke_all(db, au.id)  # invalidate every existing session, then hand this browser a fresh one
    audit.log(db, au.email, "auth.password_changed", None, "user", au.id)
    return _issue(db, request, response, au)


# ---------------------------------------------------------------- sign in with Microsoft / Google

OIDC_COOKIE = "dm_oidc"
OIDC_COOKIE_PATH = "/api/auth/oidc"
_OIDC_TTL = 600


def _frontend(path: str = "/") -> str:
    return settings.frontend_url.rstrip("/") + path


def _redirect_uri(provider: str) -> str:
    return _frontend(f"/api/auth/oidc/{provider}/callback")


def _safe_next(value: str | None) -> str:
    """Only same-site paths: never an absolute URL or a //host (open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or value.startswith("/\\") or value.startswith("/api"):
        return "/"
    return value[:500]


def _back_with_error(code: str, status_path: str = "/") -> RedirectResponse:
    sep = "&" if "?" in status_path else "?"
    response = RedirectResponse(_frontend(f"{status_path}{sep}auth_error={code}"), status_code=303)
    response.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH)
    return response


def _session_user_id(request: Request, db: Session) -> str | None:
    """The account signed in on this browser (refresh cookie), for linking a new sign-in method."""
    raw = request.cookies.get(REFRESH_COOKIE)
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(raw))) if raw else None
    if not rt or rt.revoked or _aware(rt.expires_at) < _now():
        return None
    au = db.get(AuthUser, rt.user_id)
    return au.id if au and au.active else None


@router.get("/oidc/{name}/start")
def oidc_start(name: str, request: Request, next: str = "/", link: bool = False, db: Session = Depends(get_db)):
    _native_only()
    p = oidc.provider(name)
    if not p:
        return _back_with_error("method_disabled")
    link_to = None
    if link:
        link_to = _session_user_id(request, db)
        if not link_to:
            return _back_with_error("session_required")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = oidc.new_pkce()
    try:
        url = oidc.authorize_url(p, redirect_uri=_redirect_uri(name), state=state, nonce=nonce, code_challenge=challenge)
    except oidc.OIDCError as exc:
        log.error("sign-in with %s unavailable: %s", name, exc)
        return _back_with_error(exc.code)
    now = int(time.time())
    cookie = jwt.encode({"typ": "oidc", "p": name, "s": state, "n": nonce, "v": verifier, "next": _safe_next(next),
                         "link": link_to, "iat": now, "exp": now + _OIDC_TTL}, settings.auth_secret, algorithm="HS256")
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(OIDC_COOKIE, cookie, max_age=_OIDC_TTL, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", path=OIDC_COOKIE_PATH)
    return response


def _pending_account(db: Session, ident: "oidc.Identity") -> AuthUser:
    """First sign-in of an unknown person: an account with no access until an admin assigns a role."""
    role = db.scalar(select(Role).where(Role.name == "unassigned"))
    au = AuthUser(email=ident.email, name=ident.name, password_hash="", role_id=role.id, clients=[],
                  created_by=f"sso:{ident.provider}")
    db.add(au)
    db.flush()
    rbac.notify_pending_user(db, au, oidc.LABELS[ident.provider])
    audit.log(db, au.email, "auth.account_created", None, "user", au.id, method=ident.provider)
    return au


@router.get("/oidc/{name}/callback")
def oidc_callback(name: str, request: Request, code: str = "", state: str = "", error: str = "",
                  db: Session = Depends(get_db)):
    _native_only()
    try:
        flow = jwt.decode(request.cookies.get(OIDC_COOKIE, ""), settings.auth_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _back_with_error("session_expired")
    back = "/account" if flow.get("link") else "/"
    if flow.get("typ") != "oidc" or flow.get("p") != name or not secrets.compare_digest(str(flow.get("s", "")), state):
        return _back_with_error("session_expired", back)
    if error or not code:
        return _back_with_error("cancelled" if error == "access_denied" else "provider_error", back)
    p = oidc.provider(name)
    if not p:
        return _back_with_error("method_disabled", back)
    try:
        claims = oidc.exchange_code(p, code=code, redirect_uri=_redirect_uri(name), code_verifier=flow["v"], nonce=flow["n"])
    except oidc.OIDCError as exc:
        log.warning("sign-in with %s failed: %s", name, exc)
        return _back_with_error(exc.code, back)
    ident = oidc.identity(p, claims)
    linked = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == name, ExternalIdentity.subject == ident.subject))

    if flow.get("link"):  # "Lier mon compte …" from Mon compte
        if linked and linked.user_id != flow["link"]:
            return _back_with_error("identity_in_use", back)
        if not linked:
            db.add(ExternalIdentity(user_id=flow["link"], provider=name, subject=ident.subject, email=ident.claimed_email,
                                    last_used_at=_now()))
            audit.log(db, ident.claimed_email, "auth.method_linked", None, "user", flow["link"], method=name)
            db.commit()
        response = RedirectResponse(_frontend(f"/account?linked={name}"), status_code=303)
        response.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH)
        return response

    if linked:
        au = db.get(AuthUser, linked.user_id)
    else:
        if not ident.email:
            return _back_with_error("email_unverified")
        au = db.scalar(select(AuthUser).where(func.lower(AuthUser.email) == ident.email))
        if au is None:
            if not oidc.domain_allowed(ident.email):
                return _back_with_error("domain_not_allowed")
            au = _pending_account(db, ident)
        linked = ExternalIdentity(user_id=au.id, provider=name, subject=ident.subject, email=ident.email)
        db.add(linked)
    if not au or not au.active:
        db.rollback()
        return _back_with_error("account_disabled")
    linked.email, linked.last_used_at = ident.claimed_email or linked.email, _now()
    au.last_login_at = _now()
    audit.log(db, au.email, "auth.login", None, "user", au.id, method=name)
    response = RedirectResponse(_frontend(flow.get("next") or "/"), status_code=303)
    response.delete_cookie(OIDC_COOKIE, path=OIDC_COOKIE_PATH)
    _issue(db, request, response, au)
    return response


def _reset_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ForgotIn(BaseModel):
    email: Email


FORGOT_REPLY = "Si un compte existe pour cette adresse, un e-mail contenant un lien de réinitialisation vient d'être envoyé."


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Always the same answer, whether or not the account exists (no account enumeration)."""
    _password_method()
    au = db.scalar(select(AuthUser).where(func.lower(AuthUser.email) == body.email))
    if not au or not au.active:
        return {"ok": True, "message": FORGOT_REPLY}
    recent = db.scalar(select(PasswordResetToken.id).where(
        PasswordResetToken.user_id == au.id, PasswordResetToken.used.is_(False),
        PasswordResetToken.created_at > _now() - timedelta(minutes=2)))
    if recent:  # one e-mail per couple of minutes: no mailbox flooding
        return {"ok": True, "message": FORGOT_REPLY}
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=au.id, token_hash=_reset_hash(raw),
                              expires_at=_now() + timedelta(minutes=settings.password_reset_ttl_minutes)))
    audit.log(db, au.email, "auth.password_reset_requested", None, "user", au.id)
    db.commit()
    link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={raw}"
    if mailer.configured():
        mailer.send(au.email, "DeliveryOps — réinitialisation de votre mot de passe", (
            f"Bonjour {au.name or ''},\n\nPour choisir un nouveau mot de passe, ouvrez ce lien "
            f"(valable {settings.password_reset_ttl_minutes} minutes, utilisable une seule fois) :\n\n{link}\n\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail : votre mot de passe reste inchangé."))
    elif not settings.is_production:
        log.warning("SMTP not configured — password reset link for %s: %s", au.email, link)
    else:
        log.error("SMTP not configured (SMTP_HOST/SMTP_FROM): cannot e-mail the password reset link for %s", au.email)
    return {"ok": True, "message": FORGOT_REPLY}


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/reset-password")
def reset_password(body: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _password_method()
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _reset_hash(body.token)))
    au = db.get(AuthUser, token.user_id) if token else None
    if not token or token.used or _aware(token.expires_at) < _now() or not au or not au.active:
        raise HTTPException(400, "Ce lien est invalide ou a expiré. Refaites une demande de réinitialisation.")
    for t in db.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == au.id, PasswordResetToken.used.is_(False))):
        t.used = True  # this link and any other pending one
    au.password_hash = security.hash_password(body.new_password)
    au.must_change_password = False
    au.failed_logins, au.locked_until = 0, None
    _revoke_all(db, au.id)
    audit.log(db, au.email, "auth.password_reset_done", None, "user", au.id)
    if au.mfa_enabled:
        return _second_factor_step(db, au)  # a reset link alone must not bypass two-factor
    _record_success(au)
    return _issue(db, request, response, au)
