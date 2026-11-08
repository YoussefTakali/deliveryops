"""Admin-managed LLM endpoints & API keys (per agent, from the UI) and the self-service account page."""

import json

import pytest

from app import llm, llm_config, telemetry
from app.db import SessionLocal
from app.models import AuditLog, LLMProviderConfig
from tests.conftest import as_user

ADMIN = as_user("sarah")   # director: governance.manage
KARIM = as_user("karim")   # delivery manager: no governance.manage

SECRET = "nvapi-TEST-abcdefghijklmnop-1234"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(LLMProviderConfig).delete()
        db.commit()
    llm_config.invalidate()


def test_only_governance_admins_manage_llm_keys(client):
    assert client.get("/api/admin/llm", headers=KARIM).status_code == 403
    assert client.put("/api/admin/llm/copilot", json={"api_key": SECRET}, headers=KARIM).status_code == 403
    data = client.get("/api/admin/llm", headers=ADMIN).json()
    assert data["default"]["key"] == "default" and data["default"]["effective"]["source"] == "env"
    assert any(a["key"] == "copilot" for a in data["agents"])


def test_api_key_is_write_only_encrypted_and_never_audited(client):
    r = client.put("/api/admin/llm/copilot", json={"api_key": SECRET}, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_key"] and body["key_hint"] == "nvap…1234" and body["effective"]["source"] == "agent"
    assert SECRET not in r.text and SECRET not in client.get("/api/admin/llm", headers=ADMIN).text
    with SessionLocal() as db:
        row = db.get(LLMProviderConfig, "copilot")
        assert row.api_key_enc and SECRET not in row.api_key_enc              # encrypted at rest
        audits = db.query(AuditLog).filter(AuditLog.action == "llm.config_update").all()
        assert audits and all(SECRET not in json.dumps(a.details) for a in audits)
    assert llm_config.resolve("copilot").api_key == SECRET                    # the runtime sees the real key
    assert llm_config.resolve("billing").api_key != SECRET                    # other agents untouched


def test_default_key_is_inherited_but_never_sent_to_another_endpoint(client):
    client.put("/api/admin/llm/default", json={"api_key": SECRET}, headers=ADMIN)
    assert llm_config.resolve("billing").api_key == SECRET                    # agents inherit the org key
    r = client.put("/api/admin/llm/risk", json={"base_url": "https://other-provider.example/v1"}, headers=ADMIN)
    eff = r.json()["effective"]
    assert eff["base_url"] == "https://other-provider.example/v1" and eff["has_key"] is False
    assert llm_config.resolve("risk").api_key is None


def test_model_override_per_agent_and_validation(client):
    assert client.put("/api/admin/llm/default", json={"model": "x/y"}, headers=ADMIN).status_code == 400
    assert client.put("/api/admin/llm/copilot", json={"base_url": "ftp://nope"}, headers=ADMIN).status_code == 422
    assert client.put("/api/admin/llm/unknown_agent", json={"model": "x"}, headers=ADMIN).status_code == 404
    r = client.put("/api/admin/llm/copilot", json={"model": "meta/llama-3.1-8b-instruct"}, headers=ADMIN)
    assert r.json()["effective"]["model"] == "meta/llama-3.1-8b-instruct"
    # clearing every field removes the override entirely
    client.put("/api/admin/llm/copilot", json={"model": ""}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(LLMProviderConfig, "copilot") is None
    client.put("/api/admin/llm/copilot", json={"api_key": SECRET}, headers=ADMIN)
    assert client.delete("/api/admin/llm/copilot", headers=ADMIN).json()["has_key"] is False


def test_every_llm_call_is_routed_to_its_agents_endpoint_key_and_model(client, monkeypatch):
    client.put("/api/admin/llm/copilot", json={"api_key": SECRET, "base_url": "https://copilot.example/v1",
                                              "model": "copilot-model"}, headers=ADMIN)
    seen = {}

    class FakeCompletions:
        def create(self, **kw):
            seen["model"] = kw["model"]
            return type("R", (), {"usage": None, "choices": []})()

    def fake_client_for(cfg):
        seen["cfg"] = cfg
        return type("C", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})()

    monkeypatch.setattr(llm_config, "client_for", fake_client_for)
    with telemetry.attribute(agent="copilot"):
        llm.chat("copilot_turn", model="tier-model", messages=[])
    assert seen["cfg"].api_key == SECRET and seen["cfg"].base_url == "https://copilot.example/v1"
    assert seen["model"] == "copilot-model"
    with telemetry.attribute(agent="billing"):
        llm.chat("billing_cover_note", model="tier-model", messages=[])
    assert seen["cfg"].api_key != SECRET and seen["model"] == "tier-model"


def test_connection_test_reports_failures_without_crashing(client):
    r = client.post("/api/admin/llm/copilot/test", json={"base_url": "http://127.0.0.1:9/v1", "api_key": SECRET}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert SECRET not in r.text


def test_account_page_shows_profile_access_and_ai_usage(client):
    r = client.get("/api/account", headers=KARIM)
    assert r.status_code == 200, r.text
    acc = r.json()
    assert acc["username"] == "karim" and acc["auth_mode"] == "dev"
    assert acc["clients"] == ["cli-atlas"] and "used_today" in acc["ai"] and "daily_quota" in acc["ai"]
    assert acc["can_edit_name"] is False
    assert client.put("/api/account", json={"name": "K"}, headers=KARIM).status_code == 400  # identity not managed here
