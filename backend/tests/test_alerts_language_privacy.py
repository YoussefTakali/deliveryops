"""Budget alerts (80 % / 100 % of a quota or agent cap), AI answers in the interface language, the
companion's "Mes données" summary, and e-mail validation that accepts internal company domains."""

import time

import pytest

from app import governance, llm, telemetry
from app.db import SessionLocal
from app.models import Notification, UsageLedger
from app.validators import _email
from tests.conftest import as_user

ADMIN = as_user("sarah")   # governance.manage
KARIM = as_user("karim")   # no governance.manage
TOM = as_user("tom")


@pytest.fixture
def clean_budget(client):
    def reset():
        with SessionLocal() as db:
            db.query(UsageLedger).filter(UsageLedger.purpose == "alert-test").delete()
            db.query(Notification).filter(Notification.kind == "ai_budget").delete()
            db.commit()
        client.put("/api/governance/policy", json={"default_daily_tokens": 0, "agent_caps": {"copilot": 0}}, headers=ADMIN)
        governance._alerted.clear()
        governance.invalidate()
    reset()
    yield
    reset()


def _spend(username: str, agent: str, tokens: int) -> None:
    with SessionLocal() as db:
        db.add(UsageLedger(username=username, agent=agent, purpose="alert-test", tokens=tokens))
        db.commit()
    governance.invalidate()


def _budget_titles(client, headers) -> list[str]:
    for _ in range(40):  # alerts are written by a background thread
        items = [n["title"] for n in client.get("/api/notifications", headers=headers).json()["items"] if n["kind"] == "ai_budget"]
        if items:
            return items
        time.sleep(0.05)
    return []


def test_user_quota_alert_reaches_admins_and_the_person_once(client, clean_budget):
    client.put("/api/governance/policy", json={"default_daily_tokens": 1000}, headers=ADMIN)
    _spend("tom", "copilot", 850)
    assert governance.check("tom", "copilot") is None                  # 85 %: still allowed, but alerted
    admin_view = _budget_titles(client, ADMIN)
    assert any("tom" in t and "80 %" in t for t in admin_view), admin_view
    assert any("80 %" in t for t in _budget_titles(client, TOM))      # the person is told too
    assert not [n for n in client.get("/api/notifications", headers=KARIM).json()["items"] if n["kind"] == "ai_budget"]
    governance.check("tom", "copilot")                                 # same threshold, same day: no duplicate
    time.sleep(0.2)
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.kind == "ai_budget").count() == 2
    _spend("tom", "copilot", 200)                                      # 105 %: refused + the 100 % alert
    assert "quota" in governance.check("tom", "copilot")
    time.sleep(0.3)
    assert any("atteint" in t for t in _budget_titles(client, ADMIN))


def test_agent_cap_alert_goes_to_governance_admins_only(client, clean_budget):
    client.put("/api/governance/policy", json={"agent_caps": {"copilot": 1000}}, headers=ADMIN)
    _spend("karim", "copilot", 900)
    governance.check("karim", "copilot")
    assert any("Copilot" in t for t in _budget_titles(client, ADMIN))
    time.sleep(0.2)
    assert not [n for n in client.get("/api/notifications", headers=KARIM).json()["items"] if n["kind"] == "ai_budget"]


def test_chat_answers_follow_the_interface_language_documents_stay_french():
    telemetry.start_request()
    telemetry.bind_language("de", "German")
    msgs = [{"role": "system", "content": "Tu es le copilote."}, {"role": "user", "content": "Bonjour"}]
    chat = llm._localized("copilot_turn", msgs)
    assert "German (de)" in chat[0]["content"] and chat[1] == msgs[1]
    assert llm._localized("GeneratedDoc", msgs) == msgs               # a deliverable stays in French
    telemetry.bind_language("fr", "French")
    assert llm._localized("copilot_turn", msgs) == msgs
    telemetry.bind_language("not a code!", "x")                       # garbage headers are ignored
    assert telemetry.ui_language()[0] == "fr"


def test_language_header_reaches_the_request_context(client):
    r = client.get("/api/me", headers={**ADMIN, "X-UI-Language": "ar", "X-UI-Language-Name": "Arabic"})
    assert r.status_code == 200


def test_companion_data_summary_counts_without_content(client):
    r = client.get("/api/companion/me/summary", headers=ADMIN)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data["counts"]) == {"activity", "activity_hours", "messages", "memories", "suggestions"}
    assert "retention_days" in data["tracking"] and "enabled" in data["tracking"]


@pytest.mark.parametrize("value", ["admin@deliveryops.local", "Eve@Corp.LAN", "a.b+c@sub.example.com"])
def test_internal_company_domains_are_valid_emails(value):
    assert _email(value) == value.strip().lower()


@pytest.mark.parametrize("value", ["", "no-at-sign", "two@@signs.com", "spaces in@x.com", "nodot@localhost"])
def test_malformed_emails_are_rejected(value):
    with pytest.raises(ValueError):
        _email(value)
