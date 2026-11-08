"""The shared 'delivery brain': resolve a périmètre's project scope so every agent surface (copilot,
call assistant, orchestrated agents) reads the same facts — sibling périmètres, the project team, and
the project-level deliverables (plan de charge). Retrieval unions the whole project via
`knowledge.search(db, scope_perimeter_ids(...), ...)`."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeliveryProject, Perimeter, ProjectDeliverable, ProjectMember


def project_of(db: Session, perimeter: Perimeter) -> DeliveryProject | None:
    return db.get(DeliveryProject, perimeter.project_id) if perimeter.project_id else None


def visible_perimeter_ids(db: Session, user) -> list[str]:
    """Every delivery périmètre the user is allowed to see (client-scoped). Used to give the companion
    the user's whole delivery portfolio when no single project is in focus."""
    return [p.id for p in db.scalars(select(Perimeter)) if user.can_see(p.client_id)]


def sibling_perimeters(db: Session, perimeter: Perimeter) -> list[Perimeter]:
    """Every périmètre in the same delivery project (including this one). Just itself if unprojected."""
    if not perimeter.project_id:
        return [perimeter]
    rows = db.scalars(select(Perimeter).where(Perimeter.project_id == perimeter.project_id).order_by(Perimeter.name)).all()
    return list(rows) or [perimeter]


def scope_perimeter_ids(db: Session, perimeter: Perimeter) -> list[str]:
    """The périmètre ids to search over for a project-wide answer (self + siblings)."""
    return [p.id for p in sibling_perimeters(db, perimeter)]


def perimeter_names(db: Session, perimeter: Perimeter) -> dict[str, str]:
    return {p.id: p.name for p in sibling_perimeters(db, perimeter)}


def project_team(db: Session, project: DeliveryProject) -> list[dict]:
    from app import team  # local: keeps this module import-light
    members = list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id).order_by(ProjectMember.added_at)))
    names = team.display_names(db, {m.owner for m in members})
    return [{"owner": m.owner, "name": names.get(m.owner, m.owner), "role": m.role_in_project,
             "allocation_pct": m.allocation_pct, "external": m.owner.startswith("ext:")} for m in members]


def latest_project_plan(db: Session, project: DeliveryProject) -> ProjectDeliverable | None:
    return db.scalars(select(ProjectDeliverable)
                      .where(ProjectDeliverable.project_id == project.id, ProjectDeliverable.key == "plan_de_charge")
                      .order_by(ProjectDeliverable.created_at.desc())).first()
