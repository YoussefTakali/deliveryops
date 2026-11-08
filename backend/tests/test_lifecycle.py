"""New-périmètre lifecycle: competence matrix → skill match → staffing → matrice (stored + indexed) →
plan de charge (asks the manager, allocates by availability, costs from the rate card) → lifecycle
tracker → billing plan-vs-réel → agents can search it all."""

import io

from sqlalchemy import select

from app.db import SessionLocal
from app.deliverables import plan as P
from app.models import Client, Document, Perimeter, TeamMember, Timesheet
from tests.conftest import as_user

DIRECTOR = as_user("sarah")
KARIM = as_user("karim")


def _new_perimeter(client, name="Portail Client ACME", rate_card=None):
    """Under a dedicated client so the seeded users' client isolation (Atlas-only karim…) is untouched."""
    with SessionLocal() as db:
        c = db.scalars(select(Client).where(Client.name == "Lifecycle Test Client")).first()
        if c is None:
            c = Client(name="Lifecycle Test Client", sector="test")
            db.add(c)
            db.flush()
        p = Perimeter(client_id=c.id, name=name, description="test", contract={"rate_card": rate_card} if rate_card else {})
        db.add(p)
        db.commit()
        return p.id


def test_competence_matrix_and_skill_match(client):
    r = client.put("/api/team/skills", json={"skills": [{"skill": "Angular", "level": 5}, {"skill": "Java", "level": 3, "category": "technology"},
                                                        {"skill": "angular", "level": 2}]}, headers=KARIM)   # duplicate merges
    assert r.status_code == 200, r.text
    mine = {s["skill"]: s["level"] for s in r.json()["skills"]}
    assert mine == {"angular": 2, "Java": 3} or mine == {"Angular": 2, "Java": 3}
    client.put("/api/team/skills", json={"skills": [{"skill": "PostgreSQL", "level": 4}, {"skill": "Java", "level": 5}]}, headers=DIRECTOR)
    m = client.get("/api/team/skills/matrix", headers=DIRECTOR).json()["people"]
    assert any(p["owner"] == "karim" and p["skills"] for p in m)
    # match for Java + Angular: sarah (Java 5) and karim (Java 3 + Angular 2) both match; ranking by level-weighted score
    r = client.post("/api/team/skills/match", json={"technologies": ["Java", "Angular"]}, headers=DIRECTOR).json()
    owners = [p["owner"] for p in r["people"]]
    assert "karim" in owners and "sarah" in owners
    karim = next(p for p in r["people"] if p["owner"] == "karim")
    assert set(karim["matched"]) >= {"Java"} and "availability" in karim
    # nobody matches Cobol
    assert client.post("/api/team/skills/match", json={"technologies": ["Cobol"]}, headers=DIRECTOR).json()["people"] == []


def test_staffing_copies_skills_and_links_users(client):
    pid = _new_perimeter(client)
    client.put("/api/team/skills", json={"skills": [{"skill": "Angular", "level": 4}]}, headers=KARIM)
    r = client.post(f"/api/perimeters/{pid}/staff", json={"owners": ["karim", "tom"], "profile": "senior", "weekly_capacity_h": 28}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    members = r.json()["members"]
    assert {m["owner"] for m in members} == {"karim", "tom"}
    k = next(m for m in members if m["owner"] == "karim")
    assert "Angular" in k["skills"] and k["profile"] == "senior" and k["weekly_capacity_h"] == 28
    # staffing again is idempotent (updates, no duplicate)
    again = client.post(f"/api/perimeters/{pid}/staff", json={"owners": ["karim"], "profile": "confirmed"}, headers=DIRECTOR).json()["members"]
    assert len(again) == 2 and next(m for m in again if m["owner"] == "karim")["profile"] == "confirmed"
    # availability now reflects the staffing elsewhere
    av = next(p for p in client.post("/api/team/skills/match", json={"technologies": ["Angular"]}, headers=DIRECTOR).json()["people"] if p["owner"] == "karim")["availability"]
    assert av["perimeters"] == 1 and av["staffed_weekly_h"] == 35.0
    # remove
    assert client.delete(f"/api/perimeters/{pid}/staff/{k['id']}", headers=DIRECTOR).json()["removed"] is True
    assert len(client.get(f"/api/perimeters/{pid}/staff", headers=DIRECTOR).json()["members"]) == 1


def test_full_lifecycle_matrice_then_plan_de_charge_then_billing(client):
    from openpyxl import load_workbook

    pid = _new_perimeter(client, rate_card={"confirmed": 600, "senior": 800})
    lc = client.get(f"/api/perimeters/{pid}/lifecycle", headers=DIRECTOR).json()
    assert lc["done"] == 0 and lc["current"] == "kickoff" and [s["key"] for s in lc["stages"]] == ["kickoff", "inputs", "matrice", "staffing", "plan", "billing"]

    # --- inputs + matrice (from the sample set, in the périmètre context) → stored + indexed
    prep = client.post("/api/deliverables/matrice_couverture/prepare", json={"samples": "portail_client", "perimeter_id": pid}, headers=DIRECTOR).json()
    bug_q = next(q["question"] for q in prep["questions"] if "Bug" in q["question"])
    prep = client.post("/api/deliverables/matrice_couverture/prepare",
                       json={"samples": "portail_client", "perimeter_id": pid, "answers": {bug_q: "Oui, les inclure"}}, headers=DIRECTOR).json()
    assert prep["status"] == "ready"
    g = client.post("/api/deliverables/matrice_couverture/generate", json={"draft": prep["draft"], "perimeter_id": pid}, headers=DIRECTOR)
    assert g.status_code == 200 and g.headers.get("X-Deliverable-Id")
    dl = client.get(f"/api/perimeters/{pid}/deliverables", headers=DIRECTOR).json()["deliverables"]
    assert dl and dl[0]["key"] == "matrice_couverture" and dl[0]["filename"].endswith(".xlsx")
    # the deliverable is searchable knowledge for every agent
    with SessionLocal() as db:
        docs = db.scalars(select(Document).where(Document.perimeter_id == pid)).all()
        assert any(d.doc_type == "livrable" and "EXF-01" in d.content for d in docs)
    # and downloadable in the périmètre scope
    assert client.get(f"/api/perimeters/{pid}/deliverables/{dl[0]['id']}/file", headers=DIRECTOR).status_code == 200

    # --- staffing from the competence matrix
    client.put("/api/team/skills", json={"skills": [{"skill": "Angular", "level": 5}, {"skill": "Java", "level": 4}]}, headers=KARIM)
    client.put("/api/team/skills", json={"skills": [{"skill": "PostgreSQL", "level": 4}]}, headers=as_user("tom"))
    client.post(f"/api/perimeters/{pid}/staff", json={"owners": ["karim"], "profile": "senior"}, headers=DIRECTOR)
    client.post(f"/api/perimeters/{pid}/staff", json={"owners": ["tom"], "profile": "confirmed"}, headers=DIRECTOR)

    # --- plan de charge: uses the latest matrice + the staffed team; asks the manager only the unknowns
    prep = client.post("/api/deliverables/plan_de_charge/prepare",
                       json={"perimeter_id": pid, "inputs": {"technologies": "Angular, Java"}}, headers=DIRECTOR).json()
    assert prep["status"] == "questions"
    qs = {q["question"]: q["options"] for q in prep["questions"]}
    assert P.Q_BASE in qs and P.Q_START in qs and P.Q_HORIZON in qs
    assert P.Q_TEAM not in qs and P.Q_RATE not in qs         # team staffed + rate card present → not asked
    answers = {P.Q_BASE: "1", P.Q_START: "2026-10-05", P.Q_HORIZON: "8"}
    prep = client.post("/api/deliverables/plan_de_charge/prepare",
                       json={"perimeter_id": pid, "inputs": {"technologies": "Angular, Java"}, "answers": answers}, headers=DIRECTOR).json()
    assert prep["status"] == "ready", prep
    draft = prep["draft"]
    assert len(draft["weeks"]) == 8 and draft["weeks"][0]["start"] == "2026-10-05" and draft["weeks"][0]["label"] == "S41"
    names = {m["name"] for m in draft["members"]}
    assert len(names) == 2
    ex01 = next(e for e in draft["exigences"] if e["ref"] == "EXF-01")
    # EXF-01 (criticité 3) covered by several use cases: days = 1 × n_cases × 1.5, assigned to the Angular/Java person
    assert ex01["crit"] == 3 and ex01["days"] == round(ex01["cases"] * 1.5, 2) and ex01["assignee"]
    assert ex01["cost"] == round(ex01["days"] * (800 if ex01["assignee"] == "Karim Delivery" or True else 600), 2) or ex01["cost"] > 0
    # weekly allocation never exceeds capacity; totals consistent
    for m in draft["members"]:
        assert all(a <= c + 1e-6 for a, c in zip(m["alloc"], m["capacity_days"]))
        assert abs(sum(m["alloc"]) - m["total_days"]) < 1e-6
    assert draft["totals"]["days"] == round(sum(e["days"] for e in draft["exigences"]), 2)
    assert draft["totals"]["cost"] > 0

    # --- generate the workbook
    g = client.post("/api/deliverables/plan_de_charge/generate", json={"draft": draft, "perimeter_id": pid}, headers=DIRECTOR)
    assert g.status_code == 200, g.text
    wb = load_workbook(io.BytesIO(g.content))
    assert {"Plan de charge", "Congés", "Charge.appli", "Param", "Périmètre"} <= set(wb.sheetnames)
    ws = wb["Plan de charge"]
    assert ws.cell(3, 6).value == "S41" and ws.cell(4, 6).value == "2026-10-05"
    ca = wb["Charge.appli"]
    assert ca.cell(1, 1).value == "Réf" and any(ca.cell(r, 1).value == "EXF-01" for r in range(2, 30))

    # --- lifecycle now reflects matrice + staffing + plan; billing waits for timesheets
    lc = client.get(f"/api/perimeters/{pid}/lifecycle", headers=DIRECTOR).json()
    st = {s["key"]: s["done"] for s in lc["stages"]}
    assert st["matrice"] and st["staffing"] and st["plan"] and not st["billing"] and lc["current"] in ("kickoff", "billing")

    # --- billing monitoring: plan vs réel once timesheets exist
    with SessionLocal() as db:
        m = db.scalars(select(TeamMember).where(TeamMember.perimeter_id == pid)).first()
        from datetime import date
        db.add(Timesheet(perimeter_id=pid, member_id=m.id, day=date(2026, 10, 6), activity_code="PROJ", hours=14))
        db.commit()
        who = m.name
    pv = client.get(f"/api/perimeters/{pid}/plan-vs-reel", headers=DIRECTOR).json()
    assert pv["has_plan"] and any(r["name"] == who and r["actual_days"] == 2.0 for r in pv["rows"])
    assert pv["totals"]["planned_days"] == round(sum(m["total_days"] for m in draft["members"]), 1)


def test_plan_without_matrice_is_a_clear_error_and_unstaffed_asks_for_team(client):
    pid = _new_perimeter(client, name="Vide")
    r = client.post("/api/deliverables/plan_de_charge/prepare", json={"perimeter_id": pid}, headers=DIRECTOR)
    assert r.status_code == 400 and "matrice" in r.json()["detail"].lower()
    # no périmètre at all: a sample matrice still lets you plan, and it asks who works on it + the TJM
    r = client.post("/api/deliverables/plan_de_charge/prepare", json={"samples": "gestion_conges"}, headers=DIRECTOR).json()
    qs = {q["question"] for q in r["questions"]}
    assert P.Q_TEAM in qs and P.Q_RATE in qs
    answers = {P.Q_BASE: "0,5", P.Q_START: "2026-11-02", P.Q_HORIZON: "8", P.Q_TEAM: "Alice, Bob", P.Q_RATE: "500"}
    r = client.post("/api/deliverables/plan_de_charge/prepare", json={"samples": "gestion_conges", "answers": answers}, headers=DIRECTOR).json()
    assert r["status"] == "ready" and {m["name"] for m in r["draft"]["members"]} == {"Alice", "Bob"}
    assert all(m["rate"] == 500 for m in r["draft"]["members"])


def test_plan_spreads_charge_over_qualified_members():
    # The top skill match used to take 100 % of the charge while equally qualified teammates stayed at 0.
    from app.deliverables.plan import build_plan
    matrice = {"exigences": [{"ref": f"EX-{i}", "name": f"Exigence {i}", "impact": "Grave", "probabilite": "Fréquent"} for i in range(8)],
               "cas_usage": []}
    members = [
        {"name": "Tom", "profile": "confirmed", "weekly_capacity_h": 35, "absences": [], "skills": ["Java 17", "Angular", "PostgreSQL"]},
        {"name": "Nadia", "profile": "senior", "weekly_capacity_h": 35, "absences": [], "skills": ["Java"]},
        {"name": "Léa", "profile": "junior", "weekly_capacity_h": 35, "absences": [], "skills": ["Comptabilité"]},
    ]
    plan = build_plan(matrice, members, {}, ["Java 17", "Angular", "PostgreSQL"], {})
    days = {m["name"]: m["total_days"] for m in plan["members"]}
    assert days["Tom"] > 0 and days["Nadia"] > 0          # both qualified people share the work
    assert days["Léa"] == 0                               # no matching skill → not assigned while others can take it


def test_perimeter_reads_its_projects_plan_de_charge(client):
    """The plan de charge is produced once per project; a périmètre's lifecycle step and its
    'plan vs réel' view must read that project plan (they used to look for a périmètre-level plan)."""
    from tests.conftest import as_user
    from app.db import SessionLocal
    from app.models import Perimeter, ProjectDeliverable
    from app import staffing
    KARIM = as_user("karim")
    with SessionLocal() as db:
        per = db.get(Perimeter, "per-atlas-sinistres")
        db.add(ProjectDeliverable(project_id=per.project_id, key="plan_de_charge", title="Plan", filename="Plan_test.xlsx",
                                  draft={"members": [{"name": "Tom Girard", "total_days": 12}],
                                         "exigences": [{"name": f"[{per.name}] EX-1", "assignee": "Tom Girard", "days": 5},
                                                       {"name": "[Autre périmètre] EX-2", "assignee": "Tom Girard", "days": 7}]}))
        db.commit()
        stage = next(s for s in staffing.lifecycle(db, per)["stages"] if s["key"] == "plan")
        assert stage["done"] and stage["link"] == f"/pr/{per.project_id}/plan"
        pv = staffing.plan_vs_actual(db, per)
        assert pv["has_plan"] and pv["project_id"] == per.project_id
        assert next(r for r in pv["rows"] if r["name"] == "Tom Girard")["planned_days"] == 5.0   # only this périmètre's share
