"""Tabular data import (CSV / XLSX) for onboarding a perimeter and for connectors.

Each dataset declares its columns; rows are validated one by one and upserted on a natural key
(application name, ticket ref, member name...). Errors are reported per row, valid rows still load.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, BacklogItem, Dependency, TeamMember, Ticket, Timesheet

CRITICALITY = {"low", "medium", "high", "critical"}
KINDS = {"incident", "request", "change", "problem"}
STATUSES = {"open", "in_progress", "resolved", "closed"}

DATASETS: dict[str, dict] = {
    "applications": {
        "label": "Applications",
        "columns": ["name", "description", "technology", "criticality", "business_owner", "users_count"],
        "required": ["name"],
        "example": ["ClaimsPortal", "Portail de déclaration", "Angular / Spring Boot", "critical", "Direction Indemnisation", "3500"],
    },
    "dependencies": {
        "label": "Dépendances",
        "columns": ["source", "target", "kind", "description"],
        "required": ["source", "target"],
        "example": ["ClaimsPortal", "SinistreCore", "api", "REST déclaration"],
    },
    "team": {
        "label": "Équipe",
        "columns": ["name", "profile", "skills", "weekly_capacity_h", "absences"],
        "required": ["name", "profile"],
        "example": ["Nadia Belkacem", "senior", "java;oracle", "35", "2026-10-12..2026-10-23"],
    },
    "tickets": {
        "label": "Tickets",
        "columns": ["ref", "kind", "priority", "application", "category", "title", "description", "status", "assignee", "opened_at", "resolved_at", "resolution", "estimate_h", "reopened", "failed_change"],
        "required": ["ref", "kind", "priority", "title", "opened_at"],
        "example": ["INC0001", "incident", "P2", "ClaimsPortal", "anomalie", "Erreur 500 à la soumission", "", "closed", "Nadia Belkacem", "2026-08-03 09:12", "2026-08-03 14:40", "Correctif déployé", "4", "0", "false"],
    },
    "timesheets": {
        "label": "Temps passés",
        "columns": ["member", "day", "activity_code", "ticket_ref", "hours", "comment"],
        "required": ["member", "day", "activity_code", "hours"],
        "example": ["Nadia Belkacem", "2026-08-03", "RUN", "INC0001", "5.5", ""],
    },
    "backlog": {
        "label": "Backlog",
        "columns": ["title", "required_skill", "estimate_h", "priority", "due_date", "status", "assignee"],
        "required": ["title", "required_skill", "estimate_h"],
        "example": ["Montée de version Angular", "angular", "120", "1", "2026-11-15", "todo", ""],
    },
}


@dataclass
class ImportReport:
    dataset: str
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "inserted": self.inserted, "updated": self.updated, "skipped": self.skipped, "errors": self.errors[:50], "error_count": len(self.errors)}


class RowError(ValueError):
    pass


def template_csv(dataset: str) -> str:
    spec = DATASETS[dataset]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(spec["columns"])
    writer.writerow(spec["example"])
    return out.getvalue()


def read_rows(filename: str, data: bytes) -> list[dict]:
    if filename.lower().endswith(".xlsx"):
        import openpyxl

        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        rows = [[("" if c is None else c) for c in r] for r in ws.iter_rows(values_only=True)]
        if not rows:
            return []
        header = [str(h).strip().lower() for h in rows[0]]
        return [dict(zip(header, r)) for r in rows[1:] if any(str(c).strip() for c in r)]
    text = data.decode("utf-8-sig", errors="replace")
    delimiter = ";" if text.split("\n", 1)[0].count(";") >= text.split("\n", 1)[0].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [{(k or "").strip().lower(): v for k, v in r.items()} for r in reader if any((v or "").strip() for v in r.values())]


# ---------------------------------------------------------------- coercion

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _float(v, name, default=None):
    if _s(v) == "":
        if default is None:
            raise RowError(f"{name} manquant")
        return default
    try:
        return float(str(v).replace(",", "."))
    except ValueError as exc:
        raise RowError(f"{name} invalide : {v}") from exc


def _date(v, name) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = _s(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    raise RowError(f"{name} invalide (AAAA-MM-JJ ou JJ/MM/AAAA) : {s}")


def _datetime(v, name) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = _s(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:19].replace("Z", ""), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RowError(f"{name} invalide : {s}") from exc


def _bool(v) -> bool:
    return _s(v).lower() in ("1", "true", "yes", "oui", "vrai", "x")


# ---------------------------------------------------------------- import

def import_rows(db: Session, perimeter_id: str, dataset: str, rows: list[dict]) -> ImportReport:
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset}")
    spec = DATASETS[dataset]
    report = ImportReport(dataset)
    apps = {a.name.lower(): a for a in db.scalars(select(Application).where(Application.perimeter_id == perimeter_id))}
    members = {m.name.lower(): m for m in db.scalars(select(TeamMember).where(TeamMember.perimeter_id == perimeter_id))}
    handler = globals()[f"_import_{dataset}"]

    for i, raw in enumerate(rows, start=2):  # row 1 is the header
        missing = [c for c in spec["required"] if _s(raw.get(c)) == ""]
        if missing:
            report.errors.append({"row": i, "message": f"colonnes obligatoires vides : {', '.join(missing)}"})
            continue
        try:
            # handlers validate every field before touching the session, so a bad row leaves no trace
            created = handler(db, perimeter_id, raw, apps, members)
            if created is None:
                report.skipped += 1
            elif created:
                report.inserted += 1
            else:
                report.updated += 1
        except RowError as exc:
            report.errors.append({"row": i, "message": str(exc)})
    db.flush()
    return report


def _member(members: dict, name: str, required=False) -> TeamMember | None:
    if not name:
        return None
    m = members.get(name.lower())
    if not m and required:
        raise RowError(f"membre inconnu : {name} (importer l'équipe d'abord)")
    return m


def _import_applications(db, pid, r, apps, members):
    crit = _s(r.get("criticality")).lower() or "medium"
    if crit not in CRITICALITY:
        raise RowError(f"criticité invalide : {crit}")
    name = _s(r["name"])
    users = int(_float(r.get("users_count"), "users_count", 0))
    app = apps.get(name.lower())
    created = app is None
    if created:
        app = Application(perimeter_id=pid, name=name)
        db.add(app)
        apps[name.lower()] = app
    app.description = _s(r.get("description")) or app.description or ""
    app.technology = _s(r.get("technology")) or app.technology or ""
    app.criticality = crit
    app.business_owner = _s(r.get("business_owner")) or app.business_owner or ""
    app.users_count = users
    return created


def _import_dependencies(db, pid, r, apps, members):
    src, tgt = apps.get(_s(r["source"]).lower()), apps.get(_s(r["target"]).lower())
    if not src or not tgt:
        raise RowError(f"application inconnue : {_s(r['source']) if not src else _s(r['target'])}")
    db.flush()
    existing = db.scalar(select(Dependency).where(Dependency.perimeter_id == pid, Dependency.source_id == src.id, Dependency.target_id == tgt.id))
    if existing:
        existing.kind = _s(r.get("kind")) or existing.kind
        existing.description = _s(r.get("description")) or existing.description
        return False
    db.add(Dependency(perimeter_id=pid, source_id=src.id, target_id=tgt.id, kind=_s(r.get("kind")) or "api", description=_s(r.get("description"))))
    return True


def _import_team(db, pid, r, apps, members):
    name = _s(r["name"])
    absences = []
    for part in _s(r.get("absences")).split(";"):
        if ".." in part:
            start, end = part.split("..", 1)
            absences.append({"start": _date(start, "absence début").isoformat(), "end": _date(end, "absence fin").isoformat()})
    capacity = _float(r.get("weekly_capacity_h"), "weekly_capacity_h", 35.0)
    m = members.get(name.lower())
    created = m is None
    if created:
        m = TeamMember(perimeter_id=pid, name=name, profile=_s(r["profile"]))
        db.add(m)
        members[name.lower()] = m
    m.profile = _s(r["profile"]).lower()
    m.skills = [s.strip().lower() for s in _s(r.get("skills")).replace(",", ";").split(";") if s.strip()]
    m.weekly_capacity_h = capacity
    m.absences = absences
    return created


def _import_tickets(db, pid, r, apps, members):
    kind, prio, status = _s(r["kind"]).lower(), _s(r["priority"]).upper(), (_s(r.get("status")).lower() or "open")
    if kind not in KINDS:
        raise RowError(f"type invalide : {kind}")
    if prio not in ("P1", "P2", "P3", "P4"):
        raise RowError(f"priorité invalide : {prio}")
    if status not in STATUSES:
        raise RowError(f"statut invalide : {status}")
    app_name = _s(r.get("application"))
    app = apps.get(app_name.lower()) if app_name else None
    if app_name and not app and not r.get("_lenient"):
        raise RowError(f"application inconnue : {app_name}")
    opened = _datetime(r["opened_at"], "opened_at")
    resolved = _datetime(r.get("resolved_at"), "resolved_at")
    if resolved and resolved < opened:
        raise RowError("resolved_at antérieur à opened_at")
    estimate = _float(r.get("estimate_h"), "estimate_h", 0) or None
    reopened = int(_float(r.get("reopened"), "reopened", 0))
    ref = _s(r["ref"])
    t = db.scalar(select(Ticket).where(Ticket.perimeter_id == pid, Ticket.ref == ref))
    created = t is None
    if created:
        t = Ticket(perimeter_id=pid, ref=ref, kind=kind, priority=prio, title=_s(r["title"]), opened_at=opened)
        db.add(t)
    assignee = _member(members, _s(r.get("assignee")))
    t.kind, t.priority, t.status = kind, prio, status
    t.application_id = app.id if app else None
    t.category = _s(r.get("category"))
    t.title = _s(r["title"])[:300]
    t.description = _s(r.get("description"))
    t.assignee_id = assignee.id if assignee else None
    t.opened_at, t.resolved_at = opened, resolved
    t.resolution = _s(r.get("resolution"))
    t.estimate_h = estimate
    t.reopened = reopened
    t.failed_change = _bool(r.get("failed_change"))
    return created


def _import_timesheets(db, pid, r, apps, members):
    m = _member(members, _s(r["member"]), required=True)
    day = _date(r["day"], "day")
    hours = _float(r["hours"], "hours")
    if not 0 < hours <= 24:
        raise RowError(f"heures hors bornes : {hours}")
    code = _s(r["activity_code"]).upper()
    ref = _s(r.get("ticket_ref")) or None
    existing = db.scalar(select(Timesheet).where(Timesheet.perimeter_id == pid, Timesheet.member_id == m.id, Timesheet.day == day, Timesheet.activity_code == code, Timesheet.ticket_ref == ref))
    if existing:
        existing.hours, existing.comment = hours, _s(r.get("comment"))
        return False
    db.add(Timesheet(perimeter_id=pid, member_id=m.id, day=day, activity_code=code, ticket_ref=ref, hours=hours, comment=_s(r.get("comment"))))
    return True


def _import_backlog(db, pid, r, apps, members):
    title = _s(r["title"])
    estimate = _float(r["estimate_h"], "estimate_h")
    priority = int(_float(r.get("priority"), "priority", 2))
    due = _date(r.get("due_date"), "due_date")
    item = db.scalar(select(BacklogItem).where(BacklogItem.perimeter_id == pid, BacklogItem.title == title))
    created = item is None
    if created:
        item = BacklogItem(perimeter_id=pid, title=title, required_skill="", estimate_h=0)
        db.add(item)
    assignee = _member(members, _s(r.get("assignee")))
    item.required_skill = _s(r["required_skill"]).lower()
    item.estimate_h = estimate
    item.priority = priority
    item.due_date = due
    item.status = _s(r.get("status")).lower() or "todo"
    item.assignee_id = assignee.id if assignee else None
    return created


def readiness(db: Session, perimeter) -> dict:
    """Onboarding checklist: what is loaded, what is missing, what to do next."""
    from app.models import Chunk, Document

    pid = perimeter.id

    def count(model):
        return len(db.scalars(select(model.id).where(model.perimeter_id == pid)).all())

    doc_types = {d.doc_type for d in db.scalars(select(Document).where(Document.perimeter_id == pid))}
    contract = perimeter.contract or {}
    items = [
        {"id": "documents", "label": "Documentation de transfert (dossier, DAT, DEX)", "done": bool({"transfer", "dat", "dex"} & doc_types), "detail": ", ".join(sorted(doc_types)) or "aucun document", "tab": "knowledge"},
        {"id": "contract_doc", "label": "Contrat importé", "done": "contract" in doc_types, "detail": "", "tab": "knowledge"},
        {"id": "contract_rules", "label": "Règles contractuelles validées (tarifs, SLA, périmètre)", "done": bool(contract.get("sla") and (contract.get("rate_card") or contract.get("run_fixed_monthly_fee"))), "detail": "", "tab": "knowledge"},
        {"id": "applications", "label": "Applications", "done": count(Application) > 0, "detail": f"{count(Application)} application(s)", "dataset": "applications"},
        {"id": "dependencies", "label": "Dépendances inter-applicatives", "done": count(Dependency) > 0, "detail": f"{count(Dependency)} flux", "dataset": "dependencies"},
        {"id": "team", "label": "Équipe et compétences", "done": count(TeamMember) > 0, "detail": f"{count(TeamMember)} personne(s)", "dataset": "team"},
        {"id": "tickets", "label": "Historique des tickets", "done": count(Ticket) > 0, "detail": f"{count(Ticket)} ticket(s)", "dataset": "tickets"},
        {"id": "timesheets", "label": "Temps passés", "done": count(Timesheet) > 0, "detail": f"{count(Timesheet)} saisie(s)", "dataset": "timesheets"},
        {"id": "backlog", "label": "Backlog", "done": count(BacklogItem) > 0, "detail": f"{count(BacklogItem)} élément(s)", "dataset": "backlog"},
    ]
    done = sum(1 for i in items if i["done"])
    return {"score": round(100 * done / len(items)), "items": items, "chunks": count(Chunk), "ready_for_onboarding": items[0]["done"] and items[3]["done"]}
