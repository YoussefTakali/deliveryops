"""Perimeter onboarding: create clients, import data (CSV/XLSX), readiness checklist."""

import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audit, importer
from app.api import row
from app.auth import User, current_user, perimeter_guard, require_decider
from app.db import get_db
from app.models import Client, DeliveryProject, Perimeter

router = APIRouter(prefix="/api")

# Defaults applied to a new perimeter until the contract rules are extracted and validated
DEFAULT_CONTRACT = {
    "currency": "EUR",
    "hours_per_day": 7,
    "billable_activity_codes": {"RUN": "fixed", "MCO": "fixed", "EVOL": "time_material", "UO": "work_unit"},
    "non_billable_codes": ["INTERNAL", "TRAINING", "ONBOARDING"],
    "scope": {"out_of_scope": ["migration", "nouvelle application", "formation utilisateurs", "refonte"]},
    "kpi_targets": {"mttr_p2_h": 8, "backlog_max": 40, "reopen_rate_pct": 5, "change_failure_rate_pct": 10},
}


class ClientIn(BaseModel):
    name: str
    sector: str = ""


class PerimeterIn(BaseModel):
    client_id: str
    name: str
    description: str = ""
    project_id: str | None = None


@router.post("/clients")
def create_client(body: ClientIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if user.role != "director":
        raise HTTPException(403, "Only delivery directors can create clients")
    slug = "cli-" + (re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:30] or "client")
    if db.get(Client, slug):
        raise HTTPException(409, "A client with this name already exists")
    client = Client(id=slug, name=body.name.strip(), sector=body.sector.strip())
    db.add(client)
    audit.log(db, user.username, "client.created", None, "client", client.id, name=client.name)
    db.commit()
    return row(client)


@router.post("/perimeters")
def create_perimeter(body: PerimeterIn, user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if not user.can_see(body.client_id) or not db.get(Client, body.client_id):
        raise HTTPException(404, "Client not found")
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    project_id = None
    if body.project_id:
        proj = db.get(DeliveryProject, body.project_id)
        if not proj or proj.client_id != body.client_id:
            raise HTTPException(400, "Projet introuvable pour ce client")
        project_id = proj.id
    p = Perimeter(client_id=body.client_id, project_id=project_id, name=body.name.strip(), description=body.description.strip(), contract=dict(DEFAULT_CONTRACT))
    db.add(p)
    audit.log(db, user.username, "perimeter.created", p.id, "perimeter", p.id, name=p.name)
    db.commit()
    return row(p)


@router.get("/onboarding/datasets")
def datasets():
    return [{"id": k, "label": v["label"], "columns": v["columns"], "required": v["required"]} for k, v in importer.DATASETS.items()]


@router.get("/onboarding/templates/{dataset}")
def template(dataset: str):
    if dataset not in importer.DATASETS:
        raise HTTPException(404)
    return Response(importer.template_csv(dataset).encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="deliveryops-{dataset}.csv"'})


@router.post("/perimeters/{perimeter_id}/import/{dataset}")
async def import_dataset(dataset: str, file: UploadFile = File(...), p: Perimeter = Depends(perimeter_guard), user: User = Depends(require_decider), db: Session = Depends(get_db)):
    if dataset not in importer.DATASETS:
        raise HTTPException(404, "Unknown dataset")
    try:
        rows = importer.read_rows(file.filename or "", await file.read())
    except Exception as exc:  # noqa: BLE001 — malformed files come in every shape
        raise HTTPException(400, f"Fichier illisible : {exc}") from exc
    if not rows:
        raise HTTPException(400, "Aucune ligne de données dans le fichier")
    missing = [c for c in importer.DATASETS[dataset]["required"] if c not in rows[0]]
    if missing:
        raise HTTPException(400, f"Colonnes manquantes : {', '.join(missing)}")
    report = importer.import_rows(db, p.id, dataset, rows)
    audit.log(db, user.username, "data.imported", p.id, "perimeter", p.id, dataset=dataset, filename=file.filename, inserted=report.inserted, updated=report.updated, errors=len(report.errors))
    db.commit()
    return report.as_dict()


@router.get("/perimeters/{perimeter_id}/readiness")
def readiness(p: Perimeter = Depends(perimeter_guard), db: Session = Depends(get_db)):
    return importer.readiness(db, p)
