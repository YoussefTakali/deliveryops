"""Live call copilot: a project-optional assistant that listens to a conversation (transcribed by
app/stt.py) and whispers what to say next.

Unlike agents/meeting_intelligence.py (which always answers from one perimeter's documents), this
routes each turn to the right source:
  - the project's knowledge base + tickets, only when a perimeter is selected AND the turn is
    actually about that project (strong retrieval hit or explicit project vocabulary);
  - the web (DuckDuckGo, no API key) for factual questions that aren't about the project;
  - nothing but the conversation itself for casual talk ("bonjour, ça va ?"), so small talk gets a
    natural suggested reply instead of a forced lookup.
Everything is decided with cheap heuristics — no extra LLM "router" call — so the only model call is
the streamed answer itself, with reasoning disabled for a sub-second first token.
"""

import html
import logging
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from sqlalchemy import select

from app import context, knowledge, llm
from app.agents.base import ticket_source
from app.analytics.incidents import similar_resolved
from app.config import get_settings
from app.models import Perimeter, Ticket

log = logging.getLogger(__name__)
settings = get_settings()

_QUESTION = re.compile(
    r"\?|^\s*(pourquoi|comment|quand|quel|quelle|quels|quelles|est-ce|combien|où|qui|que|quoi|peux|pouvez|peut-on|"
    r"sais-tu|savez-vous|what|why|how|when|where|who|which|can|could|do|does|is|are|will|would)\b",
    re.IGNORECASE,
)
_PROJECT_WORDS = re.compile(
    r"\b(projet|project|périmètre|perimetre|ticket|incident|sla|contrat|contract|livraison|livrable|release|"
    r"déploiement|deploiement|anomalie|backlog|sprint|facturation|factur\w*|budget|devis|jalon|planning|"
    r"recette|ticketing|tma|ams|maintenance|client|forfait|périmètre contractuel|hors périmètre|migration|flux|batch|lot|"
    r"chiffrage|copil|comité|mise en production|mep|rejet|procédure|reprise|astreinte|pénalit\w*)\b",
    re.IGNORECASE,
)
_SMALLTALK = re.compile(
    r"\b(bonjour|bonsoir|salut|coucou|hello|hi|hey|ça va|ca va|comment allez|comment vas|how are you|"
    r"week-?end|vacances|merci|thanks|thank you|bonne journée|bonne soirée|à bientôt|au revoir|bye|"
    r"enchanté|ravi de|nice to meet|good morning|good afternoon)\b",
    re.IGNORECASE,
)
_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>', re.S)
_TAGS = re.compile(r"<[^>]+>")

LIVE_SYSTEM = (
    "You are DeliveryOps's live call copilot. The user (\"Moi\") is in a live call and reads your "
    "suggestions on a small overlay while talking, so be brief and immediately usable: 1-3 short "
    "sentences they could say out loud, no preamble, no markdown headers, no lists unless essential. "
    "Answer ONLY what the other participant actually said. NEVER invent a question, request or topic "
    "they did not raise, and never turn a hesitation into a question. If their latest utterance is "
    "just filler or thinking-out-loud ('euh', 'hmm', 'bah', 'ok', 'voilà'…), an acknowledgement, or an "
    "unfinished fragment, do NOT fabricate: reply with the single word NOOP and nothing else. "
    "If the latest utterance is small talk or a pleasantry, suggest a natural, friendly reply. "
    "If project sources are provided, ground project facts in them and cite their ids like [doc] or "
    "[ticket REF]; never invent project facts. If web results are provided, use them for general "
    "facts and cite as [web N]. If you don't know, say what to answer honestly (e.g. offer to follow up)."
)

# The other participant said nothing worth answering (filler / hesitation / fragment). Matches the
# client's own gate; kept server-side as a safety net and to catch the model emitting the NOOP token.
_FILLER = re.compile(
    r"^(?:(?:e+u+h*|h+e+u+h*|h+u+h+|b+a+h+|b+e+n+|b+e+h+|e+h+|a+h+|o+h+|h+e+i+n+|h+u+m+|h+m+|m+h+|m+m+h?|"
    r"u+h+|u+m+|e+r+m+|mhm|bon|alors|donc|voil[àa]|ok(?:ay)?|d[’']?accord|ou+a+i+s?|ouaip|oui+|non+|"
    r"well|so|like|right|yeah|yep|nope)[\s.,!?…-]*)+$",
    re.IGNORECASE,
)


def is_filler(text: str) -> bool:
    return bool(_FILLER.match((text or "").strip()))

_LANG_NAMES = {"fr": "French (français)", "en": "English", "ar": "Arabic (العربية)", "es": "Spanish (español)",
               "de": "German (Deutsch)", "it": "Italian (italiano)", "pt": "Portuguese (português)", "nl": "Dutch"}
# Cheap script/marker cues to guess the conversation language when the user left it on auto-detect.
_ARABIC = re.compile(r"[؀-ۿ]")
_EN_MARKERS = re.compile(r"\b(the|you|and|is|are|what|how|why|can|could|would|please|thanks|hello|yes|no|we|they|this|that)\b", re.IGNORECASE)
_FR_MARKERS = re.compile(r"\b(le|la|les|un|une|des|est|vous|nous|je|tu|et|ou|mais|pour|avec|dans|bonjour|merci|oui|non|quoi|comment|pourquoi)\b|[àâçéèêëîïôùûœ]", re.IGNORECASE)


def _detect_language(text: str) -> str | None:
    """Best-effort language guess from a blob of conversation text. Returns an ISO code or None."""
    text = text.strip()
    if not text:
        return None
    if _ARABIC.search(text):
        return "ar"
    fr, en = len(_FR_MARKERS.findall(text)), len(_EN_MARKERS.findall(text))
    if fr == en == 0:
        return None
    return "fr" if fr >= en else "en"


def _language_directive(language: str | None, transcript: list[dict], utterance: str) -> str:
    """A firm instruction that locks the answer to ONE language for the whole call. The model was
    switching languages turn-to-turn (e.g. answering an English aside in English mid-French call);
    pinning the language — explicitly chosen by the user, or detected once from the conversation —
    stops that."""
    code = (language or "").strip().lower()[:5]
    if not code:
        blob = "\n".join(str(t.get("text", "")) for t in transcript[-12:]) + "\n" + utterance
        code = _detect_language(blob) or ""
    if code:
        name = _LANG_NAMES.get(code, code)
        return (f"CRITICAL: Write your entire answer in {name} and nothing else. This is the language of "
                f"the call — keep it for every turn. Do NOT switch languages even if the latest utterance, "
                f"an aside, a document, or a web result is in another language; translate what you need into "
                f"{name}. Only ever use another language if Moi explicitly asks you to.")
    return ("Answer in the main language of the conversation (infer it from the transcript). Use that SAME "
            "language for every turn of this call and never switch mid-conversation, even if one utterance, "
            "a document or a web result is in a different language.")


def is_question(text: str) -> bool:
    return bool(_QUESTION.search(text.strip()))


def _clean(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment)).strip()


def web_search(query: str, k: int = 4, timeout: float = 3.5) -> list[dict]:
    """Top web results via DuckDuckGo's HTML endpoint — keyless, so it works out of the box. Best
    effort: any failure/timeout just means the answer is produced without web context."""
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query[:300]},
                       headers={"User-Agent": "Mozilla/5.0 (DeliveryOps live assistant)"}, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("web search failed: %s", exc)
        return []
    out = []
    for href, title, snippet in _RESULT.findall(r.text):
        url = href
        if "uddg=" in href:  # DDG wraps results in a redirect: /l/?uddg=<real url>
            url = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if "duckduckgo.com/y.js" in url:  # ads
            continue
        out.append({"title": _clean(title), "url": url, "snippet": _clean(snippet)})
        if len(out) >= k:
            break
    return out


def prepare(db, *, utterance: str, perimeter: Perimeter | None, mode: str) -> dict:
    """All DB work happens here, before streaming starts (the request's DB session may be closed by
    the time the response body is streamed). Returns what `stream()` needs."""
    project_ctx, sources, use_project = "", [], False
    if perimeter is not None:
        # dense=False keeps this fast (no per-turn embeddings API call) — the live overlay needs a
        # sub-second first token; the lexical score is enough to tell if the turn is about the project.
        hits = knowledge.search(db, context.scope_perimeter_ids(db, perimeter), utterance, k=4, dense=False)
        names = {perimeter.name.lower(), (perimeter.client.name if perimeter.client else "").lower()} - {""}
        mentions = any(n in utterance.lower() for n in names)
        strong = bool(hits) and hits[0].score >= settings.knowledge_strong_hit
        # The user pinned this périmètre for the call: a substantive turn that finds anything in its
        # documentation is answered from the project first — a web result is never the right answer to
        # "can you take the GED migration in the fixed fee?" when the contract says what is out of scope.
        substantive = len(utterance.split()) >= 6 and not (bool(_SMALLTALK.search(utterance)) and len(utterance.split()) <= 14)
        use_project = mentions or strong or (bool(hits) and (bool(_PROJECT_WORDS.search(utterance)) or substantive))
        if use_project:
            tickets = db.scalars(select(Ticket).where(Ticket.perimeter_id == perimeter.id)).all()
            similar = similar_resolved(utterance, tickets, k=2)
            project_ctx = knowledge.format_context(hits)
            if similar:
                project_ctx += "\n" + "\n".join(
                    f'<source id="{t.ref}" cite="[ticket {t.ref}]">{t.kind} {t.priority} « {t.title} » — résolution : {t.resolution}</source>'
                    for t, _ in similar
                )
            sources = [h.as_source() for h in hits[:2]] + [ticket_source(t) for t, _ in similar[:1]]
            project_ctx = f'<project name="{perimeter.name}">\n{project_ctx}\n</project>'
    small_talk = bool(_SMALLTALK.search(utterance)) and len(utterance.split()) <= 14
    wants_web = not use_project and not small_talk and (mode == "ask" or is_question(utterance)) and len(utterance.split()) >= 3
    return {"project_ctx": project_ctx, "sources": sources, "route": "project" if use_project else ("web" if wants_web else "chat"), "wants_web": wants_web}


def stream(*, utterance: str, speaker: str, transcript: list[dict], user_context: str | None, prepared: dict,
           mode: str, language: str | None = None):
    """Yields SSE-ready dict events: meta → delta* → done."""
    # Nothing to answer (hesitation / filler / fragment on the other side): stay silent instead of
    # inventing a question. The client gates this too; this is the safety net (no LLM call, no source).
    if mode != "ask" and speaker != "Moi" and is_filler(utterance):
        yield {"type": "meta", "route": "chat", "sources": []}
        yield {"type": "done", "answer": "", "llm_used": True, "noop": True}
        return

    web: list[dict] = []
    if prepared["wants_web"]:
        web = web_search(utterance)
    sources = prepared["sources"] + [{"type": "web", "label": w["title"][:60], "ref": w["url"], "excerpt": w["snippet"][:200]} for w in web]
    yield {"type": "meta", "route": prepared["route"], "sources": sources}

    convo = "\n".join(f'{t.get("speaker", "?")}: {t.get("text", "").strip()}' for t in transcript[-16:] if t.get("text"))
    parts = []
    if user_context:
        parts.append(f"<call_context>\n{user_context.strip()[:3000]}\n</call_context>")
    if prepared["project_ctx"]:
        parts.append(prepared["project_ctx"])
    if web:
        parts.append("<web>\n" + "\n".join(f'[web {i+1}] {w["title"]} — {w["snippet"]} ({w["url"]})' for i, w in enumerate(web)) + "\n</web>")
    parts.append(f"<conversation>\n{convo}\n</conversation>" if convo else "")
    if mode == "ask":
        task = f"The user (Moi) asks you directly, off-call: « {utterance} ». Answer them directly and concisely."
    else:
        who = "the other participant (Interlocuteur)" if speaker != "Moi" else "the user (Moi)"
        task = f"Latest utterance from {who}: « {utterance} ». Suggest what Moi should say next."

    system = LIVE_SYSTEM + "\n\n" + _language_directive(language, transcript, utterance)
    text: list[str] = []
    llm_used = True
    context = "\n\n".join(p for p in parts if p)
    # The provider occasionally rejects a request outright (~0.5s, no tokens) when calls land back to
    # back; one quick retry recovers almost all of those. Never retried once tokens have been shown.
    for attempt in range(2):
        try:
            for chunk in llm.stream_text(task=task, context=context, tier="fast", purpose="LiveCopilot",
                                         system=system, thinking=False, max_tokens=300):
                text.append(chunk)
                yield {"type": "delta", "text": chunk}
            break
        except llm.LLMUnavailable as exc:
            if text or attempt == 1:
                log.warning("live copilot LLM failed: %s", exc)
                if not text:
                    llm_used = False
                    fallback = "Assistant IA momentanément indisponible — proposez de revenir vers eux avec l'information."
                    text = [fallback]
                    yield {"type": "delta", "text": fallback}
                break
            time.sleep(0.6)
    answer = "".join(text).strip()
    # The model may still decide there's nothing to answer and emit the NOOP sentinel — treat it as silence.
    if re.fullmatch(r"noop[.!\s]*", answer, re.IGNORECASE):
        yield {"type": "done", "answer": "", "llm_used": llm_used, "noop": True}
        return
    yield {"type": "done", "answer": answer, "llm_used": llm_used}
