"""DeliveryOps as an MCP server, so external assistants (desktop AI assistants, VS Code, Copilot
Studio...) can query perimeters with the same tools and access rules as the in-app Copilot.

    python -m app.mcp_server                 # stdio
    python -m app.mcp_server --http 8765     # streamable HTTP

The acting user comes from DELIVERYOPS_USER (dev personas); it scopes visible clients exactly like
the web API. Write tools (propose_action, start_workflow) still land in the human validation queue.
"""

import json
import os
import sys

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from app.agents.copilot import Toolbox
from app.auth import DEV_USERS
from app.db import SessionLocal, init_db
from app.models import Perimeter

server = MCPServer(
    name="deliveryops",
    title="DeliveryOps",
    instructions="Delivery data for AMS/TMA perimeters: KPIs, SLA, tickets, knowledge base, capacity, pre-billing. Call list_perimeters first to get perimeter ids.",
)


def _user():
    return DEV_USERS.get(os.environ.get("DELIVERYOPS_USER", "sarah"), DEV_USERS["sarah"])


def _call(perimeter_id: str, tool: str, **args) -> str:
    user = _user()
    with SessionLocal() as db:
        perimeter = db.get(Perimeter, perimeter_id)
        if not perimeter or not user.can_see(perimeter.client_id):
            return json.dumps({"error": "perimeter not found"})
        return json.dumps(Toolbox(db, perimeter, f"mcp:{user.username}").run(tool, {k: v for k, v in args.items() if v is not None}), ensure_ascii=False, default=str)


@server.tool()
def list_perimeters() -> str:
    """List the application perimeters visible to the current user, with their client."""
    user = _user()
    with SessionLocal() as db:
        return json.dumps([{"id": p.id, "name": p.name, "client": p.client.name, "description": p.description}
                           for p in db.scalars(select(Perimeter)).all() if user.can_see(p.client_id)], ensure_ascii=False)


@server.tool()
def search_documents(perimeter_id: str, query: str) -> str:
    """Search the perimeter knowledge base (transfer files, DAT, DEX, contract, minutes). Returns cited excerpts."""
    return _call(perimeter_id, "search_documents", query=query)


@server.tool()
def get_service_kpis(perimeter_id: str) -> str:
    """Service scorecard vs contract targets: SLA per priority, MTTR, backlog, reopen and change failure rates, trends."""
    return _call(perimeter_id, "get_service_kpis")


@server.tool()
def list_tickets(perimeter_id: str, status: str | None = None, kind: str | None = None, application: str | None = None, priority: str | None = None, limit: int = 20) -> str:
    """List tickets. status: open|in_progress|closed; kind: incident|request|change|problem; priority: P1..P4."""
    return _call(perimeter_id, "list_tickets", status=status, kind=kind, application=application, priority=priority, limit=limit)


@server.tool()
def find_similar_incidents(perimeter_id: str, description: str) -> str:
    """Resolved tickets similar to a description, with their resolution."""
    return _call(perimeter_id, "find_similar_incidents", description=description)


@server.tool()
def get_application_map(perimeter_id: str) -> str:
    """Applications with criticality, risk score, dependencies and single points of failure."""
    return _call(perimeter_id, "get_application_map")


@server.tool()
def get_capacity_forecast(perimeter_id: str, incident_growth_pct: float = 0, add_fte: float = 0) -> str:
    """8-week load forecast vs capacity with an optional what-if scenario."""
    return _call(perimeter_id, "get_capacity_forecast", incident_growth_pct=incident_growth_pct, add_fte=add_fte)


@server.tool()
def get_prebilling(perimeter_id: str, year: int | None = None, month: int | None = None) -> str:
    """Pre-invoice computed from contract rules, with anomalies. Defaults to last month."""
    return _call(perimeter_id, "get_prebilling", year=year, month=month)


@server.tool()
def estimate_change_request(perimeter_id: str, request: str) -> str:
    """P50/P80 effort estimate for a change request from similar delivered changes."""
    return _call(perimeter_id, "estimate_change_request", request=request)


@server.tool()
def propose_action(perimeter_id: str, title: str, detail: str, impact: str = "medium") -> str:
    """File an action proposal in the human validation queue (nothing executes until approved)."""
    return _call(perimeter_id, "propose_action", title=title, detail=detail, impact=impact)


def main() -> None:
    init_db()
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        server.run("streamable-http", port=port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
