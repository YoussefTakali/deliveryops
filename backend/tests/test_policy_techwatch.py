"""Company rules (every agent + every user input) and the personal tech watch."""

import io
import json
from datetime import datetime, timezone

import httpx
import pytest

from app import llm, policy, techwatch, telemetry
from app.db import SessionLocal
from app.models import AuditLog, PolicyDocument, PolicyRule, TechWatchBriefing, TechWatchProfile
from tests.conftest import as_user

ADMIN = as_user("sarah")   # director: policy.manage
KARIM = as_user("karim")   # delivery manager: no policy.manage
TOM = as_user("tom")       # consultant


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(PolicyRule).delete()
        db.query(PolicyDocument).delete()
        db.query(TechWatchBriefing).delete()
        db.query(TechWatchProfile).delete()
        db.commit()
    policy.invalidate()


def _rule(client, **kw):
    body = {"title": "Pas de données client confidentielles", "text": "", "category": "confidentialite", "action": "block", "keywords": [], **kw}
    r = client.post("/api/policy/rules", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _perimeter(client) -> str:
    return client.get("/api/portfolio", headers=TOM).json()[0]["id"]


# ---------------------------------------------------------------- rules: admin & users

def test_only_policy_admins_manage_rules_but_everyone_sees_them(client):
    assert client.get("/api/policy/rules", headers=KARIM).status_code == 403
    _rule(client, title="Installer uniquement depuis le portail logiciel", action="guide", category="logiciels")
    assert [r["title"] for r in client.get("/api/policy/public", headers=TOM).json()] == ["Installer uniquement depuis le portail logiciel"]
    assert client.post("/api/policy/rules", json={"title": "x" * 5, "action": "explode"}, headers=ADMIN).status_code == 422
    assert client.post("/api/policy/rules", json={"title": "Regex", "keywords": ["/[unclosed/"]}, headers=ADMIN).status_code == 422


def test_a_blocking_rule_refuses_the_message_and_records_who_where_never_what(client):
    _rule(client, keywords=["CONFIDENTIEL CLIENT", "/\\bIBAN\\s*FR\\d{2}/"])
    pid = _perimeter(client)
    r = client.post(f"/api/perimeters/{pid}/copilot", json={"message": "Résume ce document CONFIDENTIEL client"}, headers=TOM)
    assert r.status_code == 403 and "Pas de données client confidentielles" in r.json()["detail"]
    r = client.post("/api/companion/chat", json={"message": "Mon IBAN FR76 3000 6000", "tz": 0}, headers=TOM)
    assert r.status_code == 403                                     # same rule, other agent (regex keyword)
    r = client.post("/api/automations/compile", json={"description": "envoie le fichier CONFIDENTIEL CLIENT à tous"}, headers=TOM)
    assert r.status_code == 403
    with SessionLocal() as db:
        logs = db.query(AuditLog).filter(AuditLog.action == "policy.violation", AuditLog.actor == "tom").all()
        assert {a.details["where"] for a in logs} >= {"copilot", "compagnon", "automatisations"}
        assert all("IBAN FR76" not in json.dumps(a.details) and "Résume" not in json.dumps(a.details) for a in logs)
    v = client.get("/api/policy/violations", headers=ADMIN).json()
    assert v and v[0]["decision"] == "blocked" and v[0]["user"] == "tom"


def test_a_warning_rule_lets_the_request_through_and_every_answer_opens_with_the_warning(client):
    _rule(client, title="Pas d'outils non validés", action="warn", category="logiciels", keywords=["chatgpt"])
    pid = _perimeter(client)
    assert client.post(f"/api/perimeters/{pid}/copilot", json={"message": "je colle ça dans ChatGPT ?"}, headers=TOM).status_code == 200
    telemetry.start_request()
    telemetry.add_policy_notes(["« Pas d'outils non validés » (contient « chatgpt »)"])
    block = policy.instructions("copilot_turn")
    assert "<regles_entreprise>" in block and "<alerte_conformite>" in block and "AVERTIR" in block


def test_rules_reach_every_agent_but_not_the_checker_itself(client):
    _rule(client, title="Toujours anonymiser les noms de clients", action="guide")
    telemetry.start_request()
    for purpose in ("copilot_turn", "GeneratedDoc", "CompiledWorkflow", "Companion"):
        assert "Toujours anonymiser" in policy.instructions(purpose)
    assert policy.instructions("PolicyVerdict") == ""
    msgs = llm._prepared("GeneratedDoc", [{"role": "system", "content": "Tu rédiges."}, {"role": "user", "content": "x"}])
    assert "Toujours anonymiser" in msgs[0]["content"]


def test_the_ai_check_catches_what_keywords_miss(client, monkeypatch):
    _rule(client, title="Ne jamais partager de mots de passe", action="block")
    def fake(**kw):
        if kw["schema"] is policy.PolicyVerdict:
            return policy.PolicyVerdict(violations=[{"rule": 1, "explanation": "le message contient un identifiant de connexion"}]), True
        return kw["fallback"](), False
    monkeypatch.setattr(llm, "structured", fake)
    res = client.post("/api/policy/test", json={"text": "voici le login admin / Azerty123 du client"}, headers=ADMIN).json()
    assert res["decision"] == "blocked" and res["blocked"][0]["by"] == "ai"
    with SessionLocal() as db:                                         # a dry run records nothing
        assert not db.query(AuditLog).filter(AuditLog.action == "policy.violation", AuditLog.actor == "sarah").count()


def test_rules_extracted_from_a_document_wait_for_review(client):
    doc = ("Charte d'utilisation des outils numériques.\n"
           "Il est interdit de transmettre des données personnelles de clients à un service externe.\n"
           "Les logiciels doivent être installés uniquement depuis le portail interne.\n"
           "Bonne journée à tous.")
    r = client.post("/api/policy/documents", files={"file": ("charte.txt", io.BytesIO(doc.encode()), "text/plain")}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["proposed"] == 2, r.text
    rules = client.get("/api/policy/rules", headers=ADMIN).json()["rules"]
    assert all(x["proposed"] and not x["active"] and x["document"] == "charte.txt" for x in rules)
    assert next(x for x in rules if "interdit" in x["title"])["action"] == "block"
    assert policy.active_rules() == []                                   # nothing applies before review
    docs = client.get("/api/policy/documents", headers=ADMIN).json()
    assert client.post(f"/api/policy/documents/{docs[0]['id']}/accept-all", headers=ADMIN).json()["accepted"] == 2
    assert len(policy.active_rules()) == 2
    client.delete(f"/api/policy/documents/{docs[0]['id']}", headers=ADMIN)
    assert len(policy.active_rules()) == 2                                # accepted rules stay in force
    bad = client.post("/api/policy/documents", files={"file": ("vide.txt", io.BytesIO(b"ok"), "text/plain")}, headers=ADMIN)
    assert bad.status_code == 400


# ---------------------------------------------------------------- tech watch

NEWS = [
    {"title": "Critical CVE in Kubernetes API server", "url": "https://example.com/a", "source": "BleepingComputer",
     "topic": "Kubernetes", "published": "2026-09-20T10:00:00+00:00"},
    {"title": "Java 25 released with new GC", "url": "https://example.com/b", "source": "InfoQ",
     "topic": "Java", "published": "2026-09-18T10:00:00+00:00"},
]


def test_topics_come_from_the_person_and_can_be_added_or_muted(client):
    state = client.put("/api/techwatch/topics", json={"extra_topics": ["Kubernetes", " Kubernetes "], "muted_topics": []}, headers=TOM).json()
    k8s = next(t for t in state["topics"] if t["topic"] == "Kubernetes")
    assert k8s["manual"] and "Suivi manuellement" in k8s["reasons"]
    state = client.put("/api/techwatch/topics", json={"extra_topics": ["Kubernetes"], "muted_topics": ["kubernetes"]}, headers=TOM).json()
    assert next(t for t in state["topics"] if t["topic"] == "Kubernetes")["muted"] is True


def test_refresh_builds_a_briefing_with_the_impact_on_the_persons_work(client, monkeypatch):
    client.put("/api/techwatch/topics", json={"extra_topics": ["Kubernetes", "Java"]}, headers=TOM)
    monkeypatch.setattr(techwatch, "fetch_news", lambda topics: [n for n in NEWS if n["topic"] in topics])
    state = client.post("/api/techwatch/refresh", headers=TOM).json()            # AI offline in tests → rules-based analysis
    b = state["briefing"]
    assert b and not b["llm_used"] and b["stale"] is False and len(b["items"]) == 2
    cve = next(i for i in b["items"] if "CVE" in i["title"])
    assert cve["category"] == "securite" and "Kubernetes" in cve["impact"] and cve["url"].startswith("https://")
    assert client.get("/api/techwatch", headers=TOM).json()["briefing"]["id"] == b["id"]
    assert client.get("/api/techwatch", headers=KARIM).json()["briefing"] is None      # per person


def test_the_ai_explains_impact_and_drops_irrelevant_news(client, monkeypatch):
    client.put("/api/techwatch/topics", json={"extra_topics": ["Kubernetes", "Java"]}, headers=TOM)
    monkeypatch.setattr(techwatch, "fetch_news", lambda topics: NEWS)
    seen = {}

    def fake(**kw):
        seen["agent"] = telemetry.current().get("agent")
        return techwatch.TechWatchAnalysis(summary="Patch K8s cette semaine.", items=[
            {"index": 1, "relevance": 92, "category": "securite", "impact": "Le cluster du projet Atlas est exposé.",
             "affected": ["Projet Atlas"], "action": "Appliquer le correctif 1.31.4"},
            {"index": 2, "relevance": 20, "category": "nouvelle_version", "impact": "Peu d'effet.", "affected": [], "action": ""},
        ]), True
    monkeypatch.setattr(llm, "structured", fake)
    b = client.post("/api/techwatch/refresh", headers=TOM).json()["briefing"]
    assert b["llm_used"] and seen["agent"] == "tech_watch" and b["summary"] == "Patch K8s cette semaine."
    assert [i["title"] for i in b["items"]] == [NEWS[0]["title"]] and b["items"][0]["action"].startswith("Appliquer")


def test_no_network_or_no_topics_give_a_clear_error(client, monkeypatch):
    assert client.post("/api/techwatch/refresh", headers=TOM).status_code in (200, 400)   # seed may or may not give topics
    client.put("/api/techwatch/topics", json={"extra_topics": ["Kubernetes"]}, headers=TOM)

    def down(topics):
        raise techwatch.NewsUnavailable("Impossible de joindre le service d'actualités (réseau ou pare-feu).")
    monkeypatch.setattr(techwatch, "fetch_news", down)
    r = client.post("/api/techwatch/refresh", headers=TOM)
    assert r.status_code == 502 and "actualités" in r.json()["detail"]


def test_google_news_feed_parsing(monkeypatch):
    from datetime import timedelta
    from email.utils import format_datetime
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)
    stale = recent - timedelta(days=400)
    rss = f"""<?xml version="1.0"?><rss><channel>
      <item><title>Spring Boot 4 est disponible - Le Monde Informatique</title><link>https://news.google.com/rss/articles/abc</link>
        <pubDate>{format_datetime(recent)}</pubDate><source url="https://lemondeinformatique.fr">Le Monde Informatique</source></item>
      <item><title>Spring to Java EE Migration</title><link>https://news.google.com/rss/articles/old</link><pubDate>{format_datetime(stale)}</pubDate></item>
      <item><title>Unsafe link</title><link>javascript:alert(1)</link><pubDate>x</pubDate></item>
    </channel></rss>"""
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return httpx.Response(200, content=rss.encode(), request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", fake_get)
    items = techwatch._feed("Spring Boot", ("fr", "FR", "FR:fr"))
    assert items == [{"title": "Spring Boot 4 est disponible", "url": "https://news.google.com/rss/articles/abc",
                      "source": "Le Monde Informatique", "topic": "Spring Boot", "published": recent.isoformat()}]
    assert "news.google.com" in seen["url"] and "vulnerability" in seen["url"]      # tech words keep stock/HR news out


def test_versions_widen_the_search_and_titles_are_classified():
    assert techwatch.base_name("Angular 12") == "Angular" and techwatch.base_name("Oracle 12c") == "Oracle"
    assert techwatch.base_name("Spring Boot") == "Spring Boot" and techwatch.base_name("Java EE 7") == "Java EE"
    assert techwatch._guess_category("Spring Boot 3.5 Reaches End of Open Source Life") == "fin_de_support"
    assert techwatch._guess_category("Offre d'emploi chef de projet") == "autre"            # "emploi" is not "loi"
    assert techwatch.is_tech_news("Google Releases Angular v22") and not techwatch.is_tech_news("Oracle's $18bn data centre debt")
