"""Deterministic faithful replay: a recording's real events become concrete, ordered steps that
carry the captured ground truth — no LLM, identical every time."""

from types import SimpleNamespace

from app.automations import recording
from app.automations.validate import validate


def _rec(events):
    # build_faithful only reads these attributes; a stand-in avoids needing a DB session.
    return SimpleNamespace(owner="u", duration_s=20.0, events=events, narration=[], keyframes=[])


def test_click_becomes_vision_step_with_ground_truth():
    rec = _rec([
        {"t": 1.0, "type": "click", "x": 300, "y": 120, "button": "left", "clicks": 1, "window_title": "Spotify",
         "element": {"name": "Rechercher", "control_type": "Edit", "automation_id": "search-box"}, "patch": "iVBORw0KGgo="},
        {"t": 2.0, "type": "typing", "chars": 13, "deletions": 0, "window_title": "Spotify"},
        {"t": 3.0, "type": "key", "key": "Entrée", "window_title": "Spotify"},
    ])
    wf = recording.build_faithful(rec)
    types = [s["type"] for s in wf["steps"]]
    assert types == ["vision.click", "vision.type", "desktop.keys"]
    click = wf["steps"][0]
    gt = click["params"]["ground_truth"]
    assert gt["element"] == {"name": "Rechercher", "control_type": "Edit", "automation_id": "search-box"}
    assert gt["patch"] == "iVBORw0KGgo=" and gt["point"] == {"x": 300, "y": 120}
    assert "Rechercher" in click["params"]["target"]
    # typed content is never recorded -> becomes a text input the user fills, and a question
    assert wf["steps"][1]["params"]["text"] == "{{inputs.saisie_1}}"
    assert "saisie_1" in wf["inputs"] and wf["questions"]
    # Enter -> SendKeys
    assert wf["steps"][2]["params"]["keys"] == "{ENTER}"
    # it must pass validation (no blocking errors)
    assert validate(wf["trigger"], wf["steps"], wf["inputs"])["errors"] == []


def test_shortcuts_map_and_unreproducible_ones_warn():
    rec = _rec([
        {"t": 1.0, "type": "shortcut", "keys": "Ctrl+S", "window_title": "Word"},
        {"t": 2.0, "type": "shortcut", "keys": "Ctrl+Maj+Échap", "window_title": "Word"},
        {"t": 3.0, "type": "shortcut", "keys": "Win+R", "window_title": "Bureau"},
    ])
    wf = recording.build_faithful(rec)
    keys = [s["params"]["keys"] for s in wf["steps"] if s["type"] == "desktop.keys"]
    assert "^s" in keys and "^+{ESC}" in keys
    assert not any("Win" in k for k in keys)  # Windows key can't be sent
    assert any("Win+R" in w for w in wf["warnings"])


def test_window_change_inserts_a_wait_and_order_is_preserved():
    rec = _rec([
        {"t": 1.0, "type": "click", "x": 10, "y": 10, "button": "left", "window_title": "Outlook", "element": {"name": "Nouveau", "control_type": "Button"}},
        {"t": 2.0, "type": "click", "x": 20, "y": 20, "button": "left", "window_title": "Message", "element": {"name": "À", "control_type": "Edit"}},
    ])
    wf = recording.build_faithful(rec)
    assert [s["type"] for s in wf["steps"]] == ["vision.click", "flow.wait", "vision.click"]
    assert [s["id"] for s in wf["steps"]] == ["s1", "s2", "s3"]  # ids are sequential and unique


def test_double_and_right_click_flags():
    rec = _rec([
        {"t": 1.0, "type": "click", "x": 5, "y": 5, "button": "left", "clicks": 2, "window_title": "Explorateur", "element": {"name": "Dossier", "control_type": "ListItem"}},
        {"t": 2.0, "type": "click", "x": 9, "y": 9, "button": "right", "clicks": 1, "window_title": "Explorateur", "element": {"name": "Fichier", "control_type": "ListItem"}},
    ])
    wf = recording.build_faithful(rec)
    assert wf["steps"][0]["params"].get("double") is True
    assert wf["steps"][1]["params"].get("right") is True
