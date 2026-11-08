"""Meeting Intelligence Agent — live contextual answers during client meetings, then minutes."""

import re

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import knowledge, llm, skills
from app.context import scope_perimeter_ids
from app.agents.base import AgentContext, AgentOutput, recommend, ticket_source
from app.analytics.incidents import similar_resolved
from app.analytics.scope import is_out_of_scope_text
from app.models import Document, Meeting, Perimeter, Ticket

NAME = "meeting_intelligence"

QUESTION = re.compile(r"(\?\s*$)|^(pourquoi|comment|quand|quel|quelle|quels|quelles|est-ce|combien|où|qui|peut-on|pouvez-vous|what|why|how|when|can)\b", re.IGNORECASE)


class LiveAnswer(BaseModel):
    answer: str = Field(description="concise answer the Delivery Manager can say out loud, 2-4 sentences")
    confidence: str = Field(description="high, medium or low")
    source_ids: list[str]
    follow_up: str = Field(description="point to verify after the meeting, empty if none")


class ActionItem(BaseModel):
    action: str
    owner: str
    due: str = Field(description="due date or timeframe as stated, empty if not stated")


class MeetingRisk(BaseModel):
    title: str
    description: str
    severity: str


class Minutes(BaseModel):
    summary: str
    decisions: list[str]
    actions: list[ActionItem]
    risks: list[MeetingRisk]
    client_requests: list[str] = Field(description="new requests or changes asked by the client")


def is_question(segment: str) -> bool:
    return bool(QUESTION.search(segment.strip()))


def _build_context(db, perimeter, perimeter_id: str, question: str):
    """Shared retrieval/grounding step for `answer()` and `answer_stream()`: returns
    (context, hits, similar_tickets, out_of_scope, out_of_scope_terms)."""
    hits = knowledge.search(db, scope_perimeter_ids(db, perimeter), question, k=5)
    tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == perimeter_id)).all()
    similar = similar_resolved(question, tickets, k=3)
    oos, terms = is_out_of_scope_text(question, perimeter.contract)

    context = knowledge.format_context(hits)
    if similar:
        context += "\n\n" + "\n".join(
            f'<source id="{t.ref}" cite="[ticket {t.ref}]">{t.kind} {t.priority} « {t.title} » ouvert le {t.opened_at:%Y-%m-%d} — résolution : {t.resolution}</source>' for t, _ in similar
        )
    if oos:
        context += f"\n\nContract note: the request mentions out-of-scope items ({', '.join(terms)}); it would require a quote or contract amendment."
    sk = skills.prompt_block(db, NAME, perimeter_id)
    if sk:
        context = sk + "\n\n" + context
    return context, hits, similar, oos, terms


def _fallback_text(hits, similar, oos: bool, terms: list[str], question: str) -> str:
    if not hits and not similar:
        return "Je n'ai pas trouvé d'élément documenté sur ce point ; à confirmer après la réunion."
    parts = []
    if hits:
        parts.append(f"D'après {hits[0].cite()} : {hits[0].text[:280].strip()}")
    if similar:
        t = similar[0][0]
        parts.append(f"Cas similaire traité ({t.ref}, {t.opened_at:%d/%m/%Y}) : {t.resolution or t.title}.")
    if oos:
        parts.append(f"Attention : ce sujet ({', '.join(terms)}) est hors périmètre contractuel et nécessiterait un devis.")
    return " ".join(parts)


def answer(db, perimeter_id: str, question: str) -> dict:
    perimeter = db.get(Perimeter, perimeter_id)
    context, hits, similar, oos, terms = _build_context(db, perimeter, perimeter_id, question)

    def fallback() -> LiveAnswer:
        return LiveAnswer(
            answer=_fallback_text(hits, similar, oos, terms, question),
            confidence="low" if not hits and not similar else "medium",
            source_ids=[h.chunk_id for h in hits[:2]] + [t.ref for t, _ in similar[:1]],
            follow_up=question if not hits and not similar else "",
        )

    result, used = llm.structured(
        schema=LiveAnswer,
        context=context,
        task=f"The client just asked during the meeting: « {question} ». Give the Delivery Manager a short, factual answer grounded in the sources. If the request is out of contract scope, say it tactfully.",
        fallback=fallback,
        tier="fast",
        effort="low",
    )
    by_id = {h.chunk_id: h.as_source() for h in hits} | {t.ref: ticket_source(t) for t, _ in similar}
    return {
        "question": question,
        "answer": result.answer,
        "confidence": result.confidence,
        "follow_up": result.follow_up,
        "out_of_scope": oos,
        "out_of_scope_terms": terms,
        "sources": [by_id[i] for i in result.source_ids if i in by_id],
        "llm_used": used,
    }


def answer_stream(db, perimeter_id: str, question: str):
    """Streaming counterpart of `answer()`, for the live call-assistant UI. Yields plain dicts (the
    caller is responsible for framing them, e.g. as SSE) describing a small event sequence:
      - {"type": "meta", ...}   sources/out-of-scope info, computed up front (no LLM source-picking
                                 like the non-streaming path, since that requires a full structured
                                 response — we just surface the top retrieved sources instead)
      - {"type": "delta", "text": "..."}  one or more text chunks, in order, forming the answer
      - {"type": "done", "answer": "...", "llm_used": bool}  final full answer + whether the LLM
        produced it (False means the static fallback text was streamed as a single delta)
    """
    perimeter = db.get(Perimeter, perimeter_id)
    context, hits, similar, oos, terms = _build_context(db, perimeter, perimeter_id, question)
    sources = [h.as_source() for h in hits[:2]] + [ticket_source(t) for t, _ in similar[:1]]
    yield {"type": "meta", "question": question, "out_of_scope": oos, "out_of_scope_terms": terms, "sources": sources}

    task = (
        f"The client just asked during the meeting: « {question} ». Give the Delivery Manager a short, "
        "factual answer (2-4 sentences) grounded in the sources, in French unless the sources are in "
        "another language. If the request is out of contract scope, say it tactfully. Respond with plain "
        "prose only — no JSON, no markdown headers."
    )
    parts: list[str] = []
    llm_used = True
    try:
        for chunk in llm.stream_text(task=task, context=context, tier="fast", purpose="LiveAnswerStream"):
            parts.append(chunk)
            yield {"type": "delta", "text": chunk}
    except llm.LLMUnavailable:
        llm_used = False
        fallback = _fallback_text(hits, similar, oos, terms, question)
        parts = [fallback]
        yield {"type": "delta", "text": fallback}

    yield {"type": "done", "answer": "".join(parts).strip(), "llm_used": llm_used}


def _index_transcript(db, meeting: Meeting, perimeter: Perimeter) -> None:
    """Ingest the verbatim transcript once (deduped by filename), best-effort."""
    text = (meeting.transcript or "").strip()
    if not text:
        return
    filename = f"Transcription — {meeting.title} ({meeting.held_at:%Y-%m-%d}).md"
    if db.scalar(select(Document.id).where(Document.perimeter_id == perimeter.id, Document.filename == filename)):
        return
    content = f"# {meeting.title}\n\nRéunion du {meeting.held_at:%Y-%m-%d} — périmètre {perimeter.name}.\n\n## Transcription intégrale\n\n{text}\n"
    try:
        knowledge.ingest_document(db, perimeter.id, filename, "meeting_transcript", content)
    except Exception:  # noqa: BLE001 — indexing is best-effort; never fail minute generation on it
        pass


def run(ctx: AgentContext) -> AgentOutput:
    """Generate minutes for an ended meeting."""
    db = ctx.db
    meeting = db.get(Meeting, ctx.params["meeting_id"])
    perimeter = db.get(Perimeter, ctx.perimeter_id)
    transcript = meeting.transcript

    def fallback() -> Minutes:
        lines = [l.strip() for l in transcript.splitlines() if l.strip()]
        actions, decisions, risks, requests = [], [], [], []
        for l in lines:
            low = l.lower()
            speaker = l.split(":")[0] if ":" in l[:40] else "À définir"
            body = l.split(":", 1)[1].strip() if ":" in l[:40] else l
            if any(k in low for k in ("je m'en charge", "on s'engage", "action", "va envoyer", "je vous envoie", "on revient vers")):
                actions.append(ActionItem(action=body, owner=speaker, due=""))
            if any(k in low for k in ("on valide", "décid", "d'accord pour", "go pour")):
                decisions.append(body)
            if any(k in low for k in ("risque", "inquiet", "bloquant", "retard", "crainte")):
                risks.append(MeetingRisk(title=body[:80], description=body, severity="medium"))
            if any(k in low for k in ("on aimerait", "pourriez-vous", "est-ce que vous pourriez", "nous souhaitons", "il faudrait")):
                requests.append(body)
        return Minutes(summary=f"Réunion « {meeting.title} » ({len(lines)} interventions).", decisions=decisions, actions=actions, risks=risks, client_requests=requests)

    minutes, used = llm.structured(
        schema=Minutes,
        context=f"Perimeter: {perimeter.name}\nMeeting: {meeting.title} on {meeting.held_at:%Y-%m-%d}\n\nTranscript:\n{transcript}",
        task="Write the meeting minutes: summary (5 lines max), decisions, actions with owners and due dates, risks raised, and new client requests.",
        fallback=fallback,
    )
    meeting.minutes = minutes.model_dump()
    meeting.status = "ended"

    # High-precision capture: index the verbatim transcript into the périmètre's knowledge base so
    # the copilot / companion / call assistant can later answer *exactly* what was said about this
    # project (the gated `minutes` recommendation only indexes the summary, and only after approval).
    _index_transcript(db, meeting, perimeter)

    recs = []
    src = [{"type": "meeting", "ref": meeting.id, "label": meeting.title, "excerpt": ""}]
    for a in minutes.actions:
        recs.append(recommend(ctx, NAME, "action", a.action, f"Responsable : {a.owner}" + (f" — échéance : {a.due}" if a.due else ""), impact="low", sources=src, payload=a.model_dump()))
    for r in minutes.risks:
        recs.append(recommend(ctx, NAME, "risk", r.title, r.description, impact="high" if r.severity == "high" else "medium", sources=src))
    for req in minutes.client_requests:
        oos, terms = is_out_of_scope_text(req, perimeter.contract)
        if oos:
            recs.append(recommend(ctx, NAME, "scope", f"Demande hors périmètre : {req[:120]}", f"Termes hors contrat détectés : {', '.join(terms)}. Proposer un devis ou un avenant plutôt que d'absorber la demande dans le forfait.", impact="high", confidence=0.8, sources=src, payload={"request": req, "terms": terms}))
    recs.append(recommend(ctx, NAME, "minutes", f"Compte-rendu : {meeting.title}", minutes.summary, impact="medium", sources=src, payload={"meeting_id": meeting.id}))

    return AgentOutput(summary=minutes.summary, data={"meeting_id": meeting.id, "minutes": minutes.model_dump()}, recommendations=recs, llm_used=used)
