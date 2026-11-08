"""Runs workflows in background threads and applies human decisions (Human-in-the-Loop)."""

import logging
import threading
from datetime import datetime, timezone

from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, knowledge
from app.agents.orchestrator import GATED_KINDS, graph
from app.db import SessionLocal
from app.models import AgentRun, BacklogItem, Meeting, Notification, Recommendation, Ticket

log = logging.getLogger(__name__)
_run_locks: dict[str, threading.Lock] = {}


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _execute(run_id: str, payload) -> None:
    lock = _run_locks.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return  # already running (e.g. two approvals landing at once)
    try:
        g = graph()
        g.invoke(payload, _config(run_id))
        snapshot = g.get_state(_config(run_id))
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            if snapshot.next:  # stopped on an interrupt
                run.status = "awaiting_approval"
                db.add(Notification(perimeter_id=run.perimeter_id, kind="approval_required", severity="high", fingerprint=f"approval:{run_id}",
                                    title=f"Validation requise : {run.workflow}", body="Des livrables attendent une décision pour que le workflow se termine.", link=f"/runs/{run_id}"))
            else:
                run.status = "completed"
                run.output = snapshot.values.get("final")
                run.ended_at = datetime.now(timezone.utc)
                audit.log(db, "orchestrator", "run.completed", run.perimeter_id, "run", run_id)
                if run.workflow != "ask":
                    db.add(Notification(perimeter_id=run.perimeter_id, kind="run_completed", severity="low", fingerprint=f"completed:{run_id}",
                                        title=f"Workflow terminé : {run.workflow}", body="Livrables consolidés disponibles.", link=f"/runs/{run_id}"))
            db.commit()
    except Exception as exc:
        log.exception("run %s failed", run_id)
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            run.status, run.error, run.ended_at = "failed", str(exc), datetime.now(timezone.utc)
            db.commit()
    finally:
        lock.release()


def start_run(db: Session, perimeter_id: str, workflow: str, params: dict, user: str, background: bool = True) -> AgentRun:
    run = AgentRun(perimeter_id=perimeter_id, workflow=workflow, params=params, requested_by=user)
    db.add(run)
    audit.log(db, user, "run.started", perimeter_id, "run", run.id, workflow=workflow, params=params)
    db.commit()
    payload = {"run_id": run.id, "perimeter_id": perimeter_id, "workflow": workflow, "params": params, "completed": [], "results": {}}
    if background:
        threading.Thread(target=_execute, args=(run.id, payload), daemon=True).start()
    else:
        _execute(run.id, payload)
        db.refresh(run)
    return run


def _apply(db: Session, rec: Recommendation, user: str) -> str | None:
    """Side effects of an approved recommendation. Returns a short description for the audit log."""
    if rec.kind == "staffing":
        for a in rec.payload.get("assignments", []):
            item = db.get(BacklogItem, a["backlog_id"])
            if item and item.perimeter_id == rec.perimeter_id and not item.assignee_id:
                item.assignee_id = a["member_id"]
        return f"{len(rec.payload.get('assignments', []))} affectation(s) appliquée(s)"
    if rec.kind == "problem":
        n = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.perimeter_id == rec.perimeter_id, Ticket.kind == "problem")) or 0
        ticket = Ticket(perimeter_id=rec.perimeter_id, ref=f"PRB{1000 + n + 1}", kind="problem", priority="P2", application_id=rec.payload.get("application_id"),
                        category="problem", title=rec.payload.get("label", rec.title)[:300], description=f"{rec.detail}\nIncidents liés : {', '.join(rec.payload.get('refs', []))}",
                        status="open", opened_at=datetime.now(timezone.utc))
        db.add(ticket)
        return f"Problème {ticket.ref} créé"
    if rec.kind == "onboarding_plan" and rec.payload.get("app_cards"):
        def bullets(items):
            return "\n".join(f"- {i}" for i in items) or "- (non renseigné)"

        for card in rec.payload["app_cards"]:
            content = (
                f"# Fiche applicative — {card['application']}\n\n{card['purpose']}\n\n# Flux principaux\n{bullets(card['key_flows'])}\n\n"
                f"# Exploitation\n{bullets(card['operations'])}\n\n# Incidents connus\n{bullets(card['known_issues'])}\n\n"
                f"# Points de vigilance\n{bullets(card['watch_points'])}"
            )
            knowledge.ingest_document(db, rec.perimeter_id, f"Fiche {card['application']}.md", "living", content)
        return f"{len(rec.payload['app_cards'])} fiche(s) applicative(s) ajoutée(s) à la base de connaissance"
    if rec.kind == "contract_rules":
        from app.models import Perimeter

        perimeter = db.get(Perimeter, rec.perimeter_id)
        perimeter.contract = rec.payload["contract"]
        return "Règles contractuelles appliquées au périmètre"
    if rec.kind == "minutes":
        meeting = db.get(Meeting, rec.payload.get("meeting_id"))
        if meeting and meeting.minutes:
            m = meeting.minutes
            content = (
                f"# Compte-rendu — {meeting.title} ({meeting.held_at:%Y-%m-%d})\n\n{m['summary']}\n\n# Décisions\n" + "\n".join(f"- {d}" for d in m["decisions"])
                + "\n\n# Actions\n" + "\n".join(f"- {a['action']} ({a['owner']}, {a['due']})" for a in m["actions"])
                + "\n\n# Risques\n" + "\n".join(f"- {r['title']} : {r['description']}" for r in m["risks"])
            )
            knowledge.ingest_document(db, rec.perimeter_id, f"CR {meeting.title}.md", "minutes", content)
            return "Compte-rendu ajouté à la base de connaissance"
    return None


def decide(db: Session, rec: Recommendation, approve: bool, user: str, comment: str = "") -> Recommendation:
    rec.status = "approved" if approve else "rejected"
    rec.decided_by, rec.decided_at, rec.decision_comment = user, datetime.now(timezone.utc), comment
    effect = _apply(db, rec, user) if approve else None
    audit.log(db, user, f"recommendation.{rec.status}", rec.perimeter_id, "recommendation", rec.id, title=rec.title, agent=rec.agent, impact=rec.impact, comment=comment, effect=effect)
    db.commit()

    if rec.run_id:
        run = db.get(AgentRun, rec.run_id)
        still_pending = db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.run_id == rec.run_id, Recommendation.kind.in_(GATED_KINDS), Recommendation.status == "pending"))
        if run.status == "awaiting_approval" and not still_pending:
            run.status = "running"
            db.commit()
            threading.Thread(target=_execute, args=(run.id, Command(resume={"by": user})), daemon=True).start()
    return rec
