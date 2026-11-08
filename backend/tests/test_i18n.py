"""Interface translation: machine-translated with the platform LLM, cached org-wide, governed."""

import pytest

from app import governance, llm, telemetry
from app.config import Settings
from app.db import SessionLocal
from app.models import UiTranslation
from app.routes import i18n
from tests.conftest import as_user

ADMIN = as_user("sarah")
TOM = as_user("tom")


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM on, translating by upper-casing (placeholders kept); records every batch it receives."""
    calls = []

    def fake_structured(**kw):
        texts = [line.split(". ", 1)[1] for line in kw["context"].splitlines()]
        calls.append({"texts": texts, "agent": telemetry.current().get("agent")})
        return kw["schema"](translations=[t.upper() for t in texts]), True

    monkeypatch.setattr(Settings, "use_llm", property(lambda self: True))
    monkeypatch.setattr(llm, "structured", fake_structured)
    yield calls
    with SessionLocal() as db:
        db.query(UiTranslation).delete()
        db.commit()


def _post(client, texts, lang="de", headers=ADMIN):
    return client.post("/api/i18n/translate", json={"lang": lang, "language_name": "German", "texts": texts}, headers=headers)


def test_french_is_the_source_and_needs_no_ai(client):
    r = _post(client, ["Mon compte"], lang="fr").json()
    assert r["translations"] == {"Mon compte": "Mon compte"} and r["available"] is True


def test_offline_ai_keeps_the_interface_in_french(client):
    r = _post(client, ["Mon compte"]).json()          # tests run with LLM_MODE=mock
    assert r["available"] is False and r["translations"] == {} and "hors ligne" in r["reason"]


def test_strings_are_translated_once_then_served_from_the_cache(client, fake_llm):
    r = _post(client, ["Mon compte", "Sur {0} jours", "Mon compte"]).json()
    assert r["available"] and r["translations"] == {"Mon compte": "MON COMPTE", "Sur {0} jours": "SUR {0} JOURS"}
    assert len(fake_llm) == 1 and fake_llm[0]["texts"] == ["Mon compte", "Sur {0} jours"]   # deduplicated
    assert fake_llm[0]["agent"] == "ui_translation"                                         # governed & attributed
    again = _post(client, ["Mon compte", "Sur {0} jours"], headers=TOM).json()               # another user
    assert again["translations"]["Mon compte"] == "MON COMPTE" and len(fake_llm) == 1         # no second LLM call


def test_translations_that_break_placeholders_are_dropped(client, monkeypatch, fake_llm):
    monkeypatch.setattr(llm, "structured", lambda **kw: (kw["schema"](translations=["Über 7 Tage"]), True))
    r = _post(client, ["Sur {0} jours"]).json()
    assert r["translations"] == {}
    with SessionLocal() as db:
        assert db.query(UiTranslation).count() == 0


def test_blocked_ai_or_bad_input_is_refused_cleanly(client, fake_llm):
    assert _post(client, ["x"], lang="not a language!").status_code == 422
    client.put("/api/governance/users/tom", json={"ai_blocked": True}, headers=ADMIN)
    governance.invalidate()
    try:
        r = _post(client, ["Déconnexion"], headers=TOM).json()
        assert r["available"] is False and "suspendu" in r["reason"] and not fake_llm
    finally:
        client.put("/api/governance/users/tom", json={"ai_blocked": False}, headers=ADMIN)
        governance.invalidate()


def test_regional_codes_are_normalised(client):
    assert i18n.TranslateIn(lang="PT-BR", texts=["a"]).lang == "pt-BR"
    assert i18n.TranslateIn(lang="zh-Hant", texts=["a"]).lang == "zh-Hant"
