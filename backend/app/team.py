"""Groups & project management with Unix-filesystem-style access, layered on the dynamic RBAC.

The org is not a fixed manager→report tree: an admin creates ROLES and grants them PERMISSIONS
(existing RBAC), assigns people roles, and organises people into GROUPS that nest like directories.
Access to a resource (a project or a task) is then resolved exactly like Unix file permissions:

    owner  (the user who owns it)      rwx
    group  (its group + any parent)    rwx
    others (everyone else)             rwx

where r=read (see it), w=write (edit/move/comment), a=admin (change perms/owner/delete). A user is
"in the group" of a resource if they are a member of that group OR of any ancestor group (so a
top-level group is a manager's scope over everything beneath it). The `team.manage` permission is a
global superuser override. This keeps it fully dynamic while staying predictable and auditable.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (AuthUser, CompanionProfile, Group, GroupActivity, GroupMembership, MicrosoftUser, Project, WorkItem)

log = logging.getLogger(__name__)

R, W, A = 4, 2, 1  # read / write / admin bits, per triad (like Unix rwx)
STATUSES = ("todo", "doing", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "urgent")
PROJECT_STATUSES = ("active", "paused", "done", "archived")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_superuser(user) -> bool:
    return "team.manage" in getattr(user, "permissions", set())


# ---------------------------------------------------------------- group tree

def _all_groups(db: Session) -> dict[str, Group]:
    return {g.id: g for g in db.scalars(select(Group))}


def _children_map(groups: dict[str, Group]) -> dict[str | None, list[str]]:
    kids: dict[str | None, list[str]] = defaultdict(list)
    for g in groups.values():
        kids[g.parent_id].append(g.id)
    return kids


def descendants(db: Session, group_id: str, groups: dict[str, Group] | None = None) -> set[str]:
    groups = groups or _all_groups(db)
    kids = _children_map(groups)
    out: set[str] = set()
    stack = list(kids.get(group_id, []))
    while stack:
        gid = stack.pop()
        if gid in out:
            continue
        out.add(gid)
        stack.extend(kids.get(gid, []))
    return out


def ancestors(group_id: str | None, groups: dict[str, Group]) -> set[str]:
    out: set[str] = set()
    seen = 0
    while group_id and group_id in groups and seen < 64:
        out.add(group_id)
        group_id = groups[group_id].parent_id
        seen += 1
    return out


def member_group_ids(db: Session, owner: str) -> set[str]:
    """Groups the user is a direct member of."""
    return set(db.scalars(select(GroupMembership.group_id).where(GroupMembership.member_owner == owner)))


def effective_group_ids(db: Session, owner: str, groups: dict[str, Group] | None = None) -> set[str]:
    """Direct memberships PLUS everything nested under them — a member of a parent group implicitly
    covers its sub-groups (manager oversight)."""
    groups = groups or _all_groups(db)
    direct = member_group_ids(db, owner)
    out = set(direct)
    for gid in direct:
        out |= descendants(db, gid, groups)
    return out


# ---------------------------------------------------------------- access resolution (Unix-style)

def _triad(mode: int, who: str) -> int:
    shift = {"owner": 6, "group": 3, "others": 0}[who]
    return (mode >> shift) & 7


def access(user, *, owner_user: str, group_id: str | None, mode: int, eff_groups: set[str]) -> dict:
    """Effective {read, write, admin} for `user` on a resource. Union of every triad that applies
    (owner / group / others) — friendlier than Unix's first-match for a collaboration tool — plus the
    global `team.manage` override."""
    if is_superuser(user):
        return {"read": True, "write": True, "admin": True}
    bits = _triad(mode, "others")
    if owner_user and owner_user == user.username:
        bits |= _triad(mode, "owner")
    if group_id and group_id in eff_groups:
        bits |= _triad(mode, "group")
    return {"read": bool(bits & R), "write": bool(bits & W), "admin": bool(bits & A)}


def _proj_access(user, p: Project, eff: set[str]) -> dict:
    return access(user, owner_user=p.owner_user, group_id=p.group_id, mode=p.mode, eff_groups=eff)


def _item_access(user, it: WorkItem, proj: Project | None, eff: set[str]) -> dict:
    # A task's group falls back to its project's group; the assignee always keeps write on their task.
    gid = it.group_id or (proj.group_id if proj else None)
    acc = access(user, owner_user=it.owner_user, group_id=gid, mode=it.mode, eff_groups=eff)
    if it.assignee_owner and it.assignee_owner == user.username:
        acc = {**acc, "read": True, "write": True}
    return acc


# ---------------------------------------------------------------- directory of people

def directory(db: Session) -> list[dict]:
    """Everyone who can be added to a group / assigned a task: known accounts (native or Microsoft),
    anyone who has a companion profile, the dev personas, and anyone already referenced on the board."""
    from app.auth import DEV_USERS

    people: dict[str, str] = {}

    def add(owner: str | None, name: str | None):
        owner = (owner or "").strip()
        if owner and owner not in people:
            people[owner] = (name or owner).strip() or owner
        elif owner and name and people.get(owner) == owner:
            people[owner] = name

    for au in db.scalars(select(AuthUser).where(AuthUser.active.is_(True))):
        add(au.email, au.name)
    for mu in db.scalars(select(MicrosoftUser).where(MicrosoftUser.active.is_(True))):
        add(mu.email, mu.name)
    for p in db.scalars(select(CompanionProfile)):
        add(p.owner, p.display_name)
    for u in DEV_USERS.values():
        add(u.username, u.name)
    for owner in db.scalars(select(GroupMembership.member_owner).distinct()):
        add(owner, None)
    return sorted(({"owner": o, "name": n} for o, n in people.items()), key=lambda x: x["name"].lower())


def display_names(db: Session, owners: set[str]) -> dict[str, str]:
    names = {p.owner: p.display_name for p in db.scalars(select(CompanionProfile).where(CompanionProfile.owner.in_(owners))) if p.display_name}
    for au in db.scalars(select(AuthUser).where(AuthUser.email.in_(owners))):
        names.setdefault(au.email, au.name or au.email)
    for mu in db.scalars(select(MicrosoftUser).where(MicrosoftUser.email.in_(owners))):
        names.setdefault(mu.email, mu.name or mu.email)
    from app.auth import DEV_USERS
    for u in DEV_USERS.values():
        names.setdefault(u.username, u.name)
    ext_ids = [o[4:] for o in owners if o.startswith("ext:")]
    if ext_ids:  # team members entered manually by a manager (no account)
        from app.models import ExternalPerson
        for p in db.scalars(select(ExternalPerson).where(ExternalPerson.id.in_(ext_ids))):
            names[f"ext:{p.id}"] = p.name
    return {o: names.get(o, o) for o in owners}


# ---------------------------------------------------------------- serialisation

def group_out(db: Session, g: Group, member_counts: dict[str, int]) -> dict:
    return {"id": g.id, "name": g.name, "description": g.description, "parent_id": g.parent_id, "color": g.color,
            "members": member_counts.get(g.id, 0), "created_by": g.created_by}


def group_tree(db: Session, user) -> list[dict]:
    """The whole group forest with member counts. Everyone with team.view sees the org structure
    (names only); resource contents stay permission-gated."""
    groups = _all_groups(db)
    counts = dict(db.execute(select(GroupMembership.group_id, func.count()).group_by(GroupMembership.group_id)).all())
    nodes = {gid: {**group_out(db, g, counts), "children": []} for gid, g in groups.items()}
    roots = []
    for gid, node in nodes.items():
        parent = groups[gid].parent_id
        if parent and parent in nodes:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)
    _sort_tree(roots)
    return roots


def _sort_tree(nodes: list[dict]):
    nodes.sort(key=lambda n: n["name"].lower())
    for n in nodes:
        _sort_tree(n["children"])


def mode_str(mode: int) -> str:
    def t(v):
        return ("r" if v & R else "-") + ("w" if v & W else "-") + ("a" if v & A else "-")
    return t(_triad(mode, "owner")) + t(_triad(mode, "group")) + t(_triad(mode, "others"))


def project_out(db: Session, p: Project, user, eff: set[str], names: dict[str, str], counts: dict[str, dict] | None = None) -> dict:
    acc = _proj_access(user, p, eff)
    c = (counts or {}).get(p.id, {})
    return {"id": p.id, "name": p.name, "description": p.description, "status": p.status, "group_id": p.group_id,
            "owner_user": p.owner_user, "owner_name": names.get(p.owner_user, p.owner_user), "color": p.color,
            "mode": p.mode, "mode_str": mode_str(p.mode), "due": p.due.isoformat() if p.due else None,
            "access": acc, "counts": c}


def item_out(it: WorkItem, proj: Project | None, user, eff: set[str], names: dict[str, str]) -> dict:
    acc = _item_access(user, it, proj, eff)
    return {"id": it.id, "project_id": it.project_id, "group_id": it.group_id or (proj.group_id if proj else None),
            "title": it.title, "description": it.description, "status": it.status, "priority": it.priority,
            "owner_user": it.owner_user, "assignee_owner": it.assignee_owner,
            "assignee_name": names.get(it.assignee_owner, it.assignee_owner) if it.assignee_owner else None,
            "due": it.due.isoformat() if it.due else None, "order": it.order, "access": acc,
            "updated_at": it.updated_at.isoformat() if it.updated_at else None}


# ---------------------------------------------------------------- queries

def visible_projects(db: Session, user) -> list[Project]:
    eff = effective_group_ids(db, user.username)
    out = []
    for p in db.scalars(select(Project).where(Project.status != "archived").order_by(Project.updated_at.desc())):
        if _proj_access(user, p, eff)["read"]:
            out.append(p)
    return out


def project_counts(db: Session, project_ids: list[str]) -> dict[str, dict]:
    counts: dict[str, dict] = {pid: {"todo": 0, "doing": 0, "blocked": 0, "done": 0, "total": 0} for pid in project_ids}
    if not project_ids:
        return counts
    for pid, status, n in db.execute(
        select(WorkItem.project_id, WorkItem.status, func.count()).where(WorkItem.project_id.in_(project_ids)).group_by(WorkItem.project_id, WorkItem.status)
    ).all():
        c = counts.setdefault(pid, {"todo": 0, "doing": 0, "blocked": 0, "done": 0, "total": 0})
        if status in c:
            c[status] = n
        c["total"] += n
    return counts


def board(db: Session, user, project: Project) -> list[WorkItem]:
    eff = effective_group_ids(db, user.username)
    items = list(db.scalars(select(WorkItem).where(WorkItem.project_id == project.id).order_by(WorkItem.order, WorkItem.created_at)))
    return [it for it in items if _item_access(user, it, project, eff)["read"]]


def log_activity(db: Session, group_id: str | None, actor: str, verb: str, summary: str, ref_id: str | None = None) -> None:
    db.add(GroupActivity(group_id=group_id, actor=actor, verb=verb, summary=summary[:400], ref_id=ref_id))


def recent_activity(db: Session, user, limit: int = 40) -> list[dict]:
    """Feed across the groups the user can see, so a manager follows the team's progress."""
    eff = effective_group_ids(db, user.username)
    rows = db.scalars(select(GroupActivity).order_by(GroupActivity.ts.desc()).limit(300)).all()
    names = display_names(db, {r.actor for r in rows})
    out = []
    for r in rows:
        if is_superuser(user) or r.group_id is None or r.group_id in eff or r.actor == user.username:
            out.append({"actor": r.actor, "actor_name": names.get(r.actor, r.actor), "verb": r.verb,
                        "summary": r.summary, "ts": r.ts.isoformat(), "group_id": r.group_id})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- following teammates

def teammates(db: Session, user) -> list[str]:
    """Everyone who shares at least one (effective) group with the user — the people you can follow."""
    eff = effective_group_ids(db, user.username) | member_group_ids(db, user.username)
    if not eff:
        return []
    owners = set(db.scalars(select(GroupMembership.member_owner).where(GroupMembership.group_id.in_(eff))))
    owners.discard(user.username)
    return sorted(owners)


def member_work(db: Session, user, target: str, tz_offset_min: int = 0) -> dict:
    """A teammate's work status the viewer is allowed to see: their open/assigned tasks that the
    viewer can read, plus (only if the teammate opted in to sharing) their live focus for today."""
    eff = effective_group_ids(db, user.username)
    names = display_names(db, {target})
    items = db.scalars(select(WorkItem).where(WorkItem.assignee_owner == target, WorkItem.status != "done")).all()
    projs = {p.id: p for p in db.scalars(select(Project).where(Project.id.in_({it.project_id for it in items if it.project_id})))}
    visible = [item_out(it, projs.get(it.project_id), user, eff, names) for it in items if _item_access(user, it, projs.get(it.project_id), eff)["read"]]
    by_status = {s: sum(1 for v in visible if v["status"] == s) for s in STATUSES}
    done_7d = db.scalar(select(func.count()).select_from(WorkItem).where(
        WorkItem.assignee_owner == target, WorkItem.status == "done", WorkItem.done_at >= _now() - timedelta(days=7))) or 0

    focus = None
    prof = db.get(CompanionProfile, target)
    shares = bool(prof and getattr(prof, "share_work", False))
    # The viewer may see the live focus only if the teammate opted in AND they actually share a group.
    if shares and (is_superuser(user) or set(member_group_ids(db, target)) & (eff | member_group_ids(db, user.username))):
        from app import companion
        pol = companion.policy(db)
        if companion.tracking_active(prof, pol):
            day = companion.today_summary(db, target, tz_offset_min)
            focus = {"now": day.get("now"), "by_category": day.get("by_category", [])[:4], "tracked_seconds": day.get("tracked_seconds", 0)}

    return {"owner": target, "name": names.get(target, target), "shares_work": shares, "focus": focus,
            "open_tasks": visible[:50], "by_status": by_status, "done_7d": done_7d}
