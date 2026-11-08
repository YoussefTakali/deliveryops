# 04 — Frontend

Angular 21 SPA under `frontend/`. Standalone components, signals, and the new control‑flow
(`@if/@for/@switch/@let`). Lazy‑loaded routes. UI language is French.

## Layout
```
frontend/src/app/
  app.ts             root shell: top navbar + sidebar nav, theme, notification inbox
  app.routes.ts      lazy route table
  app.config.ts      providers (router with component input binding, http, etc.)
  api.ts             Api + Session services (delivery platform HTTP + current user)
  auth.ts            auth state/flow (mode-aware)
  microsoft-auth.ts  MSAL wrapper (ssoSilent, token acquisition, admin consent)
  desktop.ts         window.deliveryDesktop bridge wrapper (available/automation/companion)
  companion.service.ts   companion state (chat, activity, suggestions, tasks)
  deliverables.service.ts  livrables (templates + generate → Blob)
  team.service.ts    roles/permissions/groups/projects
  native-notify.ts   OS toasts via the desktop bridge (with browser fallback)
  local-indexer.ts   continuous local file indexing (desktop)
  voice-segmenter.ts call assistant voice activity segmentation
  theme.ts shared.ts   theme cycling; shared UI (Chart, RecCard) + helpers
  pages/             one component per screen (+ pages/perimeter/* tabs)
  automations/       runner.ts (execution engine), template.ts, dataset.ts, models.ts, describe.ts, automation-api.ts, drafts.ts, template.spec.ts, dataset.spec.ts
```

## Routing (`app.routes.ts`)
Top‑level: `''` portfolio, `companion`, `team` (redirects to `workspace`; the old groups/kanban page was removed as redundant with delivery projects), `admin/companion`, `approvals`, `audit`,
`observability`, `onboarding`, `admin/users` | `admin/microsoft-users`, **`admin/roles`** (create
roles + permission matrix), **`projects`** (delivery projects list), **`pr/:projectId`** (the project
workspace — `pages/project/`: `shell.ts` hero + KPIs + tabs sharing a component-level `ProjectStore`;
children `''` Vue d'ensemble (périmètre health, next steps computed from real state, team, technologies),
`perimetres`, `equipe` (platform users or manual people), `livrables` (catalogue picks + project-only
livrables), `plan` (plan de charge)), `microsoft`, `workspace`, `local-files`, `call-assistant`, `download`,
`automations` (+ `/new`, `/recorder`, `/:id`), `runs/:runId`.

New pages: **`livrables/catalogue`** (company catalogue; `livrables` redirects there),
**`livrables/nouveau`** + **`livrables/:id`** (the livrable designer; `?project=<id>` designs a
project-only livrable), **`admin/governance`** (Gouvernance IA, agents vs AI features grouped). The
sidebar's **Supervision** section sits right under Pilotage (Observabilité IA, Journal d'audit,
Gouvernance IA); **Catalogue de livrables** is under Administration (`deliverables.catalog`). A suspended user sees an « Accès suspendu » screen (403 on `/api/me`).

Shared UI building blocks: **`<app-choices>`** (`choices.ts` — a clarifying question with option cards,
the AI's « Recommandé » option badged and pre-selected via `seedRecommended`, plus an always-visible
« Autre » field), **`<app-debug-ai>`** (`debug-ai.ts` — shows an error with « Déboguer avec l'IA », which
hands the error + what the user was doing to the companion via `Companion.debug()`), and
**`errorText()`** (`http-error.ts` — reads FastAPI's `detail`, including from Blob download errors).

The **sidebar** groups its périmètres under their delivery project (a project heading links to
`pr/:projectId`; ungrouped périmètres come last). A floating **companion dock** (`companion-dock.ts`,
mounted in the shell) can be shown/hidden anytime via a bottom‑right button — it reuses the app‑wide
`Companion` service so its chat/history is shared with the full `/companion` page.

Perimeter shell `p/:perimeterId` with child tabs: overview (Pilotage), `copilot`, `knowledge`,
`meetings`, **`equipe`** (staffing from the competence matrix), **`livrables`**, `billing` (+ plan vs réel), `capacity`, `incidents`, `settings` (readiness score, CSV/Excel imports, connectors, contract rules, and **attaching the périmètre to one of its client's projects** — `PUT /api/perimeters/{id}/project`). The Pilotage tab (overview) shows the périmètre **lifecycle tracker**. The router uses
**component input binding**, so a tab reads the route param via `perimeterId = input.required<string>()`.

## Services
- `Api` / `Session` — delivery platform data and the signed‑in user (permissions via `can(...)`).
- `Companion` — root singleton; drains desktop activity, polls `/state`, raises toasts for offers,
  streams chat/briefs, and plans/runs agentic tasks.
- `Deliverables` — lists templates and posts answers to get the generated Office file as a Blob.
- `Team` — roles, permissions, groups (Unix‑style bits), projects and the task board.
- `AutomationRunner` (`automations/runner.ts`) — the client‑side execution engine for automations and
  companion tasks: renders steps, per‑step `flow.confirm`, hash‑approved PowerShell, live log, and the
  dataset loop (`{{row.Col}}`). See [features/automations.md](features/automations.md).

## The companion page (example of the patterns)
`pages/companion.ts` shows the house patterns: signals for state, an inline agentic **task** flow
("Faire") with a one‑question‑at‑a‑time stepper (option cards + free‑text "Autre"), an error+retry
load state, and reuse of `AutomationRunner` for execution. See [features/companion.md](features/companion.md).

## Theming & icons
- Colors are CSS variables that auto‑adapt to light/dark (`--accent`, `--surface`, `--border`, …).
- Icons are **inline outline SVGs** in the app's style (24×24, `currentColor` stroke). The AI
  surfaces (companion, automations editor, notifications) use this set — no emoji.

## Build & type-check
```bash
cd frontend && npm run build        # AoT build = full type-check
npm run test:automations            # pure-node unit tests: template.ts + dataset.ts
```
