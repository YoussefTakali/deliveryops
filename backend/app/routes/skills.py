"""Agent skills API: view an agent's baseline + learned skills, and train it by adding or removing
learnings. Adding a learning immediately changes how that agent prompts (no redeploy)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, skills
from app.auth import User, current_user, require_permission
from app.db import get_db

router = APIRouter(prefix="/api/skills")


def _skill_row(s) -> dict:
    return {"id": s.id, "agent": s.agent, "content": s.content, "source": s.source,
            "perimeter_id": s.perimeter_id, "created_by": s.created_by, "active": s.active,
            "created_at": s.created_at.isoformat() if s.created_at else None}


@router.get("/{agent}")
def get_skills(agent: str, _: User = Depends(current_user), db: Session = Depends(get_db)):
    if agent not in skills.KNOWN_AGENTS:
        raise HTTPException(404, "Unknown agent")
    return {
        "agent": agent,
        "baseline": skills.baseline(agent),
        "learnings": [_skill_row(s) for s in skills.all_for(db, agent)],
    }


class SkillIn(BaseModel):
    content: str
    perimeter_id: str | None = None
    source: str = "user"


@router.post("/{agent}")
def add_skill(agent: str, body: SkillIn, user: User = Depends(require_permission("skills.manage")), db: Session = Depends(get_db)):
    if agent not in skills.KNOWN_AGENTS:
        raise HTTPException(404, "Unknown agent")
    if not body.content.strip():
        raise HTTPException(400, "Le contenu de la compétence est requis")
    s = skills.add(db, agent, body.content, source=body.source if body.source in ("user", "feedback", "auto") else "user",
                   created_by=user.username, perimeter_id=body.perimeter_id)
    audit.log(db, user.username, "skill.added", body.perimeter_id, "agent", agent, skill_id=s.id)
    db.commit()
    return _skill_row(s)


@router.delete("/{agent}/{skill_id}")
def remove_skill(agent: str, skill_id: str, user: User = Depends(require_permission("skills.manage")), db: Session = Depends(get_db)):
    if not skills.deactivate(db, skill_id):
        raise HTTPException(404, "Skill not found")
    audit.log(db, user.username, "skill.removed", None, "agent", agent, skill_id=skill_id)
    db.commit()
    return {"ok": True}
