from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, policy
from app.agents import copilot
from app.api import row
from app.auth import User, current_user, perimeter_guard, require_ai
from app.db import get_db
from app.models import Conversation, Perimeter

router = APIRouter(prefix="/api")


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None


def _own(db: Session, user: User, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.username != user.username:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("/perimeters/{perimeter_id}/conversations")
def list_conversations(p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Conversation).where(Conversation.perimeter_id == p.id, Conversation.username == user.username).order_by(Conversation.updated_at.desc())
    return [row(c, "messages") for c in db.scalars(stmt)]


@router.post("/perimeters/{perimeter_id}/copilot")
def chat(body: ChatIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_ai), db: Session = Depends(get_db)):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message")
    policy.enforce(db, user.username, message, "copilot")
    if body.conversation_id:
        conv = _own(db, user, body.conversation_id)
        if conv.perimeter_id != p.id:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = Conversation(perimeter_id=p.id, username=user.username, title=message[:80], messages=[])
        db.add(conv)
        db.commit()

    history = list(conv.messages)
    result = copilot.respond(db, p, user.username, history, message)
    conv.messages = history + [
        copilot.new_message("user", message),
        copilot.new_message("assistant", result["text"], trace=result["trace"], sources=result["sources"], actions=result["actions"], llm_used=result["llm_used"]),
    ]
    conv.updated_at = datetime.now(timezone.utc)
    audit.log(db, user.username, "copilot.message", p.id, "conversation", conv.id, tools=[t["tool"] for t in result["trace"]], actions=result["actions"])
    db.commit()
    return row(conv)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return row(_own(db, user, conversation_id))


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own(db, user, conversation_id))
    db.commit()
    return {"deleted": True}
