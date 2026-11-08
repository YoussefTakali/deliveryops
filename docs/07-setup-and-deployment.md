# 07 — Setup & deployment

## Ports (local dev / demo)
Frontend **4300**, API **8100**, Keycloak **8280**, Postgres **5433**, MCP **8765**. The stack is
self‑contained: compose project `deliveryops`, `deliveryops-*` containers, own network and volume.

## Run with Docker (recommended)
```bash
cp .env.example .env            # then fill in secrets (see below)
docker compose up -d --build
# frontend → http://localhost:4300 , API → http://localhost:8100
```
Services (`docker-compose.yml`): `postgres` (pgvector), `keycloak`, `backend`, `mcp`, `frontend`.
`docker-compose.prod.yml` is the hardened production overlay.

> Rebuild after code changes: the containers run the **built image**, not your working tree. Rebuild
> the affected image (`docker compose build backend` / `frontend`) and recreate to see changes.

## Run locally without Docker
- Backend: Python 3.12, `pip install -r backend/requirements.txt`, set `DATABASE_URL` (SQLite works in
  dev), `uvicorn app.main:app`.
- Frontend: `cd frontend && npm install && npm start` (dev server), or `npm run build` for static.

## Key environment variables (see `.env.example` and `config.py`)
| Var | Meaning |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` | OpenAI‑compatible endpoint (default NVIDIA NIM). |
| `LLM_MODEL_REASONING/AGENT/FAST` | Model per tier. |
| `LLM_JSON_MODE` | `guided_json` (NIM) / `json_schema` / `json_object` / `prompt`. |
| `LLM_TOOL_MODE` | `native` or `json` (for models without tool calling). |
| `LLM_MAX_CONCURRENCY` | Cap for rate‑limited free tiers. |
| `EMBEDDING_PROVIDER` | `hash` / `fastembed` / `api` (+ `EMBEDDING_DIM` must match). |
| `AUTH_MODE` | `native` / `dev` / `keycloak` / `microsoft`. |
| `AUTH_SECRET` | JWT signing secret (native). |
| `MICROSOFT_TENANT_ID` / `MICROSOFT_CLIENT_ID` | Entra app registration (microsoft mode / Graph). |
| `WATCH_ENABLED` / `WATCH_INTERVAL_MINUTES` / `WATCH_AI_*` | Proactive watch. |
| `TEAMS_WEBHOOK_URL` | Teams cards for critical notifications. |
| `POSTGRES_PASSWORD` / `KEYCLOAK_ADMIN_PASSWORD` | Infra secrets. |
| `ENVIRONMENT` | `production` enables the misconfiguration guard. |

## Database & migrations
- Dev: tables auto‑created from the models (`create_all`).
- Production: `alembic upgrade head` (via `backend/docker-entrypoint.sh`); the app refuses to boot if
  the DB schema doesn't match the code (`db.py::_check_migrations`).
- Reindex embeddings after changing the embedding model/dim: `python -m app.reindex`.

## Seeding (dev only)
`backend/app/seed/` loads demo clients/perimeters/documents. The production guard blocks demo seed.

## Desktop app
Build the installer from `desktop/` (electron‑builder). Users install it and point it at the API URL
(configurable in‑app). The installer is also downloadable from the in‑app **Download** page.

## Verifying changes
- Backend: run pytest against the working tree using the backend image (see [03 — Backend](03-backend.md)).
- Frontend: `npm run build` (type‑check) and `npm run test:automations` (pure‑node unit tests).
- Desktop JS: `node -c desktop/src/<file>.js` (syntax) — full behaviour needs a real Windows GUI run.
