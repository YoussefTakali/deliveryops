"""Personal tech watch ("Veille"): recent news about what a person actually works with — their skills,
their projects' technologies, the applications of their périmètres — and what each item means for
their projects and work.

1. topics_for(): topics with the reasons they matter ("Projet Atlas", "Compétence niveau 4"…),
   plus topics the person follows by hand, minus the ones they muted.
2. fetch_news(): Google News RSS for the top topics (recent weeks, French and English editions —
   free, no key; only the topic words leave the platform, never client data).
3. analyse(): the model rates each item's relevance and explains the impact on the person's own
   projects, with a suggested action. Without AI: keyword matching and plain category rules.
"""

import email.utils
import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm, telemetry
from app.models import (Application, DeliveryProject, Perimeter, ProjectMember, TeamMember, TeamMemberUser,
                        TechWatchProfile, UserSkill)

log = logging.getLogger(__name__)

MAX_TOPICS = 6
PER_FEED = 4
# Google News doesn't always honour `when:` on long OR queries: filter by date here too. Wider than the
# query window so an actively exploited vulnerability from last month still shows.
MAX_AGE_DAYS = 45
CATEGORIES = {"securite": "Sécurité", "fin_de_support": "Fin de support", "nouvelle_version": "Nouvelle version",
              "tendance": "Tendance du marché", "reglementation": "Réglementation", "autre": "Actualité"}
_EDITIONS = (("fr", "FR", "FR:fr"), ("en-US", "US", "US:en"))
_SPLIT = re.compile(r"\s*(?:,|/|\+|;|\bet\b|&)\s*")


class NewsUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- 1. what this person works with

def profile(db: Session, owner: str) -> TechWatchProfile:
    p = db.get(TechWatchProfile, owner)
    if p is None:
        p = TechWatchProfile(owner=owner, extra_topics=[], muted_topics=[])
        db.add(p)
        db.flush()
    return p


def topics_for(db: Session, user) -> list[dict]:
    """[{topic, weight, reasons, muted, manual}] — every candidate, strongest first."""
    from app.api import visible_perimeters

    found: dict[str, dict] = {}

    def add(name: str, weight: float, reason: str, manual: bool = False):
        name = " ".join(name.split()).strip(" .")
        if len(name) < 2 or len(name) > 60:
            return
        t = found.setdefault(name.lower(), {"topic": name, "weight": 0.0, "reasons": [], "manual": False})
        t["weight"] += weight
        t["manual"] = t["manual"] or manual
        if reason not in t["reasons"]:
            t["reasons"].append(reason)

    # Technologies only: methods and soft skills ("gestion de projet") bring job offers and market studies.
    for s in db.scalars(select(UserSkill).where(UserSkill.owner == user.username, UserSkill.category == "technology")):
        add(s.skill, 1 + s.level, f"Compétence (niveau {s.level})")

    project_ids = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.owner == user.username)))
    project_ids |= set(db.scalars(select(DeliveryProject.id).where(DeliveryProject.manager == user.username)))
    projects = db.scalars(select(DeliveryProject).where(DeliveryProject.id.in_(project_ids),
                                                        DeliveryProject.status == "active")).all() if project_ids else []
    for p in projects:
        for tech in p.technologies or []:
            add(tech, 4, f"Projet {p.name}")

    visible = {p.id: p for p in visible_perimeters(db, user)}
    mine = {p.id for p in visible.values() if p.project_id in project_ids}
    mine |= set(db.scalars(select(TeamMember.perimeter_id).join(TeamMemberUser, TeamMemberUser.member_id == TeamMember.id)
                           .where(TeamMemberUser.owner == user.username)))
    perimeter_ids = [pid for pid in mine if pid in visible] or list(visible)[:6]
    for app in db.scalars(select(Application).where(Application.perimeter_id.in_(perimeter_ids))) if perimeter_ids else []:
        for tech in _SPLIT.split(app.technology or ""):
            add(tech, 3 if app.criticality in ("high", "critical") else 2, f"{visible[app.perimeter_id].name} · {app.name}")

    prof = db.get(TechWatchProfile, user.username)
    for extra in (prof.extra_topics if prof else None) or []:
        add(extra, 6, "Suivi manuellement", manual=True)
    muted = {m.lower() for m in ((prof.muted_topics if prof else None) or [])}
    out = sorted(found.values(), key=lambda t: -t["weight"])
    for t in out:
        t["muted"] = t["topic"].lower() in muted
    return out


# ---------------------------------------------------------------- 2. the news

_VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*[a-z]?$", re.IGNORECASE)


def base_name(topic: str) -> str:
    """"Angular 12" → "Angular": the news that matters for an old version is about the product
    (a new major release, the end of support), which rarely names the version the person runs."""
    base = _VERSION.sub("", topic).strip()
    return base if len(base) >= 3 else topic


# A product name alone also matches its stock price, its CEO or a cartoonist's "angular style": the
# query (and, without AI, the title) must carry a word that makes it technology news.
TECH_TERMS = ("release", "version", "end of life", "fin de support", "support", "vulnerability", "vulnérabilité", "cve",
              "patch", "update", "mise à jour", "migration", "security", "sécurité", "deprecated", "upgrade", "framework", "java")
_TECH_QUERY = "(" + " OR ".join(f'"{t}"' if " " in t else t for t in TECH_TERMS) + ")"


def is_tech_news(title: str) -> bool:
    t = title.lower()
    return any(re.search(rf"(?<!\w){re.escape(term)}", t) for term in TECH_TERMS) or _guess_category(title) != "autre"


def _feed(topic: str, edition: tuple[str, str, str]) -> list[dict]:
    hl, gl, ceid = edition
    base = base_name(topic)
    terms = f'"{topic}"' if base == topic else f'("{topic}" OR "{base}")'
    query = quote_plus(f"{terms} {_TECH_QUERY} when:30d")
    url = f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
    resp = httpx.get(url, timeout=8, headers={"User-Agent": "DeliveryOps tech watch"}, follow_redirects=True)
    resp.raise_for_status()
    items = []
    oldest = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    for item in ET.fromstring(resp.content).iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = None
        link = (item.findtext("link") or "").strip()
        if published and published < oldest:
            continue
        if title and link.startswith("https://"):
            items.append({"title": title[:300], "url": link, "source": source[:120], "topic": topic,
                          "published": published.isoformat() if published else None})
        if len(items) >= PER_FEED:
            break
    return items


def fetch_news(topics: list[str]) -> list[dict]:
    jobs = [(t, e) for t in topics[:MAX_TOPICS] for e in _EDITIONS]
    news: list[dict] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(lambda job: _safe_feed(*job), jobs):
            if result is None:
                failures += 1
            else:
                news += result
    if jobs and failures == len(jobs):
        raise NewsUnavailable("Impossible de joindre le service d'actualités (réseau ou pare-feu).")
    unique, seen = [], set()
    for n in sorted(news, key=lambda n: n["published"] or "", reverse=True):
        key = re.sub(r"\W+", " ", n["title"].lower()).strip()
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique[:40]


def _safe_feed(topic: str, edition: tuple[str, str, str]) -> list[dict] | None:
    try:
        return _feed(topic, edition)
    except (httpx.HTTPError, ET.ParseError) as exc:
        log.warning("tech watch feed %s/%s failed: %s", topic, edition[0], exc)
        return None


# ---------------------------------------------------------------- 3. what it means for this person

class _Assessment(BaseModel):
    index: int = Field(description="numéro de l'actualité")
    relevance: int = Field(ge=0, le=100, description="pertinence pour CETTE personne et SES projets")
    category: str = Field(description="securite | fin_de_support | nouvelle_version | tendance | reglementation | autre")
    impact: str = Field(description="1 à 2 phrases : ce que cela change concrètement pour ses projets ou son travail")
    affected: list[str] = Field(default_factory=list, description="projets, périmètres ou compétences concernés (noms du profil)")
    action: str = Field("", description="action conseillée, courte (vide si simple information)")


class TechWatchAnalysis(BaseModel):
    summary: str = Field(description="3 phrases max : ce qu'il faut retenir cette semaine")
    items: list[_Assessment] = Field(default_factory=list)


def _guess_category(title: str) -> str:
    t = title.lower()
    if re.search(r"\bcve\b|cve-\d|vuln|faille|security|sécurité|patch|exploit|ransomware|attaque|zero-day|0-day", t):
        return "securite"
    if re.search(r"end of (?:\w+ ){0,3}life|fin de (?:vie|support)|\beol\b|support ending|ending support|end of support"
                 r"|deprecat|obsolète|retire", t):
        return "fin_de_support"
    if re.search(r"release|version|launch|sortie|disponible|update|mise à jour", t):
        return "nouvelle_version"
    if re.search(r"\b(rgpd|gdpr|nis ?2|dora|ai act|réglementation|règlement européen|regulation|directive)\b", t):
        return "reglementation"
    return "autre"


def _fallback(topics: list[dict], news: list[dict]) -> TechWatchAnalysis:
    """Without the model: keep only articles whose title names the topic (the feed also matches bodies)."""
    by_topic = {t["topic"]: t for t in topics}
    items = []
    for i, n in enumerate(news):
        t = by_topic.get(n["topic"], {"reasons": [], "weight": 1})
        if base_name(n["topic"]).lower() not in n["title"].lower() or not is_tech_news(n["title"]):
            continue
        cat = _guess_category(n["title"])
        items.append(_Assessment(index=i + 1, relevance=min(90, 40 + int(t["weight"]) * 5 + (15 if cat == "securite" else 0)),
                                 category=cat, affected=t["reasons"][:3],
                                 impact=f"Actualité sur {n['topic']}, utilisé dans votre travail (voir ci-dessous) : "
                                        "vérifiez si elle touche vos versions et vos applications."))
    return TechWatchAnalysis(summary=f"{len(items)} actualité(s) récente(s) sur vos sujets : "
                                     + ", ".join(t["topic"] for t in topics[:MAX_TOPICS]) + ".", items=items)


def analyse(topics: list[dict], news: list[dict]) -> tuple[list[dict], str, bool]:
    if not news:
        return [], "Aucune actualité récente sur vos sujets ces dernières semaines.", False
    profile_text = "\n".join(f"- {t['topic']} : {', '.join(t['reasons'][:4])}" for t in topics[:12])
    news_text = "\n".join(f"{i + 1}. [{n['topic']}] {n['title']} — {n['source']} ({(n['published'] or '')[:10]})"
                          for i, n in enumerate(news))
    with telemetry.attribute(agent="tech_watch"):
        result, used = llm.structured(
            schema=TechWatchAnalysis, tier="agent", thinking=False, max_tokens=3500,
            system=("Tu es l'analyste de veille technologique d'une équipe de delivery IT (maintenance applicative). Pour une "
                    "personne donnée, tu évalues des titres d'actualité récents au regard de SES projets, périmètres et "
                    "compétences. Sois concret : quel projet ou application est touché, quel risque (sécurité, fin de support, "
                    "compatibilité, coût), quelle opportunité, quoi faire. Ne déduis rien au-delà du titre ; si l'impact est "
                    "incertain, dis-le. Une actualité sans lien réel avec son travail a une pertinence faible."),
            context=f"<profil>\n{profile_text}\n</profil>\n\n<actualites>\n{news_text}\n</actualites>",
            task="Évalue chaque actualité (index = son numéro) et rédige la synthèse.",
            fallback=lambda: _fallback(topics, news))
    items = []
    for a in result.items:
        if not 1 <= a.index <= len(news) or a.relevance < 35:
            continue
        n = news[a.index - 1]
        items.append({**n, "relevance": a.relevance, "category": a.category if a.category in CATEGORIES else "autre",
                      "impact": a.impact.strip(), "affected": [x for x in a.affected if x][:5], "action": a.action.strip()})
    items.sort(key=lambda x: (-x["relevance"], x["published"] or ""))
    return items[:15], result.summary.strip(), used


def since(ts: datetime) -> timedelta:
    return datetime.now(timezone.utc) - (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))
