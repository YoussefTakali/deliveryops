"""Orchestrator — a LangGraph supervisor that plans a workflow, dispatches agents in dependency
order, merges their results, and pauses on a human approval gate before finalising.

    plan ─► supervisor ─┬─► <agent> ─► supervisor (loop until plan done)
                        └─► approval_gate (interrupt while high-impact items are pending) ─► finalize
"""

import logging
import operator
import threading
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit, knowledge, llm, notify, telemetry
from app.agents import billing, capacity, doc_generator, estimation, governance, incident, knowledge_transfer, meeting_intelligence, risk
from app.agents.base import AgentContext
from app.config import get_settings
from app.db import SessionLocal
from app.models import AgentRun, Perimeter, Recommendation

log = logging.getLogger(__name__)
settings = get_settings()

# Every orchestrated agent, each driven by the single orchestrator below (one node per agent).
AGENTS = {
    "doc_generator": doc_generator,
    "meeting_intelligence": meeting_intelligence,
    "billing": billing,
    "risk": risk,
    "incident": incident,
    "capacity": capacity,
    "governance": governance,
    "estimation": estimation,
    "knowledge_transfer": knowledge_transfer,
}

AGENT_LABELS = {
    "doc_generator": "Générateur de documents",
    "meeting_intelligence": "Assistant d'appel",
    "billing": "Facturation",
    "risk": "Analyse des risques",
    "incident": "Problèmes & incidents",
    "capacity": "Capacité & staffing",
    "governance": "Gouvernance & pilotage",
    "estimation": "Chiffrage",
    "knowledge_transfer": "Transfert de connaissance",
}

# Deliverables that commit the team or the client: the workflow cannot finalise until a human decides.
# Other recommendations (risks, alerts) stay in the approval queue without blocking.
GATED_KINDS = ("prebilling", "document", "minutes", "copil", "staffing")

# Agent → agents whose results it needs first (validated by the supervisor). Risk consolidates the
# billing findings; governance consolidates risk + billing (it reads their findings + pending recs).
DEPENDENCIES = {"risk": ["billing"], "governance": ["billing", "risk"]}

WORKFLOWS = {
    "document": {"label": "Générer un document", "agents": ["doc_generator"]},
    "call": {"label": "Assistant d'appel — compte-rendu", "agents": ["meeting_intelligence"]},
    "billing": {"label": "Pré-facturation mensuelle", "agents": ["billing"]},
    "risk": {"label": "Analyse des risques", "agents": ["risk"]},
    "review": {"label": "Revue complète du périmètre", "agents": ["billing", "risk"]},
    "copil": {"label": "Revue de pilotage (COPIL)", "agents": ["incident", "capacity", "billing", "governance"]},
    # These four were launched by the UI (Pilotage, Connaissance, Incidents & chiffrage, Capacité) but had
    # dropped out of the registry, so their buttons answered "Unknown workflow" — restored.
    "onboarding": {"label": "Onboarding du périmètre", "agents": ["knowledge_transfer", "incident", "capacity"]},
    "governance": {"label": "Contrôle santé du service", "agents": ["incident", "governance"]},
    "capacity": {"label": "Revue de capacité", "agents": ["capacity"]},
    "estimation": {"label": "Chiffrage d'une demande", "agents": ["estimation"]},
    "ask": {"label": "Question libre", "agents": []},
}


_run_users: dict[str, str] = {}


def _run_user(run_id: str) -> str:
    """Who launched this run — agent threads carry no request context, so quotas and the usage
    ledger attribute their LLM calls to AgentRun.requested_by."""
    if run_id not in _run_users:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            _run_users[run_id] = (run.requested_by if run else "") or ""
    return _run_users[run_id]


class State(TypedDict, total=False):
    run_id: str
    perimeter_id: str
    workflow: str
    params: dict  # last write wins
    plan: list[str]
    completed: Annotated[list[str], operator.add]
    results: Annotated[dict, operator.or_]
    next_agent: str | None
    approval: dict
    final: dict


class Route(BaseModel):
    agents: list[str] = Field(description="subset of: billing, risk")
    reason: str


# ---------------------------------------------------------------- helpers

def _step(run_id: str, entry: dict) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        run.steps = [*run.steps, {"ts": datetime.now(timezone.utc).isoformat(), **entry}]
        db.commit()


def _resolve_plan(agents: list[str]) -> list[str]:
    """Topological order honouring DEPENDENCIES, only for agents in the workflow."""
    ordered: list[str] = []

    def visit(a):
        if a in ordered:
            return
        for dep in DEPENDENCIES.get(a, []):
            if dep in agents:
                visit(dep)
        ordered.append(a)

    for a in agents:
        visit(a)
    return ordered


# ---------------------------------------------------------------- nodes

def plan_node(state: State) -> dict:
    workflow = state["workflow"]
    agents = list(WORKFLOWS[workflow]["agents"])
    reason = WORKFLOWS[workflow]["label"]
    if workflow == "ask":
        question = state["params"]["question"]

        def fallback() -> Route:
            q = question.lower()
            picks = [a for a, kws in {
                "billing": ("factur", "billing", "montant", "pénalit", "régie"),
                "risk": ("risque", "sla", "kpi", "incident", "problème", "récurren", "capacit", "charge", "surcharge", "hors périmètre", "dérive", "tendance"),
            }.items() if any(k in q for k in kws)]
            return Route(agents=picks, reason="routage par mots-clés")

        with telemetry.attribute(agent="orchestrator", run_id=state["run_id"], perimeter_id=state["perimeter_id"], username=_run_user(state["run_id"])):
            route, _ = llm.structured(schema=Route, context="Agents: billing (pre-invoice, contractual anomalies), risk (KPIs, SLA, recurring incidents, capacity, scope — the risk watchdog).", task=f"Which agents are needed to answer: « {question} »? Return an empty list if documentation search alone is enough.", fallback=fallback, tier="fast", effort="low")
        # Free questions route only to the two responders below; the others are review-only agents.
        agents = [a for a in route.agents if a in AGENTS and a not in ("doc_generator", "meeting_intelligence", "incident", "capacity", "governance", "estimation", "knowledge_transfer")]
        reason = route.reason
        _step(state["run_id"], {"node": "plan", "message": "Mode lecture : aucune recommandation ne sera ajoutée à la file de validation"})
        plan = _resolve_plan(agents)
        _step(state["run_id"], {"node": "plan", "message": f"Plan : {' → '.join(AGENT_LABELS[a] for a in plan) or 'recherche documentaire'} ({reason})"})
        return {"plan": plan, "params": {**state["params"], "read_only": True}}
    plan = _resolve_plan(agents)
    _step(state["run_id"], {"node": "plan", "message": f"Plan : {' → '.join(AGENT_LABELS[a] for a in plan) or 'recherche documentaire'} ({reason})"})
    return {"plan": plan}


def supervisor_node(state: State) -> dict:
    done = set(state.get("completed", []))
    for agent in state["plan"]:
        if agent in done:
            continue
        missing = [d for d in DEPENDENCIES.get(agent, []) if d in state["plan"] and d not in done]
        if missing:  # cannot happen with a topological plan, but guards manual plans
            continue
        return {"next_agent": agent}
    return {"next_agent": None}


def route_from_supervisor(state: State) -> str:
    return state["next_agent"] or "approval_gate"


def make_agent_node(name: str):
    module = AGENTS[name]

    def node(state: State) -> dict:
        _step(state["run_id"], {"node": name, "status": "started", "message": f"{AGENT_LABELS[name]} : analyse en cours"})
        with SessionLocal() as db:
            _perim = db.get(Perimeter, state["perimeter_id"])
            ctx = AgentContext(db=db, perimeter_id=state["perimeter_id"], run_id=state["run_id"],
                               params=state.get("params", {}), project_id=_perim.project_id if _perim else None,
                               upstream=state.get("results", {}))
            try:
                with telemetry.attribute(agent=name, run_id=state["run_id"], perimeter_id=state["perimeter_id"], username=_run_user(state["run_id"])):
                    out = module.run(ctx)
                audit.log(db, f"agent:{name}", "agent.completed", state["perimeter_id"], "run", state["run_id"], recommendations=len(out.recommendations), llm_used=out.llm_used)
                db.commit()
            except Exception as exc:
                db.rollback()
                log.exception("agent %s failed", name)
                _step(state["run_id"], {"node": name, "status": "failed", "message": str(exc)})
                return {"completed": [name], "results": {name: {"summary": f"Échec : {exc}", "data": {}, "recommendation_ids": [], "error": True}}}
        _step(state["run_id"], {"node": name, "status": "completed", "message": out.summary[:300], "recommendations": len(out.recommendations), "llm_used": out.llm_used})
        return {"completed": [name], "results": {name: out.to_state()}}

    return node


def approval_gate_node(state: State) -> dict:
    with SessionLocal() as db:
        pending = db.scalars(select(Recommendation).where(Recommendation.run_id == state["run_id"], Recommendation.kind.in_(GATED_KINDS), Recommendation.status == "pending")).all()
        pending_ids = [r.id for r in pending]
        titles = [r.title for r in pending]
    if not pending_ids:
        return {"approval": {"required": False}}
    _step(state["run_id"], {"node": "approval_gate", "status": "waiting", "message": f"{len(pending_ids)} livrable(s) en attente de validation humaine : " + " ; ".join(titles)})
    notify.approval_request(state["run_id"], state["perimeter_id"], titles)
    # Execution stops here; the checkpoint is persisted and resumed by app.runner.resume()
    decision = interrupt({"pending_recommendation_ids": pending_ids})
    _step(state["run_id"], {"node": "approval_gate", "status": "resumed", "message": f"Reprise après validation par {decision.get('by', '?')}"})
    return {"approval": {"required": True, **decision}}


def finalize_node(state: State) -> dict:
    results = state.get("results", {})
    with SessionLocal() as db:
        recs = db.scalars(select(Recommendation).where(Recommendation.run_id == state["run_id"])).all()
        counts = {s: sum(1 for r in recs if r.status == s) for s in ("approved", "rejected", "pending", "info")}
        final = {
            "workflow": state["workflow"],
            "agents": {name: {"label": AGENT_LABELS[name], "summary": r["summary"], "llm_used": r.get("llm_used"), "recommendations": len(r["recommendation_ids"])} for name, r in results.items()},
            "decisions": counts,
            "approved": [{"id": r.id, "agent": r.agent, "kind": r.kind, "title": r.title} for r in recs if r.status == "approved"],
            "rejected": [{"id": r.id, "agent": r.agent, "kind": r.kind, "title": r.title, "comment": r.decision_comment} for r in recs if r.status == "rejected"],
        }
        if state["workflow"] == "ask":
            with telemetry.attribute(agent="orchestrator", run_id=state["run_id"], perimeter_id=state["perimeter_id"], username=_run_user(state["run_id"])):
                final["answer"] = _answer_question(db, state["perimeter_id"], state["params"]["question"], results)
    _step(state["run_id"], {"node": "finalize", "status": "completed", "message": "Livrables consolidés"})
    return {"final": final}


def _answer_question(db, perimeter_id: str, question: str, results: dict) -> dict:
    hits = knowledge.search(db, perimeter_id, question, k=5)
    perimeter = db.get(Perimeter, perimeter_id)
    context = knowledge.format_context(hits) + "".join(f"\n<agent name=\"{n}\">{r['summary']}</agent>" for n, r in results.items())

    def fallback() -> str:
        parts = [r["summary"] for r in results.values()]
        if hits:
            parts.append(f"Documentation — {hits[0].cite()} : {hits[0].text[:400]}")
        return "\n\n".join(parts) or "Aucune information disponible pour répondre à cette question."

    text, used = llm.text(purpose="ask_answer", context=context, task=f"Perimeter {perimeter.name}. Answer the question « {question} » using the agent findings and documentation. Cite sources.", fallback=fallback)
    return {"text": text, "sources": [h.as_source() for h in hits], "llm_used": used}


# ---------------------------------------------------------------- graph

def _checkpointer():
    if settings.is_postgres:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row

        conn = psycopg.connect(settings.database_url.replace("+psycopg", ""), autocommit=True, prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def build_graph():
    g = StateGraph(State)
    g.add_node("plan", plan_node)
    g.add_node("supervisor", supervisor_node)
    for name in AGENTS:
        g.add_node(name, make_agent_node(name))
        g.add_edge(name, "supervisor")
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "supervisor")
    g.add_conditional_edges("supervisor", route_from_supervisor, [*AGENTS, "approval_gate"])
    g.add_edge("approval_gate", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=_checkpointer())


_graph = None
_graph_lock = threading.Lock()


def graph():
    global _graph
    with _graph_lock:
        if _graph is None:
            _graph = build_graph()
    return _graph


__all__ = ["graph", "WORKFLOWS", "AGENT_LABELS", "Command"]
