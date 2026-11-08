"""Server-side step executors (POST /api/automations/runs/{run_id}/steps).

Params arrive already rendered by the runner (templates resolved). Each executor returns the step's
output (see catalog.py). Side-effect steps honour `run.dry_run` by returning a preview instead.
Failures raise StepError with an HTTP status and a French message shown in the run log.
"""

import base64
import json
import logging
import mimetypes
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any

import openai
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, knowledge, llm, microsoft_graph
from app.auth import User
from app.automations import catalog, documents
from app.config import get_settings
from app.models import AuditLog, AutomationFile, AutomationRun

log = logging.getLogger(__name__)
settings = get_settings()

MAIL_ACTION = "automation.mail_send"
MAX_MAIL_ATTACHMENTS_BYTES = 3 * 1024 * 1024  # Graph sendMail JSON payload limit is 4 MB (base64 inflates by 4/3)
_EMAIL = re.compile(r"^[^@\s<>;,]+@[^@\s<>;,]+\.[^@\s<>;,]+$")
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_TEXT_EXTENSIONS = (".txt", ".md", ".html", ".htm", ".json", ".xml", ".eml", ".log")


class StepError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------- files

def content_type_for(name: str, given: str | None = None) -> str:
    if given and given != "application/octet-stream":
        return given[:200]
    ext = documents.extension(name)
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def safe_name(name: str, default: str = "fichier") -> str:
    name = _UNSAFE_NAME.sub("_", PurePath(str(name or "").replace("\\", "/")).name).strip(" .")
    return (name or default)[:200]


def file_meta(f: AutomationFile) -> dict:
    return {"file_id": f.id, "name": f.name, "size": f.size, "content_type": f.content_type}


def store_file(db: Session, owner: str, name: str, data: bytes, *, content_type: str | None = None,
               automation_id: str | None = None, run_id: str | None = None) -> AutomationFile:
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise StepError(413, f"Fichier trop volumineux ({len(data) // (1024 * 1024)} Mo, maximum {settings.max_upload_mb} Mo)")
    name = safe_name(name)
    f = AutomationFile(owner=owner, name=name, data=data, size=len(data), content_type=content_type_for(name, content_type),
                       automation_id=automation_id, run_id=run_id)
    db.add(f)
    db.flush()
    return f


def owned_file(db: Session, owner: str, file_id: Any) -> AutomationFile:
    if isinstance(file_id, dict):  # tolerate a whole file object ({file_id, name, ...}) as the value
        file_id = file_id.get("file_id")
    f = db.get(AutomationFile, str(file_id or ""))
    if f is None or f.owner != owner:
        raise StepError(404, f"Fichier introuvable ({file_id})")
    return f


# ---------------------------------------------------------------- param coercion

def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "oui", "yes", "vrai")
    return bool(value)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _norm(text: Any) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def _required(params: dict, name: str) -> Any:
    value = params.get(name)
    if value in (None, "", [], {}):
        raise StepError(400, f"Paramètre obligatoire manquant « {name} »")
    return value


def parse_recipients(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    # Split only on ; , and newlines — never on spaces, which would break "Léa Martin <lea@x.fr>".
    items = value if isinstance(value, list) else re.split(r"[;,\n\r]+", str(value))
    out = []
    for item in items:
        if isinstance(item, dict):  # {email} / {address}
            item = item.get("email") or item.get("address") or ""
        m = re.search(r"<([^>]+)>", str(item))
        address = (m.group(1) if m else str(item)).strip()
        if not address:
            continue
        if not _EMAIL.match(address):
            raise StepError(400, f"Adresse e-mail invalide « {address} »")
        if address.lower() not in (a.lower() for a in out):
            out.append(address)
    return out


def _require_token(token: str | None) -> str:
    if not token:
        raise StepError(401, "Jeton Microsoft requis (connectez votre compte Microsoft et autorisez l'accès aux mails)")
    return token


def _graph_call(fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except microsoft_graph.GraphError as exc:
        raise StepError(exc.status_code if exc.status_code in (401, 403, 404, 429) else 502, exc.detail) from exc


# ---------------------------------------------------------------- mail

def _message_item(m: dict, attachments: list[dict]) -> dict:
    sender = (m.get("from") or {}).get("emailAddress") or {}
    return {
        "id": m.get("id"), "subject": m.get("subject") or "", "from_name": sender.get("name") or "",
        "from_email": sender.get("address") or "", "received": m.get("receivedDateTime"),
        "body_preview": m.get("bodyPreview") or "",
        "attachments": [{"id": a.get("id"), "name": a.get("name"), "size": a.get("size"), "content_type": a.get("contentType")}
                        for a in attachments if not a.get("isInline")],
    }


def mail_search(params: dict, token: str | None) -> dict:
    token = _require_token(token)
    since = datetime.now(timezone.utc) - timedelta(days=_int(params.get("since_days"), 7, 1, 365))
    top = _int(params.get("top"), 10, 1, 50)
    has_attachment = _bool(params.get("has_attachment"))
    from_q, subject_q, body_q = (_norm(params.get(k)).strip() for k in ("from_contains", "subject_contains", "body_contains"))
    raw = _graph_call(microsoft_graph.list_messages, token, since_iso=since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      has_attachment=has_attachment, with_body=bool(body_q))
    messages = []
    for m in raw:
        sender = (m.get("from") or {}).get("emailAddress") or {}
        if from_q and from_q not in _norm(f"{sender.get('name', '')} {sender.get('address', '')}"):
            continue
        if subject_q and subject_q not in _norm(m.get("subject")):
            continue
        if body_q and body_q not in _norm(f"{m.get('bodyPreview', '')} {(m.get('body') or {}).get('content', '')}"):
            continue
        attachments = _graph_call(microsoft_graph.list_attachments, token, m["id"]) if m.get("hasAttachments") else []
        item = _message_item(m, attachments)
        if has_attachment and not item["attachments"]:
            continue  # only inline images (signatures): not a real attachment
        messages.append(item)
        if len(messages) >= top:
            break
    return {"messages": messages, "latest": messages[0] if messages else None, "count": len(messages)}


def mail_get_attachment(db: Session, user: User, run: AutomationRun, params: dict, token: str | None) -> dict:
    token = _require_token(token)
    message_id = params.get("message_id")
    if isinstance(message_id, dict):  # the whole message object ({{steps.s1.latest}})
        message_id = message_id.get("id")
    message_id = str(message_id or "").strip()
    if not message_id:
        raise StepError(400, "Paramètre obligatoire manquant « message_id » (aucun mail trouvé ?)")
    wanted = _norm(params.get("name_contains")).strip()
    attachments = [a for a in _graph_call(microsoft_graph.list_attachments, token, message_id) if not a.get("isInline")]
    match = next((a for a in attachments if not wanted or wanted in _norm(a.get("name"))), None)
    if match is None:
        names = ", ".join(a.get("name") or "?" for a in attachments) or "aucune"
        raise StepError(404, f"Aucune pièce jointe ne correspond à « {params.get('name_contains') or '*'} » (pièces jointes : {names})")
    full = _graph_call(microsoft_graph.get_attachment, token, message_id, match["id"])
    if not full.get("contentBytes"):
        raise StepError(422, f"La pièce jointe « {match.get('name')} » n'est pas un fichier téléchargeable")
    data = base64.b64decode(full["contentBytes"])
    f = store_file(db, user.username, match.get("name") or "piece-jointe", data, content_type=match.get("contentType"), run_id=run.id)
    return {"file": file_meta(f)}


def _sends_in_run(db: Session, run_id: str) -> int:
    return db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == MAIL_ACTION, AuditLog.entity_id == run_id)) or 0


def _attachment_ids(value: Any) -> list[Any]:
    if value in (None, "", []):
        return []
    return value if isinstance(value, list) else [value]


def mail_send(db: Session, user: User, run: AutomationRun, params: dict, token: str | None) -> dict:
    to = parse_recipients(params.get("to"))
    if not to:
        raise StepError(400, "Aucun destinataire (paramètre « to »)")
    cc = parse_recipients(params.get("cc"))
    subject = str(params.get("subject") or "").strip()
    if not subject:
        raise StepError(400, "Paramètre obligatoire manquant « subject »")
    body = params.get("body")
    body = json.dumps(body, ensure_ascii=False, indent=2) if isinstance(body, (dict, list)) else str(body or "")
    is_html = _bool(params.get("body_is_html"))
    files = [owned_file(db, user.username, fid) for fid in _attachment_ids(params.get("attachments"))]
    total = sum(f.size for f in files)
    if total > MAX_MAIL_ATTACHMENTS_BYTES:
        raise StepError(413, f"Pièces jointes trop volumineuses pour un envoi ({total // 1024} Ko, maximum 3 Mo)")

    sent_so_far = _sends_in_run(db, run.id)
    if sent_so_far >= catalog.MAX_MAIL_SENDS_PER_RUN:
        raise StepError(429, f"Limite de {catalog.MAX_MAIL_SENDS_PER_RUN} envois de mails par exécution atteinte")

    details = {"to": to, "cc": cc, "subject": subject[:300], "attachments": [f.name for f in files], "dry_run": run.dry_run,
               "automation_id": run.automation_id}
    if run.dry_run:
        audit.log(db, user.username, MAIL_ACTION, None, "automation_run", run.id, **details)
        return {"sent": False, "preview": {"to": to, "cc": cc, "subject": subject, "body": body, "attachments": [f.name for f in files]}}

    token = _require_token(token)
    message = {
        "subject": subject,
        "body": {"contentType": "HTML" if is_html else "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        "attachments": [{
            "@odata.type": "#microsoft.graph.fileAttachment", "name": f.name, "contentType": f.content_type.split(";")[0],
            "contentBytes": base64.b64encode(f.data).decode("ascii"),
        } for f in files],
    }
    _graph_call(microsoft_graph.send_mail, token, message)
    audit.log(db, user.username, MAIL_ACTION, None, "automation_run", run.id, **details)
    return {"sent": True, "to": to}


# ---------------------------------------------------------------- files / documents

class ExtractedTable(BaseModel):
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]


def _document_text(f: AutomationFile) -> str:
    ext = documents.extension(f.name)
    if ext != ".pdf" and ext not in _TEXT_EXTENSIONS and not f.content_type.startswith("text/"):
        raise StepError(415, f"Impossible de lire un tableau dans « {f.name} » (formats : xlsx, csv, docx, pdf, texte)")
    try:
        return knowledge.extract_text(f.name, f.data)
    except Exception as exc:  # noqa: BLE001 — pypdf raises many types on damaged files
        raise StepError(422, f"Fichier illisible « {f.name} » : {exc}") from exc


def extract_table(db: Session, user: User, params: dict, llm_quota: Callable[[], None]) -> dict:
    f = owned_file(db, user.username, _required(params, "file_id"))
    sheet = str(params.get("sheet") or "").strip() or None
    try:
        table = documents.extract_table_structured(f.name, f.data, sheet)
    except documents.DocumentError as exc:
        raise StepError(422, str(exc)) from exc
    if table is not None:
        return table

    text = _document_text(f).strip()
    if not text or not text.replace("[page", "").strip():
        raise StepError(422, f"Aucun texte exploitable dans « {f.name} » (document scanné ?)")
    if not settings.use_llm:
        raise StepError(503, "L'IA est indisponible : impossible d'extraire un tableau de ce type de document")
    llm_quota()
    result, used = llm.structured(
        schema=ExtractedTable, tier="agent", thinking=False, max_tokens=8192,
        system="Tu extrais des données tabulaires de documents pour un outil d'automatisation. Tu recopies les valeurs exactement, sans rien inventer.",
        context=text[:30000],
        task=("Extrais du document LE tableau principal (liste d'enregistrements : personnes, lignes, articles...). "
              "columns = les intitulés de colonnes tels qu'écrits (ou des noms courts et explicites s'il n'y en a pas) ; "
              "rows = une liste par enregistrement, valeurs dans l'ordre des colonnes, null si absente. "
              "S'il n'y a aucun tableau ni liste d'enregistrements, renvoie columns=[] et rows=[]."),
        fallback=lambda: ExtractedTable(columns=[], rows=[]),
    )
    if not used:
        raise StepError(503, "L'IA n'a pas pu extraire le tableau (service indisponible), réessayez plus tard")
    return documents.rows_from_matrix(result.columns, result.rows)


def _output_name(template: AutomationFile, requested: Any, data: dict) -> str:
    ext = documents.extension(template.name)
    stem = PurePath(template.name).stem
    name = str(requested or "").strip()
    if not name:
        first = next((documents.display(v) for v in data.values() if documents.display(v).strip()), "")
        name = f"{stem} - {first}" if first else stem
    name = safe_name(name, stem)
    if documents.extension(name) != ext:
        name = f"{name}{ext}"
    return name


def fill_template(db: Session, user: User, run: AutomationRun, params: dict) -> dict:
    template = owned_file(db, user.username, _required(params, "template_file_id"))
    data = params.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        raise StepError(400, "Le paramètre « data » doit être un objet {champ: valeur} (ex. {{item}})")
    try:
        filled, missing = documents.fill_template(template.name, template.data, data)
    except documents.DocumentError as exc:
        raise StepError(422, str(exc)) from exc
    name = _output_name(template, params.get("output_name"), data)
    f = store_file(db, user.username, name, filled, content_type=template.content_type, run_id=run.id)
    return {"file": file_meta(f), "missing": missing}


def file_create(db: Session, user: User, run: AutomationRun, params: dict) -> dict:
    name = safe_name(_required(params, "name"))
    content = params.get("content")
    if content is None:
        raise StepError(400, "Paramètre obligatoire manquant « content »")
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    f = store_file(db, user.username, name, str(content).encode("utf-8"), content_type=params.get("content_type") or None, run_id=run.id)
    return {"file": file_meta(f)}


# ---------------------------------------------------------------- AI

_AI_SYSTEM = (
    "Tu es le moteur d'une automatisation DeliveryOps. Applique l'instruction aux données fournies et produis "
    "UNIQUEMENT le résultat demandé, sans préambule ni commentaire. Réponds en français sauf si l'instruction ou "
    "les données demandent une autre langue. N'invente aucune information absente des données."
)


def ai_generate(params: dict, llm_quota: Callable[[], None]) -> dict:
    instruction = str(_required(params, "instruction"))
    data = params.get("input")
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False, indent=2)
    data = str(data or "")[:30000]
    as_json = str(params.get("output") or "text").lower() == "json"
    if not settings.use_llm:
        raise StepError(503, "L'IA est indisponible (mode hors ligne)")
    llm_quota()
    system = _AI_SYSTEM + (" Réponds par un unique objet JSON valide." if as_json else "")
    user_msg = f"Instruction :\n{instruction}" + (f"\n\nDonnées :\n<donnees>\n{data}\n</donnees>" if data else "")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
    extra: dict = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    if as_json and settings.llm_json_mode in ("json_object", "json_schema"):
        extra["response_format"] = {"type": "json_object"}
    model = llm.model_for("agent")
    try:
        try:
            response = llm.chat("automation_ai_generate", model=model, messages=messages, **extra)
        except openai.BadRequestError:  # model rejects chat_template_kwargs / response_format: plain call
            response = llm.chat("automation_ai_generate", model=model, messages=messages)
    except llm.API_ERRORS as exc:
        raise StepError(503, f"L'IA est indisponible : {type(exc).__name__}") from exc
    content = llm.clean_text(response.choices[0].message.content)
    if not as_json:
        if not content:
            raise StepError(502, "Réponse vide de l'IA")
        return {"text": content}
    try:
        return {"data": llm.extract_json(content)}
    except (json.JSONDecodeError, ValueError) as exc:
        raise StepError(502, "L'IA n'a pas renvoyé de JSON valide") from exc


_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def ai_vision(db: Session, user: User, params: dict, llm_quota: Callable[[], None]) -> dict:
    """Ask the vision model a question about an image (typically a desktop.screenshot): read what a
    notification says, find a value shown in an app with no API, check a page's state…"""
    f = owned_file(db, user.username, _required(params, "file_id"))
    ctype = f.content_type.split(";")[0].strip().lower()
    if ctype not in _IMAGE_TYPES:
        raise StepError(400, f"ai.vision attend une image (reçu {ctype or 'type inconnu'})")
    question = str(_required(params, "question"))[:2000]
    if not settings.use_llm:
        raise StepError(503, "L'IA est indisponible (mode hors ligne)")
    llm_quota()
    content = [
        {"type": "text", "text": f"{question}\n\nRéponds en français, précisément et brièvement, uniquement d'après l'image. "
                                 "Si l'information n'est pas visible, dis-le."},
        {"type": "image_url", "image_url": {"url": f"data:{ctype};base64," + base64.b64encode(f.data).decode("ascii")}},
    ]
    try:
        response = llm.chat("automation_ai_vision", model=settings.llm_model_vision,
                            messages=[{"role": "user", "content": content}], max_tokens=800, temperature=0.1)
    except llm.API_ERRORS as exc:
        raise StepError(503, f"L'IA de vision est indisponible : {type(exc).__name__}") from exc
    answer = llm.clean_text(response.choices[0].message.content)
    if not answer:
        raise StepError(502, "Réponse vide de l'IA de vision")
    return {"answer": answer}


# ---------------------------------------------------------------- dispatch

def execute(db: Session, user: User, run: AutomationRun, step_type: str, params: dict, *,
            graph_token: str | None, llm_quota: Callable[[], None]) -> dict:
    if step_type not in catalog.SERVER_STEPS:
        raise StepError(400, f"L'étape « {step_type} » ne s'exécute pas sur le serveur")
    params = params or {}
    if step_type == "mail.search":
        return mail_search(params, graph_token)
    if step_type == "mail.get_attachment":
        return mail_get_attachment(db, user, run, params, graph_token)
    if step_type == "file.extract_table":
        return extract_table(db, user, params, llm_quota)
    if step_type == "doc.fill_template":
        return fill_template(db, user, run, params)
    if step_type == "mail.send":
        return mail_send(db, user, run, params, graph_token)
    if step_type == "ai.generate":
        return ai_generate(params, llm_quota)
    if step_type == "file.create":
        return file_create(db, user, run, params)
    if step_type == "ai.vision":
        return ai_vision(db, user, params, llm_quota)
    raise StepError(400, f"Étape non prise en charge « {step_type} »")
