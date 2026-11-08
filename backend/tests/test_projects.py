"""Delivery projects: a manager runs one or more (per client); périmètres sit under a project, the
project has a team (members = users), the portfolio surfaces the project, and closing frees members.
Access stays client-scoped (a manager on another client gets 404, never leaks existence)."""

from sqlalchemy import select

from app.db import SessionLocal
from app.deliverables import plan as P
from app.models import Client, PerimeterDeliverable
from tests.conftest import as_user

DIRECTOR = as_user("sarah")     # sees every client, can decide
KARIM = as_user("karim")        # delivery_manager, Atlas client only


def _client() -> str:
    """A dedicated client so seeded users' isolation (Atlas-only karim…) is untouched."""
    with SessionLocal() as db:
        c = db.scalars(select(Client).where(Client.name == "Projects Test Client")).first()
        if c is None:
            c = Client(name="Projects Test Client", sector="test")
            db.add(c)
            db.commit()
        return c.id


def test_project_crud_team_and_perimeter(client):
    cid = _client()
    # create a project
    r = client.post("/api/projects", json={"client_id": cid, "name": "Refonte SI", "technologies": ["Java", "Angular"]}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    proj = r.json()
    pid = proj["id"]
    assert proj["manager"] == "sarah" and proj["technologies"] == ["Java", "Angular"]

    # it lists (scoped) and the manager is seeded as first member
    assert any(p["id"] == pid for p in client.get("/api/projects", headers=DIRECTOR).json())
    detail = client.get(f"/api/projects/{pid}", headers=DIRECTOR).json()
    assert [m["owner"] for m in detail["members"]] == ["sarah"]

    # add a team member (with skills surfaced)
    client.put("/api/team/skills", json={"skills": [{"skill": "Angular", "level": 4}]}, headers=KARIM)
    detail = client.post(f"/api/projects/{pid}/members", json={"owner": "karim", "role_in_project": "lead"}, headers=DIRECTOR).json()
    karim = next(m for m in detail["members"] if m["owner"] == "karim")
    assert karim["role_in_project"] == "lead" and any(s["skill"] == "Angular" for s in karim["skills"])

    # create a périmètre under the project
    r = client.post("/api/perimeters", json={"client_id": cid, "name": "Lot 1", "project_id": pid}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    perimeter_id = r.json()["id"]
    assert r.json()["project_id"] == pid

    # the portfolio surfaces the project; the détail carries the project name
    item = next(i for i in client.get("/api/portfolio", headers=DIRECTOR).json() if i["id"] == perimeter_id)
    assert item["project"] == "Refonte SI" and item["project_id"] == pid
    assert client.get(f"/api/perimeters/{perimeter_id}", headers=DIRECTOR).json()["project"] == "Refonte SI"
    assert any(p["id"] == perimeter_id for p in client.get(f"/api/projects/{pid}", headers=DIRECTOR).json()["perimeters"])

    # detach then close the project
    assert client.put(f"/api/perimeters/{perimeter_id}/project", json={"project_id": None}, headers=DIRECTOR).json()["project_id"] is None
    assert client.put(f"/api/projects/{pid}", json={"status": "done"}, headers=DIRECTOR).json()["status"] == "done"


def test_project_access_is_client_scoped(client):
    cid = _client()
    pid = client.post("/api/projects", json={"client_id": cid, "name": "Isolé"}, headers=DIRECTOR).json()["id"]
    # karim (Atlas-only) cannot see a project on another client — 404, not 403 (no existence leak)
    assert client.get(f"/api/projects/{pid}", headers=KARIM).status_code == 404
    assert all(p["id"] != pid for p in client.get("/api/projects", headers=KARIM).json())
    # and cannot create a project on a client he can't see
    assert client.post("/api/projects", json={"client_id": cid, "name": "X"}, headers=KARIM).status_code == 404


def test_perimeter_project_must_match_client(client):
    cid = _client()
    pid = client.post("/api/projects", json={"client_id": cid, "name": "P"}, headers=DIRECTOR).json()["id"]
    # a périmètre on a different client can't be attached to this project
    r = client.post("/api/perimeters", json={"client_id": "cli-atlas", "name": "Bad", "project_id": pid}, headers=DIRECTOR)
    assert r.status_code == 400


def _seed_matrice(perimeter_id: str) -> None:
    with SessionLocal() as db:
        db.add(PerimeterDeliverable(
            perimeter_id=perimeter_id, key="matrice_couverture", title="Matrice", created_by="sarah",
            draft={"exigences": [{"ref": "EXF-01", "name": "Connexion SSO", "impact": "Grave", "probabilite": "Fréquent"}],
                   "cas_usage": [{"ref": "UC-01", "name": "Se connecter", "exigences": ["EXF-01"]}]}))
        db.commit()


def test_project_plan_de_charge_spans_the_project(client):
    cid = _client()
    pid = client.post("/api/projects", json={"client_id": cid, "name": "Plan Proj", "technologies": ["Java"]}, headers=DIRECTOR).json()["id"]
    per = client.post("/api/perimeters", json={"client_id": cid, "name": "Lot P", "project_id": pid}, headers=DIRECTOR).json()["id"]
    _seed_matrice(per)

    # prepare with no answers → clarifying questions (the manager is already a member, so no team question)
    r = client.post(f"/api/projects/{pid}/plan-de-charge/prepare", json={"answers": {}}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "questions" and r.json()["questions"]

    # answer everything → a ready plan draft aggregated from the périmètre's matrice
    answers = {P.Q_BASE: "1", P.Q_START: "2026-01-05", P.Q_HORIZON: "8", P.Q_RATE: "550"}
    ready = client.post(f"/api/projects/{pid}/plan-de-charge/prepare", json={"answers": answers}, headers=DIRECTOR).json()
    assert ready["status"] == "ready", ready
    assert ready["draft"]["totals"]["days"] > 0

    # generate → a real .xlsx, stored as a project deliverable
    g = client.post(f"/api/projects/{pid}/plan-de-charge/generate", json={"draft": ready["draft"]}, headers=DIRECTOR)
    assert g.status_code == 200 and g.headers.get("X-Deliverable-Id")
    assert g.content[:2] == b"PK"   # xlsx is a zip
    dl = client.get(f"/api/projects/{pid}/deliverables", headers=DIRECTOR).json()["deliverables"]
    assert len(dl) == 1 and dl[0]["key"] == "plan_de_charge" and dl[0]["totals"]["days"] > 0
    f = client.get(f"/api/projects/{pid}/deliverables/{dl[0]['id']}/file", headers=DIRECTOR)
    assert f.status_code == 200 and f.content[:2] == b"PK"

    # a project with no matrice anywhere → a clear 400, not a 500
    empty = client.post("/api/projects", json={"client_id": cid, "name": "Empty"}, headers=DIRECTOR).json()["id"]
    assert client.post(f"/api/projects/{empty}/plan-de-charge/prepare", json={"answers": {}}, headers=DIRECTOR).status_code == 400


def test_shared_brain_copilot_is_project_aware(client):
    """The copilot Toolbox now reads the whole project: search unions sibling périmètres and the
    project/plan/staffing tools expose structured data that used to be invisible to chat."""
    from app import knowledge
    from app.agents.copilot import Toolbox
    from app.models import Perimeter
    cid = _client()
    pid = client.post("/api/projects", json={"client_id": cid, "name": "Brain Proj", "technologies": ["Kafka"]}, headers=DIRECTOR).json()["id"]
    a = client.post("/api/perimeters", json={"client_id": cid, "name": "Perim A", "project_id": pid}, headers=DIRECTOR).json()["id"]
    b = client.post("/api/perimeters", json={"client_id": cid, "name": "Perim B", "project_id": pid}, headers=DIRECTOR).json()["id"]

    with SessionLocal() as db:
        knowledge.ingest_document(db, b, "Note B.md", "transfer", "Le batch Zorglubaxion tourne chaque nuit sur le périmètre B.")
        db.commit()
        tb = Toolbox(db, db.get(Perimeter, a), "sarah")

        # project-wide search finds the doc living in the SIBLING périmètre, labelled with its name
        res = tb.t_search_documents("Zorglubaxion")
        assert res["results"] != "no matching documentation"
        assert any(r.get("perimeter") == "Perim B" for r in res["results"]), res

        # get_project exposes both siblings + the seeded manager on the team
        proj = tb.t_get_project()
        assert set(proj["perimeters"]) == {"Perim A", "Perim B"}
        assert proj["technologies"] == ["Kafka"] and any(m["owner"] == "sarah" for m in proj["team"])

        # structured tools that used to be invisible to chat now answer (empty is fine, not an error)
        assert "staffing" in tb.t_get_staffing() or "note" in tb.t_get_staffing()
        assert "note" in tb.t_get_plan_de_charge()   # no plan generated yet → a clear note, not a crash


def test_team_members_can_be_users_or_people_entered_by_hand(client):
    """Not every company gives the app to every employee: a manager can add a member manually."""
    cid = _client()
    pid = client.post("/api/projects", json={"client_id": cid, "name": "Équipe mixte"}, headers=DIRECTOR).json()["id"]

    # 1) an existing platform user
    detail = client.post(f"/api/projects/{pid}/members", json={"owner": "karim", "role_in_project": "lead"}, headers=DIRECTOR).json()
    karim = next(m for m in detail["members"] if m["owner"] == "karim")
    assert karim["name"] == "Karim Haddad" and not karim["is_external"]

    # 2) a person without an account, typed in by the manager (name, profile, capacity, skills)
    manual = {"name": "Nadia Externe", "email": "nadia@partner.example", "profile": "senior", "weekly_capacity_h": 28,
              "skills": ["Kafka", {"skill": "Java", "level": 4}]}
    detail = client.post(f"/api/projects/{pid}/members", json={"manual": manual}, headers=DIRECTOR).json()
    nadia = next(m for m in detail["members"] if m["is_external"])
    assert nadia["name"] == "Nadia Externe" and nadia["owner"].startswith("ext:") and nadia["profile"] == "senior"
    assert {s["skill"] for s in nadia["skills"]} == {"Kafka", "Java"}
    # same email again → the same person, not a duplicate
    detail = client.post(f"/api/projects/{pid}/members", json={"manual": {**manual, "name": "Nadia E."}}, headers=DIRECTOR).json()
    assert sum(1 for m in detail["members"] if m["is_external"]) == 1
    assert client.post(f"/api/projects/{pid}/members", json={"manual": {"name": " "}}, headers=DIRECTOR).status_code == 400

    # she is part of the competence matrix and skill matching like any user
    people = client.post("/api/team/skills/match", json={"technologies": ["Kafka"]}, headers=DIRECTOR).json()["people"]
    assert any(p["owner"] == nadia["owner"] for p in people)

    # 3) staff someone by hand straight onto a périmètre, with their own profile and capacity
    per = client.post("/api/perimeters", json={"client_id": cid, "name": "Lot mixte", "project_id": pid}, headers=DIRECTOR).json()["id"]
    r = client.post(f"/api/perimeters/{per}/staff/manual", json={"name": "Paul Sans Compte", "profile": "junior",
                                                                "weekly_capacity_h": 21, "skills": ["Angular"]}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    paul = next(m for m in r.json()["members"] if m["name"] == "Paul Sans Compte")
    assert paul["profile"] == "junior" and paul["weekly_capacity_h"] == 21 and paul["owner"].startswith("ext:")
    assert any(p["name"] == "Paul Sans Compte" for p in client.get("/api/team/external", headers=DIRECTOR).json()["people"])

    # the companion/copilot see real names, never "ext:…" keys
    from app import context
    from app.models import DeliveryProject
    with SessionLocal() as db:
        team_names = {m["name"] for m in context.project_team(db, db.get(DeliveryProject, pid))}
    assert {"Nadia Externe", "Karim Haddad"} <= team_names

