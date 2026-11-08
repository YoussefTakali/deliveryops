"""Vision brain: choosing what to act on from what the desktop app perceived on screen."""

from app import llm
from app.automations import catalog, vision
from app.automations.validate import validate
from tests.conftest import as_user

USER = as_user("sarah")
SCENE = [
    {"i": 0, "label": "Nouvelle note", "control_type": "Button", "source": "uia", "x": 40, "y": 70, "w": 150, "h": 40},
    {"i": 1, "label": "", "control_type": "Button", "source": "uia", "x": 395, "y": 70, "w": 40, "h": 40},  # icon-only
    {"i": 2, "label": "Enregistrer brouillon", "control_type": "Text", "source": "ocr", "x": 1050, "y": 640, "w": 180, "h": 42},
    {"i": 3, "label": "Soumettre", "control_type": "Text", "source": "ocr", "x": 860, "y": 640, "w": 170, "h": 42},
]


def test_vision_steps_are_in_the_catalog_and_validate():
    assert {"vision.click", "vision.type", "vision.wait", "vision.read"} <= set(catalog.BY_TYPE)
    assert catalog.BY_TYPE["vision.click"]["executor"] == "local" and catalog.BY_TYPE["vision.click"]["side_effect"]
    steps = [
        {"id": "s1", "type": "vision.click", "title": "Valider", "params": {"target": "le bouton Soumettre", "window": "Notes de frais"}, "on_error": "stop"},
        {"id": "s2", "type": "vision.wait", "title": "Attendre", "params": {"target": "le message Envoyé"}, "on_error": "stop"},
        {"id": "s3", "type": "vision.click", "title": "Sans cible", "params": {}, "on_error": "stop"},
    ]
    result = validate({"type": "manual"}, steps, {})
    assert [e["step_id"] for e in result["errors"]] == ["s3"]  # only the step without its required target


def test_decide_offline_matches_labels_and_refuses_to_guess(client):
    body = {"goal": "le bouton Soumettre", "action": "click", "window": "Notes de frais", "candidates": SCENE, "icons": []}
    r = client.post("/api/automations/vision/decide", json=body, headers=USER).json()
    assert r["index"] == 3 and r["confidence"] >= 0.5
    # nothing on screen resembles the goal -> -1, never a random element
    r = client.post("/api/automations/vision/decide", json={**body, "goal": "exporter en PDF"}, headers=USER).json()
    assert r["index"] == -1
    # an empty scene short-circuits (no LLM quota spent)
    assert client.post("/api/automations/vision/decide", json={**body, "candidates": []}, headers=USER).json()["index"] == -1


def test_decide_uses_the_llm_choice_and_rejects_an_index_that_is_not_on_screen(monkeypatch):
    monkeypatch.setattr(vision.settings, "llm_mode", "api")
    monkeypatch.setattr(vision, "caption_icon", lambda b64: "Print")
    seen = {}

    def fake_structured(**kw):
        seen["context"] = kw["context"]
        return vision.Decision(index=seen.get("answer", 1), confidence=0.9, reason="pictogramme d'impression"), True

    monkeypatch.setattr(llm, "structured", fake_structured)
    out = vision.decide(goal="imprimer la liste", action="click", window="Notes de frais", candidates=SCENE, icons=[{"i": 1, "png_b64": "aGk="}])
    assert out["index"] == 1 and "[icône : Print]" in seen["context"]  # the icon got a label the text model could reason on
    assert "@(415,90)" in seen["context"]  # centre of the icon button

    seen["answer"] = 42  # hallucinated element
    assert vision.decide(goal="imprimer", action="click", window="", candidates=SCENE, icons=[])["index"] == -1
