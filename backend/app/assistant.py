"""Personal assistant workspace: the onboarding scan indexes the signed-in user's Microsoft 365
data and local files into a per-user knowledge perimeter, so the RAG-based agents (document
generator, call assistant, free-form ask) work over *their* data. Delivery perimeters stay separate;
this is the user's own space.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import knowledge
from app.models import PERSONAL_CLIENT, Chunk, Client, Document, Perimeter
# Minimal contract so per-perimeter code that reads `.contract` never breaks on the personal space.
PERSONAL_CONTRACT = {"currency": "EUR", "hours_per_day": 7, "scope": {"out_of_scope": []}, "kpi_targets": {}}


def _key(user) -> str:
    raw = (getattr(user, "subject", "") or user.username or user.email or "anon")
    return "me-" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def workspace(db: Session, user) -> Perimeter:
    """Return (creating if needed) the personal knowledge workspace for this user."""
    if db.get(Client, PERSONAL_CLIENT) is None:
        db.add(Client(id=PERSONAL_CLIENT, name="Espaces personnels", sector="internal"))
        db.flush()
    pid = _key(user)
    p = db.get(Perimeter, pid)
    if p is None:
        p = Perimeter(id=pid, client_id=PERSONAL_CLIENT, name=f"Espace de {user.name or user.username}",
                      description="Espace personnel — données Microsoft 365 et fichiers locaux indexés.",
                      contract=dict(PERSONAL_CONTRACT))
        db.add(p)
        db.flush()
    return p


def _replace_document(db: Session, pid: str, filename: str, doc_type: str, content: str) -> None:
    """Re-index one logical source, dropping its previous chunks so a re-scan stays idempotent."""
    old = db.scalars(select(Document).where(Document.perimeter_id == pid, Document.filename == filename)).all()
    for d in old:
        db.execute(delete(Chunk).where(Chunk.document_id == d.id))
        db.delete(d)
    db.flush()
    if content.strip():
        knowledge.ingest_document(db, pid, filename, doc_type, content)


# ---------------------------------------------------------------- Microsoft 365 → knowledge

def _fmt_mail(items: list[dict]) -> str:
    lines = ["# Boîte mail Outlook (messages récents)"]
    for m in items:
        lines.append(f"## {m.get('subject') or '(sans objet)'}")
        lines.append(f"De : {m.get('from') or '?'} — reçu {m.get('received') or '?'}")
        lines.append(m.get("preview") or "")
        lines.append("")
    return "\n".join(lines)


def _fmt_calendar(items: list[dict]) -> str:
    lines = ["# Agenda Outlook (réunions récentes et à venir)"]
    for e in items:
        lines.append(f"## {e.get('subject') or '(réunion)'}")
        lines.append(f"Quand : {e.get('start') or '?'} → {e.get('end') or '?'} — Lieu : {e.get('location') or '—'} — Organisateur : {e.get('organizer') or '?'}")
        attendees = e.get("attendees")
        if isinstance(attendees, list) and attendees:
            shown = ", ".join(attendees[:20]) + (f" (+{len(attendees) - 20})" if len(attendees) > 20 else "")
            lines.append(f"Participants : {shown}")
        if e.get("preview"):
            lines.append(str(e["preview"]))
        if e.get("join_url"):
            lines.append(f"Lien : {e['join_url']}")
        lines.append("")
    return "\n".join(lines)


def _fmt_generic(title: str, items: list[dict], fields: list[str]) -> str:
    lines = [f"# {title}"]
    for it in items:
        lines.append("- " + " | ".join(f"{f}: {it.get(f)}" for f in fields if it.get(f) is not None))
    return "\n".join(lines)


def ingest_microsoft(db: Session, pid: str, overview: dict) -> dict:
    """Index the aggregated Microsoft overview (from microsoft_graph.overview) into the workspace."""
    src = overview.get("sources", {})
    counts: dict[str, int] = {}

    def take(name, formatter):
        s = src.get(name) or {}
        items = s.get("items") or s.get("lists") or []
        if s.get("error") or not items:
            return
        _replace_document(db, pid, f"microsoft/{name}", "microsoft", formatter(items))
        counts[name] = len(items)

    take("mail", _fmt_mail)
    take("calendar", _fmt_calendar)
    take("chats", lambda it: _fmt_generic("Conversations Teams", it, ["topic", "type", "last_message", "last_at"]))
    take("files", lambda it: _fmt_generic("Fichiers récents (OneDrive/SharePoint)", it, ["name", "modified", "by", "url"]))
    take("teams", lambda it: _fmt_generic("Équipes Teams", it, ["name", "description"]))
    take("sites", lambda it: _fmt_generic("Sites SharePoint suivis", it, ["name", "url"]))
    # tasks come as {lists:[{name, tasks:[...]}]}
    tasks = src.get("tasks") or {}
    if not tasks.get("error") and tasks.get("lists"):
        flat = [{"list": l["name"], **t} for l in tasks["lists"] for t in l.get("tasks", [])]
        if flat:
            _replace_document(db, pid, "microsoft/tasks", "microsoft", _fmt_generic("Tâches (To Do / Planner)", flat, ["list", "title", "due", "importance"]))
            counts["tasks"] = len(flat)
    db.commit()
    return counts


def ingest_mail_bodies(db: Session, pid: str, mails: list[dict]) -> int:
    """Index each recent mail as its OWN document (microsoft/mail/<id>) with its full body, so a
    question about any word in any mail hits that mail precisely and cites it. Previous per-mail
    documents are dropped first so a re-scan stays idempotent."""
    old = db.scalars(select(Document).where(Document.perimeter_id == pid, Document.filename.like("microsoft/mail/%"))).all()
    for d in old:
        db.execute(delete(Chunk).where(Chunk.document_id == d.id))
        db.delete(d)
    db.flush()
    n = 0
    for m in mails:
        if not m.get("id"):
            continue
        received = (m.get("received") or "")[:16].replace("T", " ")
        content = (f"# Mail : {m.get('subject')}\n"
                   f"De : {m.get('from')} <{m.get('from_email') or ''}> — reçu le {received or '?'}\n\n"
                   f"{m.get('body') or ''}")
        title = f"microsoft/mail/{m['id'][-24:]} — {str(m.get('subject'))[:80]}"
        knowledge.ingest_document(db, pid, title, "microsoft", content)
        n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- local files → knowledge

def ingest_local(db: Session, pid: str, files: list[tuple[str, bytes]]) -> dict:
    indexed, skipped = 0, 0
    for filename, data in files:
        try:
            text = knowledge.extract_text(filename, data)
        except Exception:  # noqa: BLE001 — a single unreadable file must not fail the whole scan
            skipped += 1
            continue
        if text.strip():
            _replace_document(db, pid, f"local/{filename}", "localfile", text)
            indexed += 1
        else:
            skipped += 1
    db.commit()
    return {"indexed": indexed, "skipped": skipped}


# ---------------------------------------------------------------- status

def index_stats(db: Session, pid: str) -> dict:
    rows = db.execute(
        select(Document.doc_type, func.count(Document.id)).where(Document.perimeter_id == pid).group_by(Document.doc_type)
    ).all()
    by_type = {t: n for t, n in rows}
    chunks = db.scalar(select(func.count(Chunk.id)).where(Chunk.perimeter_id == pid)) or 0
    return {
        "documents": sum(by_type.values()),
        "chunks": chunks,
        "by_type": by_type,
        "ready": sum(by_type.values()) > 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
