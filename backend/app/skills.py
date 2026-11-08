"""Agent skills & learning.

Each agent has a baseline of skills shipped as a file (app/agents/skills/<agent>.md) plus a growing
set of *learnings* stored in the database (AgentSkill). Both are injected into the agent's prompt via
`prompt_block(...)`, so agents improve as users train them or as feedback is captured.

Files are the read-only baseline (versioned in the repo). The DB holds runtime additions so they
persist across restarts and are editable through the API without a redeploy.
"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentSkill

SKILLS_DIR = Path(__file__).resolve().parent / "agents" / "skills"

KNOWN_AGENTS = ("doc_generator", "meeting_intelligence", "billing", "risk", "copilot")


@lru_cache(maxsize=32)
def baseline(agent: str) -> str:
    """The file-based baseline skills for an agent (cached; files are read-only at runtime)."""
    path = SKILLS_DIR / f"{agent}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def learnings(db: Session, agent: str, perimeter_id: str | None = None) -> list[AgentSkill]:
    """Active learned skills for an agent: global ones plus any scoped to this perimeter."""
    stmt = select(AgentSkill).where(AgentSkill.agent == agent, AgentSkill.active.is_(True))
    rows = db.scalars(stmt.order_by(AgentSkill.created_at)).all()
    return [s for s in rows if s.perimeter_id in (None, perimeter_id)]


def prompt_block(db: Session, agent: str, perimeter_id: str | None = None) -> str:
    """A ready-to-inject block combining the baseline and the learned skills, or '' if none."""
    parts = []
    base = baseline(agent)
    if base:
        parts.append(base)
    learned = learnings(db, agent, perimeter_id)
    if learned:
        parts.append("# Apprentissages\n" + "\n".join(f"- {s.content.strip()}" for s in learned))
    if not parts:
        return ""
    return "<skills>\n" + "\n\n".join(parts) + "\n</skills>"


def add(db: Session, agent: str, content: str, *, source: str = "user", created_by: str = "",
        perimeter_id: str | None = None) -> AgentSkill:
    skill = AgentSkill(agent=agent, content=content.strip(), source=source, created_by=created_by, perimeter_id=perimeter_id)
    db.add(skill)
    db.flush()
    return skill


def all_for(db: Session, agent: str) -> list[AgentSkill]:
    return db.scalars(select(AgentSkill).where(AgentSkill.agent == agent).order_by(AgentSkill.created_at.desc())).all()


def deactivate(db: Session, skill_id: str) -> bool:
    skill = db.get(AgentSkill, skill_id)
    if not skill:
        return False
    skill.active = False
    db.flush()
    return True
