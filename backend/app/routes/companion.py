"""Companion API (/api/companion): the user's side (state, activity ingest, proactive suggestions +
feedback, chat, briefs, memories, preferences, privacy) and the governance side (org policy,
aggregate monitoring, per-user erasure). See app/companion.py for the behaviour.

Permissions: `companion.use` for everything personal, `companion.activity` additionally for sending
activity, `companion.admin` for policy / monitoring. Admin views are aggregate-only by design — an
administrator can never read someone's window titles, memories or conversation.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, companion, llm, microsoft_graph, telemetry
from app import policy as company_rules  # `policy` here would read as the companion's own policy
from app.auth import User, require_permission
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.middleware import consume_llm_quota
from app.models import ActivityEvent, Automation, CompanionMemory, CompanionMessage, CompanionProfile, CompanionSuggestion

router = APIRouter(prefix="/api/companion")
settings = get_settings()
_use = require_permission("companion.use")
_admin = require_permission("companion.admin")


def _profile_out(p: CompanionProfile) -> dict:
    return {"display_name": p.display_name, "job_role": p.job_role, "tone": p.tone, "language": p.language,
            "quiet_start": p.quiet_start, "quiet_end": p.quiet_end, "tracking_enabled": p.tracking_enabled,
            "proactive_enabled": p.proactive_enabled, "onboarded": p.onboarded,
            "nudges": [{"kind": k, "label": companion.NUDGE_LABELS[k], "weight": float((p.nudge_weights or {}).get(k, 1.0))} for k in companion.NUDGES]}


def _policy_out(p) -> dict:
    return {"enabled": p.enabled, "activity_tracking_allowed": p.activity_tracking_allowed, "title_capture": p.title_capture,
            "retention_days": p.retention_days, "excluded_apps": p.excluded_apps or [], "excluded_keywords": p.excluded_keywords or [],
            "proactive_allowed": p.proactive_allowed, "max_nudges_per_hour": p.max_nudges_per_hour,
            "memory_learning_allowed": p.memory_learning_allowed, "updated_by": p.updated_by,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def _sugg_out(s: CompanionSuggestion) -> dict:
    return {"id": s.id, "kind": s.kind, "label": companion.NUDGE_LABELS.get(s.kind, s.kind), "title": s.title, "body": s.body,
            "action": s.action or {}, "status": s.status, "created_at": s.created_at.isoformat()}


def _memory_out(m: CompanionMemory) -> dict:
    return {"id": m.id, "kind": m.kind, "content": m.content, "source": m.source, "pinned": m.pinned, "confidence": m.confidence,
            "created_at": m.created_at.isoformat()}


def _refresh_agenda(db: Session, prof: CompanionProfile, token: str | None) -> None:
    """Keep a small structured agenda on the profile whenever a Microsoft token comes along (at most
    every 10 min) — that is what lets "meeting in 15 minutes" fire without re-scanning anything."""
    if not token or not settings.microsoft_enabled:
        return
    fresh = prof.agenda_at and (datetime.now(timezone.utc) - companion._aware(prof.agenda_at)) < timedelta(minutes=10)
    if fresh:
        return
    try:
        with microsoft_graph._client(token) as c:
            cal = microsoft_graph._summarize_events(microsoft_graph._calendar_view(c, days_back=0, days_ahead=3, top=25))
        if not cal.get("error"):
            prof.agenda = [{"subject": e.get("subject"), "start": e.get("start"), "end": e.get("end"), "attendees": e.get("attendees") or [],
                            "join_url": e.get("join_url"), "location": e.get("location")} for e in cal.get("items", [])]
            prof.agenda_at = datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001 — the agenda is a bonus, never a reason to fail the request
        pass


# ---------------------------------------------------------------- state

@router.get("/state")
def state(tz: int = 0, user: User = Depends(_use), x_graph_token: str | None = Header(default=None, alias="X-Graph-Token"), db: Session = Depends(get_db)):
    pol, prof = companion.policy(db), companion.profile(db, user)
    _refresh_agenda(db, prof, x_graph_token)
    new = companion.generate_suggestions(db, user, prof, pol, tz) if prof.onboarded else []
    open_s = db.scalars(select(CompanionSuggestion).where(CompanionSuggestion.owner == prof.owner, CompanionSuggestion.status == "new")
                        .order_by(CompanionSuggestion.score.desc(), CompanionSuggestion.created_at.desc()).limit(6)).all()
    out = {
        "profile": _profile_out(prof), "policy": {k: v for k, v in _policy_out(pol).items() if k in ("enabled", "activity_tracking_allowed", "title_capture", "retention_days", "proactive_allowed", "memory_learning_allowed")},
        "tracking_active": companion.tracking_active(prof, pol),
        "can_track": "companion.activity" in user.permissions,
        "today": companion.today_summary(db, prof.owner, tz) if companion.tracking_active(prof, pol) else None,
        "suggestions": [_sugg_out(s) for s in open_s], "fresh": [s.id for s in new],
        "agenda": [e for e in (prof.agenda or []) if str(e.get("end") or e.get("start") or "") >= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")][:6],
        "memory_count": db.scalar(select(func.count()).select_from(CompanionMemory).where(CompanionMemory.owner == prof.owner)) or 0,
        "llm": settings.use_llm,
    }
    db.commit()
    return out


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    job_role: str | None = None
    tone: str | None = None
    language: str | None = Field(None, max_length=8)
    quiet_start: str | None = Field(None, pattern=r"^(|\d{2}:\d{2})$")
    quiet_end: str | None = Field(None, pattern=r"^(|\d{2}:\d{2})$")
    tracking_enabled: bool | None = None
    proactive_enabled: bool | None = None
    onboarded: bool | None = None
    nudge_weights: dict[str, float] | None = None


@router.put("/profile")
def update_profile(body: ProfileIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    pol, prof = companion.policy(db), companion.profile(db, user)
    data = body.model_dump(exclude_none=True)
    if "job_role" in data and data["job_role"] not in companion.JOB_ROLES:
        raise HTTPException(400, "Rôle inconnu")
    if "tone" in data and data["tone"] not in companion.TONES:
        raise HTTPException(400, "Ton inconnu")
    if data.get("tracking_enabled"):
        if "companion.activity" not in user.permissions or not pol.activity_tracking_allowed:
            raise HTTPException(403, "Le suivi d'activité n'est pas autorisé pour votre compte")
    if "tracking_enabled" in data and data["tracking_enabled"] != prof.tracking_enabled:  # consent changes are audited
        audit.log(db, user.username, "companion.tracking_consent", None, "companion_profile", prof.owner, enabled=data["tracking_enabled"])
    if "nudge_weights" in data:
        merged = dict(prof.nudge_weights or {})
        merged.update({k: max(0.0, min(2.0, float(v))) for k, v in data.pop("nudge_weights").items() if k in companion.NUDGES})
        prof.nudge_weights = merged
    for k, v in data.items():
        setattr(prof, k, v)
    db.commit()
    return _profile_out(prof)


# ---------------------------------------------------------------- activity

class ActivityItem(BaseModel):
    started_at: str
    seconds: int = Field(ge=0, le=14400)
    app: str = Field(max_length=200)
    title: str = Field("", max_length=600)


class ActivityIn(BaseModel):
    events: list[ActivityItem] = Field(default_factory=list, max_length=200)


@router.post("/activity")
def ingest_activity(body: ActivityIn, user: User = Depends(require_permission("companion.activity")), db: Session = Depends(get_db)):
    pol, prof = companion.policy(db), companion.profile(db, user)
    if not companion.tracking_active(prof, pol):
        return {"stored": 0, "tracking_active": False}  # the client stops sampling when it sees this
    stored = companion.record_activity(db, prof.owner, [e.model_dump() for e in body.events], pol)
    db.commit()
    return {"stored": stored, "tracking_active": True}


@router.get("/activity/today")
def activity_today(tz: int = 0, user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    return companion.today_summary(db, prof.owner, tz)


# ---------------------------------------------------------------- suggestions

class DecisionIn(BaseModel):
    status: Literal["accepted", "dismissed", "snoozed", "never"]


@router.post("/suggestions/{sid}/decision")
def decide(sid: str, body: DecisionIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    s = db.get(CompanionSuggestion, sid)
    if s is None or s.owner != prof.owner:
        raise HTTPException(404, "Suggestion introuvable")
    companion.decide(db, prof, s, body.status)
    db.commit()
    return {"suggestion": _sugg_out(s), "weight": float((prof.nudge_weights or {}).get(s.kind, 1.0))}


# ---------------------------------------------------------------- chat / briefs

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    tz: int = 0
    perimeter_id: str | None = None   # ambient context: the périmètre the user is currently viewing
    project_id: str | None = None     # or the project, so "this project" resolves without a dropdown


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream_answer(owner: str, system: str, task: str, context: str, sources: list[dict], prof_lang: CompanionProfile, save_user: str | None, learn: bool):
    """Stream the model's answer, then persist the exchange and (every few turns) learn from it.
    Uses its own DB session: the request-scoped one is closed once streaming starts."""
    yield _sse({"type": "meta", "sources": sources})
    chunks: list[str] = []
    llm_used = True
    for attempt in range(2):
        try:
            with telemetry.attribute(username=owner, agent="companion"):
                for piece in llm.stream_text(task=task, context=context, tier="agent", purpose="Companion", system=system, thinking=False, max_tokens=1200):
                    chunks.append(piece)
                    yield _sse({"type": "delta", "text": piece})
            break
        except llm.LLMUnavailable as exc:
            if getattr(exc, "blocked", False):  # refused by governance: say why, don't fake an answer
                llm_used = False
                chunks = [f"⚠️ {exc}"]
                yield _sse({"type": "delta", "text": chunks[0]})
                break
            if chunks or attempt == 1:
                if not chunks:
                    llm_used = False
                    chunks = [companion.fallback_reply(prof_lang, sources)]
                    yield _sse({"type": "delta", "text": chunks[0]})
                break
            time.sleep(0.6)
    answer = llm.clean_text("".join(chunks))
    learned = 0
    with SessionLocal() as db:
        if save_user:
            db.add(CompanionMessage(owner=owner, role="user", content=save_user))
        db.add(CompanionMessage(owner=owner, role="assistant", content=answer, sources=sources[:6]))
        prof = db.get(CompanionProfile, owner)
        if prof is not None and save_user:
            prof.messages_since_distill = (prof.messages_since_distill or 0) + 1
            if learn and llm_used and prof.messages_since_distill >= 6:
                prof.messages_since_distill = 0
                db.commit()
                try:
                    learned = companion.distill(db, owner)
                except Exception:  # noqa: BLE001 — learning must never break the conversation
                    learned = 0
        db.commit()
    yield _sse({"type": "done", "answer": answer, "llm_used": llm_used, "learned": learned})


@router.post("/chat")
def chat(body: ChatIn, request: Request, user: User = Depends(_use), x_graph_token: str | None = Header(default=None, alias="X-Graph-Token"),
         db: Session = Depends(get_db)):
    pol, prof = companion.policy(db), companion.profile(db, user)
    if not pol.enabled:
        raise HTTPException(403, "Le compagnon est désactivé par votre organisation")
    consume_llm_quota(request)
    company_rules.enforce(db, user.username, body.message, "compagnon")
    _refresh_agenda(db, prof, x_graph_token)
    noted = companion.explicit_memory(body.message)
    if noted:
        companion.remember(db, prof.owner, noted, "fact", source="user")
    context, sources = companion.build_context(db, user, prof, pol, body.message, body.tz,
                                               scope_perimeter_id=body.perimeter_id, scope_project_id=body.project_id)
    live = microsoft_graph.live_context(x_graph_token, body.message) if x_graph_token and settings.microsoft_enabled else {"text": "", "sources": []}
    if live["text"]:
        context = "<donnees_microsoft_temps_reel>\n" + live["text"] + "\n</donnees_microsoft_temps_reel>\n\n" + context
        sources = live["sources"] + sources
    past = "\n".join(f"{m.role}: {m.content[:700]}" for m in companion.history(db, prof.owner))
    if past:
        context += f"\n\n<conversation_recente>\n{past}\n</conversation_recente>"
    task = f"Message de la personne : « {body.message} »" + ("\n(Tu viens d'enregistrer cette information dans ta mémoire : confirme-le en une phrase.)" if noted else "")
    system = companion.system_prompt(prof, debug=companion.is_debug(body.message))
    db.commit()
    gen = _stream_answer(prof.owner, system, task, context, sources, prof, body.message, learn=pol.memory_learning_allowed)
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class BriefIn(BaseModel):
    kind: Literal["morning", "evening"] = "morning"
    tz: int = 0


@router.post("/brief")
def brief(body: BriefIn, request: Request, user: User = Depends(_use), x_graph_token: str | None = Header(default=None, alias="X-Graph-Token"),
          db: Session = Depends(get_db)):
    pol, prof = companion.policy(db), companion.profile(db, user)
    consume_llm_quota(request)
    _refresh_agenda(db, prof, x_graph_token)
    seed = "priorités actions en attente échéances à faire urgent" if body.kind == "morning" else "avancement terminé reste à faire"
    context, sources = companion.build_context(db, user, prof, pol, seed, body.tz)
    db.commit()
    gen = _stream_answer(prof.owner, companion.system_prompt(prof), companion.brief_task(prof, body.kind), context, sources, prof, None, learn=False)
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/messages")
def messages(limit: int = 40, user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    rows = companion.history(db, prof.owner, max(1, min(limit, 100)))
    return [{"id": m.id, "role": m.role, "content": m.content, "sources": m.sources or [], "ts": m.ts.isoformat()} for m in rows]


# ---------------------------------------------------------------- doing tasks (agentic)

class TaskIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    answers: dict[str, str] = Field(default_factory=dict)
    automation_id: str | None = None


@router.post("/task")
def plan_task(body: TaskIn, request: Request, user: User = Depends(require_permission("automation.use")),
              db: Session = Depends(get_db)):
    """Plan a real task the companion will DO (not just describe). Returns clarifying questions when the
    task is under-specified (version, source, …). Once answered, returns a concrete plan as a draft
    automation the user runs through the safe pipeline: a confirmation gate before every side-effecting
    step, and the EXACT PowerShell to approve before it can run. See companion.build_task_plan."""
    from app.automations import compiler
    from app.automations import validate as av

    pol = companion.policy(db)
    if not pol.enabled:
        raise HTTPException(403, "Le compagnon est désactivé par votre organisation")
    consume_llm_quota(request)
    company_rules.enforce(db, user.username, " ".join([body.message, *body.answers.values()]), "compagnon · tâche")
    description = f"{body.message}\n\n<contexte_execution>\n{companion.TASK_GUIDANCE}\n</contexte_execution>"
    try:
        wf = compiler.compile_workflow(description=description, answers=body.answers or None)
    except compiler.CompileUnavailable:
        raise HTTPException(503, "L'IA est momentanément indisponible pour planifier la tâche. Réessayez.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    questions = [q.strip() for q in (wf.get("questions") or []) if q and q.strip()][:5]
    if questions:  # ask what actually changes the plan, before doing anything —
        # with clickable answer options per question (the UI also offers a free-text "Autre").
        return {"status": "questions", "questions": companion.suggest_answers(body.message, questions),
                "explanation": wf.get("explanation", "")}

    plan = companion.build_task_plan(wf)
    w = plan["workflow"]
    report = av.validate(w.get("trigger") or {"type": "manual"}, w.get("steps") or [], w.get("inputs") or {})

    a = db.get(Automation, body.automation_id) if body.automation_id else None
    if a is not None and a.owner != user.username:
        a = None
    if a is None:
        a = Automation(owner=user.username, status="draft")
        db.add(a)
    a.name = (w.get("name") or "Tâche")[:200]
    a.description = w.get("description") or ""
    a.trigger = {"type": "manual"}
    a.steps = w.get("steps") or []
    a.inputs = w.get("inputs") or {}
    a.require_confirmation = True
    a.approved_script_hashes = []   # the user must approve the exact scripts shown below before running
    a.source = {"kind": "companion_task", "message": body.message[:1000]}
    db.commit()

    return {
        "status": "plan", "automation_id": a.id, "title": a.name, "explanation": w.get("explanation", ""),
        "steps": companion.task_step_summary(a.steps), "scripts": plan["scripts"],
        "inputs": {k: {"label": v.get("label", k), "type": v.get("type", "text")} for k, v in (a.inputs or {}).items()},
        "warnings": list(w.get("warnings", [])) + [i["message"] for i in report.get("errors", [])],
        "blocking": bool(report.get("errors")),
    }


# ---------------------------------------------------------------- memories

class MemoryIn(BaseModel):
    content: str = Field(min_length=4, max_length=500)
    kind: str = "fact"
    pinned: bool = False


@router.get("/memories")
def list_memories(user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    rows = db.scalars(select(CompanionMemory).where(CompanionMemory.owner == prof.owner).order_by(CompanionMemory.pinned.desc(), CompanionMemory.created_at.desc())).all()
    return [_memory_out(m) for m in rows]


@router.post("/memories")
def add_memory(body: MemoryIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    m = companion.remember(db, prof.owner, body.content, body.kind, source="user")
    if m is None:
        raise HTTPException(400, "Mémoire non enregistrée")
    m.pinned = body.pinned or m.pinned
    db.commit()
    return _memory_out(m)


class MemoryPatch(BaseModel):
    content: str | None = Field(None, min_length=4, max_length=500)
    kind: str | None = None
    pinned: bool | None = None


@router.put("/memories/{mid}")
def update_memory(mid: str, body: MemoryPatch, user: User = Depends(_use), db: Session = Depends(get_db)):
    m = db.get(CompanionMemory, mid)
    if m is None or m.owner != user.username:
        raise HTTPException(404, "Mémoire introuvable")
    if body.content is not None:
        m.content, m.source, m.confidence = body.content.strip(), "user", 1.0
    if body.kind in companion.MEMORY_KINDS:
        m.kind = body.kind
    if body.pinned is not None:
        m.pinned = body.pinned
    db.commit()
    return _memory_out(m)


@router.delete("/memories/{mid}")
def delete_memory(mid: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    m = db.get(CompanionMemory, mid)
    if m is None or m.owner != user.username:
        raise HTTPException(404, "Mémoire introuvable")
    db.delete(m)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------- privacy (self-service)

@router.get("/me/summary")
def my_data_summary(user: User = Depends(_use), db: Session = Depends(get_db)):
    """What the companion holds about me, in counts (Mon compte → Mes données): no content."""
    pol, prof = companion.policy(db), companion.profile(db, user)
    owner = prof.owner

    def count(model) -> int:
        return db.scalar(select(func.count()).select_from(model).where(model.owner == owner)) or 0

    first, last, seconds = db.execute(
        select(func.min(ActivityEvent.started_at), func.max(ActivityEvent.started_at), func.coalesce(func.sum(ActivityEvent.seconds), 0))
        .where(ActivityEvent.owner == owner)).one()
    return {
        "tracking": {
            "allowed": pol.activity_tracking_allowed and "companion.activity" in user.permissions,
            "enabled": prof.tracking_enabled, "active": companion.tracking_active(prof, pol),
            "titles": pol.title_capture, "retention_days": pol.retention_days, "excluded_apps": len(pol.excluded_apps or []),
        },
        "proactive_enabled": prof.proactive_enabled, "memory_learning_allowed": pol.memory_learning_allowed,
        "counts": {"activity": count(ActivityEvent), "activity_hours": round((seconds or 0) / 3600, 1),
                   "messages": count(CompanionMessage), "memories": count(CompanionMemory), "suggestions": count(CompanionSuggestion)},
        "activity_from": first.isoformat() if first else None, "activity_to": last.isoformat() if last else None,
    }


@router.get("/me/export")
def export_me(user: User = Depends(_use), db: Session = Depends(get_db)):
    prof = companion.profile(db, user)
    owner = prof.owner
    audit.log(db, user.username, "companion.export", None, "companion_profile", owner)
    db.commit()
    return {
        "profile": _profile_out(prof),
        "memories": [_memory_out(m) for m in db.scalars(select(CompanionMemory).where(CompanionMemory.owner == owner))],
        "messages": [{"role": m.role, "content": m.content, "ts": m.ts.isoformat()} for m in db.scalars(select(CompanionMessage).where(CompanionMessage.owner == owner).order_by(CompanionMessage.ts))],
        "suggestions": [_sugg_out(s) for s in db.scalars(select(CompanionSuggestion).where(CompanionSuggestion.owner == owner))],
        "activity": [{"started_at": a.started_at.isoformat(), "seconds": a.seconds, "app": a.app, "title": a.title, "category": a.category}
                     for a in db.scalars(select(ActivityEvent).where(ActivityEvent.owner == owner).order_by(ActivityEvent.started_at))],
    }


@router.delete("/me/data")
def erase_me(scope: Literal["all", "activity", "messages"] = "all", user: User = Depends(_use), db: Session = Depends(get_db)):
    from sqlalchemy import delete as sql_delete

    owner = user.username
    if scope == "activity":
        counts = {"activity": db.execute(sql_delete(ActivityEvent).where(ActivityEvent.owner == owner)).rowcount or 0}
    elif scope == "messages":
        counts = {"messages": db.execute(sql_delete(CompanionMessage).where(CompanionMessage.owner == owner)).rowcount or 0}
    else:
        counts = companion.erase(db, owner)
    audit.log(db, user.username, "companion.erase", None, "companion_profile", owner, scope=scope, **counts)
    db.commit()
    return {"erased": counts}


# ---------------------------------------------------------------- administration & monitoring

class PolicyIn(BaseModel):
    enabled: bool | None = None
    activity_tracking_allowed: bool | None = None
    title_capture: Literal["full", "app_only"] | None = None
    retention_days: int | None = Field(None, ge=1, le=365)
    excluded_apps: list[str] | None = Field(None, max_length=100)
    excluded_keywords: list[str] | None = Field(None, max_length=200)
    proactive_allowed: bool | None = None
    max_nudges_per_hour: int | None = Field(None, ge=0, le=20)
    memory_learning_allowed: bool | None = None


@router.get("/admin/policy")
def get_policy(user: User = Depends(_admin), db: Session = Depends(get_db)):
    pol = companion.policy(db)
    db.commit()
    return {**_policy_out(pol), "always_excluded_keywords": list(companion.ALWAYS_EXCLUDED_KEYWORDS), "always_excluded_apps": list(companion.ALWAYS_EXCLUDED_APPS)}


@router.put("/admin/policy")
def put_policy(body: PolicyIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    from sqlalchemy import delete as sql_delete

    pol = companion.policy(db)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        if k in ("excluded_apps", "excluded_keywords"):
            v = sorted({str(x).strip().lower()[:80] for x in v if str(x).strip()})
        setattr(pol, k, v)
    pol.updated_by = user.username
    purged = 0
    if changes.get("activity_tracking_allowed") is False:  # switching tracking off org-wide also clears what was collected
        purged = db.execute(sql_delete(ActivityEvent)).rowcount or 0
    elif "retention_days" in changes:
        purged = db.execute(sql_delete(ActivityEvent).where(ActivityEvent.started_at < datetime.now(timezone.utc) - timedelta(days=pol.retention_days))).rowcount or 0
    audit.log(db, user.username, "companion.policy_update", None, "companion_policy", "org", purged_activity=purged, **{k: v for k, v in changes.items() if not isinstance(v, list)})
    db.commit()
    return _policy_out(pol)


@router.get("/admin/metrics")
def admin_metrics(days: int = 30, user: User = Depends(_admin), db: Session = Depends(get_db)):
    return companion.metrics(db, max(1, min(days, 365)))


@router.get("/admin/users")
def admin_users(user: User = Depends(_admin), db: Session = Depends(get_db)):
    """Adoption per person — counts only. Content (titles, memories, messages) is never exposed."""
    week = datetime.now(timezone.utc) - timedelta(days=7)
    out = []
    for p in db.scalars(select(CompanionProfile).order_by(CompanionProfile.owner)):
        decided = db.execute(select(CompanionSuggestion.status, func.count()).where(CompanionSuggestion.owner == p.owner).group_by(CompanionSuggestion.status)).all()
        d = dict(decided)
        total = d.get("accepted", 0) + d.get("dismissed", 0) + d.get("never", 0)
        out.append({
            "owner": p.owner, "display_name": p.display_name, "job_role": p.job_role, "onboarded": p.onboarded, "tracking_enabled": p.tracking_enabled,
            "messages_7d": db.scalar(select(func.count()).select_from(CompanionMessage).where(CompanionMessage.owner == p.owner, CompanionMessage.role == "user", CompanionMessage.ts >= week)) or 0,
            "memories": db.scalar(select(func.count()).select_from(CompanionMemory).where(CompanionMemory.owner == p.owner)) or 0,
            "tracked_hours_7d": round((db.scalar(select(func.sum(ActivityEvent.seconds)).where(ActivityEvent.owner == p.owner, ActivityEvent.started_at >= week)) or 0) / 3600, 1),
            "acceptance_pct": round(100 * d.get("accepted", 0) / total, 1) if total else None,
        })
    return out


@router.delete("/admin/users/{owner}/data")
def admin_erase(owner: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    """Erasure on behalf of a person (leaver process / data-subject request). Audited."""
    counts = companion.erase(db, owner)
    audit.log(db, user.username, "companion.admin_erase", None, "companion_profile", owner, **counts)
    db.commit()
    return {"erased": counts}
