"""Enterprise AI governance: suspend a user, block their AI use, daily token quotas (per user + org
default), daily caps per agent, the single LLM choke point, per-user usage attribution, and the
searchable audit trail of every admin action."""

import pytest

from app import governance, llm, telemetry
from app.db import SessionLocal
from app.models import UsageLedger
from tests.conftest import as_user

ADMIN = as_user("sarah")   # director: governance.manage
KARIM = as_user("karim")   # delivery manager: no governance.manage
TOM = as_user("tom")       # consultant


def _reset(client):
    client.put("/api/governance/policy", json={"ai_enabled": True, "default_daily_tokens": 0,
                                               "agent_caps": {k: 0 for k in governance.AGENTS}}, headers=ADMIN)
    for u in ("karim", "tom"):
        client.put(f"/api/governance/users/{u}", json={"app_blocked": False, "ai_blocked": False, "reset_quota": True}, headers=ADMIN)
    governance.invalidate()


def _spend(username: str, agent: str, tokens: int):
    with SessionLocal() as db:
        db.add(UsageLedger(username=username, agent=agent, purpose="test", tokens=tokens))
        db.commit()
    governance.invalidate()


def test_only_governance_admins_can_see_and_change_it(client):
    assert client.get("/api/governance/policy", headers=KARIM).status_code == 403
    assert client.get("/api/governance/users", headers=TOM).status_code == 403
    pol = client.get("/api/governance/policy", headers=ADMIN).json()
    assert pol["ai_enabled"] is True and any(a["key"] == "copilot" for a in pol["agents"])
    kinds = {a["key"]: a["kind"] for a in pol["agents"]}               # agents vs AI features, honestly labelled
    assert kinds["billing"] == kinds["estimation"] == kinds["knowledge_transfer"] == kinds["copilot"] == "agent"
    assert kinds["deliverables"] == kinds["automations"] == "feature"
    from app.agents.orchestrator import AGENTS as ORCHESTRATED
    assert all(kinds[a] == "agent" for a in ORCHESTRATED)                 # every orchestrated agent is a real, wired module
    users = client.get("/api/governance/users", headers=ADMIN).json()["users"]
    assert {"karim", "tom"} <= {u["username"] for u in users}
    assert client.get("/api/governance/users", params={"q": "tom"}, headers=ADMIN).json()["users"][0]["username"] == "tom"


def test_suspending_a_user_cuts_access_immediately(client):
    _reset(client)
    assert client.put("/api/governance/users/tom", json={"app_blocked": True}, headers=ADMIN).status_code == 200
    r = client.get("/api/me", headers=TOM)
    assert r.status_code == 403 and "suspendu" in r.json()["detail"]
    client.put("/api/governance/users/tom", json={"app_blocked": False}, headers=ADMIN)
    assert client.get("/api/me", headers=TOM).status_code == 200
    # an admin can never lock themselves out
    assert client.put("/api/governance/users/sarah", json={"app_blocked": True}, headers=ADMIN).status_code == 400


def test_ai_block_and_daily_quota_refuse_ai_with_a_clear_reason(client):
    _reset(client)
    client.put("/api/governance/users/tom", json={"ai_blocked": True}, headers=ADMIN)
    r = client.post("/api/companion/chat", json={"message": "bonjour"}, headers=TOM)
    assert r.status_code == 403 and "IA" in r.json()["detail"]
    assert client.get("/api/me", headers=TOM).status_code == 200          # AI blocked ≠ app blocked

    client.put("/api/governance/users/tom", json={"ai_blocked": False, "daily_token_quota": 100}, headers=ADMIN)
    _spend("tom", "companion", 150)
    r = client.post("/api/companion/chat", json={"message": "bonjour"}, headers=TOM)
    assert r.status_code == 429 and "quota" in r.json()["detail"]
    row = next(u for u in client.get("/api/governance/users", headers=ADMIN).json()["users"] if u["username"] == "tom")
    assert row["effective_quota"] == 100 and row["used_today"] >= 150

    # the org default applies to everyone without an override; a per-user value overrides it
    client.put("/api/governance/users/tom", json={"reset_quota": True}, headers=ADMIN)
    client.put("/api/governance/policy", json={"default_daily_tokens": 50}, headers=ADMIN)
    governance.invalidate()
    assert "quota" in governance.check("tom", "companion")
    client.put("/api/governance/users/tom", json={"daily_token_quota": 0}, headers=ADMIN)   # 0 = unlimited for tom
    governance.invalidate()
    assert governance.check("tom", "companion") is None
    _reset(client)


def test_agent_caps_kill_switch_and_the_llm_choke_point(client):
    _reset(client)
    client.put("/api/governance/policy", json={"agent_caps": {"copilot": 10}}, headers=ADMIN)
    _spend("someone", "copilot", 50)
    assert "Copilot" in governance.check("karim", "copilot")
    assert governance.check("karim", "companion") is None                    # other agents unaffected

    # every model call goes through llm._guard; a refusal is an openai.APIError so all fallbacks absorb it
    with telemetry.attribute(username="karim", agent="copilot"):
        with pytest.raises(llm.openai.APIError):
            llm._guard("copilot_turn", "test-model")
    assert client.put("/api/governance/policy", json={"agent_caps": {"nope": 1}}, headers=ADMIN).status_code == 400

    client.put("/api/governance/policy", json={"ai_enabled": False, "agent_caps": {"copilot": 0}}, headers=ADMIN)
    governance.invalidate()
    assert "désactivée" in governance.check("karim", "")
    _reset(client)


def test_usage_is_attributed_to_the_user_and_agent(client):
    _reset(client)
    with telemetry.attribute(username="karim", agent="billing"):
        telemetry.record("billing_cover_note", "m", 10, "ok", type("U", (), {"prompt_tokens": 30, "completion_tokens": 12})())
    telemetry.record("Companion", "m", 10, "ok", type("U", (), {"prompt_tokens": 5, "completion_tokens": 5})())  # no user → system, agent from purpose
    telemetry.flush()
    usage = client.get("/api/governance/usage", params={"days": 1}, headers=ADMIN).json()
    assert any(u["username"] == "karim" and u["tokens"] >= 42 for u in usage["by_user"])
    assert any(a["agent"] == "billing" for a in usage["by_agent"]) and any(a["agent"] == "companion" for a in usage["by_agent"])
    obs = client.get("/api/observability", params={"days": 1}, headers=ADMIN).json()
    assert any(u["username"] == "karim" for u in obs["by_user"]) and obs["by_model"]


def test_admin_actions_are_in_a_searchable_audit_log(client):
    _reset(client)
    client.put("/api/governance/users/tom", json={"note": "contrat externe"}, headers=ADMIN)
    r = client.get("/api/audit", params={"q": "governance.user_update tom"}, headers=ADMIN)
    assert r.status_code == 200 and int(r.headers["X-Total-Count"]) >= 1
    assert all(a["action"] == "governance.user_update" for a in r.json())
    assert client.get("/api/audit", params={"action": "governance", "actor": "sarah", "limit": 5}, headers=ADMIN).json()
    facets = client.get("/api/audit/facets", headers=ADMIN).json()
    assert "governance.user_update" in facets["actions"]
    assert client.get("/api/audit", headers=TOM).status_code == 403           # consultants have no audit.view
