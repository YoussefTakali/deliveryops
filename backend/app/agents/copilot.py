"""Delivery Copilot — a conversational agent that reasons over the perimeter with tools.

The LLM decides which tools to call (documentation search, KPIs, tickets, capacity, pre-billing,
estimation...) and can act: `propose_action` files a recommendation that still needs human
validation, `start_workflow` launches an orchestrated workflow. Native OpenAI tool calling is used
when the model supports it, a JSON action protocol otherwise. Offline, a keyword planner calls
the same tools and assembles a factual answer.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone

import openai
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import context, knowledge, llm, staffing, telemetry
from app.agents.base import AgentContext, recommend
from app.agents.knowledge_transfer import build_map
from app.analytics import capacity as capacity_engine
from app.analytics.billing import prebill
from app.analytics.incidents import estimate_from_history, similar_resolved
from app.analytics.kpi import service_kpis
from app.config import get_settings
from app.models import (Application, BacklogItem, Dependency, Perimeter, PerimeterDeliverable, Recommendation,
                        TeamMember, Ticket, Timesheet)

log = logging.getLogger(__name__)
settings = get_settings()
MAX_TURNS = 10
MAX_RESULT_CHARS = 12000

TOOLS = [
    {
        "name": "search_documents",
        "description": "Search the perimeter knowledge base (transfer files, DAT, DEX, contract, meeting minutes). Returns excerpts with a citation label to quote.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "search terms, French works best"}}, "required": ["query"]},
    },
    {
        "name": "get_service_kpis",
        "description": "Service scorecard vs contract targets: SLA compliance per priority for the last complete month, MTTR, backlog, reopen and change failure rates, 6-month trend.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_project",
        "description": "The delivery project this perimeter belongs to: its manager, status, chosen technologies, the sibling perimeters under it, and the project team (who works on the project, with their role).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_plan_de_charge",
        "description": "The latest plan de charge of the project (workload plan spanning all its perimeters): total workload in days, total cost, horizon, and per-resource days/cost. Answers 'how much effort/cost is planned', 'who is loaded'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_staffing",
        "description": "Who is staffed across the project's perimeters: each person's profile, weekly capacity, skills, and which perimeter. Use for 'who works on X', 'who knows technology Y', team composition.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_deliverables",
        "description": "Deliverables produced for the project and its perimeters (matrice de couverture, COPIL per perimeter; plan de charge at project level): title, kind, which perimeter, and key figures. Use search_documents to read their content.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tickets",
        "description": "List tickets (incidents, requests, changes, problems) with filters. Most recent first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "in_progress", "closed"]},
                "kind": {"type": "string", "enum": ["incident", "request", "change", "problem"]},
                "application": {"type": "string", "description": "application name"},
                "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "limit": {"type": "integer", "description": "max rows, default 20"},
            },
        },
    },
    {
        "name": "find_similar_incidents",
        "description": "Find resolved tickets similar to a description, with how they were resolved.",
        "input_schema": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
    },
    {
        "name": "get_application_map",
        "description": "Applications with criticality, technology, incident count, dependents and risk score, plus dependency edges and single points of failure.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_capacity_forecast",
        "description": "8-week load forecast vs team capacity (absences included), per-member load, unassigned backlog proposals, skills held by one person. Optional what-if scenario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_growth_pct": {"type": "number", "description": "e.g. 20 for +20% run activity"},
                "add_fte": {"type": "number", "description": "extra full-time people"},
            },
        },
    },
    {
        "name": "get_prebilling",
        "description": "Pre-invoice for a month computed by the contract rules engine: lines, total, anomalies (double billing, cap exceeded, SLA penalties, out-of-scope work...). Defaults to last month.",
        "input_schema": {"type": "object", "properties": {"year": {"type": "integer"}, "month": {"type": "integer"}}},
    },
    {
        "name": "estimate_change_request",
        "description": "Effort range (P50/P80 hours) for a change request from the actual hours of similar delivered changes.",
        "input_schema": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]},
    },
    {
        "name": "list_recommendations",
        "description": "Recommendations and alerts raised by the agents, with their human decision status.",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected", "info"]}}},
    },
    {
        "name": "propose_action",
        "description": "File an action proposal in the human validation queue (nothing is executed until a delivery manager approves). Use when the user asks you to plan, escalate or follow up on something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "detail": {"type": "string", "description": "rationale, expected impact, owner"},
                "impact": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["title", "detail", "impact"],
        },
    },
    {
        "name": "start_workflow",
        "description": "Launch an orchestrated multi-agent workflow. billing = monthly pre-billing, risk = risk analysis, review = full perimeter review (billing + risk), copil = COPIL pilotage review (incidents, capacity/staffing, billing, governance), document = generate a document, call = meeting minutes.",
        "input_schema": {"type": "object", "properties": {"workflow": {"type": "string", "enum": ["billing", "risk", "review", "copil", "document", "call"]}}, "required": ["workflow"]},
    },
]


class Toolbox:
    """Tool implementations bound to one perimeter and one user."""

    def __init__(self, db: Session, perimeter: Perimeter, username: str):
        self.db, self.perimeter, self.username = db, perimeter, username
        self.pid = perimeter.id
        # Project scope: this périmètre + its siblings under the same delivery project. Retrieval and
        # the project/plan/staffing tools read the whole project, so the copilot is project-aware.
        self.project = context.project_of(db, perimeter)
        self.scope_pids = context.scope_perimeter_ids(db, perimeter)
        self.pid_names = context.perimeter_names(db, perimeter)
        self.sources: list[dict] = []
        self.actions: list[dict] = []

    def _all(self, model):
        return self.db.scalars(select(model).where(model.perimeter_id == self.pid)).all()

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"t_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        try:
            return handler(**args)
        except TypeError as exc:
            return {"error": f"invalid arguments: {exc}"}

    def t_search_documents(self, query: str) -> dict:
        # Search the whole project (this périmètre + siblings), so deliverables, plans and meeting
        # transcripts indexed under any périmètre of the project are reachable from here.
        hits = knowledge.search(self.db, self.scope_pids, query, k=6)
        self.sources += [h.as_source() for h in hits]
        results = []
        for h in hits:
            item = {"cite": h.cite(), "text": h.text}
            if h.perimeter_id and h.perimeter_id != self.pid:
                item["perimeter"] = self.pid_names.get(h.perimeter_id, h.perimeter_id)
            results.append(item)
        return {"results": results or "no matching documentation"}

    def t_get_project(self) -> dict:
        if not self.project:
            return {"note": "Ce périmètre n'est rattaché à aucun projet.", "perimeter": self.perimeter.name}
        p = self.project
        return {"project": p.name, "manager": p.manager, "status": p.status, "technologies": p.technologies or [],
                "perimeters": [self.pid_names[i] for i in self.scope_pids], "team": context.project_team(self.db, p)}

    def t_get_plan_de_charge(self) -> dict:
        if not self.project:
            return {"note": "Le plan de charge est un document de projet ; ce périmètre n'a pas de projet."}
        d = context.latest_project_plan(self.db, self.project)
        if not d:
            return {"note": "Aucun plan de charge généré pour ce projet."}
        draft = d.draft or {}
        tot = draft.get("totals") or {}
        return {"filename": d.filename, "created_at": d.created_at.isoformat()[:10],
                "horizon_weeks": (draft.get("params") or {}).get("weeks"),
                "totals": {"days": tot.get("days"), "cost": tot.get("cost"), "unscheduled_days": tot.get("unscheduled_days")},
                "resources": [{"name": m.get("name"), "profile": m.get("profile"), "days": m.get("total_days"), "cost": m.get("cost")} for m in draft.get("members") or []],
                "notes": draft.get("notes") or []}

    def t_get_staffing(self) -> dict:
        out = []
        for pid in self.scope_pids:
            for m in staffing.staffed(self.db, pid):
                out.append({"name": m["name"], "perimeter": self.pid_names.get(pid, pid), "profile": m["profile"],
                            "weekly_capacity_h": m["weekly_capacity_h"], "skills": m["skills"], "owner": m.get("owner")})
        return {"count": len(out), "staffing": out} if out else {"note": "Personne n'est encore staffé sur ce projet."}

    def t_get_deliverables(self) -> dict:
        items = []
        for pid in self.scope_pids:
            for d in self.db.scalars(select(PerimeterDeliverable).where(PerimeterDeliverable.perimeter_id == pid).order_by(PerimeterDeliverable.created_at.desc())):
                items.append({"title": d.title, "kind": d.key, "perimeter": self.pid_names.get(pid, pid), "created_at": d.created_at.isoformat()[:10]})
        if self.project:
            d = context.latest_project_plan(self.db, self.project)
            if d:
                tot = (d.draft or {}).get("totals") or {}
                items.append({"title": d.title, "kind": d.key, "scope": "projet (tous périmètres)", "created_at": d.created_at.isoformat()[:10], "days": tot.get("days"), "cost": tot.get("cost")})
        return {"count": len(items), "deliverables": items} if items else {"note": "Aucun livrable généré."}

    def t_get_service_kpis(self) -> dict:
        kpis = service_kpis(self._all(Ticket), self.perimeter.contract)
        apps = {a.id: a.name for a in self._all(Application)}
        kpis["top_applications"] = [{"application": apps.get(a, a), "incidents": n} for a, n in kpis["top_applications"]]
        return kpis

    def t_list_tickets(self, status=None, kind=None, application=None, priority=None, limit=20) -> dict:
        apps = {a.id: a.name for a in self._all(Application)}
        rows = sorted(self._all(Ticket), key=lambda t: t.opened_at, reverse=True)
        out = []
        for t in rows:
            if (status and t.status != status) or (kind and t.kind != kind) or (priority and t.priority != priority):
                continue
            if application and application.lower() not in apps.get(t.application_id, "").lower():
                continue
            out.append({"ref": t.ref, "kind": t.kind, "priority": t.priority, "application": apps.get(t.application_id), "title": t.title, "status": t.status,
                        "opened_at": t.opened_at.isoformat()[:16], "resolved_at": t.resolved_at.isoformat()[:16] if t.resolved_at else None, "resolution": t.resolution})
            if len(out) >= min(int(limit or 20), 100):
                break
        self.sources += [{"type": "ticket", "ref": r["ref"], "label": f"{r['ref']} — {r['title']}", "excerpt": ""} for r in out[:5]]
        return {"count": len(out), "tickets": out}

    def t_find_similar_incidents(self, description: str) -> dict:
        sim = similar_resolved(description, self._all(Ticket), k=5)
        self.sources += [{"type": "ticket", "ref": t.ref, "label": f"{t.ref} — {t.title}", "excerpt": t.resolution} for t, _ in sim]
        return {"similar": [{"ref": t.ref, "title": t.title, "priority": t.priority, "opened_at": t.opened_at.isoformat()[:10], "resolution": t.resolution, "similarity": s} for t, s in sim]}

    def t_get_application_map(self) -> dict:
        return build_map(self._all(Application), self._all(Dependency), self._all(Ticket))

    def t_get_capacity_forecast(self, incident_growth_pct: float = 0, add_fte: float = 0) -> dict:
        result = capacity_engine.analyze(self._all(TeamMember), self._all(Ticket), self._all(Timesheet), self._all(BacklogItem),
                                         scenario={"incident_growth_pct": incident_growth_pct, "add_fte": add_fte})
        result.pop("history_run_h", None)
        return result

    def t_get_prebilling(self, year: int | None = None, month: int | None = None) -> dict:
        if not (year and month):
            today = date.today()
            year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        result = prebill(self.perimeter.contract, self._all(TeamMember), self._all(Ticket), self._all(Timesheet), year, month)
        by_kind = defaultdict(list)
        for a in result["anomalies"]:
            by_kind[a["kind"]].append(a["message"])
        return {"period": result["period"], "total_excl_tax": result["total_excl_tax"], "currency": result["currency"],
                "lines": [{k: l[k] for k in ("section", "label", "amount", "justification")} for l in result["lines"]],
                "anomalies": {k: {"count": len(v), "examples": v[:3]} for k, v in by_kind.items()}}

    def t_estimate_change_request(self, request: str) -> dict:
        hours = defaultdict(float)
        for s in self._all(Timesheet):
            if s.ticket_ref:
                hours[s.ticket_ref] += s.hours
        tickets = self._all(Ticket)
        for t in tickets:
            if t.kind == "change" and t.ref not in hours and t.estimate_h:
                hours[t.ref] = t.estimate_h
        return {**estimate_from_history(request, tickets, hours), "work_unit_catalog": self.perimeter.contract.get("work_units", {})}

    def t_list_recommendations(self, status: str | None = None) -> dict:
        stmt = select(Recommendation).where(Recommendation.perimeter_id == self.pid).order_by(Recommendation.created_at.desc()).limit(40)
        if status:
            stmt = stmt.where(Recommendation.status == status)
        return {"recommendations": [{"agent": r.agent, "kind": r.kind, "title": r.title, "impact": r.impact, "status": r.status} for r in self.db.scalars(stmt)]}

    def t_propose_action(self, title: str, detail: str, impact: str = "medium") -> dict:
        ctx = AgentContext(db=self.db, perimeter_id=self.pid, run_id=None, params={})
        rec = recommend(ctx, "copilot", "action", title, f"{detail}\n\nProposé via le Copilot à la demande de {self.username}.", impact=impact if impact in ("low", "medium", "high") else "medium", confidence=0.6)
        if rec.status == "info":
            rec.status = "pending"  # the user explicitly asked for it: always route to a human
        self.db.commit()
        self.actions.append({"type": "recommendation", "id": rec.id, "title": rec.title})
        return {"filed": True, "recommendation_id": rec.id, "status": "pending human validation"}

    def t_start_workflow(self, workflow: str) -> dict:
        from app import runner  # late import: runner imports the orchestrator which imports agents

        run = runner.start_run(self.db, self.pid, workflow, {}, self.username)
        self.actions.append({"type": "run", "id": run.id, "workflow": workflow})
        return {"started": True, "run_id": run.id, "note": "runs in background; deliverables will need human validation"}


def _system(perimeter: Perimeter, tools: "Toolbox | None" = None, debug: bool = False) -> str:
    base = (
        f"{llm.BASE_SYSTEM}\n\nYou are the Delivery Copilot for the perimeter « {perimeter.name} » of client "
        f"« {perimeter.client.name} » ({perimeter.description}). You help delivery managers understand and steer the service.\n"
        "Use the tools to get facts before answering; never invent figures, ticket numbers or amounts. "
        "Quote document citations exactly as returned (e.g. [DEX SI Sinistres.md § ...]) and ticket refs. "
        "Keep answers concise and structured for a busy manager: key facts first, then recommendations. "
        "Only call propose_action or start_workflow when the user asks for an action or clearly agrees to one. "
        "Whenever you offer several options or next steps, mark exactly ONE of them as « (recommandé) » and say why in one "
        "sentence."
        + (" Debug mode: the user hit an error and wants to fix it with you — most likely cause first, then 2-3 numbered "
           "fixes with ONE « (recommandé) », then at most one question if something is missing. Do not echo the debug tag."
           if debug else "")
    )
    proj = getattr(tools, "project", None) if tools else None
    if proj:
        siblings = [n for i, n in (tools.pid_names or {}).items() if i != perimeter.id]
        base += (
            f"\n\nThis perimeter belongs to the delivery project « {proj.name} » (manager {proj.manager}; "
            f"technologies: {', '.join(proj.technologies or []) or '—'})."
            + (f" Sibling perimeters in the same project: {', '.join(siblings)}." if siblings else "")
            + " You are project-aware: search_documents covers the whole project, and get_project / "
            "get_plan_de_charge / get_staffing / get_deliverables return the project team, workload plan (jours & coûts) "
            "and deliverables. Answer project-wide questions when asked."
        )
    return base


def _json(data) -> str:
    raw = json.dumps(data, ensure_ascii=False, default=str)
    return raw if len(raw) <= MAX_RESULT_CHARS else raw[:MAX_RESULT_CHARS] + "…(truncated)"


def _summarize(name: str, result: dict) -> str:
    if "error" in result:
        return result["error"]
    return {
        "search_documents": lambda r: f"{len(r['results']) if isinstance(r['results'], list) else 0} extrait(s)",
        "list_tickets": lambda r: f"{r['count']} ticket(s)",
        "find_similar_incidents": lambda r: f"{len(r['similar'])} cas similaire(s)",
        "get_service_kpis": lambda r: f"statut global {r['overall_status']}",
        "get_application_map": lambda r: f"{len(r['nodes'])} applications",
        "get_capacity_forecast": lambda r: f"{r['overload_weeks']} semaine(s) en surcharge",
        "get_prebilling": lambda r: f"{r['period']} : {r['total_excl_tax']:,.0f} {r['currency']}",
        "estimate_change_request": lambda r: f"P50 {r['p50_h']} h / P80 {r['p80_h']} h",
        "list_recommendations": lambda r: f"{len(r['recommendations'])} élément(s)",
        "propose_action": lambda r: "proposition ajoutée à la file de validation",
        "start_workflow": lambda r: f"exécution {r['run_id']} lancée",
    }.get(name, lambda r: "ok")(result)


def respond(db: Session, perimeter: Perimeter, username: str, history: list[dict], user_message: str) -> dict:
    """Returns {"text", "trace", "sources", "actions", "llm_used"}."""
    tools = Toolbox(db, perimeter, username)
    trace: list[dict] = []
    with telemetry.attribute(agent="copilot", perimeter_id=perimeter.id):
        if settings.use_llm:
            try:
                if settings.llm_tool_mode == "native":
                    try:
                        text = _respond_native(perimeter, tools, trace, history, user_message)
                    except openai.BadRequestError as exc:  # model/endpoint without tool calling
                        log.warning("native tool calling rejected (%s), using JSON action protocol", exc)
                        text = _respond_json_protocol(perimeter, tools, trace, history, user_message)
                else:
                    text = _respond_json_protocol(perimeter, tools, trace, history, user_message)
                return {"text": text, "trace": trace, "sources": _dedupe(tools.sources), "actions": tools.actions, "llm_used": True}
            except llm.API_ERRORS as exc:
                log.error("copilot LLM failure, falling back: %s", exc)
        telemetry.record("copilot_turn", "offline", 0, "fallback")
        text = _respond_offline(tools, trace, user_message)
    return {"text": text, "trace": trace, "sources": _dedupe(tools.sources), "actions": tools.actions, "llm_used": False}


def _dedupe(sources: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in sources:
        if s["ref"] not in seen:
            seen.add(s["ref"])
            out.append(s)
    return out[:12]


OPENAI_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOLS]
GIVE_UP = "Je n'ai pas pu conclure dans le nombre d'étapes autorisé ; reformulez ou précisez la question."


def _history(perimeter, history: list[dict], user_message: str, system: str) -> list[dict]:
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["text"]} for m in history if m.get("text")][-12:]
    messages.append({"role": "user", "content": user_message})
    return messages


def _run_tool(tools: Toolbox, trace: list, name: str, raw_args) -> dict:
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return {"error": "arguments must be a JSON object"}
    args = raw_args if isinstance(raw_args, dict) else {}
    result = tools.run(name, args)
    trace.append({"tool": name, "input": args, "summary": _summarize(name, result)})
    return result


def _is_debug(message: str) -> bool:
    return (message or "").lstrip().startswith("[DÉBOGAGE]")


def _respond_native(perimeter, tools: Toolbox, trace: list, history: list[dict], user_message: str) -> str:
    """OpenAI tool calling (supported by Llama 3.1+/3.3, Mistral, Qwen NIMs...)."""
    model = llm.model_for("agent")
    messages = _history(perimeter, history, user_message, _system(perimeter, tools, _is_debug(user_message)))
    for _ in range(MAX_TURNS):
        response = llm.chat("copilot_turn", model=model, messages=messages, tools=OPENAI_TOOLS, tool_choice="auto")
        choice = response.choices[0]
        msg = choice.message
        if not msg.tool_calls:
            text = llm.clean_text(msg.content)
            return text + ("\n\n(réponse tronquée)" if choice.finish_reason == "length" else "")
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}} for c in msg.tool_calls
        ]})
        for call in msg.tool_calls:
            result = _run_tool(tools, trace, call.function.name, call.function.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": _json(result)})
    return GIVE_UP


JSON_PROTOCOL = """
You can use tools. At each step reply with ONE JSON object and nothing else:
- to call a tool: {"tool": "<name>", "arguments": {...}}
- to answer the user: {"answer": "<final answer in markdown>"}
Available tools (name: description; JSON schema of arguments):
"""


def _respond_json_protocol(perimeter, tools: Toolbox, trace: list, history: list[dict], user_message: str) -> str:
    """Tool use for models without native tool calling: the model emits JSON actions."""
    model = llm.model_for("agent")
    catalog = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'], ensure_ascii=False)}" for t in TOOLS)
    messages = _history(perimeter, history, user_message, _system(perimeter, tools, _is_debug(user_message)) + JSON_PROTOCOL + catalog)
    for _ in range(MAX_TURNS):
        response = llm.chat("copilot_turn", model=model, messages=messages)
        content = response.choices[0].message.content or ""
        try:
            action = llm.extract_json(content)
        except json.JSONDecodeError:
            cleaned = llm.clean_text(content)
            if cleaned:
                return cleaned  # the model answered in prose: accept it
            messages.append({"role": "user", "content": "Reply with a single JSON object."})
            continue
        if "answer" in action:
            return str(action["answer"])
        name = action.get("tool")
        if not name:
            messages.append({"role": "user", "content": 'Missing "tool" or "answer" key. Reply with a single JSON object.'})
            continue
        result = _run_tool(tools, trace, name, action.get("arguments", {}))
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Result of {name}:\n{_json(result)}"})
    return GIVE_UP


# ---------------------------------------------------------------- offline planner

KEYWORDS = [
    (("sla", "kpi", "santé", "indicateur", "mttr", "dérive", "tendance", "copil"), "get_service_kpis", {}),
    (("charge", "capacit", "staff", "ressource", "surcharge", "absence", "backlog", "affect"), "get_capacity_forecast", {}),
    (("factur", "montant", "pénalit", "régie", "anomal", "billing"), "get_prebilling", {}),
    (("risque", "dépendan", "cartograph", "application", "critique", "spof"), "get_application_map", {}),
    (("ticket", "incident", "ouvert", "p1", "p2"), "list_tickets", {"status": "in_progress"}),
    (("chiffr", "estim", "combien de temps", "effort"), "estimate_change_request", None),
    (("recommandation", "décision", "validation", "en attente"), "list_recommendations", {"status": "pending"}),
]


def _respond_offline(tools: Toolbox, trace: list, message: str) -> str:
    q = message.lower()
    calls = [(name, dict(args) if args is not None else {"request": message}) for kws, name, args in KEYWORDS if any(k in q for k in kws)]
    if any(k in q for k in ("pourquoi", "comment", "procédure", "échec", "erreur")):
        calls.append(("find_similar_incidents", {"description": message}))
    calls.append(("search_documents", {"query": message}))
    if any(k in q for k in ("lance", "démarre", "revue", "risque")) and any(k in q for k in ("revue", "risque", "workflow", "analyse")):
        calls.append(("start_workflow", {"workflow": "review"}))

    parts = []
    for name, args in calls:
        result = tools.run(name, args)
        trace.append({"tool": name, "input": args, "summary": _summarize(name, result)})
        parts.append(_render(name, result))
    body = "\n\n".join(p for p in parts if p)
    return f"{body}\n\n_Mode hors ligne : réponse assemblée à partir des outils, sans rédaction par le modèle._"


def _render(name: str, r: dict) -> str:
    if "error" in r:
        return ""
    if name == "get_service_kpis":
        lines = [f"- {s['kpi']} : {s['value']}{s['unit']} (cible {s['target']}{s['unit']}, {s['status']})" for s in r["scorecard"]]
        sla = [f"{p} {c['pct']} % / {c['target_pct']} %" for p, c in r["sla_by_priority_last_month"].items() if not c["met"]]
        return f"**Indicateurs ({r['reference_month']})**\n" + "\n".join(lines) + (f"\nSLA non tenus : {', '.join(sla)}" if sla else "")
    if name == "get_capacity_forecast":
        peak = max(r["forecast"], key=lambda w: w["utilization_pct"] or 0)
        risky = [c["skill"] for c in r["skill_coverage"] if c["at_risk"]]
        return (f"**Capacité** : {r['overload_weeks']} semaine(s) en surcharge sur {len(r['forecast'])}, pic {peak['utilization_pct']} % semaine du {peak['week']}, "
                f"écart {r['capacity_gap_h']} h (≈ {r['fte_gap']} ETP)." + (f" Compétences à risque : {', '.join(risky)}." if risky else ""))
    if name == "get_prebilling":
        anomalies = "; ".join(f"{k} ({v['count']})" for k, v in r["anomalies"].items())
        return f"**Pré-facture {r['period']}** : {r['total_excl_tax']:,.2f} {r['currency']} HT. Écarts : {anomalies or 'aucun'}."
    if name == "get_application_map":
        top = r["nodes"][:4]
        spof = [n["name"] for n in r["nodes"] if n["single_point_of_failure"]]
        return "**Applications les plus à risque** : " + ", ".join(f"{n['name']} (score {n['risk_score']}, {n['incidents']} incidents)" for n in top) + (f". Points de défaillance unique : {', '.join(spof)}." if spof else ".")
    if name == "list_tickets":
        return f"**Tickets en cours ({r['count']})** :\n" + "\n".join(f"- {t['ref']} {t['priority']} {t['application']} — {t['title']}" for t in r["tickets"][:8])
    if name == "find_similar_incidents" and r["similar"]:
        return "**Cas similaires résolus** :\n" + "\n".join(f"- {s['ref']} ({s['opened_at']}) : {s['resolution'] or s['title']}" for s in r["similar"][:3])
    if name == "estimate_change_request" and r.get("p50_h"):
        return f"**Estimation** : P50 {r['p50_h']} h, P80 {r['p80_h']} h — base : {r['basis']}."
    if name == "list_recommendations":
        return f"**Décisions en attente ({len(r['recommendations'])})** :\n" + "\n".join(f"- [{x['impact']}] {x['title']}" for x in r["recommendations"][:8])
    if name == "search_documents" and isinstance(r["results"], list) and r["results"]:
        top = r["results"][0]
        return f"**Documentation** {top['cite']} :\n> {top['text'][:500]}"
    if name == "start_workflow":
        return f"**Workflow lancé** : exécution {r['run_id']}."
    return ""


def new_message(role: str, text: str, **extra) -> dict:
    return {"role": role, "text": text, "ts": datetime.now(timezone.utc).isoformat(), **extra}
