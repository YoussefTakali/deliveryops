# 03 — Backend

FastAPI app under `backend/app/`. Entry point `main.py` builds the app, includes routers, runs
startup (`init_db`, schema check, seed in dev), and mounts middleware.

## Layout
```
backend/app/
  main.py            app factory, router registration, startup
  config.py          Settings (pydantic) — all env vars & defaults
  db.py              engine, SessionLocal, Base, init_db + migration guard
  models.py          all ORM tables (see 06-data-model.md)
  auth.py            User resolution for all auth modes, require_permission()
  llm.py             OpenAI-compatible client: structured(), stream_text(), JSON repair, concurrency
  knowledge.py       RAG: parsing, chunking, embeddings, hybrid search, citations
  reindex.py         (re)compute embeddings for all chunks
  middleware.py      rate limiting, security headers, request logging, LLM quota
  audit.py telemetry.py notify.py watch.py   platform services
  permissions.py rbac.py team.py             RBAC + teams/groups logic
  microsoft_graph.py connectors.py transcripts.py   integrations
  companion.py       work companion logic (memories, nudges, tasks, stuck detection)
  agents/            delivery agents + orchestrator (LangGraph)
  analytics/         deterministic number-crunching (billing, capacity, incidents, kpi, scope)
  automations/       record→replay engine brain (see features/automations.md)
  deliverables/      COPIL/matrice generation (see features/deliverables.md)
  seed/              demo data (dev only)
  routes/            one module per API area, each exposes `router`
```

## Routers (`/api/...`)
Registered in `main.py`. Common ones: `assistant`, `automations`, `companion`, `deliverables`,
`team`, `connectors`, `copilot`, `download`, `exports`, `live_assistant`, `microsoft`,
`notifications`, `observability`, `onboarding`, `skills`, plus auth‑mode‑specific admin routers
(`auth`/`rbac_admin` native, `users` keycloak, `rbac_admin`+`microsoft_admin` microsoft).

Every route depends on `auth.require_permission("...")` (or a lighter authenticated dependency) and is
owner/client/perimeter‑scoped. Endpoints return 404 (not 403) for objects outside the caller's scope.

## The LLM layer (`llm.py`)
- One client pointed at `LLM_BASE_URL` with `LLM_API_KEY`; model **tiers** chosen per call:
  - `reasoning` (exec summaries, COPIL narrative), `agent` (extraction/analysis/copilot),
    `fast` (routing, live answers), `vision` (image), optional `search`.
  - Defaults in `config.py` (e.g. `meta/llama-3.3-70b-instruct`, `…8b-instruct`, `…vision-instruct`).
- `structured(schema=…, fallback=…)` returns `(result, used)` — validates/repairs JSON, and on
  failure returns the caller's deterministic `fallback` with `used=False`.
- `stream_text(...)` streams tokens (used by the companion chat/briefs over SSE).
- Concurrency‑limited (`LLM_MAX_CONCURRENCY`, free tiers are rate‑limited); JSON mode configurable
  (`LLM_JSON_MODE`: `guided_json`/`json_schema`/`json_object`/`prompt`); tool mode configurable
  (`LLM_TOOL_MODE`) for models without native tool calling.

## Embeddings & knowledge
- `EMBEDDING_PROVIDER`: `hash` (deterministic, offline default), `fastembed` (local ONNX, real
  semantics), or `api` (OpenAI‑compatible embeddings endpoint). `embedding_dim` must match the model;
  changing it requires `python -m app.reindex`.
- `knowledge.py` does parse → chunk → embed → **hybrid search** (IDF‑weighted lexical + dense cosine
  + boosts, optional LLM rerank) with an exact‑word guarantee and citations. See
  [features/knowledge-and-search.md](features/knowledge-and-search.md).

## Config & environment
`config.py` (`Settings`) is the single source of truth. Key groups: LLM (`LLM_*`), embeddings
(`EMBEDDING_*`), STT/diarization (`STT_*`, `SPEAKER_*`), auth (`AUTH_MODE`, `AUTH_SECRET`, Keycloak,
Microsoft), watch (`WATCH_*`), database (`DATABASE_URL`), `ENVIRONMENT`. See
[07 — Setup & deployment](07-setup-and-deployment.md) and `.env.example`.

## Migrations
Alembic under `backend/alembic/`. Production runs `alembic upgrade head` (see `docker-entrypoint.sh`)
and `db.py` refuses to serve a schema that doesn't match the code. Dev uses `create_all`.

## Tests
`backend/tests/` (pytest). Notable: `test_automations.py`, `test_faithful.py`, `test_vision.py`,
`test_companion.py`, `test_deliverables.py`, `test_team.py`, `test_auth.py`, `test_microsoft.py`,
`test_security.py`, `test_production_config.py`, `test_middleware.py`, `test_platform.py`.

Run against the working tree using the backend image (the image has deps but not the tests):
```bash
docker run --rm --entrypoint python \
  -v "$(pwd)/backend:/src" -w /src deliveryops-backend \
  -m pytest -q -p no:cacheprovider
```
Two pre‑existing failures are environment‑specific (`test_microsoft` config default; `test_platform`
`copil` workflow) and unrelated to the companion/automation/deliverables features.
