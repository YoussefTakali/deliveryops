"""Tests for the Microsoft Graph integration. Graph itself is mocked with an httpx MockTransport,
so these run offline and assert our aggregation + graceful per-source degradation, not Microsoft."""

import httpx
import pytest

from app import microsoft_graph
from tests.conftest import as_user

ME = {"id": "u-1", "displayName": "Alice Doe", "userPrincipalName": "alice@contoso.com", "mail": "alice@contoso.com", "jobTitle": "DM"}


def _graph_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/me"):
        return httpx.Response(200, json=ME)
    if path.endswith("/calendarView") or path.endswith("/events"):
        return httpx.Response(200, json={"value": [{"subject": "Weekly", "start": {"dateTime": "2026-09-16T09:00:00"}, "end": {"dateTime": "2026-09-16T09:30:00"}, "isOnlineMeeting": True,
                                                    "attendees": [{"emailAddress": {"name": "Zaid Kadi", "address": "zaid@contoso.com"}}, {"emailAddress": {"address": "sam@contoso.com"}}]}]})
    if path.endswith("/messages"):
        return httpx.Response(200, json={"value": [{"subject": "Hi", "from": {"emailAddress": {"name": "Bob"}}, "receivedDateTime": "2026-09-16T08:00:00", "bodyPreview": "hello", "isRead": False}]})
    if path.endswith("/drive/recent"):
        return httpx.Response(403, json={"error": {"message": "consent required"}})  # a source that fails
    if path.endswith("/joinedTeams"):
        return httpx.Response(200, json={"value": [{"displayName": "Delivery", "description": "team"}]})
    if path.endswith("/todo/lists"):
        return httpx.Response(200, json={"value": [{"id": "l1", "displayName": "Tasks"}]})
    if "/todo/lists/l1/tasks" in path:
        return httpx.Response(200, json={"value": [{"title": "Do X", "importance": "high"}]})
    if path.endswith("/presence"):
        return httpx.Response(200, json={"availability": "Available", "activity": "Available"})
    # everything else (chats, followedSites): empty
    return httpx.Response(200, json={"value": []})


@pytest.fixture(autouse=True)
def _mock_graph(monkeypatch):
    def fake_client(token: str) -> httpx.Client:
        return httpx.Client(base_url=microsoft_graph.GRAPH, transport=httpx.MockTransport(_graph_handler))
    monkeypatch.setattr(microsoft_graph, "_client", fake_client)


def test_overview_aggregates_all_sources_and_degrades_gracefully():
    result = microsoft_graph.overview("any-token")
    assert result["profile"]["name"] == "Alice Doe"
    assert result["profile"]["email"] == "alice@contoso.com"

    sources = result["sources"]
    assert sources["calendar"]["items"][0]["subject"] == "Weekly"
    assert sources["calendar"]["items"][0]["online"] is True
    # attendee *names* are captured (not just a count), so "meeting with X" is answerable
    assert sources["calendar"]["items"][0]["attendees"] == ["Zaid Kadi", "sam@contoso.com"]
    assert sources["mail"]["unread"] == 1
    assert sources["teams"]["items"][0]["name"] == "Delivery"
    assert sources["tasks"]["count"] == 1
    assert sources["presence"]["availability"] == "Available"

    # the files source returned 403 — it must carry an error, not blow up the whole overview
    assert sources["files"]["error"] and sources["files"]["status"] == 403
    assert result["sources_available"] == result["sources_total"] - 1


def test_live_context_includes_calendar_with_attendees():
    live = microsoft_graph.live_context("any-token", "ai-je une réunion aujourd'hui avec Zaid ?")
    assert "Zaid Kadi" in live["text"] and "Weekly" in live["text"]
    assert any(s["ref"] == "live:calendar" for s in live["sources"])


def test_live_context_is_best_effort_on_failure(monkeypatch):
    monkeypatch.setattr(microsoft_graph, "_client", lambda token: (_ for _ in ()).throw(RuntimeError("boom")))
    assert microsoft_graph.live_context("t", "meeting today") == {"text": "", "sources": []}


def test_me_raises_on_401(monkeypatch):
    monkeypatch.setattr(microsoft_graph, "_client", lambda token: httpx.Client(base_url=microsoft_graph.GRAPH, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "bad"}}))))
    with pytest.raises(microsoft_graph.GraphError) as exc:
        microsoft_graph.me("expired")
    assert exc.value.status_code == 401


def test_config_reports_microsoft_disabled_by_default(client):
    # the test app runs with AUTH_MODE=dev and no Microsoft env, so config.microsoft is null
    assert client.get("/api/config").json()["microsoft"] is None


def test_microsoft_route_requires_a_token(client):
    # no Authorization header -> 401 from the graph_token dependency
    assert client.get("/api/microsoft/overview").status_code == 401
