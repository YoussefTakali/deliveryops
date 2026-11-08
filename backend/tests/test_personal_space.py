"""The personal knowledge space behind "Mon espace" is stored as a perimeter, but it is not a delivery
périmètre: it must never be listed in the portfolio / clients, nor be openable by anyone (not even a
director with access to every client) through the périmètre routes. Its owner keeps using it via
/api/assistant."""

from app import assistant
from app.auth import DEV_USERS
from app.db import SessionLocal
from app.models import PERSONAL_CLIENT
from tests.conftest import as_user

DIRECTOR = as_user("sarah")


def test_personal_space_hidden_from_perimeter_listings(client):
    r = client.get("/api/assistant/status", headers=DIRECTOR)
    assert r.status_code == 200

    with SessionLocal() as db:
        own = assistant.workspace(db, DEV_USERS["sarah"]).id
        other = assistant.workspace(db, DEV_USERS["karim"]).id

    portfolio = client.get("/api/portfolio", headers=DIRECTOR)
    assert portfolio.status_code == 200
    assert "Espace de" not in portfolio.text and own not in portfolio.text

    clients = client.get("/api/clients", headers=DIRECTOR).json()
    assert PERSONAL_CLIENT not in {c["id"] for c in clients}

    for pid in (own, other):
        assert client.get(f"/api/perimeters/{pid}", headers=DIRECTOR).status_code == 404
        assert client.post(f"/api/perimeters/{pid}/search", json={"query": "mail"}, headers=DIRECTOR).status_code == 404
