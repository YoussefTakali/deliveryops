# 06 — Data model

All tables are SQLAlchemy models in `backend/app/models.py` (dev creates them via `create_all`;
production uses Alembic migrations under `backend/alembic/versions/`). Grouped by domain below.

> Note (dev gotcha): `create_all` only creates *missing tables* — it never adds a new **column** to
> an existing table. New columns need a migration or a manual `ALTER TABLE` in dev. See
> [08 — Security & privacy](08-security-and-privacy.md) and the gotchas memory.

## Delivery core
| Table | Purpose |
|---|---|
| `Client` | A client organisation. |
| `DeliveryProject` | A client engagement a manager runs (Client → **DeliveryProject** → Perimeter). Holds the chosen technologies and status; groups one or more périmètres and a team. |
| `ProjectMember` | A user on a project's team (members are users). They work across the project's périmètres; freed for reassignment when the project is done. |
| `ProjectDeliverable` | A **project-level** deliverable (the plan de charge, which spans all the project's périmètres): structured draft + produced file. (Matrice/COPIL stay per-périmètre in `PerimeterDeliverable`.) |
| `Perimeter` | A delivery scope — the central object (client, **project**, apps, team, documents). `project_id` is a nullable FK to `DeliveryProject`. |
| `Application` / `Dependency` | Applications in a perimeter and their dependency graph. |
| `Document` / `Chunk` | Ingested documents and their embedded chunks (pgvector) for RAG. |
| `TeamMember` | People staffed on a perimeter (delivery data, distinct from auth users). |
| `Ticket` / `Timesheet` / `BacklogItem` | Incident/change tickets, time entries, backlog. |
| `Meeting` | Meetings and their transcripts/minutes. |

## Agents & platform
| Table | Purpose |
|---|---|
| `AgentRun` | One orchestrated run (plan, status, checkpoint). |
| `Recommendation` | A produced recommendation with sources/confidence, awaiting human decision. |
| `Conversation` | Copilot/assistant conversations. |
| `AuditLog` | Every impactful action (who/what/when), append‑only. |
| `LLMCall` | Telemetry per LLM call (tokens, latency, cost, cache). |
| `Notification` | In‑app notifications (watch, results, AI suggestions). |
| `Connector` | Jira/ServiceNow/demo connector config (secrets by env‑var name only). |

## Auth & RBAC
| Table | Purpose |
|---|---|
| `Role` / `RolePermission` | Dynamic roles and the permissions granted to each. |
| `AuthUser` / `RefreshToken` | Native‑auth users and refresh tokens (auth_mode=native). |
| `MicrosoftUser` | Microsoft sign‑ins and their assigned roles (auth_mode=microsoft). |
| `AgentSkill` | Registered agent skills/capabilities. |

## Automations
| Table | Purpose |
|---|---|
| `Automation` | A saved automation: steps (JSON), inputs, trigger, approved script hashes, source. |
| `AutomationRun` | One execution (status, dry‑run, context, log, error). |
| `AutomationFile` | Files produced/consumed by runs and recorder keyframes (bytes, deferred). |
| `AutomationRecording` | A screen recording: events, narration, keyframes, analysis. |
| `DeliverableTemplate` | Per‑company overrides for a built‑in deliverable template (COPIL, matrice, plan de charge). |
| `PerimeterDeliverable` | A generated deliverable of a périmètre: structured draft + produced file (the plan de charge reuses the latest matrice; agents can read it). |

## Companion
| Table | Purpose |
|---|---|
| `CompanionProfile` | Per‑user profile: role, tone, quiet hours, tracking opt‑in, learned nudge weights, `share_work`, cached agenda. |
| `CompanionMemory` | Inspectable plain‑text memories (preferences, facts, people, projects). |
| `ActivityEvent` (`companion_activity`) | Opt‑in foreground‑window focus sessions. |
| `CompanionSuggestion` | Proactive offers made and the user's decision (the learning signal). |
| `CompanionMessage` | The companion conversation. |
| `CompanionPolicy` | Org‑wide governance (tracking allowed, retention, title capture, caps). |

## Team, groups & projects
| Table | Purpose |
|---|---|
| `Group` / `GroupMembership` | Nested groups (Linux‑directory‑like) and their members. |
| `Project` / `WorkItem` | Team projects and their task‑board items. |
| `GroupActivity` | Activity/feed for a group. |

## Competences & staffing
| Table | Purpose |
|---|---|
| `UserSkill` | The competence matrix: one skill (level 1–5, category) per user. |
| `TeamMemberUser` | Links a périmètre's staffed `TeamMember` to the platform user it represents. |

## User-defined livrables, manual team members, AI governance
| Table | Purpose |
|---|---|
| `CustomDeliverable` | A livrable type: inputs, output format, sections, style rules, examples. `project_id` NULL = company catalogue, else project‑only. |
| `ProjectLivrable` | The catalogue livrables a project uses (`project_id`, `key` = built‑in key or `custom:<id>`); none = all built‑ins. |
| `ExternalPerson` | A team member entered manually (no account); owner key `ext:<id>` everywhere. |
| `GovernancePolicy` | Org AI policy (row `org`): AI switch, default daily token quota, per-agent daily caps. |
| `UserGovernance` | Per user (keyed by username): app blocked, AI blocked, quota override, note. |
| `UsageLedger` | One row per LLM call: user, agent, purpose, model, tokens, status — basis for quotas and per-user views. |

## Migrations (order)
`e43a6ae389e3` initial → `67ab9b866b54` RBAC → `a1b2c3d4e5f6` agent skills →
`b2c3d4e5f6a7` microsoft users → `c3d4e5f6a7b8` nullable notification perimeter →
`d4e5f6a7b8c9` automations → `e5f6a7b8c9d0` companion → `f6a7b8c9d0e1` teams/groups (+ `companion.share_work`) → `a7b8c9d0e1f2` user_skills / team_member_users / perimeter_deliverables → `b8c9d0e1f2a3` delivery_projects / project_members (+ `perimeters.project_id`) → `c9d0e1f2a3b4` project_deliverables → `d0e1f2a3b4c5` custom_deliverables / external_people / governance_policy / user_governance / usage_ledger (new tables only).

> `perimeters.project_id` is a **new column on an existing table**, so in a running dev DB (where
> `create_all` never alters tables) run `ALTER TABLE perimeters ADD COLUMN project_id VARCHAR(40);`
> or apply the migration. The two new tables are created by `create_all` as usual.
