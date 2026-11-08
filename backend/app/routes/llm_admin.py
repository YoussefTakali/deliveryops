"""LLM endpoints & API keys (/api/admin/llm), `governance.manage` only.

An admin sets, without touching the .env or the code, the endpoint / API key / model used by the
whole organisation ("default") or by one agent. Keys are write-only: stored encrypted
(app.secret_box), shown back only as a masked hint, never written to the audit log.
"""

from urllib.parse import urlparse

import openai
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import audit, governance, llm_config, secret_box
from app.auth import User, require_permission
from app.config import get_settings
from app.db import get_db
from app.models import LLMProviderConfig

router = APIRouter(prefix="/api/admin/llm")
settings = get_settings()
_admin = require_permission("governance.manage")


def _known(key: str) -> None:
    if key != llm_config.DEFAULT and key not in governance.AGENTS:
        raise HTTPException(404, f"Agent inconnu : {key}")


def _entry(db: Session, key: str) -> dict:
    row = db.get(LLMProviderConfig, key)
    eff = llm_config.resolve(key)
    return {
        "key": key,
        "label": "Tous les agents (par défaut)" if key == llm_config.DEFAULT else governance.AGENTS[key],
        "kind": "default" if key == llm_config.DEFAULT else governance.AGENT_KIND.get(key, "feature"),
        "base_url": row.base_url if row else "",
        "model": row.model if row else "",
        "has_key": bool(row and row.api_key_enc),
        "key_hint": row.key_hint if row else "",
        "key_unreadable": bool(row and row.api_key_enc and secret_box.decrypt(row.api_key_enc) is None),
        "updated_by": row.updated_by if row else "",
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "effective": {"base_url": eff.base_url, "model": eff.model, "has_key": bool(eff.api_key), "source": eff.source},
    }


@router.get("")
def list_configs(user: User = Depends(_admin), db: Session = Depends(get_db)):
    return {
        "mode": settings.llm_mode,
        "encryption_weak": not secret_box.strong_secret_configured(),
        "env": {
            "base_url": settings.llm_base_url, "has_key": bool(settings.llm_api_key),
            "models": {"reasoning": settings.llm_model_reasoning, "agent": settings.llm_model_agent, "fast": settings.llm_model_fast},
        },
        "default": _entry(db, llm_config.DEFAULT),
        "agents": [_entry(db, k) for k in governance.AGENTS],
    }


def _check_url(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return ""
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("L'URL doit commencer par http:// ou https://")
    return v.rstrip("/")


class ConfigIn(BaseModel):
    base_url: str | None = Field(None, max_length=300)   # None = unchanged, "" = inherit
    api_key: str | None = Field(None, max_length=500)    # None = unchanged
    clear_key: bool = False
    model: str | None = Field(None, max_length=200)      # None = unchanged, "" = inherit

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _check_url(v)

    @field_validator("api_key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) < 8:
            raise ValueError("Clé API trop courte")
        return v


@router.put("/{key}")
def put_config(key: str, body: ConfigIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    _known(key)
    if body.api_key and settings.is_production and not secret_box.strong_secret_configured():
        raise HTTPException(400, "Définissez SECRETS_KEY (ou AUTH_SECRET) sur le serveur avant d'enregistrer une clé API.")
    row = db.get(LLMProviderConfig, key) or LLMProviderConfig(key=key)
    changes: dict = {}
    if body.base_url is not None:
        row.base_url = changes["base_url"] = body.base_url
    if body.model is not None:
        if key == llm_config.DEFAULT and body.model.strip():
            raise HTTPException(400, "Le modèle se choisit par agent (les niveaux par défaut restent ceux du serveur).")
        row.model = changes["model"] = body.model.strip()
    if body.clear_key:
        row.api_key_enc, row.key_hint = "", ""
        changes["api_key"] = "removed"
    elif body.api_key:
        row.api_key_enc, row.key_hint = secret_box.encrypt(body.api_key), secret_box.mask(body.api_key)
        changes["api_key"] = "replaced"
    row.updated_by = user.username
    if not (row.base_url or row.api_key_enc or row.model):
        if db.get(LLMProviderConfig, key):
            db.delete(row)
    else:
        db.add(row)
    audit.log(db, user.username, "llm.config_update", None, "llm_config", key, **changes)
    db.commit()
    llm_config.invalidate()
    return _entry(db, key)


@router.delete("/{key}")
def reset_config(key: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    _known(key)
    row = db.get(LLMProviderConfig, key)
    if row:
        db.delete(row)
        audit.log(db, user.username, "llm.config_reset", None, "llm_config", key)
        db.commit()
        llm_config.invalidate()
    return _entry(db, key)


class TestIn(BaseModel):
    """Unsaved values to try before saving; omitted fields use the saved/effective configuration."""
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=200)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _check_url(v)


@router.post("/{key}/test")
def test_config(key: str, body: TestIn | None = None, user: User = Depends(_admin)):
    _known(key)
    body = body or TestIn()
    rows = dict(llm_config.rows())
    if body.base_url or body.api_key or body.model:
        current = dict(rows.get(key) or {"base_url": "", "api_key": None, "model": ""})
        if body.base_url:
            current["base_url"] = body.base_url
        if body.api_key:
            current["api_key"] = body.api_key.strip()
        if body.model is not None and key != llm_config.DEFAULT:
            current["model"] = body.model.strip()
        rows[key] = current
    cfg = llm_config.resolve(key, saved=rows)
    if not cfg.api_key:
        return {"ok": False, "message": "Aucune clé API pour cette configuration.", "base_url": cfg.base_url}
    try:
        client = openai.OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=10, max_retries=0)
        ids = [m.id for m in client.models.list()]
    except openai.AuthenticationError:
        return {"ok": False, "message": "Clé API refusée par le fournisseur (401).", "base_url": cfg.base_url}
    except openai.PermissionDeniedError:
        return {"ok": False, "message": "Clé valide mais sans accès (403).", "base_url": cfg.base_url}
    except openai.NotFoundError:
        return {"ok": False, "message": "URL incorrecte : le point d'accès /models est introuvable (404).", "base_url": cfg.base_url}
    except (openai.APIConnectionError, openai.APITimeoutError):
        return {"ok": False, "message": "Point d'accès injoignable (réseau, URL ou pare-feu).", "base_url": cfg.base_url}
    except openai.APIError as exc:
        return {"ok": False, "message": f"Erreur du fournisseur : {type(exc).__name__}.", "base_url": cfg.base_url}
    model = cfg.model
    if model and ids and model not in ids:
        return {"ok": False, "message": f"Connexion OK, mais le modèle « {model} » n'est pas proposé par ce point d'accès.",
                "base_url": cfg.base_url, "models": len(ids)}
    return {"ok": True, "message": f"Connexion OK — {len(ids)} modèle(s) disponible(s).", "base_url": cfg.base_url, "models": len(ids)}
