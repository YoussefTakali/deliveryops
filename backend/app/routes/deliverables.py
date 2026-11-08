"""Livrables API (/api/deliverables): generate project-management deliverables (COPIL, matrice de
couverture, plan de charge, …) as real Office files. The house method lives in app/deliverables/*;
companies tweak it via per-template overrides.

In a périmètre context (perimeter_id): input documents are ingested into the project's knowledge base
(so the copilot, the companion and the call assistant can search them), and every generated
deliverable is stored (structured draft + file) as a PerimeterDeliverable — the plan de charge starts
from the latest matrice automatically, the lifecycle tracker sees it, and agents can read it.

Permissions: `automation.use` to list/generate; `companion.admin` to edit a company's template.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, knowledge, staffing
from app.auth import User, require_permission
from app.automations import steps as auto_steps
from app.db import get_db
from app.deliverables import extract as X
from app.deliverables import generate as gen
from app.deliverables import plan as P
from app.deliverables import templates as T
from app.models import AutomationFile, DeliverableTemplate, Document, Perimeter, PerimeterDeliverable

router = APIRouter(prefix="/api/deliverables")
_use = require_permission("automation.use")
_admin = require_permission("companion.admin")

INPUT_DOC_TYPE = {"cahier_des_charges": "cahier_des_charges", "specs": "spec"}


def _overrides(db: Session) -> dict[str, dict]:
    return {r.key: (r.overrides or {}) for r in db.scalars(select(DeliverableTemplate))}


def _template(db: Session, key: str) -> dict:
    row = db.get(DeliverableTemplate, key)
    template = T.effective_template(key, row.overrides if row else None)
    if template is None:
        raise HTTPException(404, "Livrable inconnu")
    return template


def _perimeter(db: Session, user: User, perimeter_id: str | None) -> Perimeter | None:
    if not perimeter_id:
        return None
    p = db.get(Perimeter, perimeter_id)
    if p is None or not user.can_see(p.client_id):
        raise HTTPException(404, "Périmètre introuvable")
    return p


def _owned_file(db: Session, user: User, file_id: str) -> AutomationFile:
    f = db.get(AutomationFile, file_id)
    if f is None or f.owner != user.username:
        raise HTTPException(404, "Fichier introuvable")
    return f


def _ingest_input(db: Session, p: Perimeter, filename: str, doc_type: str, content: str) -> None:
    """Index an input document into the périmètre's knowledge base once (by filename)."""
    exists = db.scalar(select(Document.id).where(Document.perimeter_id == p.id, Document.filename == filename))
    if exists or not content.strip():
        return
    knowledge.ingest_document(db, p.id, filename, doc_type, content)


@router.get("/templates")
def list_templates(perimeter_id: str | None = None, user: User = Depends(_use), db: Session = Depends(get_db)):
    """The livrables available. With `perimeter_id`: what THAT périmètre can produce — its project's
    selection from the company catalogue + the project's own livrables. Without: the whole catalogue."""
    from app.routes.custom_deliverables import available_for_perimeter  # local: that module imports this one
    p = _perimeter(db, user, perimeter_id) if perimeter_id else None
    return {"templates": available_for_perimeter(db, user, p)}


# ---------------------------------------------------------------- samples (test data shipped with the app)

@router.get("/{key}/samples")
def list_samples(key: str, user: User = Depends(_use)):
    if key not in T.BUILTINS:
        raise HTTPException(404, "Livrable inconnu")
    return {"sets": X.sample_sets()}


@router.get("/{key}/samples/{set_name}/{name}")
def download_sample(key: str, set_name: str, name: str, user: User = Depends(_use)):
    p = X.sample_file(set_name, name)
    if key not in T.BUILTINS or p is None:
        raise HTTPException(404, "Fichier d'exemple introuvable")
    return FileResponse(p, filename=name)


# ---------------------------------------------------------------- prepare: inputs → extraction (questions | draft)

class PrepareIn(BaseModel):
    """`inputs` maps each declared input key to uploaded file ids (kind file/files), {"demo": true} for
    a connector input (demo tickets — later {"connector_id": …}), member ids (kind members) or text.
    `samples` loads a shipped sample set instead. `answers` are replies to earlier questions."""
    inputs: dict[str, Any] = Field(default_factory=dict)
    samples: str | None = None
    answers: dict[str, str] = Field(default_factory=dict)
    perimeter_id: str | None = None


def _prepare_matrice(db: Session, user: User, template: dict, body: PrepareIn, p: Perimeter | None) -> dict:
    texts: dict[str, str] = {}
    tickets: list[dict] = []
    if body.samples:
        try:
            texts, tickets = X.load_sample_inputs(body.samples)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    else:
        for spec in template.get("inputs") or []:
            val = body.inputs.get(spec["key"])
            if spec["kind"] in ("file", "files"):
                ids = [val] if isinstance(val, str) else list(val or [])
                if spec.get("required") and not ids:
                    raise HTTPException(400, f"Entrée requise : {spec['label']}")
                for fid in ids:
                    f = _owned_file(db, user, str(fid))
                    try:
                        texts[f.name] = knowledge.extract_text(f.name, f.data)
                    except Exception as exc:  # noqa: BLE001 — one unreadable file must be a clear error
                        raise HTTPException(400, f"Impossible de lire « {f.name} » : {exc}")
                    if p is not None:
                        _ingest_input(db, p, f.name, INPUT_DOC_TYPE.get(spec["key"], "spec"), texts[f.name])
            elif spec["kind"] == "connector" and isinstance(val, dict) and val.get("demo"):
                _, demo = X.load_sample_inputs(str(val.get("set") or "portail_client"))
                tickets.extend(demo)
    if not texts and not tickets:
        raise HTTPException(400, "Aucune entrée fournie")
    return X.extract_matrice(template, texts, tickets, body.answers)


def _prepare_plan(db: Session, user: User, template: dict, body: PrepareIn, p: Perimeter | None) -> dict:
    # 1) the matrice: an uploaded workbook, else the latest one generated for this périmètre, else a sample set
    matrice: dict | None = None
    ids = body.inputs.get("matrice")
    ids = [ids] if isinstance(ids, str) else list(ids or [])
    if ids:
        f = _owned_file(db, user, str(ids[0]))
        try:
            matrice = P.parse_matrice_xlsx(f.data)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    elif p is not None:
        last = staffing.latest_deliverable(db, p.id, "matrice_couverture")
        matrice = last.draft if last is not None else None
    if matrice is None and body.samples:
        texts, tickets = X.load_sample_inputs(body.samples)
        matrice = X.extract_matrice(_template(db, "matrice_couverture"), texts, tickets, {})["draft"]
    if not matrice or not matrice.get("exigences"):
        raise HTTPException(400, "Aucune matrice de couverture : générez-la d'abord (onglet Livrables) ou joignez son classeur")
    # 2) the team: chosen members, else everyone staffed on the périmètre
    members: list[dict] = []
    if p is not None:
        staffed = staffing.staffed(db, p.id)
        chosen = body.inputs.get("equipe")
        chosen = set(chosen) if isinstance(chosen, list) else set()
        members = [m for m in staffed if not chosen or m["id"] in chosen]
    # 3) technologies + contract
    tech_raw = body.inputs.get("technologies")
    technologies = [t.strip() for t in str(tech_raw or "").replace(";", ",").split(",") if t.strip()]
    contract = (p.contract or {}) if p is not None else {}
    return P.extract_plan(template, matrice, members, contract, technologies, body.answers)


@router.post("/{key}/prepare")
def prepare(key: str, body: PrepareIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    template = _template(db, key)
    if template.get("mode") != "extract":
        raise HTTPException(400, "Ce livrable se remplit par questions, pas par fichiers")
    p = _perimeter(db, user, body.perimeter_id)
    if key == "matrice_couverture":
        result = _prepare_matrice(db, user, template, body, p)
    elif key == "plan_de_charge":
        result = _prepare_plan(db, user, template, body, p)
    else:
        raise HTTPException(400, "Pas d'extracteur pour ce livrable")
    audit.log(db, user.username, "deliverable.prepare", body.perimeter_id, "deliverable", key,
              sources=result["sources"], used_llm=result["used_llm"])
    db.commit()
    return {"status": "questions" if result["questions"] else "ready", **result}


# ---------------------------------------------------------------- generate: answers | draft → file (+ stored per périmètre)

class GenerateIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    draft: dict[str, Any] | None = None     # the extracted (and possibly edited) structure, if any
    projet: str = ""
    perimeter_id: str | None = None         # the project this deliverable belongs to


def _summary_text(key: str, title: str, draft: dict | None, answers: dict) -> str:
    """A searchable text rendition of the deliverable for the knowledge base (agents / companion)."""
    lines = [f"# {title}"]
    d = draft or {}
    if key == "matrice_couverture":
        for e in d.get("exigences") or []:
            lines.append(f"Exigence {e.get('ref', '')} : {e.get('name', '')} — impact {e.get('impact', '?')}, probabilité {e.get('probabilite', '?')}")
        for c in d.get("cas_usage") or []:
            lines.append(f"Cas d'usage {c.get('ref', '')} : {c.get('name', '')} — couvre {', '.join(c.get('exigences') or []) or 'aucune exigence'}")
    elif key == "plan_de_charge":
        prm, tot = d.get("params") or {}, d.get("totals") or {}
        lines.append(f"Début {prm.get('start')} sur {prm.get('weeks')} semaines ; charge totale {tot.get('days')} jours ; coût {tot.get('cost')} €")
        for m in d.get("members") or []:
            lines.append(f"Ressource {m.get('name')} ({m.get('profile')}, TJM {m.get('rate')} €) : {m.get('total_days')} jours planifiés")
        for e in d.get("exigences") or []:
            lines.append(f"Exigence {e.get('ref', '')} {e.get('name', '')} : criticité {e.get('crit')}, {e.get('days')} j, {e.get('assignee')}, {e.get('week_start')}→{e.get('week_end')}, {e.get('cost')} €")
        lines += [f"Note : {n}" for n in d.get("notes") or []]
    else:
        lines += [f"{q} : {a}" for q, a in (answers or {}).items() if a]
    return "\n".join(lines)


@router.post("/{key}/generate")
def generate(key: str, body: GenerateIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    template = _template(db, key)
    p = _perimeter(db, user, body.perimeter_id)
    projet = body.projet or (p.name if p is not None else "")
    try:
        fname, content_type, data = gen.generate(key, template, body.answers, draft=body.draft, projet=projet)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    deliverable_id = None
    if p is not None:
        f = auto_steps.store_file(db, user.username, fname, data, content_type=content_type)
        d = PerimeterDeliverable(perimeter_id=p.id, key=key, title=template["title"], filename=fname, file_id=f.id,
                                 draft=body.draft or {"answers": body.answers}, created_by=user.username)
        db.add(d)
        db.flush()
        deliverable_id = d.id
        knowledge.ingest_document(db, p.id, fname.rsplit(".", 1)[0] + ".md", "livrable",
                                  _summary_text(key, template["title"], body.draft, body.answers))
    audit.log(db, user.username, "deliverable.generate", body.perimeter_id, "deliverable", key, filename=fname)
    db.commit()
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    if deliverable_id:
        headers["X-Deliverable-Id"] = deliverable_id
    return Response(content=data, media_type=content_type, headers=headers)


# ---------------------------------------------------------------- per-company template overrides

@router.get("/{key}/template")
def get_template(key: str, user: User = Depends(_admin), db: Session = Depends(get_db)):
    row = db.get(DeliverableTemplate, key)
    template = T.effective_template(key, row.overrides if row else None)
    if template is None:
        raise HTTPException(404, "Livrable inconnu")
    return {"key": key, "template": template, "override": (row.overrides if row else {}) or {},
            "updated_by": row.updated_by if row else None}


class OverrideIn(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


@router.put("/{key}/template")
def put_template(key: str, body: OverrideIn, user: User = Depends(_admin), db: Session = Depends(get_db)):
    if key not in T.BUILTINS:
        raise HTTPException(404, "Livrable inconnu")
    row = db.get(DeliverableTemplate, key)
    if row is None:
        row = DeliverableTemplate(key=key)
        db.add(row)
    allowed = {k: v for k, v in (body.overrides or {}).items()
               if k in ("title", "description", "questions", "inputs", "impacts", "probas", "grid", "sections")}
    row.overrides = allowed
    row.updated_by = user.username
    audit.log(db, user.username, "deliverable.template_update", None, "deliverable", key, fields=list(allowed))
    db.commit()
    return {"key": key, "override": row.overrides}
