"""Microsoft Graph endpoints. The SPA (MSAL) forwards its delegated Graph access token; we relay it
to Graph via app.microsoft_graph. Available whenever MICROSOFT_TENANT_ID / MICROSOFT_CLIENT_ID are
set, independent of AUTH_MODE — so Microsoft data can be a connector even under Keycloak auth."""

from fastapi import APIRouter, Depends, HTTPException

from app import microsoft_graph
from app.auth import graph_token
from app.config import get_settings

router = APIRouter(prefix="/api/microsoft")
settings = get_settings()


def _guard() -> None:
    if not settings.microsoft_enabled:
        raise HTTPException(501, "Microsoft integration is not configured (MICROSOFT_TENANT_ID / MICROSOFT_CLIENT_ID)")


def _relay(fn, token: str):
    try:
        return fn(token)
    except microsoft_graph.GraphError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (401, 403) else 502, exc.detail) from exc


@router.get("/me")
def microsoft_me(token: str = Depends(graph_token)):
    _guard()
    return _relay(microsoft_graph.me, token)


@router.get("/overview")
def microsoft_overview(token: str = Depends(graph_token)):
    """Aggregate the signed-in user's data across Microsoft tools (Outlook, Teams, OneDrive/
    SharePoint, Planner/To Do, presence). Per-source graceful degradation — see microsoft_graph."""
    _guard()
    return _relay(microsoft_graph.overview, token)
