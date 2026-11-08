# 02 — Architecture

## Components
```
                         ┌───────────────────────────── Desktop shell (Electron, Windows) ─────────────┐
                         │  main.js · preload.js (contextBridge)                                        │
                         │  local files · automation (UIA + vision) · recorder · call assistant         │
                         │  activity tracking · tray/background · native toasts                          │
                         └───────────────▲───────────────────────────────────────────────▲─────────────┘
                                         │ loads the same web app                          │ IPC bridge
   Browser ─────────────────────────────┴───────────────────────────────────────────────┘
      │  Angular 21 SPA (frontend/)  — signals, standalone components, lazy routes
      │  HTTP + SSE (chat/briefs), MSAL (microsoft mode)
      ▼
   FastAPI (backend/app)  ── OpenAI‑compatible LLM client (llm.py) ──► NVIDIA NIM / vLLM / Azure / …
      ├─ Delivery platform: agents/*, runner.py, orchestrator (LangGraph), exports.py, analytics/*
      ├─ Knowledge (RAG): knowledge.py, reindex.py  (pgvector)
      ├─ Companion: companion.py + routes/companion.py
      ├─ Automations: automations/* + routes/automations.py  (executed by the desktop/frontend runner)
      ├─ Deliverables: deliverables/* + routes/deliverables.py
      ├─ Team/RBAC/groups: team.py, rbac.py, permissions.py + routes
      ├─ Integrations: microsoft_graph.py, connectors.py, transcripts.py
      └─ Platform: auth.py, middleware.py, audit.py, telemetry.py, notify.py, watch.py, mcp_server.py
      ▼
   PostgreSQL + pgvector  (business data, vectors, audit, telemetry, companion, automations, teams)
   Keycloak (OIDC/PKCE)   — only in auth_mode=keycloak
```

## Tech stack
- **Frontend**: Angular 21 (standalone components, signals, control‑flow `@if/@for`), TypeScript,
  MSAL for Microsoft sign‑in. Served as static files (nginx) in prod on port **4300**.
- **Backend**: FastAPI (Python 3.12), SQLAlchemy 2.0 ORM, Alembic migrations, Pydantic. Port **8100**.
- **DB**: PostgreSQL 17 + `pgvector` (dev can use SQLite). Port **5433**.
- **LLM**: OpenAI‑compatible chat completions via the `openai` SDK (`llm.py`). Default NVIDIA NIM.
- **Embeddings/STT**: local ONNX (fastembed, faster‑whisper, WeSpeaker) — no external calls needed.
- **Desktop**: Electron; native automation via PowerShell + Windows UI Automation + WinRT OCR.
- **Auth**: Keycloak 26 (keycloak mode) / Microsoft Entra (microsoft mode) / native JWT / dev.
- **MCP**: a Model Context Protocol server on port **8765** exposing platform tools.

## Request/data flow (typical)
1. The SPA authenticates (mode‑dependent) and calls the API with a bearer token; in microsoft mode it
   also relays a Graph access token in `X‑Graph‑Token` for live Microsoft data.
2. The API resolves the `User` (permissions, client/perimeter access) in `auth.py`, runs the endpoint,
   and records audit/telemetry.
3. LLM work goes through `llm.py` (concurrency‑limited, JSON‑validated with repair, always with a
   fallback). Deterministic numbers come from `analytics/` and `deliverables/`.
4. Long‑running agent work runs through the orchestrator with an **approval interrupt**; the SPA shows
   the pending decision and resumes on approve/reject.
5. Automations and companion tasks are *planned* server‑side but *executed* on the client (browser
   runner + desktop bridge) so they can drive local apps and files safely.

## Cross-cutting design rules
- **Deterministic vs generative split** (see [01](01-overview.md)) — the single most important rule.
- **Owner/client isolation** — data is scoped by user, client and perimeter; endpoints 404 rather
  than leak across tenants.
- **Fallbacks everywhere** — no feature hard‑fails because the LLM/endpoint is down.
- **Opt‑in local powers** — the desktop's file access, activity tracking and automation are explicit
  and revocable; PowerShell runs only exact, hash‑approved scripts.
- **Production guard** — with `ENVIRONMENT=production`, the app refuses to boot misconfigured (dev
  auth, SQLite, demo seed, wildcard CORS, unmigrated schema). See `backend/app/config.py` / `db.py`.

## Where things run
| Concern | Runs in |
|---|---|
| UI, orchestration of automation/companion runs, MSAL | Browser / desktop renderer |
| Business logic, agents, LLM calls, exports, RAG | Backend |
| Local files, driving other apps, OCR/vision, activity, tray | Desktop main process (`desktop/src/*`) |
| Persistence, vectors, checkpoints | PostgreSQL/pgvector |
