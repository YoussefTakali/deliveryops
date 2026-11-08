"""Work companion: consent + policy gates, privacy filtering, proactive suggestions and the feedback
loop that adapts them, memories, erasure, and admin monitoring being aggregate-only."""

from datetime import datetime, timedelta, timezone

from tests.conftest import as_user

CONSULTANT = as_user("tom")      # consultant: companion.use + companion.activity
DIRECTOR = as_user("sarah")      # director: also companion.admin


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _onboard(client, headers, **extra):
    r = client.put("/api/companion/profile", json={"onboarded": True, "tracking_enabled": True, **extra}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_tracking_is_opt_in_and_sensitive_windows_are_never_stored(client):
    # not opted in yet -> nothing is stored, and the client is told to stop sampling
    r = client.post("/api/companion/activity", json={"events": [{"started_at": _iso(5), "seconds": 120, "app": "WINWORD", "title": "Offre.docx - Word"}]}, headers=CONSULTANT)
    assert r.json() == {"stored": 0, "tracking_active": False}

    _onboard(client, CONSULTANT)
    events = [
        {"started_at": _iso(30), "seconds": 600, "app": "WINWORD", "title": "Proposition ACME.docx - Word"},
        {"started_at": _iso(19), "seconds": 300, "app": "chrome", "title": "Ma banque en ligne - Google Chrome"},   # sensitive keyword
        {"started_at": _iso(13), "seconds": 200, "app": "KeePass", "title": "Database.kdbx"},                       # sensitive app
        {"started_at": _iso(9), "seconds": 400, "app": "EXCEL", "title": "Budget 2026.xlsx - Excel"},
    ]
    assert client.post("/api/companion/activity", json={"events": events}, headers=CONSULTANT).json()["stored"] == 2

    exported = client.get("/api/companion/me/export", headers=CONSULTANT).json()
    titles = [a["title"] for a in exported["activity"]]
    assert titles == ["Proposition ACME.docx", "Budget 2026.xlsx"]  # app suffix stripped, sensitive rows absent
    assert {a["category"] for a in exported["activity"]} == {"documents", "spreadsheets"}


def test_suggestions_adapt_to_feedback(client):
    _onboard(client, CONSULTANT)
    # a meeting that ended ~5 minutes ago -> the companion offers the follow-up
    client.post("/api/companion/activity", json={"events": [{"started_at": _iso(40), "seconds": 35 * 60, "app": "ms-teams", "title": "Comité de pilotage ACME | Microsoft Teams"}]}, headers=CONSULTANT)
    state = client.get("/api/companion/state", headers=CONSULTANT).json()
    followups = [s for s in state["suggestions"] if s["kind"] == "meeting_followup"]
    assert followups and followups[0]["action"]["type"] == "chat"

    # "never" switches that kind off for this person and records why
    r = client.post(f"/api/companion/suggestions/{followups[0]['id']}/decision", json={"status": "never"}, headers=CONSULTANT)
    assert r.json()["weight"] == 0.0
    weights = {n["kind"]: n["weight"] for n in client.get("/api/companion/state", headers=CONSULTANT).json()["profile"]["nudges"]}
    assert weights["meeting_followup"] == 0.0
    assert any("Suite de réunion" in m["content"] for m in client.get("/api/companion/memories", headers=CONSULTANT).json())

    # another meeting ends: no follow-up offered any more
    client.post("/api/companion/activity", json={"events": [{"started_at": _iso(20), "seconds": 15 * 60, "app": "ms-teams", "title": "Point RH | Microsoft Teams"}]}, headers=CONSULTANT)
    again = client.get("/api/companion/state", headers=CONSULTANT).json()
    assert not [s for s in again["suggestions"] if s["kind"] == "meeting_followup" and s["status"] == "new"]


def test_memories_are_deduplicated_and_owner_scoped(client):
    a = client.post("/api/companion/memories", json={"content": "Préfère des réponses en listes à puces courtes", "kind": "preference"}, headers=CONSULTANT).json()
    b = client.post("/api/companion/memories", json={"content": "préfère des réponses en listes à puces courtes.", "kind": "preference"}, headers=CONSULTANT).json()
    assert a["id"] == b["id"]
    # another user can neither see nor delete it
    assert all(m["id"] != a["id"] for m in client.get("/api/companion/memories", headers=DIRECTOR).json())
    assert client.delete(f"/api/companion/memories/{a['id']}", headers=DIRECTOR).status_code == 404
    assert client.delete(f"/api/companion/memories/{a['id']}", headers=CONSULTANT).json() == {"deleted": True}


def test_chat_streams_and_remembers_explicit_instructions(client):
    _onboard(client, CONSULTANT)
    r = client.post("/api/companion/chat", json={"message": "Souviens-toi que mon client principal est ACME Assurances"}, headers=CONSULTANT)
    assert r.status_code == 200 and '"type": "done"' in r.text  # offline (mock) mode still answers, via the fallback
    assert any("ACME Assurances" in m["content"] for m in client.get("/api/companion/memories", headers=CONSULTANT).json())
    roles = [m["role"] for m in client.get("/api/companion/messages", headers=CONSULTANT).json()]
    assert roles[-2:] == ["user", "assistant"]


def test_policy_governs_tracking_and_admin_sees_aggregates_only(client):
    assert client.get("/api/companion/admin/policy", headers=CONSULTANT).status_code == 403
    _onboard(client, CONSULTANT)
    client.post("/api/companion/activity", json={"events": [{"started_at": _iso(3), "seconds": 120, "app": "EXCEL", "title": "Salaires confidentiel.xlsx - Excel"}]}, headers=CONSULTANT)

    metrics = client.get("/api/companion/admin/metrics", headers=DIRECTOR).json()
    users = client.get("/api/companion/admin/users", headers=DIRECTOR).json()
    assert metrics["tracking_opt_in"] >= 1 and any(u["owner"] == "tom" for u in users)
    assert "Salaires" not in str(metrics) + str(users)  # never any window title on the admin side

    # org switches tracking off: collected activity is purged and further ingestion refused
    r = client.put("/api/companion/admin/policy", json={"activity_tracking_allowed": False}, headers=DIRECTOR)
    assert r.status_code == 200 and r.json()["activity_tracking_allowed"] is False
    assert client.get("/api/companion/me/export", headers=CONSULTANT).json()["activity"] == []
    assert client.post("/api/companion/activity", json={"events": [{"started_at": _iso(1), "seconds": 60, "app": "EXCEL", "title": "x"}]}, headers=CONSULTANT).json()["tracking_active"] is False
    client.put("/api/companion/admin/policy", json={"activity_tracking_allowed": True}, headers=DIRECTOR)


def test_stuck_detection_offers_help_and_ignores_calm_work(client):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app import companion
    now = datetime.now(timezone.utc)

    def s(mins_ago, app, title):
        return SimpleNamespace(app=app, title=title, seconds=30, started_at=now - timedelta(minutes=mins_ago))

    stuck = [
        s(14, "chrome", "Télécharger Node.js — nodejs.org"), s(11, "explorer", "node-v20-x64.msi"),
        s(8, "cmd", "node -v is not recognized as an internal command"), s(5, "chrome", "node not recognized windows PATH"),
        s(2, "cmd", "npm install error"),
    ]
    r = companion._detect_stuck(stuck, now)
    assert r and "node" in r["topic"].lower() and r["trail"]
    # calm, varied work must not be flagged as stuck
    calm = [s(14, "winword", "Rapport.docx"), s(9, "excel", "Budget.xlsx"), s(3, "outlook", "Boîte de réception")]
    assert companion._detect_stuck(calm, now) is None

    # generic subject: searching how to download/install ANYTHING (not a hard-coded tech) is detected,
    # and the subject is named — this is the "download kafka" case that used to be missed.
    for scenario, needle in [
        ([s(9, "chrome", "download kafka - Recherche Google"),
          s(6, "chrome", "kafka installation - Recherche Google"),
          s(2, "chrome", "comment telecharger kafka - Recherche Google")], "kafka"),
        # only two tries is enough when the intent is clearly install/download of one subject
        ([s(5, "chrome", "how to install obsidian - Google Search"),
          s(1, "chrome", "download obsidian windows - Google Search")], "obsidian"),
    ]:
        got = companion._detect_stuck(scenario, now)
        assert got and needle in got["topic"].lower(), (needle, got)
    # plain browsing that is not about installing must stay quiet
    browsing = [s(8, "chrome", "météo demain - Recherche Google"), s(3, "chrome", "actualités - Recherche Google")]
    assert companion._detect_stuck(browsing, now) is None


def test_companion_task_asks_questions_then_plans_with_confirm_gates(client, monkeypatch):
    """The companion DOES tasks: ask what changes the plan first, then return a concrete plan where
    every side-effecting step is preceded by a confirmation gate and the exact PowerShell is surfaced
    for approval (nothing pre-approved)."""
    import hashlib as _hashlib

    from app.automations import compiler

    # 1) under-specified → clarifying questions, no plan yet, nothing created
    monkeypatch.setattr(compiler, "compile_workflow",
                        lambda **_: {"name": "", "steps": [], "inputs": {}, "trigger": {"type": "manual"},
                                     "questions": ["Quelle version de Python ?", "Depuis winget ou python.org ?"],
                                     "warnings": [], "explanation": ""})
    r = client.post("/api/companion/task", json={"message": "installe python"}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    assert r.json()["status"] == "questions" and len(qs) == 2
    assert all(isinstance(q, dict) and "question" in q and isinstance(q.get("options"), list) for q in qs)  # clickable options + free text

    # 2) answered → a concrete plan; the install step is gated by a confirm, and the exact script is exposed
    script = "winget install --id Python.Python.3.12 -e"
    monkeypatch.setattr(compiler, "compile_workflow",
                        lambda **_: {"name": "Installer Python 3.12", "description": "via winget",
                                     "trigger": {"type": "manual"}, "inputs": {}, "questions": [], "warnings": [],
                                     "explanation": "Installe puis vérifie.",
                                     "steps": [{"id": "s1", "type": "local.powershell", "title": "Installer Python 3.12",
                                                "params": {"script": script}},
                                               {"id": "s2", "type": "local.powershell", "title": "Vérifier",
                                                "params": {"script": "python --version"}}]})
    r = client.post("/api/companion/task", json={"message": "installe python", "answers": {"Quelle version de Python ?": "3.12"}}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["status"] == "plan" and plan["automation_id"]
    types = [s["type"] for s in plan["steps"]]
    assert types[0] == "flow.confirm" and types[1] == "local.powershell"   # a gate precedes the real action
    assert plan["steps"][0]["confirm"] is True and plan["steps"][1]["side_effect"] is True
    assert any(sc["script"] == script and sc["sha256"] == _hashlib.sha256(script.encode()).hexdigest() for sc in plan["scripts"])

    # the draft automation exists and NOTHING is pre-approved — the user must approve the exact scripts
    got = client.get(f"/api/automations/{plan['automation_id']}", headers=DIRECTOR).json()
    assert got["approved_script_hashes"] == [] and got["source"]["kind"] == "companion_task"
    assert [s["type"] for s in got["steps"]].count("flow.confirm") == 2  # one gate per side-effecting step


def test_user_can_erase_everything(client):
    _onboard(client, CONSULTANT)
    client.post("/api/companion/memories", json={"content": "Travaille surtout le matin", "kind": "routine"}, headers=CONSULTANT)
    erased = client.delete("/api/companion/me/data", headers=CONSULTANT).json()["erased"]
    assert erased["memories"] >= 1
    fresh = client.get("/api/companion/state", headers=CONSULTANT).json()
    assert fresh["profile"]["onboarded"] is False and fresh["memory_count"] == 0


def test_companion_reads_delivery_data_when_scoped_and_respects_access(client):
    """Phase B: with the ambient scope (perimeter the user is viewing), the companion reads the
    delivery corpus — but only what the user is allowed to see (no cross-client leak)."""
    from sqlalchemy import select

    from app import knowledge
    from app.db import SessionLocal
    from app.models import Client, DeliveryProject, Perimeter

    with SessionLocal() as db:
        c = db.scalars(select(Client).where(Client.name == "Companion Bridge Client")).first()
        if c is None:
            c = Client(name="Companion Bridge Client", sector="test")
            db.add(c)
            db.flush()
        proj = DeliveryProject(client_id=c.id, name="Bridge Project", manager="sarah", technologies=["Kafka"])
        db.add(proj)
        db.flush()
        per = Perimeter(client_id=c.id, project_id=proj.id, name="Bridge Perim", contract={})
        db.add(per)
        db.flush()
        knowledge.ingest_document(db, per.id, "Note livraison.md", "livrable", "Le jalon Griffondor est prévu la semaine prochaine.")
        db.commit()
        per_id = per.id

    # sarah (director, sees all) with the scope → the delivery doc is pulled in as a source
    _onboard(client, DIRECTOR)
    r = client.post("/api/companion/chat", json={"message": "Parle-moi du jalon Griffondor", "perimeter_id": per_id}, headers=DIRECTOR)
    assert r.status_code == 200 and "Note livraison.md" in r.text

    # tom (consultant, Atlas-only) cannot see this client → its doc must never leak into his companion
    _onboard(client, CONSULTANT)
    r2 = client.post("/api/companion/chat", json={"message": "Parle-moi du jalon Griffondor", "perimeter_id": per_id}, headers=CONSULTANT)
    assert r2.status_code == 200 and "Note livraison.md" not in r2.text
