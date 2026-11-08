from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, connectors
from app.api import _guard_entity, row
from app.auth import User, perimeter_guard, require_decider
from app.db import get_db
from app.models import Connector, Perimeter

router = APIRouter(prefix="/api")

KINDS = {
    "jira": {"label": "Jira Cloud", "fields": ["base_url", "email", "project_key", "token_env", "jql_extra"]},
    "servicenow": {"label": "ServiceNow", "fields": ["instance_url", "username", "token_env", "assignment_group", "query"]},
    "simulated": {"label": "Flux de démonstration", "fields": ["batch_size"]},
}


class ConnectorIn(BaseModel):
    kind: str
    name: str
    config: dict = {}
    enabled: bool = True


def _get(db: Session, user: User, connector_id: str) -> Connector:
    c = db.get(Connector, connector_id)
    if not c:
        raise HTTPException(404)
    _guard_entity(db, user, c.perimeter_id)
    return c


def _clean(config: dict) -> dict:
    # secrets must be passed by env var name only
    forbidden = {"token", "password", "api_key", "secret"} & {k.lower() for k in config}
    if forbidden:
        raise HTTPException(400, f"Ne stockez pas de secret ({', '.join(forbidden)}) : renseignez token_env avec le nom d'une variable d'environnement")
    return config


@router.get("/connectors/kinds")
def kinds():
    return KINDS


@router.get("/perimeters/{perimeter_id}/connectors")
def list_connectors(p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    return [row(c) for c in db.scalars(select(Connector).where(Connector.perimeter_id == p.id))]


@router.post("/perimeters/{perimeter_id}/connectors")
def create_connector(body: ConnectorIn, p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if body.kind not in KINDS:
        raise HTTPException(400, "Unknown connector kind")
    c = Connector(perimeter_id=p.id, kind=body.kind, name=body.name.strip() or KINDS[body.kind]["label"], config=_clean(body.config), enabled=body.enabled)
    db.add(c)
    audit.log(db, user.username, "connector.created", p.id, "connector", c.id, kind=c.kind, name=c.name)
    db.commit()
    return row(c)


@router.put("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    c = _get(db, user, connector_id)
    c.name, c.config, c.enabled = body.name.strip() or c.name, _clean(body.config), body.enabled
    audit.log(db, user.username, "connector.updated", c.perimeter_id, "connector", c.id)
    db.commit()
    return row(c)


@router.delete("/connectors/{connector_id}")
def delete_connector(connector_id: str, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    c = _get(db, user, connector_id)
    audit.log(db, user.username, "connector.deleted", c.perimeter_id, "connector", c.id, name=c.name)
    db.delete(c)
    db.commit()
    return {"deleted": True}


@router.post("/connectors/{connector_id}/test")
def test_connector(connector_id: str, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    return connectors.test(db, _get(db, user, connector_id))


@router.post("/connectors/{connector_id}/sync")
def sync_connector(connector_id: str, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    return connectors.sync(db, _get(db, user, connector_id), user.username)
