from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import knowledge, rbac, stt, watch
from app.api import router
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.middleware import BodySizeLimitMiddleware, RateLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware, configure_logging
from app.routes import auth as auth_routes
from app.routes import account, assistant, automations, companion, connectors, copilot, custom_deliverables, deliverables, download, exports, governance, i18n, live_assistant, llm_admin, microsoft, microsoft_admin, notifications, observability, onboarding, policy, projects, rbac_admin, skills, staffing, team, techwatch, users

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.runs_api:
        with SessionLocal() as db:
            rbac.ensure_roles(db)  # roles always exist so switching to native later just works
            if settings.auth_mode == "native":
                if "microsoft" in settings.sign_in_methods:
                    rbac.import_microsoft_users(db)  # coming from AUTH_MODE=microsoft: keep everyone's access
                rbac.ensure_admin(db)
        stt.warm_up()
        knowledge.warm_up()
    if settings.runs_worker:
        watch.start_scheduler()
    yield
    if settings.runs_worker:
        watch.stop_scheduler()


app = FastAPI(
    title="DeliveryOps API", version="0.2.0", description="Agentic AI platform for AMS/TMA delivery teams", lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs", redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Middleware order matters: Starlette wraps the LAST `add_middleware()` call outermost, so CORS
# (added last) sees every response -- including 429s and 5xxs from the layers below it -- and can
# still attach Access-Control-Allow-Origin. Without that ordering, browsers would surface those
# as opaque CORS failures instead of the real status code.
app.add_middleware(BodySizeLimitMiddleware)
if settings.runs_api:
    app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()], allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Dev-User", "X-Graph-Token", "X-UI-Language", "X-UI-Language-Name"], max_age=600,
)

if settings.runs_api:
    app.include_router(router)
    modules = [account, assistant, automations, companion, custom_deliverables, deliverables, governance, i18n, llm_admin, projects, staffing, team, connectors, copilot, download, exports, live_assistant, microsoft, notifications, observability, onboarding, policy, skills, techwatch]
    if settings.auth_mode == "native":
        modules += [auth_routes, rbac_admin]
    elif settings.auth_mode == "keycloak":
        modules += [users]
    elif settings.auth_mode == "microsoft":
        # rbac_admin's role/permission-catalog endpoints are auth-mode-agnostic and reused as-is;
        # its native-user CRUD endpoints are also mounted but harmless here (no AuthUser rows exist
        # in a microsoft-mode deployment, so they just return empty results if ever called).
        # microsoft_admin adds the mode-specific piece: assigning roles to Microsoft sign-ins.
        modules += [rbac_admin, microsoft_admin]
    for module in modules:
        app.include_router(module.router)
