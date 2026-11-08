import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app import audit, contract_extraction, knowledge, runner
from app.agents import meeting_intelligence
from app.agents.knowledge_transfer import build_map
from app.agents.orchestrator import AGENT_LABELS, WORKFLOWS
from app.analytics import capacity as capacity_engine
from app.analytics.billing import prebill
from app.analytics.kpi import service_kpis
from app.auth import DEV_USERS, User, current_user, perimeter_guard, require_decider, require_permission
from app.config import get_settings
from app.db import get_db
from app.models import (AgentRun, Application, AuditLog, BacklogItem, Client, Dependency, Document, Meeting, Perimeter,
                        Recommendation, TeamMember, Ticket, Timesheet)

router = APIRouter(prefix="/api")
settings = get_settings()


def row(obj, *exclude: str) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in exclude}


def visible_perimeters(db: Session, user: User) -> list[Perimeter]:
    return [p for p in db.scalars(select(Perimeter)).all() if user.can_see(p.client_id)]


# ------------------------------------------------------------------ platform

@router.get("/me")
def me(user: User = Depends(current_user)):
    return {**user.public(), "auth_mode": settings.auth_mode, "llm": "api" if settings.use_llm else "offline", "llm_model": settings.llm_model_agent if settings.use_llm else None}


@router.get("/config")
def public_config():
    """Unauthenticated bootstrap config for the SPA."""
    issuer = settings.keycloak_public_url or settings.keycloak_issuer
    base, _, realm = issuer.partition("/realms/")
    from app import mailer, oidc

    native = settings.auth_mode == "native"
    return {
        "auth_mode": settings.auth_mode,
        "password_reset_email": native and "password" in settings.sign_in_methods and mailer.configured(),
        # Native mode: the sign-in screen offers these (password form and/or Microsoft / Google buttons)
        "sign_in": {"password": "password" in settings.sign_in_methods,
                    "providers": [{"id": p, "label": oidc.LABELS[p]} for p in oidc.external_providers()]} if native else None,
        "keycloak": {"url": base, "realm": realm, "clientId": settings.keycloak_client_id} if settings.auth_mode == "keycloak" else None,
        "microsoft": {
            "clientId": settings.microsoft_client_id,
            "authority": f"https://login.microsoftonline.com/{settings.microsoft_tenant_id}",
            "scopes": settings.microsoft_graph_scopes.split(),
            "loginScopes": settings.microsoft_login_scopes.split(),
            "basicScopes": settings.microsoft_graph_scopes_basic.split(),
            "adminScopes": settings.microsoft_graph_scopes_admin.split(),
            "automationScopes": settings.microsoft_graph_scopes_automation.split(),
        } if settings.microsoft_enabled else None,
    }


@router.get("/dev/users")
def dev_users():
    if settings.auth_mode != "dev":
        raise HTTPException(404)
    return [u.__dict__ for u in DEV_USERS.values()]


@router.get("/workflows")
def workflows():
    return {"workflows": [{"id": k, **v, "agent_labels": [AGENT_LABELS[a] for a in v["agents"]]} for k, v in WORKFLOWS.items()], "agents": AGENT_LABELS}


@router.get("/portfolio")
def portfolio(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for p in visible_perimeters(db, user):
        tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == p.id)).all()
        kpis = service_kpis(tickets, p.contract)
        recs = db.scalars(select(Recommendation).where(Recommendation.perimeter_id == p.id)).all()
        out.append({
            "id": p.id, "name": p.name, "client": p.client.name, "client_id": p.client_id, "description": p.description,
            "project": p.project.name if p.project else None, "project_id": p.project_id,
            "overall_status": kpis["overall_status"], "scorecard": kpis["scorecard"],
            "monthly": kpis["monthly"],
            "applications": len(db.scalars(select(Application.id).where(Application.perimeter_id == p.id)).all()),
            "backlog_open": kpis["backlog_open"],
            "pending_decisions": sum(1 for r in recs if r.status == "pending"),
            "high_risks": sum(1 for r in recs if r.status == "pending" and r.impact == "high" and r.kind in ("risk", "drift", "sla", "scope", "capacity_alert")),
        })
    return out


# ------------------------------------------------------------------ perimeters

@router.get("/clients")
def clients(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [row(c) for c in db.scalars(select(Client)).all() if user.can_see(c.id)]


@router.get("/perimeters/{perimeter_id}")
def perimeter_detail(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    team = db.scalars(select(TeamMember).where(TeamMember.perimeter_id == p.id)).all()
    return {
        **row(p), "client": p.client.name, "project": p.project.name if p.project else None,
        "applications": [row(a) for a in db.scalars(select(Application).where(Application.perimeter_id == p.id))],
        "team": [row(m) for m in team],
        "documents": [row(d, "content") for d in db.scalars(select(Document).where(Document.perimeter_id == p.id).order_by(Document.uploaded_at.desc()))],
    }


@router.put("/perimeters/{perimeter_id}/contract")
def update_contract(contract: dict, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    p.contract = contract
    audit.log(db, user.username, "contract.updated", p.id, "perimeter", p.id)
    db.commit()
    return p.contract


@router.post("/perimeters/{perimeter_id}/contract/extract")
def extract_contract(p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    rec = contract_extraction.extract(db, p, user.username)
    if not rec:
        raise HTTPException(400, "Aucun document de type 'contract' pour ce périmètre")
    return row(rec)


@router.get("/perimeters/{perimeter_id}/kpis")
def kpis(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == p.id)).all()
    data = service_kpis(tickets, p.contract)
    apps = {a.id: a.name for a in db.scalars(select(Application).where(Application.perimeter_id == p.id))}
    data["top_applications"] = [{"application": apps.get(a, a), "incidents": n} for a, n in data["top_applications"]]
    return data


@router.get("/perimeters/{perimeter_id}/cartography")
def cartography(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    apps = db.scalars(select(Application).where(Application.perimeter_id == p.id)).all()
    deps = db.scalars(select(Dependency).where(Dependency.perimeter_id == p.id)).all()
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == p.id)).all()
    return build_map(apps, deps, tickets)


@router.get("/perimeters/{perimeter_id}/tickets")
def tickets(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db), status: str | None = None, limit: int = 200):
    stmt = select(Ticket).where(Ticket.perimeter_id == p.id).order_by(Ticket.opened_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return [row(t) for t in db.scalars(stmt)]


@router.get("/perimeters/{perimeter_id}/backlog")
def backlog(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return [row(b) for b in db.scalars(select(BacklogItem).where(BacklogItem.perimeter_id == p.id).order_by(BacklogItem.priority))]


# ------------------------------------------------------------------ knowledge

@router.post("/perimeters/{perimeter_id}/documents")
async def upload(file: UploadFile = File(...), doc_type: str = Form("transfer"), p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read()
    content = knowledge.extract_text(file.filename, data)
    if not content.strip():
        raise HTTPException(400, "Aucun texte exploitable dans ce fichier")
    doc = knowledge.ingest_document(db, p.id, file.filename, doc_type, content)
    audit.log(db, user.username, "document.uploaded", p.id, "document", doc.id, filename=file.filename, doc_type=doc_type)
    db.commit()
    return row(doc, "content")


class SearchIn(BaseModel):
    query: str
    k: int = 5


@router.post("/perimeters/{perimeter_id}/search")
def search(body: SearchIn, p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    hits = knowledge.search(db, p.id, body.query, body.k, rerank=settings.knowledge_rerank)
    return [h.__dict__ | {"cite": h.cite()} for h in hits]


# ------------------------------------------------------------------ direct simulations (no LLM, instant)

@router.get("/perimeters/{perimeter_id}/billing/preview")
def billing_preview(year: int, month: int, p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return prebill(p.contract, db.scalars(select(TeamMember).where(TeamMember.perimeter_id == p.id)).all(), db.scalars(select(Ticket).where(Ticket.perimeter_id == p.id)).all(), db.scalars(select(Timesheet).where(Timesheet.perimeter_id == p.id)).all(), year, month)


class ScenarioIn(BaseModel):
    incident_growth_pct: float = 0
    add_fte: float = 0
    remove_member_ids: list[str] = []
    horizon_weeks: int = 8


@router.post("/perimeters/{perimeter_id}/capacity/simulate")
def simulate(body: ScenarioIn, p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    q = lambda m: db.scalars(select(m).where(m.perimeter_id == p.id)).all()  # noqa: E731
    scenario = body.model_dump(exclude={"horizon_weeks"})
    return capacity_engine.analyze(q(TeamMember), q(Ticket), q(Timesheet), q(BacklogItem), horizon_weeks=body.horizon_weeks, scenario=scenario)


# ------------------------------------------------------------------ orchestrated runs

class RunIn(BaseModel):
    workflow: str
    params: dict = {}


@router.post("/perimeters/{perimeter_id}/runs")
def create_run(body: RunIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.workflow not in WORKFLOWS:
        raise HTTPException(400, f"Unknown workflow {body.workflow}")
    if body.workflow == "ask" and not body.params.get("question"):
        raise HTTPException(400, "params.question is required")
    if body.workflow == "estimation" and not body.params.get("request"):
        raise HTTPException(400, "params.request is required")
    run = runner.start_run(db, p.id, body.workflow, body.params, user.username)
    return row(run)


@router.get("/perimeters/{perimeter_id}/runs")
def list_runs(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return [row(r, "output", "steps") for r in db.scalars(select(AgentRun).where(AgentRun.perimeter_id == p.id).order_by(AgentRun.started_at.desc()).limit(50))]


def _guard_entity(db: Session, user: User, perimeter_id: str) -> None:
    p = db.get(Perimeter, perimeter_id)
    if not p or not user.can_see(p.client_id):
        raise HTTPException(404, "Not found")


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404)
    _guard_entity(db, user, run.perimeter_id)
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.created_at)).all()
    return {**row(run), "recommendations": [row(r) for r in recs]}


# ------------------------------------------------------------------ human-in-the-loop

@router.get("/recommendations")
def list_recommendations(perimeter_id: str | None = None, status: str | None = None, run_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    allowed = {p.id: p for p in visible_perimeters(db, user)}
    stmt = select(Recommendation).where(Recommendation.perimeter_id.in_(allowed)).order_by(Recommendation.created_at.desc()).limit(300)
    if perimeter_id:
        stmt = stmt.where(Recommendation.perimeter_id == perimeter_id)
    if status:
        stmt = stmt.where(Recommendation.status == status)
    if run_id:
        stmt = stmt.where(Recommendation.run_id == run_id)
    order = {"high": 0, "medium": 1, "low": 2}
    recs = sorted(db.scalars(stmt).all(), key=lambda r: (order.get(r.impact, 3), -r.created_at.timestamp()))
    return [row(r) | {"perimeter": allowed[r.perimeter_id].name, "client": allowed[r.perimeter_id].client.name} for r in recs]


class DecisionIn(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/recommendations/{rec_id}/decision")
def decision(rec_id: str, body: DecisionIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    rec = db.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(404)
    _guard_entity(db, user, rec.perimeter_id)
    if rec.status not in ("pending", "info"):
        raise HTTPException(409, f"Already {rec.status} by {rec.decided_by}")
    if not body.approve and not body.comment.strip():
        raise HTTPException(400, "A comment is required to reject a recommendation")
    return row(runner.decide(db, rec, body.approve, user.username, body.comment))


def _audit_scope(db: Session, user: User):
    """Rows the caller may read: their perimeters, plus org-level admin actions (perimeter_id NULL:
    governance, users, roles, policies) for all-client or admin-level users."""
    allowed = AuditLog.perimeter_id.in_([p.id for p in visible_perimeters(db, user)])
    if "*" in user.clients or user.permissions & {"users.manage", "roles.manage", "governance.manage", "companion.admin"}:
        return or_(allowed, AuditLog.perimeter_id.is_(None))
    return allowed


def _parse_day(value: str | None, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value[:19]) if "T" in value else datetime.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(400, f"Date invalide : {value}")
    if "T" not in value and end:
        d = d.replace(hour=23, minute=59, second=59)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.get("/audit")
def audit_log(response: Response, perimeter_id: str | None = None, q: str = "", actor: str = "", action: str = "",
              since: str | None = None, until: str | None = None, limit: int = 200, offset: int = 0,
              user: User = Depends(require_permission("audit.view")), db: Session = Depends(get_db)):
    """Searchable audit trail. `q` matches actor, action, object and details; X-Total-Count gives the
    full match count for pagination."""
    stmt = select(AuditLog).where(_audit_scope(db, user))
    if perimeter_id:
        stmt = stmt.where(AuditLog.perimeter_id == perimeter_id)
    if actor:
        stmt = stmt.where(AuditLog.actor.ilike(f"%{actor}%"))
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if (start := _parse_day(since)) is not None:
        stmt = stmt.where(AuditLog.ts >= start)
    if (end := _parse_day(until, end=True)) is not None:
        stmt = stmt.where(AuditLog.ts <= end)
    for word in q.split()[:6]:
        like = f"%{word}%"
        stmt = stmt.where(or_(AuditLog.actor.ilike(like), AuditLog.action.ilike(like), AuditLog.entity.ilike(like),
                              AuditLog.entity_id.ilike(like), cast(AuditLog.details, String).ilike(like)))
    response.headers["X-Total-Count"] = str(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    limit = max(1, min(limit, 1000))
    return [row(a) for a in db.scalars(stmt.order_by(AuditLog.ts.desc()).offset(max(0, offset)).limit(limit))]


@router.get("/audit/facets")
def audit_facets(user: User = Depends(require_permission("audit.view")), db: Session = Depends(get_db)):
    """Distinct actions and actors (for the filter dropdowns)."""
    scope = _audit_scope(db, user)
    return {
        "actions": sorted(a for a in db.scalars(select(AuditLog.action).where(scope).distinct()) if a),
        "actors": sorted(a for a in db.scalars(select(AuditLog.actor).where(scope).distinct()) if a),
    }


# ------------------------------------------------------------------ meetings (live assistant)

class MeetingIn(BaseModel):
    title: str


class SegmentIn(BaseModel):
    speaker: str = "Client"
    text: str
    auto_answer: bool = True


class QuestionIn(BaseModel):
    question: str


def _meeting(db: Session, user: User, meeting_id: str) -> Meeting:
    m = db.get(Meeting, meeting_id)
    if not m:
        raise HTTPException(404)
    _guard_entity(db, user, m.perimeter_id)
    return m


@router.post("/perimeters/{perimeter_id}/meetings")
def create_meeting(body: MeetingIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = Meeting(perimeter_id=p.id, title=body.title)
    db.add(m)
    audit.log(db, user.username, "meeting.started", p.id, "meeting", m.id, title=body.title)
    db.commit()
    return row(m)


@router.get("/perimeters/{perimeter_id}/meetings")
def list_meetings(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return [row(m, "transcript") for m in db.scalars(select(Meeting).where(Meeting.perimeter_id == p.id).order_by(Meeting.held_at.desc()))]


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return row(_meeting(db, user, meeting_id))


@router.post("/meetings/{meeting_id}/segments")
def add_segment(meeting_id: str, body: SegmentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = _meeting(db, user, meeting_id)
    m.transcript = (m.transcript + "\n" if m.transcript else "") + f"{body.speaker}: {body.text.strip()}"
    db.commit()
    result = {"question_detected": False}
    if body.auto_answer and meeting_intelligence.is_question(body.text):
        result = {"question_detected": True, **meeting_intelligence.answer(db, m.perimeter_id, body.text)}
    return result


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/meetings/{meeting_id}/segments/stream")
def add_segment_stream(meeting_id: str, body: SegmentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Streaming counterpart of `add_segment`: appends the transcript segment the same way, then, if
    it's a detected question, streams the answer as it is generated (SSE) instead of returning it in
    one shot — so the call-assistant UI can render tokens as they arrive."""
    m = _meeting(db, user, meeting_id)
    m.transcript = (m.transcript + "\n" if m.transcript else "") + f"{body.speaker}: {body.text.strip()}"
    db.commit()
    perimeter_id, question = m.perimeter_id, body.text

    def gen():
        if not (body.auto_answer and meeting_intelligence.is_question(question)):
            yield _sse({"type": "done", "question_detected": False})
            return
        yield _sse({"type": "question_detected"})
        for event in meeting_intelligence.answer_stream(db, perimeter_id, question):
            yield _sse(event)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/meetings/{meeting_id}/ask")
def ask_in_meeting(meeting_id: str, body: QuestionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = _meeting(db, user, meeting_id)
    return meeting_intelligence.answer(db, m.perimeter_id, body.question)


@router.post("/meetings/{meeting_id}/transcribe-chunk")
async def transcribe_chunk(meeting_id: str, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Speech-to-text for a short audio chunk captured from system/loopback audio (the "Écouter tout
    l'appel" mode in the call-assistant — see frontend/src/app/pages/call-assistant.ts), which the
    browser's Web Speech API cannot transcribe on its own since it only listens to the microphone.
    Runs local Whisper (app/stt.py) — no cloud STT credential involved. The returned text is NOT
    auto-appended to the transcript here; the frontend feeds it through the same `segments` flow the
    mic path uses, so both paths share one code path for question-detection/answering."""
    from app import stt

    _meeting(db, user, meeting_id)  # 404/403 the same way other meeting endpoints do
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty audio chunk")
    try:
        text = stt.transcribe(data)
    except stt.STTUnavailable as exc:
        raise HTTPException(503, f"Speech-to-text unavailable: {exc}") from exc
    return {"text": text}


@router.post("/meetings/{meeting_id}/end")
def end_meeting(meeting_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = _meeting(db, user, meeting_id)
    if not m.transcript.strip():
        raise HTTPException(400, "Transcript is empty")
    m.status = "ended"
    db.commit()
    return row(runner.start_run(db, m.perimeter_id, "call", {"meeting_id": m.id}, user.username))


@router.post("/perimeters/{perimeter_id}/meetings/import")
async def import_transcript(file: UploadFile = File(...), title: str = Form(""), p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Create an ended meeting from a Teams transcript export (.vtt / .docx / .txt) and generate its minutes."""
    from app import transcripts

    transcript = transcripts.parse(file.filename or "", await file.read())
    if not transcript.strip():
        raise HTTPException(400, "Transcription vide ou format non reconnu")
    m = Meeting(perimeter_id=p.id, title=title.strip() or (file.filename or "Réunion importée").rsplit(".", 1)[0], transcript=transcript, status="ended")
    db.add(m)
    audit.log(db, user.username, "meeting.imported", p.id, "meeting", m.id, filename=file.filename, lines=transcript.count("\n") + 1)
    db.commit()
    run = runner.start_run(db, p.id, "call", {"meeting_id": m.id}, user.username)
    return {"meeting": row(m, "transcript"), "run": row(run)}


@router.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "llm": "api" if settings.use_llm else "mock", "today": date.today().isoformat()}
