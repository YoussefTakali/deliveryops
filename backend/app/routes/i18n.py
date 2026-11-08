"""Interface translation (/api/i18n): the UI is written in French; any other language is produced by
machine translation with the platform's own LLM, string by string, and cached org-wide
(UiTranslation) so each string is translated once per language.

Only interface strings are sent (the SPA skips user content). Numbers arrive as placeholders
({0}, {1}…) and must come back intact, otherwise that translation is dropped. Governed like any AI
feature ("ui_translation": capped/blocked from Gouvernance IA); when the AI is unavailable the
interface simply stays in French.
"""

import contextvars
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import governance, llm, telemetry
from app.auth import User, current_user
from app.config import get_settings
from app.db import get_db
from app.middleware import consume_llm_quota
from app.models import UiTranslation

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/i18n")
settings = get_settings()

SOURCE_LANG = "fr"
_BATCH = 50
_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_LANG_NAME = re.compile(r"^[\w\s()'’.,-]{1,80}$", re.UNICODE)
_PLACEHOLDER = re.compile(r"\{\d+\}")


class TranslateIn(BaseModel):
    lang: str = Field(max_length=20)
    language_name: str = Field("", max_length=80)  # e.g. "German", helps with rarer codes
    texts: list[str] = Field(max_length=200)

    @field_validator("lang")
    @classmethod
    def _lang(cls, v: str) -> str:
        base, *subtags = v.strip().split("-")
        code = "-".join([base.lower(), *subtags])
        if not _LANG.match(code):
            raise ValueError("invalid language code")
        return code

    @field_validator("language_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        return v if v and _LANG_NAME.match(v) else ""

    @field_validator("texts")
    @classmethod
    def _texts(cls, v: list[str]) -> list[str]:
        return [t for t in dict.fromkeys(s.strip() for s in v) if t and len(t) <= 500]


class UiTranslationBatch(BaseModel):
    translations: list[str]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _system(lang: str, name: str) -> str:
    target = f"{name} ({lang})" if name else lang
    return (
        "You translate the user interface of DeliveryOps, a French enterprise web application used by IT "
        f"delivery teams. Translate each French interface string into {target}. Rules: keep placeholders such as "
        "{0} and {1} exactly as they are; keep product, feature and brand names (DeliveryOps, Copilot, Microsoft, "
        "Teams, Keycloak, Jira, ServiceNow), people's and companies' names, e-mails, URLs, file names and codes "
        "unchanged; keep punctuation, symbols (→ ↗ ✓ • …) and emojis; stay as short as a UI label; if a string is "
        "not French text, return it unchanged. Return exactly one translation per input string, in the same order."
    )


def _translate_batch(texts: list[str], lang: str, name: str) -> dict[str, str]:
    result, by_llm = llm.structured(
        schema=UiTranslationBatch, tier="fast", thinking=False, system=_system(lang, name),
        task=f"Translate these {len(texts)} strings. Return {{\"translations\": [...]}} with {len(texts)} items.",
        context="\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)),
        fallback=lambda: UiTranslationBatch(translations=[]),
    )
    if not by_llm or len(result.translations) != len(texts):
        return {}
    out = {}
    for src, dst in zip(texts, result.translations):
        dst = (dst or "").strip()
        if dst and sorted(_PLACEHOLDER.findall(src)) == sorted(_PLACEHOLDER.findall(dst)):
            out[src] = dst
    return out


def _machine_translate(texts: list[str], lang: str, name: str) -> dict[str, str]:
    batches = [texts[i : i + _BATCH] for i in range(0, len(texts), _BATCH)]
    with telemetry.attribute(agent="ui_translation"), ThreadPoolExecutor(max_workers=min(3, len(batches))) as pool:
        # each batch runs in a copy of this context, so the call stays attributed to the user and agent
        futures = [pool.submit(contextvars.copy_context().run, _translate_batch, b, lang, name) for b in batches]
        out: dict[str, str] = {}
        for f in futures:
            try:
                out.update(f.result())
            except Exception:  # noqa: BLE001 — a failed batch just stays in French
                log.exception("interface translation batch failed")
    return out


@router.post("/translate")
def translate(body: TranslateIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lang = body.lang
    if lang.split("-")[0] == SOURCE_LANG or not body.texts:
        return {"lang": lang, "translations": {t: t for t in body.texts}, "available": True, "reason": None}

    hashes = {_hash(t): t for t in body.texts}
    cached = {hashes[r.source_hash]: r.text for r in db.scalars(
        select(UiTranslation).where(UiTranslation.lang == lang, UiTranslation.source_hash.in_(list(hashes))))}
    missing = [t for t in body.texts if t not in cached]
    if not missing:
        return {"lang": lang, "translations": cached, "available": True, "reason": None}

    if not settings.use_llm:
        return {"lang": lang, "translations": cached, "available": False,
                "reason": "La traduction automatique nécessite l'IA, qui est hors ligne sur ce serveur."}
    reason = governance.check(user.username, "ui_translation")
    if reason:
        return {"lang": lang, "translations": cached, "available": False, "reason": reason}
    consume_llm_quota(request)

    fresh = _machine_translate(missing, lang, body.language_name)
    for src, dst in fresh.items():
        db.add(UiTranslation(lang=lang, source_hash=_hash(src), source=src, text=dst))
    try:
        db.commit()
    except IntegrityError:  # another user translated the same strings meanwhile — theirs are as good
        db.rollback()
    return {"lang": lang, "translations": {**cached, **fresh}, "available": True, "reason": None}
