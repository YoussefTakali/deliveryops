"""The work companion: an assistant that knows the user's data (their indexed Microsoft 365 + local
files, see app/assistant.py), knows what they are doing right now (opt-in activity sessions sent by
the desktop app), offers help on its own initiative, and adapts to each person.

Design rules
  - Privacy first, because this ships to regulated firms: tracking is opt-in per user AND allowed
    per org policy; sensitive windows (password managers, banking, private browsing…) are dropped
    before storage; titles can be disabled org-wide; data expires (retention) and the user can
    export or erase everything. Admin monitoring only ever sees aggregates, never window titles.
  - Learning is explicit and inspectable: what the companion "knows" is a list of plain-text
    memories the user can read, edit, pin or delete; how much they value each kind of proactive help
    is a weight moved by their own accept/dismiss clicks. No opaque fine-tuning — it works with any
    hosted LLM and degrades to deterministic behaviour with none.
  - Proactive help is deterministic to *trigger* (cheap, predictable, auditable) and only uses the
    LLM to *talk* (chat, briefs).
"""

import hashlib
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import assistant, knowledge, llm
from app.config import get_settings
from app.models import ActivityEvent, CompanionMemory, CompanionMessage, CompanionPolicy, CompanionProfile, CompanionSuggestion

log = logging.getLogger(__name__)
settings = get_settings()

JOB_ROLES = ("employee", "manager", "hr", "consultant", "executive", "assistant")
TONES = ("warm", "neutral", "direct")
MEMORY_KINDS = ("preference", "fact", "routine", "person", "project")

# Never recorded, whatever the policy says (the org policy can only ADD to these).
ALWAYS_EXCLUDED_KEYWORDS = (
    "password", "mot de passe", "keepass", "1password", "bitwarden", "lastpass", "dashlane", "inprivate", "incognito",
    "navigation privée", "private browsing", "banque", "bank", "paypal", "credit card", "carte bancaire", "payslip",
    "bulletin de paie", "fiche de paie", "médical", "medical", "ameli", "impots.gouv",
)
ALWAYS_EXCLUDED_APPS = ("keepass", "keepassxc", "1password", "bitwarden", "lockapp", "logonui", "credentialuibroker")

_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("meeting", ("teams", "ms-teams", "zoom", "webex", "meet", "slack huddle", "skype")),
    ("mail", ("outlook", "olk", "thunderbird", "hxoutlook", "mail")),
    ("documents", ("winword", "word", "acrobat", "acrord32", "notepad", "onenote", "foxit", "sumatrapdf", "writer")),
    ("spreadsheets", ("excel", "calc")),
    ("slides", ("powerpnt", "powerpoint", "impress", "keynote")),
    ("code", ("code", "devenv", "idea", "pycharm", "webstorm", "rider", "windowsterminal", "powershell", "cmd", "wt")),
    ("chat", ("slack", "discord", "whatsapp", "telegram", "signal")),
    ("browser", ("chrome", "msedge", "firefox", "brave", "opera", "arc")),
    ("files", ("explorer", "totalcmd")),
    ("planning", ("planner", "project", "jira", "trello", "asana", "todo")),
]
_TITLE_HINTS = (("meeting", ("réunion", "meeting", "| microsoft teams", "zoom meeting")), ("mail", ("boîte de réception", "inbox", "outlook")))
_APP_SUFFIX = re.compile(r"\s+[-–—|]\s+(microsoft\s+)?(word|excel|powerpoint|outlook|teams|edge|google chrome|mozilla firefox|visual studio code|notepad|bloc-notes|adobe acrobat.*|onenote)\s*$", re.IGNORECASE)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- policy / profile

def policy(db: Session) -> CompanionPolicy:
    p = db.get(CompanionPolicy, "org")
    if p is None:
        p = CompanionPolicy(id="org")
        db.add(p)
        db.flush()
    return p


def profile(db: Session, user) -> CompanionProfile:
    p = db.get(CompanionProfile, user.username)
    if p is None:
        p = CompanionProfile(owner=user.username, display_name=(user.name or user.username).split("@")[0], nudge_weights={})
        db.add(p)
        db.flush()
    return p


def tracking_active(prof: CompanionProfile, pol: CompanionPolicy) -> bool:
    return bool(pol.enabled and pol.activity_tracking_allowed and prof.tracking_enabled)


# ---------------------------------------------------------------- activity

def categorize(app: str, title: str = "") -> str:
    a, t = (app or "").lower().replace(".exe", ""), (title or "").lower()
    for cat, hints in _TITLE_HINTS:
        if any(h in t for h in hints) and cat == "meeting" and ("teams" in a or "zoom" in a):
            return "meeting"
    for cat, names in _CATEGORIES:
        if any(a == n or a.startswith(n) for n in names):
            return cat
    return "other"


def _sensitive(app: str, title: str, pol: CompanionPolicy) -> bool:
    a, t = (app or "").lower().replace(".exe", ""), (title or "").lower()
    apps = set(ALWAYS_EXCLUDED_APPS) | {str(x).lower().replace(".exe", "") for x in (pol.excluded_apps or [])}
    words = set(ALWAYS_EXCLUDED_KEYWORDS) | {str(x).lower() for x in (pol.excluded_keywords or []) if str(x).strip()}
    return a in apps or any(w in t for w in words)


def clean_title(title: str) -> str:
    return _APP_SUFFIX.sub("", (title or "").strip())[:300]


def record_activity(db: Session, owner: str, events: list[dict], pol: CompanionPolicy) -> int:
    """Store focus sessions reported by the desktop app. Sensitive windows are dropped, titles are
    blanked when the org only allows app-level capture, consecutive identical sessions are merged,
    and anything older than the retention window is purged on the way."""
    stored = 0
    last = db.scalars(select(ActivityEvent).where(ActivityEvent.owner == owner).order_by(ActivityEvent.started_at.desc()).limit(1)).first()
    for e in events[:200]:
        app = str(e.get("app") or "")[:120]
        title = str(e.get("title") or "")
        seconds = max(0, min(int(e.get("seconds") or 0), 4 * 3600))
        if not app or seconds < 3 or _sensitive(app, title, pol):
            continue
        try:
            started = _aware(datetime.fromisoformat(str(e.get("started_at")).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            continue
        if started is None or started > _now() + timedelta(minutes=5):
            continue
        title = "" if pol.title_capture == "app_only" else clean_title(title)
        if last is not None and last.app == app and last.title == title and \
                (started - (_aware(last.started_at) + timedelta(seconds=last.seconds))).total_seconds() < 90:
            last.seconds += seconds
        else:
            last = ActivityEvent(owner=owner, started_at=started, seconds=seconds, app=app, title=title, category=categorize(app, title))
            db.add(last)
        stored += 1
    cutoff = _now() - timedelta(days=max(1, pol.retention_days))
    db.execute(delete(ActivityEvent).where(ActivityEvent.owner == owner, ActivityEvent.started_at < cutoff))
    db.flush()
    return stored


def sessions_since(db: Session, owner: str, since: datetime) -> list[ActivityEvent]:
    return list(db.scalars(select(ActivityEvent).where(ActivityEvent.owner == owner, ActivityEvent.started_at >= since).order_by(ActivityEvent.started_at)))


def today_summary(db: Session, owner: str, tz_offset_min: int = 0) -> dict:
    """What the day looked like so far: time per category, top documents, current focus."""
    local_now = _now() + timedelta(minutes=tz_offset_min)
    day_start = (local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=tz_offset_min))
    rows = sessions_since(db, owner, day_start)
    by_cat: Counter = Counter()
    by_doc: Counter = Counter()
    for r in rows:
        by_cat[r.category] += r.seconds
        if r.title:
            by_doc[(r.app, r.title)] += r.seconds
    current = rows[-1] if rows else None
    is_live = bool(current and (_now() - (_aware(current.started_at) + timedelta(seconds=current.seconds))).total_seconds() < 180)
    return {
        "tracked_seconds": sum(by_cat.values()),
        "by_category": [{"category": c, "seconds": s} for c, s in by_cat.most_common()],
        "top": [{"app": a, "title": t, "seconds": s} for (a, t), s in by_doc.most_common(6)],
        "switches": max(0, len(rows) - 1),
        "now": {"app": current.app, "title": current.title, "category": current.category, "seconds": current.seconds} if is_live else None,
    }


# ---------------------------------------------------------------- memory

_REMEMBER = re.compile(r"^\s*(souviens[- ]toi|retiens|n'oublie pas|note)\s+(que\s+|qu')?(?P<fr>.+)|^\s*(remember|note|keep in mind)\s+(that\s+)?(?P<en>.+)", re.IGNORECASE | re.DOTALL)


def explicit_memory(message: str) -> str | None:
    m = _REMEMBER.match(message or "")
    if not m:
        return None
    text = (m.group("fr") or m.group("en") or "").strip().rstrip(".")
    return text[:500] if len(text) >= 4 else None


def _similar(a: str, b: str) -> float:
    ta, tb = set(knowledge.tokens(a)), set(knowledge.tokens(b))
    return len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0


def remember(db: Session, owner: str, content: str, kind: str = "fact", source: str = "user", confidence: float = 1.0) -> CompanionMemory | None:
    """Add a memory unless the companion already knows (nearly) the same thing."""
    content = (content or "").strip()[:500]
    if len(content) < 4:
        return None
    existing = db.scalars(select(CompanionMemory).where(CompanionMemory.owner == owner)).all()
    for m in existing:
        if _similar(m.content, content) >= 0.8:
            if source == "user" and m.source != "user":  # the user confirming a learned memory hardens it
                m.source, m.confidence = "user", 1.0
            return m
    if len(existing) >= 300:  # keep the prompt budget sane: drop the weakest learned, unpinned memory
        weakest = min((m for m in existing if not m.pinned and m.source != "user"), key=lambda m: (m.confidence, m.uses), default=None)
        if weakest is None:
            return None
        db.delete(weakest)
    mem = CompanionMemory(owner=owner, kind=kind if kind in MEMORY_KINDS else "fact", content=content, source=source, confidence=confidence)
    db.add(mem)
    db.flush()
    return mem


def relevant_memories(db: Session, owner: str, query: str = "", n: int = 14) -> list[CompanionMemory]:
    rows = db.scalars(select(CompanionMemory).where(CompanionMemory.owner == owner)).all()

    def rank(m: CompanionMemory) -> float:
        return (2.0 if m.pinned else 0.0) + (1.0 if m.kind == "preference" else 0.0) + 2.5 * _similar(m.content, query) + 0.3 * m.confidence

    chosen = sorted(rows, key=rank, reverse=True)[:n]
    for m in chosen:
        m.uses += 1
        m.last_used_at = _now()
    return chosen


class _Learned(BaseModel):
    kind: str = "fact"
    content: str
    confidence: float = Field(0.6, ge=0, le=1)


class _Distilled(BaseModel):
    memories: list[_Learned] = Field(default_factory=list)


def distill(db: Session, owner: str) -> int:
    """Learn durable things from the recent conversation (preferences, people, projects, routines).
    Runs every few messages. Only adds; the user curates the result in "Ce que je sais de vous"."""
    msgs = db.scalars(select(CompanionMessage).where(CompanionMessage.owner == owner).order_by(CompanionMessage.ts.desc()).limit(30)).all()
    if len(msgs) < 4:
        return 0
    convo = "\n".join(f"{m.role}: {m.content[:600]}" for m in reversed(msgs))
    known = "\n".join(f"- {m.content}" for m in db.scalars(select(CompanionMemory).where(CompanionMemory.owner == owner)).all()[:80])
    result, used = llm.structured(
        schema=_Distilled, tier="agent", thinking=False, max_tokens=700,
        system=("Tu maintiens la mémoire d'un assistant personnel de travail. À partir d'une conversation, tu extrais "
                "UNIQUEMENT des informations durables et utiles pour mieux aider cette personne à l'avenir : préférences "
                "de travail ou de communication (kind=preference), faits professionnels stables (fact), habitudes/horaires "
                "(routine), personnes avec qui elle travaille et leur rôle (person), projets/clients en cours (project). "
                "Jamais de données sensibles (santé, opinions, vie privée, finances personnelles, identifiants). Jamais "
                "d'information ponctuelle ou déjà connue. Phrases courtes à la 3e personne. Liste vide si rien de durable."),
        context=f"<deja_connu>\n{known or '(rien)'}\n</deja_connu>\n\n<conversation>\n{convo}\n</conversation>",
        task="Extrais au plus 5 nouvelles mémoires durables.",
        fallback=lambda: _Distilled(),
    )
    added = 0
    if used:
        for item in result.memories[:5]:
            before = db.scalar(select(func.count()).select_from(CompanionMemory).where(CompanionMemory.owner == owner))
            remember(db, owner, item.content, item.kind, source="learned", confidence=item.confidence)
            after = db.scalar(select(func.count()).select_from(CompanionMemory).where(CompanionMemory.owner == owner))
            added += int((after or 0) > (before or 0))
    return added


# ---------------------------------------------------------------- proactive suggestions

# kind -> (base priority, cool-down before the same kind may be offered again)
NUDGES: dict[str, tuple[float, timedelta]] = {
    "stuck_help": (1.2, timedelta(minutes=10)),   # highest: jump in fast when someone is clearly struggling
    "meeting_prep": (1.0, timedelta(minutes=20)),
    "meeting_followup": (0.9, timedelta(minutes=45)),
    "daily_brief": (0.85, timedelta(hours=16)),
    "context_help": (0.7, timedelta(minutes=40)),
    "deep_work_summary": (0.65, timedelta(hours=2)),
    "automate_repetition": (0.6, timedelta(hours=24)),
    "focus_break": (0.5, timedelta(hours=2)),
    "day_recap": (0.55, timedelta(hours=16)),
}
NUDGE_LABELS = {
    "stuck_help": "Coup de main", "meeting_prep": "Préparation de réunion", "meeting_followup": "Suite de réunion",
    "daily_brief": "Brief du matin", "context_help": "Aide sur le document en cours", "deep_work_summary": "Point d'étape sur un long travail",
    "automate_repetition": "Automatiser une tâche répétitive", "focus_break": "Pause", "day_recap": "Bilan de journée",
}

# What "being stuck" looks like in a stream of window titles.
_ERROR_RE = re.compile(r"\b(error|erreur|failed|échec|echec|cannot|can't|not recognized|n'est pas reconnu|introuvable|not found|"
                       r"exception|refus|denied|permission|impossible|invalid|unable|traceback|is not recognized)\b", re.IGNORECASE)
_INSTALL_RE = re.compile(r"\b(install(er|ing|ation)?|download(er|ing)?|télécharger|telecharger|setup|winget|choco(latey)?|"
                         r"npm i|pip install|apt install|configure|configurer|mettre en place)\b", re.IGNORECASE)
# Common things people try to install/set up — used to name the task nicely when detected. Detection
# does NOT depend on this list (the subject is extracted generically); it only makes the label prettier.
_TECH = ("node.js", "nodejs", "node", "npm", "python", "pip", "docker", "git", "vs code", "vscode", "visual studio",
         "java", "jdk", "cuda", "ffmpeg", "postgres", "postgresql", "mysql", "wsl", "chrome", "anaconda", "conda", "pytorch",
         "tensorflow", "angular", "react", ".net", "dotnet", "rust", "golang", "kubernetes", "kubectl", "terraform",
         "kafka", "spark", "hadoop", "redis", "mongodb", "mongo", "nginx", "maven", "gradle", "jenkins", "helm",
         "minikube", "elasticsearch", "rabbitmq", "zookeeper", "flink", "airflow", "grafana", "prometheus", "ansible")
_STOP_TOK = set("the and for with this that how comment pour est how-to tuto tutorial guide http https www com org "
                "new nouvel nouvelle onglet tab home accueil page google chrome microsoft edge mozilla firefox "
                "recherche search results résultats resultats bing you tube fenetre fenêtre window profil profile "
                "settings paramètres parametres télécharger telecharger download install installer installation "
                "setup configurer configure".split())
# Install/download keyword tokens — stripped when guessing WHAT is being installed (the subject).
_INSTALL_WORDS = {"install", "installer", "installing", "installation", "download", "downloading", "downloader",
                  "télécharger", "telecharger", "setup", "winget", "choco", "chocolatey", "configure", "configurer"}


def _topic_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ0-9.#+]{3,}", (title or "").lower()) if w not in _STOP_TOK}


def _detect_stuck(recent: list, now: datetime) -> dict | None:
    """Heuristic 'is the user struggling with something?' from the last ~20 min of window titles:
    they keep returning to the same task, hit errors, or retry an install/setup. Returns a readable
    topic + the recent titles (evidence for the chat), or None. Deliberately conservative."""
    window = [r for r in recent if r.title and (now - _aware(r.started_at)) < timedelta(minutes=20)]
    if len(window) < 2:
        return None
    titles = [r.title for r in window]
    joined = " || ".join(titles).lower()
    errors = sum(1 for t in titles if _ERROR_RE.search(t))
    install_titles = [t for t in titles if _INSTALL_RE.search(t)]
    installs = len(install_titles)

    # tokens the user keeps coming back to (recurring focus = a task they can't finish)
    counts: Counter = Counter()
    for t in titles:
        counts.update(_topic_tokens(t))
    recurring = [tok for tok, c in counts.items() if c >= 3 and tok not in {"exe", "msi"}]

    # WHAT is being installed/downloaded — extracted generically from the install/download titles
    # (works for ANY software, e.g. Kafka, not just a hard-coded list): the token shared across them.
    subj_counts: Counter = Counter()
    for t in install_titles:
        subj_counts.update(_topic_tokens(t) - _INSTALL_WORDS)
    install_subject = [tok for tok, c in subj_counts.most_common() if c >= 2 and tok not in {"exe", "msi"}]

    tech = next((name for name in _TECH if re.search(r"\b" + re.escape(name) + r"\b", joined)), None)

    def _install_topic() -> str:
        if tech:
            return f"installer {tech}"
        subj = install_subject[:2] or recurring[:3]
        return "installer « " + " ".join(subj) + " »" if subj else "installer ce logiciel"

    # Clear install/download intent about one subject (≥2 tries) → offer help fast; or an install plus
    # an error; or the same task circled repeatedly; or errors that keep recurring.
    if installs >= 2 and (tech or install_subject or recurring):
        topic = _install_topic()
    elif installs >= 1 and errors >= 1 and (tech or install_subject or recurring):
        topic = _install_topic()
    elif errors >= 2 and recurring:
        topic = "résoudre « " + " ".join(recurring[:3]) + " »"
    elif len(recurring) >= 1 and len(window) >= 5 and len({(r.app, r.title) for r in window}) >= 3:
        topic = "avancer sur « " + " ".join(recurring[:3]) + " »"
    else:
        return None
    # a compact, de-duplicated trail of what they tried, newest last
    trail, seen = [], set()
    for t in titles[-10:]:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            trail.append(t.strip()[:120])
    return {"topic": topic, "tech": tech, "errors": errors, "trail": trail}


def _in_quiet_hours(prof: CompanionProfile, local_now: datetime) -> bool:
    if not (prof.quiet_start and prof.quiet_end):
        return False
    try:
        hs, ms = map(int, prof.quiet_start.split(":"))
        he, me = map(int, prof.quiet_end.split(":"))
    except ValueError:
        return False
    cur, start, end = local_now.hour * 60 + local_now.minute, hs * 60 + ms, he * 60 + me
    return start <= cur < end if start <= end else (cur >= start or cur < end)


def _t(prof: CompanionProfile, fr: str, en: str) -> str:
    return en if prof.language == "en" else fr


def _candidates(db: Session, user, prof: CompanionProfile, tz_offset_min: int) -> list[dict]:
    owner = prof.owner
    now = _now()
    local_now = now + timedelta(minutes=tz_offset_min)
    out: list[dict] = []
    recent = sessions_since(db, owner, now - timedelta(hours=3))
    live = [r for r in recent if (now - (_aware(r.started_at) + timedelta(seconds=r.seconds))).total_seconds() < 180]
    current = live[-1] if live else None

    # 0) the user seems stuck (retrying an install, hitting errors, circling back to the same task) →
    #    offer concrete help immediately. Highest priority. Only when tracking is on (there's activity).
    stuck = _detect_stuck(recent, now) if current is not None else None
    if stuck:
        topic = stuck["topic"]
        trail = "\n".join(f"- {t}" for t in stuck["trail"])
        # Accepting this launches the agentic task flow ("Faire"): it plans the install, asks the
        # deciding questions (version/source), shows the exact commands and does it — not just chat.
        out.append({"kind": "stuck_help", "fingerprint": f"stuck:{topic}:{local_now:%Y-%m-%d-%H}",
                    "title": _t(prof, f"Besoin d'un coup de main pour {topic} ?", f"Need a hand to {topic}?"),
                    "body": _t(prof, "On dirait que ça coince — je peux le faire pour vous (je vous montre chaque commande avant).",
                               "Looks like you're stuck — I can do it for you (showing each command before it runs)."),
                    "action": {"type": "task", "prompt": topic, "chat_prompt": _t(prof,
                        f"J'essaie de {topic} sur mon PC (Windows) et je bloque. Voici mes dernières fenêtres :\n{trail}\n\n"
                        "Aide-moi à y arriver : les étapes exactes, la commande précise, et comment vérifier que c'est bon.",
                        f"I'm trying to {topic} on my Windows PC and I'm stuck. My recent windows:\n{trail}\n\n"
                        "Help me get it done: exact steps, the precise command, and how to check it worked.")}})

    # 1) a meeting is about to start (cached agenda, refreshed whenever a Microsoft token is around)
    for ev in prof.agenda or []:
        try:
            start = _aware(datetime.fromisoformat(str(ev.get("start"))[:19]))
        except (TypeError, ValueError):
            continue
        minutes = (start - now).total_seconds() / 60 if start else -1
        if 0 < minutes <= 30:
            who = ", ".join((ev.get("attendees") or [])[:4])
            subject = ev.get("subject") or _t(prof, "réunion", "meeting")
            out.append({"kind": "meeting_prep", "fingerprint": f"prep:{subject}:{str(ev.get('start'))[:16]}",
                        "title": _t(prof, f"« {subject} » commence dans {int(minutes)} min", f"“{subject}” starts in {int(minutes)} min"),
                        "body": _t(prof, f"Je vous prépare un brief{f' (avec {who})' if who else ''} : contexte, derniers échanges, points à aborder ?",
                                   f"Want a quick brief{f' (with {who})' if who else ''}: context, latest exchanges, talking points?"),
                        "action": {"type": "chat", "prompt": _t(prof, f"Prépare-moi un brief pour ma réunion « {subject} »{f' avec {who}' if who else ''} : contexte dans mes mails et fichiers, points à aborder, questions à poser.",
                                                                 f"Prepare a brief for my meeting “{subject}”{f' with {who}' if who else ''}: context from my mail and files, talking points, questions to ask.")}})

    # 2) a meeting just ended → minutes / follow-up
    meetings = [r for r in recent if r.category == "meeting" and r.seconds >= 600]
    if meetings and (current is None or current.category != "meeting"):
        last = meetings[-1]
        ended = _aware(last.started_at) + timedelta(seconds=last.seconds)
        if timedelta(minutes=1) < now - ended < timedelta(minutes=25):
            label = last.title or _t(prof, "votre réunion", "your meeting")
            out.append({"kind": "meeting_followup", "fingerprint": f"followup:{last.id}",
                        "title": _t(prof, f"Réunion terminée — « {label[:80]} »", f"Meeting over — “{label[:80]}”"),
                        "body": _t(prof, "Je peux rédiger le mail de suivi et la liste d'actions pendant que c'est frais.", "I can draft the follow-up email and action list while it's fresh."),
                        "action": {"type": "chat", "prompt": _t(prof, f"Aide-moi à rédiger le mail de suivi et la liste d'actions de ma réunion « {label} ». Pose-moi les questions nécessaires.",
                                                                 f"Help me write the follow-up email and action list for my meeting “{label}”. Ask me what you need.")}})

    # 3) first activity of the day → morning brief
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=tz_offset_min)
    today = [r for r in recent if _aware(r.started_at) >= day_start]
    if 6 <= local_now.hour < 12 and (today or not prof.tracking_enabled):
        out.append({"kind": "daily_brief", "fingerprint": f"brief:{local_now:%Y-%m-%d}",
                    "title": _t(prof, f"Bonjour {prof.display_name or ''} — votre brief du jour est prêt".replace("  ", " "), f"Good morning {prof.display_name or ''} — your daily brief is ready"),
                    "body": _t(prof, "Agenda, points en attente et priorités, en une minute.", "Agenda, pending items and priorities, in one minute."),
                    "action": {"type": "brief", "brief": "morning"}})

    if current is not None and current.title:
        # 4) the companion holds material related to what's on screen
        if current.category in ("documents", "spreadsheets", "slides", "mail", "browser", "files") and current.seconds >= 120:
            ws = assistant.workspace(db, user)
            hits = [h for h in knowledge.search(db, ws.id, current.title, k=3, dense=False)
                    if h.score >= 0.4 and current.title.lower()[:40] not in h.filename.lower()]
            if hits:
                names = ", ".join(sorted({h.filename.split("/")[-1][:50] for h in hits})[:3])
                out.append({"kind": "context_help", "fingerprint": f"ctx:{current.title[:120]}",
                            "title": _t(prof, f"J'ai des éléments liés à « {current.title[:70]} »", f"I have material related to “{current.title[:70]}”"),
                            "body": _t(prof, f"Notamment : {names}. Je vous fais une synthèse ?", f"Including: {names}. Want a summary?"),
                            "action": {"type": "chat", "prompt": _t(prof, f"Je travaille sur « {current.title} ». Retrouve ce que j'ai de lié dans mes mails et fichiers et fais-moi une synthèse utile.",
                                                                     f"I'm working on “{current.title}”. Find related material in my mail and files and give me a useful summary.")}})
        # 5) a long stretch on one document → offer a checkpoint
        if current.seconds >= 50 * 60 and current.category in ("documents", "spreadsheets", "slides", "code"):
            out.append({"kind": "deep_work_summary", "fingerprint": f"deep:{current.title[:120]}:{local_now:%Y-%m-%d}",
                        "title": _t(prof, f"{current.seconds // 60} min sur « {current.title[:70]} »", f"{current.seconds // 60} min on “{current.title[:70]}”"),
                        "body": _t(prof, "Un regard extérieur ? Je peux relire, structurer ou lister ce qu'il reste à faire.", "A second pair of eyes? I can review, structure, or list what's left."),
                        "action": {"type": "chat", "prompt": _t(prof, f"Je travaille depuis un moment sur « {current.title} ». Aide-moi à faire le point : que reste-t-il, comment structurer la suite ?",
                                                                 f"I've been working on “{current.title}” for a while. Help me take stock: what's left, how should I structure the rest?")}})

    # 6) ping-pong between the same two apps → an automation candidate
    hour = [r for r in recent if now - _aware(r.started_at) < timedelta(hours=1)]
    pairs: Counter = Counter()
    for a, b in zip(hour, hour[1:]):
        if a.app != b.app:
            pairs[tuple(sorted((a.app, b.app)))] += 1
    if pairs:
        (a, b), n = pairs.most_common(1)[0]
        if n >= 14:
            out.append({"kind": "automate_repetition", "fingerprint": f"auto:{a}:{b}:{local_now:%Y-%m-%d}",
                        "title": _t(prof, f"{n} allers-retours entre {a} et {b} en une heure", f"{n} switches between {a} and {b} in an hour"),
                        "body": _t(prof, "Ça ressemble à une tâche répétitive : on l'enregistre une fois et je la ferai pour vous ?", "Looks repetitive: record it once and I'll do it for you?"),
                        "action": {"type": "link", "link": "/automations/new"}})

    # 7) wellbeing: long continuous work without a gap
    if live:
        streak, prev_end = 0, None
        for r in recent:
            start = _aware(r.started_at)
            if prev_end is not None and (start - prev_end).total_seconds() > 300:
                streak = 0
            streak += r.seconds
            prev_end = start + timedelta(seconds=r.seconds)
        if streak >= 110 * 60:
            out.append({"kind": "focus_break", "fingerprint": f"break:{local_now:%Y-%m-%d-%H}",
                        "title": _t(prof, f"{streak // 60} minutes sans pause", f"{streak // 60} minutes without a break"),
                        "body": _t(prof, "Cinq minutes loin de l'écran et vous repartez plus efficace. Je garde le fil.", "Five minutes away from the screen pays off. I'll keep your place."),
                        "action": {"type": "none"}})

    # 8) end of day recap
    if local_now.hour >= 17 and sum(r.seconds for r in today) >= 3 * 3600:
        out.append({"kind": "day_recap", "fingerprint": f"recap:{local_now:%Y-%m-%d}",
                    "title": _t(prof, "Bilan de votre journée", "Your day in review"),
                    "body": _t(prof, "Ce que vous avez avancé, ce qui reste, et de quoi démarrer demain sereinement.", "What moved, what's left, and a clean start for tomorrow."),
                    "action": {"type": "brief", "brief": "evening"}})
    return out


def generate_suggestions(db: Session, user, prof: CompanionProfile, pol: CompanionPolicy, tz_offset_min: int = 0) -> list[CompanionSuggestion]:
    """Turn the current situation into at most a couple of new offers, honouring what this user has
    taught us (weights), quiet hours, per-kind cool-downs, dedupe and the org's hourly cap."""
    owner = prof.owner
    now = _now()
    if not (pol.enabled and pol.proactive_allowed and prof.proactive_enabled):
        return []
    if _in_quiet_hours(prof, now + timedelta(minutes=tz_offset_min)):
        return []
    # stale offers expire silently (no learning signal either way)
    for s in db.scalars(select(CompanionSuggestion).where(CompanionSuggestion.owner == owner, CompanionSuggestion.status == "new",
                                                          CompanionSuggestion.created_at < now - timedelta(hours=3))):
        s.status = "expired"
    last_hour = db.scalar(select(func.count()).select_from(CompanionSuggestion).where(
        CompanionSuggestion.owner == owner, CompanionSuggestion.created_at >= now - timedelta(hours=1))) or 0
    budget = max(0, pol.max_nudges_per_hour - last_hour)
    if not budget:
        return []
    weights = prof.nudge_weights or {}
    created: list[CompanionSuggestion] = []
    ranked = sorted(_candidates(db, user, prof, tz_offset_min), key=lambda c: NUDGES[c["kind"]][0] * float(weights.get(c["kind"], 1.0)), reverse=True)
    for c in ranked:
        if len(created) >= min(budget, 2):
            break
        weight = float(weights.get(c["kind"], 1.0))
        if weight < 0.3:  # the user taught us they don't want this kind
            continue
        base, cooldown = NUDGES[c["kind"]]
        recent_same = db.scalar(select(func.count()).select_from(CompanionSuggestion).where(
            CompanionSuggestion.owner == owner, CompanionSuggestion.kind == c["kind"], CompanionSuggestion.created_at >= now - cooldown))
        dup = db.scalar(select(func.count()).select_from(CompanionSuggestion).where(
            CompanionSuggestion.owner == owner, CompanionSuggestion.fingerprint == c["fingerprint"]))
        if recent_same or dup:
            continue
        s = CompanionSuggestion(owner=owner, kind=c["kind"], title=c["title"][:300], body=c["body"], action=c["action"],
                                fingerprint=c["fingerprint"][:200], score=round(base * weight, 3))
        db.add(s)
        created.append(s)
    db.flush()
    return created


def decide(db: Session, prof: CompanionProfile, sugg: CompanionSuggestion, status: str) -> None:
    """The learning signal. Accepting a kind of help makes it more likely, dismissing it less;
    "never" switches that kind off for this person (they can re-enable it in their preferences)."""
    if status not in ("accepted", "dismissed", "snoozed", "never"):
        raise ValueError(status)
    sugg.status, sugg.decided_at = status, _now()
    weights = dict(prof.nudge_weights or {})
    w = float(weights.get(sugg.kind, 1.0))
    if status == "accepted":
        w = min(2.0, w + 0.15)
    elif status == "dismissed":
        w = max(0.0, w - 0.25)
    elif status == "never":
        w = 0.0
    weights[sugg.kind] = round(w, 2)
    prof.nudge_weights = weights
    if status == "never":
        remember(db, prof.owner, f"Ne souhaite pas recevoir de suggestions de type « {NUDGE_LABELS.get(sugg.kind, sugg.kind)} ».", "preference", source="feedback")


# ---------------------------------------------------------------- doing tasks (agentic)

# What the companion tells the planner about the machine it acts on, so plans are concrete and safe:
# real Windows steps, official sources, exact commands the user can read before approving.
TASK_GUIDANCE = (
    "L'utilisateur veut que tu RÉALISES cette tâche sur son PC Windows, pas seulement que tu l'expliques. "
    "Produis des étapes concrètes et exécutables. Pour installer/configurer un logiciel, privilégie winget "
    "(ex. `winget install --id Python.Python.3.12 -e`) ou le site officiel de l'éditeur, jamais une source "
    "douteuse. Mets la commande EXACTE dans une étape local.powershell (l'utilisateur la verra et l'approuvera "
    "avant exécution). Ajoute une étape de vérification à la fin (ex. `python --version`). Ne demande JAMAIS de "
    "mot de passe, de carte bancaire, ni de créer un compte, et ne modifie pas de réglages de sécurité : si "
    "c'est nécessaire, dis-le en clair et laisse l'utilisateur le faire. Pose des questions (questions) seulement "
    "sur ce qui change réellement le résultat : version voulue, source de téléchargement, pour cet utilisateur "
    "ou tout le monde, ajout au PATH, etc."
)


def build_task_plan(workflow: dict) -> dict:
    """Turn a compiled workflow into a companion *task*, honouring "confirm each risky step": insert a
    confirmation gate before every side-effecting step so the run pauses and asks before each real
    change, and surface the exact PowerShell to approve. Read-only steps run without a gate. Returns
    the gated workflow plus the scripts (with sha256) the user must approve before running."""
    from app.automations import catalog

    steps = [s for s in (workflow.get("steps") or []) if isinstance(s, dict)]
    gated: list[dict] = []
    for s in steps:
        if s.get("type") == "flow.confirm":  # don't double-gate an existing confirm
            gated.append(s)
            continue
        entry = catalog.BY_TYPE.get(s.get("type"))
        if entry and entry.get("side_effect"):
            gated.append({"id": f"confirm_{s.get('id')}"[:40], "type": "flow.confirm", "on_error": "stop",
                          "params": {"message": f"Exécuter l'étape « {s.get('title') or s.get('type')} » ?"}})
        gated.append(s)
    gated = gated[:catalog.MAX_STEPS]
    scripts = []
    for s in gated:
        if s.get("type") == "local.powershell":
            script = str((s.get("params") or {}).get("script") or "")
            scripts.append({"step_id": s.get("id"), "title": s.get("title") or "Script PowerShell", "script": script[:8000],
                            "sha256": hashlib.sha256(script.encode("utf-8")).hexdigest()})
    return {"workflow": {**workflow, "steps": gated}, "scripts": scripts}


class _QOptions(BaseModel):
    options: list[str] = Field(default_factory=list)
    recommended: str = Field("", description="the ONE option you recommend (the safest / most common choice)")


class _QSuggestions(BaseModel):
    items: list[_QOptions] = Field(default_factory=list)


def suggest_answers(message: str, questions: list[str]) -> list[dict]:
    """For each clarifying question, propose a few short, concrete answer options the user can click
    (they can always type their own — the UI adds an 'Autre' field). Best-effort: on no LLM, returns
    the questions with no options (plain free-text). Options come back in question order (positional),
    so a reworded question can't misalign them."""
    questions = [q.strip() for q in (questions or []) if q and q.strip()][:5]
    if not questions:
        return []
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    result, used = llm.structured(
        schema=_QSuggestions, tier="agent", thinking=False, max_tokens=500,
        system=("Tu aides à préciser une tâche que l'utilisateur veut RÉALISER sur son PC Windows. Pour CHAQUE question, "
                "dans le MÊME ORDRE, propose 2 à 4 réponses possibles, courtes et concrètes (versions, sources de "
                "téléchargement, oui/non, options usuelles). Pas d'options absurdes ni de phrases : quelques mots max. "
                "Pour chaque question, indique dans `recommended` l'UNE des options que tu recommandes (le choix le plus sûr "
                "ou le plus courant). Renvoie une entrée par question (items), même si la liste d'options est vide pour une "
                "question ouverte."),
        context=f"<tache>\n{message[:600]}\n</tache>\n<questions>\n{numbered}\n</questions>",
        task="Donne, pour chaque question et dans l'ordre, 2 à 4 options de réponse cliquables.",
        fallback=lambda: _QSuggestions(),
    )
    opts_by_index: list[list[str]] = []
    rec_by_index: list[str] = []
    if used:
        for it in result.items:
            clean = []
            for o in it.options:
                o = str(o).strip().strip(".").strip()
                if o and o.lower() not in {c.lower() for c in clean}:
                    clean.append(o[:60])
            clean = clean[:4]
            rec = next((o for o in clean if o.lower() == str(it.recommended or "").strip().strip(".").lower()), clean[0] if clean else "")
            if rec:  # the recommended option always comes first
                clean = [rec] + [o for o in clean if o != rec]
            opts_by_index.append(clean)
            rec_by_index.append(rec)
    return [{"question": q, "options": opts_by_index[i] if i < len(opts_by_index) else [],
             "recommended": rec_by_index[i] if i < len(rec_by_index) else ""} for i, q in enumerate(questions)]


def task_step_summary(steps: list[dict]) -> list[dict]:
    from app.automations import catalog

    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        entry = catalog.BY_TYPE.get(s.get("type")) or {}
        out.append({"id": s.get("id"), "type": s.get("type"), "title": s.get("title") or entry.get("title") or s.get("type"),
                    "confirm": s.get("type") == "flow.confirm", "side_effect": bool(entry.get("side_effect"))})
    return out


# ---------------------------------------------------------------- conversation

_TONE = {
    "warm": "Ton chaleureux et attentionné, comme un(e) assistant(e) de confiance qui connaît bien la personne — jamais mièvre, jamais familier à l'excès.",
    "neutral": "Ton professionnel, posé et courtois.",
    "direct": "Ton direct et efficace : va à l'essentiel, pas de formules.",
}
_ROLE_FOCUS = {
    "employee": "Aide-la à s'organiser, à retrouver l'information, à rédiger et à avancer sur ses tâches.",
    "manager": "Elle encadre une équipe : pense suivi d'équipe, préparation de 1:1 et de comités, arbitrages, délégation, reporting vers le haut.",
    "hr": "Elle travaille aux RH : recrutement, onboarding, entretiens, politique interne. Extrême discrétion : jamais de donnée nominative sensible dans tes réponses au-delà de ce qu'elle a fourni.",
    "consultant": "Elle est consultante : livrables client, comptes rendus, propositions, suivi du temps, qualité rédactionnelle irréprochable.",
    "executive": "Elle est dirigeante : synthèses très courtes, décisions à prendre, risques, préparation de réunions à fort enjeu.",
    "assistant": "Elle est assistante de direction : agenda, déplacements, préparation de dossiers, relances.",
}


DEBUG_TAG = "[DÉBOGAGE]"


def is_debug(message: str | None) -> bool:
    """The UI's « Déboguer avec l'IA » prefixes the message with [DÉBOGAGE]."""
    return (message or "").lstrip().startswith(DEBUG_TAG)


def system_prompt(prof: CompanionProfile, debug: bool = False) -> str:
    lang = {"fr": "Réponds toujours en français.", "en": "Always answer in English."}.get(prof.language, "Réponds dans la langue de la personne, sans en changer en cours de conversation.")
    return (
        f"Tu es le compagnon de travail de {prof.display_name or 'cette personne'} dans DeliveryOps : une présence fiable qui connaît son "
        "contexte (ses mails, son agenda, ses fichiers, ce qu'elle fait en ce moment) et l'aide à traverser sa journée de travail. "
        f"{_TONE.get(prof.tone, _TONE['warm'])} {_ROLE_FOCUS.get(prof.job_role, _ROLE_FOCUS['employee'])} {lang} "
        "Appuie-toi sur <memoire> pour t'adapter à ses préférences sans les réciter. Appuie-toi sur <activite> pour comprendre ce "
        "qu'elle fait sans le lui rappeler lourdement. Tu vois aussi le côté livraison : <projet_courant> (projet en cours, équipe, "
        "plan de charge) et <livraison> (documents des projets/périmètres auxquels elle a accès) — sers-t'en pour répondre sur les "
        "projets, les livrables, la charge ou l'équipe, en citant la source. Quand tu utilises ses données, cite la source entre crochets. N'invente jamais "
        "un mail, une réunion, un chiffre ou un nom : si tu ne sais pas, dis-le et propose comment le trouver. Sois concis : des "
        "réponses actionnables, des listes courtes quand c'est utile, et propose la prochaine étape concrète. Quand tu proposes "
        "plusieurs options ou pistes, marque toujours UNE seule d'entre elles « (recommandé) » et dis pourquoi en une phrase."
        # Only in debug mode: stating the rule unconditionally made models echo the tag in ordinary answers.
        + (" Mode débogage : la personne a rencontré une erreur dans DeliveryOps et veut la résoudre avec toi. Explique la "
           "cause la plus probable en une ou deux phrases simples, propose 2 à 3 corrections concrètes et numérotées dont UNE "
           "marquée « (recommandé) », puis pose UNE seule question si une information te manque pour trancher. N'écris pas "
           "le mot-clé de débogage dans ta réponse." if debug else "")
    )


def _delivery_context(db: Session, user, message: str, perimeter_id: str | None, project_id: str | None) -> tuple[str, list[dict]]:
    """Bridge the companion to delivery data (Phase B): a structured snapshot of the focused project
    (team, plan de charge) plus retrieval across the relevant delivery périmètres — scoped to what the
    user is allowed to see. Falls back to the user's whole visible portfolio when nothing is in focus."""
    from app import context, staffing
    from app.models import DeliveryProject, Perimeter

    project = None
    pids: list[str] = []
    if perimeter_id:
        p = db.get(Perimeter, perimeter_id)
        if p and user.can_see(p.client_id):
            pids = context.scope_perimeter_ids(db, p)
            project = context.project_of(db, p)
    elif project_id:
        proj = db.get(DeliveryProject, project_id)
        if proj and user.can_see(proj.client_id):
            project = proj
            pids = [pp.id for pp in db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id))]
    if not pids:
        pids = context.visible_perimeter_ids(db, user)
    if not pids:
        return "", []   # purely-personal user: stay personal

    parts: list[str] = []
    if project:
        snap = [f"Projet « {project.name} » (client {project.client.name}), manager {project.manager or '—'}, statut {project.status}."]
        if project.technologies:
            snap.append("Technologies : " + ", ".join(project.technologies))
        team = context.project_team(db, project)
        if team:
            snap.append("Équipe : " + ", ".join(f"{m['name']} ({m['role']})" for m in team))
        plan = context.latest_project_plan(db, project)
        if plan:
            tot = (plan.draft or {}).get("totals") or {}
            snap.append(f"Plan de charge : {tot.get('days', '?')} jours, {tot.get('cost', '?')} €.")
        parts.append("<projet_courant>\n" + "\n".join(snap) + "\n</projet_courant>")

    hits = knowledge.search(db, pids, message, k=6)
    sources = [h.as_source() for h in hits]
    if hits:
        parts.append("<livraison>\n" + knowledge.format_context(hits) + "\n</livraison>")
    return "\n\n".join(parts), sources


def build_context(db: Session, user, prof: CompanionProfile, pol: CompanionPolicy, message: str, tz_offset_min: int = 0,
                  scope_perimeter_id: str | None = None, scope_project_id: str | None = None) -> tuple[str, list[dict]]:
    local_now = _now() + timedelta(minutes=tz_offset_min)
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    parts = [f"<maintenant>{jours[local_now.weekday()]} {local_now:%d/%m/%Y %H:%M} (heure locale)</maintenant>"]
    mems = relevant_memories(db, prof.owner, message)
    if mems:
        parts.append("<memoire>\n" + "\n".join(f"- ({m.kind}) {m.content}" for m in mems) + "\n</memoire>")
    if tracking_active(prof, pol):
        day = today_summary(db, prof.owner, tz_offset_min)
        lines = []
        if day["now"]:
            lines.append(f"En ce moment : {day['now']['app']} — « {day['now']['title'] or '?'} » depuis {day['now']['seconds'] // 60} min")
        lines += [f"Aujourd'hui : {d['seconds'] // 60} min sur « {d['title']} » ({d['app']})" for d in day["top"][:5]]
        if lines:
            parts.append("<activite>\n" + "\n".join(lines) + "\n</activite>")
    upcoming = [e for e in (prof.agenda or []) if str(e.get("start", ""))[:10] >= f"{local_now:%Y-%m-%d}"][:8]
    if upcoming:
        parts.append("<agenda>\n" + "\n".join(f"- {e.get('start')} → {e.get('end')} « {e.get('subject')} » avec {', '.join(e.get('attendees') or []) or '—'}" for e in upcoming) + "\n</agenda>")
    ws = assistant.workspace(db, user)
    query = message
    if tracking_active(prof, pol) and re.search(r"\b(ce document|ce fichier|ce mail|ça|this (doc|file|email)|sur quoi je travaille|what i'?m working on)\b", message, re.IGNORECASE):
        now_ctx = today_summary(db, prof.owner, tz_offset_min)["now"]
        if now_ctx and now_ctx["title"]:
            query = f"{message} {now_ctx['title']}"
    hits = knowledge.search(db, ws.id, query, k=6)
    if hits:
        parts.append(knowledge.format_context(hits))
    sources = [h.as_source() for h in hits]
    # Phase B: also read the delivery side (projects/perimeters/deliverables) the user can see, so one
    # companion answers both personal and delivery questions.
    deliv_text, deliv_sources = _delivery_context(db, user, message, scope_perimeter_id, scope_project_id)
    if deliv_text:
        parts.append(deliv_text)
        sources += deliv_sources
    return "\n\n".join(parts), sources


def history(db: Session, owner: str, n: int = 12) -> list[CompanionMessage]:
    rows = db.scalars(select(CompanionMessage).where(CompanionMessage.owner == owner).order_by(CompanionMessage.ts.desc()).limit(n)).all()
    return list(reversed(rows))


def fallback_reply(prof: CompanionProfile, sources: list[dict]) -> str:
    if sources:
        return _t(prof, "L'IA est momentanément indisponible. Voici ce que j'ai trouvé dans vos données :\n", "The AI is temporarily unavailable. Here is what I found in your data:\n") + \
            "\n".join(f"- {s['label']} : {s['excerpt'][:200]}" for s in sources[:4])
    return _t(prof, "L'IA est momentanément indisponible et je n'ai rien trouvé de pertinent dans vos données indexées.", "The AI is temporarily unavailable and I found nothing relevant in your indexed data.")


def brief_task(prof: CompanionProfile, kind: str) -> str:
    if kind == "evening":
        return ("Rédige le bilan de journée de la personne : ce qu'elle a fait avancer (d'après <activite>), ce qui semble rester en "
                "suspens, et 3 priorités pour démarrer demain. Court, concret, encourageant sans flatterie.")
    return ("Rédige son brief du matin : agenda du jour (d'après <agenda>), ce qui demande son attention dans ses données récentes, "
            "et 3 priorités suggérées. Si une section n'a aucune donnée, omets-la plutôt que d'inventer. Court et concret.")


# ---------------------------------------------------------------- admin monitoring (aggregates only)

def metrics(db: Session, days: int = 30) -> dict:
    since = _now() - timedelta(days=days)
    profiles = db.scalars(select(CompanionProfile)).all()
    sugg = db.scalars(select(CompanionSuggestion).where(CompanionSuggestion.created_at >= since)).all()
    by_kind: dict[str, dict] = defaultdict(lambda: {"offered": 0, "accepted": 0, "dismissed": 0, "never": 0})
    for s in sugg:
        k = by_kind[s.kind]
        k["offered"] += 1
        if s.status in ("accepted", "dismissed", "never"):
            k[s.status] += 1
    cat_rows = db.execute(select(ActivityEvent.category, func.sum(ActivityEvent.seconds)).where(ActivityEvent.started_at >= since).group_by(ActivityEvent.category)).all()
    active = db.scalar(select(func.count(func.distinct(CompanionMessage.owner))).where(CompanionMessage.ts >= _now() - timedelta(days=7))) or 0
    decided = sum(k["accepted"] + k["dismissed"] + k["never"] for k in by_kind.values())
    return {
        "days": days,
        "users": len(profiles), "onboarded": sum(1 for p in profiles if p.onboarded), "tracking_opt_in": sum(1 for p in profiles if p.tracking_enabled),
        "active_7d": active,
        "messages": db.scalar(select(func.count()).select_from(CompanionMessage).where(CompanionMessage.ts >= since, CompanionMessage.role == "user")) or 0,
        "memories": db.scalar(select(func.count()).select_from(CompanionMemory)) or 0,
        "suggestions": {"offered": len(sugg), "acceptance_pct": round(100 * sum(k["accepted"] for k in by_kind.values()) / decided, 1) if decided else None,
                        "by_kind": [{"kind": k, "label": NUDGE_LABELS.get(k, k), **v} for k, v in sorted(by_kind.items())]},
        "time_by_category": [{"category": c, "hours": round((s or 0) / 3600, 1)} for c, s in sorted(cat_rows, key=lambda r: -(r[1] or 0))],
        "roles": dict(Counter(p.job_role for p in profiles)),
    }


def erase(db: Session, owner: str) -> dict:
    """Right to erasure: everything the companion holds about one person."""
    counts = {}
    for model, name in ((ActivityEvent, "activity"), (CompanionSuggestion, "suggestions"), (CompanionMessage, "messages"), (CompanionMemory, "memories")):
        counts[name] = db.execute(delete(model).where(model.owner == owner)).rowcount or 0
    prof = db.get(CompanionProfile, owner)
    if prof is not None:
        db.delete(prof)
    return counts
