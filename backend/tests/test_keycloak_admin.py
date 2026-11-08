"""Regression test for app.keycloak_admin.update_user: Keycloak's PUT /users/{id} replaces the
whole user representation, so a naive sparse-patch PUT silently wipes fields (email, name) that
weren't part of the update. Caught by live-testing against a real Keycloak container; pinned here
with a mock so it can't regress without a live server. See git history for the live repro.
"""

from app import keycloak_admin as kc


def test_update_user_merges_into_current_representation_instead_of_replacing(monkeypatch):
    current_representation = {
        "id": "u1", "username": "test.user", "email": "test.user@example.com",
        "firstName": "Test", "lastName": "User", "enabled": True, "attributes": {"clients": ["cli-atlas"]},
    }
    requests_made = []

    def fake_request(method, path, **kwargs):
        requests_made.append((method, path, kwargs.get("json")))

        class _Resp:
            def json(self_inner):
                return current_representation if method == "GET" else []

        return _Resp()

    monkeypatch.setattr(kc, "_request", fake_request)
    monkeypatch.setattr(kc, "get_user", lambda uid: {"id": uid})  # not under test here
    monkeypatch.setattr(kc, "set_role", lambda uid, role: requests_made.append(("SET_ROLE", role, None)))

    kc.update_user("u1", clients=["cli-atlas", "cli-horizon"], role="consultant")

    put_calls = [r for r in requests_made if r[0] == "PUT"]
    assert len(put_calls) == 1, "must issue exactly one PUT, built from the fetched representation"
    _, _, body = put_calls[0]
    # the fields we didn't touch must still be present in the PUT body (this is what the bug lost)
    assert body["email"] == "test.user@example.com"
    assert body["firstName"] == "Test" and body["lastName"] == "User"
    assert body["attributes"]["clients"] == ["cli-atlas", "cli-horizon"]
    assert ("SET_ROLE", "consultant", None) in requests_made


def test_update_user_skips_get_put_when_only_role_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(kc, "_request", lambda method, path, **kw: calls.append((method, path)))
    monkeypatch.setattr(kc, "get_user", lambda uid: {"id": uid})
    monkeypatch.setattr(kc, "set_role", lambda uid, role: calls.append(("SET_ROLE", role)))

    kc.update_user("u1", role="director")

    assert calls == [("SET_ROLE", "director")]
