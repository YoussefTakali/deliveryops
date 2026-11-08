# Feature — Team, roles, permissions & groups

A dynamic access model (no fixed hierarchy) plus a Linux‑filesystem‑style group model, with team
projects and a task board so teammates (who are users) can be followed.

- Backend: `backend/app/rbac.py`, `backend/app/permissions.py`, `backend/app/team.py`,
  `backend/app/routes/rbac_admin.py`, `backend/app/routes/team.py`.
- Frontend: `frontend/src/app/team.service.ts` (people directory only), `frontend/src/app/pages/my-skills.ts` (in « Mon espace »),
  admin pages (`admin-users.ts` / `microsoft-users.ts`).
- Tables: `Role`, `RolePermission`, `Group`, `GroupMembership`, `Project`, `WorkItem`,
  `GroupActivity` (+ `AuthUser`/`MicrosoftUser` depending on auth mode).

## Dynamic RBAC
- An **admin creates roles**, grants each role its **permissions**, and assigns people a role — rather
  than a fixed org chart. Permissions gate every API (`require_permission("...")`).
- System roles are seeded (e.g. director / delivery_manager / consultant) and are backfilled with new
  permissions as features land (e.g. `companion.use/activity/admin`, `automation.use`).
- **The UI for this is `/admin/roles`** (`pages/admin-roles.ts`, gated by `roles.manage`): create a
  role, tick its permissions in a **matrix grouped by category** (from `GET /api/admin/permissions`),
  rename/delete. Roles are then assigned to users on the Utilisateurs page. This is how a team is
  built dynamically — a role is a reusable bundle of permissions granted to whoever holds it. (The
  RBAC admin router is mounted in `native`/`microsoft` auth modes.)

## Delivery projects (a manager runs one or more)
- **Company → Client → `DeliveryProject` (a manager) → Perimeter.** A manager can run several
  projects (one per client). Projects: `GET/POST /api/projects`, `GET/PUT /api/projects/{id}`,
  `POST/DELETE /api/projects/{id}/members`, and `PUT /api/perimeters/{id}/project` to attach/detach a
  périmètre. Access stays **client‑scoped** (a manager on another client gets 404).
- **The project team = `ProjectMember` (members are users).** They can work on any périmètre under
  the project; **closing the project** (`status: done`) frees them for reassignment elsewhere.
- Frontend: **Projets** list (`pages/projects.ts`, grouped by client) and a **project page**
  (`pages/project.ts`: périmètres + team with skills, add/remove members, edit technologies, close).
  New périmètres pick their project in onboarding; the sidebar groups périmètres by project.

## Groups (Linux-directory-like) with Unix-style permission bits
- **Nested groups** form a tree (like directories). Group nesting expresses manager oversight:
  `effective_group_ids` includes descendants, so a parent‑group manager sees children.
- **Permission bits** per object: owner / group / others × read=4 / write=2 / admin=1 (like Unix mode
  bits). `owner` is the user's `username` (= email across auth modes).

## Competence matrix & staffing a périmètre (`app/staffing.py`)
- **Members are users.** Each user declares their **skills** (technology / functional / language /
  method, level 1–5) in « Mon espace › Mes compétences » → `UserSkill` (global, one matrix).
- **Skill match**: `POST /api/team/skills/match {technologies}` ranks people by level‑weighted match,
  then by **availability** (hours already staffed on their other périmètres, absences in the next 4
  weeks) — a manager on two clients is never double‑booked blindly.
- **Staffing**: the périmètre's **Équipe** tab staffs people from that ranking (`POST
  /api/perimeters/{id}/staff`) → creates the `TeamMember` (skills copied, profile → rate card, weekly
  capacity) linked to the user via `TeamMemberUser`, so capacity/billing analytics work unchanged.
- The plan de charge assigns work using this matrix (see [deliverables.md](deliverables.md)).

## Team members without an account (manual entry)
Not every company gives the app to every employee, so a manager can add a team member **by hand**
(name, e-mail, profile, weekly capacity, skills) as well as picking an existing user. Such a person is
an `ExternalPerson` whose owner key everywhere is **`ext:<id>`** — so the project team, the competence
matrix and skill matching, périmètre staffing, the plan de charge and the agents' context all work for
them unchanged (no ALTER on any existing table). The same e-mail reuses the same person. UI: project
page « Utilisateur existant | Saisie manuelle », périmètre Équipe tab « Ajouter quelqu'un qui n'a pas
de compte »; they show a « sans compte » badge and their real name (never the `ext:` key).
API: `POST /api/projects/{id}/members {manual: {...}}`, `POST /api/perimeters/{id}/staff/manual`,
`GET /api/team/external`.

## Projects & task board
- `Project` and `WorkItem` power a team task board; teammates' work can be **followed** (opt‑in via the
  companion's `share_work`, so a manager who shares a group can see live focus).
- `GroupActivity` is the group's feed.

## Enterprise governance
Beyond roles: an admin with `governance.manage` can suspend a user, block their AI use, set daily
token quotas and per-agent caps — see [governance.md](governance.md).

## How access is decided (mental model)
1. The caller resolves to a `User` (permissions + group memberships) in `auth.py`.
2. Endpoint requires a permission (RBAC) *and* the object's mode bits allow the caller (owner / a
   shared group / others).
3. Out‑of‑scope objects return **404**, never leaking existence.

## Tested
`backend/tests/test_team.py` (roles/permissions, nested groups, mode bits, share_work consent gating).


## Removed as redundant (2026-09-23)
The « Équipe & projets » page (nested groups, an internal kanban of "projets" with Unix-style permission bits,
"Mes tâches", teammates' activity, "Partager mon activité") duplicated the delivery **Projets** under the same name
while nothing else in the app read its data. Its UI was removed; `/team` redirects to « Mon espace », where
« Mes compétences » now lives. The backend tables/routes (`Group`, `Project`, `WorkItem`, `GroupActivity`,
`routes/team.py`) are kept for now so no data is lost; `/api/team/directory` and `/api/team/skills*` are still used.
Teams are managed once, on the project (« Équipe » tab: users or people entered by hand, plus the project
technologies); a périmètre's « Équipe » tab only assigns project members (profile, weekly capacity), ranked by
fit with the project technologies. A périmètre outside any project keeps a free search + manual entry.
