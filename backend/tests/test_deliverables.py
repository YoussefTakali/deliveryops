"""Livrables: generate real Office files (COPIL .pptx, matrice de couverture .xlsx) from a
step-by-step questionnaire, with the criticité grid and per-company template overrides."""

import io

from app.deliverables import templates as T
from tests.conftest import as_user

DIRECTOR = as_user("sarah")   # automation.use + companion.admin


def _answers(key: str, **fields) -> dict:
    """Map field names to the template's actual question text (how the client keys answers)."""
    t = T.BUILTINS[key]
    return {t["questions"][t["map"][k]]["question"]: v for k, v in fields.items()}


def test_lists_templates(client):
    r = client.get("/api/deliverables/templates", headers=DIRECTOR)
    assert r.status_code == 200, r.text
    keys = {t["key"] for t in r.json()["templates"]}
    assert {"copil", "matrice_couverture"} <= keys
    copil = next(t for t in r.json()["templates"] if t["key"] == "copil")
    assert copil["output"] == "pptx" and len(copil["questions"]) >= 4


def test_generates_copil_pptx(client):
    from pptx import Presentation

    answers = _answers("copil", projet="Refonte SI", client="ACME", date="2026-09-30",
                       faits_realise="Atelier de cadrage tenu\nRecette V1 livrée", faits_alerte="Retard sur l'API paiement",
                       plan="Valider le budget | Sarah | 2026-10-05", perspectives="Démarrer le lot 2")
    r = client.post("/api/deliverables/copil/generate", json={"answers": answers}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    assert "presentationml" in r.headers["content-type"]
    prs = Presentation(io.BytesIO(r.content))
    texts = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame)
    assert "COPIL Refonte SI" in texts and "Faits marquants" in texts
    assert "Atelier de cadrage tenu" in texts and "Retard sur l'API paiement" in texts


def test_generates_matrice_xlsx_with_criticite_and_coverage(client):
    from openpyxl import load_workbook

    answers = _answers("matrice_couverture", projet="Portail Client", client="ACME",
                       exigences="Login | Grave | Occasionnel\nExport PDF | Modéré | Rare",
                       cas="Connexion\nTéléchargement", couverture="Connexion : Login")
    r = client.post("/api/deliverables/matrice_couverture/generate", json={"answers": answers}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert {"Matrice", "Criticité"} <= set(wb.sheetnames)
    ws = wb["Matrice"]
    assert ws.cell(1, 2).value == "Login"
    assert ws.cell(4, 2).value == 2          # Grave × Occasionnel → criticité 2 (per the grid)
    assert ws.cell(4, 3).value == 1          # Modéré × Rare → 1
    assert ws.cell(5, 1).value == "Connexion" and ws.cell(5, 2).value == "X"   # coverage placed
    grid = wb["Criticité"]
    assert grid.cell(2, 2).value == 3        # Catastrophique × Inévitable → 3


def test_samples_are_listed_and_downloadable(client):
    r = client.get("/api/deliverables/matrice_couverture/samples", headers=DIRECTOR)
    assert r.status_code == 200, r.text
    sets = {s["set"]: s for s in r.json()["sets"]}
    assert {"portail_client", "gestion_conges"} <= set(sets)
    names = {f["name"] for f in sets["portail_client"]["files"]}
    assert "cahier_des_charges_portail_client.md" in names and "jira_tickets.json" in names
    dl = client.get("/api/deliverables/matrice_couverture/samples/portail_client/jira_tickets.json", headers=DIRECTOR)
    assert dl.status_code == 200 and b"PC-101" in dl.content
    # path traversal is refused
    assert client.get("/api/deliverables/matrice_couverture/samples/portail_client/..%2F..%2Ftemplates.py", headers=DIRECTOR).status_code == 404


def test_prepare_from_samples_extracts_asks_only_when_needed_then_generates(client):
    from openpyxl import load_workbook

    # Rich set: every requirement has impact+probabilité in the cahier → no criticité question; but Jira has
    # a Bug → the ONE question that changes the result is asked.
    r = client.post("/api/deliverables/matrice_couverture/prepare", json={"samples": "portail_client"}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    out = r.json()
    draft = out["draft"]
    refs = {e["ref"] for e in draft["exigences"]}
    assert {"EXF-01", "EXF-10", "EXNF-04"} <= refs and len(refs) >= 14
    ex01 = next(e for e in draft["exigences"] if e["ref"] == "EXF-01")
    assert ex01["impact"] == "Catastrophique" and ex01["probabilite"] == "Inévitable"
    assert out["status"] == "questions"
    qs = [q["question"] for q in out["questions"]]
    assert any("Bug" in q for q in qs) and not any("impact par défaut" in q for q in qs)
    # use cases come from the specs AND the tickets, de-duplicated, with coverage refs
    names = {c["name"] for c in draft["cas_usage"]}
    assert "Connexion de l'assuré" in names and "Déclarer un sinistre" in names and "Notifications email et push" in names
    connexion = next(c for c in draft["cas_usage"] if c["name"] == "Connexion de l'assuré")
    assert "EXF-01" in connexion["exigences"]

    # answer the question → ready, bugs excluded
    bug_q = next(q["question"] for q in out["questions"] if "Bug" in q["question"])
    r2 = client.post("/api/deliverables/matrice_couverture/prepare",
                     json={"samples": "portail_client", "answers": {bug_q: "Non, uniquement les stories et tâches"}}, headers=DIRECTOR)
    assert r2.json()["status"] == "ready", r2.text
    draft2 = r2.json()["draft"]
    assert not any(c["kind"].lower() == "bug" for c in draft2["cas_usage"])

    # generate from the draft → real workbook with refs in headers, correct criticité and coverage marks
    g = client.post("/api/deliverables/matrice_couverture/generate", json={"draft": draft2, "projet": "Portail Client"}, headers=DIRECTOR)
    assert g.status_code == 200, g.text
    wb = load_workbook(io.BytesIO(g.content))
    ws = wb["Matrice"]
    headers = [ws.cell(1, j).value for j in range(2, 2 + len(draft2["exigences"]))]
    col_ex01 = 2 + headers.index(next(h for h in headers if h.startswith("EXF-01")))
    assert ws.cell(4, col_ex01).value == 3                       # Catastrophique × Inévitable
    col_ex10 = 2 + headers.index(next(h for h in headers if h.startswith("EXF-10")))
    assert ws.cell(4, col_ex10).value == 1                       # Ennuyeux × Rare
    rows = {ws.cell(i, 1).value: i for i in range(5, 5 + len(draft2["cas_usage"]))}
    row_connexion = next(i for name, i in rows.items() if name and "Connexion de l'assuré" in name)
    assert ws.cell(row_connexion, col_ex01).value == "X"


def test_prepare_lean_set_asks_for_default_criticite(client):
    r = client.post("/api/deliverables/matrice_couverture/prepare", json={"samples": "gestion_conges"}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "questions"
    qs = [q for q in out["questions"]]
    texts = [q["question"] for q in qs]
    assert any("impact par défaut" in t for t in texts) and any("probabilité par défaut" in t for t in texts)
    impact_q = next(q for q in qs if "impact" in q["question"])
    assert impact_q["options"] == ["Catastrophique", "Grave", "Modéré", "Ennuyeux"]   # clickable, from the scale
    answers = {q["question"]: (q["options"][1] if q["options"] else "ok") for q in qs}
    r2 = client.post("/api/deliverables/matrice_couverture/prepare", json={"samples": "gestion_conges", "answers": answers}, headers=DIRECTOR)
    assert r2.json()["status"] == "ready"
    assert all(e["impact"] and e["probabilite"] for e in r2.json()["draft"]["exigences"])


def test_prepare_from_uploaded_files_uses_the_same_extractor(client):
    from app.deliverables import extract as X

    cahier = X.sample_file("portail_client", "cahier_des_charges_portail_client.md").read_bytes()
    spec = X.sample_file("portail_client", "spec_sinistres_et_paiement.md").read_bytes()
    up1 = client.post("/api/automations/files", files={"file": ("cahier.md", cahier)}, headers=DIRECTOR).json()
    up2 = client.post("/api/automations/files", files={"file": ("spec.md", spec)}, headers=DIRECTOR).json()
    r = client.post("/api/deliverables/matrice_couverture/prepare",
                    json={"inputs": {"cahier_des_charges": up1["file_id"], "specs": [up2["file_id"]], "jira": {"demo": True}}}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert any(e["ref"] == "EXF-08" for e in draft["exigences"])
    assert any(c["name"] == "Payer une cotisation en ligne" for c in draft["cas_usage"])
    assert set(r.json()["sources"]) >= {"cahier.md", "spec.md", "tickets"}
    # a required input missing is a clear 400, not a crash
    bad = client.post("/api/deliverables/matrice_couverture/prepare", json={"inputs": {"specs": [up2["file_id"]]}}, headers=DIRECTOR)
    assert bad.status_code == 400 and "Cahier des charges" in bad.json()["detail"]


def test_per_company_override_changes_title_and_questions(client):
    r = client.put("/api/deliverables/copil/template",
                   json={"overrides": {"title": "Support de pilotage ACME"}}, headers=DIRECTOR)
    assert r.status_code == 200, r.text
    listed = client.get("/api/deliverables/templates", headers=DIRECTOR).json()["templates"]
    assert any(t["key"] == "copil" and t["title"] == "Support de pilotage ACME" for t in listed)


def test_criticite_grid_lookup_is_accent_and_case_insensitive():
    assert T.criticite("grave", "occasionnel") == 2
    assert T.criticite("Catastrophique", "Inévitable") == 3
    assert T.criticite("ennuyeux", "rare") == 1
    assert T.criticite("inconnu", "inconnu") in (1, 2, 3)   # falls back, never crashes
