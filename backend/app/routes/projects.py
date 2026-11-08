"""Delivery projects: a manager runs one or more projects (per client); périmètres sit under a
project, and the project team (members = users) can work on any périmètre under it. When a project
is done its members are freed so they can be staffed on other projects."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, knowledge, staffing, team
from app.api import row
from app.auth import User, current_user, require_decider
from app.automations import steps as auto_steps
from app.db import get_db
from app.deliverables import generate as gen
from app.deliverables import plan as P
from app.models import (AutomationFile, Client, DeliveryProject, Perimeter, ProjectDeliverable, ProjectMember, Ticket)
from app.routes.deliverables import _summary_text, _template

router = APIRouter(prefix="/api")


def _project(pid: str, user: User, db: Session) -> DeliveryProject:
    proj = db.get(DeliveryProject, pid)
    if not proj or not user.can_see(proj.client_id):
        raise HTTPException(404, "Project not found")
    return proj


def _summary(proj: DeliveryProject, db: Session) -> dict:
    perims = db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id)).all()
    members = db.scalars(select(ProjectMember).where(ProjectMember.project_id == proj.id)).all()
    return {
        **row(proj), "client": proj.client.name,
        "perimeter_count": len(perims), "member_count": len(members),
    }


class ProjectIn(BaseModel):
    client_id: str
    name: str
    description: str = ""
    manager: str = ""
    technologies: list[str] = []


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    manager: str | None = None
    status: str | None = None
    technologies: list[str] | None = None


class ManualIn(BaseModel):
    name: str
    email: str = ""
    profile: str = "confirmed"
    weekly_capacity_h: float = 35.0
    skills: list[Any] = []


class MemberIn(BaseModel):
    owner: str = ""                       # an existing user (username/email) or an "ext:<id>" manual person
    manual: ManualIn | None = None        # …or a person entered by hand (no account)
    role_in_project: str = "member"
    allocation_pct: int = 100


class AttachIn(BaseModel):
    project_id: str | None = None


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    projs = [p for p in db.scalars(select(DeliveryProject).order_by(DeliveryProject.created_at.desc())).all() if user.can_see(p.client_id)]
    return [_summary(p, db) for p in projs]


@router.post("/projects")
def create_project(body: ProjectIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if not user.can_see(body.client_id) or not db.get(Client, body.client_id):
        raise HTTPException(404, "Client not found")
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    proj = DeliveryProject(
        client_id=body.client_id, name=body.name.strip(), description=body.description.strip(),
        manager=(body.manager or user.username).strip(), technologies=[t.strip() for t in body.technologies if t.strip()],
    )
    db.add(proj)
    db.flush()
    # The manager is the first team member.
    if proj.manager:
        db.add(ProjectMember(project_id=proj.id, owner=proj.manager, role_in_project="manager", added_by=user.username))
    audit.log(db, user.username, "project.created", None, "project", proj.id, name=proj.name, client=body.client_id)
    db.commit()
    return _summary(proj, db)


@router.get("/projects/{pid}")
def project_detail(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    perims = db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id)).all()

    def perim_card(p: Perimeter) -> dict:
        recs_open = 0  # keep the card light; the portfolio already carries KPIs
        return {**row(p), "client": p.client.name, "tickets": len(db.scalars(select(Ticket.id).where(Ticket.perimeter_id == p.id)).all()), "recs_open": recs_open}

    members = db.scalars(select(ProjectMember).where(ProjectMember.project_id == proj.id).order_by(ProjectMember.added_at)).all()
    names = team.display_names(db, {m.owner for m in members})
    externals = staffing._externals_by_owner(db, [m.owner for m in members])

    def member_card(m: ProjectMember) -> dict:
        ext = externals.get(m.owner)
        return {**row(m), "name": names.get(m.owner, m.owner), "is_external": m.owner.startswith("ext:"),
                "email": ext.email if ext else "", "profile": ext.profile if ext else None,
                "weekly_capacity_h": ext.weekly_capacity_h if ext else None,
                "skills": [row(s) for s in staffing.skills_of(db, m.owner)]}

    return {
        **row(proj), "client": proj.client.name,
        "perimeters": [perim_card(p) for p in perims],
        "members": [member_card(m) for m in members],
    }


@router.put("/projects/{pid}")
def update_project(pid: str, body: ProjectUpdate, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    if body.name is not None and body.name.strip():
        proj.name = body.name.strip()
    if body.description is not None:
        proj.description = body.description.strip()
    if body.manager is not None:
        proj.manager = body.manager.strip()
    if body.status is not None and body.status in ("active", "done", "archived"):
        proj.status = body.status
    if body.technologies is not None:
        proj.technologies = [t.strip() for t in body.technologies if t.strip()]
    audit.log(db, user.username, "project.updated", None, "project", proj.id, status=proj.status)
    db.commit()
    return _summary(proj, db)


@router.post("/projects/{pid}/members")
def add_member(pid: str, body: MemberIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    owner = body.owner.strip()
    if body.manual is not None:  # a person without an account, entered by the manager
        try:
            owner = staffing.create_external(db, body.manual.name, user.username, body.manual.email, body.manual.profile,
                                             body.manual.weekly_capacity_h, body.manual.skills)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not owner:
        raise HTTPException(400, "Choisissez un utilisateur ou saisissez une personne")
    existing = db.scalar(select(ProjectMember).where(ProjectMember.project_id == pid, ProjectMember.owner == owner))
    if existing:
        existing.role_in_project = body.role_in_project
        existing.allocation_pct = max(0, min(100, body.allocation_pct))
    else:
        db.add(ProjectMember(project_id=pid, owner=owner, role_in_project=body.role_in_project,
                             allocation_pct=max(0, min(100, body.allocation_pct)), added_by=user.username))
    audit.log(db, user.username, "project.member.added", None, "project", pid, owner=owner)
    db.commit()
    return project_detail(pid, user, db)


@router.delete("/projects/{pid}/members/{owner}")
def remove_member(pid: str, owner: str, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    m = db.scalar(select(ProjectMember).where(ProjectMember.project_id == pid, ProjectMember.owner == owner))
    if m:
        db.delete(m)
        audit.log(db, user.username, "project.member.removed", None, "project", pid, owner=owner)
        db.commit()
    return project_detail(pid, user, db)


@router.put("/perimeters/{perimeter_id}/project")
def attach_perimeter(perimeter_id: str, body: AttachIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    p = db.get(Perimeter, perimeter_id)
    if not p or not user.can_see(p.client_id):
        raise HTTPException(404, "Perimeter not found")
    if body.project_id:
        proj = _project(body.project_id, user, db)
        if proj.client_id != p.client_id:
            raise HTTPException(400, "Le projet et le périmètre doivent appartenir au même client")
        p.project_id = proj.id
    else:
        p.project_id = None
    audit.log(db, user.username, "perimeter.project.set", p.id, "perimeter", p.id, project=p.project_id)
    db.commit()
    return {**row(p), "client": p.client.name}


# ---------------------------------------------------------------- plan de charge (a PROJECT-level document)
# The plan de charge spans the whole project: it aggregates the matrices of all the project's périmètres,
# staffs from the project team, and produces one workbook + costs. Reuses deliverables/plan.py.

def _perim_tag(name: str) -> str:
    """A short, readable prefix so refs stay unique across the project's périmètres."""
    initials = "".join(w[0] for w in str(name).split() if w)[:4].upper()
    return initials or "P"


def _aggregate_matrice(db: Session, project: DeliveryProject) -> dict | None:
    """Merge the latest matrice de couverture of every périmètre under the project into one draft,
    namespacing refs by périmètre so coverage never matches across périmètres."""
    exigences: list[dict] = []
    cas_usage: list[dict] = []
    for per in db.scalars(select(Perimeter).where(Perimeter.project_id == project.id)):
        last = staffing.latest_deliverable(db, per.id, "matrice_couverture")
        draft = last.draft if last is not None else None
        if not draft:
            continue
        tag = _perim_tag(per.name)
        for e in draft.get("exigences") or []:
            ex = dict(e)
            ex["ref"] = f"{tag}-{e.get('ref', '')}" if e.get("ref") else ""
            ex["name"] = f"[{per.name}] {e.get('name', '')}"
            exigences.append(ex)
        for c in draft.get("cas_usage") or []:
            cc = dict(c)
            cc["exigences"] = [f"{tag}-{r}" for r in (c.get("exigences") or [])]
            cc["name"] = f"[{per.name}] {c.get('name', '')}"
            cas_usage.append(cc)
    if not exigences:
        return None
    return {"exigences": exigences, "cas_usage": cas_usage}


def _plan_members(db: Session, project: DeliveryProject) -> list[dict]:
    """The project team as plan members: reuse staffing data (profile/capacity/absences/skills) when a
    member is staffed on any of the project's périmètres, else sensible defaults + their competence matrix."""
    staffed_by_owner: dict[str, dict] = {}
    for per in db.scalars(select(Perimeter).where(Perimeter.project_id == project.id)):
        for m in staffing.staffed(db, per.id):
            if m.get("owner") and m["owner"] not in staffed_by_owner:
                staffed_by_owner[m["owner"]] = m
    members: list[dict] = []
    pms = list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id).order_by(ProjectMember.added_at)))
    names = team.display_names(db, {pm.owner for pm in pms})
    externals = staffing._externals_by_owner(db, [pm.owner for pm in pms])
    for pm in pms:
        s = staffed_by_owner.get(pm.owner)
        if s:
            members.append({"name": s["name"], "profile": s["profile"], "weekly_capacity_h": s["weekly_capacity_h"],
                            "absences": s["absences"], "skills": s["skills"]})
        else:
            ext = externals.get(pm.owner)
            members.append({"name": names.get(pm.owner, pm.owner), "profile": ext.profile if ext else "confirmed",
                            "weekly_capacity_h": ext.weekly_capacity_h if ext else 35.0, "absences": [],
                            "skills": [sk.skill for sk in staffing.skills_of(db, pm.owner)]})
    return members


def _plan_contract(db: Session, project: DeliveryProject) -> dict:
    """A rate card for the whole project: the first périmètre contract that has one (else its contract)."""
    first = None
    for per in db.scalars(select(Perimeter).where(Perimeter.project_id == project.id)):
        first = first or per
        if (per.contract or {}).get("rate_card"):
            return per.contract
    return (first.contract if first else {}) or {}


class PlanPrepareIn(BaseModel):
    answers: dict[str, str] = {}
    technologies: list[str] | None = None


class PlanGenerateIn(BaseModel):
    draft: dict[str, Any]


@router.post("/projects/{pid}/plan-de-charge/prepare")
def plan_prepare(pid: str, body: PlanPrepareIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    matrice = _aggregate_matrice(db, proj)
    if not matrice:
        raise HTTPException(400, "Aucune matrice de couverture dans les périmètres du projet : générez-en au moins une (onglet Livrables d'un périmètre).")
    members = _plan_members(db, proj)
    technologies = body.technologies if body.technologies is not None else (proj.technologies or [])
    contract = _plan_contract(db, proj)
    result = P.extract_plan(_template(db, "plan_de_charge"), matrice, members, contract, technologies, body.answers)
    return {"status": "questions" if result["questions"] else "ready", **result}


@router.post("/projects/{pid}/plan-de-charge/generate")
def plan_generate(pid: str, body: PlanGenerateIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    proj = _project(pid, user, db)
    template = _template(db, "plan_de_charge")
    try:
        fname, content_type, data = gen.generate("plan_de_charge", template, {}, draft=body.draft, projet=proj.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    f = auto_steps.store_file(db, user.username, fname, data, content_type=content_type)
    d = ProjectDeliverable(project_id=proj.id, key="plan_de_charge", title=template["title"], filename=fname,
                           file_id=f.id, draft=body.draft, created_by=user.username)
    db.add(d)
    db.flush()
    did = d.id
    # Index the plan into every périmètre of the project, so every agent (copilot/companion/call
    # assistant) can find it from any of the project's périmètres.
    text = _summary_text("plan_de_charge", template["title"], body.draft, {})
    for per in db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id)):
        try:
            knowledge.ingest_document(db, per.id, fname.rsplit(".", 1)[0] + ".md", "livrable", text)
        except Exception:  # noqa: BLE001 — indexing is best-effort; never fail the download on it
            pass
    audit.log(db, user.username, "project.deliverable.generate", None, "project", proj.id, key="plan_de_charge", filename=fname)
    db.commit()
    return Response(content=data, media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Deliverable-Id": did})


@router.get("/projects/{pid}/deliverables")
def project_deliverables(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _project(pid, user, db)
    ds = db.scalars(select(ProjectDeliverable).where(ProjectDeliverable.project_id == pid).order_by(ProjectDeliverable.created_at.desc())).all()
    return {"deliverables": [{"id": d.id, "key": d.key, "title": d.title, "filename": d.filename,
                             "created_by": d.created_by, "created_at": d.created_at,
                             "totals": (d.draft or {}).get("totals")} for d in ds]}


@router.get("/projects/{pid}/deliverables/{did}/file")
def project_deliverable_file(pid: str, did: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _project(pid, user, db)
    d = db.get(ProjectDeliverable, did)
    if not d or d.project_id != pid:
        raise HTTPException(404, "Livrable introuvable")
    f = db.get(AutomationFile, d.file_id) if d.file_id else None
    if not f:
        raise HTTPException(404, "Fichier indisponible")
    return Response(content=f.data, media_type=f.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d.filename}"'})
