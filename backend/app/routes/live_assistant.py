"""Project-optional live call copilot endpoints (see app/live_assistant.py): speech-to-text for mic /
system-audio chunks, and a streamed (SSE) suggestion for each conversation turn. No meeting or
perimeter is required; a perimeter can be passed to let the copilot answer project questions."""

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import live_assistant, stt
from app.api import visible_perimeters
from app.auth import User, current_user, require_ai, require_permission
from app.db import get_db

router = APIRouter(prefix="/api/live")


class Turn(BaseModel):
    speaker: str
    text: str


class RespondIn(BaseModel):
    utterance: str = Field(min_length=1, max_length=4000)
    speaker: str = "Interlocuteur"
    mode: str = Field("reply", pattern="^(reply|ask)$")
    transcript: list[Turn] = Field(default_factory=list, max_length=60)
    context: str | None = Field(None, max_length=6000)
    perimeter_id: str | None = None
    # ISO code the user pinned in the overlay ("fr"/"en"/"ar"), or empty for auto-detect. Locks the
    # suggestion to one language so the copilot stops switching mid-call.
    language: str | None = Field(None, max_length=8)


def _can_transcribe(user: User = Depends(current_user)) -> User:
    """Speech-to-text serves both the call copilot and the automation recorder's narration."""
    if "agent.call_assistant" not in user.permissions and "automation.use" not in user.permissions:
        raise HTTPException(403, "Missing permission: agent.call_assistant or automation.use")
    return user


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str | None = None, diarize: bool = False, hint: str | None = None,
                     user: User = Depends(_can_transcribe)):
    hint = (hint or "").strip()[:300] or None      # the call's context, used as the decoder's vocabulary hint
    data = await file.read()
    if not data:
        return {"text": ""}
    try:
        await run_in_threadpool(stt._get_model)  # distinguishes "STT down" (503) from a bad chunk
    except stt.STTUnavailable as exc:
        raise HTTPException(503, f"Speech-to-text unavailable: {exc}") from exc
    try:
        if diarize:  # also return a voice embedding so the client can split speakers on a call
            text, speaker, model, duration = await run_in_threadpool(stt.transcribe_and_fingerprint, data, language=language or None, hint=hint)
            return {"text": text, "speaker": speaker, "speaker_model": model, "duration_s": duration}
        text = await run_in_threadpool(stt.transcribe, data, language=language or None, hint=hint)
    except stt.STTUnavailable:
        return {"text": ""}  # undecodable/too-short chunk: skip it, the stream keeps going
    return {"text": text}


@router.post("/respond")
def respond(body: RespondIn, user: User = Depends(require_permission("agent.call_assistant")), _ai: User = Depends(require_ai), db: Session = Depends(get_db)):
    perimeter = None
    if body.perimeter_id:
        perimeter = next((p for p in visible_perimeters(db, user) if p.id == body.perimeter_id), None)
        if perimeter is None:
            raise HTTPException(404, "Perimeter not found")
    prepared = live_assistant.prepare(db, utterance=body.utterance, perimeter=perimeter, mode=body.mode)

    def gen():
        for event in live_assistant.stream(
            utterance=body.utterance, speaker=body.speaker, transcript=[t.model_dump() for t in body.transcript],
            user_context=body.context, prepared=prepared, mode=body.mode, language=body.language,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
