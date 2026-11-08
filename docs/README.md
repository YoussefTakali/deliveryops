# DeliveryOps — Documentation

DeliveryOps is an enterprise, agentic-AI platform for Big‑4‑style consulting delivery (AMS/TMA).
It combines a multi‑agent delivery platform (per‑project "périmètres", agents, exports, governance)
with a Windows **work companion** and a **desktop automation** engine — all running on free,
OpenAI‑compatible LLMs (NVIDIA NIM by default), with a privacy‑by‑design and human‑in‑the‑loop stance.

This folder documents the whole system. Start here, then dive into the area you need.

## Map of the docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Overview](01-overview.md) | What the product is, who it's for, the capability catalogue |
| 02 | [Architecture](02-architecture.md) | Components, data flow, tech stack, cross‑cutting design rules |
| 03 | [Backend](03-backend.md) | FastAPI structure, modules, routes, LLM layer, config/env, tests |
| 04 | [Frontend](04-frontend.md) | Angular structure, routing, pages, services, theming/icons |
| 05 | [Desktop](05-desktop.md) | Electron shell: main/preload/bridges, tray, activity, automation, vision |
| 06 | [Data model](06-data-model.md) | The ORM tables, grouped by domain |
| 07 | [Setup & deployment](07-setup-and-deployment.md) | Local dev, Docker, env vars, migrations, seeding |
| 08 | [Security & privacy](08-security-and-privacy.md) | Auth modes, RBAC, isolation, privacy guarantees, prod guards |
| 09 | [Glossary](09-glossary.md) | Domain terms (périmètre, COPIL, TMA, criticité…) |

### Feature guides ([features/](features/))
- [Delivery platform: agents & orchestrator](features/agents-and-orchestrator.md)
- [Knowledge & search (RAG)](features/knowledge-and-search.md)
- [Work companion](features/companion.md)
- [Desktop automation (record → replay + vision)](features/automations.md)
- [Livrables (COPIL, matrice de couverture)](features/deliverables.md)
- [Team, roles, permissions & groups](features/team-and-permissions.md)
- [Call / meeting assistant](features/call-assistant.md)
- [Microsoft 365 & connectors](features/microsoft-and-connectors.md)

## The one-paragraph mental model

A **frontend** (Angular) talks over HTTP to a **backend** (FastAPI + PostgreSQL/pgvector). The
backend hosts the delivery platform (agents, orchestrator, exports, knowledge base), the **companion**
and **automations** APIs, and integrations (Microsoft Graph, Jira/ServiceNow). An optional **Electron
desktop shell** wraps the same web app and adds native powers the browser can't have: reading local
files, driving other Windows apps (automation/vision), a call assistant, activity awareness, and a
system‑tray background mode. All LLM work goes through one client (`backend/app/llm.py`) pointed at an
OpenAI‑compatible endpoint, and every LLM feature has a deterministic fallback so the platform runs
end‑to‑end even with no model available.

## Conventions used in these docs
- Paths are relative to the repo root, e.g. `backend/app/companion.py`.
- "périmètre" = a delivery project/scope (the central business object).
- French is the product's UI language; identifiers and code are English.
- Where a doc says *verified*, there are tests under `backend/tests/` or a build check covering it.
