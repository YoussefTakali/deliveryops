"""Team API (/api/team): dynamic groups (nested like a filesystem), projects and tasks with
Unix-style owner/group/others permissions, and following what teammates are working on. Layered on
the existing role/permission RBAC — see app/team.py for the access model.

`team.view` lets a user see the org groups and participate where their permissions allow; `team.manage`
is the global admin override (create/rename/delete any group, chmod/chown any resource)."""

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit, team
from app.auth import User, require_permission
from app.db import get_db
from app.models import CompanionProfile, Group, GroupMembership, Project, WorkItem

router = APIRouter(prefix="/api/team")
_view = require_permission("team.view")
_manage = require_permission("team.manage")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_due(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _group_owner(db: Session, user: User, group_id: str) -> bool:
    """May the user administer this group? (global manager, its creator, or a group 'owner' member)."""
    if team.is_superuser(user):
        return True
    g = db.get(Group, group_id)
    if g is not None and g.created_by == user.username:
        return True
    m = db.scalar(select(GroupMembership).where(GroupMembership.group_id == group_id, GroupMembership.member_owner == user.username))
    return bool(m and m.group_role == "owner")


# ---------------------------------------------------------------- directory & overview

@router.get("/directory")
def directory(user: User = Depends(_view), db: Session = Depends(get_db)):
    return team.directory(db)


@router.get("/overview")
def overview(tz: int = 0, user: User = Depends(_view), db: Session = Depends(get_db)):
    eff = team.effective_group_ids(db, user.username)
    names = team.display_names(db, set(team.teammates(db, user)) | {user.username})
    projects = team.visible_projects(db, user)
    counts = team.project_counts(db, [p.id for p in projects])
    my_tasks = db.scalars(select(WorkItem).where(WorkItem.assignee_owner == user.username, WorkItem.status != "done").order_by(WorkItem.due.is_(None), WorkItem.due, WorkItem.updated_at.desc())).all()
    proj_by_id = {p.id: p for p in projects}
    mates = team.teammates(db, user)
    return {
        "groups": team.group_tree(db, user),
        "my_group_ids": sorted(team.member_group_ids(db, user.username)),
        "can_manage": team.is_superuser(user),
        "projects": [team.project_out(db, p, user, eff, names, counts) for p in projects],
        "my_tasks": [team.item_out(it, proj_by_id.get(it.project_id), user, eff, names) for it in my_tasks][:50],
        "teammates": [team.member_work(db, user, m, tz) for m in mates][:24],
        "activity": team.recent_activity(db, user, 30),
        "share_work": bool((db.get(CompanionProfile, user.username) or CompanionProfile()).share_work),
    }


class MeIn(BaseModel):
    share_work: bool


@router.put("/me")
def set_me(body: MeIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    from app import companion

    prof = companion.profile(db, user)
    if prof.share_work != body.share_work:
        audit.log(db, user.username, "team.share_work", None, "companion_profile", user.username, enabled=body.share_work)
    prof.share_work = body.share_work
    db.commit()
    return {"share_work": prof.share_work}


# ---------------------------------------------------------------- groups

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    parent_id: str | None = None
    color: str = Field("", max_length=16)


@router.get("/groups")
def list_groups(user: User = Depends(_view), db: Session = Depends(get_db)):
    return team.group_tree(db, user)


@router.get("/groups/{group_id}")
def group_detail(group_id: str, tz: int = 0, user: User = Depends(_view), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Groupe introuvable")
    eff = team.effective_group_ids(db, user.username)
    members = db.scalars(select(GroupMembership).where(GroupMembership.group_id == group_id)).all()
    names = team.display_names(db, {m.member_owner for m in members})
    projects = [p for p in db.scalars(select(Project).where(Project.group_id == group_id)) if team._proj_access(user, p, eff)["read"]]
    counts = team.project_counts(db, [p.id for p in projects])
    return {
        "group": {"id": g.id, "name": g.name, "description": g.description, "parent_id": g.parent_id, "color": g.color},
        "can_admin": _group_owner(db, user, group_id),
        "members": [{"owner": m.member_owner, "name": names.get(m.member_owner, m.member_owner), "group_role": m.group_role, "title": m.title} for m in members],
        "projects": [team.project_out(db, p, user, eff, team.display_names(db, {p.owner_user for p in projects}), counts) for p in projects],
    }


@router.post("/groups")
def create_group(body: GroupIn, user: User = Depends(_manage), db: Session = Depends(get_db)):
    if body.parent_id and db.get(Group, body.parent_id) is None:
        raise HTTPException(400, "Groupe parent introuvable")
    g = Group(name=body.name.strip(), description=body.description.strip(), parent_id=body.parent_id or None, color=body.color, created_by=user.username)
    db.add(g)
    db.flush()
    db.add(GroupMembership(group_id=g.id, member_owner=user.username, group_role="owner"))
    team.log_activity(db, g.id, user.username, "created_group", f"a créé le groupe « {g.name} »", g.id)
    audit.log(db, user.username, "team.group_create", None, "group", g.id, name=g.name, parent=body.parent_id)
    db.commit()
    return {"id": g.id}


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Groupe introuvable")
    if not _group_owner(db, user, group_id):
        raise HTTPException(403, "Réservé à un responsable du groupe")
    if body.parent_id == group_id or (body.parent_id and body.parent_id in team.descendants(db, group_id)):
        raise HTTPException(400, "Un groupe ne peut pas être placé sous lui-même")
    g.name, g.description, g.color, g.parent_id = body.name.strip(), body.description.strip(), body.color, (body.parent_id or None)
    db.commit()
    return {"id": g.id}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, user: User = Depends(_view), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Groupe introuvable")
    if not _group_owner(db, user, group_id):
        raise HTTPException(403, "Réservé à un responsable du groupe")
    # children are detached (parent_id -> NULL by FK), memberships cascade; resources keep their id but lose the group
    db.execute(delete(GroupMembership).where(GroupMembership.group_id == group_id))
    db.delete(g)
    audit.log(db, user.username, "team.group_delete", None, "group", group_id, name=g.name)
    db.commit()
    return {"deleted": True}


class MemberIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    group_role: Literal["owner", "member"] = "member"
    title: str = Field("", max_length=120)


@router.post("/groups/{group_id}/members")
def add_member(group_id: str, body: MemberIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    if db.get(Group, group_id) is None:
        raise HTTPException(404, "Groupe introuvable")
    if not _group_owner(db, user, group_id):
        raise HTTPException(403, "Réservé à un responsable du groupe")
    existing = db.scalar(select(GroupMembership).where(GroupMembership.group_id == group_id, GroupMembership.member_owner == body.owner))
    if existing:
        existing.group_role, existing.title = body.group_role, body.title
    else:
        db.add(GroupMembership(group_id=group_id, member_owner=body.owner.strip(), group_role=body.group_role, title=body.title))
        team.log_activity(db, group_id, user.username, "added_member", f"a ajouté {body.owner} au groupe", group_id)
    audit.log(db, user.username, "team.member_add", None, "group", group_id, member=body.owner, role=body.group_role)
    db.commit()
    return {"ok": True}


@router.delete("/groups/{group_id}/members/{owner}")
def remove_member(group_id: str, owner: str, user: User = Depends(_view), db: Session = Depends(get_db)):
    if not _group_owner(db, user, group_id) and owner != user.username:  # you can always leave a group
        raise HTTPException(403, "Réservé à un responsable du groupe")
    db.execute(delete(GroupMembership).where(GroupMembership.group_id == group_id, GroupMembership.member_owner == owner))
    audit.log(db, user.username, "team.member_remove", None, "group", group_id, member=owner)
    db.commit()
    return {"removed": True}


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    group_id: str | None = None
    status: Literal["active", "paused", "done", "archived"] = "active"
    color: str = Field("", max_length=16)
    due: str | None = None
    mode: int | None = Field(None, ge=0, le=0o777)


def _owned_project(db: Session, user: User, project_id: str, need: str) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Projet introuvable")
    eff = team.effective_group_ids(db, user.username)
    if not team._proj_access(user, p, eff)[need]:
        raise HTTPException(403 if need != "read" else 404, "Accès refusé" if need != "read" else "Projet introuvable")
    return p


@router.get("/projects")
def list_projects(user: User = Depends(_view), db: Session = Depends(get_db)):
    eff = team.effective_group_ids(db, user.username)
    projects = team.visible_projects(db, user)
    names = team.display_names(db, {p.owner_user for p in projects})
    counts = team.project_counts(db, [p.id for p in projects])
    return [team.project_out(db, p, user, eff, names, counts) for p in projects]


@router.post("/projects")
def create_project(body: ProjectIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    # To file a project inside a group you must belong to it (or be a manager).
    if body.group_id:
        if db.get(Group, body.group_id) is None:
            raise HTTPException(400, "Groupe introuvable")
        if not team.is_superuser(user) and body.group_id not in (team.member_group_ids(db, user.username) | team.effective_group_ids(db, user.username)):
            raise HTTPException(403, "Vous n'êtes pas membre de ce groupe")
    p = Project(name=body.name.strip(), description=body.description.strip(), group_id=body.group_id or None,
                status=body.status, color=body.color, due=_parse_due(body.due), owner_user=user.username,
                mode=body.mode if body.mode is not None else 0o770)
    db.add(p)
    db.flush()
    team.log_activity(db, p.group_id, user.username, "created_project", f"a créé le projet « {p.name} »", p.id)
    audit.log(db, user.username, "team.project_create", None, "project", p.id, name=p.name, group=body.group_id)
    db.commit()
    return {"id": p.id}


@router.put("/projects/{project_id}")
def update_project(project_id: str, body: ProjectIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    p = _owned_project(db, user, project_id, "write")
    admin = team._proj_access(user, p, team.effective_group_ids(db, user.username))["admin"]
    p.name, p.description, p.status, p.color, p.due = body.name.strip(), body.description.strip(), body.status, body.color, _parse_due(body.due)
    if admin:  # chmod / chgrp require admin on the resource
        if body.mode is not None:
            p.mode = body.mode
        p.group_id = body.group_id or None
    p.updated_at = _now()
    db.commit()
    return {"id": p.id}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, user: User = Depends(_view), db: Session = Depends(get_db)):
    p = _owned_project(db, user, project_id, "admin")
    db.execute(delete(WorkItem).where(WorkItem.project_id == project_id))
    db.delete(p)
    audit.log(db, user.username, "team.project_delete", None, "project", project_id, name=p.name)
    db.commit()
    return {"deleted": True}


@router.get("/projects/{project_id}/board")
def project_board(project_id: str, user: User = Depends(_view), db: Session = Depends(get_db)):
    p = _owned_project(db, user, project_id, "read")
    eff = team.effective_group_ids(db, user.username)
    items = team.board(db, user, p)
    names = team.display_names(db, {it.assignee_owner for it in items if it.assignee_owner} | {p.owner_user})
    columns = {s: [] for s in team.STATUSES}
    for it in items:
        columns[it.status if it.status in columns else "todo"].append(team.item_out(it, p, user, eff, names))
    return {"project": team.project_out(db, p, user, eff, names), "columns": columns,
            "members": team.directory(db) if team._proj_access(user, p, eff)["write"] else []}


# ---------------------------------------------------------------- tasks

class TaskIn(BaseModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str = Field("", max_length=4000)
    status: Literal["todo", "doing", "blocked", "done"] = "todo"
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    assignee_owner: str | None = None
    due: str | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=4000)
    status: Literal["todo", "doing", "blocked", "done"] | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    assignee_owner: str | None = None
    due: str | None = None
    order: float | None = None
    clear_assignee: bool = False
    clear_due: bool = False


@router.post("/tasks")
def create_task(body: TaskIn, user: User = Depends(_view), db: Session = Depends(get_db)):
    proj = None
    group_id = None
    if body.project_id:
        proj = _owned_project(db, user, body.project_id, "write")
        group_id = proj.group_id
    it = WorkItem(project_id=body.project_id or None, group_id=group_id, title=body.title.strip(), description=body.description.strip(),
                  status=body.status, priority=body.priority, owner_user=user.username,
                  assignee_owner=(body.assignee_owner or None), due=_parse_due(body.due),
                  order=_now().timestamp(), mode=proj.mode if proj else 0o770)
    db.add(it)
    db.flush()
    team.log_activity(db, group_id, user.username, "created_task", f"a créé la tâche « {it.title} »"
                      + (f" pour {it.assignee_owner}" if it.assignee_owner else ""), it.id)
    db.commit()
    return {"id": it.id}


def _owned_task(db: Session, user: User, task_id: str, need: str) -> tuple[WorkItem, Project | None]:
    it = db.get(WorkItem, task_id)
    if it is None:
        raise HTTPException(404, "Tâche introuvable")
    proj = db.get(Project, it.project_id) if it.project_id else None
    eff = team.effective_group_ids(db, user.username)
    if not team._item_access(user, it, proj, eff)[need]:
        raise HTTPException(403 if need != "read" else 404, "Accès refusé" if need != "read" else "Tâche introuvable")
    return it, proj


@router.put("/tasks/{task_id}")
def update_task(task_id: str, body: TaskPatch, user: User = Depends(_view), db: Session = Depends(get_db)):
    it, proj = _owned_task(db, user, task_id, "write")
    before_status, before_assignee = it.status, it.assignee_owner
    if body.title is not None:
        it.title = body.title.strip() or it.title
    if body.description is not None:
        it.description = body.description.strip()
    if body.status is not None:
        it.status = body.status
        it.done_at = _now() if body.status == "done" else None
    if body.priority is not None:
        it.priority = body.priority
    if body.order is not None:
        it.order = body.order
    if body.clear_assignee:
        it.assignee_owner = None
    elif body.assignee_owner is not None:
        it.assignee_owner = body.assignee_owner or None
    if body.clear_due:
        it.due = None
    elif body.due is not None:
        it.due = _parse_due(body.due)
    it.updated_at = _now()
    gid = it.group_id or (proj.group_id if proj else None)
    if body.status is not None and body.status != before_status:
        verb = "completed_task" if body.status == "done" else "moved_task"
        team.log_activity(db, gid, user.username, verb, f"« {it.title} » → {it.status}", it.id)
    if it.assignee_owner != before_assignee and it.assignee_owner:
        team.log_activity(db, gid, user.username, "assigned_task", f"a assigné « {it.title} » à {it.assignee_owner}", it.id)
    db.commit()
    return {"id": it.id}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: User = Depends(_view), db: Session = Depends(get_db)):
    it, _ = _owned_task(db, user, task_id, "admin")
    db.delete(it)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------- following teammates

@router.get("/members/{owner}/work")
def member_work(owner: str, tz: int = 0, user: User = Depends(_view), db: Session = Depends(get_db)):
    if owner != user.username and owner not in team.teammates(db, user) and not team.is_superuser(user):
        raise HTTPException(404, "Introuvable")
    return team.member_work(db, user, owner, tz)
