"""End-to-end API tests in mock LLM mode: isolation, orchestration, human-in-the-loop, audit."""

import time

from tests.conftest import as_user

ATLAS = "per-atlas-sinistres"
HORIZON = "per-horizon-paiements"


def wait_run(client, run_id, statuses=("completed", "awaiting_approval", "failed"), timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} stuck in {run['status']}")


def test_client_isolation(client):
    karim = as_user("karim")  # Atlas only
    assert [p["id"] for p in client.get("/api/portfolio", headers=karim).json()] == [ATLAS]
    assert client.get(f"/api/perimeters/{HORIZON}", headers=karim).status_code == 404
    assert client.post(f"/api/perimeters/{HORIZON}/search", json={"query": "Kafka"}, headers=karim).status_code == 404
    assert {p["id"] for p in client.get("/api/portfolio", headers=as_user("sarah")).json()} >= {ATLAS, HORIZON}


def test_search_is_scoped_and_cited(client):
    hits = client.post(f"/api/perimeters/{ATLAS}/search", json={"query": "reprise batch IND060"}).json()
    assert hits and "DEX" in hits[0]["filename"]
    assert all("Horizon" not in h["filename"] for h in hits)


def test_copil_workflow_waits_for_approval_then_completes(client):
    run = client.post(f"/api/perimeters/{ATLAS}/runs", json={"workflow": "copil"}).json()
    run = wait_run(client, run["id"])
    assert run["status"] == "awaiting_approval"
    assert [s["node"] for s in run["steps"] if s.get("status") == "completed"][:4] == ["incident", "capacity", "billing", "governance"]

    gated = [r for r in run["recommendations"] if r["kind"] in ("prebilling", "copil", "staffing") and r["status"] == "pending"]
    assert {r["kind"] for r in gated} == {"prebilling", "copil", "staffing"}

    # consultants cannot decide; rejections need a reason
    assert client.post(f"/api/recommendations/{gated[0]['id']}/decision", json={"approve": True}, headers=as_user("tom")).status_code == 403
    assert client.post(f"/api/recommendations/{gated[0]['id']}/decision", json={"approve": False}, headers=as_user("karim")).status_code == 400

    for r in gated:
        ok = client.post(f"/api/recommendations/{r['id']}/decision", json={"approve": r["kind"] != "copil", "comment": "à retravailler"}, headers=as_user("karim"))
        assert ok.status_code == 200

    run = wait_run(client, run["id"], statuses=("completed", "failed"))
    assert run["status"] == "completed"
    assert run["output"]["decisions"]["rejected"] == 1

    staffed = [b for b in client.get(f"/api/perimeters/{ATLAS}/backlog").json() if b["assignee_id"]]
    assert len(staffed) > 3  # approved staffing proposal was applied

    actions = {a["action"] for a in client.get("/api/audit", params={"perimeter_id": ATLAS}).json()}
    assert {"run.started", "agent.completed", "recommendation.approved", "recommendation.rejected", "run.completed"} <= actions


def test_ask_is_read_only(client):
    before = len(client.get("/api/recommendations", params={"perimeter_id": ATLAS}).json())
    run = client.post(f"/api/perimeters/{ATLAS}/runs", json={"workflow": "ask", "params": {"question": "Quelle est la charge prévue de l'équipe ?"}}).json()
    run = wait_run(client, run["id"])
    assert run["status"] == "completed"
    assert run["output"]["answer"]["text"]
    assert len(client.get("/api/recommendations", params={"perimeter_id": ATLAS}).json()) == before


def test_live_meeting_flow(client):
    meeting = client.post(f"/api/perimeters/{ATLAS}/meetings", json={"title": "Point hebdo"}).json()
    answer = client.post(f"/api/meetings/{meeting['id']}/segments", json={"speaker": "Client", "text": "Pourquoi le batch indemnisation échoue en fin de mois ?"}).json()
    assert answer["question_detected"] and answer["sources"]
    oos = client.post(f"/api/meetings/{meeting['id']}/segments", json={"speaker": "Client", "text": "Pouvez-vous prendre en charge la migration des archives GED ?"}).json()
    assert oos["out_of_scope"]

    run = client.post(f"/api/meetings/{meeting['id']}/end").json()
    run = wait_run(client, run["id"])
    # High-precision capture: the verbatim transcript is indexed into the KB unconditionally, before
    # anyone approves the minutes — so the copilot/companion can answer exactly what was said.
    docs_now = client.get(f"/api/perimeters/{ATLAS}").json()["documents"]
    assert any(d["doc_type"] == "meeting_transcript" for d in docs_now)
    minutes = next(r for r in run["recommendations"] if r["kind"] == "minutes")
    client.post(f"/api/recommendations/{minutes['id']}/decision", json={"approve": True}, headers=as_user("karim"))
    docs = client.get(f"/api/perimeters/{ATLAS}").json()["documents"]
    assert any(d["doc_type"] == "minutes" for d in docs)  # living documentation


def test_capacity_scenario(client):
    base = client.post(f"/api/perimeters/{ATLAS}/capacity/simulate", json={}).json()
    more = client.post(f"/api/perimeters/{ATLAS}/capacity/simulate", json={"add_fte": 2}).json()
    assert more["forecast"][0]["capacity_h"] == base["forecast"][0]["capacity_h"] + 70
    assert more["capacity_gap_h"] <= base["capacity_gap_h"]


import pytest  # noqa: E402


@pytest.mark.parametrize("workflow, params, agents", [
    ("onboarding", {}, ["knowledge_transfer", "incident", "capacity"]),
    ("governance", {}, ["incident", "governance"]),
    ("capacity", {}, ["capacity"]),
    ("estimation", {"request": "Ajouter un champ SIRET au formulaire de déclaration de sinistre"}, ["estimation"]),
])
def test_workflows_launched_by_the_ui_exist_and_run(client, workflow, params, agents):
    """The Pilotage / Connaissance / Incidents & chiffrage / Capacité buttons launch these; they had
    dropped out of the orchestrator ("Unknown workflow"). Every agent of the chain must actually run."""
    r = client.post(f"/api/perimeters/{ATLAS}/runs", json={"workflow": workflow, "params": params})
    assert r.status_code == 200, r.text
    run = wait_run(client, r.json()["id"])
    assert run["status"] in ("completed", "awaiting_approval"), run.get("error")
    done = [s["node"] for s in run["steps"] if s.get("status") == "completed"]
    assert done[:len(agents)] == agents
