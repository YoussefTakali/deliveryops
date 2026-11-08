"""Automatisations API (/api/automations): workflows, runs, server steps, files, recordings, AI compile.

Every endpoint requires the `automation.use` permission and is owner-scoped: another user's
automation/run/file/recording is a 404, never a 403 (don't reveal it exists).

The runner (frontend/desktop) drives execution: it renders templates, loops, evaluates `when`,
runs local steps itself and calls POST /runs/{run_id}/steps for server steps. Scheduling is
pull-based (GET /due + an atomic POST /{id}/claim) so several devices of the same user never
double-run an occurrence.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app import audit, policy
from app.auth import User, require_permission
from app.automations import catalog, compiler, recording, steps
from app.automations.schedule import next_run_at
from app.automations.validate import script_hashes, validate
from app.config import get_settings
from app.db import get_db
from app.middleware import consume_llm_quota
from app.models import Automation, AutomationFile, AutomationRecording, AutomationRun

log = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/automations")

_use = require_permission("automation.use")

FINAL_STATUSES = ("succeeded", "failed", "cancelled", "skipped")


# ---------------------------------------------------------------- vision: deciding what to act on

class VisionCandidate(BaseModel):
    i: int = Field(ge=0, le=1000)
    label: str = Field("", max_length=200)
    control_type: str = Field("", max_length=40)
    source: str = Field("ocr", max_length=8)
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


class VisionIcon(BaseModel):
    i: int = Field(ge=0, le=1000)
    png_b64: str = Field(max_length=90_000)


class VisionDecideIn(BaseModel):
    """What the desktop app perceived (never the screenshot itself) + what the step wants."""
    goal: str = Field(min_length=1, max_length=500)
    action: Literal["click", "type", "wait"] = "click"
    window: str = Field("", max_length=300)
    text: str = Field("", max_length=500)
    candidates: list[VisionCandidate] = Field(default_factory=list, max_length=300)
    icons: list[VisionIcon] = Field(default_factory=list, max_length=12)


@router.post("/vision/decide")
def vision_decide(body: VisionDecideIn, request: Request, user: User = Depends(_use)):
    from app.automations import vision

    if not body.candidates:
        return {"index": -1, "confidence": 0.0, "reason": "rien de lisible à l'écran"}
    consume_llm_quota(request)
    return vision.decide(goal=body.goal, action=body.action, window=body.window, text=body.text,
                         candidates=[c.model_dump() for c in body.candidates], icons=[i.model_dump() for i in body.icons])


# ---------------------------------------------------------------- AI locator resolution

class ResolveElementIn(BaseModel):
    """What the step wanted (as compiled) plus what the window actually contains (accessibility
    tree dumped by the desktop app), so a model can pick the right control at run time."""
    window: str = ""
    step_title: str = ""
    name: str = ""
    control_type: str = ""
    action: Literal["click", "type"] = "click"
    elements: list[dict[str, Any]] = Field(default_factory=list, max_length=400)


class _ResolvedElement(BaseModel):
    index: int = Field(ge=-1, description="index in the candidate list, -1 if nothing fits")
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


@router.post("/resolve-element")
def resolve_element(body: ResolveElementIn, request: Request, user: User = Depends(_use)):
    """Runtime locator repair. The compiler can only *guess* a control's label; when that guess isn't
    found (or is ambiguous) the desktop app dumps the window's real controls and we let the model
    choose the one matching the step's intent. The caller then clicks that element and remembers it."""
    if not body.elements:
        return {"index": -1, "confidence": 0.0, "element": None}
    from app import llm

    consume_llm_quota(request)
    kinds = ("Button", "MenuItem", "Edit", "ComboBox", "CheckBox", "RadioButton", "TabItem", "ListItem", "TreeItem", "Hyperlink", "SplitButton", "Text", "Document", "DataItem")
    lines = []
    for i, e in enumerate(body.elements[:400]):
        ct = str(e.get("control_type") or "")
        if body.action == "type" and ct not in ("Edit", "ComboBox", "Document", "Text"):
            continue
        if ct and ct not in kinds:
            continue
        lines.append(f"[{i}] {ct or '?'} | name=« {str(e.get('name') or '')[:80]} » | id={str(e.get('automation_id') or '')[:40]}")
    if not lines:
        return {"index": -1, "confidence": 0.0, "element": None}
    want = " / ".join(x for x in (body.step_title, body.name, body.control_type) if x)
    result, used = llm.structured(
        schema=_ResolvedElement, tier="agent", thinking=False, max_tokens=200,
        system=("Tu choisis, dans la liste des contrôles réellement présents dans une fenêtre Windows, celui que "
                "l'utilisateur voulait actionner. Juge par le sens (libellé synonyme, abréviation, autre langue, "
                "accélérateur avec « & »), le type de contrôle attendu, et l'action (click = bouton/menu/onglet/lien ; "
                "type = champ de saisie). Renvoie index=-1 si aucun contrôle ne correspond vraiment."),
        context=f"Fenêtre : « {body.window} »\nAction : {body.action}\nÉtape voulue : {want}\n\nContrôles :\n" + "\n".join(lines),
        task="Indique l'index du contrôle à actionner, une confiance 0-1 et une raison d'un mot ou deux.",
        fallback=lambda: _ResolvedElement(index=-1, confidence=0.0),
    )
    idx = result.index if used and 0 <= result.index < len(body.elements) else -1
    return {"index": idx, "confidence": result.confidence if used else 0.0, "reason": result.reason,
            "element": body.elements[idx] if idx >= 0 else None}
MAX_LOG_ENTRIES = 2000
MAX_CONTEXT_BYTES = 256 * 1024
MAX_KEYFRAMES = 150
MAX_RECORDING_S = 10 * 60 + 30  # 10 min + slack for the stop button


# ---------------------------------------------------------------- helpers

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes (stored as UTC); Postgres returns aware ones."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _automation_row(a: Automation) -> dict:
    return {
        "id": a.id, "owner": a.owner, "name": a.name, "description": a.description, "status": a.status,
        "trigger": a.trigger or {"type": "manual"}, "steps": a.steps or [], "inputs": a.inputs or {},
        "require_confirmation": a.require_confirmation, "approved_script_hashes": a.approved_script_hashes or [],
        "source": a.source or {}, "next_run_at": _iso(a.next_run_at), "last_run_at": _iso(a.last_run_at),
        "last_run_status": a.last_run_status, "mail_trigger_checked_at": _iso(a.mail_trigger_checked_at),
        "created_at": _iso(a.created_at), "updated_at": _iso(a.updated_at),
    }


def _run_row(r: AutomationRun) -> dict:
    return {
        "id": r.id, "automation_id": r.automation_id, "owner": r.owner, "trigger": r.trigger, "dry_run": r.dry_run,
        "status": r.status, "context": r.context or {}, "log": r.log or [], "error": r.error or "",
        "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
    }


def _file_row(f: AutomationFile) -> dict:
    return {**steps.file_meta(f), "automation_id": f.automation_id, "run_id": f.run_id, "created_at": _iso(f.created_at)}


def _owned_automation(db: Session, user: User, automation_id: str) -> Automation:
    a = db.get(Automation, automation_id)
    if a is None or a.owner != user.username:
        raise HTTPException(404, "Automatisation introuvable")
    return a


def _owned_run(db: Session, user: User, run_id: str) -> AutomationRun:
    r = db.get(AutomationRun, run_id)
    if r is None or r.owner != user.username:
        raise HTTPException(404, "Exécution introuvable")
    return r


def _owned_file(db: Session, user: User, file_id: str) -> AutomationFile:
    f = db.get(AutomationFile, file_id)
    if f is None or f.owner != user.username:
        raise HTTPException(404, "Fichier introuvable")
    return f


def _owned_recording(db: Session, user: User, recording_id: str) -> AutomationRecording:
    rec = db.get(AutomationRecording, recording_id)
    if rec is None or rec.owner != user.username:
        raise HTTPException(404, "Enregistrement introuvable")
    return rec


def _graph_token(authorization: str | None = Header(default=None),
                 x_graph_token: str | None = Header(default=None, alias="X-Graph-Token")) -> str | None:
    """Graph token for mail.* steps: X-Graph-Token when sent (any auth mode; lets the runner pass a
    token carrying the automation scopes), else — in microsoft auth mode — the bearer itself."""
    if x_graph_token:
        return x_graph_token.strip()
    if settings.auth_mode == "microsoft" and authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return None


def _schedule(trigger: dict, after: datetime) -> datetime | None:
    try:
        return next_run_at(trigger, after)
    except ValueError as exc:
        raise HTTPException(400, f"Planification invalide : {exc}") from exc


def _activation_problems(a: Automation) -> list[str]:
    report = validate(a.trigger, a.steps, a.inputs)
    problems = [f"{('[' + e['step_id'] + '] ') if e.get('step_id') else ''}{e['message']}" for e in report["errors"]]
    approved = set(a.approved_script_hashes or [])
    for step_id, digest in script_hashes(a.steps).items():
        if digest not in approved:
            problems.append(f"[{step_id}] Script PowerShell non approuvé")
    for key, spec in (a.inputs or {}).items():
        if isinstance(spec, dict) and spec.get("value") in (None, "", [], {}):
            problems.append(f"Entrée « {spec.get('label') or key} » sans valeur")
    return problems


def _problems_detail(problems: list[str]) -> str:
    return "Automatisation invalide : " + " ; ".join(problems)


# ---------------------------------------------------------------- catalog & validation

@router.get("/catalog")
def get_catalog(_: User = Depends(_use)):
    return catalog.CATALOG


class WorkflowIn(BaseModel):
    trigger: dict[str, Any] | None = None
    steps: list[Any] = Field(default_factory=list)
    inputs: dict[str, Any] | None = None


@router.post("/validate")
def validate_workflow(body: WorkflowIn, _: User = Depends(_use)):
    return validate(body.trigger, body.steps, body.inputs or {})


class CompileIn(BaseModel):
    description: str | None = None
    recording_id: str | None = None
    answers: dict[str, str] | None = None
    current: dict[str, Any] | None = None


def _recording_analysis(db: Session, rec: AutomationRecording) -> dict:
    if rec.analysis is None:
        rec.analysis = recording.analyze(db, rec)
        db.commit()
    return rec.analysis


@router.post("/compile")
def compile_workflow(body: CompileIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    policy.enforce(db, user.username, " ".join([body.description or "", *(body.answers or {}).values()]), "automatisations")
    analysis = _recording_analysis(db, _owned_recording(db, user, body.recording_id)) if body.recording_id else None
    try:
        return compiler.compile_workflow(body.description or "", analysis, body.answers, body.current)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except compiler.CompileUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------- scheduling (static paths first)

@router.get("/due")
def due(user: User = Depends(_use), db: Session = Depends(get_db)):
    now = _now()
    rows = db.scalars(select(Automation).where(
        Automation.owner == user.username, Automation.status == "active",
        Automation.next_run_at.is_not(None), Automation.next_run_at <= now,
    ).order_by(Automation.next_run_at))
    return [_automation_row(a) for a in rows if (a.trigger or {}).get("type") == "schedule"]


# ---------------------------------------------------------------- files

@router.post("/files")
async def upload_file(file: UploadFile, automation_id: str | None = Form(default=None), run_id: str | None = Form(default=None),
                      user: User = Depends(_use), db: Session = Depends(get_db)):
    automation_id = (automation_id or "").strip() or None
    run_id = (run_id or "").strip() or None
    if automation_id:
        _owned_automation(db, user, automation_id)
    if run_id:
        _owned_run(db, user, run_id)
    data = await file.read()
    try:
        f = steps.store_file(db, user.username, file.filename or "fichier", data, content_type=file.content_type,
                             automation_id=automation_id, run_id=run_id)
    except steps.StepError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return _file_row(f)


@router.get("/files/{file_id}/meta")
def file_meta(file_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    return _file_row(_owned_file(db, user, file_id))


@router.get("/files/{file_id}")
def download_file(file_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    f = _owned_file(db, user, file_id)
    ascii_name = f.name.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return Response(content=f.data, media_type=f.content_type or "application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.name)}",
    })


# ---------------------------------------------------------------- recordings

@router.post("/recordings")
async def create_recording(request: Request, user: User = Depends(_use), db: Session = Depends(get_db)):
    form = await request.form(max_files=MAX_KEYFRAMES + 10)
    try:
        meta = json.loads(form.get("meta") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Champ « meta » invalide (JSON attendu)") from exc
    if not isinstance(meta, dict):
        raise HTTPException(400, "Champ « meta » invalide (objet attendu)")
    duration = float(meta.get("duration_s") or 0)
    if duration > MAX_RECORDING_S:
        raise HTTPException(400, "Enregistrement trop long (10 minutes maximum)")
    frames_in = [k for k in meta.get("keyframes") or [] if isinstance(k, dict)]
    if len(frames_in) > MAX_KEYFRAMES:
        raise HTTPException(400, f"Trop de captures ({len(frames_in)}, maximum {MAX_KEYFRAMES})")
    keyframes = []
    for k in frames_in:
        upload = form.get(str(k.get("field") or ""))
        if upload is None or isinstance(upload, str):
            continue  # a keyframe whose image didn't make it: keep the rest of the evidence
        data = await upload.read()
        try:
            f = steps.store_file(db, user.username, upload.filename or f"capture-{k.get('t', 0)}.jpg", data, content_type=upload.content_type or "image/jpeg")
        except steps.StepError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        keyframes.append({"t": k.get("t", 0), "file_id": f.id, "window_title": str(k.get("window_title") or "")[:500],
                          "process": str(k.get("process") or "")[:200]})
    events = [e for e in meta.get("events") or [] if isinstance(e, dict)][:5000]
    narration = [{"t": n.get("t", 0), "text": str(n.get("text") or "")[:2000]} for n in meta.get("narration") or [] if isinstance(n, dict)][:1000]
    rec = AutomationRecording(owner=user.username, duration_s=duration, events=events, narration=narration, keyframes=keyframes)
    db.add(rec)
    db.commit()
    return {"recording_id": rec.id}


@router.post("/recordings/{recording_id}/analyze")
def analyze_recording(recording_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    return _recording_analysis(db, _owned_recording(db, user, recording_id))


@router.post("/recordings/{recording_id}/faithful")
def faithful_recording(recording_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    """Deterministic record-and-replay build (no LLM restructuring) — the reliable, identical replay.
    Runs through the same validator as the compiler so the editor treats it like any workflow."""
    rec = _owned_recording(db, user, recording_id)
    workflow = recording.build_faithful(rec)
    workflow["warnings"] = list(workflow.get("warnings", []))
    errors = validate(workflow["trigger"], workflow["steps"], workflow["inputs"])["errors"]
    if errors:
        workflow["warnings"].append("À corriger avant activation :\n" + "\n".join(
            f"- {('[' + e['step_id'] + '] ') if e.get('step_id') else ''}{e['message']}" for e in errors))
    return workflow


# ---------------------------------------------------------------- runs (by run id)

class LogEntryIn(BaseModel):
    ts: str | None = None
    step_id: str | None = None
    level: Literal["info", "warn", "error"] = "info"
    message: str = ""
    output_summary: Any = None


class LogIn(BaseModel):
    entries: list[LogEntryIn] = Field(default_factory=list)
    # Optional pause/resume for the flow.confirm control step (running <-> awaiting_confirmation).
    status: Literal["running", "awaiting_confirmation"] | None = None


def _summary(value: Any) -> Any:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= 4000:
        return value
    return text[:4000] + "…"


def _require_open(run: AutomationRun) -> None:
    if run.status in FINAL_STATUSES:
        raise HTTPException(409, f"Exécution déjà terminée ({run.status})")


def _finish(db: Session, user: User, run: AutomationRun, status: str, error: str = "") -> None:
    run.status = status
    run.error = (error or "")[:4000]
    run.finished_at = _now()
    a = db.get(Automation, run.automation_id)
    if a is not None:
        a.last_run_at = run.finished_at
        a.last_run_status = status
    audit.log(db, user.username, "automation.run_finished", None, "automation_run", run.id,
              automation_id=run.automation_id, status=status, dry_run=run.dry_run, error=run.error[:500])


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    return _run_row(_owned_run(db, user, run_id))


@router.post("/runs/{run_id}/confirm")
def confirm_run(run_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    run = _owned_run(db, user, run_id)
    if run.status != "awaiting_confirmation":
        raise HTTPException(409, f"Cette exécution n'attend pas de confirmation ({run.status})")
    run.status = "running"
    audit.log(db, user.username, "automation.run_confirmed", None, "automation_run", run.id, automation_id=run.automation_id)
    db.commit()
    return _run_row(run)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    run = _owned_run(db, user, run_id)
    _require_open(run)
    _finish(db, user, run, "cancelled")
    db.commit()
    return _run_row(run)


@router.post("/runs/{run_id}/log")
def append_log(run_id: str, body: LogIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    run = _owned_run(db, user, run_id)
    _require_open(run)
    now = _iso(_now())
    new = [{"ts": e.ts or now, "step_id": e.step_id, "level": e.level, "message": e.message[:2000],
            **({"output_summary": _summary(e.output_summary)} if e.output_summary is not None else {})} for e in body.entries]
    run.log = ([*(run.log or []), *new])[-MAX_LOG_ENTRIES:]  # reassign: JSON columns only track replacement
    if body.status and body.status != run.status:
        run.status = body.status
    db.commit()
    return {"ok": True, "count": len(run.log), "status": run.status}


class FinishIn(BaseModel):
    status: Literal["succeeded", "failed", "cancelled", "skipped"]
    error: str | None = None


@router.post("/runs/{run_id}/finish")
def finish_run(run_id: str, body: FinishIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    run = _owned_run(db, user, run_id)
    _require_open(run)
    _finish(db, user, run, body.status, body.error or "")
    db.commit()
    return _run_row(run)


class StepIn(BaseModel):
    step_id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/runs/{run_id}/steps")
def run_step(run_id: str, body: StepIn, request: Request, user: User = Depends(_use), db: Session = Depends(get_db),
             graph_token: str | None = Depends(_graph_token)):
    run = _owned_run(db, user, run_id)
    if run.status != "running":
        raise HTTPException(409, f"L'exécution n'est pas en cours ({run.status})")
    a = db.get(Automation, run.automation_id)
    declared = next((s for s in (a.steps if a else []) or [] if isinstance(s, dict) and s.get("id") == body.step_id), None)
    if declared is None or declared.get("type") != body.type:
        raise HTTPException(400, f"L'étape « {body.step_id} » ({body.type}) ne fait pas partie de l'automatisation enregistrée")
    try:
        output = steps.execute(db, user, run, body.type, body.params, graph_token=graph_token,
                               llm_quota=lambda: consume_llm_quota(request))
    except steps.StepError as exc:
        db.rollback()
        # Graph auth problems -> 424, never 401/403: those would make the SPA think its own session is gone.
        status = 424 if exc.status_code in (401, 403) else exc.status_code
        raise HTTPException(status, exc.message) from exc
    db.commit()
    return output


# ---------------------------------------------------------------- automations CRUD

class AutomationIn(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger: dict[str, Any] | None = None
    steps: list[Any] | None = None
    inputs: dict[str, Any] | None = None
    require_confirmation: bool | None = None
    source: dict[str, Any] | None = None


@router.get("")
@router.get("/")
def list_automations(user: User = Depends(_use), db: Session = Depends(get_db)):
    rows = db.scalars(select(Automation).where(Automation.owner == user.username).order_by(Automation.updated_at.desc()))
    return [_automation_row(a) for a in rows]


@router.post("")
@router.post("/")
def create_automation(body: AutomationIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = Automation(
        owner=user.username, name=(body.name or "").strip()[:200] or "Nouvelle automatisation",
        description=body.description or "", status="draft", trigger=body.trigger or {"type": "manual"},
        steps=body.steps or [], inputs=body.inputs or {},
        require_confirmation=True if body.require_confirmation is None else body.require_confirmation,
        approved_script_hashes=[], source=body.source or {},
    )
    db.add(a)
    audit.log(db, user.username, "automation.created", None, "automation", a.id, name=a.name)
    db.commit()
    return _automation_row(a)


@router.get("/{automation_id}")
def get_automation(automation_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    return _automation_row(_owned_automation(db, user, automation_id))


@router.put("/{automation_id}")
def update_automation(automation_id: str, body: AutomationIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        a.name = (body.name or "").strip()[:200] or a.name
    for field in ("description", "trigger", "steps", "inputs", "source"):
        if field in changes and changes[field] is not None:
            setattr(a, field, changes[field])
    if body.require_confirmation is not None:
        a.require_confirmation = body.require_confirmation
    if a.status == "active":
        problems = _activation_problems(a)
        if problems:
            db.rollback()
            raise HTTPException(400, _problems_detail(problems) + " (mettez l'automatisation en pause pour enregistrer un brouillon)")
        if "trigger" in changes:
            a.next_run_at = _schedule(a.trigger, _now())
    a.updated_at = _now()
    db.commit()
    return _automation_row(a)


@router.delete("/{automation_id}")
def delete_automation(automation_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    run_ids = select(AutomationRun.id).where(AutomationRun.automation_id == a.id)
    db.execute(delete(AutomationFile).where(or_(AutomationFile.automation_id == a.id, AutomationFile.run_id.in_(run_ids))))
    db.execute(delete(AutomationRun).where(AutomationRun.automation_id == a.id))
    db.delete(a)
    audit.log(db, user.username, "automation.deleted", None, "automation", automation_id, name=a.name)
    db.commit()
    return {"ok": True}


@router.post("/{automation_id}/activate")
def activate(automation_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    problems = _activation_problems(a)
    if problems:
        raise HTTPException(400, _problems_detail(problems))
    now = _now()
    a.status = "active"
    a.next_run_at = _schedule(a.trigger, now)
    if (a.trigger or {}).get("type") == "mail" and a.mail_trigger_checked_at is None:
        a.mail_trigger_checked_at = now  # only mails arriving from now on start runs
    a.updated_at = now
    audit.log(db, user.username, "automation.activated", None, "automation", a.id, next_run_at=_iso(a.next_run_at))
    db.commit()
    return _automation_row(a)


@router.post("/{automation_id}/pause")
def pause(automation_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    a.status = "paused"
    a.next_run_at = None
    a.updated_at = _now()
    audit.log(db, user.username, "automation.paused", None, "automation", a.id)
    db.commit()
    return _automation_row(a)


class ApproveIn(BaseModel):
    sha256: str


@router.post("/{automation_id}/approve-script")
def approve_script(automation_id: str, body: ApproveIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    digest = body.sha256.strip().lower()
    current = script_hashes(a.steps)
    if digest not in current.values():
        raise HTTPException(400, "Ce script ne correspond à aucune étape PowerShell de l'automatisation enregistrée")
    if digest not in (a.approved_script_hashes or []):
        a.approved_script_hashes = [*(a.approved_script_hashes or []), digest]
    step_ids = [sid for sid, h in current.items() if h == digest]
    audit.log(db, user.username, "automation.script_approved", None, "automation", a.id, sha256=digest, steps=step_ids)
    a.updated_at = _now()
    db.commit()
    return _automation_row(a)


class MailCheckedIn(BaseModel):
    checked_at: datetime


@router.post("/{automation_id}/mail-checked")
def mail_checked(automation_id: str, body: MailCheckedIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    checked = _aware(body.checked_at).astimezone(timezone.utc)
    previous = _aware(a.mail_trigger_checked_at)
    if previous is None or checked > previous:  # never move backwards (two devices polling)
        a.mail_trigger_checked_at = checked
    db.commit()
    return _automation_row(a)


# ---------------------------------------------------------------- runs (by automation)

class RunIn(BaseModel):
    trigger: Literal["schedule", "mail", "manual", "test"] = "manual"
    dry_run: bool = False
    context: dict[str, Any] | None = None


def _start_run(db: Session, user: User, a: Automation, trigger: str, dry_run: bool, context: dict | None) -> AutomationRun:
    context = context or {}
    if len(json.dumps(context, default=str)) > MAX_CONTEXT_BYTES:
        raise HTTPException(413, "Contexte d'exécution trop volumineux")
    awaiting = a.require_confirmation and trigger in ("schedule", "mail")
    run = AutomationRun(automation_id=a.id, owner=user.username, trigger=trigger, dry_run=dry_run,
                        status="awaiting_confirmation" if awaiting else "running", context=context, log=[], error="")
    db.add(run)
    audit.log(db, user.username, "automation.run_started", None, "automation_run", run.id,
              automation_id=a.id, trigger=trigger, dry_run=dry_run, status=run.status)
    return run


@router.post("/{automation_id}/runs")
def create_run(automation_id: str, body: RunIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    run = _start_run(db, user, a, body.trigger, body.dry_run, body.context)
    db.commit()
    return _run_row(run)


@router.get("/{automation_id}/runs")
def list_runs(automation_id: str, limit: int = 50, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    rows = db.scalars(select(AutomationRun).where(AutomationRun.automation_id == a.id)
                      .order_by(AutomationRun.started_at.desc()).limit(max(1, min(limit, 200))))
    return [_run_row(r) for r in rows]


class ClaimConflict(Exception):
    pass


def claim(db: Session, user: User, a: Automation, now: datetime | None = None) -> AutomationRun:
    """Atomically take the due occurrence of a scheduled automation. The conditional UPDATE only
    matches if next_run_at still holds the value this caller read, so when two devices race, exactly
    one advances it (row lock + re-check under READ COMMITTED on Postgres) and the other gets 0 rows."""
    now = now or _now()
    current = _aware(a.next_run_at)
    if a.status != "active" or (a.trigger or {}).get("type") != "schedule":
        raise ClaimConflict("L'automatisation n'est pas active avec un déclencheur planifié")
    if current is None or current > now:
        raise ClaimConflict("Aucune exécution due (déjà prise en charge ou pas encore l'heure)")
    upcoming = _schedule(a.trigger, max(now, current))
    result = db.execute(
        update(Automation)
        .where(Automation.id == a.id, Automation.status == "active", Automation.next_run_at == a.next_run_at)
        .values(next_run_at=upcoming)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ClaimConflict("Exécution déjà prise en charge par un autre appareil")
    a.next_run_at = upcoming
    run = _start_run(db, user, a, "schedule", False, {"scheduled_for": _iso(current)})
    db.commit()
    return run


@router.post("/{automation_id}/claim")
def claim_run(automation_id: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    a = _owned_automation(db, user, automation_id)
    try:
        run = claim(db, user, a)
    except ClaimConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _run_row(run)
