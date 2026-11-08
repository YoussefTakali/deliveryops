"""Ticketing connectors. Each adapter fetches records and maps them to importer rows, so sync
reuses the same validation and upsert logic as file imports. Credentials are read from the
environment variable named in `config.token_env`, never stored in the database."""

import os
import random
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, importer
from app.models import Application, Connector, Notification, Ticket


class ConnectorError(RuntimeError):
    pass


def _secret(config: dict) -> str:
    name = config.get("token_env", "")
    value = os.environ.get(name, "") if name else ""
    if not value:
        raise ConnectorError(f"variable d'environnement {name or '(token_env non renseigné)'} absente")
    return value


# ---------------------------------------------------------------- Jira Cloud

class Jira:
    PRIORITY = {"highest": "P1", "blocker": "P1", "high": "P2", "critical": "P1", "medium": "P3", "low": "P4", "lowest": "P4", "minor": "P4"}
    KIND = {"bug": "incident", "incident": "incident", "story": "change", "task": "request", "sub-task": "request", "problem": "problem", "change": "change", "service request": "request"}

    def __init__(self, config: dict):
        for key in ("base_url", "email", "project_key"):
            if not config.get(key):
                raise ConnectorError(f"paramètre {key} manquant")
        self.config = config
        self.client = httpx.Client(base_url=config["base_url"].rstrip("/"), auth=(config["email"], _secret(config)), timeout=30, headers={"Accept": "application/json"})

    def test(self) -> str:
        r = self.client.get(f"/rest/api/3/project/{self.config['project_key']}")
        r.raise_for_status()
        return f"Projet {r.json().get('name')} accessible"

    def fetch(self, since: datetime | None) -> list[dict]:
        jql = f"project = {self.config['project_key']}"
        if since:
            jql += f" AND updated >= \"{since:%Y-%m-%d %H:%M}\""
        if self.config.get("jql_extra"):
            jql += f" AND ({self.config['jql_extra']})"
        rows, token = [], None
        fields = "summary,description,issuetype,priority,status,assignee,created,resolutiondate,components,labels,timeoriginalestimate"
        while True:
            params = {"jql": jql + " ORDER BY updated ASC", "fields": fields, "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            r = self.client.get("/rest/api/3/search/jql", params=params)
            r.raise_for_status()
            data = r.json()
            rows += [self._map(i) for i in data.get("issues", [])]
            token = data.get("nextPageToken")
            if not token or len(rows) >= 5000:
                return rows

    def _map(self, issue: dict) -> dict:
        f = issue["fields"]
        status_cat = ((f.get("status") or {}).get("statusCategory") or {}).get("key", "new")
        components = [c["name"] for c in f.get("components") or []]
        estimate = f.get("timeoriginalestimate")
        return {
            "ref": issue["key"],
            "kind": self.KIND.get(((f.get("issuetype") or {}).get("name") or "").lower(), "request"),
            "priority": self.PRIORITY.get(((f.get("priority") or {}).get("name") or "medium").lower(), "P3"),
            "application": components[0] if components else "",
            "category": ",".join(f.get("labels") or []),
            "title": f.get("summary") or issue["key"],
            "description": _adf_text(f.get("description")),
            "status": {"new": "open", "indeterminate": "in_progress", "done": "closed"}.get(status_cat, "open"),
            "assignee": ((f.get("assignee") or {}).get("displayName")) or "",
            "opened_at": f.get("created"),
            "resolved_at": f.get("resolutiondate") or "",
            "estimate_h": round(estimate / 3600, 1) if estimate else "",
        }


def _adf_text(node) -> str:
    """Flatten Atlassian Document Format to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    return " ".join(_adf_text(c) for c in node.get("content", [])).strip()


# ---------------------------------------------------------------- ServiceNow

class ServiceNow:
    TABLE_KIND = {"incident": "incident", "change_request": "change", "sc_req_item": "request", "problem": "problem"}

    def __init__(self, config: dict):
        for key in ("instance_url", "username"):
            if not config.get(key):
                raise ConnectorError(f"paramètre {key} manquant")
        self.config = config
        self.tables = config.get("tables") or ["incident"]
        self.client = httpx.Client(base_url=config["instance_url"].rstrip("/"), auth=(config["username"], _secret(config)), timeout=30, headers={"Accept": "application/json"})

    def test(self) -> str:
        r = self.client.get(f"/api/now/table/{self.tables[0]}", params={"sysparm_limit": 1})
        r.raise_for_status()
        return "Instance ServiceNow accessible"

    def fetch(self, since: datetime | None) -> list[dict]:
        rows = []
        for table in self.tables:
            query = self.config.get("query", "")
            if self.config.get("assignment_group"):
                query = "^".join(filter(None, [query, f"assignment_group.name={self.config['assignment_group']}"]))
            if since:
                query = "^".join(filter(None, [query, f"sys_updated_on>={since:%Y-%m-%d %H:%M:%S}"]))
            offset = 0
            while True:
                r = self.client.get(f"/api/now/table/{table}", params={"sysparm_query": query + "^ORDERBYsys_updated_on", "sysparm_limit": 500, "sysparm_offset": offset, "sysparm_display_value": "true"})
                r.raise_for_status()
                batch = r.json().get("result", [])
                rows += [self._map(table, rec) for rec in batch]
                if len(batch) < 500 or len(rows) >= 5000:
                    break
                offset += 500
        return rows

    def _map(self, table: str, rec: dict) -> dict:
        prio = str(rec.get("priority", "3"))[:1]
        state = str(rec.get("state", "")).lower()
        closed = any(s in state for s in ("closed", "resolved", "clos", "résolu", "cancel"))
        return {
            "ref": rec.get("number"),
            "kind": self.TABLE_KIND.get(table, "request"),
            "priority": f"P{prio}" if prio in "1234" else "P3",
            "application": _display(rec.get("cmdb_ci")),
            "category": rec.get("category") or "",
            "title": rec.get("short_description") or rec.get("number"),
            "description": rec.get("description") or "",
            "status": "closed" if closed else ("in_progress" if "progress" in state or "cours" in state else "open"),
            "assignee": _display(rec.get("assigned_to")),
            "opened_at": rec.get("opened_at") or rec.get("sys_created_on"),
            "resolved_at": rec.get("resolved_at") or rec.get("closed_at") or "",
            "resolution": rec.get("close_notes") or "",
            "reopened": rec.get("reopen_count") or 0,
        }


def _display(value) -> str:
    return value.get("display_value", "") if isinstance(value, dict) else (value or "")


# ---------------------------------------------------------------- demo feed

class Simulated:
    """Generates realistic new incidents so the sync and proactive watch can be demonstrated
    without a real ticketing tool."""

    TEMPLATES = [
        ("Échec batch indemnisation nocturne étape IND060", "P2", "batch"),
        ("Indisponibilité du portail sinistres", "P1", "disponibilite"),
        ("Lenteur écran de recherche sinistres", "P3", "performance"),
        ("Courriers de règlement non générés", "P2", "interface"),
    ]

    def __init__(self, config: dict, db: Session, perimeter_id: str):
        self.db, self.pid, self.config = db, perimeter_id, config

    def test(self) -> str:
        return "Flux de démonstration prêt"

    def fetch(self, since: datetime | None) -> list[dict]:
        apps = self.db.scalars(select(Application).where(Application.perimeter_id == self.pid)).all()
        if not apps:
            return []
        n = self.db.scalars(select(Ticket.id).where(Ticket.perimeter_id == self.pid)).all()
        rows = []
        now = datetime.now(timezone.utc)
        for i in range(int(self.config.get("batch_size", 3))):
            title, prio, cat = random.choice(self.TEMPLATES)
            app = next((a for a in apps if a.name.lower() in title.lower() or a.name.lower().startswith(title.split()[-1].lower()[:5])), random.choice(apps))
            rows.append({"ref": f"SIM{len(n) + i + 1:05d}", "kind": "incident", "priority": prio, "application": app.name, "category": cat, "title": title,
                         "description": "Ticket reçu via le connecteur de démonstration.", "status": "open", "opened_at": (now - timedelta(hours=random.uniform(1, 9))).isoformat()})
        return rows


ADAPTERS = {"jira": Jira, "servicenow": ServiceNow, "simulated": Simulated}


def _adapter(db: Session, connector: Connector):
    if connector.kind == "simulated":
        return Simulated(connector.config, db, connector.perimeter_id)
    cls = ADAPTERS.get(connector.kind)
    if not cls:
        raise ConnectorError(f"type de connecteur inconnu : {connector.kind}")
    return cls(connector.config)


def test(db: Session, connector: Connector) -> dict:
    try:
        return {"ok": True, "message": _adapter(db, connector).test()}
    except (ConnectorError, httpx.HTTPError) as exc:
        return {"ok": False, "message": str(exc)}


def sync(db: Session, connector: Connector, actor: str) -> dict:
    started = datetime.now(timezone.utc)
    try:
        rows = _adapter(db, connector).fetch(connector.last_sync_at if connector.last_status == "ok" else None)
        # external tools name applications loosely: unknown ones are kept unlinked rather than rejected
        report = importer.import_rows(db, connector.perimeter_id, "tickets", [{**r, "_lenient": True} for r in rows]).as_dict()
        connector.last_status = "ok" if not report["error_count"] else "partial"
        connector.last_message = f"{report['inserted']} créé(s), {report['updated']} mis à jour, {report['error_count']} erreur(s)"
    except (ConnectorError, httpx.HTTPError) as exc:
        db.rollback()
        report = {"inserted": 0, "updated": 0, "error_count": 1, "errors": [{"row": 0, "message": str(exc)}]}
        connector.last_status, connector.last_message = "error", str(exc)[:500]
        db.add(Notification(perimeter_id=connector.perimeter_id, kind="sync", severity="medium", title=f"Échec de synchronisation : {connector.name}",
                            body=str(exc)[:500], link=f"/p/{connector.perimeter_id}/settings", fingerprint=f"sync_error:{connector.id}:{started:%Y-%m-%d}"))
    connector.last_sync_at = started
    audit.log(db, actor, "connector.synced", connector.perimeter_id, "connector", connector.id, status=connector.last_status, message=connector.last_message)
    db.commit()
    return {"status": connector.last_status, "message": connector.last_message, **{k: report[k] for k in ("inserted", "updated", "error_count")}, "errors": report.get("errors", [])[:20]}


def sync_all(db: Session) -> None:
    for connector in db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.kind != "simulated")).all():
        sync(db, connector, "scheduler")
