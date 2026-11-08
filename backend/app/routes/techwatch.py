"""Personal tech watch API (/api/techwatch), permission `techwatch.use`. See app/techwatch.py."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import techwatch
from app.auth import User, require_permission
from app.db import get_db
from app.middleware import consume_llm_quota
from app.models import TechWatchBriefing

router = APIRouter(prefix="/api/techwatch")
_use = require_permission("techwatch.use")
STALE_AFTER = timedelta(hours=12)
KEEP = 5


def _briefing_out(b: TechWatchBriefing | None) -> dict | None:
    if not b:
        return None
    return {"id": b.id, "created_at": b.created_at.isoformat(), "topics": b.topics or [], "items": b.items or [],
            "summary": b.summary, "llm_used": b.llm_used, "stale": techwatch.since(b.created_at) > STALE_AFTER}


def _state(db: Session, user: User) -> dict:
    latest = db.scalar(select(TechWatchBriefing).where(TechWatchBriefing.owner == user.username)
                       .order_by(TechWatchBriefing.created_at.desc()).limit(1))
    return {"briefing": _briefing_out(latest), "topics": techwatch.topics_for(db, user), "categories": techwatch.CATEGORIES}


@router.get("")
def get_watch(user: User = Depends(_use), db: Session = Depends(get_db)):
    return _state(db, user)


@router.post("/refresh")
def refresh(request: Request, user: User = Depends(_use), db: Session = Depends(get_db)):
    topics = [t for t in techwatch.topics_for(db, user) if not t["muted"]]
    if not topics:
        raise HTTPException(400, "Aucun sujet à suivre : renseignez vos compétences (Mon espace) ou ajoutez un sujet.")
    consume_llm_quota(request)
    try:
        news = techwatch.fetch_news([t["topic"] for t in topics])
    except techwatch.NewsUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    items, summary, used = techwatch.analyse(topics, news)
    b = TechWatchBriefing(owner=user.username, topics=[t["topic"] for t in topics[:techwatch.MAX_TOPICS]],
                          items=items, summary=summary, llm_used=used)
    db.add(b)
    db.flush()
    old = db.scalars(select(TechWatchBriefing.id).where(TechWatchBriefing.owner == user.username)
                     .order_by(TechWatchBriefing.created_at.desc()).offset(KEEP)).all()
    if old:
        db.execute(delete(TechWatchBriefing).where(TechWatchBriefing.id.in_(old)))
    db.commit()
    return _state(db, user)


class TopicsIn(BaseModel):
    extra_topics: list[str] = Field(default_factory=list, max_length=20)
    muted_topics: list[str] = Field(default_factory=list, max_length=50)


@router.put("/topics")
def set_topics(body: TopicsIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    def clean(values: list[str]) -> list[str]:
        return list(dict.fromkeys(" ".join(v.split())[:60] for v in values if v.strip()))

    prof = techwatch.profile(db, user.username)
    prof.extra_topics, prof.muted_topics = clean(body.extra_topics), clean(body.muted_topics)
    db.commit()
    return _state(db, user)
