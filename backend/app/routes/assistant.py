"""Personal assistant API: onboarding scan (Microsoft 365 + local files) and the RAG-based agents
(document generator, free-form ask, risk) operating over the signed-in user's own workspace.

Auth: the user is authenticated normally (Authorization: Bearer <app token>). The Microsoft Graph
access token, when needed, is forwarded separately in the `X-Graph-Token` header so it never
collides with the app token under native auth.
"""

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import assistant, knowledge, llm, microsoft_graph, policy
from app.agents import doc_generator, risk
from app.agents.base import AgentContext
from app.auth import User, current_user, require_ai, require_permission
from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/api/assistant")
settings = get_settings()


def _graph_token(x_graph_token: str | None = Header(default=None, alias="X-Graph-Token")) -> str:
    if not x_graph_token:
        raise HTTPException(401, "Microsoft token required (connectez votre compte Microsoft)")
    return x_graph_token


# ---------------------------------------------------------------- onboarding scan

@router.get("/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = assistant.workspace(db, user)
    db.commit()
    return {"workspace_id": ws.id, **assistant.index_stats(db, ws.id)}


@router.post("/scan/microsoft")
def scan_microsoft(user: User = Depends(require_permission("microsoft.use")), token: str = Depends(_graph_token), db: Session = Depends(get_db)):
    if not settings.microsoft_enabled:
        raise HTTPException(501, "Microsoft integration is not configured")
    try:
        overview = microsoft_graph.overview(token)
    except microsoft_graph.GraphError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (401, 403) else 502, exc.detail) from exc
    ws = assistant.workspace(db, user)
    counts = assistant.ingest_microsoft(db, ws.id, overview)
    # Full mail bodies, one document per message — the overview only carries short previews.
    counts["mail_bodies"] = assistant.ingest_mail_bodies(db, ws.id, microsoft_graph.list_mail_bodies(token))
    return {"indexed": counts, "profile": overview.get("profile"), "sources_available": overview.get("sources_available"), "status": assistant.index_stats(db, ws.id)}


@router.post("/scan/local")
async def scan_local(files: list[UploadFile] = File(...), user: User = Depends(require_permission("localfiles.use")), db: Session = Depends(get_db)):
    ws = assistant.workspace(db, user)
    payload = [(f.filename or "fichier", await f.read()) for f in files]
    result = assistant.ingest_local(db, ws.id, payload)
    return {**result, "status": assistant.index_stats(db, ws.id)}


# ---------------------------------------------------------------- agents over the workspace

class DocRequest(BaseModel):
    title: str
    instructions: str = ""
    example: str = ""
    sources_query: str = ""


@router.post("/document")
def generate_document(body: DocRequest, user: User = Depends(require_permission("agent.doc_generator")), _ai: User = Depends(require_ai), db: Session = Depends(get_db)):
    ws = assistant.workspace(db, user)
    db.commit()
    ctx = AgentContext(db=db, perimeter_id=ws.id, run_id=None, params=body.model_dump())
    doc, hits, used = doc_generator.generate(ctx)
    return {**doc.model_dump(), "llm_used": used, "sources": [h.as_source() for h in hits]}


class AskRequest(BaseModel):
    question: str


_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _temporal_preamble() -> str:
    from datetime import datetime

    now = datetime.now().astimezone()
    human = f"{_JOURS[now.weekday()]} {now.day} {_MOIS[now.month - 1]} {now.year} à {now:%H:%M}"
    return (f"<contexte_temporel>\nNous sommes le {human} (date ISO {now.date().isoformat()}, fuseau {now:%Z}). "
            "Résous les dates relatives (« aujourd'hui », « demain », « hier », « cette semaine », « lundi prochain ») "
            "par rapport à cette date et cette heure.\n</contexte_temporel>")


@router.post("/ask")
def ask(body: AskRequest, user: User = Depends(require_permission("copilot.use")), _ai: User = Depends(require_ai),
        x_graph_token: str | None = Header(default=None, alias="X-Graph-Token"), db: Session = Depends(get_db)):
    policy.enforce(db, user.username, body.question, "recherche")
    ws = assistant.workspace(db, user)
    db.commit()
    hits = knowledge.search(db, ws.id, body.question, k=8, rerank=settings.knowledge_rerank)

    # Fresh Microsoft data (calendar always, mail when asked), so time-sensitive questions ("do I have
    # a meeting today with X?") don't depend on when the last scan ran. Best-effort — empty on failure.
    live = microsoft_graph.live_context(x_graph_token, body.question) if x_graph_token else {"text": "", "sources": []}

    parts = [_temporal_preamble()]
    if live["text"]:
        parts.append("<donnees_microsoft_temps_reel>\n" + live["text"] + "\n</donnees_microsoft_temps_reel>")
    parts.append(knowledge.format_context(hits))
    context = "\n\n".join(p for p in parts if p)
    sources = live["sources"] + [h.as_source() for h in hits]

    def fallback() -> str:
        if live["text"]:
            return ("D'après vos données Microsoft les plus récentes :\n" + live["text"][:1200]
                    + "\n\n(Assistant IA momentanément indisponible — réponse basée sur les données brutes.)")
        if not hits:
            return "Je n'ai trouvé aucune donnée indexée correspondant à votre question. Lancez une analyse (scan) de votre compte Microsoft et de vos fichiers, puis réessayez."
        return "\n\n".join(f"{h.cite()} : {h.text[:400].strip()}" for h in hits[:3])

    text, used = llm.text(purpose="assistant_ask", context=context, model=settings.llm_model_search or None, thinking=False,
                          system=ASK_SYSTEM, max_tokens=1200,
                          task=(f"Question de l'utilisateur : « {body.question} »\n\n"
                                "Réponds UNIQUEMENT à partir du contexte ci-dessus (agenda et mails Microsoft — blocs « temps réel » "
                                "à privilégier s'ils existent —, fichiers et tâches indexés). Tiens compte du <contexte_temporel> pour "
                                "les dates relatives. Pour chaque fait avancé, cite la source [nom § section] et, quand la question "
                                "porte sur un mot ou un terme précis, recopie l'extrait exact où il apparaît. Si le contexte ne "
                                "contient pas la réponse, dis-le en une phrase — n'invente rien."),
                          fallback=fallback)
    return {"answer": text, "llm_used": used, "sources": sources}


ASK_SYSTEM = (
    "Tu es l'assistant personnel DeliveryOps. Tu réponds à partir des données de l'utilisateur fournies dans "
    "<context> (mails, agenda, fichiers, tâches) et de rien d'autre. Règles absolues : (1) chaque affirmation "
    "doit s'appuyer sur un passage du contexte que tu cites ; (2) si l'information demandée n'est pas dans le "
    "contexte, réponds « Je n'ai rien trouvé à ce sujet dans vos données indexées » et propose éventuellement de "
    "relancer une analyse ; (3) n'invente jamais de mail, de réunion, de nom, de date ou de contenu ; (4) réponds "
    "dans la langue de la question, de façon concise."
)


@router.post("/risk")
def risk_scan(user: User = Depends(require_permission("agent.risk")), db: Session = Depends(get_db)):
    ws = assistant.workspace(db, user)
    db.commit()
    ctx = AgentContext(db=db, perimeter_id=ws.id, run_id=None, params={})
    report, _sig, used = risk.assess(ctx)
    return {**report.model_dump(), "llm_used": used}
