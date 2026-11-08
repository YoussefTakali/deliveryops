"""User-defined livrables: a user describes a deliverable in their own words + gives style examples,
the AI turns it into a spec and asks what's unclear (always with ONE recommended option), then any
run reads the inputs, asks what's missing, drafts the content in that style and renders a real file."""

from tests.conftest import as_user

SARAH = as_user("sarah")
TOM = as_user("tom")
ATLAS = "per-atlas-sinistres"

EXAMPLE = b"""# Faits marquants
- Mise en production du lot 3 le 12/09.
# Risques et alertes
- Retard fournisseur sur l'API paiement.
# Prochaines etapes
- Recette du lot 4.
"""
SOURCE = b"""Compte-rendu du point hebdo. Faits marquants : le lot 3 est en production depuis lundi, aucun incident P1.

Risques : le fournisseur de l'API paiement annonce deux semaines de retard, impact possible sur le lot 4.

Prochaines etapes : lancer la recette du lot 4 et relancer le fournisseur mardi.
"""


def _upload(client, name, data, headers=SARAH):
    r = client.post("/api/automations/files", files={"file": (name, data)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def test_design_asks_unclear_points_with_one_recommended_option(client):
    r = client.post("/api/deliverables/custom/design", json={"description": "Un bilan à partir de nos notes"}, headers=SARAH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "questions" and out["questions"]
    for q in out["questions"]:
        assert q["recommended"] and q["recommended"] in q["options"] and q["options"][0] == q["recommended"]
    # answering with the recommended choices completes the design
    answers = {q["question"]: q["recommended"] for q in out["questions"]}
    done = client.post("/api/deliverables/custom/design", json={"description": "Un bilan à partir de nos notes", "answers": answers},
                       headers=SARAH).json()
    assert done["status"] == "ready" and done["spec"]["output"] in ("docx", "pptx", "xlsx", "md")
    assert "Précisions du concepteur" in done["spec"]["instructions"]        # the answers shape the spec


def test_full_custom_livrable_learned_from_an_example(client):
    ex = _upload(client, "exemple_bilan.md", EXAMPLE)
    desc = "Rapport hebdo Word synthétique pour le client, à partir des comptes-rendus de la semaine"
    d = client.post("/api/deliverables/custom/design", json={"description": desc, "example_file_ids": [ex]}, headers=SARAH).json()
    assert d["status"] == "ready", d["questions"]                         # format, audience, length already stated
    spec = d["spec"]
    assert [s["title"] for s in spec["sections"]] == ["Faits marquants", "Risques et alertes", "Prochaines etapes"]   # learned
    assert spec["output"] == "docx" and spec["examples"]

    saved = client.post("/api/deliverables/custom", json={**spec, "title": "Bilan hebdo client"}, headers=SARAH).json()
    cid = saved["custom_id"]
    listed = client.get("/api/deliverables/templates", headers=SARAH).json()["templates"]
    assert any(t["key"] == f"custom:{cid}" and t["custom"] for t in listed)
    assert any(t["key"] == "copil" and not t["custom"] for t in listed)     # built-ins stay in the same list

    # a required input missing → a clear 400
    assert client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {}}, headers=SARAH).status_code == 400
    src = _upload(client, "cr_semaine.txt", SOURCE)
    key = spec["inputs"][0]["key"]
    # the project runs this catalogue livrable per périmètre (custom livrables default to project level)
    client.put("/api/projects/prj-atlas/livrables/scope", json={"key": f"custom:{cid}", "level": "perimeter"}, headers=SARAH)
    first = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {key: [src]}, "perimeter_id": ATLAS}, headers=SARAH).json()
    assert first["status"] == "questions" and first["questions"][0]["recommended"]
    answers = {q["question"]: q["recommended"] for q in first["questions"]}
    ready = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {key: [src]}, "answers": answers, "perimeter_id": ATLAS},
                        headers=SARAH).json()
    assert ready["status"] == "ready"
    draft = ready["draft"]
    assert [b["heading"] for b in draft["sections"]] == ["Faits marquants", "Risques et alertes", "Prochaines etapes"]
    assert any("fournisseur" in " ".join(b["bullets"]).lower() for b in draft["sections"])   # grounded in the source

    g = client.post(f"/api/deliverables/custom/{cid}/generate", json={"draft": draft, "perimeter_id": ATLAS}, headers=SARAH)
    assert g.status_code == 200 and g.content[:2] == b"PK" and g.headers.get("X-Deliverable-Id")
    history = client.get(f"/api/perimeters/{ATLAS}/deliverables", headers=SARAH).json()["deliverables"]
    assert any(h["key"] == f"custom:{cid}" for h in history)             # stored on the périmètre (and indexed)

    # the same content renders in every supported format
    for output, magic in (("pptx", b"PK"), ("xlsx", b"PK"), ("md", b"# ")):
        client.put(f"/api/deliverables/custom/{cid}", json={**spec, "title": "Bilan hebdo client", "output": output}, headers=SARAH)
        r = client.post(f"/api/deliverables/custom/{cid}/generate", json={"draft": draft}, headers=SARAH)
        assert r.status_code == 200 and r.content[:2] == magic, output


def test_livrables_belong_to_projects_with_a_company_catalogue(client):
    """Livrables belong to a PROJECT (its selection from the company catalogue + its own livrables);
    the catalogue is managed by `deliverables.catalog`; a project's livrable never leaks to another."""
    KARIM, LEA = as_user("karim"), as_user("lea")        # delivery managers: Atlas only / Horizon only
    HORIZON = "per-horizon-paiements"
    spec = {"title": "Note de risques", "sections": [{"title": "Risques"}], "inputs": [{"key": "notes", "label": "Notes", "kind": "text"}]}

    # the company catalogue is not for everyone
    assert client.post("/api/deliverables/custom", json=spec, headers=TOM).status_code == 403
    assert client.post("/api/deliverables/custom", json=spec, headers=KARIM).status_code == 403
    cat = client.post("/api/deliverables/custom", json={**spec, "title": "Bilan catalogue"}, headers=SARAH).json()
    assert cat["scope"] == "catalog"

    # a project's manager creates a project-only livrable — only on a project they can see
    own = client.post("/api/deliverables/custom", json={**spec, "project_id": "prj-atlas"}, headers=KARIM)
    assert own.status_code == 200 and own.json()["scope"] == "project"
    own_key = own.json()["key"]
    assert client.post("/api/deliverables/custom", json={**spec, "project_id": "prj-horizon"}, headers=KARIM).status_code == 404
    assert client.post("/api/deliverables/custom", json={**spec, "project_id": "prj-atlas"}, headers=TOM).status_code == 403

    atlas = {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": ATLAS}, headers=KARIM).json()["templates"]}
    assert own_key not in atlas                                            # a new project livrable is project-level by default
    client.put("/api/projects/prj-atlas/livrables/scope", json={"key": own_key, "level": "perimeter"}, headers=KARIM)
    atlas = {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": ATLAS}, headers=KARIM).json()["templates"]}
    horizon = {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": HORIZON}, headers=LEA).json()["templates"]}
    assert own_key in atlas and own_key not in horizon                     # it belongs to Atlas's project
    assert client.get(f"/api/deliverables/custom/{own.json()['custom_id']}", headers=LEA).status_code == 404
    r = client.post(f"/api/deliverables/custom/{own.json()['custom_id']}/prepare", json={"inputs": {"notes": "x"}, "perimeter_id": HORIZON}, headers=SARAH)
    assert r.status_code == 400 and "autre projet" in r.json()["detail"]

    # each project picks its catalogue livrables; the project's own ones always stay available
    pl = client.get("/api/projects/prj-atlas/livrables", headers=KARIM).json()
    assert pl["can_edit"] and {c["key"] for c in pl["catalog"] if c["enabled"]} >= {"copil", "matrice_couverture"}   # default: built-ins
    assert client.put("/api/projects/prj-atlas/livrables", json={"keys": ["copil", cat["key"]]}, headers=TOM).status_code == 403
    assert client.put("/api/projects/prj-atlas/livrables", json={"keys": ["nope"]}, headers=KARIM).status_code == 400
    client.put("/api/projects/prj-atlas/livrables", json={"keys": ["copil", cat["key"]]}, headers=KARIM)
    atlas = {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": ATLAS}, headers=KARIM).json()["templates"]}
    assert atlas == {"copil", own_key}                                      # matrice deselected; the catalogue item is project-level
    other = {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": HORIZON}, headers=LEA).json()["templates"]}
    assert "matrice_couverture" in other and cat["key"] not in other         # Horizon keeps its own (default) set

    # only the project's deciders edit its livrable; only catalogue managers edit the catalogue
    assert client.put(f"/api/deliverables/custom/{cat['custom_id']}", json=spec, headers=KARIM).status_code == 403
    assert client.put(f"/api/deliverables/custom/{own.json()['custom_id']}", json={**spec, "title": "Note v2"}, headers=KARIM).status_code == 200
    # a good project livrable can be promoted to the company catalogue (a copy)
    assert client.post(f"/api/deliverables/custom/{own.json()['custom_id']}/promote", headers=KARIM).status_code == 403
    promoted = client.post(f"/api/deliverables/custom/{own.json()['custom_id']}/promote", headers=SARAH).json()
    assert promoted["scope"] == "catalog" and promoted["title"] == "Note v2"
    assert any(c["key"] == promoted["key"] for c in client.get("/api/deliverables/catalog", headers=TOM).json()["custom"])
    client.put("/api/projects/prj-atlas/livrables", json={"keys": ["copil", "matrice_couverture"]}, headers=KARIM)   # restore


def test_every_builtin_question_now_has_a_recommended_option(client):
    out = client.post("/api/deliverables/matrice_couverture/prepare", json={"samples": "gestion_conges"}, headers=SARAH).json()
    assert out["questions"] and all(q["recommended"] in q["options"] for q in out["questions"])


def test_livrable_level_project_or_selected_perimeters(client):
    """A project's livrable is produced either once for the whole project (fed by all its périmètres) or per
    périmètre for the périmètres the project picks. Built-ins keep their natural level."""
    KARIM = as_user("karim")
    spec = {"title": "Bilan projet", "sections": [{"title": "Synthèse"}], "inputs": [{"key": "notes", "label": "Notes", "kind": "text", "required": True}],
            "project_id": "prj-atlas"}
    own = client.post("/api/deliverables/custom", json=spec, headers=KARIM).json()
    key, cid = own["key"], own["custom_id"]
    item = next(i for i in client.get("/api/projects/prj-atlas/livrables", headers=KARIM).json()["project_items"] if i["key"] == key)
    assert item["level"] == "project" and not item["level_locked"]

    # built-ins: the plan de charge is always project-level, COPIL always per périmètre
    pl = client.get("/api/projects/prj-atlas/livrables", headers=KARIM).json()
    assert {c["key"]: c["level"] for c in pl["catalog"] if not c["custom"]} == {"copil": "perimeter", "matrice_couverture": "perimeter", "plan_de_charge": "project"}
    assert client.put("/api/projects/prj-atlas/livrables/scope", json={"key": "plan_de_charge", "level": "perimeter"}, headers=KARIM).status_code == 400
    assert client.put("/api/projects/prj-atlas/livrables/scope", json={"key": key, "level": "perimeter"}, headers=TOM).status_code == 403

    # project-level run: not from a périmètre, from the project → one ProjectDeliverable
    r = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {"notes": "lot 3 livré"}, "perimeter_id": ATLAS}, headers=KARIM)
    assert r.status_code == 400 and "niveau du projet" in r.json()["detail"]
    r = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {"notes": "lot 3 livré"}, "project_id": "prj-atlas"}, headers=KARIM)
    assert r.status_code == 200, r.text
    draft = r.json()["draft"] or {"title": "Bilan projet", "sections": [{"heading": "Synthèse", "paragraphs": ["Lot 3 livré."]}]}
    g = client.post(f"/api/deliverables/custom/{cid}/generate", json={"draft": draft, "project_id": "prj-atlas"}, headers=KARIM)
    assert g.status_code == 200, g.text
    docs = client.get("/api/projects/prj-atlas/deliverables", headers=KARIM).json()["deliverables"]
    assert any(d["key"] == key and d["id"] == g.headers["X-Deliverable-Id"] for d in docs)

    # per-périmètre, restricted to chosen périmètres
    assert client.put("/api/projects/prj-atlas/livrables/scope", json={"key": key, "level": "perimeter", "perimeter_ids": ["per-horizon-paiements"]},
                      headers=KARIM).status_code == 400                         # not a périmètre of this project
    ok = client.put("/api/projects/prj-atlas/livrables/scope", json={"key": key, "level": "perimeter", "perimeter_ids": [ATLAS]}, headers=KARIM)
    assert ok.status_code == 200
    assert next(i for i in ok.json()["project_items"] if i["key"] == key)["perimeter_ids"] == [ATLAS]
    assert key in {t["key"] for t in client.get("/api/deliverables/templates", params={"perimeter_id": ATLAS}, headers=KARIM).json()["templates"]}
    r = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {"notes": "x"}, "project_id": "prj-atlas"}, headers=KARIM)
    assert r.status_code == 400 and "par périmètre" in r.json()["detail"]


def test_office_and_binary_inputs(client):
    """A PowerPoint input is read as text (it used to be stored as raw zip bytes → 500 on PostgreSQL);
    an unreadable binary gets a clear 400, never a server error."""
    import io
    from pptx import Presentation
    deck = Presentation(); slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Stratégie de test"; slide.placeholders[1].text = "Recette du lot 4 en octobre"
    buf = io.BytesIO(); deck.save(buf)
    from app import knowledge
    text = knowledge.extract_text("strategie.pptx", buf.getvalue())
    assert "Stratégie de test" in text and "Recette du lot 4" in text

    spec = {"title": "Synthèse recette", "sections": [{"title": "Synthèse"}], "inputs": [{"key": "doc", "label": "Document", "kind": "file", "required": True}]}
    cid = client.post("/api/deliverables/custom", json=spec, headers=SARAH).json()["custom_id"]
    client.put("/api/projects/prj-atlas/livrables", json={"keys": ["copil", f"custom:{cid}"]}, headers=SARAH)
    ok = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {"doc": [_upload(client, "strategie.pptx", buf.getvalue())]}, "project_id": "prj-atlas"}, headers=SARAH)
    assert ok.status_code == 200, ok.text
    bad = client.post(f"/api/deliverables/custom/{cid}/prepare", json={"inputs": {"doc": [_upload(client, "image.bin", b"\x89PNG\x00\x00\x01binary")]}, "project_id": "prj-atlas"}, headers=SARAH)
    assert bad.status_code == 400 and "non pris en charge" in bad.json()["detail"]
