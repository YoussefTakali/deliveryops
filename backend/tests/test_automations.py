"""Automatisations: schedule computation, workflow validation, document steps, owner scoping,
atomic claim, dry-run mail preview (Graph mocked) and the compile repair round (LLM mocked)."""

import copy
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import docx
import httpx
import openpyxl
import pytest

from app import llm, microsoft_graph
from app.automations import compiler, documents
from app.automations.schedule import next_run_at
from app.automations.validate import validate
from tests.conftest import as_user

UTC = timezone.utc
PARIS = {"timezone": "Europe/Paris"}


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------- schedule

def test_schedule_daily_weekly_monthly():
    daily = {"type": "schedule", "every": "day", "at": "08:00", **PARIS}
    # Fri 2026-09-18 09:00 Paris (07:00Z) -> next day 08:00 Paris (06:00Z, CEST)
    assert next_run_at(daily, _utc(2026, 9, 18, 7, 0)) == _utc(2026, 9, 19, 6, 0)
    # strictly after: exactly at the occurrence -> the following one
    assert next_run_at(daily, _utc(2026, 9, 19, 6, 0)) == _utc(2026, 9, 20, 6, 0)

    weekly = {"type": "schedule", "every": "week", "weekdays": [0, 3], "at": "08:00", **PARIS}
    assert next_run_at(weekly, _utc(2026, 9, 18, 7, 0)) == _utc(2026, 9, 21, 6, 0)  # Fri -> Mon
    assert next_run_at(weekly, _utc(2026, 9, 21, 6, 0)) == _utc(2026, 9, 24, 6, 0)  # Mon -> Thu

    monthly = {"type": "schedule", "every": "month", "day_of_month": 1, "at": "09:30", **PARIS}
    assert next_run_at(monthly, _utc(2026, 9, 18, 7, 0)) == _utc(2026, 10, 1, 7, 30)
    assert next_run_at(monthly, _utc(2026, 12, 5)) == _utc(2027, 1, 1, 8, 30)  # CET in winter


def test_schedule_hours_and_other_timezone():
    every3 = {"type": "schedule", "every": "hours", "interval_hours": 3, "at": "08:00", **PARIS}
    assert next_run_at(every3, _utc(2026, 9, 18, 10, 30)) == _utc(2026, 9, 18, 12, 0)  # 12:30 -> 14:00 Paris
    assert next_run_at(every3, _utc(2026, 9, 18, 21, 30)) == _utc(2026, 9, 19, 6, 0)  # 23:30 -> next day 08:00
    ny = {"type": "schedule", "every": "day", "at": "09:00", "timezone": "America/New_York"}
    assert next_run_at(ny, _utc(2026, 9, 18, 0, 0)) == _utc(2026, 9, 18, 13, 0)
    assert next_run_at({"type": "manual"}, _utc(2026, 9, 18)) is None


def test_schedule_dst_edges():
    daily = {"type": "schedule", "every": "day", "at": "08:00", **PARIS}
    # Spring forward in Paris on 2026-03-29: 08:00 is 07:00Z before, 06:00Z after.
    assert next_run_at(daily, _utc(2026, 3, 27, 8, 0)) == _utc(2026, 3, 28, 7, 0)
    assert next_run_at(daily, _utc(2026, 3, 28, 8, 0)) == _utc(2026, 3, 29, 6, 0)
    # A wall time that doesn't exist (02:30 on the spring-forward night) runs at the equivalent instant.
    gap = {"type": "schedule", "every": "day", "at": "02:30", **PARIS}
    assert next_run_at(gap, _utc(2026, 3, 28, 12, 0)) == _utc(2026, 3, 29, 1, 30)
    # An ambiguous wall time (fall back, 2026-10-25) runs once, at its first occurrence (CEST).
    assert next_run_at(gap, _utc(2026, 10, 24, 12, 0)) == _utc(2026, 10, 25, 0, 30)
    assert next_run_at(gap, _utc(2026, 10, 25, 0, 30)) == _utc(2026, 10, 26, 1, 30)


def test_schedule_rejects_invalid():
    for bad in ({"every": "week", "weekdays": []}, {"every": "month", "day_of_month": 31},
                {"every": "day", "at": "25:00"}, {"every": "day", "timezone": "Mars/Olympus"}, {"every": "yearly"}):
        with pytest.raises(ValueError):
            next_run_at({"type": "schedule", **PARIS, **bad}, _utc(2026, 9, 18))


# ---------------------------------------------------------------- validate

def _hr_workflow() -> dict:
    wf = copy.deepcopy(compiler.HR_EXAMPLE_OUTPUT)
    return {"trigger": wf["trigger"], "steps": wf["steps"],
            "inputs": {"modele_convention": {"label": "Modèle", "type": "file", "value": {"file_id": "f1", "name": "m.docx"}}}}


def test_validate_accepts_the_hr_example():
    report = validate(**_hr_workflow())
    assert report["errors"] == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda w: w["steps"].append({"id": "s7", "type": "mail.teleport", "params": {}}), "Type d'étape inconnu"),
    (lambda w: w["steps"][4]["params"].pop("file_id"), "indiquez file_id ou path"),
    (lambda w: w["steps"][5]["params"].pop("subject"), "Paramètre obligatoire manquant « subject »"),
    (lambda w: w["steps"][1]["params"].update(message_id="{{steps.s3.rows}}"), "pas encore exécutée"),
    (lambda w: w["steps"][2]["params"].update(file_id="{{steps.s9.file.file_id}}"), "Étape inconnue « s9 »"),
    (lambda w: w["steps"][3]["params"].update(template_file_id="{{inputs.autre.file_id}}"), "Entrée inconnue"),
    (lambda w: w["steps"][4].update(foreach="{{item.enfants}}"), "Boucle imbriquée"),
    (lambda w: w["steps"][2]["params"].update(file_id="{{steps.s2.$index.file.file_id}}"), "« $index » n'est utilisable"),
    (lambda w: w["steps"][2]["params"].update(sheet="{{item.Feuille}}"), "n'est disponible que dans une étape foreach"),
    (lambda w: w["steps"][3].update(foreach="rows: {{steps.s3.rows}}"), "foreach doit être une seule expression"),
    (lambda w: w["steps"].append({"id": "s1", "type": "flow.confirm", "params": {"message": "ok ?"}}), "en double"),
    (lambda w: w["steps"].append({"id": "s7", "type": "local.powershell", "params": {"script": "echo {{item}}"}}), "ne doit pas contenir"),
    (lambda w: w["trigger"].update(weekdays=[9]), "Jours de la semaine invalides"),
])
def test_validate_reports_errors(mutate, expected):
    wf = _hr_workflow()
    mutate(wf)
    messages = [e["message"] for e in validate(**wf)["errors"]]
    assert any(expected in m for m in messages), messages


def test_validate_warns_on_unknown_output_field_and_list_outputs():
    wf = _hr_workflow()
    wf["steps"][2]["params"]["file_id"] = "{{steps.s2.fichier.file_id}}"
    wf["steps"][5]["params"]["attachments"] = ["{{steps.s4.file.file_id}}"]
    report = validate(**wf)
    assert report["errors"] == []
    warnings = " | ".join(w["message"] for w in report["warnings"])
    assert "Champ « fichier »" in warnings and "est une liste" in warnings


def test_dataset_input_and_row_scope():
    """A dataset input (replay once per row) is accepted, and {{row.<col>}} validates only with one."""
    steps = [{"id": "s1", "type": "vision.type", "params": {"target": "champ", "text": "{{row.Email}}", "clear": False}}]
    # with a dataset input, row.* resolves and there are no errors
    inputs = {"donnees": {"label": "Clients", "type": "dataset",
                          "value": [{"Email": "a@x.fr", "Nom": "Dupont"}, {"Email": "b@x.fr", "Nom": "Martin"}]}}
    ok = validate({"type": "manual"}, steps, inputs)
    assert ok["errors"] == [], ok
    assert not any("row" in w["message"] for w in ok["warnings"])
    # without a dataset, row.* is a (non-blocking) warning, not an error
    warn = validate({"type": "manual"}, steps, {})
    assert warn["errors"] == []
    assert any("jeu de données" in w["message"].lower() for w in warn["warnings"]), warn
    # a malformed dataset value is a blocking error
    bad = validate({"type": "manual"}, steps, {"donnees": {"label": "X", "type": "dataset", "value": "pas une liste"}})
    assert any("liste de lignes" in e["message"] for e in bad["errors"]), bad
    # at most one dataset per automation
    two = validate({"type": "manual"}, steps, {
        "a": {"label": "A", "type": "dataset", "value": [{"Email": "a@x.fr"}]},
        "b": {"label": "B", "type": "dataset", "value": [{"Email": "b@x.fr"}]}})
    assert any("un seul jeu de données" in e["message"].lower() for e in two["errors"]), two


# ---------------------------------------------------------------- documents

def _docx_template() -> bytes:
    d = docx.Document()
    header = d.sections[0].header.paragraphs[0]
    header.add_run("Convention — «Nom»")
    p = d.add_paragraph()
    for chunk in ("Bonjour {{Pr", "énom", "}} {{ nom }}, né le {", "{Date de naissance}}.", " Reste [inconnu]."):
        p.add_run(chunk).bold = chunk.startswith("Bonjour")
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "E-mail"
    table.cell(0, 1).text = "[Email]"
    d.add_paragraph("Adresse : {{Adresse}}")
    d.add_paragraph("Manquant : {{Service}}")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def test_fill_docx_split_runs_header_table_and_newlines():
    data = {"Prénom": "Léa", "NOM": "Martin", "date_de_naissance": "2003-05-14", "email": "lea@example.com",
            "Adresse": "1 rue de Paris\n75001 Paris"}
    filled, missing = documents.fill_template("modele.docx", _docx_template(), data)
    d = docx.Document(io.BytesIO(filled))
    body = "\n".join(p.text for p in d.paragraphs)
    assert "Bonjour Léa Martin, né le 14/05/2003." in body  # split across runs, accent/case-insensitive keys
    assert "Reste [inconnu]." in body  # unknown [..] left untouched
    assert d.paragraphs[0].runs[0].bold  # formatting of the run holding the placeholder start kept
    assert d.sections[0].header.paragraphs[0].text == "Convention — Martin"
    assert d.tables[0].cell(0, 1).text == "lea@example.com"
    addr = next(p for p in d.paragraphs if p.text.startswith("Adresse"))
    assert addr.text == "Adresse : 1 rue de Paris\n75001 Paris"  # \n rendered as <w:br/>
    assert missing == ["Service"]


def test_fill_xlsx_and_text_templates():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["C1"] = "Stagiaire : {{Nom}}", "{{Heures}}", "=B1*2"
    buf = io.BytesIO()
    wb.save(buf)
    filled, missing = documents.fill_template("fiche.xlsx", buf.getvalue(), {"nom": "Martin", "Heures": 35})
    ws = openpyxl.load_workbook(io.BytesIO(filled)).active
    assert ws["A1"].value == "Stagiaire : Martin"
    assert ws["B1"].value == 35  # whole-cell placeholder with a number stays numeric
    assert ws["C1"].value == "=B1*2"
    assert missing == []
    html_out, _ = documents.fill_template("mail.html", "<p>Bonjour {{Nom}}</p>".encode(), {"Nom": "<b>Tom</b>"})
    assert html_out.decode() == "<p>Bonjour &lt;b&gt;Tom&lt;/b&gt;</p>"
    with pytest.raises(documents.DocumentError):
        documents.fill_template("modele.doc", b"...", {})


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vide"
    ws2 = wb.create_sheet("Stagiaires")
    for r in rows:
        ws2.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_table_xlsx_csv_docx():
    xlsx = _xlsx([[], ["Nom", "Prénom", "Email", "Arrivée", "Heures"],
                  ["Martin", "Léa", "lea@example.com", datetime(2026, 9, 21), 35.0],
                  [None, None, None, None, None], ["Durand", "Tom", "tom@example.com", datetime(2026, 9, 21), 17.5]])
    t = documents.extract_table_structured("stagiaires.xlsx", xlsx)  # first NON-EMPTY sheet, first non-empty row
    assert t["columns"] == ["Nom", "Prénom", "Email", "Arrivée", "Heures"]
    assert t["count"] == 2
    assert t["rows"][0] == {"Nom": "Martin", "Prénom": "Léa", "Email": "lea@example.com", "Arrivée": "2026-09-21", "Heures": 35}
    assert t["rows"][1]["Heures"] == 17.5
    assert documents.extract_table_structured("s.xlsx", xlsx, sheet="stagiaires")["count"] == 2
    with pytest.raises(documents.DocumentError):
        documents.extract_table_structured("s.xlsx", xlsx, sheet="Absente")

    semicolon = "Nom;Prénom;Email\nMartin;Léa;lea@example.com\nDurand;Tom;tom@example.com\n".encode("cp1252")
    t = documents.extract_table_structured("liste.csv", semicolon)
    assert t["columns"] == ["Nom", "Prénom", "Email"] and t["rows"][1]["Prénom"] == "Tom"
    buf = io.StringIO()
    csv.writer(buf).writerows([["name", "email"], ["Ann", "ann@example.com"]])
    assert documents.extract_table_structured("x.csv", buf.getvalue().encode())["rows"] == [{"name": "Ann", "email": "ann@example.com"}]
    tab = documents.extract_table_structured("x.csv", b"a\tb\n1\t2\n")
    assert tab["rows"] == [{"a": "1", "b": "2"}]

    d = docx.Document()
    d.add_paragraph("Liste")
    table = d.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("Nom", "Email"), ("Martin", "lea@example.com"), ("Durand", "tom@example.com")]):
        table.cell(r, 0).text, table.cell(r, 1).text = a, b
    out = io.BytesIO()
    d.save(out)
    t = documents.extract_table_structured("liste.docx", out.getvalue())
    assert t["rows"] == [{"Nom": "Martin", "Email": "lea@example.com"}, {"Nom": "Durand", "Email": "tom@example.com"}]
    assert documents.extract_table_structured("scan.pdf", b"%PDF") is None  # -> LLM path


# ---------------------------------------------------------------- API

def _create(client, user="sarah", **body) -> dict:
    payload = {"name": "Test", "trigger": {"type": "manual"}, "steps": [], "inputs": {}, **body}
    r = client.post("/api/automations", json=payload, headers=as_user(user))
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, name: str, data: bytes, user="sarah", **form) -> dict:
    r = client.post("/api/automations/files", files={"file": (name, data)}, data=form, headers=as_user(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_catalog_and_permission(client):
    cat = client.get("/api/automations/catalog", headers=as_user("tom")).json()
    assert {"mail.search", "doc.fill_template", "local.powershell", "flow.confirm"} <= {e["type"] for e in cat}
    assert all({"type", "title", "executor", "side_effect", "params", "output"} <= set(e) for e in cat)


def test_owner_scoping(client):
    a = _create(client, "sarah", name="Privée")
    f = _upload(client, "note.txt", b"hello", automation_id=a["id"])
    run = client.post(f"/api/automations/{a['id']}/runs", json={"trigger": "manual"}, headers=as_user("sarah")).json()
    karim = as_user("karim")
    assert client.get(f"/api/automations/{a['id']}", headers=karim).status_code == 404
    assert client.put(f"/api/automations/{a['id']}", json={"name": "x"}, headers=karim).status_code == 404
    assert client.delete(f"/api/automations/{a['id']}", headers=karim).status_code == 404
    assert client.post(f"/api/automations/{a['id']}/runs", json={}, headers=karim).status_code == 404
    assert client.get(f"/api/automations/runs/{run['id']}", headers=karim).status_code == 404
    assert client.post(f"/api/automations/runs/{run['id']}/finish", json={"status": "failed"}, headers=karim).status_code == 404
    assert client.get(f"/api/automations/files/{f['file_id']}", headers=karim).status_code == 404
    assert client.post("/api/automations/files", files={"file": ("x.txt", b"x")}, data={"automation_id": a["id"]}, headers=karim).status_code == 404
    assert a["id"] not in {x["id"] for x in client.get("/api/automations", headers=karim).json()}
    assert client.get(f"/api/automations/files/{f['file_id']}", headers=as_user("sarah")).content == b"hello"


def test_activate_validation_and_script_approval(client):
    script = "Get-ChildItem $env:USERPROFILE\\Documents | Measure-Object"
    a = _create(client, steps=[{"id": "s1", "type": "local.powershell", "title": "Compter", "params": {"script": script}}],
                trigger={"type": "schedule", "every": "day", "at": "07:00", **PARIS})
    r = client.post(f"/api/automations/{a['id']}/activate", headers=as_user("sarah"))
    assert r.status_code == 400 and "non approuvé" in r.json()["detail"]
    import hashlib
    assert client.post(f"/api/automations/{a['id']}/approve-script", json={"sha256": "0" * 64}, headers=as_user("sarah")).status_code == 400
    digest = hashlib.sha256(script.encode()).hexdigest()
    assert client.post(f"/api/automations/{a['id']}/approve-script", json={"sha256": digest}, headers=as_user("sarah")).json()["approved_script_hashes"] == [digest]
    active = client.post(f"/api/automations/{a['id']}/activate", headers=as_user("sarah")).json()
    assert active["status"] == "active" and active["next_run_at"]
    # an active automation can't be saved into an invalid state
    bad = client.put(f"/api/automations/{a['id']}", json={"steps": [{"id": "s1", "type": "nope"}]}, headers=as_user("sarah"))
    assert bad.status_code == 400
    paused = client.post(f"/api/automations/{a['id']}/pause", headers=as_user("sarah")).json()
    assert paused["status"] == "paused" and paused["next_run_at"] is None


def _make_due(automation_id: str):
    from app.db import SessionLocal
    from app.models import Automation

    with SessionLocal() as db:
        db.get(Automation, automation_id).next_run_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()


def test_due_and_claim_is_atomic(client):
    a = _create(client, "karim", steps=[{"id": "s1", "type": "file.create", "params": {"name": "a.txt", "content": "x"}}],
                trigger={"type": "schedule", "every": "hours", "interval_hours": 1, **PARIS})
    assert client.post(f"/api/automations/{a['id']}/activate", headers=as_user("karim")).status_code == 200
    _make_due(a["id"])
    assert a["id"] in {x["id"] for x in client.get("/api/automations/due", headers=as_user("karim")).json()}
    assert a["id"] not in {x["id"] for x in client.get("/api/automations/due", headers=as_user("sarah")).json()}

    first = client.post(f"/api/automations/{a['id']}/claim", headers=as_user("karim"))
    assert first.status_code == 200, first.text
    run = first.json()
    assert run["trigger"] == "schedule" and run["status"] == "awaiting_confirmation"  # require_confirmation default
    assert client.post(f"/api/automations/{a['id']}/claim", headers=as_user("karim")).status_code == 409
    assert a["id"] not in {x["id"] for x in client.get("/api/automations/due", headers=as_user("karim")).json()}

    # the race itself: two devices read the same due row, only one conditional UPDATE matches
    from app.auth import DEV_USERS
    from app.db import SessionLocal
    from app.models import Automation
    from app.routes.automations import ClaimConflict, claim

    _make_due(a["id"])
    with SessionLocal() as db1, SessionLocal() as db2:
        a1, a2 = db1.get(Automation, a["id"]), db2.get(Automation, a["id"])
        claim(db1, DEV_USERS["karim"], a1)
        with pytest.raises(ClaimConflict):
            claim(db2, DEV_USERS["karim"], a2)

    # confirm -> running -> finish updates last_run_*
    assert client.post(f"/api/automations/runs/{run['id']}/confirm", headers=as_user("karim")).json()["status"] == "running"
    client.post(f"/api/automations/runs/{run['id']}/log", json={"entries": [{"step_id": "s1", "message": "ok", "output_summary": {"n": 1}}]}, headers=as_user("karim"))
    done = client.post(f"/api/automations/runs/{run['id']}/finish", json={"status": "succeeded"}, headers=as_user("karim")).json()
    assert done["status"] == "succeeded" and done["log"][0]["message"] == "ok"
    assert client.post(f"/api/automations/runs/{run['id']}/finish", json={"status": "failed"}, headers=as_user("karim")).status_code == 409
    assert client.get(f"/api/automations/{a['id']}", headers=as_user("karim")).json()["last_run_status"] == "succeeded"


class _FakeGraph:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/me/messages"):
            return httpx.Response(200, json={"value": [
                {"id": "m2", "subject": "Newsletter", "from": {"emailAddress": {"name": "News", "address": "news@x.com"}},
                 "receivedDateTime": "2026-09-14T08:00:00Z", "bodyPreview": "...", "hasAttachments": False},
                {"id": "m1", "subject": "Liste des stagiaires - semaine 38", "from": {"emailAddress": {"name": "RH", "address": "rh@contoso.com"}},
                 "receivedDateTime": "2026-09-14T07:00:00Z", "bodyPreview": "Ci-joint", "hasAttachments": True},
            ]})
        if path.endswith("/me/messages/m1/attachments"):
            return httpx.Response(200, json={"value": [
                {"id": "a0", "name": "logo.png", "size": 10, "contentType": "image/png", "isInline": True},
                {"id": "a1", "name": "stagiaires.xlsx", "size": 5000, "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "isInline": False},
            ]})
        if path.endswith("/me/messages/m1/attachments/a1"):
            import base64
            data = _xlsx([["Nom", "Email"], ["Martin", "lea@example.com"]])
            return httpx.Response(200, json={"id": "a1", "name": "stagiaires.xlsx", "contentBytes": base64.b64encode(data).decode()})
        if path.endswith("/me/sendMail") and request.method == "POST":
            return httpx.Response(202)
        return httpx.Response(404, json={"error": {"message": "not found"}})


@pytest.fixture
def fake_graph(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(microsoft_graph, "_client", lambda token: httpx.Client(base_url=microsoft_graph.GRAPH, transport=httpx.MockTransport(fake.handler)))
    return fake


HR_STEPS = [
    {"id": "s1", "type": "mail.search", "params": {"subject_contains": "stagiaires", "has_attachment": True}},
    {"id": "s2", "type": "mail.get_attachment", "params": {"message_id": "{{steps.s1.latest.id}}", "name_contains": ".xlsx"}},
    {"id": "s3", "type": "file.extract_table", "params": {"file_id": "{{steps.s2.file.file_id}}"}},
    {"id": "s4", "type": "doc.fill_template", "foreach": "{{steps.s3.rows}}", "params": {"template_file_id": "{{inputs.modele.file_id}}", "data": "{{item}}"}},
    {"id": "s5", "type": "mail.send", "foreach": "{{steps.s3.rows}}", "params": {"to": "{{item.Email}}", "subject": "Convention", "body": "Bonjour", "attachments": ["{{steps.s4.$index.file.file_id}}"]}},
]


def test_server_steps_end_to_end_with_dry_run_mail(client, fake_graph):
    from app.db import SessionLocal
    from app.models import AuditLog

    a = _create(client, "lea", steps=HR_STEPS)
    template = _upload(client, "Convention.docx", _docx_template(), user="lea", automation_id=a["id"])
    client.put(f"/api/automations/{a['id']}", json={"inputs": {"modele": {"label": "Modèle", "type": "file", "value": {"file_id": template["file_id"], "name": template["name"]}}}}, headers=as_user("lea"))
    run = client.post(f"/api/automations/{a['id']}/runs", json={"trigger": "test", "dry_run": True}, headers=as_user("lea")).json()
    assert run["status"] == "running"  # manual/test runs never wait for confirmation
    h = {**as_user("lea"), "X-Graph-Token": "graph-token"}
    step = lambda sid, typ, params: client.post(f"/api/automations/runs/{run['id']}/steps", json={"step_id": sid, "type": typ, "params": params}, headers=h)

    found = step("s1", "mail.search", {"subject_contains": "STAGIAIRES", "has_attachment": True}).json()
    assert found["count"] == 1 and found["latest"]["id"] == "m1"
    assert found["latest"]["attachments"] == [{"id": "a1", "name": "stagiaires.xlsx", "size": 5000, "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]
    att = step("s2", "mail.get_attachment", {"message_id": "m1", "name_contains": ".xlsx"}).json()["file"]
    table = step("s3", "file.extract_table", {"file_id": att["file_id"]}).json()
    assert table["rows"] == [{"Nom": "Martin", "Email": "lea@example.com"}]
    filled = step("s4", "doc.fill_template", {"template_file_id": template["file_id"], "data": {**table["rows"][0], "Prénom": "Léa"}}).json()
    assert filled["file"]["name"] == "Convention - Martin.docx"
    out = docx.Document(io.BytesIO(client.get(f"/api/automations/files/{filled['file']['file_id']}", headers=as_user("lea")).content))
    assert out.sections[0].header.paragraphs[0].text == "Convention — Martin"

    sent = step("s5", "mail.send", {"to": "lea@example.com", "subject": "Convention", "body": "Bonjour", "attachments": [filled["file"]["file_id"]]})
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"sent": False, "preview": {"to": ["lea@example.com"], "cc": [], "subject": "Convention", "body": "Bonjour", "attachments": ["Convention - Martin.docx"]}}
    assert not any(r.url.path.endswith("/sendMail") for r in fake_graph.requests)  # dry run never sends
    with SessionLocal() as db:
        entry = db.query(AuditLog).filter(AuditLog.action == "automation.mail_send", AuditLog.entity_id == run["id"]).one()
        assert entry.details["dry_run"] is True and entry.details["to"] == ["lea@example.com"]

    # a step that isn't part of the saved automation is refused; local steps never run on the server
    assert step("s9", "mail.send", {"to": "x@y.z"}).status_code == 400
    # another user can't run steps on this run
    assert client.post(f"/api/automations/runs/{run['id']}/steps", json={"step_id": "s1", "type": "mail.search", "params": {}}, headers=as_user("karim")).status_code == 404


def test_real_mail_send_limit_and_graph_errors(client, fake_graph, monkeypatch):
    from app.automations import catalog

    a = _create(client, "tom", steps=HR_STEPS)
    f = _upload(client, "piece.txt", b"contenu", user="tom", run_id="")
    run = client.post(f"/api/automations/{a['id']}/runs", json={"trigger": "manual"}, headers=as_user("tom")).json()
    url = f"/api/automations/runs/{run['id']}/steps"
    body = {"step_id": "s5", "type": "mail.send", "params": {"to": "Léa <lea@example.com>; tom@example.com", "subject": "Hello", "body": "<b>Hi</b>", "body_is_html": True, "attachments": [f["file_id"]]}}
    # no Graph token -> 424 (not 401, which would sign the SPA out)
    assert client.post(url, json=body, headers=as_user("tom")).status_code == 424
    r = client.post(url, json=body, headers={**as_user("tom"), "X-Graph-Token": "t"})
    assert r.status_code == 200 and r.json() == {"sent": True, "to": ["lea@example.com", "tom@example.com"]}
    sent = [q for q in fake_graph.requests if q.url.path.endswith("/sendMail")]
    payload = json.loads(sent[0].content)["message"]
    assert payload["body"] == {"contentType": "HTML", "content": "<b>Hi</b>"}
    assert payload["attachments"][0]["name"] == "piece.txt" and payload["attachments"][0]["contentBytes"]
    assert client.post(url, json={**body, "params": {**body["params"], "to": "pas-une-adresse"}}, headers={**as_user("tom"), "X-Graph-Token": "t"}).status_code == 400

    monkeypatch.setattr(catalog, "MAX_MAIL_SENDS_PER_RUN", 1)
    assert client.post(url, json=body, headers={**as_user("tom"), "X-Graph-Token": "t"}).status_code == 429


def test_recording_upload_and_text_only_analysis(client):
    meta = {"duration_s": 42, "events": [{"t": 1.5, "type": "click", "x": 10, "y": 20, "button": "left"}, {"t": 3, "type": "shortcut", "keys": "Ctrl+C"}],
            "narration": [{"t": 2, "text": "je copie la liste des stagiaires"}],
            "keyframes": [{"t": 1, "window_title": "Boîte de réception - Outlook", "process": "OUTLOOK.EXE", "field": "f0"},
                          {"t": 5, "window_title": "stagiaires.xlsx - Excel", "process": "EXCEL.EXE", "field": "f1"}]}
    r = client.post("/api/automations/recordings", data={"meta": json.dumps(meta)},
                    files=[("f0", ("f0.jpg", b"\xff\xd8jpeg0", "image/jpeg")), ("f1", ("f1.jpg", b"\xff\xd8jpeg1", "image/jpeg"))], headers=as_user("sarah"))
    assert r.status_code == 200, r.text
    rid = r.json()["recording_id"]
    analysis = client.post(f"/api/automations/recordings/{rid}/analyze", headers=as_user("sarah")).json()
    # LLM is in mock mode here: deterministic, text-only degradation
    assert analysis["vision_used"] is False
    assert "stagiaires" in analysis["summary"]
    assert [s["detail"] for s in analysis["observed_steps"]] == ["Boîte de réception - Outlook", "stagiaires.xlsx - Excel"]
    assert client.post(f"/api/automations/recordings/{rid}/analyze", headers=as_user("karim")).status_code == 404


def test_compile_runs_one_repair_round(client, monkeypatch):
    calls = []
    invalid = compiler.CompiledWorkflow(
        name="Conventions", trigger={"type": "manual"},
        steps=[{"id": "s1", "type": "excel.read", "params": {}}], questions=["Quel objet ?"], explanation="…",
    )
    valid = compiler.CompiledWorkflow(**{**copy.deepcopy(compiler.HR_EXAMPLE_OUTPUT)})

    def fake_structured(**kwargs):
        calls.append(kwargs)
        return (invalid if len(calls) == 1 else valid), True

    monkeypatch.setattr(llm, "structured", fake_structured)
    r = client.post("/api/automations/compile", json={"description": compiler.HR_EXAMPLE_REQUEST, "answers": {"Quel objet ?": "Stagiaires"}}, headers=as_user("sarah"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(calls) == 2
    assert "erreurs_de_validation" in calls[1]["context"] and "excel.read" in calls[1]["context"]
    assert "Stagiaires" in calls[0]["context"]  # answers are fed to the model
    assert calls[0]["tier"] == "reasoning"
    assert [s["type"] for s in out["steps"]] == ["mail.search", "mail.get_attachment", "file.extract_table", "doc.fill_template", "local.print", "mail.send"]
    assert out["inputs"] == {"modele_convention": {"label": "Modèle Word de la convention", "type": "file"}}
    assert set(out) == {"name", "description", "trigger", "steps", "inputs", "questions", "warnings", "explanation"}

    # still invalid after the repair -> returned with the errors surfaced as a warning (no third call)
    calls.clear()
    monkeypatch.setattr(llm, "structured", lambda **kw: (calls.append(kw), (invalid, True))[1])
    out = client.post("/api/automations/compile", json={"description": "x"}, headers=as_user("sarah")).json()
    assert len(calls) == 2 and any("À corriger" in w for w in out["warnings"])

    # LLM unavailable -> 503; nothing to compile -> 400
    monkeypatch.setattr(llm, "structured", lambda **kw: (kw["fallback"](), False))
    assert client.post("/api/automations/compile", json={"description": "x"}, headers=as_user("sarah")).status_code == 503
    assert client.post("/api/automations/compile", json={}, headers=as_user("sarah")).status_code == 400


def test_public_config_exposes_automation_scopes(monkeypatch):
    from app import api

    monkeypatch.setattr(api.settings, "microsoft_tenant_id", "common")
    monkeypatch.setattr(api.settings, "microsoft_client_id", "cid")
    assert api.public_config()["microsoft"]["automationScopes"] == ["Mail.Send"]


def test_parse_recipients_keeps_display_names_intact():
    from app.automations.steps import parse_recipients

    assert parse_recipients("Léa Martin <lea@acme.fr>; bob@acme.fr,\nLEA@acme.fr") == ["lea@acme.fr", "bob@acme.fr"]
    assert parse_recipients(["a@x.io", {"email": "b@x.io"}]) == ["a@x.io", "b@x.io"]


# ---------------------------------------------------------------- desktop / web / vision steps

def test_catalog_exposes_desktop_web_and_vision_steps(client):
    cat = {e["type"]: e for e in client.get("/api/automations/catalog", headers=as_user("tom")).json()}
    for t in ("desktop.open_app", "desktop.window", "desktop.click", "desktop.type", "desktop.keys", "desktop.read",
              "desktop.read_notifications", "desktop.screenshot", "web.browse"):
        assert cat[t]["executor"] == "local", t
    assert cat["ai.vision"]["executor"] == "server" and cat["flow.wait"]["executor"] == "control"
    assert cat["desktop.click"]["side_effect"] and not cat["desktop.read"]["side_effect"]


def _desktop_workflow():
    return {
        "trigger": {"type": "manual"},
        "inputs": {},
        "steps": [
            {"id": "s1", "type": "desktop.open_app", "params": {"app": "Outlook", "wait_window": "Outlook"}},
            {"id": "s2", "type": "flow.wait", "params": {"seconds": 3}},
            {"id": "s3", "type": "desktop.click", "params": {"window": "Outlook", "name": "Nouveau courrier", "control_type": "Button"}},
            {"id": "s4", "type": "desktop.read_notifications", "params": {"app_contains": "Teams"}},
            {"id": "s5", "type": "desktop.screenshot", "params": {}},
            {"id": "s6", "type": "ai.vision", "params": {"file_id": "{{steps.s5.file.file_id}}", "question": "Quel est le dernier message ?"}},
            {"id": "s7", "type": "web.browse", "params": {"url": "https://intranet.example.com", "actions": [
                {"do": "type", "selector": "label=Recherche", "text": "{{steps.s6.answer}}"},
                {"do": "click", "selector": "text=Rechercher"},
                {"do": "extract", "selector": ".result", "name": "top", "all": True},
            ]}},
        ],
    }


def test_validate_accepts_a_desktop_and_web_workflow():
    report = validate(**_desktop_workflow())
    assert report["errors"] == [], report["errors"]
    assert any("souris/du clavier" in (w["message"] if isinstance(w, dict) else w) for w in report["warnings"])


@pytest.mark.parametrize("mutate, expected", [
    (lambda w: w["steps"][2]["params"].pop("name"), "indiquez le libellé"),
    (lambda w: w["steps"][1]["params"].update(seconds=5000), "entre 1 et 600"),
    (lambda w: w["steps"][6]["params"].update(actions=[{"do": "teleport"}]), "action n°1 invalide"),
    (lambda w: w["steps"][6]["params"].update(actions=[{"do": "click"}]), "sans selector"),
    (lambda w: w["steps"][6]["params"].update(actions="click everything"), "doit être une liste"),
    (lambda w: w["steps"][6]["params"].update(actions=[{"action": "type", "selector": "label=Recherche", "text": "{{steps.s6.answer}}"}]), "action n°1 invalide"),
])
def test_validate_rejects_bad_desktop_and_web_steps(mutate, expected):
    wf = _desktop_workflow()
    mutate(wf)
    messages = [e["message"] for e in validate(**wf)["errors"]]
    assert any(expected in m for m in messages), messages


def test_ai_vision_answers_about_an_image_and_rejects_other_files(client, monkeypatch):
    from app.config import Settings
    from app.automations import steps as steps_mod

    seen = {}

    class _Resp:
        choices = [type("C", (), {"message": type("M", (), {"content": "Le dernier message vient de Léa : « Réunion à 15 h »."})()})()]

    def fake_chat(purpose, *, model, messages, **params):
        seen.update(purpose=purpose, model=model, content=messages[0]["content"])
        return _Resp()

    monkeypatch.setattr(Settings, "use_llm", property(lambda self: True))
    monkeypatch.setattr(steps_mod.llm, "chat", fake_chat)
    a = _create(client, "lea", steps=[{"id": "s1", "type": "ai.vision", "params": {"file_id": "x", "question": "?"}}])
    run = client.post(f"/api/automations/{a['id']}/runs", json={"trigger": "manual"}, headers=as_user("lea")).json()
    img = _upload(client, "capture.png", b"\x89PNG\r\n\x1a\nfake", user="lea")
    r = client.post(f"/api/automations/runs/{run['id']}/steps", json={"step_id": "s1", "type": "ai.vision",
                    "params": {"file_id": img["file_id"], "question": "Quel est le dernier message ?"}}, headers=as_user("lea"))
    assert r.status_code == 200, r.text
    assert "Léa" in r.json()["answer"]
    assert seen["purpose"] == "automation_ai_vision" and seen["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

    txt = _upload(client, "notes.txt", b"hello", user="lea")
    r = client.post(f"/api/automations/runs/{run['id']}/steps", json={"step_id": "s1", "type": "ai.vision",
                    "params": {"file_id": txt["file_id"], "question": "?"}}, headers=as_user("lea"))
    assert r.status_code == 400 and "image" in r.json()["detail"]
