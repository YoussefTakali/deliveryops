"""Company rules API (/api/policy): `policy.manage` edits the rules and uploads policy documents to
extract rules from; everyone can read the active rules that apply to them. See app/policy.py."""

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, knowledge, llm, policy, telemetry
from app.auth import User, current_user, require_permission
from app.config import get_settings
from app.db import get_db
from app.middleware import consume_llm_quota
from app.models import AuditLog, PolicyDocument, PolicyRule

router = APIRouter(prefix="/api/policy")
settings = get_settings()
_admin = require_permission("policy.manage")
_MAX_DOC_CHARS = 60_000
_EXTRACT_CHUNK = 14_000


def _rule_out(r: PolicyRule, docs: dict[str, str] | None = None) -> dict:
    return {
        "id": r.id, "title": r.title, "text": r.text, "category": r.category, "action": r.action,
        "keywords": r.keywords or [], "active": r.active, "proposed": r.proposed,
        "document_id": r.document_id, "document": (docs or {}).get(r.document_id or "", ""),
        "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _changed(db: Session) -> None:
    db.commit()
    policy.invalidate()


# ---------------------------------------------------------------- read (everyone)

@router.get("/public")
def public_rules(user: User = Depends(current_user)):
    """The rules in force, so every person knows what the agents will refuse or warn about."""
    return [{"title": r.title, "text": r.text, "category": r.category, "action": r.action} for r in policy.active_rules()]


# ---------------------------------------------------------------- rules (admin)

class RuleIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    text: str = Field("", max_length=2000)
    category: str = "autre"
    action: str = "warn"
    keywords: list[str] = Field(default_factory=list, max_length=50)
    active: bool = True

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in policy.CATEGORIES:
            raise ValueError("catégorie inconnue")
        return v

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v not in policy.ACTIONS:
            raise ValueError("action inconnue (block, warn ou guide)")
        return v

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        out = []
        for k in (s.strip() for s in v):
            if not k:
                continue
            if len(k) > 2 and k.startswith("/") and k.endswith("/"):
                try:
                    re.compile(k[1:-1])
                except re.error as exc:
                    raise ValueError(f"expression régulière invalide {k} : {exc}") from exc
            out.append(k[:120])
        return list(dict.fromkeys(out))


@router.get("/rules")
def list_rules(user: User = Depends(_admin), db: Session = Depends(get_db)):
    docs = {d.id: d.filename for d in db.scalars(select(PolicyDocument))}
    rows = db.scalars(select(PolicyRule).order_by(PolicyRule.proposed.desc(), PolicyRule.created_at.desc())).all()
    return {"rules": [_rule_out(r, docs) for r in rows], "categories": policy.CATEGORIES, "actions": policy.ACTIONS}


@router.post("/rules")
def create_rule(body: RuleIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    r = PolicyRule(**body.model_dump(), created_by=user.username, updated_by=user.username)
    db.add(r)
    audit.log(db, user.username, "policy.rule_created", None, "policy_rule", r.id, title=r.title, rule_action=r.action)
    _changed(db)
    return _rule_out(r)


def _get(db: Session, rule_id: str) -> PolicyRule:
    r = db.get(PolicyRule, rule_id)
    if not r:
        raise HTTPException(404, "Règle introuvable")
    return r


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    r = _get(db, rule_id)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    r.updated_by = user.username
    audit.log(db, user.username, "policy.rule_updated", None, "policy_rule", r.id, title=r.title, rule_action=r.action, active=r.active)
    _changed(db)
    return _rule_out(r)


@router.post("/rules/{rule_id}/accept")
def accept_rule(rule_id: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    """A rule proposed from a document goes into force."""
    r = _get(db, rule_id)
    r.proposed, r.active, r.updated_by = False, True, user.username
    audit.log(db, user.username, "policy.rule_accepted", None, "policy_rule", r.id, title=r.title)
    _changed(db)
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    r = _get(db, rule_id)
    audit.log(db, user.username, "policy.rule_deleted", None, "policy_rule", r.id, title=r.title)
    db.delete(r)
    _changed(db)
    return {"deleted": True}


# ---------------------------------------------------------------- documents → proposed rules

class _Extracted(BaseModel):
    title: str = Field(description="la règle, courte et impérative (max 120 caractères)")
    text: str = Field("", description="précision utile : périmètre, exceptions, qui contacter")
    category: str = Field("autre", description="confidentialite | securite | logiciels | conformite | autre")
    action: str = Field("warn", description="block si l'interdiction est explicite et grave, warn sinon, guide pour une bonne pratique")
    keywords: list[str] = Field(default_factory=list, description="mots ou noms qui signalent à coup sûr une infraction (souvent vide)")


class ExtractedRules(BaseModel):
    rules: list[_Extracted] = Field(default_factory=list)


_OBLIGATION = re.compile(r"\b(interdit|interdite|ne doit pas|ne doivent pas|ne jamais|jamais|obligatoire|doit|doivent|"
                         r"est tenu|sont tenus|prohib\w*|must|shall|never|forbidden|not allowed)\b", re.IGNORECASE)


def _heuristic_rules(text: str) -> ExtractedRules:
    """Without the AI: every sentence that states an obligation or a prohibition becomes a draft rule."""
    out, seen = [], set()
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        s = " ".join(sentence.split()).strip(" -•*\t")
        if 20 <= len(s) <= 400 and _OBLIGATION.search(s) and s.lower() not in seen:
            seen.add(s.lower())
            strong = re.search(r"\b(interdit|interdite|ne doit pas|ne jamais|prohib\w*|forbidden|never)\b", s, re.IGNORECASE)
            out.append(_Extracted(title=s[:120], text=s if len(s) > 120 else "", action="block" if strong else "warn"))
        if len(out) >= 40:
            break
    return ExtractedRules(rules=out)


def _extract(text: str, filename: str) -> tuple[list[_Extracted], bool]:
    chunks = [text[i:i + _EXTRACT_CHUNK] for i in range(0, min(len(text), _MAX_DOC_CHARS), _EXTRACT_CHUNK)] or [""]
    found: list[_Extracted] = []
    used_ai = False
    with telemetry.attribute(agent="policy"):
        for chunk in chunks:
            result, used = llm.structured(
                schema=ExtractedRules, tier="agent", thinking=False, max_tokens=3000,
                system=("Tu extrais d'un document d'entreprise (charte, politique de sécurité, règlement) les règles que des "
                        "assistants IA et leurs utilisateurs doivent respecter : confidentialité des données clients, logiciels "
                        "et sites autorisés, sécurité, conformité. Une règle par obligation ou interdiction concrète ; ignore le "
                        "préambule et les généralités. Reste fidèle au texte, n'invente rien."),
                context=f"<document nom=\"{filename}\">\n{chunk}\n</document>",
                task="Liste les règles de ce document.", fallback=lambda: _heuristic_rules(chunk))
            used_ai = used_ai or used
            found += result.rules
    unique, seen = [], set()
    for r in found:
        key = r.title.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:60], used_ai


@router.get("/documents")
def list_documents(user: User = Depends(_admin), db: Session = Depends(get_db)):
    counts: dict[str, dict] = {}
    for r in db.scalars(select(PolicyRule).where(PolicyRule.document_id.is_not(None))):
        c = counts.setdefault(r.document_id, {"proposed": 0, "accepted": 0})
        c["proposed" if r.proposed else "accepted"] += 1
    return [{"id": d.id, "filename": d.filename, "uploaded_by": d.uploaded_by, "uploaded_at": d.uploaded_at.isoformat(),
             "chars": len(d.text or ""), **counts.get(d.id, {"proposed": 0, "accepted": 0})}
            for d in db.scalars(select(PolicyDocument).order_by(PolicyDocument.uploaded_at.desc()))]


@router.post("/documents")
async def upload_document(request: Request, file: UploadFile = File(...), user: User = Depends(_admin), db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Fichier trop volumineux (max {settings.max_upload_mb} Mo)")
    filename = (file.filename or "document")[:300]
    try:
        text = knowledge.extract_text(filename, data).strip()
    except Exception as exc:  # noqa: BLE001 — unreadable/unsupported file
        raise HTTPException(400, f"Impossible de lire ce fichier : {exc}") from exc
    if len(text) < 40:
        raise HTTPException(400, "Aucun texte exploitable dans ce fichier (document scanné ?).")
    consume_llm_quota(request)
    doc = PolicyDocument(filename=filename, text=text[:_MAX_DOC_CHARS], uploaded_by=user.username)
    db.add(doc)
    db.flush()
    extracted, used_ai = _extract(doc.text, filename)
    for e in extracted:
        db.add(PolicyRule(
            title=e.title.strip()[:200], text=e.text.strip()[:2000],
            category=e.category if e.category in policy.CATEGORIES else "autre",
            action=e.action if e.action in policy.ACTIONS else "warn",
            keywords=[k.strip()[:120] for k in e.keywords if k.strip()][:20],
            active=False, proposed=True, document_id=doc.id, created_by=user.username, updated_by=user.username))
    audit.log(db, user.username, "policy.document_uploaded", None, "policy_document", doc.id, filename=filename,
              proposed=len(extracted), ai=used_ai)
    _changed(db)
    return {"id": doc.id, "filename": filename, "proposed": len(extracted), "llm_used": used_ai}


@router.post("/documents/{doc_id}/accept-all")
def accept_document_rules(doc_id: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    n = 0
    for r in db.scalars(select(PolicyRule).where(PolicyRule.document_id == doc_id, PolicyRule.proposed.is_(True))):
        r.proposed, r.active, r.updated_by = False, True, user.username
        n += 1
    audit.log(db, user.username, "policy.document_rules_accepted", None, "policy_document", doc_id, count=n)
    _changed(db)
    return {"accepted": n}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    """Removes the document and the rules still awaiting review; rules already in force stay."""
    doc = db.get(PolicyDocument, doc_id)
    if not doc:
        raise HTTPException(404, "Document introuvable")
    for r in db.scalars(select(PolicyRule).where(PolicyRule.document_id == doc_id)):
        if r.proposed:
            db.delete(r)
        else:
            r.document_id = None
    audit.log(db, user.username, "policy.document_deleted", None, "policy_document", doc_id, filename=doc.filename)
    db.delete(doc)
    _changed(db)
    return {"deleted": True}


# ---------------------------------------------------------------- try it & see breaches

class TestIn(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


@router.post("/test")
def test_text(body: TestIn, user: User = Depends(_admin)):
    """Dry run: what would happen to this message (nothing is recorded)."""
    v = policy.check(body.text, "test")

    def out(hits):
        return [{"rule": h.rule.title, "action": h.rule.action, "reason": h.reason, "by": h.by} for h in hits]

    decision = "blocked" if v.blocked else "warned" if v.warned else "allowed"
    return {"decision": decision, "blocked": out(v.blocked), "warned": out(v.warned)}


@router.get("/violations")
def violations(days: int = 30, user: User = Depends(_admin), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "policy.violation", AuditLog.ts >= since)
                      .order_by(AuditLog.ts.desc()).limit(300)).all()
    return [{"ts": a.ts.isoformat(), "user": a.actor, "rule": (a.details or {}).get("rule", ""),
             "where": (a.details or {}).get("where", ""), "decision": (a.details or {}).get("decision", ""),
             "detected_by": (a.details or {}).get("detected_by", "")} for a in rows]
