"""Competence matrix, staffing a périmètre, and the périmètre lifecycle.

Members are users: skills live per user (`UserSkill`, global), a périmètre staffs people from that
matrix into `TeamMember` rows (linked back to the user via `TeamMemberUser`) so the existing capacity /
billing analytics keep working unchanged. Matching = skills vs. the technologies chosen for the project,
weighted by level, then by availability (what the person is already staffed on elsewhere, absences).
"""

import unicodedata
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import team
from app.models import (Document, ExternalPerson, Meeting, Perimeter, PerimeterDeliverable, TeamMember, TeamMemberUser, Ticket,
                        Timesheet, UserSkill)

SKILL_CATEGORIES = ("technology", "functional", "language", "method")
HOURS_PER_DAY = 7.0


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").strip().lower()


# ---------------------------------------------------------------- competence matrix

def skills_of(db: Session, owner: str) -> list[UserSkill]:
    return list(db.scalars(select(UserSkill).where(UserSkill.owner == owner).order_by(UserSkill.category, UserSkill.skill)))


def set_skills(db: Session, owner: str, items: list[dict]) -> list[UserSkill]:
    """Replace a user's skills with `items` [{skill, level, category}] (deduped, case-insensitive)."""
    wanted: dict[str, dict] = {}
    for it in items:
        name = str(it.get("skill") or "").strip()[:80]
        if not name:
            continue
        lvl = max(1, min(5, int(it.get("level") or 3)))
        cat = str(it.get("category") or "technology")
        wanted[_norm(name)] = {"skill": name, "level": lvl, "category": cat if cat in SKILL_CATEGORIES else "technology"}
    existing = {_norm(s.skill): s for s in skills_of(db, owner)}
    for key, s in existing.items():
        if key not in wanted:
            db.delete(s)
    for key, spec in wanted.items():
        s = existing.get(key)
        if s is None:
            db.add(UserSkill(owner=owner, **spec))
        else:
            s.skill, s.level, s.category = spec["skill"], spec["level"], spec["category"]
    db.flush()
    return skills_of(db, owner)


# ---------------------------------------------------------------- people without an account
# Not every company gives the app to every employee: a manager can enter a team member by hand. They get
# the owner key "ext:<id>", so the competence matrix, matching, staffing and project teams — all keyed by
# owner — work for them unchanged.

def external_people(db: Session) -> list[ExternalPerson]:
    return list(db.scalars(select(ExternalPerson).order_by(ExternalPerson.name)))


def create_external(db: Session, name: str, created_by: str, email: str = "", profile: str = "confirmed",
                    weekly_capacity_h: float = 35.0, skills: list | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Le nom est obligatoire")
    email = (email or "").strip().lower()
    person = db.scalars(select(ExternalPerson).where(ExternalPerson.email == email)).first() if email else None
    if person is None:
        person = ExternalPerson(name=name[:200], email=email[:200], profile=(profile or "confirmed")[:60],
                                weekly_capacity_h=max(1.0, min(float(weekly_capacity_h or 35.0), 60.0)), created_by=created_by)
        db.add(person)
        db.flush()
    owner = f"ext:{person.id}"
    if skills:
        set_skills(db, owner, [s if isinstance(s, dict) else {"skill": str(s)} for s in skills])
    return owner


def _externals_by_owner(db: Session, owners) -> dict[str, ExternalPerson]:
    ids = [o[4:] for o in owners if str(o).startswith("ext:")]
    if not ids:
        return {}
    return {f"ext:{p.id}": p for p in db.scalars(select(ExternalPerson).where(ExternalPerson.id.in_(ids)))}


def matrix(db: Session) -> list[dict]:
    """Everyone in the directory — plus the people managers entered manually — with their skills."""
    by_owner: dict[str, list[dict]] = {}
    for s in db.scalars(select(UserSkill)):
        by_owner.setdefault(s.owner, []).append({"skill": s.skill, "level": s.level, "category": s.category})
    people = [{**p, "is_external": False} for p in team.directory(db)]
    people += [{"owner": f"ext:{p.id}", "name": p.name, "is_external": True, "email": p.email, "profile": p.profile}
               for p in external_people(db)]
    return [{**p, "skills": sorted(by_owner.get(p["owner"], []), key=lambda x: (-x["level"], x["skill"]))} for p in people]


# ---------------------------------------------------------------- availability & matching

def _absence_days(absences: list, start: date, end: date) -> int:
    days = 0
    for a in absences or []:
        try:
            s = date.fromisoformat(str(a.get("start"))[:10])
            e = date.fromisoformat(str(a.get("end"))[:10])
        except (TypeError, ValueError, AttributeError):
            continue
        cur = max(s, start)
        while cur <= min(e, end):
            if cur.weekday() < 5:
                days += 1
            cur += timedelta(days=1)
    return days


def availability(db: Session, owner: str, exclude_perimeter: str | None = None) -> dict:
    """How free a person is: hours already staffed on other périmètres and absences in the next 4 weeks."""
    links = db.execute(select(TeamMemberUser, TeamMember).join(TeamMember, TeamMember.id == TeamMemberUser.member_id)
                       .where(TeamMemberUser.owner == owner)).all()
    today = date.today()
    staffed_h = 0.0
    absent = 0
    for _link, m in links:
        if exclude_perimeter and m.perimeter_id == exclude_perimeter:
            continue
        staffed_h += float(m.weekly_capacity_h or 0)
        absent += _absence_days(m.absences, today, today + timedelta(days=28))
    return {"staffed_weekly_h": round(staffed_h, 1), "free_weekly_h": round(max(0.0, 35.0 - staffed_h), 1),
            "absent_days_4w": absent, "perimeters": len({m.perimeter_id for _l, m in links})}


def match(db: Session, technologies: list[str], perimeter_id: str | None = None, limit: int = 20) -> list[dict]:
    """Rank people for a set of technologies: skill match (level-weighted) first, then availability."""
    wanted = [_norm(t) for t in technologies if str(t).strip()]
    out = []
    for person in matrix(db):
        score = 0
        matched = []
        for s in person["skills"]:
            n = _norm(s["skill"])
            if any(w and (w == n or w in n or n in w) for w in wanted):
                score += int(s["level"])
                matched.append(s["skill"])
        if wanted and score == 0:
            continue
        avail = availability(db, person["owner"], exclude_perimeter=perimeter_id)
        out.append({**person, "score": score, "matched": matched, "availability": avail})
    out.sort(key=lambda p: (-p["score"], -p["availability"]["free_weekly_h"], p["name"].lower()))
    return out[:limit]


# ---------------------------------------------------------------- staffing a périmètre

def staffed(db: Session, perimeter_id: str) -> list[dict]:
    links = {l.member_id: l.owner for l in db.scalars(
        select(TeamMemberUser).join(TeamMember, TeamMember.id == TeamMemberUser.member_id).where(TeamMember.perimeter_id == perimeter_id))}
    out = []
    for m in db.scalars(select(TeamMember).where(TeamMember.perimeter_id == perimeter_id).order_by(TeamMember.name)):
        out.append({"id": m.id, "name": m.name, "profile": m.profile, "skills": m.skills or [],
                    "weekly_capacity_h": m.weekly_capacity_h, "absences": m.absences or [], "owner": links.get(m.id)})
    return out


def staff(db: Session, perimeter: Perimeter, owners: list[str], profile: str = "confirmed", weekly_capacity_h: float = 35.0) -> list[dict]:
    """Staff users onto a périmètre: creates (or updates) a TeamMember per user with their skills copied
    from the competence matrix, linked back to the user."""
    names = team.display_names(db, set(owners))
    externals = _externals_by_owner(db, owners)
    existing = {l.owner: l for l in db.scalars(
        select(TeamMemberUser).join(TeamMember, TeamMember.id == TeamMemberUser.member_id).where(TeamMember.perimeter_id == perimeter.id))}
    for owner in owners:
        skills = [s.skill for s in skills_of(db, owner)]
        ext = externals.get(owner)  # a manually-entered person carries their own profile and capacity
        prof, cap = (ext.profile, ext.weekly_capacity_h) if ext else (profile, weekly_capacity_h)
        if owner in existing:
            m = db.get(TeamMember, existing[owner].member_id)
            if m is not None:
                m.skills, m.profile, m.weekly_capacity_h = skills, prof, cap
            continue
        m = TeamMember(perimeter_id=perimeter.id, name=names.get(owner, owner), profile=prof, skills=skills,
                       weekly_capacity_h=cap, absences=[])
        db.add(m)
        db.flush()
        db.add(TeamMemberUser(member_id=m.id, owner=owner))
    db.flush()
    return staffed(db, perimeter.id)


def unstaff(db: Session, perimeter_id: str, member_id: str) -> bool:
    m = db.get(TeamMember, member_id)
    if m is None or m.perimeter_id != perimeter_id:
        return False
    link = db.get(TeamMemberUser, member_id)
    if link is not None:
        db.delete(link)
    db.delete(m)
    db.flush()
    return True


# ---------------------------------------------------------------- lifecycle of a périmètre

INPUT_DOC_TYPES = ("cahier_des_charges", "spec", "specification")


def lifecycle(db: Session, p: Perimeter) -> dict:
    """The onboarding → run lifecycle of a périmètre, computed from what actually exists."""
    pid = p.id
    meetings = db.scalar(select(func.count()).select_from(Meeting).where(Meeting.perimeter_id == pid)) or 0
    inputs = db.scalar(select(func.count()).select_from(Document).where(Document.perimeter_id == pid, Document.doc_type.in_(INPUT_DOC_TYPES))) or 0
    tickets = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.perimeter_id == pid)) or 0
    members = db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.perimeter_id == pid)) or 0
    timesheets = db.scalar(select(func.count()).select_from(Timesheet).where(Timesheet.perimeter_id == pid)) or 0
    dl: dict[str, PerimeterDeliverable] = {}
    for d in db.scalars(select(PerimeterDeliverable).where(PerimeterDeliverable.perimeter_id == pid).order_by(PerimeterDeliverable.created_at)):
        dl[d.key] = d
    contract = p.contract or {}
    stages = [
        {"key": "kickoff", "label": "Réunion de cadrage avec le client", "done": meetings > 0,
         "detail": f"{meetings} réunion(s)", "link": f"/p/{pid}/meetings"},
        {"key": "inputs", "label": "Accès Jira, cahier des charges et spécifications", "done": inputs > 0 or tickets > 0,
         "detail": f"{inputs} document(s) d'entrée · {tickets} ticket(s)", "link": f"/p/{pid}/livrables"},
        {"key": "matrice", "label": "Matrice de couverture", "done": "matrice_couverture" in dl,
         "detail": dl["matrice_couverture"].filename if "matrice_couverture" in dl else "à générer", "link": f"/p/{pid}/livrables"},
        {"key": "staffing", "label": "Technologies et équipe (matrice de compétences)", "done": members > 0,
         "detail": f"{members} personne(s) affectée(s)", "link": f"/p/{pid}/equipe"},
        _plan_stage(db, p, dl),
        {"key": "billing", "label": "Suivi de la facturation", "done": bool(contract.get("rate_card")) and timesheets > 0,
         "detail": ("grille tarifaire " + ("définie" if contract.get("rate_card") else "manquante")) + f" · {timesheets} saisie(s) de temps",
         "link": f"/p/{pid}/billing"},
    ]
    done = sum(1 for s in stages if s["done"])
    return {"perimeter_id": pid, "stages": stages, "done": done, "total": len(stages),
            "current": next((s["key"] for s in stages if not s["done"]), None)}


def _plan_stage(db: Session, p: Perimeter, dl: dict) -> dict:
    """The plan de charge is a PROJECT document (it aggregates every périmètre's matrice): a périmètre
    under a project reads its project's plan; a stand-alone périmètre keeps its own."""
    plan = project_plan_for(db, p)
    if p.project_id:
        return {"key": "plan", "label": "Plan de charge (projet)", "done": plan is not None,
                "detail": plan.filename if plan is not None else "à générer au niveau du projet", "link": f"/pr/{p.project_id}/plan"}
    return {"key": "plan", "label": "Plan de charge", "done": "plan_de_charge" in dl,
            "detail": dl["plan_de_charge"].filename if "plan_de_charge" in dl else "à générer", "link": f"/p/{p.id}/livrables"}


def project_plan_for(db: Session, p: Perimeter):
    """The latest plan de charge that covers this périmètre: its project's plan, else a legacy périmètre plan."""
    if p.project_id:
        from app import context  # local: context imports this module's neighbours
        from app.models import DeliveryProject
        proj = db.get(DeliveryProject, p.project_id)
        return context.latest_project_plan(db, proj) if proj is not None else None
    return latest_deliverable(db, p.id, "plan_de_charge")


def _planned_days(plan, p: Perimeter) -> dict[str, float]:
    """Planned days per person for THIS périmètre. A project plan tags each exigence with its périmètre
    ("[SI Sinistres] …"), so only that périmètre's share is compared with its timesheets."""
    draft = plan.draft or {}
    if getattr(plan, "project_id", None):
        tag = f"[{p.name}]"
        mine = [e for e in draft.get("exigences") or [] if str(e.get("name", "")).startswith(tag)]
        if mine:
            out: dict[str, float] = {}
            for e in mine:
                if e.get("assignee"):
                    out[e["assignee"]] = out.get(e["assignee"], 0.0) + float(e.get("days") or 0)
            return out
    return {m["name"]: float(m.get("total_days") or 0) for m in draft.get("members", [])}


# ---------------------------------------------------------------- plan vs réel (billing monitoring)

def latest_deliverable(db: Session, perimeter_id: str, key: str) -> PerimeterDeliverable | None:
    return db.scalars(select(PerimeterDeliverable).where(PerimeterDeliverable.perimeter_id == perimeter_id, PerimeterDeliverable.key == key)
                      .order_by(PerimeterDeliverable.created_at.desc())).first()


def plan_vs_actual(db: Session, p: Perimeter) -> dict:
    """Planned days per person (latest plan de charge) vs. timesheets — the billing monitoring view."""
    plan = project_plan_for(db, p)
    if plan is None:
        return {"has_plan": False, "project_id": p.project_id, "rows": [], "totals": {"planned_days": 0, "actual_days": 0}}
    hpd = float((p.contract or {}).get("hours_per_day") or HOURS_PER_DAY)
    planned = _planned_days(plan, p)
    actual: dict[str, float] = {}
    for m, hours in db.execute(select(TeamMember, func.sum(Timesheet.hours)).join(Timesheet, Timesheet.member_id == TeamMember.id)
                               .where(TeamMember.perimeter_id == p.id).group_by(TeamMember.id)).all():
        actual[m.name] = round(float(hours or 0) / hpd, 1)
    rows = []
    for name in sorted(set(planned) | set(actual)):
        pl, ac = planned.get(name, 0.0), actual.get(name, 0.0)
        status = "over" if pl and ac > pl * 1.1 else ("under" if pl and ac < pl * 0.5 else "ok")
        rows.append({"name": name, "planned_days": round(pl, 1), "actual_days": ac, "delta_days": round(ac - pl, 1), "status": status})
    return {"has_plan": True, "plan_id": plan.id, "project_id": p.project_id, "generated_at": plan.created_at.isoformat() if plan.created_at else None,
            "rows": rows, "totals": {"planned_days": round(sum(planned.values()), 1), "actual_days": round(sum(actual.values()), 1)}}
