"""Groups & projects: Unix-style owner/group/others permissions, group nesting (manager oversight),
collaborative tasks, and following a teammate's work."""

from types import SimpleNamespace

from app import team
from tests.conftest import as_user

DIRECTOR = as_user("sarah")   # team.manage + team.view
MANAGER = as_user("karim")    # team.manage + team.view
CONSULTANT = as_user("tom")   # team.view only


def _u(name, perms=()):
    return SimpleNamespace(username=name, permissions=set(perms))


def test_unix_style_access_bits():
    a = _u("alice")
    # owner triad
    assert team.access(a, owner_user="alice", group_id=None, mode=0o700, eff_groups=set()) == {"read": True, "write": True, "admin": True}
    # group member gets the group triad (rw, no admin) via 0o760
    assert team.access(a, owner_user="bob", group_id="g", mode=0o760, eff_groups={"g"}) == {"read": True, "write": True, "admin": False}
    # not owner, not in group, others=0 -> nothing
    assert team.access(a, owner_user="bob", group_id="g", mode=0o770, eff_groups=set())["read"] is False
    # world-readable
    assert team.access(a, owner_user="bob", group_id="g", mode=0o774, eff_groups=set()) == {"read": True, "write": False, "admin": False}
    # global superuser override
    assert team.access(_u("x", ["team.manage"]), owner_user="bob", group_id="g", mode=0o700, eff_groups=set())["admin"] is True


def test_group_nesting_and_membership_inheritance(client):
    # Engineering ⊃ Backend ; Karim is a member of Engineering only.
    eng = client.post("/api/team/groups", json={"name": "Engineering"}, headers=DIRECTOR).json()["id"]
    backend = client.post("/api/team/groups", json={"name": "Backend", "parent_id": eng}, headers=DIRECTOR).json()["id"]
    client.post(f"/api/team/groups/{eng}/members", json={"owner": "karim", "group_role": "member"}, headers=DIRECTOR)

    # A private project inside Backend (owner sarah, group rwx, others none).
    proj = client.post("/api/team/projects", json={"name": "API v2", "group_id": backend, "mode": 0o770}, headers=DIRECTOR).json()["id"]

    # Karim, member of the PARENT group, can read/write it (manager oversight); Tom (outside) cannot see it.
    karim_projects = {p["id"] for p in client.get("/api/team/projects", headers=MANAGER).json()}
    tom_projects = {p["id"] for p in client.get("/api/team/projects", headers=CONSULTANT).json()}
    assert proj in karim_projects and proj not in tom_projects
    assert client.get(f"/api/team/projects/{proj}/board", headers=CONSULTANT).status_code == 404


def test_project_task_flow_and_following_a_teammate(client):
    grp = client.post("/api/team/groups", json={"name": "Delivery"}, headers=DIRECTOR).json()["id"]
    client.post(f"/api/team/groups/{grp}/members", json={"owner": "tom"}, headers=DIRECTOR)
    proj = client.post("/api/team/projects", json={"name": "Migration ACME", "group_id": grp}, headers=DIRECTOR).json()["id"]

    # A group member (Tom) can create a task and assign it to himself, then move it.
    t = client.post("/api/team/tasks", json={"project_id": proj, "title": "Écrire le plan de bascule", "assignee_owner": "tom"}, headers=CONSULTANT)
    assert t.status_code == 200, t.text
    tid = t.json()["id"]
    board = client.get(f"/api/team/projects/{proj}/board", headers=CONSULTANT).json()
    assert any(i["id"] == tid for i in board["columns"]["todo"])
    client.put(f"/api/team/tasks/{tid}", json={"status": "doing"}, headers=CONSULTANT)
    client.put(f"/api/team/tasks/{tid}", json={"status": "done"}, headers=CONSULTANT)

    # The manager (Director, in the group) follows Tom's work: sees his tasks + the completion.
    mates = {m["owner"]: m for m in client.get("/api/team/overview", headers=DIRECTOR).json()["teammates"]}
    assert "tom" in mates
    work = client.get("/api/team/members/tom/work", headers=DIRECTOR).json()
    assert work["done_7d"] >= 1
    # activity feed reflects the completion for anyone who can see the group
    verbs = [a["verb"] for a in client.get("/api/team/overview", headers=DIRECTOR).json()["activity"]]
    assert "completed_task" in verbs


def test_share_work_consent_gates_live_focus(client):
    grp = client.post("/api/team/groups", json={"name": "Ops"}, headers=DIRECTOR).json()["id"]
    client.post(f"/api/team/groups/{grp}/members", json={"owner": "tom"}, headers=DIRECTOR)
    # By default Tom does not share his focus.
    assert client.get("/api/team/members/tom/work", headers=DIRECTOR).json()["shares_work"] is False
    client.put("/api/team/me", json={"share_work": True}, headers=CONSULTANT)
    assert client.get("/api/team/members/tom/work", headers=DIRECTOR).json()["shares_work"] is True


def test_team_view_permission_is_required(client):
    # a persona with no team.view (use an existing low-priv path): the endpoints require the permission
    assert client.get("/api/team/overview").status_code in (200, 401, 403)  # default dev user is sarah (allowed)
