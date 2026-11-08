# 01 — Overview

## What it is
DeliveryOps assists application‑maintenance / TMA teams across the whole lifecycle of a delivery
**périmètre** (project scope): onboarding, client meetings, pre‑billing, capacity planning, service
governance and steering committees (COPIL). On top of that delivery platform, it adds a personal
**work companion** and a **desktop automation** engine so the same product both *advises* and *acts*.

## Who it's for
- **Consultants / delivery managers**: run agents, generate deliverables, track projects and teammates.
- **Managers / directors**: governance, capacity, approvals, monitoring, roles & permissions.
- **HR / employees**: the companion (organisation, retrieval, drafting) with strong privacy controls.

## Design principles
1. **Numbers are deterministic.** Anything shown to a client (amounts, SLA %, loads, criticité) is
   computed by code in `backend/app/analytics/` (and `app/deliverables/`), never invented by the LLM.
   The LLM extracts, reasons and writes prose — always with a fallback.
2. **Human‑in‑the‑loop.** Impactful actions (recommendations, automation side‑effects, companion
   tasks) block until a human approves; rejections capture a reason; approvals apply their effect.
3. **Privacy by design.** Activity tracking and local‑file access are opt‑in and policy‑gated;
   admin views are aggregate‑only; screenshots never leave the PC.
4. **Runs offline.** Every LLM feature degrades to deterministic behaviour, so demos and tests never
   depend on a live model.
5. **Free tooling only.** OpenAI‑compatible LLMs (NVIDIA NIM free tier by default), local ONNX
   embeddings/STT — no paid SaaS required.

## Capability catalogue
| Area | Capability |
|------|-----------|
| Delivery agents | Onboarding, Meeting intelligence, Billing, Capacity, Governance, Incident/Problem, Estimation, Copilot |
| Orchestration | LangGraph supervisor with dependency ordering + human approval interrupt/resume |
| Knowledge | RAG over documents (parse → chunk → hybrid search → citations) |
| Companion | Context‑aware chat, proactive nudges, "stuck" detection, agentic tasks ("Faire"), learned memories |
| Automation | Record → faithful replay with UI‑Automation + computer vision; datasets (once per row) |
| Livrables | Generate COPIL (.pptx) and matrice de couverture (.xlsx) from the house method |
| Team | Dynamic roles + permissions, nested groups (Unix‑style perms), projects & task board |
| Meetings | Live call assistant (transcribe + answer), Teams transcript import, minutes |
| Integrations | Microsoft 365 (Graph), Jira / ServiceNow / demo connectors |
| Ops | Auth (native/keycloak/microsoft/dev), audit, AI telemetry, notifications, MCP server |

## Surfaces
- **Web app** (Angular) — the primary UI, works in any browser.
- **Desktop app** (Electron) — the web app plus native powers (local files, automation, call
  assistant, activity awareness, tray/background). Ships as a Windows installer.
- **MCP server** — exposes the platform's tools to external MCP-compatible assistants.

See [02 — Architecture](02-architecture.md) for how these fit together.
