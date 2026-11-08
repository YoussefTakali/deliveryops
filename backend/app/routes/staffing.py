"""Competence matrix & staffing API.

  /api/team/skills            my skills (any signed-in user) · the whole matrix · admin edit · skill match
  /api/perimeters/{id}/staff  who works on this périmètre (from the matrix), lifecycle, plan vs réel,
                              generated deliverables
Members are users: the matrix is per user, a périmètre staffs people from it (see app/staffing.py).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, staffing
from app.auth import User, current_user, perimeter_guard, require_decider, require_permission
from app.db import get_db
from app.models import AutomationFile, Perimeter, PerimeterDeliverable

router = APIRouter()
_view = require_permission("team.view")
_manage = require_permission("team.manage")


def _skill_out(s) -> dict:
    return {"skill": s.skill, "level": s.level, "category": s.category}


class SkillsIn(BaseModel):
    skills: list[dict[str, Any]] = Field(default_factory=list)   # [{skill, level 1..5, category}]


# ---------------------------------------------------------------- competence matrix

@router.get("/api/team/skills")
def my_skills(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"owner": user.username, "skills": [_skill_out(s) for s in staffing.skills_of(db, user.username)],
            "categories": list(staffing.SKILL_CATEGORIES)}


@router.put("/api/team/skills")
def set_my_skills(body: SkillsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = [_skill_out(s) for s in staffing.set_skills(db, user.username, body.skills)]
    audit.log(db, user.username, "skills.update", None, "user", user.username, count=len(out))
    db.commit()
    return {"owner": user.username, "skills": out}


@router.get("/api/team/skills/matrix")
def skills_matrix(user: User = Depends(_view), db: Session = Depends(get_db)):
    return {"people": staffing.matrix(db)}


@router.put("/api/team/skills/{owner}")
def set_user_skills(owner: str, body: SkillsIn, user: User = Depends(_manage), db: Session = Depends(get_db)):
    out = [_skill_out(s) for s in staffing.set_skills(db, owner, body.skills)]
    audit.log(db, user.username, "skills.update", None, "user", owner, count=len(out))
    db.commit()
    return {"owner": owner, "skills": out}


class MatchIn(BaseModel):
    technologies: list[str] = Field(default_factory=list)
    perimeter_id: str | None = None
    limit: int = 20


@router.post("/api/team/skills/match")
def match_people(body: MatchIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    return {"technologies": body.technologies, "people": staffing.match(db, body.technologies, body.perimeter_id, max(1, min(body.limit, 50)))}


# ---------------------------------------------------------------- staffing a périmètre

@router.get("/api/perimeters/{perimeter_id}/staff")
def get_staff(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return {"members": staffing.staffed(db, p.id)}


class StaffIn(BaseModel):
    owners: list[str] = Field(default_factory=list)
    profile: str = "confirmed"
    weekly_capacity_h: float = Field(35.0, ge=1, le=60)


@router.post("/api/perimeters/{perimeter_id}/staff")
def add_staff(body: StaffIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    owners = [o.strip() for o in body.owners if o and o.strip()]
    if not owners:
        raise HTTPException(400, "Indiquez au moins une personne")
    members = staffing.staff(db, p, owners, body.profile, body.weekly_capacity_h)
    audit.log(db, user.username, "perimeter.staff", p.id, "perimeter", p.id, owners=owners, profile=body.profile)
    db.commit()
    return {"members": members}


class ManualPersonIn(BaseModel):
    """A team member entered by hand — for people who don't have an account on the platform."""
    name: str = Field(min_length=1, max_length=200)
    email: str = Field("", max_length=200)
    profile: str = Field("confirmed", max_length=60)
    weekly_capacity_h: float = Field(35.0, ge=1, le=60)
    skills: list[Any] = Field(default_factory=list)   # ["Java", …] or [{"skill": "Java", "level": 4}, …]


@router.post("/api/perimeters/{perimeter_id}/staff/manual")
def add_manual_staff(body: ManualPersonIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    try:
        owner = staffing.create_external(db, body.name, user.username, body.email, body.profile, body.weekly_capacity_h, body.skills)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    members = staffing.staff(db, p, [owner])
    audit.log(db, user.username, "perimeter.staff_manual", p.id, "perimeter", p.id, owner=owner, name=body.name)
    db.commit()
    return {"members": members, "owner": owner}


@router.get("/api/team/external")
def list_external(user: User = Depends(_view), db: Session = Depends(get_db)):
    """People entered manually by managers (no account), with their skills — reusable on any project."""
    return {"people": [{"owner": f"ext:{e.id}", "name": e.name, "email": e.email, "profile": e.profile,
                        "weekly_capacity_h": e.weekly_capacity_h,
                        "skills": [{"skill": s.skill, "level": s.level} for s in staffing.skills_of(db, f"ext:{e.id}")]}
                       for e in staffing.external_people(db)]}


@router.delete("/api/perimeters/{perimeter_id}/staff/{member_id}")
def remove_staff(member_id: str, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if not staffing.unstaff(db, p.id, member_id):
        raise HTTPException(404, "Membre introuvable")
    audit.log(db, user.username, "perimeter.unstaff", p.id, "team_member", member_id)
    db.commit()
    return {"removed": True}


# ---------------------------------------------------------------- lifecycle, billing monitoring, deliverables

@router.get("/api/perimeters/{perimeter_id}/lifecycle")
def get_lifecycle(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return staffing.lifecycle(db, p)


@router.get("/api/perimeters/{perimeter_id}/plan-vs-reel")
def get_plan_vs_actual(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return staffing.plan_vs_actual(db, p)


@router.get("/api/perimeters/{perimeter_id}/deliverables")
def list_deliverables(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    rows = db.scalars(select(PerimeterDeliverable).where(PerimeterDeliverable.perimeter_id == p.id)
                      .order_by(PerimeterDeliverable.created_at.desc())).all()
    return {"deliverables": [{"id": d.id, "key": d.key, "title": d.title, "filename": d.filename, "file_id": d.file_id,
                              "created_by": d.created_by, "created_at": d.created_at.isoformat() if d.created_at else None}
                             for d in rows]}


@router.get("/api/perimeters/{perimeter_id}/deliverables/{deliverable_id}")
def get_deliverable(deliverable_id: str, p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    d = db.get(PerimeterDeliverable, deliverable_id)
    if d is None or d.perimeter_id != p.id:
        raise HTTPException(404, "Livrable introuvable")
    f = db.get(AutomationFile, d.file_id) if d.file_id else None
    return {"id": d.id, "key": d.key, "title": d.title, "filename": d.filename, "file_id": d.file_id, "draft": d.draft,
            "created_by": d.created_by, "created_at": d.created_at.isoformat() if d.created_at else None,
            "download": f"/api/automations/files/{d.file_id}" if f is not None else None}


@router.get("/api/perimeters/{perimeter_id}/deliverables/{deliverable_id}/file")
def download_deliverable(deliverable_id: str, p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    """Download a périmètre deliverable — scoped to the périmètre (any member with access), not to the uploader."""
    from fastapi import Response

    d = db.get(PerimeterDeliverable, deliverable_id)
    f = db.get(AutomationFile, d.file_id) if d is not None and d.perimeter_id == p.id and d.file_id else None
    if f is None:
        raise HTTPException(404, "Fichier introuvable")
    return Response(content=f.data, media_type=f.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d.filename or f.name}"'})
