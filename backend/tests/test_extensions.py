"""Copilot, onboarding import, exports, proactive watch, connectors, observability (mock LLM mode)."""

import io

import openpyxl
import pptx

from app import telemetry
from tests.conftest import as_user

ATLAS = "per-atlas-sinistres"


def test_copilot_uses_tools_and_keeps_history(client):
    first = client.post(f"/api/perimeters/{ATLAS}/copilot", json={"message": "Quel est le respect des SLA et pourquoi le batch échoue ?"}).json()
    answer = first["messages"][-1]
    assert {"get_service_kpis", "find_similar_incidents", "search_documents"} <= {t["tool"] for t in answer["trace"]}
    assert answer["sources"]
    second = client.post(f"/api/perimeters/{ATLAS}/copilot", json={"message": "Et la charge de l'équipe ?", "conversation_id": first["id"]}).json()
    assert len(second["messages"]) == 4
    # conversations are private to their author
    assert client.get(f"/api/conversations/{first['id']}", headers=as_user("karim")).status_code == 404


def test_onboarding_new_perimeter_with_imports(client):
    new = client.post("/api/perimeters", json={"client_id": "cli-horizon", "name": "Crédit conso", "description": "test"}, headers=as_user("sarah")).json()
    pid = new["id"]
    assert client.get(f"/api/perimeters/{pid}/readiness").json()["score"] < 20

    apps = "name;technology;criticality\nLoanCore;Java;critical\nScoring;Python;high\n"
    deps = "source;target;kind\nLoanCore;Scoring;api\nLoanCore;Unknown;api\n"
    team = "name;profile;skills;weekly_capacity_h\nAlice Martin;senior;java;35\n"
    tickets = "ref;kind;priority;application;title;status;assignee;opened_at;resolved_at\nL1;incident;P2;LoanCore;Crash;closed;Alice Martin;2026-08-03 09:00;2026-08-03 12:00\nL2;incident;P9;LoanCore;Bad;open;;2026-08-04 09:00;\n"
    for dataset, content, inserted, errors in [("applications", apps, 2, 0), ("dependencies", deps, 1, 1), ("team", team, 1, 0), ("tickets", tickets, 1, 1)]:
        r = client.post(f"/api/perimeters/{pid}/import/{dataset}", files={"file": (f"{dataset}.csv", content.encode(), "text/csv")}, headers=as_user("sarah")).json()
        assert (r["inserted"], r["error_count"]) == (inserted, errors), (dataset, r)
    again = client.post(f"/api/perimeters/{pid}/import/applications", files={"file": ("a.csv", apps.encode(), "text/csv")}, headers=as_user("sarah")).json()
    assert again["updated"] == 2 and again["inserted"] == 0

    readiness = client.get(f"/api/perimeters/{pid}/readiness").json()
    assert {i["id"] for i in readiness["items"] if i["done"]} >= {"applications", "dependencies", "team", "tickets"}
    # Karim (Atlas only) cannot import into a Horizon perimeter
    assert client.post(f"/api/perimeters/{pid}/import/applications", files={"file": ("a.csv", apps.encode(), "text/csv")}, headers=as_user("karim")).status_code == 404


def test_exports_are_valid_office_files(client):
    deck = client.get(f"/api/perimeters/{ATLAS}/exports/copil.pptx")
    assert deck.status_code == 200 and len(pptx.Presentation(io.BytesIO(deck.content)).slides) == 6
    book = client.get(f"/api/perimeters/{ATLAS}/exports/prebilling.xlsx", params={"year": 2026, "month": 8})
    assert openpyxl.load_workbook(io.BytesIO(book.content)).sheetnames == ["Synthèse", "Lignes", "Justificatifs", "Écarts"]
    assert client.get(f"/api/perimeters/{ATLAS}/exports/onboarding.docx").status_code == 200


def test_watch_creates_deduplicated_notifications(client):
    first = client.post("/api/watch/run", headers=as_user("sarah")).json()["created"]
    assert first > 0
    assert client.post("/api/watch/run", headers=as_user("sarah")).json()["created"] == 0
    notes = client.get("/api/notifications", headers=as_user("lea")).json()
    assert all(n["client"] == "Banque Horizon" for n in notes["items"])
    client.post("/api/notifications/read-all", headers=as_user("lea"))
    assert client.get("/api/notifications", params={"unread": True}, headers=as_user("lea")).json()["unread"] == 0


def test_connectors_reject_secrets_and_sync_demo_feed(client):
    assert client.post(f"/api/perimeters/{ATLAS}/connectors", json={"kind": "jira", "name": "J", "config": {"password": "x"}}).status_code == 400
    jira = client.post(f"/api/perimeters/{ATLAS}/connectors", json={"kind": "jira", "name": "J", "config": {"base_url": "https://x.atlassian.net", "email": "a@b.c", "project_key": "T", "token_env": "NOPE_TOKEN"}}).json()
    assert client.post(f"/api/connectors/{jira['id']}/test").json()["ok"] is False
    demo = client.post(f"/api/perimeters/{ATLAS}/connectors", json={"kind": "simulated", "name": "Démo", "config": {"batch_size": 2}}).json()
    result = client.post(f"/api/connectors/{demo['id']}/sync").json()
    assert result["status"] == "ok" and result["inserted"] == 2
    assert client.post(f"/api/connectors/{demo['id']}/sync", headers=as_user("tom")).status_code == 403


def test_teams_transcript_import_generates_minutes(client):
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Claire Martin>On valide la mise en production le 15.</v>\n\n"
        "00:00:05.000 --> 00:00:09.000\n<v Karim Haddad>Je m'en charge, je vous envoie le planning.</v>\n"
    )
    res = client.post(f"/api/perimeters/{ATLAS}/meetings/import", files={"file": ("copil.vtt", vtt.encode(), "text/vtt")}, data={"title": "COPIL septembre"}).json()
    assert res["meeting"]["title"] == "COPIL septembre" and res["run"]["workflow"] == "call"
    meeting = client.get(f"/api/meetings/{res['meeting']['id']}").json()
    assert meeting["transcript"].splitlines() == ["Claire Martin: On valide la mise en production le 15.", "Karim Haddad: Je m'en charge, je vous envoie le planning."]


def test_observability_counts_calls(client):
    telemetry.flush()
    data = client.get("/api/observability").json()
    agents = {a["agent"] for a in data["by_agent"]}
    assert "copilot" in agents and data["totals"]["calls"] > 0
    assert data["mode"] == "offline"
