"""Livrables beyond the built-ins: a COMPANY CATALOGUE + livrables that BELONG TO A PROJECT.

- catalogue:   custom livrables with project_id NULL (+ the built-ins COPIL / matrice / plan de charge);
               visible to everyone, managed with `deliverables.catalog`.
- project:     each project picks which catalogue livrables it uses (ProjectLivrable) and can design its
               own project-only livrables (project_id set) — managed by the project's deciders, visible to
               anyone who can see the project's client. A project's périmètres use the project's set.
- design:      description + style examples → a spec (+ clarifying questions with a recommended option)
- level:       each livrable of a project is produced either at PROJECT level (one document for the whole
               project, fed by all its périmètres — like the plan de charge) or per PÉRIMÈTRE (for the
               périmètres the project picks). Built-ins have a fixed level; custom livrables default to project.
- prepare/generate: a run on a périmètre or on the project → questions or drafted content → the file,
               stored (PerimeterDeliverable / ProjectDeliverable) and indexed so every agent can read it.
See app/deliverables/custom.py for the engine.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, knowledge
from app.auth import User, require_ai, require_permission
from app.automations import steps as auto_steps
from app.db import get_db
from app.deliverables import custom as C
from app.deliverables import templates as T
from app.models import CustomDeliverable, DeliveryProject, LivrableScope, Perimeter, PerimeterDeliverable, ProjectDeliverable, ProjectLivrable
from app.routes.deliverables import _ingest_input, _overrides, _owned_file, _perimeter

router = APIRouter()
_use = require_permission("automation.use")
BUILTIN_KEYS = tuple(T.BUILTINS)


# ---------------------------------------------------------------- scope & rights

def _project(db: Session, user: User, pid: str) -> DeliveryProject:
    proj = db.get(DeliveryProject, pid)
    if proj is None or not user.can_see(proj.client_id):
        raise HTTPException(404, "Projet introuvable")
    return proj


def _is_decider(user: User) -> bool:
    return "recommendation.decide" in user.permissions


def can_edit(db: Session, user: User, c: CustomDeliverable) -> bool:
    if c.project_id is None:
        return "deliverables.catalog" in user.permissions
    proj = db.get(DeliveryProject, c.project_id)
    return bool(proj and user.can_see(proj.client_id) and _is_decider(user))


def _visible(db: Session, user: User, c: CustomDeliverable) -> bool:
    if c.project_id is None:
        return True
    proj = db.get(DeliveryProject, c.project_id)
    return bool(proj and user.can_see(proj.client_id))


def public(c: CustomDeliverable, user: User, db: Session | None = None) -> dict:
    return {
        "key": f"custom:{c.id}", "custom_id": c.id, "custom": True, "mode": "custom",
        "scope": "catalog" if c.project_id is None else "project", "project_id": c.project_id,
        "title": c.title, "description": c.description, "output": c.output,
        "inputs": c.inputs or [], "sections": c.sections or [], "instructions": c.instructions,
        "examples": [{"name": e.get("name"), "chars": len(e.get("text") or "")} for e in (c.examples or [])],
        "questions": [], "owner": c.owner, "can_edit": can_edit(db, user, c) if db is not None else False,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _builtins(db: Session) -> list[dict]:
    return [{**t, "custom": False, "scope": "catalog", "can_edit": False} for t in T.public_list(_overrides(db))]


def catalog_items(db: Session) -> list[CustomDeliverable]:
    return list(db.scalars(select(CustomDeliverable).where(CustomDeliverable.project_id.is_(None)).order_by(CustomDeliverable.created_at)))


def project_items(db: Session, pid: str) -> list[CustomDeliverable]:
    return list(db.scalars(select(CustomDeliverable).where(CustomDeliverable.project_id == pid).order_by(CustomDeliverable.created_at)))


def enabled_keys(db: Session, pid: str) -> set[str]:
    """Catalogue livrables the project uses. No selection yet → every built-in (sensible default)."""
    keys = set(db.scalars(select(ProjectLivrable.key).where(ProjectLivrable.project_id == pid)))
    return keys or set(BUILTIN_KEYS)


# Built-ins have a fixed level: the plan de charge aggregates every périmètre's matrice (project), COPIL and
# matrice de couverture are built from one périmètre's data (périmètre — the project still picks which ones).
LOCKED_LEVEL = {"plan_de_charge": "project", "copil": "perimeter", "matrice_couverture": "perimeter"}


def scope_of(db: Session, pid: str, key: str) -> dict:
    """{level, perimeter_ids (None = all), locked} for one livrable of a project."""
    row = db.scalar(select(LivrableScope).where(LivrableScope.project_id == pid, LivrableScope.key == key))
    locked = LOCKED_LEVEL.get(key)
    level = locked or (row.level if row else "project")
    ids = (row.perimeter_ids if row else None) if level == "perimeter" else None
    return {"level": level, "perimeter_ids": ids, "level_locked": bool(locked)}


def _in_scope(scope: dict, perimeter_id: str) -> bool:
    return scope["level"] == "perimeter" and (scope["perimeter_ids"] is None or perimeter_id in scope["perimeter_ids"])


def available_for_perimeter(db: Session, user: User, perimeter: Perimeter | None) -> list[dict]:
    """The livrables a périmètre can produce: its project's PÉRIMÈTRE-level livrables that include it
    (project-level ones are produced from the project page). A périmètre outside any project gets the
    catalogue's périmètre-level livrables."""
    builtins, catalog = _builtins(db), catalog_items(db)
    if perimeter is None or not perimeter.project_id:
        return [t for t in builtins if LOCKED_LEVEL.get(t["key"]) != "project"] + [public(c, user, db) for c in catalog]
    pid, keys = perimeter.project_id, enabled_keys(db, perimeter.project_id)
    items = ([t for t in builtins if t["key"] in keys]
             + [public(c, user, db) for c in catalog if f"custom:{c.id}" in keys]
             + [public(c, user, db) for c in project_items(db, pid)])
    return [t for t in items if _in_scope(scope_of(db, pid, t["key"]), perimeter.id)]


def _get(db: Session, user: User, cid: str) -> CustomDeliverable:
    c = db.get(CustomDeliverable, cid)
    if c is None or not _visible(db, user, c):
        raise HTTPException(404, "Livrable introuvable")
    return c


def _get_editable(db: Session, user: User, cid: str) -> CustomDeliverable:
    c = _get(db, user, cid)
    if not can_edit(db, user, c):
        raise HTTPException(403, "Vous ne pouvez pas modifier ce livrable" + (" (catalogue de l'entreprise)" if c.project_id is None else ""))
    return c


def _spec(c: CustomDeliverable) -> dict:
    return {"title": c.title, "description": c.description, "output": c.output, "inputs": c.inputs or [],
            "sections": c.sections or [], "instructions": c.instructions, "examples": c.examples or []}


def _read_files(db: Session, user: User, ids: list[str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for fid in ids:
        f = _owned_file(db, user, str(fid))
        try:
            texts[f.name] = knowledge.extract_text(f.name, f.data)
        except Exception as exc:  # noqa: BLE001 — every file format fails differently
            raise HTTPException(400, f"Impossible de lire « {f.name} » : {exc}") from exc
    return texts


# ---------------------------------------------------------------- catalogue & project livrables

@router.get("/api/deliverables/catalog")
def get_catalog(user: User = Depends(_use), db: Session = Depends(get_db)):
    return {"builtins": _builtins(db), "custom": [public(c, user, db) for c in catalog_items(db)],
            "can_manage": "deliverables.catalog" in user.permissions}


@router.get("/api/projects/{pid}/livrables")
def get_project_livrables(pid: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    proj = _project(db, user, pid)
    keys = enabled_keys(db, proj.id)
    catalog = _builtins(db) + [public(c, user, db) for c in catalog_items(db)]
    return {
        "catalog": [{**t, "enabled": t["key"] in keys, **scope_of(db, proj.id, t["key"])} for t in catalog],
        "project_items": [{**public(c, user, db), **scope_of(db, proj.id, f"custom:{c.id}")} for c in project_items(db, proj.id)],
        "perimeters": [{"id": p.id, "name": p.name} for p in db.scalars(
            select(Perimeter).where(Perimeter.project_id == proj.id).order_by(Perimeter.name))],
        "can_edit": user.can_see(proj.client_id) and _is_decider(user),
        "can_manage_catalog": "deliverables.catalog" in user.permissions,
    }


class KeysIn(BaseModel):
    keys: list[str] = Field(default_factory=list)


@router.put("/api/projects/{pid}/livrables")
def set_project_livrables(pid: str, body: KeysIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    proj = _project(db, user, pid)
    if not _is_decider(user):
        raise HTTPException(403, "Seuls les responsables du projet choisissent ses livrables")
    valid = set(BUILTIN_KEYS) | {f"custom:{c.id}" for c in catalog_items(db)}
    unknown = [k for k in body.keys if k not in valid]
    if unknown:
        raise HTTPException(400, f"Livrable inconnu du catalogue : {', '.join(unknown)}")
    for row in db.scalars(select(ProjectLivrable).where(ProjectLivrable.project_id == proj.id)):
        db.delete(row)
    db.flush()
    for k in dict.fromkeys(body.keys):
        db.add(ProjectLivrable(project_id=proj.id, key=k, added_by=user.username))
    audit.log(db, user.username, "project.livrables.set", None, "project", proj.id, keys=body.keys)
    db.commit()
    return get_project_livrables(pid, user, db)


class ScopeIn(BaseModel):
    key: str
    level: str = Field(pattern="^(project|perimeter)$")
    perimeter_ids: list[str] | None = None      # None = every périmètre of the project


@router.put("/api/projects/{pid}/livrables/scope")
def set_livrable_scope(pid: str, body: ScopeIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    """Choose whether a livrable is produced once for the whole project or per périmètre (and which ones)."""
    proj = _project(db, user, pid)
    if not _is_decider(user):
        raise HTTPException(403, "Seuls les responsables du projet choisissent le niveau de ses livrables")
    locked = LOCKED_LEVEL.get(body.key)
    if locked and body.level != locked:
        raise HTTPException(400, "Ce livrable de la méthode maison se produit toujours au niveau "
                                 + ("du projet" if locked == "project" else "des périmètres"))
    known = set(BUILTIN_KEYS) | {f"custom:{c.id}" for c in catalog_items(db)} | {f"custom:{c.id}" for c in project_items(db, proj.id)}
    if body.key not in known:
        raise HTTPException(404, "Livrable inconnu")
    ids = None
    if body.level == "perimeter" and body.perimeter_ids is not None:
        mine = {p.id for p in db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id))}
        ids = [i for i in dict.fromkeys(body.perimeter_ids) if i in mine]
        if not ids:
            raise HTTPException(400, "Choisissez au moins un périmètre du projet")
    row = db.scalar(select(LivrableScope).where(LivrableScope.project_id == proj.id, LivrableScope.key == body.key))
    if row is None:
        row = LivrableScope(project_id=proj.id, key=body.key)
        db.add(row)
    row.level, row.perimeter_ids, row.updated_by = body.level, ids, user.username
    audit.log(db, user.username, "project.livrable.scope", None, "project", proj.id, key=body.key, level=body.level, perimeter_ids=ids)
    db.commit()
    return get_project_livrables(pid, user, db)


# ---------------------------------------------------------------- design

class DesignIn(BaseModel):
    description: str = Field(min_length=3, max_length=6000)
    example_file_ids: list[str] = Field(default_factory=list)
    examples: list[dict] = Field(default_factory=list)    # already-extracted examples (kept across rounds)
    answers: dict[str, str] = Field(default_factory=dict)
    current: dict | None = None


@router.post("/api/deliverables/custom/design")
def design(body: DesignIn, user: User = Depends(_use), _ai: User = Depends(require_ai), db: Session = Depends(get_db)):
    examples = [e for e in body.examples if str(e.get("text") or "").strip()]
    for name, text in _read_files(db, user, body.example_file_ids[:3]).items():
        examples.append({"name": name, "text": text[:8000]})
    result = C.design(body.description, examples[:3], body.answers, body.current)
    audit.log(db, user.username, "deliverable.custom.design", None, "deliverable", "custom",
              used_llm=result["used_llm"], questions=len(result["questions"]))
    db.commit()
    return result


# ---------------------------------------------------------------- CRUD

class SpecIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    output: str = "docx"
    inputs: list[dict] = Field(default_factory=list)
    sections: list[dict] = Field(default_factory=list)
    instructions: str = ""
    examples: list[dict] = Field(default_factory=list)
    project_id: str | None = None      # set → a project-only livrable; None → the company catalogue


def _apply(c: CustomDeliverable, body: SpecIn) -> None:
    spec = C.normalize_spec(body.model_dump())
    c.title, c.description, c.output = spec["title"], spec["description"], spec["output"]
    c.inputs, c.sections, c.instructions, c.examples = spec["inputs"], spec["sections"], spec["instructions"], spec["examples"]


@router.get("/api/deliverables/custom/{cid}")
def get_custom(cid: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    c = _get(db, user, cid)
    editable = can_edit(db, user, c)
    return {**public(c, user, db), "examples_full": (c.examples or []) if editable else []}


@router.post("/api/deliverables/custom")
def create_custom(body: SpecIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    if body.project_id:
        proj = _project(db, user, body.project_id)
        if not _is_decider(user):
            raise HTTPException(403, "Seuls les responsables du projet créent ses livrables")
    elif "deliverables.catalog" not in user.permissions:
        raise HTTPException(403, "Le catalogue de l'entreprise est géré par ses administrateurs : créez ce livrable dans un projet")
    c = CustomDeliverable(owner=user.username, project_id=body.project_id or None)
    _apply(c, body)
    db.add(c)
    db.flush()
    audit.log(db, user.username, "deliverable.custom.created", None, "deliverable", c.id, title=c.title, output=c.output,
              scope="project" if c.project_id else "catalog", project=c.project_id)
    db.commit()
    return public(c, user, db)


@router.put("/api/deliverables/custom/{cid}")
def update_custom(cid: str, body: SpecIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    c = _get_editable(db, user, cid)
    _apply(c, body)   # the scope (catalogue vs project) never changes on edit — use « promote » for that
    audit.log(db, user.username, "deliverable.custom.updated", None, "deliverable", c.id, title=c.title)
    db.commit()
    return public(c, user, db)


@router.delete("/api/deliverables/custom/{cid}")
def delete_custom(cid: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    c = _get_editable(db, user, cid)
    for row in db.scalars(select(ProjectLivrable).where(ProjectLivrable.key == f"custom:{c.id}")):
        db.delete(row)
    audit.log(db, user.username, "deliverable.custom.deleted", None, "deliverable", c.id, title=c.title)
    db.delete(c)
    db.commit()
    return {"deleted": cid}


@router.post("/api/deliverables/custom/{cid}/promote")
def promote(cid: str, user: User = Depends(_use), db: Session = Depends(get_db)):
    """Publish a project's livrable to the company catalogue (a copy; the project keeps its own)."""
    if "deliverables.catalog" not in user.permissions:
        raise HTTPException(403, "Réservé aux gestionnaires du catalogue")
    src = _get(db, user, cid)
    if src.project_id is None:
        raise HTTPException(400, "Ce livrable est déjà dans le catalogue")
    c = CustomDeliverable(owner=user.username, project_id=None, title=src.title, description=src.description, output=src.output,
                          inputs=src.inputs, sections=src.sections, instructions=src.instructions, examples=src.examples)
    db.add(c)
    db.flush()
    audit.log(db, user.username, "deliverable.custom.promoted", None, "deliverable", c.id, source=src.id, title=c.title)
    db.commit()
    return public(c, user, db)


# ---------------------------------------------------------------- use: prepare → generate

def _check_usable(db: Session, c: CustomDeliverable, p: Perimeter | None, proj: DeliveryProject | None) -> None:
    key = f"custom:{c.id}"
    if proj is not None:
        if c.project_id and c.project_id != proj.id:
            raise HTTPException(400, "Ce livrable appartient à un autre projet")
        if c.project_id is None and key not in enabled_keys(db, proj.id):
            raise HTTPException(400, "Ce livrable du catalogue n'est pas utilisé par ce projet")
        if scope_of(db, proj.id, key)["level"] != "project":
            raise HTTPException(400, "Ce livrable se produit par périmètre : lancez-le depuis un périmètre du projet")
        return
    if p is None:
        return
    if c.project_id and p.project_id != c.project_id:
        raise HTTPException(400, "Ce livrable appartient à un autre projet")
    if p.project_id and not _in_scope(scope_of(db, p.project_id, key), p.id):
        raise HTTPException(400, "Ce livrable se produit au niveau du projet (onglet Livrables du projet), pas sur ce périmètre")


def _project_perimeters(db: Session, proj: DeliveryProject) -> list[Perimeter]:
    return list(db.scalars(select(Perimeter).where(Perimeter.project_id == proj.id)))


def _data_context(db: Session, c: CustomDeliverable, perimeter_ids: list[str]) -> str:
    """What the project already knows that is relevant to this livrable (matrices, plans, minutes, documents…)
    — so a project-level livrable is built from the data of all its périmètres, not only from uploads."""
    if not perimeter_ids:
        return ""
    query = " ".join([c.title, c.description or ""] + [s.get("title", "") for s in (c.sections or [])])[:600]
    try:
        hits = knowledge.search(db, perimeter_ids, query, k=8)
    except Exception:  # noqa: BLE001 — retrieval is a bonus; the uploaded inputs still work without it
        return ""
    return "\n\n".join(f"{h.cite()}\n{h.text[:1200]}" for h in hits)


class PrepareIn(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)   # {input_key: [file ids] | "text"}
    answers: dict[str, str] = Field(default_factory=dict)
    perimeter_id: str | None = None
    project_id: str | None = None             # set → a project-level run (one document for the whole project)


@router.post("/api/deliverables/custom/{cid}/prepare")
def prepare(cid: str, body: PrepareIn, user: User = Depends(_use), _ai: User = Depends(require_ai), db: Session = Depends(get_db)):
    c = _get(db, user, cid)
    proj = _project(db, user, body.project_id) if body.project_id else None
    p = None if proj else _perimeter(db, user, body.perimeter_id)
    _check_usable(db, c, p, proj)
    targets = _project_perimeters(db, proj) if proj else ([p] if p is not None else [])
    texts: dict[str, str] = {}
    notes: list[str] = []
    missing = []
    for spec_in in c.inputs or []:
        val = body.inputs.get(spec_in["key"])
        if spec_in["kind"] == "text":
            if isinstance(val, str) and val.strip():
                notes.append(f"{spec_in['label']} : {val.strip()}")
            elif spec_in.get("required"):
                missing.append(spec_in["label"])
            continue
        ids = [val] if isinstance(val, str) else list(val or [])
        if not ids:
            if spec_in.get("required"):
                missing.append(spec_in["label"])
            continue
        for name, text in _read_files(db, user, ids).items():
            texts[name] = text
            for target in targets:
                _ingest_input(db, target, name, "livrable_input", text)
    if missing:
        raise HTTPException(400, "Entrées obligatoires manquantes : " + ", ".join(missing))
    known = _data_context(db, c, [t.id for t in targets])
    if known:
        texts["Données du projet (base de connaissance)" if proj else "Données du périmètre (base de connaissance)"] = known
    result = C.prepare(_spec(c), texts, "\n".join(notes), body.answers, proj.name if proj else (p.name if p is not None else ""))
    audit.log(db, user.username, "deliverable.custom.prepare", body.perimeter_id, "deliverable", f"custom:{c.id}",
              used_llm=result["used_llm"], status=result["status"], project_id=body.project_id)
    db.commit()
    return result


class GenerateIn(BaseModel):
    draft: dict[str, Any]
    perimeter_id: str | None = None
    project_id: str | None = None


def _summary(draft: dict) -> str:
    lines = [f"# {draft.get('title', '')}"]
    for b in draft.get("sections") or []:
        lines.append(f"## {b.get('heading', '')}")
        lines += [str(p) for p in b.get("paragraphs") or []] + [f"- {x}" for x in b.get("bullets") or []]
    return "\n".join(lines)


@router.post("/api/deliverables/custom/{cid}/generate")
def generate(cid: str, body: GenerateIn, user: User = Depends(_use), db: Session = Depends(get_db)):
    c = _get(db, user, cid)
    proj = _project(db, user, body.project_id) if body.project_id else None
    p = None if proj else _perimeter(db, user, body.perimeter_id)
    _check_usable(db, c, p, proj)
    if not (body.draft.get("sections") or []):
        raise HTTPException(400, "Le brouillon est vide : préparez d'abord le livrable.")
    try:
        fname, ctype, data = C.render(_spec(c), body.draft)
    except Exception as exc:  # noqa: BLE001 — surface rendering problems to the user (and the debug helper)
        raise HTTPException(400, f"Le fichier n'a pas pu être généré : {exc}") from exc
    headers = {"Content-Disposition": f'attachment; filename="{fname}"', "Access-Control-Expose-Headers": "Content-Disposition, X-Deliverable-Id"}
    if proj is not None:          # one document for the project, indexed into every périmètre so all agents see it
        f = auto_steps.store_file(db, user.username, fname, data, content_type=ctype)
        d = ProjectDeliverable(project_id=proj.id, key=f"custom:{c.id}", title=c.title, filename=fname, file_id=f.id,
                               draft=body.draft, created_by=user.username)
        db.add(d)
        db.flush()
        headers["X-Deliverable-Id"] = d.id
        for target in _project_perimeters(db, proj):
            knowledge.ingest_document(db, target.id, fname.rsplit(".", 1)[0] + ".md", "livrable", _summary(body.draft))
    elif p is not None:
        f = auto_steps.store_file(db, user.username, fname, data, content_type=ctype)
        d = PerimeterDeliverable(perimeter_id=p.id, key=f"custom:{c.id}", title=c.title, filename=fname, file_id=f.id,
                                 draft=body.draft, created_by=user.username)
        db.add(d)
        db.flush()
        headers["X-Deliverable-Id"] = d.id
        knowledge.ingest_document(db, p.id, fname.rsplit(".", 1)[0] + ".md", "livrable", _summary(body.draft))
    audit.log(db, user.username, "deliverable.generate", body.perimeter_id, "deliverable", f"custom:{c.id}", filename=fname,
              project_id=body.project_id)
    db.commit()
    return Response(content=data, media_type=ctype, headers=headers)
